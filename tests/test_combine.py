"""Combine files (Milestone F2.8), through the bridge and the real worker
process: the order kept, annotations kept, each file's bookmarks nested
under one named after it (one top-level bookmark for a file with none), a
protected input asking for its password and the result not protected, an
image going in at its own size, a signed input flagged, clashing form field
names reported, fewer than two files refused, and what a drop offers it.
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

import file_tools_support  # noqa: E402
import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import destinations, engine, jobs, messages  # noqa: E402
from lector.features.home import file_drop  # noqa: E402
from lector.features.reading import ocr_cache, open_errors  # noqa: E402
from lector.features.settings import store  # noqa: E402

WAIT_S = 120


def _pdf(path: Path, texts: list[str], toc: list | None = None, highlight: bool = False) -> Path:
    doc = pymupdf.open()
    for text in texts:
        doc.new_page().insert_text((72, 100), text, fontsize=12)
    if highlight:
        page = doc[0]
        page.add_highlight_annot(page.search_for(texts[0])[0])
    if toc:
        doc.set_toc(toc)
    doc.save(path)
    doc.close()
    return path


def _form(path: Path, field: str, value: str) -> Path:
    doc = pymupdf.open()
    widget = pymupdf.Widget()
    widget.field_name = field
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(72, 72, 300, 100)
    widget.field_value = value
    doc.new_page().add_widget(widget)
    doc.save(path)
    doc.close()
    return path


class NestedTocTests(unittest.TestCase):
    def test_no_level_jumps_more_than_one(self):
        self.assertEqual(
            engine._nested([[1, "A", 1], [3, "deep", 2], [2, "B", 3], [1, "C", 4]]),
            [[1, "A", 1], [2, "deep", 2], [2, "B", 3], [1, "C", 4]],
        )

    def test_the_name_and_the_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = str(Path(tmp) / "Annual Report.pdf")
            self.assertEqual(destinations.suggested_stem(messages.COMBINE, first, tmp), "Annual Report (combined)")
            self.assertEqual(messages.message("disk_full", messages.COMBINE),
                             "Couldn't combine the files: the disk is full. The original files are unchanged.")


class DropTests(unittest.TestCase):
    def test_a_drop_offers_combine_every_pdf_image_and_e_book_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            a, b = pdf_support.multi_page(folder, 1, name="a.pdf"), pdf_support.multi_page(folder, 1, name="b.pdf")
            png = file_tools_support.png_file(folder, "c.png", (8, 8))
            txt = folder / "d.txt"
            txt.write_text("x", encoding="utf-8")
            event = {"dataTransfer": {"files": [{"pywebviewFullPath": str(p)} for p in (b, txt, png, a)]}}
            self.assertEqual(file_drop.combinable_from_drop(event), [str(b), str(png), str(a)])
            outcome = file_drop.with_combinable(file_drop.pdf_from_drop(event), file_drop.combinable_from_drop(event))
            self.assertEqual((outcome["path"], len(outcome["combinable"])), (str(b), 3))  # opening still works
            self.assertIsNone(file_drop.with_combinable(None, [str(a)]))  # no files: still nothing


class CombineTests(unittest.TestCase):
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

    def ends(self) -> list[dict]:
        return [e for e in self.events() if e["state"] != jobs.WORKING]

    def run_job(self, inputs: list[Path], name: str) -> dict:
        before = len(self.ends())
        options = {"inputs": [str(p) for p in inputs], "folder": str(self.out), "name": name}
        started = self.api.start_file_job("combine", str(inputs[0]), options)
        self.assertTrue(started["ok"], started)
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if len(self.ends()) > before:
                self.api._jobs.wait(WAIT_S)
                return self.ends()[-1]
            time.sleep(0.1)
        self.fail("no lector:filejob end event")

    def combine(self, inputs: list[Path], name: str = "joined") -> dict:
        end = self.run_job(inputs, name)
        self.assertEqual(end["state"], jobs.DONE, end)
        return end["result"]

    def test_order_annotations_and_nested_bookmarks(self):
        report = _pdf(self.folder / "Report.pdf", ["Report one", "Report two", "Report three"],
                      toc=[[1, "Intro", 1], [2, "Detail", 2], [1, "End", 3]], highlight=True)
        plain = _pdf(self.folder / "Appendix.pdf", ["Appendix one", "Appendix two"])
        result = self.combine([plain, report])
        self.assertEqual((result["pages"], result["files"], result["clashes"]), (5, 2, []))
        self.assertEqual(result["where"], "in out")
        with pymupdf.open(self.out / "joined.pdf") as doc:
            self.assertEqual([p.get_text().strip() for p in doc],
                             ["Appendix one", "Appendix two", "Report one", "Report two", "Report three"])
            self.assertEqual([[a.type[1] for a in p.annots()] for p in doc], [[], [], ["Highlight"], [], []])
            self.assertEqual(doc.get_toc(simple=True), [
                [1, "Appendix", 1],
                [1, "Report", 3], [2, "Intro", 3], [3, "Detail", 4], [2, "End", 5],
            ])
            self.assertFalse(doc.needs_pass)

    def test_a_protected_pdf_asks_and_the_result_is_not_protected(self):
        locked = pdf_support.encrypted(self.folder)
        plain = _pdf(self.folder / "Plain.pdf", ["Plain page"])
        first = self.api.describe_combine_inputs([str(locked), str(plain)])
        self.assertEqual((first[0]["reason"], first[0]["error"]), (open_errors.PASSWORD, open_errors.MESSAGES["password"]))
        self.assertEqual((first[1]["pages"], first[1]["type"], first[1]["pdf"]), (1, "PDF", True))
        self.assertEqual(self.api.unlock_combine_input(str(locked), "nope")["reason"], open_errors.WRONG_PASSWORD)
        unlocked = self.api.unlock_combine_input(str(locked), pdf_support.PASSWORD)
        self.assertTrue(unlocked["needs_pass"])
        self.assertTrue(unlocked["thumbnail"])
        self.combine([locked, plain])
        with pymupdf.open(self.out / "joined.pdf") as doc:
            self.assertFalse(doc.needs_pass)
            self.assertEqual(len(doc), 2)
        settings_file = self.folder / "settings.json"
        if settings_file.exists():
            self.assertNotIn(pdf_support.PASSWORD, settings_file.read_text(encoding="utf-8"))
        # Closing the dialog lets go of the password: a later job can't read the file.
        self.api.forget_combine_passwords()
        self.assertEqual(self.run_job([locked, plain], "again")["state"], jobs.FAILED)

    def test_an_image_goes_in_at_its_own_size(self):
        report = _pdf(self.folder / "Report.pdf", ["Report one"])
        png = file_tools_support.png_file(self.folder, "Receipt.png", (192, 96))
        described = self.api.describe_combine_inputs([str(png)])[0]
        self.assertEqual((described["type"], described["pages"]), ("PNG", 1))
        self.combine([report, png])
        with pymupdf.open(self.out / "joined.pdf") as doc:
            self.assertAlmostEqual(doc[1].rect.width, 144, delta=1)
            self.assertEqual(doc.get_toc(simple=True), [[1, "Report", 1], [1, "Receipt", 2]])

    def test_a_signed_input_is_flagged(self):
        signed = file_tools_support.signed_flag_pdf(self.folder)
        self.assertTrue(self.api.describe_combine_inputs([str(signed)])[0]["signed"])

    def test_clashing_form_fields_are_reported_by_file(self):
        a = _form(self.folder / "First form.pdf", "name", "Alice")
        b = _form(self.folder / "Second form.pdf", "name", "Bob")
        c = _form(self.folder / "Third form.pdf", "city", "Paris")
        result = self.combine([a, b, c])
        self.assertEqual(result["clashes"], ["Second form.pdf"])
        with pymupdf.open(self.out / "joined.pdf") as doc:
            fields = [(w.field_name.split(" [")[0], w.field_value) for p in doc for w in p.widgets()]
        # Every field kept, with its value; MuPDF renamed the second "name".
        self.assertEqual(fields, [("name", "Alice"), ("name", "Bob"), ("city", "Paris")])

    def test_files_it_cant_use_and_too_few(self):
        txt = self.folder / "notes.txt"
        txt.write_text("x", encoding="utf-8")
        junk = self.folder / "junk.pdf"
        junk.write_bytes(b"never a PDF")
        answers = self.api.describe_combine_inputs([str(txt), str(junk), str(self.folder / "gone.pdf")])
        self.assertEqual([a["error"] for a in answers], [
            "notes.txt can't be combined. Choose PDFs, images or e-books.",
            "junk.pdf couldn't be read.",
            "Couldn't find gone.pdf.",
        ])
        one = _pdf(self.folder / "One.pdf", ["One"])
        here = {"folder": str(self.out), "name": "x"}
        for inputs in ([str(one)], [], "a", [str(one), str(txt)], [str(one), str(self.folder / "gone.pdf")]):
            with self.subTest(inputs=inputs):
                self.assertFalse(self.api.start_file_job("combine", str(one), {"inputs": inputs, **here})["ok"])
        # Never over one of the files given.
        two = _pdf(self.folder / "Two.pdf", ["Two"])
        check = self.api.check_tool_destination("combine", str(one), str(self.folder), "Two", [str(one), str(two)])
        self.assertEqual((check["ok"], check["error"]), (False, destinations.NAME_IS_INPUT))
        self.assertEqual(self.api.suggest_tool_name("combine", str(one), str(self.folder))["stem"], "One (combined)")


if __name__ == "__main__":
    unittest.main()
