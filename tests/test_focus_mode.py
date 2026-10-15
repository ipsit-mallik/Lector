"""The reader's Focus mode, page centring and toolbar layout.

* Voice: "focus mode" and "exit focus mode" route in the reader only, are in
  the recognizer's vocabulary and are listed in "What can I say?".
* `frontend/pages/focus-mode.js` under Node, with a stand-in DOM: the chrome
  fades before it leaves the layout, comes back transparent and then fades
  in, keeps the page still on screen, and the floating listening pill follows
  the mic state.
* `frontend/pages/page-centering.js` under Node: the page is centred on the
  window when it fits there, and on its own area when it does not.
* The markup: zoom steps in the topbar around the percentage, undo and redo
  always present and dimmed by the undo stack rather than the unsaved flag,
  and Focus mode remembering nothing.

The Node tests are skipped when Node is not installed.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector.features.voice import command_grammar as grammar  # noqa: E402
from lector.features.voice import reference, router  # noqa: E402

PAGES = ROOT / "frontend" / "pages"
FOCUS_JS = PAGES / "focus-mode.js"
CENTERING_JS = PAGES / "page-centering.js"
READING_JS = PAGES / "reading.js"
READING_HTML = PAGES / "reading.html"
READING_CSS = PAGES / "reading.css"
NODE = shutil.which("node")


class FocusVoiceCommandTests(unittest.TestCase):
    PHRASES = {
        "focus mode": grammar.FOCUS_MODE,
        "exit focus mode": grammar.EXIT_FOCUS_MODE,
    }

    def test_each_phrase_resolves_in_the_reader(self):
        for phrase, intent in self.PHRASES.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(router.resolve(router.READING, phrase)["command"]["intent"], intent)

    def test_on_and_off_have_one_phrasing_each(self):
        # "enter focus mode" would sit 0.75 from "exit focus mode", over the
        # threshold: opposite actions one mishearing apart.
        self.assertEqual(grammar.PHRASES[grammar.FOCUS_MODE], ("focus mode",))
        self.assertEqual(grammar.PHRASES[grammar.EXIT_FOCUS_MODE], ("exit focus mode",))

    def test_the_words_are_in_the_readers_vocabulary(self):
        self.assertLessEqual({"focus", "mode", "exit"}, set(router.vocabulary_for(router.READING)))

    def test_not_heard_outside_the_reader(self):
        for context in (router.HOME, router.SETTINGS):
            self.assertIsNone(router.resolve(context, "focus mode")["command"])

    def test_listed_in_what_can_i_say(self):
        section = next(s for s in reference.sections(router.READING) if s["id"] == "focus")
        listed = {p for c in section["commands"] for p in c["phrases"]}
        self.assertEqual(listed, set(self.PHRASES))
        self.assertTrue(section["available"])
        keys = " ".join(c["equivalent"] for c in section["commands"])
        self.assertIn("F11", keys)
        self.assertIn("Esc", keys)

    def test_the_reader_acts_on_both_intents(self):
        source = READING_JS.read_text(encoding="utf-8")
        self.assertIn("FOCUS_MODE: () => enterFocusMode()", source)
        self.assertIn("EXIT_FOCUS_MODE: () => setFocusMode(false)", source)


FOCUS_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function fakeElement(id) {
  const classes = new Set();
  return {
    id, textContent: "", scrollTop: 100, top: 49,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      contains: (c) => classes.has(c),
    },
    get offsetHeight() { return 0; },
    getBoundingClientRect() { return { top: this.top }; },
  };
}

const view = fakeElement("view");
const pill = fakeElement("focusVoicePill");
const label = fakeElement("focusVoiceLabel");
const scroller = fakeElement("scroller");
// The topbar (48px and its 1px divider) is above the scroller until Focus
// mode takes it out of the layout.
const origAdd = view.classList.add;
view.classList.add = (c) => { origAdd(c); if (c === "is-focus-mode") scroller.top = 0; };
const origRemove = view.classList.remove;
view.classList.remove = (c) => { origRemove(c); if (c === "is-focus-mode") scroller.top = 49; };

const document = {
  documentElement: {},
  querySelector: (sel) => (sel === ".reading-view" ? view : null),
  getElementById: (id) => ({ focusVoicePill: pill, focusVoiceLabel: label })[id],
};
const context = vm.createContext({
  document, setTimeout, clearTimeout, console,
  getComputedStyle: () => ({ getPropertyValue: () => "20ms" }),
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext("this.api = { configureFocusMode, setFocusMode, toggleFocusMode, isFocusMode, showFocusVoice };", context);
const api = context.api;
api.configureFocusMode({ scroller: () => scroller });

const out = {};
const snap = () => ({
  on: api.isFocusMode(),
  fading: view.classList.contains("is-focus-fading"),
  hidden: view.classList.contains("is-focus-mode"),
  scrollTop: scroller.scrollTop,
});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  out.start = snap();
  api.setFocusMode(true);
  out.fadingOut = snap();
  await wait(60);
  out.on = snap();
  api.setFocusMode(true);
  out.onAgain = snap();
  api.setFocusMode(false);
  out.off = snap();
  api.toggleFocusMode();
  api.toggleFocusMode();
  await wait(60);
  out.quickToggle = snap();

  api.showFocusVoice("listening", "");
  out.listening = { showing: pill.classList.contains("is-showing"), label: label.textContent };
  api.showFocusVoice("listening", "next pa");
  out.partial = label.textContent;
  api.showFocusVoice("heard", "next page");
  out.heard = { showing: pill.classList.contains("is-showing"), label: label.textContent };
  api.showFocusVoice("idle", "");
  out.idle = pill.classList.contains("is-showing");
  console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(NODE, "Node is not installed")
class FocusModeBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [NODE, "-e", FOCUS_HARNESS, str(FOCUS_JS)], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_starts_off(self):
        self.assertEqual(self.out["start"], {"on": False, "fading": False, "hidden": False, "scrollTop": 100})

    def test_the_chrome_fades_before_it_leaves_the_layout(self):
        self.assertEqual(self.out["fadingOut"], {"on": True, "fading": True, "hidden": False, "scrollTop": 100})
        self.assertTrue(self.out["on"]["hidden"])

    def test_the_page_stays_still_on_screen(self):
        # The scroller grew 49px upward, so the offset dropped by as much.
        self.assertEqual(self.out["on"]["scrollTop"], 51)
        self.assertEqual(self.out["off"]["scrollTop"], 100)

    def test_turning_on_twice_changes_nothing(self):
        self.assertEqual(self.out["onAgain"], self.out["on"])

    def test_off_brings_the_chrome_back_and_fades_it_in(self):
        self.assertEqual(self.out["off"], {"on": False, "fading": False, "hidden": False, "scrollTop": 100})

    def test_on_then_off_within_the_fade_ends_off(self):
        self.assertEqual(self.out["quickToggle"], {"on": False, "fading": False, "hidden": False, "scrollTop": 100})

    def test_the_pill_shows_while_listening_with_what_is_heard(self):
        self.assertEqual(self.out["listening"], {"showing": True, "label": "Listening..."})
        self.assertEqual(self.out["partial"], '"next pa"')

    def test_the_pill_leaves_with_the_final_words_on_it(self):
        self.assertEqual(self.out["heard"], {"showing": False, "label": '"next page"'})
        self.assertFalse(self.out["idle"])


CENTERING_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({ document: {} });
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const offset = vm.runInContext("pageOffsetForWindow", context);
// A 1280px window: the progress bar's 4px on the left, the toolbar's 57px on
// the right, so the area is 4..1223 and its centre 26.5px left of the window's.
const area = { windowWidth: 1280, areaLeft: 4, areaWidth: 1219 };
console.log(JSON.stringify({
  narrow: offset({ ...area, pageWidth: 800 }),
  justFits: offset({ ...area, pageWidth: 1134 }),
  tooWide: offset({ ...area, pageWidth: 1150 }),
  wider: offset({ ...area, pageWidth: 2000 }),
  panelOpen: offset({ windowWidth: 1280, areaLeft: 269, areaWidth: 954, pageWidth: 600 }),
  panelOpenWide: offset({ windowWidth: 1280, areaLeft: 269, areaWidth: 954, pageWidth: 800 }),
  focus: offset({ windowWidth: 1280, areaLeft: 4, areaWidth: 1276, pageWidth: 800 }),
  noPage: offset({ ...area, pageWidth: 0 }),
}));
"""


