"""Auto-hiding scrollbars live in exactly one place (docs/DESIGN_SYSTEM.md,
"Scrollbars").

Styling is frontend/shared/scrollbars.css and the activity logic is
frontend/js/scrollbars.js. These tests are the guard that keeps it that way:
a second copy of scrollbar CSS anywhere else — or one of the standard
`scrollbar-color` / `scrollbar-width` properties, which override the
`::-webkit-scrollbar` rules in Chromium and silently break the fade — fails
here instead of shipping.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.shared import theme  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SHARED_CSS = FRONTEND / "shared" / "scrollbars.css"
SHARED_JS = FRONTEND / "js" / "scrollbars.js"
SHARED_FILES = {SHARED_CSS, SHARED_JS}

SURFACES = ("bg", "surface-1", "surface-2")
NON_TEXT = 3.0

# Anything that styles or toggles a scrollbar. `themed-scroll` is the retired
# per-element opt-in class: scrollbars are now styled globally, so a leftover
# use would be dead markup that suggests the old way still applies.
SCROLLBAR_STYLING = re.compile(
    r"::-webkit-scrollbar|scrollbar-(?:color|width|gutter)|themed-scroll|scrollbars-active"
)
STANDARD_SCROLLBAR_PROPERTIES = re.compile(r"scrollbar-(?:color|width)\s*:")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"(?m)(?<![:\"'])//.*$", "", text)


def _frontend_files(*suffixes: str) -> list[Path]:
    return sorted(p for p in FRONTEND.rglob("*") if p.suffix in suffixes)


def _contrast(a: str, b: str) -> float:
    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _css_chain(entry: Path, seen: set[Path] | None = None) -> set[Path]:
    """`entry` plus every stylesheet it pulls in through `@import`."""
    seen = seen if seen is not None else set()
    if entry in seen or not entry.exists():
        return seen
    seen.add(entry)
    for target in re.findall(r'@import\s+"([^"]+)"', entry.read_text(encoding="utf-8")):
        _css_chain((entry.parent / target).resolve(), seen)
    return seen


def _page_stylesheets(html: Path) -> set[Path]:
    chain: set[Path] = set()
    for href in re.findall(r'<link[^>]+href="([^"]+\.css)"', html.read_text(encoding="utf-8")):
        _css_chain((html.parent / href).resolve(), chain)
    return chain


class SingleSourceTests(unittest.TestCase):
    def test_shared_module_files_exist(self):
        self.assertTrue(SHARED_CSS.is_file(), SHARED_CSS)
        self.assertTrue(SHARED_JS.is_file(), SHARED_JS)

    def test_scrollbar_styling_appears_nowhere_else(self):
        offenders = []
        for path in _frontend_files(".css", ".html", ".js"):
            if path in SHARED_FILES:
                continue
            hit = SCROLLBAR_STYLING.search(_strip_comments(path.read_text(encoding="utf-8")))
            if hit:
                offenders.append(f"{path.relative_to(FRONTEND)}: {hit.group(0)}")
        self.assertEqual(offenders, [], "scrollbar styling must live in shared/scrollbars.css only")

    def test_standard_scrollbar_properties_are_never_used(self):
        # They take precedence over ::-webkit-scrollbar in modern Chromium, which
        # would leave a permanently visible thumb that ignores the fade.
        offenders = [
            str(path.relative_to(FRONTEND))
            for path in _frontend_files(".css", ".html", ".js")
            if STANDARD_SCROLLBAR_PROPERTIES.search(_strip_comments(path.read_text(encoding="utf-8")))
        ]
        self.assertEqual(offenders, [])

    def test_every_page_loads_the_stylesheet_and_the_script(self):
        pages = sorted(FRONTEND.glob("*.html")) + sorted((FRONTEND / "pages").glob("*.html"))
        self.assertGreaterEqual(len(pages), 4)
        for page in pages:
            with self.subTest(page=page.name):
                self.assertIn(SHARED_CSS, _page_stylesheets(page))
                scripts = re.findall(r'<script[^>]+src="([^"]+)"', page.read_text(encoding="utf-8"))
                self.assertTrue(any(s.endswith("js/scrollbars.js") for s in scripts), scripts)


class StylesheetBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.css = _strip_comments(SHARED_CSS.read_text(encoding="utf-8"))

    def rule(self, selector: str) -> str:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(match, f"no rule for {selector}")
        return match.group(1)

    def test_gutter_is_reserved_even_when_the_thumb_is_hidden(self):
        # The scrollbar keeps its size; only the thumb's colour changes, so
        # layout never shifts when scrollbars appear or fade.
        body = self.rule("::-webkit-scrollbar")
        self.assertRegex(body, r"width:\s*var\(--scrollbar-size\)")
        self.assertRegex(body, r"height:\s*var\(--scrollbar-size\)")
        self.assertNotRegex(self.css, r"overflow\s*:\s*hidden")

    def test_idle_thumb_is_transparent_and_active_thumb_is_coloured(self):
        self.assertRegex(self.rule("::-webkit-scrollbar-thumb"), r"background-color:\s*transparent")
        self.assertRegex(
            self.css,
            r":root\.scrollbars-active\s+::-webkit-scrollbar-thumb[^{]*\{[^}]*background-color:\s*var\(--scrollbar-thumb\)",
        )

    def test_viewport_scrollbar_is_covered_too(self):
        self.assertIn(":root.scrollbars-active::-webkit-scrollbar-thumb", self.css)

    def test_hovered_or_dragged_thumb_stays_visible(self):
        # Written as `:root ...:hover` so its specificity ties with the
        # `:root.scrollbars-active` rule and (coming later) wins while hovered.
        self.assertRegex(
            self.css,
            r":root\s+::-webkit-scrollbar-thumb:hover[^{]*::-webkit-scrollbar-thumb:active[^{]*\{[^}]*background-color:\s*var\(--scrollbar-thumb-hover\)",
        )

    def test_reduced_motion_removes_the_fade(self):
        block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(block)
        self.assertIn("transition: none", block.group(1))

    def test_forced_colors_never_hides_the_thumb(self):
        block = re.search(r"@media \(forced-colors: active\)\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1), r"background-color:\s*CanvasText")

    def test_a_transition_fades_the_thumb(self):
        self.assertRegex(self.rule("::-webkit-scrollbar-thumb"), r"transition:\s*background-color")


class ThumbContrastTests(unittest.TestCase):
    def thumb_token(self, variable: str) -> str:
        css = _strip_comments(SHARED_CSS.read_text(encoding="utf-8"))
        match = re.search(rf"{re.escape(variable)}:\s*var\(--([a-z0-9-]+)\)", css)
        self.assertIsNotNone(match, f"{variable} must reference a theme token")
        return match.group(1)

    def test_visible_thumb_is_at_least_3_to_1_on_every_surface_in_every_theme(self):
        for variable in ("--scrollbar-thumb", "--scrollbar-thumb-hover"):
            token = self.thumb_token(variable)
            for theme_name in theme.THEME_ORDER:
                for surface in SURFACES:
                    with self.subTest(variable=variable, theme=theme_name, surface=surface):
                        ratio = _contrast(theme.token(theme_name, token), theme.token(theme_name, surface))
                        self.assertGreaterEqual(ratio, NON_TEXT)


if __name__ == "__main__":
    unittest.main()
