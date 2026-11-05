"""Tests for freehand marker strokes as PDF /Ink annotations.

The promises held here, from the PDF outwards:

* **Another viewer draws the same stroke.** A stroke is a real /Ink
  annotation with its colour in /C, its 14pt width in /BS, 60% in /CA and
  Multiply in /BM, and an appearance stream that uses them (round caps and
  joins), checked on a saved copy reopened from disk, down to the pixel:
  where the stroke crosses itself it is no darker than where it does not.
* **It lands where it was drawn,** on a page turned by /Rotate too: points
  arrive on the page as shown and are written on the unrotated page.
* **It is a highlight to everything else:** counted, on the progress bar's
  pages, listed for the editor, recoloured, removed and undone, and an
  overwrite save keeps its undo history.
* **Undo after straightening gives back the stroke as drawn;** a second
  Undo removes it.
* **The bridge refuses a malformed stroke** without drawing anything.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds and reads back the throwaway PDFs

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.settings import store  # noqa: E402

YELLOW = highlighter.HIGHLIGHT_COLORS["yellow"]
UNDERLINE = [(80.0, 200.0), (160.0, 203.0), (240.0, 198.0), (320.0, 201.0)]
# Runs out along y=300 to x=300, then back over 200..300 of it.
CROSSING = [(100.0, 300.0), (300.0, 300.0), (300.0, 300.5), (200.0, 300.5)]


def _multiplied(rgb, alpha=highlighter.INK_OPACITY):
    """`rgb` (0..1) at `alpha` through Multiply over white paper, 0..255."""
    return tuple(round(255 * (1 - alpha * (1 - c))) for c in rgb)


def _close(a, b, tolerance=3):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _rounded(strokes):
    return [[(round(x, 3), round(y, 3)) for x, y in stroke] for stroke in strokes]


class _DocTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-ink-")
        self.addCleanup(self.dir.cleanup)
        self.doc = PdfDocument()
        self.doc.open(str(pdf_support.plain_text(Path(self.dir.name))))
        self.addCleanup(self.doc.close)

    def _saved_copy(self):
        path = str(Path(self.dir.name) / "copy.pdf")
        self.doc.save_copy(path)
        reopened = pymupdf.open(path)
        self.addCleanup(reopened.close)
        return reopened

    def _strokes(self):
        """The one ink highlight's strokes on the page as shown."""
        (found,) = [h for h in self.doc.highlights_on_page(0) if isinstance(h.shape, highlighter.InkShape)]
        return _rounded(self.doc.shown_strokes(0, found.shape))


