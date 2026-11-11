"""Organise's Extract… (Milestone F2.6), through the bridge and the real
worker process: the selected pages of the draft written as a new PDF, in
the draft's order, turned as the grid shows them, with unsaved highlights
and inserted pages; the pages left out written nowhere; a protected PDF's
copy protected the same way; the name and where it goes; and the draft and
the open document untouched (Also delete is the page's own plan step).
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
from lector.features.file_tools import destinations, jobs, naming  # noqa: E402
from lector.features.reading import ocr_cache  # noqa: E402
from lector.features.settings import store  # noqa: E402
from test_page_changes import MARKER, _file_holds, _marked_pdf  # noqa: E402

WAIT_S = 120


class PagesLabelTests(unittest.TestCase):
    def test_a_run_one_page_and_a_scattered_selection(self):
        self.assertEqual(naming.pages_label([4, 5, 6]), "pages 4–6")
        self.assertEqual(naming.pages_label([6, 4, 5]), "pages 4–6")
        self.assertEqual(naming.pages_label([4]), "page 4")
        self.assertEqual(naming.pages_label([1, 3, 7, 8, 9]), "5 pages")

    def test_the_suggestion_takes_the_next_free_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = str(Path(tmp) / "Annual Report.pdf")
            self.assertEqual(destinations.extract_stem(source, [4, 5, 6], tmp), "Annual Report (pages 4–6)")
            (Path(tmp) / "Annual Report (pages 4–6).pdf").write_bytes(b"x")
            self.assertEqual(destinations.extract_stem(source, [4, 5, 6], tmp), "Annual Report (pages 4–6) (2)")


class ExtractTests(unittest.TestCase):
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

    def events(self) -> list[dict]:
        found = []
        for call in self.window.evaluate_js.call_args_list:
            script = call.args[0]
            if "lector:filejob" in script:
                found.append(json.loads(script.split("detail: ", 1)[1].rsplit("}))", 1)[0]))
        return found

    def extract(self, positions: list[int], name: str = "part") -> dict:
        before = len([e for e in self.events() if e["state"] != jobs.WORKING])
        options = {"positions": positions, "folder": str(self.out), "name": name}
        started = self.api.start_file_job("extract", "open", options)
        self.assertTrue(started["ok"], started)
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            ends = [e for e in self.events() if e["state"] != jobs.WORKING]
            if len(ends) > before:
                self.assertEqual(ends[-1]["state"], jobs.DONE, ends[-1])
                self.api._jobs.wait(WAIT_S)
                return ends[-1]
            time.sleep(0.1)
        self.fail("no lector:filejob end event")

    @staticmethod
    def texts(path: Path, password: str | None = None) -> list[str]:
        with pymupdf.open(path) as doc:
            if password:
                doc.authenticate(password)
            return [page.get_text().strip() for page in doc]

    def other_pdf(self) -> Path:
        doc = pymupdf.open()
        for n in range(2):
            doc.new_page().insert_text((72, 100), f"Appendix page {n + 1}", fontsize=12)
        path = self.folder / "Appendix.pdf"
        doc.save(path)
        doc.close()
        return path

    def test_the_setup_names_the_selection_beside_the_original(self):
        source = pdf_support.multi_page(self.folder, 8, name="Report.pdf")
        self.api.open_pdf(str(source))
        self.api.organise_begin()
        self.assertEqual(self.api.organise_extract_setup([3, 4, 5]), {
            "ok": True, "count": 3, "label": "pages 4–6", "every_page": False,
            "output": {"folder": str(self.folder), "stem": "Report (pages 4–6)", "extension": ".pdf"},
        })
        scattered = self.api.organise_extract_setup([0, 2, 7])
        self.assertEqual((scattered["label"], scattered["output"]["stem"]), ("3 pages", "Report (3 pages)"))
        self.assertTrue(self.api.organise_extract_setup(list(range(8)))["every_page"])
        self.assertEqual(self.api.suggest_extract_name([3], str(self.out)), {"stem": "Report (page 4)"})
        for bad in ([], [8], [1, 1], "3", None):
            with self.subTest(positions=bad):
                self.assertEqual(self.api.organise_extract_setup(bad), {"ok": False})

    def test_the_selection_in_the_drafts_order_turned_with_highlights_and_inserted_pages(self):
        source = pdf_support.multi_page(self.folder, 5)
        self.api.open_pdf(str(source))
        # An unsaved highlight on page 4 (index 3).
        page = self.api._doc._doc[3]
        page.add_highlight_annot(page.search_for("topic4")[0])
        self.api._doc._dirty = True
        self.api.organise_begin()
        # Draft: page 4 moved to the front and turned, the appendix after page 2.
        self.api.organise_action("move", {"positions": [3], "gap": 0})
        self.api.organise_action("rotate", {"positions": [0], "degrees": 90})
        added = self.api.organise_add_source(str(self.other_pdf()))["source"]
        view = self.api.organise_action("insert", {"source": added, "gap": 2})
        keys = [p["key"] for p in view["pages"]]
        self.assertEqual(keys, ["doc:3", "doc:0", f"{added}:0", f"{added}:1", "doc:1", "doc:2", "doc:4"])
        final = self.extract([0, 2, 4], name="Picked")
        result = final["result"]
        self.assertEqual((result["name"], result["pages"]), ("Picked.pdf", 3))
        self.assertEqual(result["where"], "in out")
        written = self.out / "Picked.pdf"
        self.assertEqual(self.texts(written), [pdf_support.page_text(3), "Appendix page 1", pdf_support.page_text(1)])
        with pymupdf.open(written) as doc:
            self.assertEqual([p.rotation for p in doc], [90, 0, 0])
            self.assertEqual([[a.type[1] for a in p.annots()] for p in doc], [["Highlight"], [], []])
        # Nothing changed here: the draft, the document, its unsaved state.
        self.assertEqual([p["key"] for p in self.api.organise_begin()["pages"]], keys)
        state = self.api.get_state()
        self.assertEqual((state["page_count"], state["is_dirty"]), (5, True))

    def test_the_pages_left_out_are_written_nowhere(self):
        source = _marked_pdf(self.folder)
        self.api.open_pdf(str(source))
        self.api.organise_begin()
        self.extract([0, 2, 3])
        self.assertFalse(_file_holds(self.out / "part.pdf", MARKER))
        self.extract([1], name="marked")
        self.assertTrue(_file_holds(self.out / "marked.pdf", MARKER))

    def test_a_protected_pdf_gives_a_protected_copy(self):
        source = pdf_support.encrypted(self.folder)
        self.api.open_pdf(str(source), pdf_support.PASSWORD)
        self.api.organise_begin()
        self.extract([0])
        with pymupdf.open(self.out / "part.pdf") as doc:
            self.assertTrue(doc.needs_pass)
        self.assertEqual(self.texts(self.out / "part.pdf", pdf_support.PASSWORD), self.texts(source, pdf_support.PASSWORD))

    def test_an_unlocked_inserted_pdf_goes_in_without_its_password(self):
        source = pdf_support.multi_page(self.folder, 2)
        self.api.open_pdf(str(source))
        self.api.organise_begin()
        locked = pdf_support.encrypted(self.folder, name="other-locked.pdf")
        added = self.api.organise_add_source(str(locked), pdf_support.PASSWORD)["source"]
        self.api.organise_action("insert", {"source": added, "gap": 2})
        self.extract([2])
        self.assertEqual(self.texts(self.out / "part.pdf"), self.texts(locked, pdf_support.PASSWORD))

    def test_refused_requests(self):
        source = pdf_support.multi_page(self.folder, 3)
        self.api.open_pdf(str(source))
        here = {"folder": str(self.out), "name": "part"}
        # Organise isn't open.
        self.assertFalse(self.api.start_file_job("extract", "open", {"positions": [0], **here})["ok"])
        self.api.organise_begin()
        for source_arg, options in (
            ("open", {"positions": [5], **here}),
            ("open", {"positions": [], **here}),
            ("open", {"positions": "0", **here}),
            (str(source), {"positions": [0], **here}),
            ("open", {"positions": [0], "folder": str(self.folder), "name": source.stem}),  # the original
            ("open", {"positions": [0], "folder": str(self.out), "name": "a/b"}),
        ):
            with self.subTest(source=source_arg, options=options):
                self.assertFalse(self.api.start_file_job("extract", source_arg, options)["ok"])
        self.assertFalse(self.api._jobs.is_running())

    def test_the_destination_check_knows_extract(self):
        source = pdf_support.multi_page(self.folder, 3)
        self.api.open_pdf(str(source))
        (self.out / "part.pdf").write_bytes(b"x")
        check = self.api.check_tool_destination("extract", "open", str(self.out), "part")
        self.assertEqual((check["ok"], check["exists"]), (True, True))
        same = self.api.check_tool_destination("extract", "open", str(self.folder), source.stem)
        self.assertEqual((same["ok"], same["field"]), (False, "name"))


if __name__ == "__main__":
    unittest.main()
