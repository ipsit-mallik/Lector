"""Behaviour of frontend/js/voice-box.js's screen-reader announcements.

The sidebar voice box changes between "listening", "heard" and "unavailable",
but only sighted readers could see it. `createVoiceBox` now also writes one
concise message into a `role="status"` element. Two rules matter and are what
this tests:

* **One message per state change.** While someone speaks, `initVoice` reports a
  new partial transcript many times a second; announcing each would chatter
  over the very thing they are saying. "Listening" is announced once.
* **Only a real message is written.** The visible text (which carries the
  partials) is left to the screen; the live region carries the summary.

Run under Node like tests/test_voice_keyboard_behavior.py; skipped without it.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "js" / "voice-box.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const context = vm.createContext({ setTimeout, clearTimeout });
vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + "\nthis.api = { createVoiceBox };", context);

// A text node that remembers every value written to it.
function textNode() {
  const writes = [];
  return { writes, set textContent(v) { writes.push(v); this._v = v; }, get textContent() { return this._v; } };
}
function build({ withLive = true } = {}) {
  const els = {
    box: { classList: { toggle() {} } },
    state: textNode(),
    detail: textNode(),
  };
  if (withLive) els.live = textNode();
  return { els, box: context.api.createVoiceBox(els) };
}
const base = { text: "", error: null, wake: false, pushToTalk: true, viaWake: false };

const results = {};
const check = (name, fn) => {
  try { results[name] = fn() === true || "assertion false"; }
  catch (err) { results[name] = `threw: ${err.message}`; }
};

check("ready is announced", () => {
  const { els, box } = build();
  box.render({ ...base, state: "idle" });
  return els.live.textContent === "Voice ready";
});

check("listening is announced once, not once per partial transcript", () => {
  const { els, box } = build();
  box.render({ ...base, state: "listening", text: "" });
  box.render({ ...base, state: "listening", text: "open" });
  box.render({ ...base, state: "listening", text: "open set" });
  box.render({ ...base, state: "listening", text: "open settings" });
  return els.live.writes.filter((w) => w === "Listening").length === 1 && els.live.writes.length === 1;
});

check("the visible text still follows every partial", () => {
  const { els, box } = build();
  box.render({ ...base, state: "listening", text: "open" });
  box.render({ ...base, state: "listening", text: "open settings" });
  return els.detail.textContent === '"open settings"';
});

check("what was heard is announced", () => {
  const { els, box } = build();
  box.render({ ...base, state: "heard", text: "open settings" });
  return els.live.textContent === 'Heard: "open settings"';
});

check("a miss is announced as one", () => {
  const { els, box } = build();
  box.render({ ...base, state: "heard", text: "" });
  return els.live.textContent === "Didn't catch that";
});

check("unavailable carries the reason", () => {
  const { els, box } = build();
  box.render({ ...base, state: "unavailable", error: "No speech model installed." });
  return els.live.textContent.startsWith("Voice unavailable") && els.live.textContent.includes("No speech model installed.");
});

check("off is announced", () => {
  const { els, box } = build();
  box.render({ ...base, state: "off" });
  return els.live.textContent === "Voice off";
});

check("a failure before the engine could be asked is announced too", () => {
  const { els, box } = build();
  box.showUnavailable("Voice commands couldn't start on this screen.");
  return els.live.textContent.includes("couldn't start");
});

check("a full utterance reads: listening, heard, ready", () => {
  const { els, box } = build();
  box.render({ ...base, state: "idle" });
  box.render({ ...base, state: "listening", text: "" });
  box.render({ ...base, state: "listening", text: "dark theme" });
  box.render({ ...base, state: "heard", text: "dark theme" });
  box.render({ ...base, state: "idle" });
  return JSON.stringify(els.live.writes) ===
    JSON.stringify(["Voice ready", "Listening", 'Heard: "dark theme"', "Voice ready"]);
});

check("no live element is fine", () => {
  const { box } = build({ withLive: false });
  box.render({ ...base, state: "listening", text: "x" });
  box.render({ ...base, state: "heard", text: "x" });
  return true;
});

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class VoiceBoxAnnouncementTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.results), 10)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
