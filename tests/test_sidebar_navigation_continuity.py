"""Moving between Home and Settings from the sidebar must look like one window.

Each page is its own HTML document, so the sidebar is rebuilt on every move. Two
things made that visible:
- The sidebar's icons and the brand mark were fetched by icons.js after the page
  started, so the first frames had blank icon slots that filled in 100-280ms later.
  They are now in the markup, so they are on the first frame.
- The highlight cut from one item to the next. The item the reader left is now
  drawn lit on the new page's first frame and both items fade across (a CSS
  animation, started by a small inline script that reads a note the old page left
  in sessionStorage).

A cross-document view transition did the second job first, but WebView2 held the
new page's first frame back by ~280ms on every move with it on (first paint
340-372ms after the click, against 56-92ms without), so no stylesheet may opt in.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
PAGES = {
    "index.html": (FRONTEND / "index.html").read_text(encoding="utf-8"),
    "settings.html": (FRONTEND / "pages" / "settings.html").read_text(encoding="utf-8"),
}
HOME_CSS = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")
THEME_CSS = (FRONTEND / "shared" / "theme.css").read_text(encoding="utf-8")
SIDEBAR_ICONS = ("logo", "recent", "all_pdfs", "favorites", "settings")


def _squash(svg):
    return re.sub(r"\s+", " ", svg).replace("> <", "><").strip()


def _icon_file(name):
    return _squash((FRONTEND / "shared" / "icons" / f"{name}.svg").read_text(encoding="utf-8"))


def _inline_icon(html, name):
    match = re.search(rf'data-icon="{name}">\s*(<svg.*?</svg>)\s*</', html, re.S)
    return _squash(match.group(1)) if match else None


class SidebarIconsAreInTheMarkupTest(unittest.TestCase):
    def test_every_sidebar_icon_is_drawn_on_the_first_frame(self):
        for page, html in PAGES.items():
            for name in SIDEBAR_ICONS:
                with self.subTest(page=page, icon=name):
                    self.assertIsNotNone(_inline_icon(html, name), "icon slot is empty in the markup")

    def test_inline_icons_are_exactly_the_icon_files(self):
        for page, html in PAGES.items():
            for name in SIDEBAR_ICONS:
                with self.subTest(page=page, icon=name):
                    self.assertEqual(_inline_icon(html, name), _icon_file(name))


def _first_external_script(html):
    return html.index("<script src=")


def _crossfade_script(html):
    match = re.search(r"<script>((?:(?!</script>).)*lector-nav-from.*?)</script>", html, re.S)
    return match


class NoCrossDocumentViewTransitionTest(unittest.TestCase):
    def test_no_stylesheet_opts_in_to_cross_document_transitions(self):
        for css in FRONTEND.rglob("*.css"):
            with self.subTest(css=css.name):
                self.assertNotIn("@view-transition", css.read_text(encoding="utf-8"))

    def test_sidebar_is_not_a_named_transition_layer(self):
        rule = re.search(r"\.sidebar\s*\{([^}]*)\}", HOME_CSS)
        self.assertIsNotNone(rule)
        self.assertNotIn("view-transition-name", rule.group(1))
        self.assertNotIn("(sidebar)", THEME_CSS)


class SidebarHighlightCrossfadeTest(unittest.TestCase):
    def test_each_page_runs_the_crossfade_before_its_first_paint(self):
        for page, html in PAGES.items():
            with self.subTest(page=page):
                match = _crossfade_script(html)
                self.assertIsNotNone(match, "no inline crossfade script")
                # After every nav item it reads, before the scripts that load late.
                self.assertGreater(match.start(), html.index('id="settingsNav"'))
                self.assertLess(match.start(), _first_external_script(html))

    def test_home_runs_it_after_the_favorites_hand_off(self):
        html = PAGES["index.html"]
        self.assertGreater(_crossfade_script(html).start(), html.index('"lector-home-section"'))

    def test_both_pages_carry_the_same_script(self):
        scripts = [_squash(_crossfade_script(html).group(1)) for html in PAGES.values()]
        self.assertEqual(scripts[0], scripts[1])

    def test_the_note_is_left_on_pagehide_and_only_honoured_when_fresh(self):
        script = _crossfade_script(PAGES["index.html"]).group(1)
        self.assertIn('addEventListener("pagehide"', script)
        self.assertIn("sessionStorage.setItem", script)
        self.assertIn("sessionStorage.removeItem", script)
        self.assertIn("Date.now()", script)
        self.assertIn("nav-leaving", script)
        self.assertIn("nav-arriving", script)

    def test_the_fade_is_an_animation_so_is_restoring_cannot_stop_it(self):
        # Settings zeroes every transition until its saved state is restored.
        for cls in ("nav-arriving", "nav-leaving"):
            with self.subTest(cls=cls):
                rule = re.search(rf"\.nav-item\.{cls}\s*\{{([^}}]*)\}}", HOME_CSS)
                self.assertIsNotNone(rule)
                self.assertRegex(rule.group(1), r"animation:\s*nav-[\w-]+\s+var\(--dur-fast\)\s+var\(--ease-out\)")

    def test_leaving_starts_lit_and_arriving_starts_unlit(self):
        leaving = re.search(r"@keyframes nav-highlight-out\s*\{\s*from\s*\{([^}]*)\}", HOME_CSS)
        arriving = re.search(r"@keyframes nav-highlight-in\s*\{\s*from\s*\{([^}]*)\}", HOME_CSS)
        self.assertIsNotNone(leaving)
        self.assertIsNotNone(arriving)
        self.assertIn("var(--accent-soft)", leaving.group(1))
        self.assertIn("var(--accent-strong)", leaving.group(1))
        self.assertIn("transparent", arriving.group(1))
        self.assertIn("var(--text-secondary)", arriving.group(1))


if __name__ == "__main__":
    unittest.main()
