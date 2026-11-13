"""Highlight options: style, opacity and freehand thickness.

What is held here, from the PDF outwards:

* **Each style is the PDF annotation another viewer expects:** Highlight a
  /Highlight, Underline an /Underline, Strikethrough a /StrikeOut, each with
  the palette colour in /C and the reader's opacity in /CA, kept on a saved
  copy reopened from disk.
* **A line sits where the text says,** not where the selection box is: an
  underline just under the baseline, a strike-through about half the
  x-height above it, as thick as the font size makes it — on a text layer
  and on what OCR read off a scanned page.
* **Opacity is drawn,** not only stored: a highlight at the default 60% is
  the colour at 60% multiplied onto the paper; an underline at 100% is solid.
* **Freehand strokes take the reader's width and the Highlight opacity.**
* **Changing a highlight's opacity or a stroke's width** happens in place
  (the same annotation), as one undo step per gesture, and undoes exactly.
* **Underlines and strike-throughs are highlights** everywhere they are
  counted: the footer, the progress bar's pages, undo and redo.
* **The settings and the bridge** remember each option and refuse anything
  the popover does not offer.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds and reads back throwaway PDFs

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading import ocr, ocr_cache  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.shared import highlight_options  # noqa: E402

HAS_TESSDATA = (ocr.TESSDATA_DIR / f"{ocr.OCR_LANGUAGE}.traineddata").is_file()

# pdf_support's line: 12pt Helvetica, baseline at y = 100.
BASELINE = 100.0
FONT_SIZE = 12.0
UNDERLINE_CENTRE = BASELINE + highlighter.UNDERLINE_OFFSET_EM * FONT_SIZE
STRIKE_CENTRE = BASELINE - highlighter.STRIKE_OFFSET_EM * FONT_SIZE
LINE_THICKNESS = highlighter.LINE_THICKNESS_EM * FONT_SIZE

# Renders are this many pixels per PDF point.
ZOOM = 10


def _expected_fill(rgb, alpha):
    """The colour `rgb` at `alpha`, multiplied onto white paper, as 0..255."""
    return tuple(round(255 * (1 - alpha * (1 - c))) for c in rgb)


def _colour_rows(page, clip, rgb, tolerance=12, share=0.3):
    """The rows (in PDF points from the page top) of a render of `clip` in
    which at least `share` of the pixels are `rgb` (0..1 floats, solid): the
    bar a line annotation draws, whatever glyphs it crosses."""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=clip)
    want = tuple(round(255 * c) for c in rgb)
    rows = []
    for y in range(pix.height):
        hits = sum(
            1 for x in range(pix.width)
            if all(abs(a - b) <= tolerance for a, b in zip(pix.pixel(x, y), want))
        )
        if hits >= share * pix.width:
            rows.append(clip.y0 + (y + 0.5) / ZOOM)
    return rows


class _DocTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-styles-")
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

    def _all_words(self, style, color="blue", opacity=None):
        words = self.doc.words_on_page(0)
        return self.doc.highlight_word_indices(0, 0, len(words) - 1, color, style=style, opacity=opacity)

    def _line_box(self):
        words = self.doc.words_on_page(0)
        return highlighter.merge_line_rects(words)[0]


class StyleWritingTests(_DocTest):
    def test_each_style_is_its_own_annotation_with_colour_and_opacity(self):
        expected = {
            highlight_options.HIGHLIGHT: (pymupdf.PDF_ANNOT_HIGHLIGHT, 0.6),
            highlight_options.UNDERLINE: (pymupdf.PDF_ANNOT_UNDERLINE, 1.0),
            highlight_options.STRIKETHROUGH: (pymupdf.PDF_ANNOT_STRIKE_OUT, 1.0),
        }
        for style in highlight_options.STYLES:
            self._all_words(style, "green")
        page = self._saved_copy()[0]
        annots = list(page.annots())
        self.assertEqual(len(annots), 3)
        for annot, style in zip(annots, highlight_options.STYLES):
            subtype, opacity = expected[style]
            with self.subTest(style=style):
                self.assertEqual(annot.type[0], subtype)
                self.assertEqual(highlighter.color_name(highlighter.stroke_rgb(annot)), "green")
                # PyMuPDF reads an absent /CA as -1, which a viewer draws as 1.
                self.assertAlmostEqual(annot.opacity if annot.opacity >= 0 else 1.0, opacity, places=3)

    def test_an_opacity_is_written_into_the_annotation(self):
        self._all_words(highlight_options.UNDERLINE, opacity=0.35)
        page = self._saved_copy()[0]  # held while its Annot is used
        (annot,) = page.annots()
        self.assertAlmostEqual(annot.opacity, 0.35, places=3)

    def test_a_highlight_at_the_default_is_the_colour_at_sixty_percent(self):
        self._all_words(highlight_options.HIGHLIGHT, "blue")
        page = self._saved_copy()[0]
        box = self._line_box()
        pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=box)
        counts = {}
        for y in range(pix.height):
            for x in range(pix.width):
                counts[pix.pixel(x, y)] = counts.get(pix.pixel(x, y), 0) + 1
        most_common = max(counts, key=counts.get)
        want = _expected_fill(highlighter.HIGHLIGHT_COLORS["blue"], 0.6)
        for got, w in zip(most_common, want):
            self.assertLessEqual(abs(got - w), 3, (most_common, want))

    def test_an_underline_sits_just_under_the_baseline(self):
        self._all_words(highlight_options.UNDERLINE, "blue")
        page = self._saved_copy()[0]
        box = self._line_box()
        rows = _colour_rows(page, box, highlighter.HIGHLIGHT_COLORS["blue"])
        self.assertTrue(rows, "no bar drawn")
        centre = (rows[0] + rows[-1]) / 2
        self.assertAlmostEqual(centre, UNDERLINE_CENTRE, delta=0.3)
        self.assertAlmostEqual(rows[-1] - rows[0] + 1 / ZOOM, LINE_THICKNESS, delta=0.25)
        self.assertGreater(rows[0], BASELINE, "an underline does not cross the letters")

    def test_a_strikethrough_crosses_the_lowercase_letters(self):
        self._all_words(highlight_options.STRIKETHROUGH, "blue")
        page = self._saved_copy()[0]
        rows = _colour_rows(page, self._line_box(), highlighter.HIGHLIGHT_COLORS["blue"], share=0.2)
        self.assertTrue(rows, "no bar drawn")
        centre = (rows[0] + rows[-1]) / 2
        self.assertAlmostEqual(centre, STRIKE_CENTRE, delta=0.3)
        # Helvetica's x-height is 0.52em: the line is through it, not over it.
        self.assertTrue(BASELINE - 0.52 * FONT_SIZE < centre < BASELINE)

    def test_a_line_is_drawn_without_multiply(self):
        self._all_words(highlight_options.UNDERLINE)
        self._all_words(highlight_options.HIGHLIGHT)
        underline, highlight = self._saved_copy()[0].annots()
        self.assertIsNone(underline.blendmode)
        self.assertEqual(highlight.blendmode, "Multiply")

    def test_a_character_drag_is_underlined_on_its_line_too(self):
        # Mouse selection is per character; the line is placed the same way.
        self.doc.highlight_char_indices(0, 4, 8, "blue", style=highlight_options.UNDERLINE)
        page = self._saved_copy()[0]
        (annot,) = page.annots()
        (box,) = highlighter.highlight_rects(annot)
        rows = _colour_rows(page, box, highlighter.HIGHLIGHT_COLORS["blue"])
        self.assertAlmostEqual((rows[0] + rows[-1]) / 2, UNDERLINE_CENTRE, delta=0.3)

    def test_an_unknown_style_or_opacity_falls_back_to_the_defaults(self):
        self._all_words("squiggly", opacity=7)
        page = self._saved_copy()[0]  # held while its Annot is used
        (annot,) = page.annots()
        self.assertEqual(annot.type[0], pymupdf.PDF_ANNOT_HIGHLIGHT)
        self.assertAlmostEqual(annot.opacity, 0.6, places=3)


class LineMetricsTests(unittest.TestCase):
    def test_a_text_layer_uses_its_own_baseline_and_size(self):
        chars = [
            highlighter.Char(pymupdf.Rect(10 + 6 * i, 90, 16 + 6 * i, 105), "a", 0, 0, i, 100.0, 12.0)
            for i in range(5)
        ]
        metrics = highlighter.line_metrics(pymupdf.Rect(10, 90, 40, 105), chars)
        self.assertEqual(metrics, highlighter.LineMetrics(100.0, 12.0))

    def test_ocr_boxes_are_read_from_their_shape(self):
        # An OCR box runs from the cap height to the bottom of the descenders.
        chars = [highlighter.Char(pymupdf.Rect(10 + 6 * i, 88.6, 16 + 6 * i, 104), "a", 0, 0, i) for i in range(5)]
        metrics = highlighter.line_metrics(pymupdf.Rect(10, 88.6, 40, 104), chars)
        self.assertAlmostEqual(metrics.baseline, 104 - 0.25 * 15.4, places=3)
        self.assertAlmostEqual(metrics.size, 15.4 / 0.95, places=3)

    def test_no_text_under_it_places_the_line_from_the_box(self):
        metrics = highlighter.line_metrics(pymupdf.Rect(10, 80, 40, 100), [])
        self.assertAlmostEqual(metrics.baseline, 100 - 0.22 * 20, places=3)

    def test_the_bar_stays_inside_the_box(self):
        bar = highlighter.markup_bar(
            pymupdf.Rect(0, 0, 10, 2), highlighter.LineMetrics(5.0, 40.0), highlight_options.UNDERLINE
        )
        self.assertGreaterEqual(bar.y0, 0)
        self.assertLessEqual(bar.y1, 2)


@unittest.skipUnless(HAS_TESSDATA, "needs assets/tessdata (python scripts/fetch_tessdata.py)")
class OcrStyleTests(unittest.TestCase):
    """The same lines on a page that is a picture of text, read by OCR (the
    real recognizer), as on the scanned College ID."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-styles-ocr-")
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(ocr_cache, "cache_dir", lambda: Path(self.dir.name) / "cache")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.doc = PdfDocument()
        self.doc.open(str(pdf_support.image_only(Path(self.dir.name))))
        self.addCleanup(self.doc.close)
        job = self.doc.begin_ocr(0)
        payload = ocr.recognize(job.page_pdf, str(ocr.TESSDATA_DIR))
        self.assertTrue(self.doc.finish_ocr(job, ocr.DONE, payload))
        self.assertTrue(self.doc.words_on_page(0), "OCR read nothing")

    def _bar_centre(self, style):
        words = self.doc.words_on_page(0)
        self.doc.highlight_word_indices(0, 0, len(words) - 1, "blue", style=style)
        page = self.doc._doc[0]
        annot = list(page.annots())[-1]
        (box,) = highlighter.highlight_rects(annot)
        rows = _colour_rows(page, box, highlighter.HIGHLIGHT_COLORS["blue"], tolerance=40, share=0.2)
        self.assertTrue(rows, "no bar drawn")
        return (rows[0] + rows[-1]) / 2

    def test_an_underline_on_ocr_text_is_under_its_baseline(self):
        self.assertAlmostEqual(self._bar_centre(highlight_options.UNDERLINE), UNDERLINE_CENTRE, delta=1.0)

    def test_a_strikethrough_on_ocr_text_crosses_its_letters(self):
        self.assertAlmostEqual(self._bar_centre(highlight_options.STRIKETHROUGH), STRIKE_CENTRE, delta=1.0)