@unittest.skipUnless(NODE, "Node is not installed")
class PageCenteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [NODE, "-e", CENTERING_HARNESS, str(CENTERING_JS)], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_a_page_that_fits_is_centred_on_the_window(self):
        # 640 - (4 + 609.5) = 26.5, rounded.
        self.assertEqual(self.out["narrow"], 27)

    def test_up_to_the_width_that_still_clears_the_toolbar(self):
        # Right edge at 640 + 567 = 1207 = 1223 - 16.
        self.assertEqual(self.out["justFits"], 27)

    def test_a_page_too_wide_for_that_is_centred_in_its_area(self):
        self.assertEqual(self.out["tooWide"], 0)
        self.assertEqual(self.out["wider"], 0)

    def test_with_the_outline_open_the_page_still_aims_for_the_window_centre(self):
        self.assertEqual(self.out["panelOpen"], -106)
        # Its left edge would land under the panel: back to the area's centre.
        self.assertEqual(self.out["panelOpenWide"], 0)

    def test_in_focus_mode_only_the_progress_line_is_left_to_correct(self):
        self.assertEqual(self.out["focus"], -2)

    def test_no_page_yet_means_no_offset(self):
        self.assertEqual(self.out["noPage"], 0)


class ReaderMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = READING_HTML.read_text(encoding="utf-8")
        cls.js = READING_JS.read_text(encoding="utf-8")
        cls.css = READING_CSS.read_text(encoding="utf-8")

    def section(self, start, end):
        return self.html[self.html.index(start):self.html.index(end)]

    def test_zoom_steps_sit_either_side_of_the_percentage_in_the_topbar(self):
        zoom = self.section('<div class="zoom-control">', 'id="zoomPopover"')
        ids = re.findall(r'id="(zoomOutBtn|zoomLabel|zoomInBtn)"', zoom)
        self.assertEqual(ids, ["zoomOutBtn", "zoomLabel", "zoomInBtn"])
        rail = self.section('<div class="rail focus-chrome">', '<div class="footer focus-chrome">')
        self.assertNotIn("zoomInBtn", rail)
        self.assertNotIn("zoomOutBtn", rail)

    def test_ctrl_zoom_keys_are_the_readers(self):
        self.assertIn('case "=":\n    case "+":\n      zoomIn();', self.js)
        self.assertIn('case "-":\n      zoomOut();', self.js)
        # Handled ahead of the guard that hands typing keys to an input box.
        ctrl_zoom = self.js.index('ev.key === "-" ? zoomOut() : zoomIn();')
        self.assertLess(ctrl_zoom, self.js.index('if (ev.target.tagName === "INPUT") return;'))

    def test_undo_and_redo_are_never_hidden_or_natively_disabled(self):
        for button in ("undoBtn", "redoBtn"):
            tag = re.search(rf'<button[^>]*id="{button}"[^>]*>', self.html).group(0)
            with self.subTest(button=button):
                self.assertNotIn(" hidden", tag)
                self.assertNotRegex(tag, r"\sdisabled")
                self.assertIn('aria-disabled="true"', tag)
        self.assertNotRegex(self.js, r"(undoBtn|redoBtn)\.(hidden|disabled)\s*=")

    def test_undo_and_redo_follow_the_undo_stack_not_the_unsaved_flag(self):
        body = self.js[self.js.index("function updateUndoButtons()"):self.js.index("function setUndoButton(")]
        self.assertIn("state.can_undo", body)
        self.assertIn("state.can_redo", body)
        self.assertNotIn("is_dirty", body)
        self.assertIn('"Nothing to undo"', body)
        self.assertIn('"Nothing to redo"', body)

    def test_dimmed_undo_is_forty_percent(self):
        rule = re.search(r'\.rail \.icon-btn\[aria-disabled="true"\] \{([^}]*)\}', self.css).group(1)
        self.assertIn("opacity: 0.4", rule)

    def test_every_bar_focus_mode_hides_is_marked(self):
        for marked in ('class="topbar focus-chrome"', 'class="rail focus-chrome"', 'class="footer focus-chrome"'):
            self.assertIn(marked, self.html)
        # The left edge is not part of it.
        self.assertNotRegex(self.html, r'class="[^"]*(progress-edge|outline-panel)[^"]*focus-chrome')

    def test_f11_and_esc_drive_focus_mode(self):
        self.assertIn('if (ev.key === "F11")', self.js)
        self.assertIn("else if (isFocusMode() && !dialogOpenAtKey) setFocusMode(false);", self.js)
        self.assertIn("if (ev.repeat || dialogOpenAtKey) return;", self.js)

    def test_entering_closes_the_popovers_in_the_hidden_chrome(self):
        body = self.js[self.js.index("function enterFocusMode()"):self.js.index("let dialogOpenAtKey")]
        self.assertIn("closeColorFlyout();", body)
        self.assertIn("toggleZoomPopover(false);", body)

    def test_focus_mode_remembers_nothing(self):
        source = FOCUS_JS.read_text(encoding="utf-8")
        for store in ("localStorage", "sessionStorage", "callApi", "indexedDB", "document.cookie"):
            self.assertNotIn(store, source)


if __name__ == "__main__":
    unittest.main()