class SavedInkTests(_DocTest):
    def test_a_stroke_is_written_as_ink_with_width_opacity_and_multiply(self):
        self.doc.add_ink_stroke(0, UNDERLINE, color="yellow")
        page = self._saved_copy()[0]
        (annot,) = list(page.annots())
        self.assertEqual(annot.type[0], pymupdf.PDF_ANNOT_INK)
        self.assertEqual(highlighter.color_name(highlighter.stroke_rgb(annot)), "yellow")
        self.assertEqual(annot.border["width"], highlighter.INK_WIDTH_PT)
        self.assertAlmostEqual(annot.opacity, highlighter.INK_OPACITY, places=3)
        self.assertEqual(annot.blendmode, "Multiply")
        appearance = annot._getAP()
        self.assertIn(b"1 J", appearance)  # round caps
        self.assertIn(b"1 j", appearance)  # round joins
        self.assertIn(b" gs", appearance)  # the ExtGState carrying /CA and /BM
        del annot, page

    def test_another_reader_of_the_file_finds_the_stroke(self):
        self.doc.add_ink_stroke(0, UNDERLINE, color="green")
        self._saved_copy()
        (found,) = pdf_support.ink_on_disk(Path(self.dir.name) / "copy.pdf")[0]
        green = tuple(round(c, 3) for c in highlighter.HIGHLIGHT_COLORS["green"])
        self.assertEqual(found, (green, highlighter.INK_WIDTH_PT, highlighter.INK_OPACITY, "Multiply"))

    def test_the_saved_stroke_renders_multiplied_and_does_not_double_where_it_crosses(self):
        self.doc.add_ink_stroke(0, CROSSING, color="yellow")
        pix = self._saved_copy()[0].get_pixmap(alpha=False)
        once = pix.pixel(150, 300)  # covered by one pass
        twice = pix.pixel(250, 300)  # covered going out and coming back
        self.assertTrue(_close(once, _multiplied(YELLOW)), (once, _multiplied(YELLOW)))
        self.assertEqual(once, twice)

    def test_every_colour_is_the_text_highlights_own_applied_once(self):
        # The same five values as a text highlight, never a lightened tint,
        # with the opacity applied once: on white, each stroke is exactly its
        # palette colour at INK_OPACITY through Multiply.
        for i, name in enumerate(highlighter.HIGHLIGHT_COLORS):
            self.doc.add_ink_stroke(0, [(60.0, 300.0 + i * 40), (300.0, 300.0 + i * 40)], color=name)
        pix = self._saved_copy()[0].get_pixmap(alpha=False)
        for i, (name, rgb) in enumerate(highlighter.HIGHLIGHT_COLORS.items()):
            with self.subTest(name):
                self.assertTrue(_close(pix.pixel(180, 300 + i * 40), _multiplied(rgb)), pix.pixel(180, 300 + i * 40))

    def test_the_stroke_is_strong_enough_to_match_a_text_highlight(self):
        # docs/DESIGN_SYSTEM.md: 55-60% and 14pt, a stroke beside a text
        # highlight of the same colour reading as that colour.
        self.assertGreaterEqual(highlighter.INK_OPACITY, 0.55)
        self.assertLessEqual(highlighter.INK_OPACITY, 0.6)
        self.assertEqual(highlighter.INK_WIDTH_PT, 14.0)

    def test_text_under_a_stroke_stays_dark(self):
        page = self.doc._doc[0]
        word = pymupdf.Rect(page.get_text("words")[0][:4])
        del page
        middle = (word.y0 + word.y1) / 2
        self.doc.add_ink_stroke(0, [(word.x0 - 10, middle), (word.x1 + 10, middle)], color="blue")
        pix = self._saved_copy()[0].get_pixmap(alpha=False, dpi=144, clip=word)
        darkest = min(sum(pix.pixel(x, y)) for x in range(pix.width) for y in range(pix.height))
        self.assertLess(darkest, 3 * 80)

    def test_a_recoloured_stroke_keeps_its_width_opacity_and_blend(self):
        xref = self.doc.add_ink_stroke(0, UNDERLINE, color="yellow")
        self.assertTrue(self.doc.set_highlight_color(0, xref, "pink"))
        page = self._saved_copy()[0]
        (annot,) = list(page.annots())
        self.assertEqual(highlighter.color_name(highlighter.stroke_rgb(annot)), "pink")
        self.assertEqual(annot.border["width"], highlighter.INK_WIDTH_PT)
        self.assertAlmostEqual(annot.opacity, highlighter.INK_OPACITY, places=3)
        self.assertEqual(annot.blendmode, "Multiply")
        del annot, page


class RotationTests(unittest.TestCase):
    def test_a_stroke_lands_where_it_was_drawn_on_a_turned_page(self):
        drawn = [(40.0, 60.0), (90.0, 60.0)]
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation), tempfile.TemporaryDirectory() as folder:
                doc = PdfDocument()
                doc.open(str(pdf_support.rotated(Path(folder), rotation)))
                try:
                    doc.add_ink_stroke(0, drawn, color="green")
                    (found,) = doc.highlights_on_page(0)
                    self.assertEqual(_rounded(doc.shown_strokes(0, found.shape)), [drawn])
                    # And it is drawn there: half way along is tinted.
                    page = doc._doc[0]
                    pix = page.get_pixmap(alpha=False)
                    del page
                    self.assertNotEqual(pix.pixel(65, 60), (255, 255, 255))
                finally:
                    doc.close()


