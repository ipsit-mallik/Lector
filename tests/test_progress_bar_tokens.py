"""The reader's progress bar colours (docs/DESIGN_SYSTEM.md, "Reader progress
bar"): one fixed marker colour for pages with highlights, visible on both the
read and unread parts of the bar in every theme.

The marker is lighter than any track or fill, so it can clear 3:1 against both
at once (a marker between the two could not, unless they were 9:1 apart).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.shared import theme  # noqa: E402

from test_theme_tokens import contrast  # noqa: E402

NON_TEXT = 3.0


class ProgressBarTokenTests(unittest.TestCase):
    def setUp(self):
        self.marker = theme.shared_token("progress-marker")

    def test_the_marker_is_one_colour_for_every_theme(self):
        # Defined once in theme.css's shared block, and overridden by no theme.
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                with self.assertRaises(KeyError):
                    theme.token(name, "progress-marker")

    def test_the_marker_stands_off_the_read_and_the_unread_bar(self):
        for name in theme.THEME_ORDER:
            for part in ("progress-track", "progress-fill"):
                with self.subTest(theme=name, part=part):
                    self.assertGreaterEqual(contrast(self.marker, theme.token(name, part)), NON_TEXT)

    def test_read_and_unread_are_told_apart(self):
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                ratio = contrast(theme.token(name, "progress-fill"), theme.token(name, "progress-track"))
                self.assertGreaterEqual(ratio, 2.9)

    def test_the_read_fill_shows_on_the_window(self):
        for name in theme.THEME_ORDER:
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    contrast(theme.token(name, "progress-fill"), theme.token(name, "bg")), NON_TEXT
                )


if __name__ == "__main__":
    unittest.main()
