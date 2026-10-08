"""Settings' Favorites item must land on Home already showing Favorites.

Settings and Home are separate pages, so the section travels in localStorage
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
        self.assertRegex(BEFORE_INIT, r"localStorage\.getItem\(HOME_SECTION_HANDOFF_KEY\)")

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
        self.assertRegex(INIT_BODY, r"localStorage\.removeItem\(HOME_SECTION_HANDOFF_KEY\)")

    def test_init_does_not_read_the_hand_off_after_its_awaits(self):
        self.assertNotIn("localStorage.getItem(HOME_SECTION_HANDOFF_KEY)", INIT_BODY)

    def test_settings_writes_the_same_key_home_reads(self):
        key = re.search(r'const HOME_SECTION_HANDOFF_KEY = "([^"]+)"', HOME_JS)
        self.assertIsNotNone(key)
        self.assertIn(f'localStorage.setItem("{key.group(1)}", "favorites")', SETTINGS_JS)


class HomeFirstPaintSectionTest(unittest.TestCase):
    """The browser paints index.html before home.js (the last of a dozen scripts)
    has run, so a hand-off applied only in home.js still showed one frame of
    Recent, then faded the sidebar highlight across to Favorites. The markup has
    to carry it: a small inline script right after the elements it changes."""

    HANDOFF_KEY = "lector-home-section"

    def _inline_script(self):
        scripts = re.findall(r"<script>(.*?)</script>", INDEX_HTML, re.S)
        return next((s for s in scripts if self.HANDOFF_KEY in s), None)

    def test_markup_applies_the_hand_off_itself(self):
        self.assertIsNotNone(self._inline_script())

    def test_inline_script_follows_the_elements_it_changes(self):
        at = INDEX_HTML.index(self.HANDOFF_KEY)
        for element_id in ("recentNav", "favoritesNav", "sectionTitle"):
            self.assertLess(INDEX_HTML.index(f'id="{element_id}"'), at, element_id)

    def test_inline_script_runs_before_any_external_script(self):
        at = INDEX_HTML.index(self.HANDOFF_KEY)
        self.assertLess(at, INDEX_HTML.index('<script src="js/icons.js">'))

    def test_inline_script_moves_the_highlight_and_the_title(self):
        script = self._inline_script() or ""
        for needle in ("recentNav", "favoritesNav", "sectionTitle", "aria-current", "Favorites"):
            self.assertIn(needle, script, needle)


if __name__ == "__main__":
    unittest.main()
