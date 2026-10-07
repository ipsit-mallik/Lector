"""Design-token contrast guarantees (docs/DESIGN_SYSTEM.md, "Tokens").

The ratios here are the contract the token values were solved against: if a
future edit to frontend/shared/theme.css drops a pair below its minimum, this
fails instead of the regression shipping silently.
"""

import colorsys
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.shared import theme  # noqa: E402
from lector.shared.window_chrome import _colorref  # noqa: E402

TEXT_AA = 4.5
NON_TEXT = 3.0
SURFACES = ("bg", "surface-1", "surface-2")

# Fixed across themes (theme.css :root), not per-theme blocks.
VOICE_FILL = "#E0954F"
ON_VOICE = "#1A130C"


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TokenContrastTests(unittest.TestCase):
    def assert_on_surfaces(self, name: str, minimum: float) -> None:
        for theme_name in theme.THEME_ORDER:
            fg = theme.token(theme_name, name)
            for surface in SURFACES:
                bg = theme.token(theme_name, surface)
                with self.subTest(theme=theme_name, fg=name, bg=surface):
                    self.assertGreaterEqual(contrast(fg, bg), minimum)

    def test_text_levels_meet_aa_on_every_surface(self):
        for name in ("text-primary", "text-secondary", "text-muted"):
            self.assert_on_surfaces(name, TEXT_AA)

    def test_tertiary_and_voice_meet_non_text_minimum(self):
        self.assert_on_surfaces("text-tertiary", NON_TEXT)
        self.assert_on_surfaces("voice", NON_TEXT)

    def test_accent_strong_and_danger_readable_as_text(self):
        for theme_name in theme.THEME_ORDER:
            for name in ("accent-strong", "danger"):
                for surface in ("surface-1", "surface-2"):
                    with self.subTest(theme=theme_name, fg=name, bg=surface):
                        ratio = contrast(theme.token(theme_name, name), theme.token(theme_name, surface))
                        self.assertGreaterEqual(ratio, TEXT_AA)

    def test_button_label_on_accent(self):
        for theme_name in theme.THEME_ORDER:
            for fill in ("accent", "accent-hover", "accent-pressed"):
                with self.subTest(theme=theme_name, fill=fill):
                    ratio = contrast(theme.token(theme_name, "surface-2"), theme.token(theme_name, fill))
                    self.assertGreaterEqual(ratio, TEXT_AA)

    def test_voice_badge_numeral(self):
        self.assertGreaterEqual(contrast(ON_VOICE, VOICE_FILL), TEXT_AA)
        css = theme.THEME_CSS.read_text(encoding="utf-8")
        self.assertIn(f"--voice-fill: {VOICE_FILL};", css)
        self.assertIn(f"--on-voice: {ON_VOICE};", css)


def _blend(top: str, bottom: str, amount: float) -> str:
    """`amount` of `top` over `bottom`, as CSS color-mix(in srgb) does."""
    mixed = [
        round(int(top[i : i + 2], 16) * amount + int(bottom[i : i + 2], 16) * (1 - amount))
        for i in (1, 3, 5)
    ]
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def _hue(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


# The grid card's action chips are the card surface (--surface-input), solid;
# the remove chip turns this much red on hover/focus, and more while pressed
# (home.css, .recent-remove-btn). Keep in step with the colour-mix percentages.
REMOVE_HOVER_TINT = 0.14
REMOVE_PRESSED_TINT = 0.24
# The star and the voice orange are both warm; their hues must stay this far apart.
MIN_STAR_VOICE_HUE_GAP = 10.0


class LibraryChromeContrastTests(unittest.TestCase):
    """The library pages' own text and icons: count, list headers and cells, the
    grid card's star and trash, and the view toggle."""

    def each_theme(self):
        for theme_name in theme.THEME_ORDER:
            yield theme_name, lambda name, t=theme_name: theme.token(t, name)

    def test_count_and_list_text_meet_aa_on_the_page_and_the_table(self):
        # Count and header row sit on surface-2; rows and the grid card on surface-input.
        for theme_name, tok in self.each_theme():
            for surface in ("surface-2", "surface-input"):
                with self.subTest(theme=theme_name, surface=surface):
                    self.assertGreaterEqual(contrast(tok("text-secondary"), tok(surface)), TEXT_AA)
                    self.assertGreaterEqual(contrast(tok("text-primary"), tok(surface)), TEXT_AA)

    def test_star_clears_three_to_one_on_its_chip_in_both_views(self):
        # Grid chip and list row are both --surface-input.
        for theme_name, tok in self.each_theme():
            with self.subTest(theme=theme_name):
                self.assertGreaterEqual(contrast(tok("star"), tok("surface-input")), NON_TEXT)

    def test_unfilled_star_and_trash_clear_three_to_one_on_their_chip(self):
        for theme_name, tok in self.each_theme():
            with self.subTest(theme=theme_name):
                self.assertGreaterEqual(contrast(tok("text-secondary"), tok("surface-input")), NON_TEXT)

    def test_trash_stays_legible_on_its_destructive_tint(self):
        for theme_name, tok in self.each_theme():
            for tint in (REMOVE_HOVER_TINT, REMOVE_PRESSED_TINT):
                chip = _blend(tok("danger"), tok("surface-input"), tint)
                with self.subTest(theme=theme_name, tint=tint):
                    self.assertGreaterEqual(contrast(tok("danger"), chip), NON_TEXT)

    def test_star_is_its_own_gold_not_the_accent_or_the_voice_colour(self):
        for theme_name, tok in self.each_theme():
            with self.subTest(theme=theme_name):
                self.assertNotIn(tok("star"), (tok("accent"), tok("voice"), VOICE_FILL))
                gap = abs(_hue(tok("star")) - _hue(tok("voice")))
                self.assertGreaterEqual(gap, MIN_STAR_VOICE_HUE_GAP)

    def test_active_view_segment_stands_out_from_the_track(self):
        for theme_name, tok in self.each_theme():
            with self.subTest(theme=theme_name):
                self.assertGreaterEqual(contrast(tok("accent"), tok("surface-1")), NON_TEXT)
                self.assertGreaterEqual(contrast(tok("surface-2"), tok("accent")), TEXT_AA)


class TokenReaderTests(unittest.TestCase):
    def test_reads_per_theme_values(self):
        self.assertNotEqual(theme.token("light", "surface-1"), theme.token("dark", "surface-1"))

    def test_unknown_theme_falls_back_to_light(self):
        self.assertEqual(theme.token("neon", "bg"), theme.token("light", "bg"))

    def test_unknown_token_raises(self):
        with self.assertRaises(KeyError):
            theme.token("light", "no-such-token")

    def test_colorref_is_bgr(self):
        self.assertEqual(_colorref("#102030").value, 0x302010)


if __name__ == "__main__":
    unittest.main()
