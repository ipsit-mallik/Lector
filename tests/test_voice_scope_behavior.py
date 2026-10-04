"""Behaviour of frontend/js/voice.js's `createVoiceScope` (Milestone 8.12).

A modal (the "What can I say?" panel, Settings' overwrite confirmation,
dictation) takes a voice scope on open and gives it back on close. The bridge
calls are async and pywebview runs each on its own thread, so a close that
lands while the open's push is still in flight used to send its pop first (or
not at all), leaving the recognizer stuck on the modal's tiny grammar with the
screen's own commands dead. `createVoiceScope` serializes the two.

Run under Node like tests/test_voice_keyboard_behavior.py: voice.js is loaded
into a context with a fake `callApi` that records the order calls *finish* in,
with a configurable delay per call. Skipped when Node is not installed.
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

// Every bridge call finishes after `delayFor(method)` ms, and is logged then.
// With `numberPops`, each pop is logged as `pop_voice_context#N`, N being the
// order it was *sent* in, so a log that finishes them out of order is visible.
function makeContext(delayFor, failPush = false, numberPops = false) {
  const log = [];
  let popsSent = 0;
  const context = vm.createContext({
    document: { activeElement: null },
    console: { error() {} },
    setTimeout,
    callApi: (method, arg) => {
      const sentAs = numberPops && method === "pop_voice_context" ? `${method}#${++popsSent}` : null;
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          if (method === "push_voice_context" && failPush) return reject(new Error("boom"));
          log.push(sentAs || (arg ? `${method}:${arg}` : method));
          resolve({});
        }, delayFor(method));
      });
    },
  });
  vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + "\nthis.api = { createVoiceScope };", context);
  return { api: context.api, log };
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const results = {};
const check = async (name, fn) => {
  try {
    results[name] = (await fn()) === true || "assertion false";
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

(async () => {
  await check("open then close: push finishes before pop", async () => {
    // The push is slow and the pop is fast: unserialized, the pop lands first.
    const { api, log } = makeContext((m) => (m === "push_voice_context" ? 60 : 1));
    const scope = api.createVoiceScope("reference");
    scope.enter();
    scope.leave();
    await wait(200);
    return same(log, ["push_voice_context:reference", "pop_voice_context"]);
  });

  await check("a close during the push still pops (no stuck scope)", async () => {
    const { api, log } = makeContext((m) => (m === "push_voice_context" ? 50 : 1));
    const scope = api.createVoiceScope("overwrite_confirm");
    scope.enter();
    await wait(10); // push still in flight
    scope.leave();
    await wait(200);
    return log.filter((c) => c === "pop_voice_context").length === 1;
  });

  await check("entering twice pushes once", async () => {
    const { api, log } = makeContext(() => 1);
    const scope = api.createVoiceScope("reference");
    scope.enter();
    scope.enter();
    await wait(50);
    return same(log, ["push_voice_context:reference"]);
  });

  await check("leaving twice pops once", async () => {
    const { api, log } = makeContext(() => 1);
    const scope = api.createVoiceScope("reference");
    scope.enter();
    scope.leave();
    scope.leave();
    await wait(50);
    return log.filter((c) => c === "pop_voice_context").length === 1;
  });

  await check("leaving without entering sends nothing", async () => {
    const { api, log } = makeContext(() => 1);
    api.createVoiceScope("reference").leave();
    await wait(30);
    return log.length === 0;
  });

  await check("a failed push is never popped (would pop someone else's scope)", async () => {
    const { api, log } = makeContext(() => 1, true);
    const scope = api.createVoiceScope("reference");
    scope.enter();
    scope.leave();
    await wait(50);
    return !log.includes("pop_voice_context");
  });

  await check("enter reports whether the scope was actually taken", async () => {
    const ok = makeContext(() => 1);
    const bad = makeContext(() => 1, true);
    return (await ok.api.createVoiceScope("reference").enter()) === true
      && (await bad.api.createVoiceScope("reference").enter()) === false;
  });

  await check("open, close, open again is push, pop, push in order", async () => {
    const { api, log } = makeContext((m) => (m === "push_voice_context" ? 30 : 1));
    const scope = api.createVoiceScope("reference");
    scope.enter();
    scope.leave();
    scope.enter();
    await wait(200);
    return same(log, ["push_voice_context:reference", "pop_voice_context", "push_voice_context:reference"]);
  });

  await check("nested scopes unwind innermost first even when the inner pop is slow", async () => {
    // Dictation sits on the panel; closing the panel while dictating leaves
    // dictation, then the panel. The two pops must not overtake each other.
    let popCount = 0;
    const { api, log } = makeContext((m) => {
      if (m !== "pop_voice_context") return 1;
      popCount += 1;
      return popCount === 1 ? 60 : 1; // the first pop sent (dictation's) is the slow one
    }, false, true);
    const panel = api.createVoiceScope("reference");
    const dictation = api.createVoiceScope("dictation");
    panel.enter();
    dictation.enter();
    await wait(30);
    dictation.leave();
    panel.leave();
    await wait(250);
    return same(log, [
      "push_voice_context:reference",
      "push_voice_context:dictation",
      "pop_voice_context#1",
      "pop_voice_context#2",
    ]);
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class VoiceScopeTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.results), 9)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
