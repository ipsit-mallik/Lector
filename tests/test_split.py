"""Split (Milestone F2.9): the every-N boundaries (the last file shorter),
the page-ranges parser (good input, en dashes, out of bounds, overlaps,
backwards, gaps reported), the live preview, the names and the folder, and
the job through the bridge and the real worker process: annotations kept,
bookmarks kept within each part and retargeted, a protected PDF's parts
still protected, and what the bridge refuses.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds and reads the fixtures

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import destinations, jobs, messages, naming, trash  # noqa: E402
from lector.features.file_tools import tools as file_tools_module  # noqa: E402
from lector.features.reading import ocr_cache, open_errors, safe_write  # noqa: E402
from lector.features.settings import store  # noqa: E402

WAIT_S = 120


def _pdf(path: Path, pages: int, toc: list | None = None, highlight_on: int | None = None, password: str | None = None) -> Path:
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page().insert_text((72, 100), pdf_support.page_text(n), fontsize=12)
    if highlight_on is not None:
        page = doc[highlight_on]
        page.add_highlight_annot(page.search_for(f"topic{highlight_on + 1}")[0])
    if toc:
        doc.set_toc(toc)
    if password:
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password[::-1])
    else:
        doc.save(path)
    doc.close()
    return path


class EveryTests(unittest.TestCase):
    def test_the_boundaries_and_the_short_last_file(self):
        self.assertEqual(naming.split_every("10", 25), [(1, 10), (11, 20), (21, 25)])
        self.assertEqual(naming.split_every("10", 30), [(1, 10), (11, 20), (21, 30)])
        self.assertEqual(naming.split_every(" 24 ", 25), [(1, 24), (25, 25)])
        self.assertEqual(naming.split_every("1", 3), [(1, 1), (2, 2), (3, 3)])
        self.assertEqual(naming.split_every(4, 9), [(1, 4), (5, 8), (9, 9)])

    def test_n_runs_from_1_to_one_less_than_the_pages(self):
        for bad in ("0", "25", "", "x", "2.5", "-3", True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(naming.RangeError) as caught:
                    naming.split_every(bad, 25)
                self.assertEqual(caught.exception.message, "Enter a number of pages from 1 to 24.")
        with self.assertRaises(naming.RangeError) as caught:
            naming.split_every("1", 1)
        self.assertEqual(caught.exception.message, naming.ONE_PAGE)

    def test_the_first_n_is_10_or_half_the_pages_when_smaller(self):
        cases = {120: 10, 20: 10, 19: 10, 15: 8, 3: 2, 2: 1, 1: 1}
        for pages, n in cases.items():
            with self.subTest(pages=pages):
                self.assertEqual(naming.default_split_every(pages), n)


class RangesTests(unittest.TestCase):
    def test_each_part_is_its_own_file_in_the_order_typed(self):
        self.assertEqual(
            naming.parse_split_ranges("1–10, 11-40,41—120", 120), [(1, 10), (11, 40), (41, 120)]
        )
        self.assertEqual(naming.parse_split_ranges("11 - 20 , 5", 30), [(11, 20), (5, 5)])

    def test_overlaps_are_allowed(self):
        self.assertEqual(naming.parse_split_ranges("1–5, 3–8, 3–8", 10), [(1, 5), (3, 8), (3, 8)])

    def test_what_it_refuses_and_says(self):
        cases = {
            "1–130": "“1–130” isn’t a page or range within 1–120.",
            "0": "“0” isn’t a page or range within 1–120.",
            "1–10, abc": "“abc” isn’t a page or range within 1–120.",
            "4-": "“4-” isn’t a page or range within 1–120.",
            "9–3": "“9–3” runs backwards. Try 3–9.",
            "": naming.RANGES_EMPTY,
            " , ,": naming.RANGES_EMPTY,
            "1," * 300: "That's too long for a list of pages.",
        }
        for text, message in cases.items():
            with self.subTest(text=text[:20]):
                with self.assertRaises(naming.RangeError) as caught:
                    naming.parse_split_ranges(text, 120)
                self.assertEqual(caught.exception.message, message)

    def test_gaps_are_found_and_said(self):
        self.assertEqual(naming.uncovered_pages([(1, 10), (21, 30)], 40), [(11, 20), (31, 40)])
        self.assertEqual(naming.uncovered_pages([(1, 5), (3, 10)], 10), [])
        self.assertIsNone(naming.gaps_sentence([]))
        self.assertEqual(naming.gaps_sentence([(5, 5)]), "Page 5 isn’t in any file.")
        self.assertEqual(naming.gaps_sentence([(121, 130)]), "Pages 121–130 aren’t in any file.")
        self.assertEqual(naming.gaps_sentence([(3, 3), (7, 9)]), "Pages 3 and 7–9 aren’t in any file.")
        self.assertEqual(
            naming.gaps_sentence([(2, 2), (4, 4), (6, 7), (9, 9)]), "Pages 2, 4, 6–7 and others aren’t in any file."
        )


class PreviewAndNamesTests(unittest.TestCase):
    def test_the_preview_names_three_then_counts_the_rest(self):
        preview = naming.split_preview("every", "10", 120, "Annual Report 2025")
        self.assertEqual((preview["count"], preview["more"], preview["gaps"]), (12, 9, None))
        self.assertEqual(preview["named"][0], {"name": "Annual Report 2025 (part 1).pdf", "pages": "pages 1–10"})
        self.assertEqual([n["pages"] for n in preview["named"]], ["pages 1–10", "pages 11–20", "pages 21–30"])
        ranged = naming.split_preview("ranges", "1–10, 15", 20, "R")
        self.assertEqual((ranged["count"], ranged["more"]), (2, 0))
        self.assertEqual(ranged["named"][1]["pages"], "page 15")
        self.assertEqual(ranged["gaps"], "Pages 11–14 and 16–20 aren’t in any file.")
        self.assertEqual(naming.split_preview("ranges", "x", 20, "R"),
                         {"error": "“x” isn’t a page or range within 1–20.", "count": 0})
        self.assertEqual(naming.split_preview("sideways", "1", 20, "R")["count"], 0)

    def test_the_folder_and_its_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = str(Path(tmp) / "Annual Report.pdf")
            self.assertEqual(destinations.suggested_stem(messages.SPLIT, source, tmp), "Annual Report (split)")
            self.assertEqual(destinations.extension(messages.SPLIT), "")
            (Path(tmp) / "Annual Report (split)").mkdir()
            self.assertEqual(destinations.suggested_stem(messages.SPLIT, source, tmp), "Annual Report (split) (2)")
        self.assertEqual(naming.split_part_name("Annual Report", 12), "Annual Report (part 12).pdf")
        self.assertEqual(messages.message("disk_full", messages.SPLIT),
                         "Couldn't split the PDF: the disk is full. The original is unchanged.")


class SplitJobTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.out = self.folder / "out"
        self.out.mkdir()
        self.window = mock.Mock()
        for patcher in (
            mock.patch.object(store, "_settings_path", lambda: self.folder / "settings.json"),
            mock.patch.object(ocr_cache, "cache_dir", lambda: self.folder / "ocr-cache"),
            mock.patch.object(api_module.webview, "windows", [self.window]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.api._voice = mock.MagicMock()

    def tearDown(self):
        self.api._jobs.cancel()
        self.api._jobs.wait(WAIT_S)
        self.api._ocr.shutdown()
        self.api._doc.close()
        self._tmp.cleanup()

    def ends(self) -> list[dict]:
        found = []
        for call in self.window.evaluate_js.call_args_list:
            script = call.args[0]
            if "lector:filejob" in script:
                event = json.loads(script.split("detail: ", 1)[1].rsplit("}))", 1)[0])
                if event["state"] != jobs.WORKING:
                    found.append(event)
        return found

    def split(self, source: Path, mode: str, value: str, name: str = "parts", **extra) -> dict:
        end = self.run_split(source, mode, value, name, **extra)
        self.assertEqual(end["state"], jobs.DONE, end)
        return end["result"]

    def run_split(self, source: Path, mode: str, value: str, name: str = "parts", **extra) -> dict:
        before = len(self.ends())
        options = {"mode": mode, "value": value, "folder": str(self.out), "name": name, **extra}
        started = self.api.start_file_job("split", str(source), options)
        self.assertTrue(started["ok"], started)
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if len(self.ends()) > before:
                self.api._jobs.wait(WAIT_S)
                return self.ends()[-1]
            time.sleep(0.1)
        self.fail("no lector:filejob end event")

    def texts(self, path: Path, password: str | None = None) -> list[str]:
        with pymupdf.open(path) as doc:
            if password:
                self.assertTrue(doc.authenticate(password))
            return [page.get_text().strip() for page in doc]

    def test_every_n_keeps_annotations_and_retargets_bookmarks(self):
        toc = [[1, "One", 1], [1, "Two", 11], [2, "Two, more", 12], [1, "Three", 21]]
        report = _pdf(self.folder / "Report.pdf", 25, toc=toc, highlight_on=11)
        info = self.api.file_tool_info("split", str(report))
        self.assertEqual((info["pages"], info["split_every"], info["output"]["stem"]), (25, 10, "Report (split)"))
        result = self.split(report, "every", "10")
        self.assertEqual((result["files"], result["where"]), (3, "in out"))
        folder = self.out / "parts"
        self.assertEqual(Path(result["path"]), folder)
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["Report (part 1).pdf", "Report (part 2).pdf", "Report (part 3).pdf"])
        self.assertEqual(self.texts(folder / "Report (part 3).pdf"),
                         [pdf_support.page_text(n) for n in range(20, 25)])
        with pymupdf.open(folder / "Report (part 2).pdf") as doc:
            self.assertEqual(len(doc), 10)
            self.assertEqual([[a.type[1] for a in p.annots()] for p in doc][:3], [[], ["Highlight"], []])
            self.assertEqual(doc.get_toc(simple=True), [[1, "Two", 1], [2, "Two, more", 2]])
        with pymupdf.open(folder / "Report (part 1).pdf") as doc:
            self.assertEqual(doc.get_toc(simple=True), [[1, "One", 1]])
        # The original is as it was.
        self.assertEqual(len(self.texts(report)), 25)

    def test_a_chapter_cut_in_two_keeps_its_sections_in_the_second_part(self):
        # MuPDF alone would drop "Section 2" from part 2 with its chapter.
        toc = [[1, "Chapter", 1], [2, "Section 1", 2], [2, "Section 2", 6], [1, "Next", 9]]
        source = _pdf(self.folder / "Book.pdf", 10, toc=toc)
        result = self.split(source, "every", "5")
        with pymupdf.open(Path(result["path"]) / "Book (part 2).pdf") as doc:
            self.assertEqual(doc.get_toc(simple=True), [[1, "Chapter", -1], [2, "Section 2", 1], [1, "Next", 4]])
        with pymupdf.open(Path(result["path"]) / "Book (part 1).pdf") as doc:
            self.assertEqual(doc.get_toc(simple=True), [[1, "Chapter", 1], [2, "Section 1", 2]])

    def test_ranges_overlap_and_leave_pages_out(self):
        source = _pdf(self.folder / "Notes.pdf", 10)
        result = self.split(source, "ranges", "1–5, 3–8")
        self.assertEqual(result["files"], 2)
        folder = Path(result["path"])
        self.assertEqual(self.texts(folder / "Notes (part 2).pdf"), [pdf_support.page_text(n) for n in range(2, 8)])
        preview = self.api.split_preview(str(source), "ranges", "1–5, 3–8", 10)
        self.assertEqual(preview["gaps"], "Pages 9–10 aren’t in any file.")

    def test_a_protected_pdf_asks_and_its_parts_stay_protected(self):
        locked = _pdf(self.folder / "Locked.pdf", 4, password=pdf_support.PASSWORD)
        asked = self.api.file_tool_info("split", str(locked))
        self.assertEqual(asked["reason"], open_errors.PASSWORD)
        info = self.api.file_tool_info("split", str(locked), pdf_support.PASSWORD)
        self.assertEqual((info["ok"], info["pages"], info["split_every"]), (True, 4, 2))
        result = self.split(locked, "every", "2")
        for part in Path(result["path"]).iterdir():
            with pymupdf.open(part) as doc:
                self.assertTrue(doc.needs_pass)
            self.assertEqual(len(self.texts(part, pdf_support.PASSWORD)), 2)

    def test_a_pdf_whose_owner_forbids_changes_is_refused(self):
        restricted = self.folder / "Restricted.pdf"
        doc = pymupdf.open()
        for n in range(4):
            doc.new_page().insert_text((72, 100), pdf_support.page_text(n), fontsize=12)
        # No user password, so it opens; the owner allows printing only.
        doc.save(restricted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner",
                 permissions=pymupdf.PDF_PERM_PRINT)
        doc.close()
        info = self.api.file_tool_info("split", str(restricted))
        self.assertEqual((info["ok"], info["error"]), (False, "This PDF's owner doesn't allow changing its pages."))
        options = {"mode": "every", "value": "2", "folder": str(self.out), "name": "x"}
        self.assertFalse(self.api.start_file_job("split", str(restricted), options)["ok"])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_one_range_makes_one_pdf_named_for_its_pages(self):
        source = _pdf(self.folder / "Notes.pdf", 10, highlight_on=2)
        info = self.api.file_tool_info("split", str(source))
        self.assertEqual(info["trash_name"], trash.TRASH_NAME)
        preview = self.api.split_preview(str(source), "ranges", "1–6", 10)
        self.assertEqual(preview["count"], 1)
        self.assertEqual(preview["single"], {"label": "pages 1–6", "first": 1, "last": 6, "stem": "Notes (pages 1–6)"})
        self.assertEqual(preview["named"][0]["name"], "Notes (pages 1–6).pdf")
        # The note about the pages left out still holds.
        self.assertEqual(preview["gaps"], "Pages 7–10 aren’t in any file.")
        self.assertEqual(self.api.split_preview(str(source), "ranges", "4", 10)["single"]["label"], "page 4")
        named = self.api.suggest_tool_name("split_one", str(source), str(self.out), [1, 6])
        self.assertEqual(named, {"stem": "Notes (pages 1–6)", "extension": ".pdf"})
        four = self.api.suggest_tool_name("split_one", str(source), str(self.out), [4, 4])
        self.assertEqual(four["stem"], "Notes (page 4)")
        checked = self.api.check_tool_destination("split_one", str(source), str(self.out), "Notes (pages 1–6)")
        self.assertEqual((checked["ok"], checked["name"]), (True, "Notes (pages 1–6).pdf"))
        result = self.split(source, "ranges", "1–6", name="Notes (pages 1–6)")
        made = self.out / "Notes (pages 1–6).pdf"
        self.assertEqual((Path(result["path"]), result["files"], result["single"]), (made, 1, True))
        self.assertEqual(self.texts(made), [pdf_support.page_text(n) for n in range(6)])
        with pymupdf.open(made) as doc:
            self.assertEqual([len(list(p.annots())) for p in doc][:3], [0, 0, 1])
        self.assertTrue(source.exists())
        # Once taken, the next suggestion is the free one.
        again = self.api.suggest_tool_name("split_one", str(source), str(self.out), [1, 6])
        self.assertEqual(again["stem"], "Notes (pages 1–6) (2)")

    def test_one_ranges_preview_names_the_file_it_saves(self):
        source = _pdf(self.folder / "Notes.pdf", 10)
        _pdf(self.folder / "Notes (pages 1–6).pdf", 2)
        preview = self.api.split_preview(str(source), "ranges", "1–6", 10)
        self.assertEqual(preview["named"][0]["name"], "Notes (pages 1–6) (2).pdf")
        self.assertEqual(preview["single"]["stem"], "Notes (pages 1–6) (2)")
        beside = self.api.suggest_tool_name("split_one", str(source), str(self.folder), [1, 6])
        self.assertEqual(beside["stem"], preview["single"]["stem"])

    def test_one_range_of_an_extract_states_its_own_pages(self):
        source = _pdf(self.folder / "Report (pages 4–6).pdf", 3)
        preview = self.api.split_preview(str(source), "ranges", "1–2", 3)
        self.assertEqual(preview["named"][0]["name"], "Report (pages 1–2).pdf")
        named = self.api.suggest_tool_name("split_one", str(source), str(self.out), [1, 2])
        self.assertEqual(named["stem"], "Report (pages 1–2)")

    def test_the_original_moves_to_the_recycle_bin_only_after_every_file_is_checked(self):
        source = _pdf(self.folder / "Report.pdf", 10)
        store.add_recent_file(str(source))
        store.add_favorite(str(source))
        seen = []

        def move(path):
            folder = self.out / "parts"
            seen.append((path, sorted(p.name for p in folder.iterdir()) if folder.is_dir() else None))
            return None

        self.api.file_tool_info("split", str(source))
        with mock.patch.object(trash, "move_to_trash", side_effect=move):
            result = self.split(source, "every", "5", trash=True)
        self.assertEqual(seen, [(str(source), ["Report (part 1).pdf", "Report (part 2).pdf"])])
        self.assertEqual(result["original"], {"moved": True, "trash_name": trash.TRASH_NAME})
        self.assertEqual(store.get_recent_files(), [])
        self.assertEqual(store.get_favorites(), [])

    def test_nothing_is_moved_when_an_output_fails_its_check(self):
        source = _pdf(self.folder / "Report.pdf", 10)
        self.api.file_tool_info("split", str(source))
        failing = mock.patch.object(
            file_tools_module, "_check_pdf", side_effect=jobs.JobFailed(safe_write.VERIFY_FAILED)
        )
        with mock.patch.object(trash, "move_to_trash") as move, failing:
            end = self.run_split(source, "every", "5", trash=True)
            single = self.run_split(source, "ranges", "2–3", name="one", trash=True)
        self.assertEqual((end["state"], single["state"]), (jobs.FAILED, jobs.FAILED))
        move.assert_not_called()
        self.assertTrue(source.exists())
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_move_that_fails_keeps_the_new_files_and_says_why(self):
        source = _pdf(self.folder / "Report.pdf", 10)
        store.add_recent_file(str(source))
        self.api.file_tool_info("split", str(source))
        with mock.patch.object(trash, "move_to_trash", return_value=trash.IN_USE):
            result = self.split(source, "ranges", "1–6", name="kept", trash=True)
        self.assertTrue((self.out / "kept.pdf").exists())
        self.assertEqual(result["original"]["moved"], False)
        self.assertEqual(result["original"]["error"], "it's open in another program.")
        self.assertEqual([e["path"] for e in store.get_recent_files()], [str(source)])

    def test_an_original_open_in_the_reader_is_closed_before_it_moves(self):
        source = _pdf(self.folder / "Open.pdf", 4)
        self.assertTrue(self.api.open_pdf(str(source))["is_open"])
        open_when_moved = []

        def move(path):
            open_when_moved.append(self.api._doc.is_open)
            return None

        self.api.file_tool_info("split", str(source))
        with mock.patch.object(trash, "move_to_trash", side_effect=move):
            self.split(source, "every", "2", trash=True)
        self.assertEqual(open_when_moved, [False])
        self.assertEqual(self.api._state(), {"is_open": False})

    def test_the_readers_own_document_is_never_moved(self):
        source = _pdf(self.folder / "Open.pdf", 4)
        self.api.open_pdf(str(source))
        options = {"mode": "every", "value": "2", "folder": str(self.out), "name": "x", "trash": True}
        self.assertFalse(self.api.start_file_job("split", "open", options)["ok"])

    def test_what_the_bridge_refuses(self):
        source = _pdf(self.folder / "Short.pdf", 10)
        self.api.file_tool_info("split", str(source))
        here = {"folder": str(self.out), "name": "x"}
        for options in (
            {"mode": "every", "value": "0"},
            {"mode": "every", "value": "10"},  # every N always makes two or more
            {"mode": "ranges", "value": "1–6", "trash": "yes"},
            {"mode": "ranges", "value": "1–11, 2"},
            {"mode": "sideways", "value": "2"},
            {},
        ):
            with self.subTest(options=options):
                self.assertFalse(self.api.start_file_job("split", str(source), {**options, **here})["ok"])
        gone = self.api.start_file_job("split", str(self.folder / "gone.pdf"), {"mode": "every", "value": "2", **here})
        self.assertFalse(gone["ok"])
        one = _pdf(self.folder / "One.pdf", 1)
        self.assertEqual(self.api.split_preview(str(one), "every", "1", 1)["error"], naming.ONE_PAGE)


if __name__ == "__main__":
    unittest.main()
