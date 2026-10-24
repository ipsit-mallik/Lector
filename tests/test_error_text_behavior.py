"""No error text from Python reaches the screen (hardening H3).

Two frontend scripts, run under Node the same way
tests/test_voice_keyboard_behavior.py runs voice.js:

* voice.js: a rejected voice bridge call (pywebview rejects with the Python
  exception's message) reports a fixed sentence, never the rejection's text.
* background-error.js: the hook Python calls after a background failure
  shows one toast per page, however often it is called.

Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "frontend" / "js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const SECRET = "quokkazephyr";
const results = {};
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function voiceContext(answers) {
  const states = [];
  const context = vm.createContext({
    setTimeout,
    console: { error() {} },
    window: { addEventListener() {}, dispatchEvent() {} },
    document: { activeElement: null, addEventListener() {} },
    callApi: (method) => answers[method](),
  });
  vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
  vm.runInContext("this.start = (onState) => initVoice(onState);", context);
  context.start((state) => states.push(state));
  return states;
}

async function main() {
  {
    const states = voiceContext({
      get_voice_activation: () => Promise.resolve({ push_to_talk: true, wake_phrase: false }),
      get_voice_status: () => Promise.reject(new Error(`ValueError: ${SECRET} at C:\\x.pdf`)),
    });
    await flush();
    const last = states[states.length - 1] || {};
    results["a rejected status call reports a sentence"] =
      (last.state === "unavailable" && last.error === "Voice couldn't start.") ||
      JSON.stringify(last);
    results["the rejection text is not shown"] =
      !JSON.stringify(states).includes(SECRET) || JSON.stringify(states);
  }
  {
    const states = voiceContext({
      get_voice_activation: () => Promise.resolve({ push_to_talk: true, wake_phrase: false }),
      get_voice_status: () =>
        Promise.resolve({ available: false, loading: false, error: "No speech model installed." }),
    });
    await flush();
    const last = states[states.length - 1] || {};
    results["the engine's own sentence is passed through"] =
      last.error === "No speech model installed." || JSON.stringify(last);
  }
  {
    const toasts = [];
    const window = {};
    const context = vm.createContext({ window, showToast: (message) => toasts.push(message) });
    vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
    window.lectorBackgroundError();
    window.lectorBackgroundError();
    window.lectorBackgroundError();
    results["one toast per page"] = toasts.length === 1 || `toasts: ${toasts.length}`;
    results["the toast is the plain sentence"] =
      toasts[0] === "Something went wrong in the background. Lector kept running." || toasts[0];
  }
  process.stdout.write(JSON.stringify(results));
}

main().catch((err) => {
  process.stdout.write(JSON.stringify({ harness: `threw: ${err.stack}` }));
});
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class ErrorTextBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(JS / "voice.js"), str(JS / "background-error.js")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        cls.results = json.loads(completed.stdout)

    def test_every_scenario(self):
        self.assertTrue(self.results)
        for name, outcome in self.results.items():
            with self.subTest(name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
