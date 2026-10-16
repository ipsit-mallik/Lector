"""The reader's progress bar colours (docs/DESIGN_SYSTEM.md, "Reader progress
bar"), all per theme: the unread track, the read fill, the highlight marker and
its edge, and the current-page label.

The bar's place is told by where the read fill ends, so the fill has to clear
3:1 against the window (WCAG 1.4.11) -- against --bg, the page area beside it,
and --surface-1, the column it sits in -- and against the unread track. In
light and sepia the track is deliberately pale, close to the window, so the bar
stops reading as the window's frame; it is not held to 3:1 on the window.

A highlight marker is light amber in a 1px dark edge (bright yellow in a
near-black edge in dark): the amber or the edge clears 3:1 on the track and on
the window, and the amber is apart from the fill by hue.

These ratios are for the active bar. Idle, the whole bar is at 55% opacity and
under 3:1 on purpose; the header's page number is the primary position
indicator.
"""
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
PARTS = (
    "progress-track", "progress-fill", "progress-marker", "progress-marker-edge",
    "progress-label", "progress-label-text",
)

# docs/DESIGN_SYSTEM.md token table. Sepia's fill is light's green one step
# darker: light's is 2.95:1 on sepia's track.
EXPECTED = {
    "light": {"track": "#CFCAC1", "fill": "#2E7D64", "marker": "#F2C14E", "marker-edge": "#8A5A00"},
    "sepia": {"track": "#D6C6A6", "fill": "#2C775F", "marker": "#F2C14E", "marker-edge": "#8A5A00"},
    "dark": {"track": "#78726A", "fill": "#B8DBD0", "marker": "#FFE98F", "marker-edge": "#1A1816"},
}


def tok(name, part):
    return theme.token(name, part)


def best(name, part, against):
    """The better of the marker's amber and its edge against `against`."""
    return max(contrast(tok(name, part), against), contrast(tok(name, part + "-edge"), against))


class ProgressBarTokenTests(unittest.TestCase):
    def test_every_part_is_a_theme_token(self):
        for part in PARTS:
            with self.subTest(part=part):
                with self.assertRaises(KeyError):
                    theme.shared_token(part)
                for name in theme.THEME_ORDER:
                    self.assertTrue(tok(name, part))

    def test_the_values_are_the_specified_ones(self):
        for name, parts in EXPECTED.items():
            for part, value in parts.items():
                with self.subTest(theme=name, part=part):
                    self.assertEqual(tok(name, f"progress-{part}"), value)

    def test_the_fill_shows_on_the_window(self):
        for name in theme.THEME_ORDER:
            for surface in WINDOW:
                with self.subTest(theme=name, against=surface):
                    self.assertGreaterEqual(contrast(tok(name, "progress-fill"), tok(name, surface)), NON_TEXT)

    def test_read_and_unread_are_told_apart(self):
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    contrast(tok(name, "progress-fill"), tok(name, "progress-track")), NON_TEXT
                )

    def test_light_and_sepia_tracks_recede_into_the_window(self):
        # The point of the change: the unread track is pale, not frame-dark.
        for name in ("light", "sepia"):
            with self.subTest(theme=name):
                self.assertLess(contrast(tok(name, "progress-track"), tok(name, "bg")), 1.5)

    def test_a_marker_shows_on_the_track_and_on_the_window(self):
        # A marker's edge is flush with the bar's sides, beside the column it sits in.
        for name in theme.THEME_ORDER:
            for against in ("progress-track", *WINDOW):
                with self.subTest(theme=name, against=against):
                    self.assertGreaterEqual(best(name, "progress-marker", tok(name, against)), NON_TEXT)

    def test_a_marker_differs_from_the_fill_by_hue(self):
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    _delta_e2000(_hex(tok(name, "progress-marker")), _hex(tok(name, "progress-fill"))),
                    HUE_APART,
                )

    def test_the_marker_edge_is_darker_than_the_marker(self):
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    contrast(tok(name, "progress-marker"), tok(name, "progress-marker-edge")), NON_TEXT
                )

    def test_the_page_label_is_a_chip_in_the_read_colour(self):
        for name in theme.THEME_ORDER:
            chip = tok(name, "progress-label")
            with self.subTest(theme=name):
                self.assertEqual(chip, tok(name, "progress-fill"))
                self.assertGreaterEqual(contrast(tok(name, "progress-label-text"), chip), TEXT)
                self.assertGreaterEqual(contrast(chip, tok(name, "bg")), NON_TEXT)


if __name__ == "__main__":
    unittest.main()
