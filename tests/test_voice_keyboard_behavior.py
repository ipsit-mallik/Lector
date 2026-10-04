"""Behaviour of frontend/js/voice.js's `shouldIgnoreKey` (Milestone 8.12).

Push-to-talk is "hold Space", and Space is also how a keyboard user activates
the button or checkbox that has focus. `shouldIgnoreKey` decides which of the
two a Space press means. It used to give way to *any* focused button — and on
Home and Settings nearly everything is a button, so after the reader clicked a
sidebar item or a view toggle, holding Space silently did nothing: voice looked
broken exactly when it was working.

Run under Node, the same way tests/test_recent_list_behavior.py runs its
script: voice.js is loaded into a context holding only a fake `document`, which
its top level supports (nothing runs until `initVoice` is called). Skipped when
Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "js" / "voice.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({ document: { activeElement: null } });
vm.runInContext(source + "\nthis.api = { shouldIgnoreKey };", context);

// A focused element as the browser reports it. `keyboard` is whether the
// browser would match :focus-visible, which for a button or checkbox means
// focus arrived by Tab/arrow keys rather than by a mouse click.
const focused = (tagName, { type = "", keyboard = false, editable = false } = {}) => ({
  tagName,
  type,
  isContentEditable: editable,
  matches: (selector) => selector === ":focus-visible" && keyboard,
});

const ignores = (el) => {
  context.document.activeElement = el;
  return context.api.shouldIgnoreKey();
};

const results = {};
const check = (name, expected, el) => {
  try {
    results[name] = ignores(el) === expected || `expected ${expected}`;
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};

check("nothing focused: Space talks", false, null);
check("page body: Space talks", false, focused("BODY"));

// Typing fields always own Space.
check("text input owns Space", true, focused("INPUT", { type: "text" }));
check("search input owns Space", true, focused("INPUT", { type: "search" }));
check("textarea owns Space", true, focused("TEXTAREA"));
check("select owns Space", true, focused("SELECT"));
check("contenteditable owns Space", true, focused("DIV", { editable: true }));

// A button the reader clicked with the mouse does not own Space...
check("mouse-focused button: Space talks", false, focused("BUTTON"));
check("mouse-focused link: Space talks", false, focused("A"));
check("mouse-focused checkbox: Space talks", false, focused("INPUT", { type: "checkbox" }));
check("mouse-focused radio: Space talks", false, focused("INPUT", { type: "radio" }));

// ...but one they reached by keyboard still does, so keyboard activation is
// not taken away (docs/PRD.md's mouse/keyboard parity).
check("keyboard-focused button owns Space", true, focused("BUTTON", { keyboard: true }));
check("keyboard-focused link owns Space", true, focused("A", { keyboard: true }));
check(
  "keyboard-focused checkbox owns Space",
  true,
  focused("INPUT", { type: "checkbox", keyboard: true })
);

// An element with no `matches` (an old or odd host) must fail safe: keep
// treating a focused button as owning Space rather than throwing.
check("no :focus-visible support fails safe", true, { tagName: "BUTTON", type: "" });

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class PushToTalkFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 15)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
