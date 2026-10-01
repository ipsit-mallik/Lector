"""Tests for `features/dialogs/browser.py` (Milestone 8.7).

`unittest`, matching the rest of the suite. Run with:

    python -m unittest discover -s tests

Exercises `list_directory` against a real temporary directory tree (folders
and PDFs are simplest to prove correct as actual files rather than mocks),
and `default_open_dir`/`default_save_dir` against a mocked
`features.settings.store`, since they only need its return shape, not a real
settings.json.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.dialogs import browser  # noqa: E402


class ListDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-browser-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_folders_are_listed_before_files_both_alphabetically(self):
        (self.root / "Zebra").mkdir()
        (self.root / "apple").mkdir()
        (self.root / "b.pdf").touch()
        (self.root / "A.pdf").touch()

        result = browser.list_directory(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["apple", "Zebra", "A.pdf", "b.pdf"])
        self.assertTrue(result["entries"][0]["is_dir"])
        self.assertTrue(result["entries"][1]["is_dir"])
        self.assertFalse(result["entries"][2]["is_dir"])
        self.assertFalse(result["entries"][3]["is_dir"])

    def test_non_pdf_files_are_omitted(self):
        (self.root / "notes.txt").touch()
        (self.root / "doc.pdf").touch()

        result = browser.list_directory(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["doc.pdf"])

    def test_dotfolders_are_omitted(self):
        (self.root / ".git").mkdir()
        (self.root / "visible").mkdir()

        result = browser.list_directory(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["visible"])

    def test_windows_hidden_and_system_folders_are_omitted(self):
        (self.root / "$RECYCLE.BIN").mkdir()
        (self.root / "Reports").mkdir()
        flagged = {"$RECYCLE.BIN": browser.stat.FILE_ATTRIBUTE_HIDDEN | browser.stat.FILE_ATTRIBUTE_SYSTEM}
        real_stat = Path.stat

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            attributes = flagged.get(path.name, 0)
            return mock.Mock(st_mode=result.st_mode, st_file_attributes=attributes)

        with mock.patch.object(Path, "stat", autospec=True, side_effect=fake_stat):
            result = browser.list_directory(str(self.root))

        self.assertEqual([e["name"] for e in result["entries"]], ["Reports"])

    def test_a_system_only_flagged_folder_is_still_listed(self):
        # Customised user folders (desktop.ini) can carry the system attribute
        # alone; only hidden-flagged folders are noise.
        (self.root / "Customised").mkdir()
        flagged = {"Customised": browser.stat.FILE_ATTRIBUTE_SYSTEM}
        real_stat = Path.stat

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            return mock.Mock(st_mode=result.st_mode, st_file_attributes=flagged.get(path.name, 0))

        with mock.patch.object(Path, "stat", autospec=True, side_effect=fake_stat):
            result = browser.list_directory(str(self.root))

        self.assertEqual([e["name"] for e in result["entries"]], ["Customised"])

    def test_an_empty_directory_reports_no_entries_and_no_error(self):
        result = browser.list_directory(str(self.root))

        self.assertEqual(result["entries"], [])
        self.assertIsNone(result["error"])

    def test_parent_is_reported_for_a_non_root_directory(self):
        child = self.root / "child"
        child.mkdir()

        result = browser.list_directory(str(child))

        # Compared via `.resolve()` on both sides: Windows can report a
        # temp dir's short (8.3) form for one of the two independently
        # resolved paths, which is a filesystem quirk unrelated to what
        # this is checking — that the parent is `self.root`, however it is
        # spelled.
        self.assertEqual(Path(result["parent"]).resolve(), self.root.resolve())

    def test_the_filesystem_root_reports_no_parent(self):
        root = Path(self.tmp.name).parents[-1]

        result = browser.list_directory(str(root))

        self.assertIsNone(result["parent"])

    def test_the_filesystem_root_has_no_synthetic_other_drives_row(self):
        # Every drive is a permanent tree node now (`list_drives()`), so a
        # drive-root listing is only ever real folders/PDFs.
        root = Path(self.tmp.name).parents[-1]

        result = browser.list_directory(str(root))

        self.assertFalse(any(e["path"].startswith("__lector") for e in result["entries"]))

    def test_an_unreadable_directory_reports_an_error_instead_of_raising(self):
        missing = self.root / "does-not-exist"

        result = browser.list_directory(str(missing))

        self.assertEqual(result["entries"], [])
        self.assertIsNotNone(result["error"])


class ListDrivesTests(unittest.TestCase):
    def test_windows_lists_only_drive_letters_that_exist(self):
        exists_map = {"C:\\": True, "D:\\": True}
        with mock.patch.object(browser.platform, "system", return_value="Windows"), mock.patch.object(
            browser.Path, "exists", autospec=True, side_effect=lambda p: exists_map.get(str(p), False)
        ):
            result = browser.list_drives()

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["C:\\", "D:\\"])
        self.assertIsNone(result["parent"])

    def test_mac_surfaces_root_plus_mounted_volumes(self):
        with tempfile.TemporaryDirectory(prefix="lector-volumes-") as volumes_dir:
            volumes_path = Path(volumes_dir)
            (volumes_path / "Macintosh HD").mkdir()
            (volumes_path / "Backup Drive").mkdir()
            (volumes_path / ".hidden").mkdir()

            with mock.patch.object(browser.platform, "system", return_value="Darwin"), mock.patch.object(
                browser, "Path", side_effect=lambda p: volumes_path if p == "/Volumes" else Path(p)
            ):
                result = browser.list_drives()

        names = [e["name"] for e in result["entries"]]
        self.assertIn("/", names)
        self.assertTrue(any(n.endswith("Macintosh HD") for n in names))
        self.assertTrue(any(n.endswith("Backup Drive") for n in names))
        self.assertFalse(any(".hidden" in n for n in names))


    def test_mac_does_not_list_the_boot_volume_symlink_as_a_second_drive(self):
        # /Volumes/<boot volume> is a symlink back to "/", which is already
        # listed, so it must not appear as a duplicate node in the tree.
        with tempfile.TemporaryDirectory(prefix="lector-volumes-") as volumes_dir:
            volumes_path = Path(volumes_dir)
            (volumes_path / "Backup Drive").mkdir()
            (volumes_path / "Macintosh HD").mkdir()
            filesystem_root = Path("/").resolve()
            real_resolve = Path.resolve

            # A real symlink needs privileges some platforms withhold, so the
            # boot volume is simulated by making its resolve() land on "/".
            def fake_resolve(path, *args, **kwargs):
                if path.name == "Macintosh HD":
                    return filesystem_root
                return real_resolve(path, *args, **kwargs)

            with mock.patch.object(browser.platform, "system", return_value="Darwin"), mock.patch.object(
                browser, "Path", side_effect=lambda p: volumes_path if p == "/Volumes" else Path(p)
            ), mock.patch.object(Path, "resolve", autospec=True, side_effect=fake_resolve):
                result = browser.list_drives()

        names = [e["name"] for e in result["entries"]]
        self.assertFalse(any(n.endswith("Macintosh HD") for n in names))
        self.assertTrue(any(n.endswith("Backup Drive") for n in names))


class ListQuickAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-home-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def _quick_access(self) -> list[dict]:
        # `_known_folder` is pinned to home/<name> so these tests do not pick up
        # the real machine's (possibly redirected) Desktop/Documents/Downloads.
        with mock.patch.object(browser.Path, "home", return_value=self.home), mock.patch.object(
            browser, "_known_folder", side_effect=lambda name: self.home / name
        ):
            return browser.list_quick_access()["entries"]

    def test_a_plain_file_with_a_folders_name_is_not_offered(self):
        (self.home / "Downloads").touch()
        (self.home / "Desktop").mkdir()

        entries = self._quick_access()

        self.assertEqual([e["name"] for e in entries], ["Home", "Desktop"])

    def test_a_redirected_folder_is_offered_at_its_real_location(self):
        # OneDrive-style redirection: Documents lives outside the home folder.
        redirected = self.home / "OneDrive" / "Documents"
        redirected.mkdir(parents=True)
        with mock.patch.object(browser.Path, "home", return_value=self.home), mock.patch.object(
            browser,
            "_known_folder",
            side_effect=lambda name: redirected if name == "Documents" else self.home / name,
        ):
            entries = browser.list_quick_access()["entries"]

        self.assertEqual([e["name"] for e in entries], ["Home", "Documents"])
        self.assertEqual(entries[1]["path"], str(redirected))

    def test_home_is_always_first_even_with_no_children(self):
        entries = self._quick_access()

        self.assertEqual([e["name"] for e in entries], ["Home"])
        self.assertEqual(entries[0]["path"], str(self.home))
        self.assertTrue(entries[0]["is_dir"])

    def test_only_children_that_exist_are_offered_in_order(self):
        (self.home / "Downloads").mkdir()
        (self.home / "Desktop").mkdir()

        entries = self._quick_access()

        self.assertEqual([e["name"] for e in entries], ["Home", "Desktop", "Downloads"])
        self.assertEqual(entries[1]["path"], str(self.home / "Desktop"))

    def test_all_children_are_offered_when_all_exist(self):
        for name in ("Desktop", "Documents", "Downloads"):
            (self.home / name).mkdir()

        entries = self._quick_access()

        self.assertEqual(
            [e["name"] for e in entries], ["Home", "Desktop", "Documents", "Downloads"]
        )


class DefaultOpenDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-browser-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_uses_the_most_recent_files_folder_when_it_still_exists(self):
        recent_file = self.root / "report.pdf"
        recent_file.touch()
        with mock.patch.object(
            browser.settings, "get_recent_files", return_value=[{"path": str(recent_file)}]
        ):
            result = browser.default_open_dir()

        self.assertEqual(result, str(self.root))

    def test_falls_back_to_home_when_there_is_no_recent_file(self):
        with mock.patch.object(browser.settings, "get_recent_files", return_value=[]):
            result = browser.default_open_dir()

        self.assertEqual(result, str(Path.home()))

    def test_falls_back_to_home_when_the_recent_folder_is_gone(self):
        gone = self.root / "deleted" / "report.pdf"
        with mock.patch.object(
            browser.settings, "get_recent_files", return_value=[{"path": str(gone)}]
        ):
            result = browser.default_open_dir()

        self.assertEqual(result, str(Path.home()))


class DefaultSaveDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-browser-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_uses_the_suggested_paths_folder_when_it_exists(self):
        suggested = str(self.root / "report (highlighted).pdf")

        result = browser.default_save_dir(suggested)

        self.assertEqual(result, str(self.root))

    def test_falls_back_to_home_when_that_folder_is_gone(self):
        suggested = str(self.root / "deleted" / "report (highlighted).pdf")

        result = browser.default_save_dir(suggested)

        self.assertEqual(result, str(Path.home()))


if __name__ == "__main__":
    unittest.main()
