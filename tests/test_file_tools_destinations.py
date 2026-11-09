"""Where a file tool saves (Milestone F1, F1.10): the Name field's rules, the
"Save in" menu's RECENT list, the writable check, and how the result and the
toast name the place. docs/PRD.md § "Post-v1: file tools" → "Rules every
tool follows".
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import destinations  # noqa: E402
from lector.features.settings import store  # noqa: E402


class _Folders(unittest.TestCase):
    """A temporary home with Desktop, Documents and Downloads, and its own
    settings file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        for name in ("Desktop", "Documents", "Downloads"):
            (self.home / name).mkdir(parents=True)
        self.reports = self.root / "Reports"
        self.reports.mkdir()
        patches = (
            mock.patch.object(store, "_settings_path", lambda: self.root / "settings.json"),
            mock.patch.object(destinations.browser, "_known_folder", lambda name: self.home / name),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def folder(self, name: str) -> str:
        path = self.root / name
        path.mkdir(exist_ok=True)
        return str(path)


class NameTests(unittest.TestCase):
    def test_an_empty_name_is_refused(self):
        for stem in ("", "   ", None):
            with self.subTest(stem=stem):
                self.assertEqual(destinations.name_error(stem), destinations.NAME_EMPTY)

    def test_each_character_windows_does_not_allow_is_refused(self):
        for char in '\\/:*?"<>|':
            with self.subTest(char=char):
                self.assertEqual(destinations.name_error(f"Report{char}2025"), destinations.NAME_ILLEGAL)
        self.assertEqual(destinations.name_error("Report\x07"), destinations.NAME_ILLEGAL)

    def test_a_name_of_only_dots_is_refused(self):
        for stem in (".", "..", " ... "):
            with self.subTest(stem=stem):
                self.assertEqual(destinations.name_error(stem), destinations.NAME_UNUSABLE)

    def test_ordinary_names_pass(self):
        for stem in ("Report (compressed)", "Bericht – März", "a.b", "notes 2025"):
            with self.subTest(stem=stem):
                self.assertIsNone(destinations.name_error(stem))

    def test_the_extension_is_fixed_by_the_tool(self):
        self.assertEqual(destinations.extension("compress"), ".pdf")
        self.assertEqual(destinations.extension("searchable"), ".pdf")
        self.assertEqual(destinations.extension("convert"), ".pdf")
        self.assertEqual(destinations.extension("export"), "")


class CheckTests(_Folders):
    def test_the_extension_is_added_to_the_name_and_cannot_be_changed(self):
        target = destinations.check("compress", str(self.reports), "  Report  ", [])
        self.assertEqual(target.path, str(self.reports / "Report.pdf"))
        typed = destinations.check("compress", str(self.reports), "Report.txt", [])
        self.assertEqual(Path(typed.path).name, "Report.txt.pdf")
        folder = destinations.check("export", str(self.reports), "Report (images)", [])
        self.assertEqual(folder.path, str(self.reports / "Report (images)"))

    def test_a_bad_name_is_reported_under_the_name(self):
        with self.assertRaises(destinations.DestinationError) as raised:
            destinations.check("compress", str(self.reports), "a/b", [])
        self.assertEqual(raised.exception.field, destinations.NAME_FIELD)
        self.assertEqual(raised.exception.message, destinations.NAME_ILLEGAL)

    def test_a_missing_or_unwritable_folder_is_reported_under_save_in(self):
        for folder in (str(self.root / "gone"), "relative", None):
            with self.subTest(folder=folder), self.assertRaises(destinations.DestinationError) as raised:
                destinations.check("compress", folder, "Report", [])
            self.assertEqual(raised.exception.field, destinations.FOLDER_FIELD)
            self.assertEqual(raised.exception.message, "Can't save in this folder. Choose another.")
        with mock.patch.object(destinations.tempfile, "mkstemp", side_effect=PermissionError):
            self.assertFalse(destinations.is_writable_folder(str(self.reports)))
            with self.assertRaises(destinations.DestinationError) as raised:
                destinations.check("compress", str(self.reports), "Report", [])
        self.assertEqual(raised.exception.field, destinations.FOLDER_FIELD)

    def test_the_writable_check_leaves_nothing_behind(self):
        self.assertTrue(destinations.is_writable_folder(str(self.reports)))
        self.assertEqual(os.listdir(self.reports), [])

    def test_the_original_is_refused_in_any_spelling(self):
        original = self.reports / "Report.pdf"
        original.write_bytes(b"%PDF-1.4")
        with self.assertRaises(destinations.DestinationError) as raised:
            destinations.check("compress", str(self.reports).upper(), "report", [str(original)])
        self.assertEqual(raised.exception.message, destinations.NAME_IS_SOURCE)
        with self.assertRaises(destinations.DestinationError) as raised:
            destinations.check("convert", str(self.reports), "Report", [str(original)])
        self.assertEqual(raised.exception.message, destinations.NAME_IS_INPUT)

    def test_a_taken_name_in_a_chosen_folder_is_flagged_for_the_replace_question(self):
        other = Path(self.folder("Elsewhere"))
        (other / "Report (compressed).pdf").write_bytes(b"%PDF-1.4")
        self.assertTrue(destinations.check("compress", str(other), "Report (compressed)", []).exists)
        self.assertFalse(destinations.check("compress", str(self.reports), "Report (compressed)", []).exists)

    def test_the_suggestion_is_the_free_name_in_the_chosen_folder(self):
        other = Path(self.folder("Elsewhere"))
        (other / "Report (compressed).pdf").write_bytes(b"%PDF-1.4")
        (other / "Report (images)").mkdir()
        source = str(self.reports / "Report.pdf")
        self.assertEqual(destinations.suggested_stem("compress", source, str(self.reports)), "Report (compressed)")
        self.assertEqual(destinations.suggested_stem("compress", source, str(other)), "Report (compressed) (2)")
        self.assertEqual(destinations.suggested_stem("export", source, str(other)), "Report (images) (2)")
        self.assertEqual(destinations.suggested_stem("convert", str(self.reports / "scan.png"), str(other)), "scan")


class RecentTests(_Folders):
    def test_most_recent_first_and_at_most_three(self):
        for name in ("A", "B", "C", "D"):
            destinations.remember(self.folder(name))
        self.assertEqual([Path(p).name for p in store.get_recent_output_folders()], ["D", "C", "B"])

    def test_a_folder_is_listed_once_however_it_is_spelled(self):
        a = self.folder("A")
        destinations.remember(a)
        destinations.remember(self.folder("B"))
        destinations.remember(a.upper() + os.sep)
        names = [Path(p).name.upper() for p in store.get_recent_output_folders()]
        self.assertEqual(names, ["A", "B"])

    def test_folders_that_no_longer_exist_are_forgotten_when_the_menu_opens(self):
        destinations.remember(self.folder("A"))
        gone = self.folder("Gone")
        destinations.remember(gone)
        os.rmdir(gone)
        menu = destinations.menu(str(self.reports))
        self.assertEqual([entry["name"] for entry in menu["recent"]], ["A"])
        self.assertEqual([Path(p).name for p in store.get_recent_output_folders()], ["A"])

    def test_on_this_pc_folders_take_no_recent_slot(self):
        destinations.remember(str(self.home / "Documents"))
        self.assertEqual(store.get_recent_output_folders(), [])

    def test_the_menu_lists_the_known_folders_that_exist(self):
        os.rmdir(self.home / "Downloads")
        menu = destinations.menu(str(self.reports))
        self.assertEqual([p["name"] for p in menu["known"]], ["Desktop", "Documents"])
        self.assertEqual(menu["origin"], {"path": str(self.reports), "name": "Reports"})
        self.assertEqual(menu["recent"], [])

    def test_the_origin_is_not_listed_again_under_recent(self):
        destinations.remember(str(self.reports))
        self.assertEqual(destinations.menu(str(self.reports))["recent"], [])

    def test_junk_in_the_settings_file_is_ignored(self):
        data = {store._RECENT_OUTPUT_FOLDERS_KEY: [3, "relative", None, str(self.reports)]}
        with mock.patch.object(store, "_load", return_value=data):
            self.assertEqual(store.get_recent_output_folders(), [str(self.reports)])


class WhereTests(_Folders):
    def test_each_destination_kind_has_its_phrase(self):
        origin = str(self.reports)
        cases = (
            (origin, "beside the original"),
            (str(self.home / "Desktop"), "on the Desktop"),
            (str(self.home / "Documents"), "in Documents"),
            (str(self.home / "Downloads"), "in Downloads"),
            (self.folder("Lector notes"), "in Lector notes"),
        )
        for folder, phrase in cases:
            with self.subTest(folder=folder):
                self.assertEqual(destinations.where_phrase(folder, origin), phrase)
        self.assertEqual(
            destinations.where_phrase(origin, origin, destinations.BESIDE_FIRST_FILE), "beside the first file"
        )

    def test_a_drive_root_is_named_by_its_path(self):
        root = os.path.abspath(os.sep)
        self.assertEqual(destinations.folder_label(root), root)


class BridgeTests(_Folders):
    """api.py's side: the dialog always starts beside the original, and a
    folder that can't be written to stops the job before it starts."""

    def setUp(self):
        super().setUp()
        self.window = mock.Mock()
        windows = mock.patch.object(api_module.webview, "windows", [self.window])
        windows.start()
        self.addCleanup(windows.stop)
        self.api = api_module.Api()
        self.api._voice = mock.MagicMock()
        self.addCleanup(self.api._ocr.shutdown)
        self.addCleanup(self.api._doc.close)
        self.source = pdf_support.plain_text(self.reports)
        self.api.open_pdf(str(self.source))

    def test_every_open_starts_beside_the_original_even_after_another_folder_was_used(self):
        destinations.remember(self.folder("Elsewhere"))
        for _ in range(2):
            info = self.api.file_tool_info("compress", "open")
            self.assertEqual(info["output"]["folder"], str(self.reports))
            self.assertEqual(info["output"]["stem"], f"{self.source.stem} (compressed)")

    def test_an_unwritable_folder_blocks_the_run(self):
        with mock.patch.object(destinations, "is_writable_folder", return_value=False):
            checked = self.api.check_tool_destination("compress", "open", str(self.reports), "x")
            outcome = self.api.start_file_job(
                "compress", "open", {"level": "light", "folder": str(self.reports), "name": "x"}
            )
            self.assertEqual(self.api.check_output_folder(str(self.reports)), {
                "ok": False, "error": "Can't save in this folder. Choose another."
            })
        self.assertEqual(checked, {"ok": False, "field": "folder", "error": "Can't save in this folder. Choose another."})
        self.assertFalse(outcome["ok"])
        self.assertIn("isn't allowed to write to that folder", outcome["error"])
        self.assertFalse(self.api._jobs.is_running())

    def test_the_replace_question_is_asked_in_a_non_default_folder(self):
        other = Path(self.folder("Elsewhere"))
        (other / "taken.pdf").write_bytes(b"%PDF-1.4")
        checked = self.api.check_tool_destination("compress", "open", str(other), "taken")
        self.assertEqual(checked["exists"], True)
        self.assertEqual(checked["path"], str(other / "taken.pdf"))
        self.assertEqual(checked["folder"], str(other))

    def test_the_original_is_refused_under_the_name(self):
        checked = self.api.check_tool_destination("compress", "open", str(self.reports), self.source.stem)
        self.assertEqual(checked, {"ok": False, "field": "name", "error": destinations.NAME_IS_SOURCE})

    def test_the_name_follows_the_chosen_folder(self):
        other = Path(self.folder("Elsewhere"))
        (other / f"{self.source.stem} (compressed).pdf").write_bytes(b"%PDF-1.4")
        answer = self.api.suggest_tool_name("compress", "open", str(other))
        self.assertEqual(answer, {"stem": f"{self.source.stem} (compressed) (2)", "extension": ".pdf"})

    def test_remembering_only_takes_a_folder_that_is_there(self):
        self.assertEqual(self.api.remember_output_folder(str(self.root / "gone")), {"ok": False})
        self.assertEqual(self.api.remember_output_folder("relative"), {"ok": False})
        self.assertEqual(self.api.remember_output_folder(self.folder("Kept")), {"ok": True})
        self.assertEqual([e["name"] for e in self.api.get_save_places(str(self.reports))["recent"]], ["Kept"])

    def test_the_folder_picker_lists_folders_only(self):
        (self.reports / "inner").mkdir()
        listing = self.api.browse_directory(str(self.reports), "folder")
        self.assertEqual([(e["name"], e["is_dir"]) for e in listing["entries"]], [("inner", True)])


if __name__ == "__main__":
    unittest.main()
