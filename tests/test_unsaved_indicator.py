"""The reader header's unsaved-changes dot (docs/DESIGN_SYSTEM.md §
"Unsaved changes"): shown from the one flag (`state.is_dirty`), labelled
"Unsaved changes" for the tooltip and assistive tech, hidden but still laid
out when there is nothing unsaved, so the name never moves, and drawn in a
theme token only.

`updateUnsavedDot` runs under Node on a stand-in element (dom_standin.py);
the markup and CSS are checked as text. The look in Light, Dark and Sepia is
checked by screenshots in the running app, not here.
"""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from dom_standin import run_node

PAGES = Path(__file__).resolve().parents[1] / "frontend" / "pages"
CHROME_JS = PAGES / "reading-chrome.js"
READING_HTML = PAGES / "reading.html"
READING_CSS = PAGES / "reading.css"


def _function_source(name: str) -> str:
    text = CHROME_JS.read_text(encoding="utf-8")
    match = re.search(rf"^function {name}\(.*?^\}}$", text, re.S | re.M)
    assert match, f"{name} not found in reading-chrome.js"
    return match.group(0)


def _css_rule(selector: str) -> str:
    text = READING_CSS.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", text, re.S | re.M)
    assert match, f"{selector} not found in reading.css"
    return match.group(1)


HARNESS = r"""
const dot = document.createElement("span");
const ctx = context({ unsavedDot: dot });
load(ctx, process.argv.slice(2));
const look = () => ({
  shown: dot.classList.contains("is-shown"),
  hidden: dot.getAttribute("aria-hidden"),
  tooltip: dot.dataset.tooltip === undefined ? null : dot.dataset.tooltip,
});
const out = {};
vm.runInContext("updateUnsavedDot(true)", ctx);
out.dirty = look();
vm.runInContext("updateUnsavedDot(false)", ctx);
out.clean = look();
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class DotBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "dot.js"
            source.write_text(_function_source("updateUnsavedDot"), encoding="utf-8")
            cls.out = run_node(HARNESS, [source])

    def test_unsaved_shows_it_with_its_tooltip(self):
        self.assertEqual(self.out["dirty"], {"shown": True, "hidden": "false", "tooltip": "Unsaved changes"})

    def test_saved_hides_it_from_sight_and_assistive_tech(self):
        self.assertEqual(self.out["clean"], {"shown": False, "hidden": "true", "tooltip": None})


class DotWiringTests(unittest.TestCase):
    def test_update_chrome_reads_the_one_flag(self):
        self.assertIn("updateUnsavedDot(Boolean(state.is_dirty));", _function_source("updateChrome"))

    def test_it_follows_the_name_with_an_accessible_label(self):
        html = READING_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            html,
            r'<div class="doc-title" id="docTitle"></div>\s*<span class="unsaved-dot" id="unsavedDot" '
            r'role="img" aria-label="Unsaved changes"',
        )

    def test_hidden_keeps_its_room_so_the_name_never_moves(self):
        rule = _css_rule(".unsaved-dot")
        self.assertIn("visibility: hidden;", rule)
        self.assertIn("flex: none;", rule)
        self.assertNotIn("display: none", READING_CSS.read_text(encoding="utf-8").split(".unsaved-dot")[1][:800])
        self.assertIn("visibility: visible;", _css_rule(".unsaved-dot.is-shown"))

    def test_drawn_in_a_theme_token(self):
        rule = _css_rule(".unsaved-dot::before")
        self.assertIn("background: var(--text-secondary);", rule)
        self.assertNotRegex(rule, r"#[0-9A-Fa-f]{3,8}\b")


if __name__ == "__main__":
    unittest.main()
