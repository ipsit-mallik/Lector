"""Organise pages over the bridge (Milestone F2.2; no UI yet): the draft's
actions, its own Undo/Redo, Done applying it as one reader undo step, the
reader's state after it, and scanned pages after a move — their text from
the disk OCR cache, voice highlighting on their own words.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the fixtures

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading import ocr, ocr_cache, page_changes, page_plan  # noqa: E402
from lector.features.settings import store  # noqa: E402

# Prose dense with common words, as `ocr.is_readable` asks of a page's text,
# with one word on each page that the other doesn't have.
APPLES = (
    "Apples are what this page is about. It is one of the pages that were "
    "scanned, and there is no text in it other than what was read from it."
)
ZEBRAS = (
    "Zebras are what this page is about. It is one of the pages that were "
    "scanned, and there is no text in it other than what was read from it."
)


def _text_page(doc: pymupdf.Document, text: str) -> pymupdf.Page:
    page = doc.new_page(width=420, height=300)
    page.insert_textbox(pymupdf.Rect(30, 30, 390, 270), text, fontsize=14)
    return page


def _payload_for(page: pymupdf.Page) -> dict:
    """What `ocr.recognize` returns for a page it read perfectly."""
    return {
        "chars": [
            [c.rect.x0, c.rect.y0, c.rect.x1, c.rect.y1, c.text, c.block_no, c.line_no, c.char_no]
            for c in highlighter.chars_on_page(page)
        ],
        "words": [
            [w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1, w.text, w.block_no, w.line_no, w.word_no]
            for w in highlighter.words_on_page(page)
        ],
    }


class _ApiCase(unittest.TestCase):
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
        self.addCleanup(self.api._doc.close)  # Windows will not delete an open file


class OrganiseApiTests(_ApiCase):
    def setUp(self):
        super().setUp()
        self.api.open_pdf(str(pdf_support.multi_page(self.dir, 5)))

    @staticmethod
    def _keys(view: dict) -> list[str]:
        return [p["key"] for p in view["pages"]]

    def test_begin_shows_the_document_as_it_is(self):
        view = self.api.organise_begin()
        self.assertTrue(view["ok"])
        self.assertEqual(self._keys(view), [f"doc:{i}" for i in range(5)])
        self.assertFalse(view["can_undo"])
        self.assertEqual(view["steps"], 0)

    def test_actions_change_only_the_draft(self):
        self.api.organise_begin()
        view = self.api.organise_action("rotate", {"positions": [0], "degrees": 90})
        self.assertEqual(view["pages"][0]["turn"], 90)
        self.assertEqual(view["selection"], [0])
        view = self.api.organise_action("move", {"positions": [3, 4], "gap": 0})
        self.assertEqual(self._keys(view)[:2], ["doc:3", "doc:4"])
        self.assertEqual(view["selection"], [0, 1])
        view = self.api.organise_action("shift", {"positions": [0, 1], "step": 1})
        self.assertEqual(view["selection"], [1, 2])
        view = self.api.organise_action("delete", {"positions": [4]})
        self.assertEqual(len(view["pages"]), 4)
        self.assertEqual(view["steps"], 4)
        # The open document hasn't changed.
        state = self.api.get_state()
        self.assertEqual(state["page_count"], 5)
        self.assertFalse(state["is_dirty"])

    def test_the_drafts_own_undo_and_redo(self):
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0]})
        self.assertEqual(len(self.api.organise_undo()["pages"]), 5)
        self.assertFalse(self.api.organise_undo()["ok"])
        self.assertEqual(len(self.api.organise_redo()["pages"]), 4)

    def test_refused_actions_say_why_and_change_nothing(self):
        self.api.organise_begin()
        refused = self.api.organise_action("delete", {"positions": [0, 1, 2, 3, 4]})
        self.assertEqual((refused["ok"], refused["reason"]), (False, page_plan.EVERY_PAGE))
        # With the draft as it stays: the page has already shown the action
        # and goes back to this.
        self.assertEqual(self._keys(refused), [f"doc:{i}" for i in range(5)])
        self.assertEqual((refused["steps"], refused["can_undo"]), (0, False))
        for kind, args in (
            ("rotate", {"positions": [0], "degrees": 45}),
            ("rotate", {"positions": "0", "degrees": 90}),
            ("shift", {"positions": [0], "step": 2}),
            ("move", {"positions": [9], "gap": 0}),
            ("move", {"positions": [0], "gap": None}),
            ("explode", {"positions": [0]}),
            ("delete", None),
        ):
            with self.subTest(kind=kind, args=args):
                self.assertFalse(self.api.organise_action(kind, args)["ok"])
        self.assertEqual(self.api.organise_begin()["steps"], 0)

    def test_done_applies_the_draft_as_one_reader_undo_step(self):
        self.api.goto_page(3)
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0, 1]})
        self.api.organise_action("rotate", {"positions": [0], "degrees": -90})
        state = self.api.organise_done()
        self.assertTrue(state["pages_changed"])
        self.assertEqual(state["page_count"], 3)
        self.assertEqual(state["page_index"], 1)  # page 4 follows its page
        self.assertTrue(state["is_dirty"])
        self.assertEqual(state["undo_kind"], page_changes.PAGE_CHANGES)
        self.assertEqual(self.api._doc._doc[0].rotation, 270)

        undone = self.api.undo_highlight()
        self.assertTrue(undone["pages_changed"])
        self.assertEqual(undone["page_count"], 5)
        self.assertEqual(undone["page_index"], 3)
        self.assertFalse(undone["can_undo"])
        self.assertEqual(undone["redo_kind"], page_changes.PAGE_CHANGES)
        self.assertTrue(self.api.redo_highlight()["pages_changed"])

    def test_done_without_changes_just_leaves(self):
        self.api.organise_begin()
        self.api.organise_action("rotate", {"positions": [0], "degrees": 90})
        self.api.organise_action("rotate", {"positions": [0], "degrees": -90})
        state = self.api.organise_done()
        self.assertFalse(state["pages_changed"])
        self.assertFalse(state["can_undo"])
        self.assertEqual(self.api.organise_begin()["steps"], 0)  # a fresh draft

    def test_a_highlight_undo_is_not_a_page_change(self):
        self.api.highlight_chars(0, 0, 3)
        state = self.api.undo_highlight()
        self.assertFalse(state["pages_changed"])
        self.assertEqual(state["affected_page"], 0)

    def test_a_failed_apply_keeps_the_draft_and_says_so(self):
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0]})
        with mock.patch.object(self.api._doc, "apply_page_plan", side_effect=RuntimeError("boom")), \
                self.assertLogs(api_module.log, "ERROR"):
            state = self.api.organise_done()
        self.assertEqual(state["error"], api_module.PAGE_CHANGES_FAILED)
        self.assertEqual(state["page_count"], 5)
        self.assertEqual(self.api.organise_begin()["steps"], 1)

    def test_thumbnails_of_the_draft(self):
        self.api.organise_begin()
        thumb = self.api.organise_thumbnail(page_plan.DOC, 2, 92)
        self.assertEqual(thumb["width"], 92)
        for args in ((page_plan.DOC, 9, 92), ("insert-0", 0, 92), (page_plan.DOC, -1, 92), (3, 0, 92)):
            with self.subTest(args=args):
                self.assertIsNone(self.api.organise_thumbnail(*args)["image"])

    def test_turned_thumbnails_are_the_same_page_on_its_side(self):
        self.api.organise_begin()
        upright = self.api.organise_thumbnail(page_plan.DOC, 2, 92)
        for turn in (90, 270):
            with self.subTest(turn=turn):
                turned = self.api.organise_thumbnail(page_plan.DOC, 2, 92, turn)
                self.assertEqual((turned["width"], turned["height"]), (upright["height"], upright["width"]))
        half = self.api.organise_thumbnail(page_plan.DOC, 2, 92, 180)
        self.assertEqual((half["width"], half["height"]), (upright["width"], upright["height"]))
        self.assertEqual(self.api.organise_thumbnail(page_plan.DOC, 2, 92, 0)["image"], upright["image"])
        for turn in (45, -90, 360, 90.0, True, "90", None):
            with self.subTest(turn=turn):
                self.assertIsNone(self.api.organise_thumbnail(page_plan.DOC, 2, 92, turn)["image"])

    def test_a_turned_thumbnail_is_the_upright_one_rotated_clockwise(self):
        # So the grid can swap one for the other with nothing visibly changing.
        import base64
        import io

        from PIL import Image, ImageChops, ImageStat

        self.api.organise_begin()

        def image(turn):
            data = self.api.organise_thumbnail(page_plan.DOC, 0, 92, turn)["image"]
            return Image.open(io.BytesIO(base64.b64decode(data))).convert("L")

        upright = image(0)
        for turn, transpose in ((90, Image.Transpose.ROTATE_270), (270, Image.Transpose.ROTATE_90)):
            with self.subTest(turn=turn):
                diff = ImageChops.difference(upright.transpose(transpose), image(turn))
                self.assertLess(ImageStat.Stat(diff).mean[0], 4)  # a rasterising edge at most

    def test_refused_steps_carry_the_draft_as_it_stays(self):
        self.api.organise_begin()
        self.api.organise_action("move", {"positions": [0], "gap": 5})
        for refused in (
            self.api.organise_action("move", {"positions": [9], "gap": 0}),
            self.api.organise_action("rotate", None),
        ):
            self.assertFalse(refused["ok"])
            self.assertEqual(self._keys(refused), [f"doc:{i}" for i in (1, 2, 3, 4, 0)])
            self.assertEqual(refused["steps"], 1)
        self.api.organise_undo()
        nothing = self.api.organise_undo()
        self.assertEqual((nothing["ok"], nothing["steps"], nothing["can_redo"]), (False, 0, True))
        self.api.organise_redo()
        nothing = self.api.organise_redo()
        self.assertEqual((nothing["ok"], nothing["steps"], nothing["can_undo"]), (False, 1, True))

    def test_the_timing_probe_logs_numbers_only_and_only_in_a_debug_run(self):
        report = {"kind": "move", "pages": 1, "bridge": 0.4, "python": 9.1, "frame": 12, "text": "Page one",
                  "render": "x", "thumbs": True}
        with self.assertNoLogs("lector.api", level="DEBUG"), mock.patch.dict(os.environ, {"LECTOR_DEBUG": "0"}):
            self.assertFalse(self.api.organise_debug_timing(report)["ok"])
        with mock.patch.dict(os.environ, {"LECTOR_DEBUG": "1"}), self.assertLogs("lector.api", level="DEBUG") as logs:
            self.assertTrue(self.api.organise_debug_timing(report)["ok"])
            self.api.organise_debug_timing({**report, "kind": "<script>"})
            self.assertFalse(self.api.organise_debug_timing(["not", "a", "report"])["ok"])
        self.assertEqual(logs.records[0].getMessage(), "organise timing move pages=1 bridge=0.4 python=9.1 frame=12")
        self.assertEqual(logs.records[1].getMessage(), "organise timing ? pages=1 bridge=0.4 python=9.1 frame=12")
        with mock.patch.dict(os.environ, {"LECTOR_DEBUG": "1"}):
            self.assertTrue(self.api.organise_begin()["debug"])
        with mock.patch.dict(os.environ, {"LECTOR_DEBUG": ""}):
            self.assertFalse(self.api.organise_begin()["debug"])

    def test_reader_undo_does_nothing_while_organise_is_open(self):
        # Found in review: an undone page change under an open draft let Done
        # apply a 3-page draft to a 4-page document, losing page 4.
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0]})
        self.api.organise_done()
        self.api.organise_begin()
        self.api.organise_action("rotate", {"positions": [2], "degrees": 90})
        for step in (self.api.undo_highlight, self.api.redo_highlight):
            state = step()
            self.assertFalse(state["pages_changed"])
            self.assertIsNone(state["affected_page"])
        self.assertEqual(self.api.get_state()["page_count"], 4)
        self.api.organise_done()
        self.assertEqual(self.api.get_state()["page_count"], 4)
        self.assertEqual(self.api._doc._doc[2].rotation, 90)

    def test_a_page_change_forgets_the_voice_viewport(self):
        # Found in review: a viewport naming page 5 outlived a plan that left
        # 2 pages, and the next vocabulary rebuild asked for a missing page.
        self.api.start_listening([{"page_index": 4, "y0": 0, "y1": 800}])
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [2, 3, 4]})
        self.api.organise_done()
        self.assertEqual(self.api._voice_viewport, [])
        self.api.set_voice_context("reading")  # rebuilds the vocabulary
        self.api.start_listening([{"page_index": 1, "y0": 0, "y1": 800}])
        self.api.undo_highlight()
        self.assertEqual(self.api._voice_viewport, [])

    def test_only_whole_numbers_turn_and_shift(self):
        self.api.organise_begin()
        for kind, args in (
            ("rotate", {"positions": [0], "degrees": 90.0}),
            ("rotate", {"positions": [0], "degrees": True}),
            ("shift", {"positions": [1], "step": 1.0}),
        ):
            with self.subTest(kind=kind, args=args):
                self.assertFalse(self.api.organise_action(kind, args)["ok"])

    def test_cancel_drops_the_draft_and_changes_nothing(self):
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0, 1]})
        self.api.organise_action("rotate", {"positions": [0], "degrees": 90})
        with mock.patch.object(self.api._doc, "snapshot") as snapshot, \
                mock.patch.object(self.api._doc, "_capture_state") as capture:
            state = self.api.organise_cancel()
        snapshot.assert_not_called()
        capture.assert_not_called()
        self.assertFalse(state["pages_changed"])
        self.assertEqual(state["page_count"], 5)
        self.assertFalse(state["is_dirty"])
        self.assertFalse(state["can_undo"])
        self.assertEqual(self.api.organise_begin()["steps"], 0)  # a fresh draft next time
        self.assertIsNone(self.api.handle_window_closing())  # nothing left to ask about

    def test_closing_with_changes_in_the_draft_asks_the_page_first(self):
        # The document isn't dirty yet (nothing applied), but the draft is
        # unsaved work: the page applies it, then asks the usual question.
        window = api_module.webview.windows[0]
        self.api.organise_begin()
        self.assertIsNone(self.api.handle_window_closing())  # an untouched draft: close
        self.api._closing_confirmed = False
        self.api.organise_action("delete", {"positions": [0]})
        self.assertFalse(self.api.get_state()["is_dirty"])
        self.assertIs(self.api.handle_window_closing(), False)
        self.assertIn("promptSaveIfDirty", window.evaluate_js.call_args[0][0])

    def test_opening_another_pdf_drops_the_draft(self):
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0]})
        self.api.open_pdf(str(pdf_support.multi_page(self.dir, 2, name="other.pdf")))
        self.assertEqual(len(self.api.organise_begin()["pages"]), 2)


class OrganiseBlockedTests(_ApiCase):
    def test_a_restricted_pdf_says_why_organise_is_unavailable(self):
        doc = pymupdf.open()
        doc.new_page()
        doc.new_page()
        path = self.dir / "restricted.pdf"
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
                 permissions=pymupdf.PDF_PERM_PRINT)
        doc.close()
        state = self.api.open_pdf(str(path))
        message = page_changes.BLOCK_MESSAGES[page_changes.RESTRICTED]
        self.assertEqual(state["organise_blocked"], message)
        self.assertEqual(self.api.organise_begin(), {"ok": False, "message": message})

    def test_an_ordinary_pdf_is_not_blocked(self):
        state = self.api.open_pdf(str(pdf_support.multi_page(self.dir, 2)))
        self.assertIsNone(state["organise_blocked"])


class ScannedPageMoveTests(_ApiCase):
    """A scanned page that moves keeps its text without being read again,
    and voice highlighting on its new place matches its own words."""

    def setUp(self):
        super().setUp()
        sources = pymupdf.open()
        for text in (APPLES, ZEBRAS):
            _text_page(sources, text)
        doc = pymupdf.open()
        for text_page in sources:  # fresh Page objects: adding a page invalidates older ones
            pix = text_page.get_pixmap(dpi=150)
            page = doc.new_page(width=text_page.rect.width, height=text_page.rect.height)
            page.insert_image(page.rect, stream=pix.tobytes("png"))
        path = self.dir / "scans.pdf"
        doc.save(path)
        # What an earlier session's OCR left in the disk cache.
        for page, text_page in zip(doc, sources):
            ocr_cache.store(ocr.page_key(doc, page), _payload_for(text_page))
        doc.close()
        self.api.open_pdf(str(path))

    def test_a_moved_page_is_served_from_the_cache_and_highlights_its_own_words(self):
        self.assertEqual(self.api._doc.words_on_page(0)[0].text, "Apples")
        self.api.organise_begin()
        self.api.organise_action("move", {"positions": [1], "gap": 0})
        self.assertTrue(self.api.organise_done()["pages_changed"])

        self.assertEqual(self.api._doc.ocr_status(0), ocr.DONE)
        self.assertEqual(self.api._doc.words_on_page(0)[0].text, "Zebras")
        self.api._ocr.submit.assert_not_called()  # no OCR: the cache had it

        self.api.start_listening([{"page_index": 0, "y0": 0, "y1": 300}])
        hit = self.api.highlight_by_voice("zebras")["voice_highlight"]
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["affected_page"], 0)
        self.assertEqual(hit["text"], "Zebras")
        miss = self.api.highlight_by_voice("apples")["voice_highlight"]
        self.assertFalse(miss["matched"])  # the words of the page that used to be there
        self.api._ocr.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