class UndoTests(_DocTest):
    def test_undo_after_straightening_gives_back_the_drawn_stroke_then_removes_it(self):
        straight = [(80.0, 200.0), (320.0, 200.0)]
        self.doc.add_ink_stroke(0, UNDERLINE, straight, color="yellow")
        self.assertEqual(self._strokes(), [straight])
        self.doc.undo_highlight()
        self.assertEqual(self._strokes(), [UNDERLINE])
        self.doc.undo_highlight()
        self.assertEqual(self.doc.highlights_on_page(0), [])
        self.assertFalse(self.doc.can_undo)
        self.doc.redo_highlight()
        self.assertEqual(self._strokes(), [UNDERLINE])
        self.doc.redo_highlight()
        self.assertEqual(self._strokes(), [straight])

    def test_remove_and_recolour_are_undone(self):
        xref = self.doc.add_ink_stroke(0, UNDERLINE, color="yellow")
        self.doc.set_highlight_color(0, xref, "orange")
        (found,) = self.doc.highlights_on_page(0)
        self.assertTrue(self.doc.remove_highlight(0, found.xref))
        self.assertEqual(self.doc.highlights_on_page(0), [])
        self.doc.undo_highlight()
        (back,) = self.doc.highlights_on_page(0)
        self.assertEqual(back.color, "orange")
        self.assertEqual(self._strokes(), [UNDERLINE])
        self.doc.undo_highlight()
        self.assertEqual(self.doc.highlights_on_page(0)[0].color, "yellow")

    def test_another_apps_ink_comes_back_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = PdfDocument()
            doc.open(str(pdf_support.already_drawn(Path(folder))))
            try:
                (before,) = doc.highlights_on_page(0)
                self.assertIsNone(before.color)
                self.assertEqual(before.shape.width, pdf_support.FOREIGN_INK_WIDTH)
                self.assertTrue(doc.remove_highlight(0, before.xref))
                doc.undo_highlight()
                (after,) = doc.highlights_on_page(0)
                self.assertEqual(after.shape, before.shape)
                self.assertIsNone(after.color)
                page = doc._doc[0]
                (annot,) = list(page.annots())
                self.assertEqual(annot.info["content"], "a note")
                del annot, page
            finally:
                doc.close()

    def test_an_overwrite_save_keeps_undo_history(self):
        self.doc.add_ink_stroke(0, UNDERLINE, color="yellow")
        self.doc.save_overwrite()
        self.assertTrue(self.doc.can_undo)
        self.doc.undo_highlight()
        self.assertEqual(self.doc.highlights_on_page(0), [])


class CountTests(_DocTest):
    def test_ink_counts_as_a_highlight_and_marks_its_page(self):
        self.doc.add_ink_stroke(0, UNDERLINE, color="yellow")
        self.doc.highlight_char_indices(0, 4, 8)
        self.assertEqual(self.doc.highlight_counts(), {0: 2})
        self.assertEqual(self.doc.highlight_count_on_current_page(), 2)

    def test_the_saved_file_counts_ink_too(self):
        self.doc.add_ink_stroke(0, UNDERLINE, color="yellow")
        page = self._saved_copy()[0]
        self.assertEqual(highlighter.highlight_count(page), 1)
        del page

    def test_points_off_the_page_are_kept_on_it(self):
        self.doc.add_ink_stroke(0, [(-50.0, 200.0), (5000.0, 200.0)], color="yellow")
        width, _ = self.doc.page_size_points(0)
        (stroke,) = self._strokes()
        self.assertEqual([x for x, _ in stroke], [0, width])


