"""Design-token contrast guarantees (docs/DESIGN_SYSTEM.md, "Tokens").

The ratios here are the contract the token values were solved against: if a
future edit to frontend/shared/theme.css drops a pair below its minimum, this
fails instead of the regression shipping silently.
"""

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
