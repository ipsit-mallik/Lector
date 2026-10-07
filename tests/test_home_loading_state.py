"""Home's first paint must not claim "no files yet" or look empty while the list
is still loading.

The list arrives from the backend after the page has opened (it renders each PDF's
first-page thumbnail, a couple of seconds for a long file). Until then the page
used to say "no files yet" with a blank area, which read as missing cards, buttons
and icons. These tests pin the markup the reader sees first (frontend/index.html)
and the wiring that replaces it (frontend/pages/home.js), so the false default and
the blank wait cannot come back.
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
HOME_JS = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
HOME_CSS = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")


class _Element:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.classes = set((self.attrs.get("class") or "").split())
        self.children = []
        self.text = ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class _Tree(HTMLParser):
    VOID = {"meta", "link", "input", "br", "img", "hr"}

    def __init__(self):
        super().__init__()
        self.root = _Element("root", [])
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        el = _Element(tag, attrs)
        self._stack[-1].children.append(el)
        if tag not in self.VOID:
            self._stack.append(el)

    def handle_endtag(self, tag):
        if len(self._stack) > 1 and self._stack[-1].tag == tag:
            self._stack.pop()

    def handle_data(self, data):
        self._stack[-1].text += data


def _page():
    tree = _Tree()
    tree.feed(INDEX_HTML)
    return tree.root


def _by_id(root, element_id):
    return next(el for el in root.walk() if el.attrs.get("id") == element_id)


class HomeFirstPaintTest(unittest.TestCase):
    def setUp(self):
        self.page = _page()

    def test_count_does_not_claim_no_files_before_the_list_has_loaded(self):
        count = _by_id(self.page, "recentCount")
        self.assertEqual(count.text.strip(), "")

    def test_count_stays_a_polite_live_region_so_the_loaded_count_is_announced(self):
        count = _by_id(self.page, "recentCount")
        self.assertEqual(count.attrs.get("role"), "status")

    def test_list_area_is_marked_busy_until_loaded(self):
        area = _by_id(self.page, "recentArea")
        self.assertEqual(area.attrs.get("aria-busy"), "true")

    def test_list_area_shows_placeholder_cards_on_first_paint(self):
        area = _by_id(self.page, "recentArea")
        cards = [el for el in area.walk() if "recent-card--skeleton" in el.classes]
        self.assertGreaterEqual(len(cards), 3)

    def test_placeholder_cards_are_decorative_for_screen_readers(self):
        area = _by_id(self.page, "recentArea")
        grid = next(el for el in area.walk() if "recent-grid" in el.classes)
        self.assertEqual(grid.attrs.get("aria-hidden"), "true")

    def test_screen_readers_are_told_the_list_is_loading(self):
        area = _by_id(self.page, "recentArea")
        notes = [el for el in area.walk() if "sr-only" in el.classes]
        self.assertTrue(any("loading" in el.text.lower() for el in notes))

    def test_header_controls_start_hidden_until_there_is_something_to_act_on(self):
        for element_id in ("headerSearch", "viewToggle", "openPdfBtn"):
            self.assertIn("is-absent", _by_id(self.page, element_id).classes, element_id)


class HomeLoadingWiringTest(unittest.TestCase):
    def test_placeholder_cards_use_the_shared_skeleton(self):
        self.assertRegex(INDEX_HTML, r'class="[^"]*\bskeleton\b[^"]*"')

    def test_placeholder_card_has_its_own_non_interactive_style(self):
        rule = re.search(r"\.recent-card--skeleton\s*\{([^}]*)\}", HOME_CSS)
        self.assertIsNotNone(rule)
        self.assertIn("cursor: default", rule.group(1))

    def test_a_finished_render_clears_the_busy_state(self):
        self.assertRegex(HOME_JS, r"recentArea\.removeAttribute\(\"aria-busy\"\)")

    def test_a_failed_load_does_not_leave_the_placeholders_shimmering_forever(self):
        load = re.search(r"async function loadSection\(\)\s*\{(.*?)\n\}", HOME_JS, re.S)
        self.assertIsNotNone(load)
        self.assertIn("catch", load.group(1))
        self.assertIn("buildLoadFailedState", HOME_JS)

    def test_a_failed_load_offers_a_way_to_try_again(self):
        failed = re.search(r"function buildLoadFailedState\(\)\s*\{(.*?)\n\}", HOME_JS, re.S)
        self.assertIsNotNone(failed)
        self.assertIn("loadSection", failed.group(1))


if __name__ == "__main__":
    unittest.main()