class UndoTests(_DocTest):
    def test_an_undone_removal_brings_an_underline_back_as_one(self):
        xref = self._all_words(highlight_options.UNDERLINE)
        before = self.doc._doc[0].load_annot(xref)._getAP()
        self.assertTrue(self.doc.remove_highlight(0, xref))
        self.doc.undo_highlight()
        (found,) = self.doc.highlights_on_page(0)
        self.assertEqual(found.shape.style, highlight_options.UNDERLINE)
        self.assertEqual(self.doc._doc[0].load_annot(found.xref)._getAP(), before)
        self.doc.redo_highlight()
        self.assertEqual(self.doc.highlights_on_page(0), [])

    def test_a_recoloured_strikethrough_stays_one_at_its_opacity(self):
        xref = self._all_words(highlight_options.STRIKETHROUGH, opacity=0.5)
        self.assertTrue(self.doc.set_highlight_color(0, xref, "pink"))
        (found,) = self.doc.highlights_on_page(0)
        self.assertEqual((found.color, found.shape.style), ("pink", highlight_options.STRIKETHROUGH))
        self.assertAlmostEqual(found.shape.opacity, 0.5, places=3)

    def test_underlines_and_strikethroughs_are_counted(self):
        self._all_words(highlight_options.UNDERLINE)
        self._all_words(highlight_options.STRIKETHROUGH)
        self.assertEqual(self.doc.highlight_counts(), {0: 2})


