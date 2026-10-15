"""The reader's progress bar colours (docs/DESIGN_SYSTEM.md, "Reader progress
bar"), all per theme: the unread track, the read fill, the highlight marker
and the current-page label.

The collapsed line is 4px at the window's edge, so every part of it has to be
seen against the window on its own (WCAG 1.4.11, 3:1): against --bg, the page
area beside it, and --surface-1, the outline panel beside it when that is open.

Track and fill are a ladder from the window, 3:1 a step, so read and unread
are told apart too. A third 3:1 step would need 27:1, more than black on white,
so the marker is 3:1 off the window and one part of the bar, and apart from
the other by hue. In light and sepia that one part is the fill (a dark ochre
marker on a near-black fill); dark keeps its bright yellow marker, which
stands off the track.
"""
import colorsys
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.shared import theme  # noqa: E402

from test_highlight_colors import _delta_e2000, _hex  # noqa: E402
from test_theme_tokens import contrast  # noqa: E402

NON_TEXT = 3.0
TEXT = 4.5
# Far enough apart in hue to read as different colours at a glance.
HUE_APART = 18
WINDOW = ("bg", "surface-1")
PARTS = ("progress-track", "progress-fill", "progress-marker")


def tok(name, part):
    return theme.token(name, part)


def _hsv(hex_value):
    r, g, b = (c / 255 for c in _hex(hex_value))
    return colorsys.rgb_to_hsv(r, g, b)


class ProgressBarTokenTests(unittest.TestCase):
    def test_every_part_is_a_theme_token(self):
        for part in (*PARTS, "progress-label", "progress-label-text"):
            with self.subTest(part=part):
                with self.assertRaises(KeyError):
                    theme.shared_token(part)
                for name in theme.THEME_ORDER:
                    self.assertTrue(tok(name, part))

    def test_every_part_shows_on_the_window(self):
        for name in theme.THEME_ORDER:
            for part in PARTS:
                for surface in WINDOW:
                    with self.subTest(theme=name, part=part, against=surface):
                        self.assertGreaterEqual(contrast(tok(name, part), tok(name, surface)), NON_TEXT)

    def test_read_and_unread_are_told_apart(self):
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    contrast(tok(name, "progress-fill"), tok(name, "progress-track")), NON_TEXT
                )

    def test_the_marker_stands_off_one_part_of_the_bar_and_differs_from_both_in_hue(self):
        for name in theme.THEME_ORDER:
            marker = tok(name, "progress-marker")
            ratios = [contrast(marker, tok(name, part)) for part in ("progress-track", "progress-fill")]
            with self.subTest(theme=name):
                self.assertGreaterEqual(max(ratios), NON_TEXT)
                for part in ("progress-track", "progress-fill"):
                    self.assertGreaterEqual(_delta_e2000(_hex(marker), _hex(tok(name, part))), HUE_APART)

    def test_light_and_sepia_mark_in_a_dark_ochre_clear_of_the_fill(self):
        for name in ("light", "sepia"):
            marker = tok(name, "progress-marker")
            hue, saturation, value = _hsv(marker)
            with self.subTest(theme=name):
                self.assertGreaterEqual(contrast(marker, tok(name, "progress-fill")), NON_TEXT)
                self.assertTrue(30 <= hue * 360 <= 45, hue * 360)
                self.assertGreater(saturation, 0.9)
                # Darker than the old pale yellow, and than dark's marker.
                self.assertLess(value, _hsv(tok("dark", "progress-marker"))[2])
                self.assertLess(value, 0.7)

    def test_dark_keeps_its_bright_marker(self):
        self.assertEqual(tok("dark", "progress-marker"), "#FFE98F")

    def test_the_page_label_is_readable_and_shows_on_the_window(self):
        for name in theme.THEME_ORDER:
            chip = tok(name, "progress-label")
            with self.subTest(theme=name):
                self.assertGreaterEqual(contrast(tok(name, "progress-label-text"), chip), TEXT)
                self.assertGreaterEqual(contrast(chip, tok(name, "bg")), NON_TEXT)


if __name__ == "__main__":
    unittest.main()
