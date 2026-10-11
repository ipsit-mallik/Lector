"""The surface elevation scale (docs/DESIGN_SYSTEM.md, "Elevation").

Dark mode separates surfaces by lightness, not shadow: the page, then the
chrome (sidebar), then dialogs, then cards, then a hovered card, each a clear
step lighter than the one below. Every card picks this up from theme.css
tokens; no stylesheet carries a dark-only override of its own. Sepia keeps at
least the page/card separation light has.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT / "src"))

from lector.shared import theme  # noqa: E402
from test_theme_tokens import NON_TEXT, TEXT_AA, _blend, _luminance, contrast  # noqa: E402

THEME_CSS = (FRONTEND / "shared" / "theme.css").read_text(encoding="utf-8")
COMPONENTS_CSS = (FRONTEND / "shared" / "components.css").read_text(encoding="utf-8")
HOME_CSS = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")
SETTINGS_CSS = (FRONTEND / "pages" / "settings.css").read_text(encoding="utf-8")
SETTINGS_HTML = (FRONTEND / "pages" / "settings.html").read_text(encoding="utf-8")

# A hovered card in dark is --surface-hover: the card mixed this much toward
# the text colour (theme.css). Keep in step with it.
HOVER_MIX = 0.06
# Page to card, in CIELAB L*. Dark has no shadow to help, so it needs a wide
# step; light's is what sepia has to match.
MIN_DARK_PAGE_TO_CARD = 10.0
MIN_DARK_STEP = 3.0
# Dark's selected fill must sit clearly apart from the hover step.
MIN_SELECTED_FROM_HOVER = 2.0


def l_star(hex_color: str) -> float:
    y = _luminance(hex_color)
    f = y ** (1 / 3) if y > 0.008856 else 7.787 * y + 16 / 116
    return 116 * f - 16


def tok(theme_name: str, name: str) -> str:
    return theme.token(theme_name, name)


def card_hover(theme_name: str) -> str:
    return _blend(tok(theme_name, "text-primary"), tok(theme_name, "surface-input"), HOVER_MIX)


def theme_block(theme_name: str) -> str:
    start = THEME_CSS.index(f'[data-theme="{theme_name}"]')
    return THEME_CSS[start:THEME_CSS.index("}", start)]


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


class DarkScaleTests(unittest.TestCase):
    def test_higher_surfaces_are_lighter_by_a_clear_step(self):
        ladder = [tok("dark", name) for name in ("surface-page", "surface-1", "surface-2", "surface-input")]
        ladder.append(card_hover("dark"))
        lightness = [l_star(c) for c in ladder]
        for lower, higher in zip(lightness, lightness[1:]):
            self.assertGreaterEqual(higher - lower, MIN_DARK_STEP, lightness)
        self.assertGreaterEqual(lightness[3] - lightness[0], MIN_DARK_PAGE_TO_CARD)
        self.assertLessEqual(l_star(tok("dark", "bg")), lightness[0])

    def test_card_edges_are_translucent_white_and_brighten_on_hover(self):
        block = theme_block("dark")
        self.assertIn("--border-elevated: rgba(255, 255, 255, 0.06);", block)
        self.assertIn("--card-hover-border: rgba(255, 255, 255, 0.10);", block)
        self.assertIn("--card-hover-fill: var(--surface-hover);", block)
        self.assertRegex(block, r"--card-highlight: inset 0 1px 0 rgba\(255, 255, 255, 0\.0\d\);")
        self.assertIn("--dialog-edge: 0 0 0 1px var(--border-elevated);", block)

    def test_selected_fill_stays_apart_from_the_hover_step(self):
        tint = float(re.search(r"--selected-tint: (\d+)%;", theme_block("dark")).group(1)) / 100
        selected = _blend(tok("dark", "accent"), tok("dark", "surface-input"), tint)
        self.assertGreaterEqual(abs(l_star(selected) - l_star(card_hover("dark"))), MIN_SELECTED_FROM_HOVER)
        # The subtitle on a selected card is --text-secondary (components.css).
        self.assertGreaterEqual(contrast(tok("dark", "text-secondary"), selected), TEXT_AA)

    def test_thumbnails_are_dimmed_in_dark_only_and_never_inverted(self):
        self.assertIn("--thumb-filter: brightness(0.9);", theme_block("dark"))
        for theme_name in ("light", "sepia"):
            self.assertIn("--thumb-filter: none;", theme_block(theme_name))
        self.assertNotIn("invert(", THEME_CSS)
        # The slot's paper around a page that doesn't fill it dims to match.
        self.assertIn("--thumb-paper: color-mix(in srgb, var(--paper) 90%, #000000);", theme_block("dark"))
        for selector in (r"\.recent-thumb", r"\.recent-table-thumb"):
            rule = re.search(r"(?m)^" + selector + r"\s*\{([^}]*)\}", HOME_CSS)
            self.assertIn("background-color: var(--thumb-paper)", rule.group(1))
        for selector in (r"\.recent-thumb img", r"\.recent-table-thumb img"):
            rule = re.search(selector + r"\s*\{([^}]*)\}", HOME_CSS)
            self.assertIsNotNone(rule, selector)
            self.assertIn("filter: var(--thumb-filter)", rule.group(1))


class SeparationTests(unittest.TestCase):
    def test_sepia_separates_page_and_card_at_least_as_clearly_as_light(self):
        def gap(theme_name):
            return l_star(tok(theme_name, "surface-input")) - l_star(tok(theme_name, "surface-page"))
        self.assertGreaterEqual(round(gap("sepia"), 1), round(gap("light"), 1))

    def test_light_and_sepia_page_is_their_old_content_surface(self):
        for theme_name in ("light", "sepia"):
            self.assertEqual(tok(theme_name, "surface-page"), tok(theme_name, "surface-2"))


class ContrastOnNewSurfacesTests(unittest.TestCase):
    def test_text_reads_on_the_page_on_cards_and_on_a_hovered_card(self):
        for theme_name in theme.THEME_ORDER:
            surfaces = {
                "surface-page": tok(theme_name, "surface-page"),
                "surface-input": tok(theme_name, "surface-input"),
                "card-hover": card_hover(theme_name),
            }
            for fg, minimum in (("text-primary", TEXT_AA), ("text-secondary", TEXT_AA),
                                ("text-muted", TEXT_AA), ("text-tertiary", NON_TEXT), ("voice", NON_TEXT)):
                for surface, colour in surfaces.items():
                    with self.subTest(theme=theme_name, fg=fg, bg=surface):
                        self.assertGreaterEqual(contrast(tok(theme_name, fg), colour), minimum)

    def test_destructive_text_reads_on_a_card(self):
        for theme_name in theme.THEME_ORDER:
            with self.subTest(theme=theme_name):
                self.assertGreaterEqual(contrast(tok(theme_name, "danger"), tok(theme_name, "surface-input")), TEXT_AA)


class TokensNotOverridesTests(unittest.TestCase):
    def test_no_stylesheet_but_theme_css_names_a_theme(self):
        for path in FRONTEND.rglob("*.css"):
            if path.name == "theme.css":
                continue
            with self.subTest(path.name):
                self.assertNotIn("data-theme=", strip_comments(path.read_text(encoding="utf-8")))

    def test_the_page_is_its_own_surface(self):
        self.assertRegex(HOME_CSS, r"\.content \{[^}]*background-color: var\(--surface-page\)")

    def test_every_card_takes_the_card_edge_and_highlight(self):
        family = re.search(r"\.card,\s*\.option-row,\s*\.theme-card\s*\{([^}]*)\}", COMPONENTS_CSS).group(1)
        self.assertIn("border: 1px solid var(--border-elevated)", family)
        self.assertIn("box-shadow: var(--card-highlight)", family)
        for selector in (r"\.recent-card", r"\.recent-table", r"\.empty-state"):
            rule = re.search(r"(?m)^" + selector + r"\s*\{([^}]*)\}", HOME_CSS).group(1)
            with self.subTest(selector):
                self.assertIn("border: 1px solid var(--border-elevated)", rule)
                self.assertIn("var(--card-highlight)", rule)
        dialog = re.search(r"\.dialog-card\s*\{(.*?)\n\}", COMPONENTS_CSS, re.S).group(1)
        self.assertIn("box-shadow: var(--card-highlight), var(--dialog-edge), var(--shadow-3)", strip_comments(dialog))


class SettingsReadOnlyChipTests(unittest.TestCase):
    def test_no_fixed_for_now_labels(self):
        self.assertNotIn("fixed for now", SETTINGS_HTML)
        self.assertNotIn("voice-card-note", SETTINGS_HTML + SETTINGS_CSS)

    def test_key_and_phrase_are_read_only_chips_with_no_hover(self):
        # Space, and the wake phrase (filled in by settings.js).
        self.assertEqual(SETTINGS_HTML.count('class="key-chip"'), 2)
        self.assertIn('class="key-chip" id="wakePhraseLabel"', SETTINGS_HTML)
        chip = re.search(r"\.key-chip\s*\{([^}]*)\}", SETTINGS_CSS).group(1)
        self.assertIn("pointer-events: none", chip)
        self.assertNotRegex(SETTINGS_CSS, r"\.key-chip[^{]*:hover")
        # The same chip whether the card is on or off.
        self.assertNotRegex(strip_comments(SETTINGS_CSS), r"\.key-chip\s*\{\s*background-color: transparent")


if __name__ == "__main__":
    unittest.main()
