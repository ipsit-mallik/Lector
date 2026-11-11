"""Organise's Insert… over the bridge (Milestone F2.5): a PDF, an image or
an e-book chosen as the source, a protected PDF's password, every page of it
put into the draft at a gap, the files Insert opened let go when they are
no longer wanted, and Done applying the inserted pages.
"""
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds and reads the fixtures

import file_tools_support  # noqa: E402
import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import engine as tool_engine  # noqa: E402
from lector.features.reading import ocr_cache, open_errors, page_plan  # noqa: E402
from lector.features.settings import store  # noqa: E402


def _inline_worker(fn, args=(), timeout=None, abort=None):
    """`OcrRunner.call`, run here rather than in the worker process."""
    future = Future()
    try:
        future.set_result(fn(*args))
    except Exception as exc:  # the worker hands back the call's own exception
        future.set_exception(exc)
    return future


class _InsertCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        for patcher in (
            mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json"),
            mock.patch.object(ocr_cache, "cache_dir", lambda: self.dir / "ocr-cache"),
            mock.patch.object(api_module.webview, "windows", [mock.Mock()]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api._voice = mock.MagicMock()
        self.api._ocr = mock.Mock()
        self.api._ocr.call.side_effect = _inline_worker
        self.addCleanup(self.api._doc.close)  # Windows will not delete an open file
        self.api.open_pdf(str(pdf_support.multi_page(self.dir, 5)))
        self.api.organise_begin()

    def _other_pdf(self, pages: int = 3) -> Path:
        folder = self.dir / "other"
        folder.mkdir(exist_ok=True)
        doc = pymupdf.open()
        for n in range(pages):
            doc.new_page().insert_text((72, 100), f"Appendix page {n + 1}", fontsize=12)
        path = folder / "Appendix.pdf"
        doc.save(path)
        doc.close()
        return path

    def _page_texts(self) -> list[str]:
        doc = self.api._doc._doc
        return [doc[i].get_text().strip() for i in range(len(doc))]


class InsertSourceTests(_InsertCase):
    def test_a_pdf_is_held_with_its_page_count(self):
        answer = self.api.organise_add_source(str(self._other_pdf(3)))
        self.assertEqual((answer["ok"], answer["pages"], answer["name"]), (True, 3, "Appendix.pdf"))
        self.assertEqual(self.api._doc.page_source_count(answer["source"]), 3)
        thumb = self.api.organise_thumbnail(answer["source"], 0, 52)
        self.assertTrue(thumb["image"])

    def test_an_image_becomes_one_page_at_its_own_size(self):
        png = file_tools_support.png_file(self.dir, "Receipt.png", (192, 96))
        answer = self.api.organise_add_source(str(png))
        self.assertEqual((answer["ok"], answer["pages"]), (True, 1))
        page = self.api._doc._page_sources[answer["source"]][0]
        # 96 image pixels to the inch: 192×96 px is 144×72 pt.
        self.assertAlmostEqual(page.rect.width, 144, delta=1)
        self.assertAlmostEqual(page.rect.height, 72, delta=1)

    def test_an_ebook_is_laid_out_into_pages(self):
        book = file_tools_support.epub_file(self.dir, paragraphs=80)
        answer = self.api.organise_add_source(str(book))
        self.assertTrue(answer["ok"])
        self.assertGreater(answer["pages"], 1)

    def test_a_tiff_gives_a_page_per_frame(self):
        tiff = file_tools_support.tiff_file(self.dir, frames=3)
        self.assertEqual(self.api.organise_add_source(str(tiff))["pages"], 3)

    def test_a_protected_pdf_asks_for_its_password_and_never_keeps_it(self):
        locked = pdf_support.encrypted(self.dir)
        first = self.api.organise_add_source(str(locked))
        self.assertEqual((first["ok"], first["reason"]), (False, open_errors.PASSWORD))
        wrong = self.api.organise_add_source(str(locked), "not it")
        self.assertEqual(wrong["reason"], open_errors.WRONG_PASSWORD)
        self.assertEqual(wrong["error"], open_errors.MESSAGES[open_errors.WRONG_PASSWORD])
        right = self.api.organise_add_source(str(locked), pdf_support.PASSWORD)
        self.assertTrue(right["ok"])
        settings_file = self.dir / "settings.json"
        if settings_file.exists():
            self.assertNotIn(pdf_support.PASSWORD, settings_file.read_text(encoding="utf-8"))

    def test_files_it_cant_use_say_why_by_name(self):
        text = self.dir / "notes.txt"
        text.write_text("hello", encoding="utf-8")
        junk = self.dir / "junk.pdf"
        junk.write_bytes(b"this was never a PDF")
        cases = (
            (str(self.dir / "gone.pdf"), open_errors.MISSING, "Couldn't find gone.pdf. It may have been moved or deleted."),
            (str(text), open_errors.NOT_A_PDF, "notes.txt can't be inserted. Choose a PDF, an image or an e-book."),
            (str(junk), open_errors.UNREADABLE, "Couldn't read junk.pdf. It may be damaged."),
        )
        for path, reason, error in cases:
            with self.subTest(reason=reason):
                answer = self.api.organise_add_source(path)
                self.assertEqual((answer["ok"], answer["reason"], answer["error"]), (False, reason, error))
        self.assertEqual(self.api._doc._page_sources, {})

    def test_a_damaged_image_is_unreadable(self):
        bad = self.dir / "broken.png"
        bad.write_bytes(b"\x89PNG\r\n\x1a\nnot really")
        answer = self.api.organise_add_source(str(bad))
        self.assertEqual(answer["reason"], open_errors.UNREADABLE)

    def test_nothing_is_opened_unless_organise_is(self):
        self.api.organise_cancel()
        answer = self.api.organise_add_source(str(self._other_pdf()))
        self.assertFalse(answer["ok"])
        self.assertEqual(self.api._doc._page_sources, {})
        for bad in (None, 3, ["a.pdf"]):
            with self.subTest(path=bad):
                self.assertFalse(self.api.organise_add_source(bad)["ok"])


class InsertActionTests(_InsertCase):
    def test_every_page_goes_in_at_the_gap_and_is_selected(self):
        source = self.api.organise_add_source(str(self._other_pdf(3)))["source"]
        view = self.api.organise_action("insert", {"source": source, "gap": 2})
        self.assertTrue(view["ok"])
        self.assertEqual(
            [p["key"] for p in view["pages"]],
            ["doc:0", "doc:1", f"{source}:0", f"{source}:1", f"{source}:2", "doc:2", "doc:3", "doc:4"],
        )
        self.assertEqual([p["new"] for p in view["pages"]][2:5], [True] * 3)
        self.assertEqual(view["selection"], [2, 3, 4])
        self.assertEqual(view["steps"], 1)

    def test_before_after_and_at_the_end(self):
        for gap, first_new in ((0, 0), (3, 3), (5, 5)):
            with self.subTest(gap=gap):
                source = self.api.organise_add_source(str(self._other_pdf(2)))["source"]
                view = self.api.organise_action("insert", {"source": source, "gap": gap})
                self.assertEqual(view["selection"], [first_new, first_new + 1])
                self.api.organise_undo()

    def test_refused_inserts_change_nothing(self):
        source = self.api.organise_add_source(str(self._other_pdf(2)))["source"]
        for args in ({"source": "insert-99", "gap": 0}, {"source": page_plan.DOC, "gap": 0},
                     {"source": source, "gap": 6}, {"source": source, "gap": True}, {"gap": 0}):
            with self.subTest(args=args):
                self.assertFalse(self.api.organise_action("insert", args)["ok"])
        self.api.organise_action("insert", {"source": source, "gap": 0})
        twice = self.api.organise_action("insert", {"source": source, "gap": 0})
        self.assertEqual(twice, {"ok": False, "reason": page_plan.BAD_SOURCE})

    def test_done_applies_the_inserted_pages(self):
        source = self.api.organise_add_source(str(self._other_pdf(2)))["source"]
        self.api.organise_action("insert", {"source": source, "gap": 1})
        state = self.api.organise_done()
        self.assertTrue(state["pages_changed"])
        self.assertEqual(state["page_count"], 7)
        own = [pdf_support.page_text(n) for n in range(5)]
        self.assertEqual(self._page_texts(), [own[0], "Appendix page 1", "Appendix page 2", *own[1:]])
        self.assertEqual(self.api._doc._page_sources, {})  # let go once applied
        # One reader undo takes the whole insert back.
        self.api.undo_highlight()
        self.assertEqual(self.api.get_state()["page_count"], 5)

    def test_a_protected_source_inserts_once_unlocked(self):
        source = self.api.organise_add_source(str(pdf_support.encrypted(self.dir)), pdf_support.PASSWORD)["source"]
        self.api.organise_action("insert", {"source": source, "gap": 5})
        self.assertEqual(self.api.organise_done()["page_count"], 6)


class ForgetSourceTests(_InsertCase):
    def test_a_file_chosen_and_not_put_in_is_let_go(self):
        source = self.api.organise_add_source(str(self._other_pdf()))["source"]
        self.api.organise_forget_source(source)
        self.assertIsNone(self.api._doc.page_source_count(source))

    def test_one_the_draft_uses_is_kept_even_undone(self):
        source = self.api.organise_add_source(str(self._other_pdf()))["source"]
        self.api.organise_action("insert", {"source": source, "gap": 0})
        self.api.organise_forget_source(source)
        self.assertEqual(self.api._doc.page_source_count(source), 3)
        self.api.organise_undo()  # still redoable
        self.api.organise_forget_source(source)
        self.assertEqual(self.api._doc.page_source_count(source), 3)
        self.assertTrue(self.api.organise_redo()["ok"])

    def test_cancel_lets_go_of_every_file(self):
        self.api.organise_add_source(str(self._other_pdf()))
        self.api.organise_cancel()
        self.assertEqual(self.api._doc._page_sources, {})

    def test_the_document_itself_is_never_forgotten(self):
        self.assertTrue(self.api.organise_forget_source(page_plan.DOC)["ok"])
        self.assertEqual(self.api.get_state()["page_count"], 5)


class InsertBrowseTests(_InsertCase):
    def test_the_open_dialog_lists_pdfs_and_what_convert_takes(self):
        folder = self.dir / "mixed"
        folder.mkdir()
        for name in ("a.pdf", "b.png", "c.epub", "d.txt", "e.jpg"):
            (folder / name).write_bytes(b"x")
        listing = self.api.browse_directory(str(folder), "insert")
        self.assertEqual([e["name"] for e in listing["entries"]], ["a.pdf", "b.png", "c.epub", "e.jpg"])


class ConvertToPdfBytesTests(unittest.TestCase):
    def test_an_image_comes_back_as_a_one_page_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            jpg = file_tools_support.jpeg_file(Path(tmp), "photo.jpg", (96, 48))
            data = tool_engine.convert_to_pdf_bytes(str(jpg))
        doc = pymupdf.open("pdf", data)
        self.addCleanup(doc.close)
        self.assertEqual(len(doc), 1)
        self.assertAlmostEqual(doc[0].rect.width, 72, delta=1)

    def test_an_unreadable_file_raises_unsupported_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.gif"
            bad.write_bytes(b"GIF89a broken")
            with self.assertRaises(tool_engine.UnsupportedInput):
                tool_engine.convert_to_pdf_bytes(str(bad))


if __name__ == "__main__":
    unittest.main()
