"""Settings' Favorites item must land on Home already showing Favorites.

Settings and Home are separate pages, so the section travels in sessionStorage
(frontend/pages/settings.js writes it, frontend/pages/home.js reads it). Home's
HTML starts as Recent, and init() awaits several backend calls before it syncs
the chrome, so reading the hand-off inside init() let the page paint Recent (nav
highlight, title, loading cards) for a moment before it flipped to Favorites.
These tests pin that the hand-off is applied while the script loads, before
init() can await anything.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
HOME_JS = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
SETTINGS_JS = (FRONTEND / "pages" / "settings.js").read_text(encoding="utf-8")

INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")

INIT_START = HOME_JS.index("(async function init()")
BEFORE_INIT = HOME_JS[:INIT_START]
INIT_BODY = HOME_JS[INIT_START:]


class HomeSectionHandoffTest(unittest.TestCase):
    def test_hand_off_is_read_before_init_awaits_anything(self):
        self.assertRegex(BEFORE_INIT, r"sessionStorage\.getItem\(HOME_SECTION_HANDOFF_KEY\)")

    def test_chrome_is_synced_to_the_handed_off_section_before_init(self):
        handoff = re.search(
            r'getItem\(HOME_SECTION_HANDOFF_KEY\) === "favorites"\)\s*\{(.*?)\n\}',
            BEFORE_INIT,
            re.S,
        )
        self.assertIsNotNone(handoff)
        self.assertIn('homeSection = "favorites"', handoff.group(1))
        self.assertIn("syncSectionChrome()", handoff.group(1))

    def test_init_still_clears_the_hand_off_so_it_stays_one_shot(self):
        self.assertRegex(INIT_BODY, r"sessionStorage\.removeItem\(HOME_SECTION_HANDOFF_KEY\)")

    def test_init_does_not_read_the_hand_off_after_its_awaits(self):
        self.assertNotIn("sessionStorage.getItem(HOME_SECTION_HANDOFF_KEY)", INIT_BODY)

    def test_settings_writes_the_same_key_home_reads(self):
        key = re.search(r'const HOME_SECTION_HANDOFF_KEY = "([^"]+)"', HOME_JS)
        self.assertIsNotNone(key)
        self.assertIn(f'sessionStorage.setItem("{key.group(1)}", "favorites")', SETTINGS_JS)


class HomeFirstPaintSectionTest(unittest.TestCase):
    """The browser paints index.html before home.js (the last of a dozen scripts)
    has run, so a hand-off applied only in home.js still showed one frame of
    Recent, then faded the sidebar highlight across to Favorites. The markup has
    to carry it: a small inline script right after the elements it changes."""

    HANDOFF_KEY = "lector-home-section"

    # The hand-off script (the one that moves the title); the <head> one
    # that holds the Tools page's first paint is tested below.
    def _inline_script(self):
        scripts = re.findall(r"<script>(.*?)</script>", INDEX_HTML, re.S)
        return next((s for s in scripts if self.HANDOFF_KEY in s and "sectionTitle" in s), None)

    def _script_at(self):
        return INDEX_HTML.index(self._inline_script())

    def test_markup_applies_the_hand_off_itself(self):
        self.assertIsNotNone(self._inline_script())

    def test_inline_script_follows_the_elements_it_changes(self):
        at = self._script_at()
        for element_id in ("recentNav", "favoritesNav", "sectionTitle"):
            self.assertLess(INDEX_HTML.index(f'id="{element_id}"'), at, element_id)

    def test_inline_script_runs_before_any_external_script(self):
        at = self._script_at()
        self.assertLess(at, INDEX_HTML.index('<script src="js/icons.js">'))

    def test_inline_script_moves_the_highlight_and_the_title(self):
        script = self._inline_script() or ""
        for needle in ("recentNav", "favoritesNav", "sectionTitle", "aria-current", "Favorites"):
            self.assertIn(needle, script, needle)


class ToolsFirstPaintHoldTest(unittest.TestCase):
    """Arriving on the Tools page (back from Organise pages), Home draws it
    as home.js loads (drawToolsAtLoad); the first paint must wait for that,
    or an empty Tools page shows for several frames. Held by a render-blocking
    `expect` link, which is only honoured when added in <head>."""

    def test_the_hold_is_added_in_head_for_the_tools_page_only(self):
        head = INDEX_HTML[: INDEX_HTML.index("</head>")]
        script = next(s for s in re.findall(r"<script>(.*?)</script>", head, re.S) if "expect" in s)
        self.assertIn('sessionStorage.getItem("lector-home-section") === "tools"', script)
        self.assertIn('hold.rel = "expect"', script)
        self.assertIn('setAttribute("blocking", "render")', script)

    def test_back_from_organise_the_tools_page_has_no_entrance(self):
        # Its first frames would show an empty page (.page-enter starts at 0).
        head = INDEX_HTML[: INDEX_HTML.index("</head>")]
        script = next(s for s in re.findall(r"<script>(.*?)</script>", head, re.S) if "expect" in s)
        self.assertIn('sessionStorage.getItem("lector-home-focus") !== null', script)
        self.assertIn('classList.add("home-returning")', script)
        css = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")
        self.assertIn(".home-returning .page-enter > * { animation: none; }", css)

    def test_home_draws_the_tools_page_as_it_loads(self):
        home = (Path(__file__).resolve().parents[1] / "frontend" / "pages" / "home.js").read_text(encoding="utf-8")
        at_load = home.index("\ndrawToolsAtLoad();\n")
        self.assertLess(at_load, home.index("(async function init()"))
        body = home[home.index("function drawToolsAtLoad()"):at_load]
        for needle in ("renderToolsSection()", "mountIconsFromCache(recentArea)", "HOME_FOCUS_NOTE",
                       "homeScroller.scrollTop = pendingHomeScroll"):
            self.assertIn(needle, body, needle)


if __name__ == "__main__":
    unittest.main()