class AppearanceEditTests(_DocTest):
    def test_opacity_changes_in_place_and_undoes(self):
        xref = self._all_words(highlight_options.HIGHLIGHT)
        self.assertTrue(self.doc.set_highlight_appearance(0, xref, opacity=0.3))
        (found,) = self.doc.highlights_on_page(0)
        self.assertEqual(found.xref, xref, "restyled in place, not re-added")
        self.assertAlmostEqual(found.shape.opacity, 0.3, places=3)
        self.doc.undo_highlight()
        (found,) = self.doc.highlights_on_page(0)
        self.assertAlmostEqual(found.shape.opacity, 0.6, places=3)
        self.doc.redo_highlight()
        (found,) = self.doc.highlights_on_page(0)
        self.assertAlmostEqual(found.shape.opacity, 0.3, places=3)

    def test_one_gesture_is_one_undo_step(self):
        xref = self._all_words(highlight_options.HIGHLIGHT)
        for value in (0.55, 0.5, 0.42, 0.4):
            self.assertTrue(self.doc.set_highlight_appearance(0, xref, opacity=value, gesture="g1"))
        self.doc.undo_highlight()
        (found,) = self.doc.highlights_on_page(0)
        self.assertAlmostEqual(found.shape.opacity, 0.6, places=3)
        self.doc.undo_highlight()
        self.assertEqual(self.doc.highlights_on_page(0), [], "the next undo takes back the add")

    def test_a_second_gesture_is_a_second_step(self):
        xref = self._all_words(highlight_options.HIGHLIGHT)
        self.doc.set_highlight_appearance(0, xref, opacity=0.4, gesture="g1")
        self.doc.set_highlight_appearance(0, xref, opacity=0.8, gesture="g2")
        self.doc.undo_highlight()
        (found,) = self.doc.highlights_on_page(0)
        self.assertAlmostEqual(found.shape.opacity, 0.4, places=3)

    def test_a_gesture_back_where_it_began_leaves_no_step(self):
        xref = self._all_words(highlight_options.HIGHLIGHT)
        self.doc.set_highlight_appearance(0, xref, opacity=0.4, gesture="g1")
        self.doc.set_highlight_appearance(0, xref, opacity=0.6, gesture="g1")
        self.doc.undo_highlight()
        self.assertEqual(self.doc.highlights_on_page(0), [])

    def test_nothing_to_change_is_refused(self):
        xref = self._all_words(highlight_options.HIGHLIGHT)
        self.assertFalse(self.doc.set_highlight_appearance(0, xref, opacity=0.6))
        self.assertFalse(self.doc.set_highlight_appearance(0, xref, opacity=0))
        self.assertFalse(self.doc.set_highlight_appearance(0, xref, opacity=1.5))
        self.assertFalse(self.doc.set_highlight_appearance(0, xref, width=24), "a text highlight has no width")
        self.assertFalse(self.doc.set_highlight_appearance(0, 99999, opacity=0.3))

    def test_a_stroke_changes_width_in_place_and_undoes(self):
        xref = self.doc.add_ink_stroke(0, [(80, 200), (240, 200)], width=8, opacity=0.6)
        self.assertTrue(self.doc.set_highlight_appearance(0, xref, width=36))
        (found,) = self.doc.highlights_on_page(0)
        self.assertEqual((found.xref, found.shape.width), (xref, 36.0))
        self.doc.undo_highlight()
        (found,) = self.doc.highlights_on_page(0)
        self.assertEqual(found.shape.width, 8.0)

    def test_an_edited_stroke_is_saved_with_its_width_and_opacity(self):
        xref = self.doc.add_ink_stroke(0, [(80, 200), (240, 200)])
        self.doc.set_highlight_appearance(0, xref, width=24, opacity=0.9)
        page = self._saved_copy()[0]  # held while its Annot is used
        (annot,) = page.annots()
        self.assertEqual(annot.border["width"], 24)
        self.assertAlmostEqual(annot.opacity, 0.9, places=3)


