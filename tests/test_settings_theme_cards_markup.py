"""Settings' theme cards must be on the page's first frame, like the rest of it.

They used to be built by settings.js after init() had fetched every icon on the
page, waited for pywebview's bridge and asked Python for the theme twice -- the only
thing above the fold that waited on the bridge, so they appeared after everything
around them. They are now in the markup: each preview resolves its colours from its
own [data-theme] (theme.css stays the one palette), the check badge is inline, and
a small inline script marks the current theme from the <head>'s localStorage mirror.
settings.js only wires the clicks and re-marks the card from the saved setting.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SETTINGS_HTML = (FRONTEND / "pages" / "settings.html").read_text(encoding="utf-8")
SETTINGS_JS = (FRONTEND / "pages" / "settings.js").read_text(encoding="utf-8")
SETTINGS_CSS = (FRONTEND / "pages" / "settings.css").read_text(encoding="utf-8")
THEMES = (("light", "Light"), ("dark", "Dark"), ("sepia", "Sepia"))


def _squash(svg):
    return re.sub(r"\s+", " ", svg).replace("> <", "><").strip()


def _cards_markup():
    start = SETTINGS_HTML.index('id="themeCards"')
    return SETTINGS_HTML[start:SETTINGS_HTML.index("</div>", start)]


def _cards():
    return re.findall(
        r'<button[^>]*class="theme-card card-lift"[^>]*data-theme-name="(\w+)"[^>]*>(.*?)</button>',
        _cards_markup(), re.S,
    )


def _css_rule(selector):
    match = re.search(rf"(?:^|\}})\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", SETTINGS_CSS, re.M)
    return match.group(1) if match else ""


class ThemeCardsAreInTheMarkupTest(unittest.TestCase):
    def test_the_three_cards_are_written_out_in_order(self):
        self.assertEqual([name for name, _ in _cards()], [name for name, _ in THEMES])

    def test_each_card_previews_its_own_theme_and_names_it(self):
        for (name, body), (_, label) in zip(_cards(), THEMES):
            with self.subTest(theme=name):
                self.assertRegex(body, rf'class="theme-preview" data-theme="{name}"')
                self.assertIn(f'class="theme-card-footer-label">{label}<', body)

    def test_each_check_badge_is_the_check_icon_file(self):
        icon = _squash((FRONTEND / "shared" / "icons" / "check.svg").read_text(encoding="utf-8"))
        for name, body in _cards():
            with self.subTest(theme=name):
                badge = re.search(r'data-icon="check"[^>]*>\s*(<svg.*?</svg>)', body, re.S)
                self.assertIsNotNone(badge, "check badge is empty in the markup")
                self.assertEqual(_squash(badge.group(1)), icon)

    def test_no_preview_colour_is_written_into_the_markup(self):
        self.assertNotIn("style=", _cards_markup())

    def test_the_current_theme_is_marked_before_the_first_paint(self):
        script = re.search(r"<script>((?:(?!</script>).)*\.theme-card.*?)</script>", SETTINGS_HTML, re.S)
        self.assertIsNotNone(script)
        self.assertGreater(script.start(), SETTINGS_HTML.index('id="themeCards"'))
        self.assertLess(script.start(), SETTINGS_HTML.index("<script src="))
        self.assertIn("document.documentElement.dataset.theme", script.group(1))
        self.assertIn('classList.toggle("selected"', script.group(1))


class ThemePreviewColoursComeFromTheTokensTest(unittest.TestCase):
    def test_each_part_of_the_preview_reads_its_token(self):
        expected = {
            ".theme-preview": ("var(--bg)",),
            ".theme-preview-mock": ("var(--surface-2)", "var(--border)"),
            ".theme-preview-line": ("var(--border-strong)",),
            ".theme-preview-line.title": ("var(--text-primary)",),
            ".theme-preview-line.accent": ("var(--highlight)",),
        }
        for selector, tokens in expected.items():
            with self.subTest(selector=selector):
                rule = _css_rule(selector)
                for token in tokens:
                    self.assertIn(token, rule)


class SettingsScriptOnlyWiresTheCardsTest(unittest.TestCase):
    def test_settings_js_no_longer_builds_them(self):
        for gone in ("readThemeTokens", "buildThemeCard", "renderThemeCards", "THEME_LABELS"):
            with self.subTest(name=gone):
                self.assertNotIn(gone, SETTINGS_JS)

    def test_each_card_click_selects_its_theme(self):
        self.assertRegex(SETTINGS_JS, r"selectTheme\(card\.dataset\.themeName\)")

    def test_init_re_marks_the_card_from_the_saved_theme(self):
        body = SETTINGS_JS[SETTINGS_JS.index("(async function init()"):]
        self.assertRegex(body, r'const theme = await callApi\("get_theme"\);[\s\S]*?markCurrentThemeCard\(theme\)')


if __name__ == "__main__":
    unittest.main()