class HostileInkTests(unittest.TestCase):
    """Another app's ink comes from a PDF Lector did not write: whatever is in
    it, the page's highlights still list, and what is listed (and written
    again by an undone removal) is a drawable stroke on the page."""

    def _open(self, folder, build):
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 40), "words to highlight", fontsize=12)
        build(doc, page)
        path = Path(folder) / "hostile.pdf"
        del page
        doc.save(path)
        doc.close()
        lector = PdfDocument()
        lector.open(str(path))
        return lector

    def test_an_ink_annotation_with_vertices_does_not_break_the_page(self):
        def build(doc, page):
            ink = page.add_ink_annot([[(10, 100), (100, 110)]])
            ink.update()
            doc.xref_set_key(ink.xref, "Vertices", "[10 200 150 210 200 260]")
            del ink

        with tempfile.TemporaryDirectory() as folder:
            doc = self._open(folder, build)
            try:
                doc.highlight_char_indices(0, 0, 4)
                kinds = sorted(type(h.shape).__name__ for h in doc.highlights_on_page(0))
                self.assertEqual(kinds, ["InkShape", "TextShape"])
            finally:
                doc.close()

    def test_a_hostile_width_and_points_are_bounded(self):
        def build(doc, page):
            ink = page.add_ink_annot([[(10, 100), (100, 110)]])
            ink.update()
            doc.xref_set_key(ink.xref, "BS", "<</W 1e30>>")
            doc.xref_set_key(ink.xref, "InkList", "[[10 100 -5000 90000 100 110]]")
            del ink

        with tempfile.TemporaryDirectory() as folder:
            doc = self._open(folder, build)
            try:
                (found,) = doc.highlights_on_page(0)
                self.assertLessEqual(found.shape.width, highlighter.INK_MAX_WIDTH_PT)
                for x, y in found.shape.strokes[0]:
                    self.assertTrue(0 <= x <= 300 and 0 <= y <= 300, (x, y))
                # And it comes back drawable after a removal is undone.
                self.assertTrue(doc.remove_highlight(0, found.xref))
                doc.undo_highlight()
                (back,) = doc.highlights_on_page(0)
                self.assertEqual(back.shape.strokes, found.shape.strokes)
            finally:
                doc.close()

    def _strokes_on_disk(self, doc, folder):
        """The page's ink strokes in a saved copy, read by a fresh PyMuPDF."""
        path = Path(folder) / "saved.pdf"
        doc.save_copy(str(path))
        saved = pymupdf.open(path)
        try:
            return [[[tuple(round(c, 3) for c in p) for p in s] for s in a.vertices] for a in saved[0].annots()]
        finally:
            saved.close()

    def test_an_undone_removal_writes_back_every_stroke_and_point(self):
        # A long handwritten note: more strokes, and more points, than the
        # editor lists, and one stroke running off the page.
        strokes = [[(10 + (i % 25) * 11, 60 + (i // 25) * 50), (15 + (i % 25) * 11, 80 + (i // 25) * 50)]
                   for i in range(highlighter.INK_MAX_STROKES + 36)]
        strokes.append([(10 + i * 0.05, 280) for i in range(highlighter.INK_MAX_STROKE_POINTS + 10)])
        strokes.append([(250, 150), (400, 150)])

        def build(doc, page):
            ink = page.add_ink_annot(strokes)
            ink.set_colors(stroke=pdf_support.FOREIGN_RGB)
            ink.update()
            del ink

        with tempfile.TemporaryDirectory() as folder:
            doc = self._open(folder, build)
            try:
                before = self._strokes_on_disk(doc, folder)
                self.assertEqual(len(before[0]), len(strokes))
                (listed,) = doc.highlights_on_page(0)
                self.assertEqual(len(listed.shape.strokes), highlighter.INK_MAX_STROKES, "the list is bounded")
                self.assertTrue(doc.remove_highlight(0, listed.xref))
                doc.undo_highlight()
                self.assertEqual(self._strokes_on_disk(doc, folder), before)
            finally:
                doc.close()

    def test_an_undone_recolour_keeps_every_stroke(self):
        strokes = [[(10 + i * 3, 100), (12 + i * 3, 140)] for i in range(highlighter.INK_MAX_STROKES + 6)]

        def build(doc, page):
            ink = page.add_ink_annot(strokes)
            ink.update()
            del ink

        with tempfile.TemporaryDirectory() as folder:
            doc = self._open(folder, build)
            try:
                before = self._strokes_on_disk(doc, folder)
                (listed,) = doc.highlights_on_page(0)
                self.assertTrue(doc.set_highlight_color(0, listed.xref, "green"))
                (green,) = doc.highlights_on_page(0)
                self.assertTrue(doc.remove_highlight(0, green.xref))
                doc.undo_highlight()  # the removal, then the recolour
                doc.undo_highlight()
                self.assertEqual(self._strokes_on_disk(doc, folder), before)
            finally:
                doc.close()

    def test_ink_with_nothing_drawable_is_neither_listed_nor_counted(self):
        def build(doc, page):
            ink = page.add_ink_annot([[(10, 100), (100, 110)]])
            ink.update()
            doc.xref_set_key(ink.xref, "InkList", "[[] []]")  # PyMuPDF reads a name as 0
            del ink

        with tempfile.TemporaryDirectory() as folder:
            doc = self._open(folder, build)
            try:
                doc.highlight_char_indices(0, 0, 4)
                self.assertEqual(len(doc.highlights_on_page(0)), 1)
                self.assertEqual(doc.highlight_counts(), {0: 1})
                self.assertEqual(doc.highlight_count_on_current_page(), 1)
            finally:
                doc.close()

    def test_the_count_agrees_with_the_list_past_the_listed_strokes(self):
        # Every stroke the list reads empty, a drawable one after them: not
        # listed, so not counted either (though an undone removal would keep it).
        garbage = " ".join("[]" for _ in range(highlighter.INK_MAX_STROKES))

        def build(doc, page):
            ink = page.add_ink_annot([[(10, 100), (100, 110)]])
            ink.update()
            doc.xref_set_key(ink.xref, "InkList", f"[{garbage} [10 100 100 110]]")
            del ink

        with tempfile.TemporaryDirectory() as folder:
            doc = self._open(folder, build)
            try:
                self.assertEqual(doc.highlights_on_page(0), [])
                self.assertEqual(doc.highlight_counts().get(0, 0), 0)
            finally:
                doc.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-ink-api-")
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(store, "_settings_path", lambda: Path(self.dir.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api.open_pdf(str(pdf_support.plain_text(Path(self.dir.name))))
        self.addCleanup(self.api._doc.close)

    def test_a_stroke_uses_the_active_colour_and_reports_its_page(self):
        self.api.set_highlight_color("green")
        state = self.api.add_ink_stroke(0, [[80, 200], [160, 203], [240, 198]])
        self.assertEqual(state["affected_page"], 0)
        self.assertIsInstance(state["id"], int)
        self.assertTrue(state["is_dirty"])
        self.assertTrue(state["can_undo"])
        self.assertEqual(state["highlighted_pages"], [0])
        self.assertEqual(state["highlight_count_total"], 1)
        (found,) = self.api.get_page_highlights(0)["highlights"]
        self.assertEqual(set(found), {"id", "kind", "style", "opacity", "color", "rects", "strokes", "width", "text"})
        self.assertEqual(found["kind"], "ink")
        self.assertEqual(found["color"], "green")
        self.assertIsNone(found["text"])
        self.assertEqual(found["width"], highlighter.INK_WIDTH_PT)
        self.assertEqual([[round(c) for c in p] for p in found["strokes"][0]], [[80, 200], [160, 203], [240, 198]])
        (box,) = found["rects"]  # the box the stroke paints: points out by half its width
        self.assertEqual([round(box[k]) for k in ("x0", "y0", "x1", "y1")], [73, 191, 247, 210])

    def test_a_straightened_stroke_is_two_undo_steps(self):
        self.api.add_ink_stroke(0, [[80, 200], [160, 203], [240, 198]], [[80, 200], [240, 200]])
        self.assertEqual(self.api.undo_highlight()["highlight_count_total"], 1)
        self.assertEqual(self.api.undo_highlight()["highlight_count_total"], 0)

    def test_a_bad_page_lists_no_highlights(self):
        for page in (-1, 7, "0", True, 10 ** 400):
            with self.subTest(page=page):
                self.assertEqual(self.api.get_page_highlights(page)["highlights"], [])

    def test_the_ink_style_is_what_the_pdf_gets(self):
        # The preview reads width and opacity from the options the popover
        # starts from; the PDF's defaults are the same numbers.
        options = self.api.get_highlight_options()
        self.assertEqual(options["ink_width"], highlighter.INK_WIDTH_PT)
        self.assertEqual(options["opacity"]["highlight"] / 100, highlighter.INK_OPACITY)

    def test_a_malformed_stroke_draws_nothing(self):
        bad = {
            "no points": (0, [], None),
            "not a list": (0, "80,200", None),
            "a point is not a pair": (0, [[80, 200, 1]], None),
            "not a number": (0, [["80", 200]], None),
            "a bool": (0, [[True, 200]], None),
            "not finite": (0, [[float("nan"), 200]], None),
            "infinite": (0, [[float("inf"), 200]], None),
            "too many points": (0, [[1, 1]] * (api_module.MAX_INK_POINTS + 1), None),
            "no such page": (5, [[80, 200]], None),
            "negative page": (-1, [[80, 200]], None),
            "page not a number": ("0", [[80, 200]], None),
            "fractional page": (0.5, [[80, 200]], None),
            "an integer too large for a float": (0, [[10 ** 400, 200]], None),
            "a page too large for a float": (10 ** 400, [[80, 200]], None),
            "a straightened line needs both ends": (0, [[80, 200], [90, 200]], [[80, 200]]),
            "a straightened line is not a list": (0, [[80, 200], [90, 200]], "line"),
        }
        for name, (page, points, straightened) in bad.items():
            with self.subTest(name):
                state = self.api.add_ink_stroke(page, points, straightened)
                self.assertIsNone(state["affected_page"])
                self.assertIsNone(state["id"])
        self.assertEqual(self.api._state()["highlight_count_total"], 0)
        self.assertFalse(self.api._state()["can_undo"])


if __name__ == "__main__":
    unittest.main()
