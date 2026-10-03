"""The Open/Save-As dialogs' directory tree follows the active folder.

Two halves, both pinned here:

* the scroll arithmetic (`treeScrollTarget` in frontend/js/file-browser.js), a
  pure function, run under Node so the centre-on-open and minimum-movement rules
  are checked exactly. Skipped when Node is not installed;
* the layout the behaviour depends on: only the tree scrolls, Quick Access is
  pinned outside it, and nothing uses `scrollIntoView`, which can scroll the
  modal or the page as well as the tree.

How the pieces behave together in a browser (lazy children, expand animation,
every way of changing folder) is verified by screenshots, as docs/TASKS.md's
verify-before-checkoff rule requires.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
BROWSER_JS = FRONTEND / "js" / "file-browser.js"
BROWSER_CSS = FRONTEND / "shared" / "file-browser.css"
DIALOG_PAGES = (FRONTEND / "index.html", FRONTEND / "pages" / "reading.html")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(?<![:\"'])//.*$", "", text)


HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({});
vm.runInContext(
  source + "\nthis.treeScrollTarget = treeScrollTarget; this.PAD = TREE_SCROLL_EDGE_PAD;",
  context,
);
const { treeScrollTarget, PAD } = context;

const view = (over) => ({
  nodeTop: 0, nodeHeight: 36, viewTop: 0, viewHeight: 300, contentHeight: 2000,
  center: false, ...over,
});
const target = (over) => treeScrollTarget(view(over));

const results = {};
const check = (name, fn) => {
  try {
    results[name] = fn() === true;
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};

check("pad is a small positive number", () => PAD > 0 && PAD <= 16);

// Centre (used when the dialog opens).
check("centre puts the node in the middle of the pane", () =>
  target({ center: true, nodeTop: 1000 }) === 1000 - (300 - 36) / 2);
check("centre clamps at the top", () => target({ center: true, nodeTop: 20 }) === 0);
check("centre clamps at the bottom", () =>
  target({ center: true, nodeTop: 1950 }) === 2000 - 300);
check("centre is returned even if already centred (open always positions)", () =>
  target({ center: true, nodeTop: 132, viewTop: 0 }) === 0);
check("centre with content shorter than the pane is 0", () =>
  target({ center: true, nodeTop: 100, contentHeight: 250 }) === 0);

// Minimum movement (used on later folder changes).
check("fully visible node: no scroll", () => target({ nodeTop: 100, viewTop: 0 }) === null);
check("node flush with the top edge counts as visible", () =>
  target({ nodeTop: 500, viewTop: 500 }) === null);
check("node flush with the bottom edge counts as visible", () =>
  target({ nodeTop: 764, viewTop: 500 }) === null);
check("node above the view: align its top, leaving the pad", () =>
  target({ nodeTop: 450, viewTop: 500 }) === 450 - PAD);
check("node below the view: align its bottom, leaving the pad", () =>
  target({ nodeTop: 400, viewTop: 0 }) === 400 + 36 - 300 + PAD);
check("node below the view clamps at the end of the content", () =>
  target({ nodeTop: 1990, viewTop: 1500, contentHeight: 2020 }) === 2020 - 300);
check("node above the view clamps at 0", () =>
  target({ nodeTop: 3, viewTop: 500 }) === 0);
check("movement is the minimum: below-view scroll is never more than needed", () => {
  const next = target({ nodeTop: 400, viewTop: 0 });
  return next - 0 === 400 + 36 + PAD - 300;
});

// Tiny panes (short window): the pad shrinks instead of pushing the node out.
check("a pane barely taller than a row still shows the whole row", () => {
  const next = treeScrollTarget(view({ viewHeight: 40, nodeTop: 400, viewTop: 0 }));
  // pad is limited to (40 - 36) / 2 = 2, so the node bottom lands 2px inside.
  return next === 400 + 36 - 40 + 2;
});
check("a pane shorter than a row aligns the node's top", () => {
  const next = treeScrollTarget(view({ viewHeight: 30, nodeTop: 400, viewTop: 0 }));
  return next === 400;
});

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class TreeScrollTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(BROWSER_JS)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 16)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


class TreeFollowsActiveFolderWiringTests(unittest.TestCase):
    def setUp(self):
        self.js = _strip_comments(BROWSER_JS.read_text(encoding="utf-8"))

    def test_scroll_is_computed_against_the_tree_not_with_scroll_into_view(self):
        # scrollIntoView scrolls every scrollable ancestor — the modal and the
        # page too — and ignores the pane-relative maths this feature needs.
        self.assertNotIn("scrollIntoView", self.js)

    def test_only_the_trees_own_container_is_scrolled(self):
        self.assertRegex(self.js, r"treeEl\.scrollTo\(")
        self.assertNotRegex(self.js, r"(listEl|scrim|document\.(body|documentElement))\.scrollTo")

    def test_every_folder_change_goes_through_navigate_to_which_follows_the_tree(self):
        body = re.search(r"async function navigateTo\(.*?\n  }\n", self.js, re.S)
        self.assertIsNotNone(body)
        self.assertIn("scrollTreeToCurrent", body.group(0))

    def test_opening_the_dialog_centres_the_active_folder(self):
        opener = re.search(r"function open\(.*?\n  }\n", self.js, re.S)
        self.assertIsNotNone(opener)
        self.assertRegex(opener.group(0), r"navigateTo\(dir,\s*\{\s*initial:\s*true\s*\}\)")

    def test_it_waits_for_whatever_layout_transitions_are_running_not_a_timer(self):
        # The tree's height changes while a grid-rows expand/collapse transition
        # runs, and any earlier navigation's may still be going: measure a row
        # mid-transition and the scroll lands in the wrong place. Waiting on the
        # tree's actual running transitions covers every cause; a fixed delay
        # (or waiting only on this navigation's own expand) does not.
        self.assertIn("getAnimations", self.js)
        self.assertIn("grid-template-rows", self.js)
        self.assertNotIn("TREE_EXPAND_SETTLE_FALLBACK_MS", self.js)
        body = re.search(r"async function scrollTreeToCurrent\(.*?\n  }\n", self.js, re.S)
        self.assertIsNotNone(body)
        self.assertIn("layoutTransitionsSettled", body.group(0))
        self.assertLess(
            body.group(0).index("layoutTransitionsSettled"),
            body.group(0).index("offsetWithinTree"),
            "must wait for the layout to settle before measuring",
        )

    def test_visibility_is_judged_against_an_in_flight_scrolls_destination(self):
        # A row momentarily in view as a running smooth scroll passes it would
        # count as "already visible", and the scroll would carry on past it.
        body = re.search(r"async function scrollTreeToCurrent\(.*?\n  }\n", self.js, re.S)
        self.assertIsNotNone(body)
        self.assertRegex(body.group(0), r"viewTop:\s*pendingTreeScrollTop\s*\?\?\s*treeEl\.scrollTop")
        self.assertRegex(self.js, r"addEventListener\(\"scrollend\"")

    def test_reduced_motion_is_respected_for_the_scroll_animation(self):
        self.assertIn("prefers-reduced-motion", self.js)


class TreeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.css = _strip_comments(BROWSER_CSS.read_text(encoding="utf-8"))

    def rule(self, selector: str) -> str:
        match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(match, f"no rule for {selector}")
        return match.group(1)

    def test_the_sidebar_itself_does_not_scroll(self):
        body = self.rule(".browser-side")
        self.assertNotRegex(body, r"overflow(-y)?\s*:\s*(auto|scroll)")
        self.assertRegex(body, r"overflow\s*:\s*hidden")
        self.assertRegex(body, r"flex-direction\s*:\s*column")

    def test_the_tree_is_the_one_scroll_container(self):
        body = self.rule(".browser-tree")
        self.assertRegex(body, r"overflow-y\s*:\s*auto")
        self.assertRegex(body, r"flex\s*:\s*1")
        self.assertRegex(body, r"min-height\s*:\s*0")

    def test_the_tree_is_positioned_so_offsets_are_measured_against_it(self):
        # Row offsets are summed up the offsetParent chain until the tree, so it
        # has to be a positioned ancestor. Layout offsets (unlike
        # getBoundingClientRect) are unaffected by the dialog's transform.
        self.assertRegex(self.rule(".browser-tree"), r"position\s*:\s*relative")

    def test_quick_access_cannot_shrink_away_inside_the_pinned_area(self):
        self.assertRegex(self.rule(".browser-quick"), r"flex\s*:\s*none")

    def test_short_windows_get_a_compact_quick_access_so_the_tree_keeps_room(self):
        block = re.search(r"@media \(max-height:\s*\d+px\)\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(block, "no short-window media block")
        self.assertIn(".browser-quick", block.group(1))
        self.assertIn("grid-template-columns", block.group(1))


class DialogMarkupTests(unittest.TestCase):
    def test_quick_access_is_a_sibling_of_the_tree_not_inside_its_scroll_area(self):
        for page in DIALOG_PAGES:
            html = page.read_text(encoding="utf-8")
            side = re.findall(r'<div class="browser-side">(.*?)</div>\s*<div class="browser-main">', html, re.S)
            self.assertEqual(len(side), 1, page.name)
            with self.subTest(page=page.name):
                self.assertRegex(side[0], r'<div class="browser-quick" id="\w+"></div>')
                # The tree element is empty markup: nothing (Quick Access
                # included) is nested inside the element that scrolls.
                self.assertRegex(side[0], r'<div class="browser-tree" id="\w+" role="tree"></div>')


if __name__ == "__main__":
    unittest.main()
