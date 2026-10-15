"""Overlay scrollbars live in exactly one place (docs/DESIGN_SYSTEM.md,
"Scrollbars").

Styling is frontend/shared/scrollbars.css and the overlay and activity logic
is frontend/js/scrollbars.js. These tests are the guard that keeps it that
way: a second copy of scrollbar CSS anywhere else — say a `scrollbar-color`
that brings a native bar back, or a rule restyling the overlay's rail — fails
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

# Anything that styles or toggles a scrollbar, native or overlay. `themed-scroll`
# is the retired per-element opt-in class: scrollbars are styled globally, so a
# leftover use would be dead markup that suggests the old way still applies.
SCROLLBAR_STYLING = re.compile(
    r"::-webkit-scrollbar|scrollbar-(?:color|width|gutter|rail|thumb|size|hit|inset)"
    r"|themed-scroll|scrollbars-active"
)


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
        # The whole selector of a rule, not the tail of a longer one.
        match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(match, f"no rule for {selector}")
        return match.group(1)

    def test_sizes_match_the_windows_11_overlay_scrollbar(self):
        # Thin at rest, thicker when pointed at or dragged, a constant grab
        # area, held off the edge, short of both ends, never a sliver.
        for name, value in (
            ("thin", "3px"),
            ("thick", "7px"),
            ("hit", "12px"),
            ("edge", "3px"),
            ("inset", "8px"),
            ("min-thumb", "32px"),
        ):
            with self.subTest(token=name):
                self.assertRegex(self.css, rf"--scrollbar-{name}:\s*{value}\s*;")

    def test_native_scrollbars_are_switched_off(self):
        # Chromium honours the standard property, WebKit (the Mac build) the
        # pseudo-element. Neither leaves a gutter, so nothing reflows.
        self.assertRegex(self.rule("*"), r"scrollbar-width:\s*none")
        self.assertRegex(self.rule("::-webkit-scrollbar"), r"display:\s*none")
        self.assertNotRegex(self.css, r"scrollbar-color|scrollbar-gutter")
        self.assertNotRegex(self.css, r"overflow\s*:\s*hidden")

    def test_the_rail_is_an_overlay_that_takes_no_layout_space(self):
        rail = self.rule(".scrollbar-rail")
        self.assertRegex(rail, r"position:\s*absolute")
        self.assertRegex(rail, r"display:\s*none")
        self.assertRegex(self.rule(".scrollbar-rail.is-scrollable"), r"display:\s*block")
        self.assertRegex(self.rule(".scrollbar-rail--y"), r"width:\s*var\(--scrollbar-hit\)")
        self.assertRegex(self.rule(".scrollbar-rail--x"), r"height:\s*var\(--scrollbar-hit\)")

    def test_the_wheel_over_a_scrollbar_is_the_scrollers_native_scroll(self):
        # The rail lets the wheel fall through to the scroller, and while it is
        # pointed at the scroller's contents step aside so the scroller itself
        # takes it. A wheel handed on by script lagged and lost the smoothing.
        self.assertRegex(self.rule(".scrollbar-rail"), r"pointer-events:\s*none")
        self.assertRegex(self.rule("[data-scrollbar-hover] *"), r"pointer-events:\s*none")
        self.assertRegex(self.rule("[data-scrollbar-hover]"), r"cursor:\s*default")
        script = _strip_comments(SHARED_JS.read_text(encoding="utf-8"))
        self.assertNotRegex(script, r"""addEventListener\(\s*["']wheel["']""")
        self.assertNotRegex(self.css, r"\.scrollbar-rail[^{]*:hover")

    def test_the_thumb_rests_thin_and_off_the_edge(self):
        self.assertRegex(
            self.rule(".scrollbar-rail--y .scrollbar-thumb"),
            r"right:\s*var\(--scrollbar-edge\).*width:\s*var\(--scrollbar-thin\)",
        )
        self.assertRegex(
            self.rule(".scrollbar-rail--x .scrollbar-thumb"),
            r"bottom:\s*var\(--scrollbar-edge\).*height:\s*var\(--scrollbar-thin\)",
        )

    def test_idle_thumb_is_invisible_and_activity_shows_it(self):
        thumb = self.rule(".scrollbar-thumb")
        self.assertRegex(thumb, r"opacity:\s*0\s*;")
        self.assertRegex(thumb, r"background-color:\s*var\(--scrollbar-thumb\)")
        self.assertRegex(
            self.css,
            r":root\.scrollbars-active \.scrollbar-thumb,\s*"
            r"\.scrollbar-rail\.is-hovered \.scrollbar-thumb,\s*"
            r"\.scrollbar-rail\.is-dragging \.scrollbar-thumb\s*\{\s*opacity:\s*1",
        )

    def test_pointed_at_or_dragged_it_thickens_and_strengthens(self):
        self.assertRegex(
            self.css,
            r"\.scrollbar-rail\.is-hovered \.scrollbar-thumb,\s*\.scrollbar-rail\.is-dragging \.scrollbar-thumb\s*"
            r"\{\s*background-color:\s*var\(--scrollbar-thumb-hover\)",
        )
        self.assertRegex(
            self.css,
            r"\.scrollbar-rail--y\.is-dragging \.scrollbar-thumb\s*\{\s*width:\s*var\(--scrollbar-thick\)",
        )
        self.assertRegex(
            self.css,
            r"\.scrollbar-rail--x\.is-dragging \.scrollbar-thumb\s*\{\s*height:\s*var\(--scrollbar-thick\)",
        )

    def test_it_fades_over_dur_base_and_thickens_over_dur_fast(self):
        transition = self.rule(".scrollbar-thumb")
        self.assertRegex(transition, r"opacity var\(--dur-base\)")
        self.assertRegex(transition, r"width var\(--dur-fast\)")
        self.assertRegex(transition, r"height var\(--dur-fast\)")

    def test_reduced_motion_removes_the_fade_and_the_growth(self):
        block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1), r"\.scrollbar-thumb[^{]*\{\s*transition:\s*none")

    def test_forced_colors_never_hides_the_thumb(self):
        block = re.search(r"@media \(forced-colors: active\)\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1), r"background-color:\s*CanvasText")
        self.assertRegex(block.group(1), r"opacity:\s*1")

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
