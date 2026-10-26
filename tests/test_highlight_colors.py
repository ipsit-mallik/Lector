"""Tests for highlight colors: five fixed colors, the active one remembered,
existing highlights recolored or removed, by mouse, keyboard and voice.

The promises held here, roughly from the PDF outwards:

* **The palette is tuned, and stays tuned.** Text under every color stays far
  above body-text contrast, and the five stay apart from each other and from
  the other things drawn on the page (the find marks, the selection wash),
  measured in CIEDE2000 — so a later tweak to one value cannot quietly make
  two of them look alike.
* **Another viewer sees the same color.** The color is in the annotation's
  /C *and* its appearance stream, checked on a saved copy reopened from disk,
  down to the rendered pixel.
* **Every change is one undo step,** including recolors and removals, and a
  highlight another app wrote comes back exactly as it was.
* **The active color is a setting, not page state,** and a one-off voice color
  does not change it.
"""
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the throwaway PDFs these tests open

from lector import api as api_module  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import command_grammar as grammar  # noqa: E402
from lector.shared import theme  # noqa: E402

FRONTEND = ROOT / "frontend"
THEME_CSS = (FRONTEND / "shared" / "theme.css").read_text(encoding="utf-8")
READING_HTML = (FRONTEND / "pages" / "reading.html").read_text(encoding="utf-8")
READING_JS = (FRONTEND / "pages" / "reading.js").read_text(encoding="utf-8")
COLORS_JS = (FRONTEND / "pages" / "highlight-colors.js").read_text(encoding="utf-8")

TEXT = "The quick brown fox jumps over the lazy dog"
BODY_TEXT = (0x16, 0x1A, 0x18)  # light theme --text-primary, the darkest text a page is likely to carry