class UnderTheMinimumTests(unittest.TestCase):
    """A highlight already in a PDF below the slider's 20% (written at 10%
    when that was allowed, or by another app) is left exactly as it is."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-styles-faint-")
        self.addCleanup(self.dir.cleanup)
        source = pymupdf.open(str(pdf_support.plain_text(Path(self.dir.name))))
        page = source[0]
        annot = page.add_highlight_annot(page.get_text("words")[0][:4])
        annot.set_colors(stroke=(0xF7 / 255, 0xDE / 255, 0x7A / 255))  # --highlight-yellow
        annot.set_opacity(0.1)
        annot.update()
        self.path = str(Path(self.dir.name) / "faint.pdf")
        source.save(self.path)
        source.close()
        self.doc = PdfDocument()
        self.doc.open(self.path)
        self.addCleanup(self.doc.close)

    def test_it_opens_at_its_own_opacity(self):
        (found,) = self.doc.highlights_on_page(0)
        self.assertAlmostEqual(found.shape.opacity, 0.1, places=3)

    def test_saving_with_other_changes_leaves_it_at_ten_percent(self):
        self.doc.add_ink_stroke(0, [(80, 300), (240, 300)])
        copy = str(Path(self.dir.name) / "copy.pdf")
        self.doc.save_copy(copy)
        reopened = pymupdf.open(copy)
        self.addCleanup(reopened.close)
        faint = [a for a in reopened[0].annots() if a.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT]
        self.assertEqual(len(faint), 1)
        self.assertAlmostEqual(faint[0].opacity, 0.1, places=3)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-styles-settings-")
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "settings.json"
        patcher = mock.patch.object(store, "_settings_path", lambda: self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults(self):
        self.assertEqual(store.get_highlight_style(), "highlight")
        self.assertEqual(store.get_highlight_opacities(), {"highlight": 60, "underline": 100, "strikethrough": 100})
        self.assertEqual(store.get_ink_width(), 14)

    def test_each_option_is_remembered(self):
        store.set_highlight_style("strikethrough")
        store.set_highlight_opacity("underline", 45)
        store.set_highlight_opacity("highlight", 80)
        store.set_ink_width(36)
        self.assertEqual(store.get_highlight_style(), "strikethrough")
        self.assertEqual(store.get_highlight_opacities(), {"highlight": 80, "underline": 45, "strikethrough": 100})
        self.assertEqual(store.get_ink_width(), 36)

    def test_bad_values_are_refused(self):
        for call in (
            lambda: store.set_highlight_style("squiggly"),
            lambda: store.set_highlight_opacity("underline", 5),
            lambda: store.set_highlight_opacity("underline", 101),
            lambda: store.set_highlight_opacity("underline", True),
            lambda: store.set_highlight_opacity("bold", 50),
            lambda: store.set_ink_width(15),
        ):
            with self.assertRaises(ValueError):
                call()

    def test_a_hand_edited_file_falls_back_to_the_defaults(self):
        self.path.write_text(json.dumps({
            "highlight_style": "bold",
            "highlight_opacity": {"highlight": 3, "underline": "50", "strikethrough": 70, "extra": 40},
            "ink_width": 1000,
        }), encoding="utf-8")
        self.assertEqual(store.get_highlight_style(), "highlight")
        self.assertEqual(store.get_highlight_opacities(), {"highlight": 60, "underline": 100, "strikethrough": 70})
        self.assertEqual(store.get_ink_width(), 14)

    def test_a_remembered_ten_percent_loads_as_twenty_and_is_written_back(self):
        self.path.write_text(json.dumps({
            "highlight_style": "underline",
            "highlight_opacity": {"highlight": 10, "underline": 15, "strikethrough": 70},
        }), encoding="utf-8")
        self.assertEqual(store.get_highlight_opacities(), {"highlight": 20, "underline": 20, "strikethrough": 70})
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["highlight_opacity"], {"highlight": 20, "underline": 20, "strikethrough": 70})
        # Nothing else in the file changes.
        self.assertEqual(saved["highlight_style"], "underline")
        self.assertEqual(store.get_highlight_opacity("highlight"), 20)

    def test_settings_at_or_above_the_minimum_are_not_rewritten(self):
        self.path.write_text(json.dumps({"highlight_opacity": {"highlight": 20}}), encoding="utf-8")
        before = self.path.read_bytes()
        self.assertEqual(store.get_highlight_opacities()["highlight"], 20)
        self.assertEqual(self.path.read_bytes(), before)

    def test_the_slider_s_minimum_is_twenty(self):
        with self.assertRaises(ValueError):
            store.set_highlight_opacity("highlight", 10)
        with self.assertRaises(ValueError):
            store.set_highlight_opacity("highlight", 19)
        store.set_highlight_opacity("highlight", 20)
        self.assertEqual(store.get_highlight_opacity("highlight"), 20)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-styles-api-")
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(store, "_settings_path", lambda: Path(self.dir.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api.open_pdf(str(pdf_support.plain_text(Path(self.dir.name))))
        self.addCleanup(self.api._doc.close)

    def test_the_options_the_popover_starts_from(self):
        self.assertEqual(self.api.get_highlight_options(), {
            "style": "highlight",
            "styles": ["highlight", "underline", "strikethrough"],
            "opacity": {"highlight": 60, "underline": 100, "strikethrough": 100},
            "default_opacity": {"highlight": 60, "underline": 100, "strikethrough": 100},
            "opacity_min": 20,
            "opacity_max": 100,
            "opacity_snap": 3,
            "opacity_tick_step": 10,
            "ink_width": 14,
            "ink_widths": [4, 8, 14, 24, 36],
        })

    def test_options_are_set_and_bad_ones_ignored(self):
        self.api.set_highlight_style("underline")
        self.api.set_highlight_style("bold")
        self.api.set_highlight_opacity("underline", 40)
        self.api.set_highlight_opacity("underline", 400)
        self.api.set_highlight_opacity("underline", "40")
        self.api.set_ink_width(24)
        self.api.set_ink_width(25)
        options = self.api.get_highlight_options()
        self.assertEqual(options["style"], "underline")
        self.assertEqual(options["opacity"]["underline"], 40)
        self.assertEqual(options["ink_width"], 24)

    def test_a_drag_writes_the_active_style_at_its_opacity(self):
        self.api.set_highlight_style("strikethrough")
        self.api.set_highlight_opacity("strikethrough", 70)
        state = self.api.highlight_chars(0, 0, 8)
        self.assertEqual(state["highlight_count_total"], 1)
        (found,) = self.api.get_page_highlights(0)["highlights"]
        self.assertEqual((found["kind"], found["style"]), ("text", "strikethrough"))
        self.assertAlmostEqual(found["opacity"], 0.7, places=3)

    def test_a_stroke_takes_the_width_and_the_highlight_opacity(self):
        self.api.set_ink_width(36)
        self.api.set_highlight_opacity("highlight", 30)
        self.api.set_highlight_opacity("underline", 90)
        self.api.set_highlight_style("underline")
        self.api.add_ink_stroke(0, [[80, 200], [240, 200]])
        (found,) = self.api.get_page_highlights(0)["highlights"]
        self.assertEqual((found["kind"], found["style"], found["width"]), ("ink", "ink", 36))
        self.assertAlmostEqual(found["opacity"], 0.3, places=3)

    def test_an_existing_highlight_is_restyled_over_the_bridge(self):
        self.api.highlight_chars(0, 0, 8)
        (found,) = self.api.get_page_highlights(0)["highlights"]
        state = self.api.set_highlight_appearance(0, found["id"], 25, None, "g1")
        self.assertEqual(state["affected_page"], 0)
        self.assertTrue(state["can_undo"])
        (found,) = self.api.get_page_highlights(0)["highlights"]
        self.assertAlmostEqual(found["opacity"], 0.25, places=3)

    def test_the_bridge_refuses_bad_appearance_values(self):
        self.api.highlight_chars(0, 0, 8)
        (found,) = self.api.get_page_highlights(0)["highlights"]
        for opacity, width in ((5, None), (101, None), ("50", None), (True, None), (None, 15), (None, 24)):
            with self.subTest(opacity=opacity, width=width):
                state = self.api.set_highlight_appearance(0, found["id"], opacity, width, "g")
                self.assertIsNone(state["affected_page"])
        for page, ident in ((-1, found["id"]), ("0", found["id"]), (0, "x"), (0, 10 ** 400)):
            with self.subTest(page=page, ident=ident):
                self.assertIsNone(self.api.set_highlight_appearance(page, ident, 50, None, "g")["affected_page"])

    def test_voice_underlines_and_strikes_through(self):
        self.api.start_listening(viewport=[{"page_index": 0, "y0": 0, "y1": 900}])
        self.api.stop_listening()
        state = self.api.highlight_by_voice("quick brown", False, "pink", "underline")
        self.assertTrue(state["voice_highlight"]["matched"])
        self.assertEqual(state["voice_highlight"]["style"], "underline")
        state = self.api.highlight_by_voice("lazy dog", False, None, "strikethrough")
        self.assertEqual(state["voice_highlight"]["style"], "strikethrough")
        styles = [(h["style"], h["color"]) for h in self.api.get_page_highlights(0)["highlights"]]
        self.assertEqual(styles, [("underline", "pink"), ("strikethrough", "yellow")])


if __name__ == "__main__":
    unittest.main()
