"""Home's first paint must not claim "no files yet", and a slow load shows a
placeholder shaped like what is coming.

The list arrives from the backend after the page has opened. Until then the page
used to say "no files yet" with a blank area, which read as missing cards, buttons
and icons; then it drew three grid cards whatever the view, so a list-view reader
saw cards turn into a table. The placeholder is now chosen by script from the view
and file count the page server writes into `<html>` (no bridge call), and only
appears if the wait passes 150ms (frontend/pages/library-loading.js). These tests
pin the markup the reader sees first (frontend/index.html) and the wiring that
replaces it (frontend/pages/home.js), so neither the false default nor the wrong
shape can come back.
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
HOME_JS = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
HOME_CSS = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")
LOADING_JS = (FRONTEND / "pages" / "library-loading.js").read_text(encoding="utf-8")


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

    def test_no_placeholder_is_drawn_into_the_markup(self):
        # Which placeholder (cards or table rows) and how many depend on the
        # reader's view and list, so script chooses it; a fixed one in the
        # markup was cards even in list view.
        area = _by_id(self.page, "recentArea")
        self.assertFalse([el for el in area.walk() if "skeleton" in el.classes])

    def test_the_placeholder_layer_is_decorative_and_out_of_reach(self):
        layer = _by_id(self.page, "librarySkeleton")
        self.assertEqual(layer.attrs.get("aria-hidden"), "true")
        self.assertIn("inert", layer.attrs)
        self.assertIn("hidden", layer.attrs)

    def test_the_page_declares_the_state_the_server_fills_in(self):
        html = next(el for el in self.page.walk() if el.tag == "html")
        self.assertEqual(html.attrs.get("data-recent-view"), "grid")
        self.assertEqual(html.attrs.get("data-recent-count"), "")
        self.assertEqual(html.attrs.get("data-favorites-count"), "")

    def test_screen_readers_are_told_the_list_is_loading(self):
        area = _by_id(self.page, "recentArea")
        notes = [el for el in area.walk() if "sr-only" in el.classes]
        self.assertTrue(any("loading" in el.text.lower() for el in notes))

    def test_header_controls_start_hidden_until_there_is_something_to_act_on(self):
        for element_id in ("headerSearch", "viewToggle", "openPdfBtn"):
            self.assertIn("is-absent", _by_id(self.page, element_id).classes, element_id)


class HomeLoadingWiringTest(unittest.TestCase):
    def test_placeholders_use_the_shared_skeleton(self):
        self.assertRegex(LOADING_JS, r"[\"'`]skeleton ")

    def test_placeholder_card_has_its_own_non_interactive_style(self):
        rule = re.search(r"\.recent-card--skeleton\s*\{([^}]*)\}", HOME_CSS)
        self.assertIsNotNone(rule)
        self.assertIn("cursor: default", rule.group(1))

    def test_the_view_is_read_from_the_page_not_asked_of_the_backend(self):
        self.assertIn("document.documentElement.dataset.recentView", HOME_JS)
        self.assertNotIn('callApi("get_recent_view")', HOME_JS)

    def test_the_placeholder_is_armed_as_the_script_loads(self):
        # Before init() awaits anything, so the 150ms clock starts at first paint.
        arm = HOME_JS.index("\narmListSkeleton();")
        self.assertLess(arm, HOME_JS.index("(async function init()"))

    def test_the_timings_and_counts_are_the_agreed_ones(self):
        for constant, value in (
            ("SKELETON_DELAY_MS", "150"),
            ("SKELETON_MIN_VISIBLE_MS", "300"),
            ("SKELETON_FALLBACK_COUNT", "6"),
            ("SKELETON_MAX_COUNT", "20"),
            ("THUMBNAIL_BATCH_SIZE", "4"),
        ):
            with self.subTest(constant=constant):
                self.assertRegex(LOADING_JS, rf"const {constant} = {value};")

    def test_both_views_build_their_placeholder_in_one_place(self):
        for builder in ("buildGridSkeleton", "buildListSkeleton"):
            with self.subTest(builder=builder):
                self.assertIn(f"function {builder}(", LOADING_JS)
                self.assertNotIn(builder, INDEX_HTML)

    def test_rows_wait_for_no_thumbnail(self):
        for name in ("recent-card.js", "recent-list.js"):
            with self.subTest(file=name):
                source = (FRONTEND / "pages" / name).read_text(encoding="utf-8")
                self.assertIn("mountThumbnail(", source)
        self.assertIn('callApi("get_thumbnails"', LOADING_JS)

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