def _hex(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _over_paper(rgb, alpha):
    return tuple(255 - alpha * (255 - c) for c in rgb)


def _linear(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    return sum(w * _linear(c) for w, c in zip((0.2126, 0.7152, 0.0722), rgb))


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _lab(rgb):
    r, g, b = (_linear(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e2000(c1, c2):
    """CIEDE2000 between two sRGB colors (Sharma et al.'s formulation)."""
    (l1, a1, b1), (l2, a2, b2) = _lab(c1), _lab(c2)
    c_bar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dh = 0 if c1p * c2p == 0 else (h2p - h1p + 180) % 360 - 180
    d_hp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    l_bar, c_barp = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) > 180:
        h_bar = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    else:
        h_bar = (h1p + h2p) / 2
    t = (1 - 0.17 * math.cos(math.radians(h_bar - 30)) + 0.24 * math.cos(math.radians(2 * h_bar))
         + 0.32 * math.cos(math.radians(3 * h_bar + 6)) - 0.2 * math.cos(math.radians(4 * h_bar - 63)))
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / math.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * c_barp
    s_h = 1 + 0.015 * c_barp * t
    r_t = (-2 * math.sqrt(c_barp ** 7 / (c_barp ** 7 + 25 ** 7))
           * math.sin(math.radians(60 * math.exp(-(((h_bar - 275) / 25) ** 2)))))
    return math.sqrt(((l2 - l1) / s_l) ** 2 + ((c2p - c1p) / s_c) ** 2 + (d_hp / s_h) ** 2
                     + r_t * ((c2p - c1p) / s_c) * (d_hp / s_h))


def _rgba_token(name: str):
    match = re.search(rf"--{name}:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", THEME_CSS)
    return tuple(int(v) for v in match.group(1, 2, 3)), float(match.group(4))


def _palette() -> dict[str, tuple[int, int, int]]:
    return {name: _hex(theme.shared_token(f"highlight-{name}")) for name in theme.HIGHLIGHT_COLOR_NAMES}


def _make_pdf(directory: str) -> str:
    path = str(Path(directory) / "doc.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 80), TEXT, fontsize=14)
    page.insert_text((40, 140), TEXT, fontsize=14)
    doc.save(path)
    doc.close()
    return path


class PaletteTests(unittest.TestCase):
    def test_five_fixed_colors_yellow_first_and_default(self):
        self.assertEqual(theme.HIGHLIGHT_COLOR_NAMES, ("yellow", "green", "blue", "pink", "orange"))
        self.assertEqual(theme.DEFAULT_HIGHLIGHT_COLOR, "yellow")
        self.assertEqual(tuple(highlighter.HIGHLIGHT_COLORS), theme.HIGHLIGHT_COLOR_NAMES)

    def test_the_pdf_colors_are_the_theme_tokens(self):
        # One home for each value: the swatch the reader picks (theme.css) is
        # the color written into the file.
        for name, rgb in _palette().items():
            with self.subTest(name=name):
                self.assertEqual(highlighter.HIGHLIGHT_COLORS[name], tuple(c / 255 for c in rgb))

    def test_the_colors_are_not_themed(self):
        # They are on the white paper in every theme, and in the file.
        for name in theme.HIGHLIGHT_COLOR_NAMES:
            with self.subTest(name=name):
                self.assertEqual(len(re.findall(rf"--highlight-{name}:", THEME_CSS)), 1)

    def test_text_stays_readable_under_every_color(self):
        # Multiplied onto white paper, the fill is the color itself; body text
        # under it has to stay well past WCAG AAA's 7:1.
        for name, rgb in _palette().items():
            with self.subTest(name=name):
                self.assertGreaterEqual(_contrast(rgb, BODY_TEXT), 8.0)

    def test_the_colors_are_distinguishable_from_each_other(self):
        palette = list(_palette().items())
        for i, (a, ca) in enumerate(palette):
            for b, cb in palette[i + 1:]:
                with self.subTest(pair=(a, b)):
                    self.assertGreaterEqual(_delta_e2000(ca, cb), 20)

    def test_no_color_reads_as_a_find_match_or_a_selection(self):
        others = {
            "find match": _over_paper(*_rgba_token("find-match")),
            "current find match": _over_paper(*_rgba_token("find-match-current")),
            "selection wash": _over_paper(*_rgba_token("selection")),
        }
        for name, rgb in _palette().items():
            for other, other_rgb in others.items():
                with self.subTest(color=name, other=other):
                    self.assertGreaterEqual(_delta_e2000(rgb, other_rgb), 15)


class PdfAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-colors-")
        self.addCleanup(self.dir.cleanup)
        self.doc = PdfDocument()
        self.doc.open(_make_pdf(self.dir.name))
        self.addCleanup(self.doc.close)

    def _saved_copy(self):
        path = str(Path(self.dir.name) / "copy.pdf")
        self.doc.save_copy(path)
        reopened = pymupdf.open(path)
        self.addCleanup(reopened.close)
        return reopened

    def test_every_color_is_written_to_c_and_to_the_appearance(self):
        for i, name in enumerate(theme.HIGHLIGHT_COLOR_NAMES):
            self.doc.highlight_char_indices(0, i * 4, i * 4 + 2, name)
        # The Page is held for as long as its Annots are used: PyMuPDF unbinds
        # an Annot once the Page wrapper it came from is collected.
        page = self._saved_copy()[0]
        annots = list(page.annots())
        self.assertEqual(len(annots), 5)
        for annot, name in zip(annots, theme.HIGHLIGHT_COLOR_NAMES):
            rgb = highlighter.HIGHLIGHT_COLORS[name]
            with self.subTest(name=name):
                self.assertEqual(annot.type[0], pymupdf.PDF_ANNOT_HIGHLIGHT)
                self.assertEqual(highlighter.color_name(highlighter.stroke_rgb(annot)), name)
                fill = re.search(rb"([\d.]+) ([\d.]+) ([\d.]+) rg", annot._getAP())
                self.assertIsNotNone(fill)
                for got, want in zip(fill.groups(), rgb):
                    self.assertAlmostEqual(float(got), want, places=3)

    def test_a_saved_highlight_renders_in_its_color(self):
        # What a viewer that honours the appearance stream draws: a fresh
        # reader of the saved file. Inside the highlight's line box the glyphs
        # are the minority of pixels; everything else is the fill multiplied
        # onto white, i.e. the color itself.
        self.doc.highlight_word_indices(0, 0, 3, "blue")
        page = self._saved_copy()[0]
        (box,) = highlighter.highlight_rects(next(page.annots()))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=box)
        counts = {}
        for y in range(pix.height):
            for x in range(pix.width):
                counts[pix.pixel(x, y)] = counts.get(pix.pixel(x, y), 0) + 1
        most_common = max(counts, key=counts.get)
        for got, want in zip(most_common, _palette()["blue"]):
            self.assertLessEqual(abs(got - want), 2)

    def test_recolor_remove_undo_redo_walk_back_and_forth(self):
        xref = self.doc.highlight_char_indices(0, 4, 8, "green")
        self.assertTrue(self.doc.set_highlight_color(0, xref, "pink"))
        self.assertTrue(self.doc.remove_highlight(0, xref))

        def colors():
            return [h.color for h in self.doc.highlights_on_page(0)]

        self.assertEqual(colors(), [])
        self.doc.undo_highlight()
        self.assertEqual(colors(), ["pink"])  # removal undone: a *new* annotation
        self.doc.undo_highlight()
        self.assertEqual(colors(), ["green"])  # and the recolor finds that new one
        self.doc.undo_highlight()
        self.assertEqual(colors(), [])
        self.assertFalse(self.doc.can_undo)
        for want in (["green"], ["pink"], []):
            self.doc.redo_highlight()
            self.assertEqual(colors(), want)
        self.assertTrue(self.doc.is_dirty)

    def test_a_new_change_clears_redo(self):
        xref = self.doc.highlight_char_indices(0, 4, 8, "green")
        self.doc.set_highlight_color(0, xref, "blue")
        self.doc.undo_highlight()
        self.assertTrue(self.doc.can_redo)
        self.doc.remove_highlight(0, xref)
        self.assertFalse(self.doc.can_redo)

    def test_no_op_and_stale_edits_change_nothing(self):
        xref = self.doc.highlight_char_indices(0, 4, 8, "green")
        self.assertFalse(self.doc.set_highlight_color(0, xref, "green"))
        self.assertFalse(self.doc.set_highlight_color(0, xref, "purple"))
        self.assertFalse(self.doc.set_highlight_color(0, xref + 1000, "blue"))
        self.assertFalse(self.doc.remove_highlight(0, xref + 1000))
        self.assertFalse(self.doc.remove_highlight(5, xref))
        self.doc.undo_highlight()
        self.assertFalse(self.doc.can_undo)  # only the add was ever recorded

    def test_another_apps_highlight_comes_back_exactly(self):
        page = self.doc._doc[0]
        foreign = page.add_highlight_annot(pymupdf.Rect(40, 66, 160, 84))
        foreign.set_colors(stroke=(0.2, 0.8, 0.9))
        foreign.set_info(content="a note", title="someone")
        foreign.update()
        xref = foreign.xref
        del page, foreign

        self.assertEqual([h.color for h in self.doc.highlights_on_page(0)], [None])
        self.assertTrue(self.doc.set_highlight_color(0, xref, "orange"))
        self.doc.undo_highlight()
        page = self.doc._doc[0]  # held while its Annots are used
        annot = next(page.annots())
        for got, want in zip(highlighter.stroke_rgb(annot), (0.2, 0.8, 0.9)):
            self.assertAlmostEqual(got, want, places=3)
        xref = annot.xref
        del annot, page

        self.assertTrue(self.doc.remove_highlight(0, xref))
        self.doc.undo_highlight()
        page = self.doc._doc[0]
        restored = next(page.annots())
        self.assertEqual(restored.info["content"], "a note")
        self.assertEqual(restored.info["title"], "someone")
        for got, want in zip(highlighter.stroke_rgb(restored), (0.2, 0.8, 0.9)):
            self.assertAlmostEqual(got, want, places=3)

    def test_highlights_report_their_text_and_line_boxes(self):
        self.doc.highlight_word_indices(0, 1, 3, "pink")
        (found,) = self.doc.highlights_on_page(0)
        self.assertEqual(found.color, "pink")
        self.assertEqual(len(found.rects), 1)
        self.assertEqual(self.doc.text_in_rects(0, found.rects), "quick brown fox")


class _SettingsIsolated(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-colors-")
        self.addCleanup(self.dir.cleanup)
        self.settings_path = Path(self.dir.name) / "settings.json"
        patcher = mock.patch.object(store, "_settings_path", lambda: self.settings_path)
        patcher.start()
        self.addCleanup(patcher.stop)


class ActiveColorSettingTests(_SettingsIsolated):
    def test_defaults_to_yellow(self):
        self.assertEqual(store.get_highlight_color(), "yellow")

    def test_is_remembered(self):
        store.set_highlight_color("orange")
        self.assertEqual(store.get_highlight_color(), "orange")
        self.assertIn('"highlight_color": "orange"', self.settings_path.read_text(encoding="utf-8"))

    def test_rejects_a_color_outside_the_palette(self):
        with self.assertRaises(ValueError):
            store.set_highlight_color("purple")

    def test_a_hand_edited_value_falls_back_to_yellow(self):
        self.settings_path.write_text('{"highlight_color": "mauve"}', encoding="utf-8")
        self.assertEqual(store.get_highlight_color(), "yellow")


class ApiTests(_SettingsIsolated):
    def setUp(self):
        super().setUp()
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api.open_pdf(_make_pdf(self.dir.name))
        # Before the temp folder goes: Windows will not delete an open file.
        self.addCleanup(self.api._doc.close)

    def test_new_highlights_use_the_active_color(self):
        self.assertEqual(self.api.set_highlight_color("green")["highlight_color"], "green")
        self.api.highlight_chars(0, 4, 8)
        (found,) = self.api.get_page_highlights(0)["highlights"]
        self.assertEqual(found["color"], "green")
        self.assertEqual(found["text"], "quick")

    def test_an_unknown_color_from_the_bridge_is_ignored(self):
        self.assertEqual(self.api.set_highlight_color("purple")["highlight_color"], "yellow")

    def test_page_highlights_carry_what_a_target_needs(self):
        self.api.highlight_chars(0, 4, 8)
        page = self.api.get_page_highlights(0)
        self.assertEqual((page["width"], page["height"]), (400, 300))
        (found,) = page["highlights"]
        self.assertEqual(set(found), {"id", "color", "rects", "text"})
        self.assertEqual(set(found["rects"][0]), {"x0", "y0", "x1", "y1"})

    def test_recolor_and_remove_report_the_page_they_changed(self):
        self.api.highlight_chars(0, 4, 8)
        (found,) = self.api.get_page_highlights(0)["highlights"]
        self.assertEqual(self.api.recolor_highlight(0, found["id"], "blue")["affected_page"], 0)
        self.assertEqual(self.api.get_page_highlights(0)["highlights"][0]["color"], "blue")
        self.assertIsNone(self.api.recolor_highlight(0, found["id"], "blue")["affected_page"])
        self.assertEqual(self.api.remove_highlight(0, found["id"])["affected_page"], 0)
        self.assertEqual(self.api.get_page_highlights(0)["highlights"], [])
        self.assertEqual(self.api.undo_highlight()["affected_page"], 0)
        self.assertEqual(self.api.get_page_highlights(0)["highlights"][0]["color"], "blue")

    def test_a_one_off_voice_color_leaves_the_active_color_alone(self):
        self.api._voice_viewport = [{"page_index": 0, "y0": 0, "y1": 300}]
        state = self.api.highlight_by_voice("quick brown", False, "pink")
        self.assertTrue(state["voice_highlight"]["matched"])
        self.assertEqual(state["voice_highlight"]["color"], "pink")
        self.assertEqual(state["highlight_color"], "yellow")
        self.assertEqual(self.api.get_page_highlights(0)["highlights"][0]["color"], "pink")

    def test_voice_without_a_color_uses_the_active_one(self):
        self.api.set_highlight_color("orange")
        self.api._voice_viewport = [{"page_index": 0, "y0": 0, "y1": 300}]
        self.api.highlight_by_voice("lazy dog")
        self.assertEqual(self.api.get_page_highlights(0)["highlights"][0]["color"], "orange")


class GrammarTests(unittest.TestCase):
    def test_highlight_color_sets_the_active_color(self):
        for phrase in ("highlight color green", "set highlight color to pink", "change highlight color to blue"):
            with self.subTest(phrase=phrase):
                parsed = grammar.parse(phrase)
                self.assertEqual(parsed["intent"], grammar.SET_HIGHLIGHT_COLOR)
                self.assertEqual(parsed["color"], phrase.split()[-1])

    def test_in_a_color_is_a_one_off_on_either_highlight_command(self):
        parsed = grammar.parse("highlight the quick brown fox in orange")
        self.assertEqual((parsed["intent"], parsed["query"], parsed["color"]),
                         (grammar.HIGHLIGHT, "the quick brown fox", "orange"))
        parsed = grammar.parse("highlight the sentence lazy dog in blue")
        self.assertEqual((parsed["intent"], parsed["query"], parsed["color"]),
                         (grammar.HIGHLIGHT_SENTENCE, "lazy dog", "blue"))

    def test_a_plain_highlight_has_no_color(self):
        self.assertNotIn("color", grammar.parse("highlight quick brown fox"))

    def test_a_color_word_on_the_page_is_still_just_a_word(self):
        # "green" alone is text to find, not a color change.
        parsed = grammar.parse("highlight green")
        self.assertEqual((parsed["intent"], parsed["query"]), (grammar.HIGHLIGHT, "green"))
        self.assertNotIn("color", parsed)

    def test_a_color_change_needs_the_word_color(self):
        # "highlight cool in green" sits close to "highlight color" by edit
        # distance; it must stay a one-off highlight of "cool".
        parsed = grammar.parse("highlight cool in green")
        self.assertEqual((parsed["intent"], parsed["query"], parsed["color"]),
                         (grammar.HIGHLIGHT, "cool", "green"))

    def test_nothing_before_in_color_means_those_are_the_words(self):
        parsed = grammar.parse("highlight in green")
        self.assertEqual((parsed["intent"], parsed["query"]), (grammar.HIGHLIGHT, "in green"))
        self.assertNotIn("color", parsed)

    def test_a_color_outside_the_palette_is_not_a_color(self):
        parsed = grammar.parse("highlight color purple")
        self.assertEqual(parsed["intent"], grammar.HIGHLIGHT)
        self.assertNotIn("color", parsed)

    def test_the_recognizer_can_hear_every_color_word(self):
        for word in (*grammar.HIGHLIGHT_COLORS, "color", "in", "set", "change", "to"):
            with self.subTest(word=word):
                self.assertIn(word, grammar.VOCABULARY)


class FrontendWiringTests(unittest.TestCase):
    def test_the_color_button_sits_with_the_highlight_tool_and_says_what_it_opens(self):
        group = re.search(r'<div class="rail-highlight">(.*?)</div>\s*</div>', READING_HTML, flags=re.S)
        self.assertIsNotNone(group)
        block = group.group(1)
        self.assertIn('id="highlightBtn"', block)
        self.assertIn('id="highlightColorBtn"', block)
        for attr in ('aria-haspopup="true"', 'aria-expanded="false"', 'aria-controls="highlightColorFlyout"'):
            self.assertIn(attr, block)

    def test_the_editor_is_a_labelled_dialog_with_a_remove_button(self):
        self.assertRegex(READING_HTML, r'id="highlightEditor"[^>]*role="dialog"')
        self.assertIn('id="highlightEditorRemoveBtn"', READING_HTML)

    def test_the_module_loads_before_the_reader_that_uses_it(self):
        self.assertLess(READING_HTML.index("highlight-colors.js"), READING_HTML.index('src="reading.js"'))

    def test_swatches_name_colors_by_token_never_by_value(self):
        # theme.css is the only file with literal colors (its own header says
        # so); the module refers to --highlight-<name> only.
        self.assertNotRegex(COLORS_JS, r"#[0-9A-Fa-f]{6}\b|rgba?\(")
        self.assertIn("var(--highlight-${name})", COLORS_JS)

    def test_a_chosen_swatch_is_marked_by_more_than_color(self):
        self.assertIn('check.dataset.icon = "check"', COLORS_JS)
        self.assertIn("initRadioCards(group, { selectOnArrow: false })", COLORS_JS)

    def test_voice_handles_both_color_commands(self):
        self.assertIn("SET_HIGHLIGHT_COLOR:", READING_JS)
        self.assertIn("voiceHighlight(command.query, false, command.color)", READING_JS)
        self.assertIn("voiceHighlight(command.query, true, command.color)", READING_JS)

    def test_the_release_flash_uses_the_paper_color_not_the_themed_token(self):
        # Dark's --highlight is a fill for dark chrome; on the white page it
        # flashed brown before the real (yellow) annotation appeared.
        css = (FRONTEND / "pages" / "reading.css").read_text(encoding="utf-8")
        rule = re.search(r"\.selection-rect\.applied \{(.*?)\}", css, flags=re.S).group(1)
        self.assertIn("var(--applied-highlight, var(--highlight-yellow))", rule)
        self.assertNotIn("var(--highlight)", rule)


if __name__ == "__main__":
    unittest.main()
