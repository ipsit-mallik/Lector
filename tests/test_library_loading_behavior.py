"""Behaviour of frontend/pages/library-loading.js: when the library's loading
placeholder appears and goes, how many rows or cards it has, and the queue that
fetches thumbnails after the rows are on screen.

Run under Node, the same way tests/test_recent_list_behavior.py runs
recent-list.js, with a fake clock in place of the browser's timers so the 150ms
and 300ms rules are checked exactly. Skipped when Node is not installed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "library-loading.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// A fake clock: timers fire only when advance() passes their time.
let now = 0;
let timers = [];
let nextId = 1;
const clock = {
  setTimeout: (fn, ms) => { const id = nextId++; timers.push({ id, at: now + ms, fn }); return id; },
  clearTimeout: (id) => { timers = timers.filter((t) => t.id !== id); },
  performance: { now: () => now },
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
async function advance(ms) {
  const until = now + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at);
    const due = timers[0];
    if (!due || due.at > until) break;
    timers.shift();
    now = due.at;
    due.fn();
    await flush();
  }
  now = until;
  await flush();
}

// Just enough of an element for the skeleton layer.
function fakeLayer() {
  const classes = new Set();
  const listeners = {};
  return {
    hidden: true,
    children: [],
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    replaceChildren(...nodes) { this.children = nodes; },
    addEventListener(type, fn) { listeners[type] = fn; },
    fire(type) { const fn = listeners[type]; delete listeners[type]; if (fn) fn({ target: this }); },
  };
}

let apiCalls = [];
let apiReplies = {};
const context = vm.createContext({
  ...clock,
  console: { error: () => {} },
  callApi: async (method, paths) => {
    apiCalls.push(`${method}:${paths.join("+")}`);
    await flush();
    const failed = paths.find((path) => apiReplies[path] instanceof Error);
    if (failed) throw apiReplies[failed];
    return Object.fromEntries(paths.map((path) => [path, apiReplies[path]]));
  },
});
vm.runInContext(
  fs.readFileSync(process.argv[1], "utf8") +
    "\nthis.api = { skeletonCount, parseKnownCount, createSkeletonGate, createThumbnailLoader };",
  context
);
const api = context.api;

const results = {};
const check = async (name, fn) => {
  now = 0;
  timers = [];
  try {
    results[name] = (await fn()) === true;
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};

// Settles when `promise` does, recording the clock at that moment.
function timed(promise) {
  const out = { at: null };
  promise.then(() => { out.at = now; });
  return out;
}

(async () => {
  // How many placeholders.
  await check("an unknown count falls back to six", () => api.skeletonCount(null) === 6);
  await check("a known count is used as it is", () => api.skeletonCount(7) === 7);
  await check("a known empty list has no placeholders", () => api.skeletonCount(0) === 0);
  await check("a long list is capped at twenty", () => api.skeletonCount(35) === 20);
  await check("the server's attribute text is read as a count", () =>
    api.parseKnownCount("12") === 12 && api.parseKnownCount("") === null &&
    api.parseKnownCount("-3") === null && api.parseKnownCount(undefined) === null);

  // When it appears.
  await check("a load under 150ms never shows the placeholder", async () => {
    const layer = fakeLayer();
    const gate = api.createSkeletonGate(layer);
    let built = 0;
    gate.arm(() => { built++; return "skeleton"; });
    await advance(149);
    const settled = timed(gate.settle());
    await advance(0);
    gate.release();
    await advance(1000);
    return built === 0 && layer.hidden === true && settled.at === 149;
  });

  await check("a slower load shows it at 150ms", async () => {
    const layer = fakeLayer();
    const gate = api.createSkeletonGate(layer);
    gate.arm(() => "skeleton");
    await advance(150);
    return layer.hidden === false && layer.children[0] === "skeleton" &&
      layer.classList.contains("is-shown");
  });

  await check("once shown it stays at least 300ms", async () => {
    const gate = api.createSkeletonGate(fakeLayer());
    gate.arm(() => "skeleton");
    await advance(200);
    const settled = timed(gate.settle());
    await advance(1000);
    return settled.at === 450;
  });

  await check("content later than that replaces it at once", async () => {
    const gate = api.createSkeletonGate(fakeLayer());
    gate.arm(() => "skeleton");
    await advance(700);
    const settled = timed(gate.settle());
    await advance(0);
    return settled.at === 700;
  });

  await check("release fades it out, then empties and hides it", async () => {
    const layer = fakeLayer();
    const gate = api.createSkeletonGate(layer);
    gate.arm(() => "skeleton");
    await advance(500);
    await gate.settle();
    gate.release();
    const fading = layer.classList.contains("is-leaving") && !layer.classList.contains("is-shown") &&
      layer.hidden === false;
    layer.fire("animationend");
    return fading && layer.hidden === true && layer.children.length === 0 &&
      !layer.classList.contains("is-leaving");
  });

  await check("nothing to show (a known empty list) shows nothing", async () => {
    const layer = fakeLayer();
    const gate = api.createSkeletonGate(layer);
    gate.arm(() => null);
    await advance(500);
    const settled = timed(gate.settle());
    await advance(0);
    return layer.hidden === true && settled.at === 500;
  });

  await check("onShow runs when, and only when, it appears", async () => {
    let shown = 0;
    const gate = api.createSkeletonGate(fakeLayer(), { onShow: () => shown++ });
    gate.arm(() => "a");
    await advance(100);
    await gate.settle();
    gate.arm(() => "b");
    await advance(150);
    return shown === 1;
  });

  // Thumbnails after the rows.
  const pending = (...paths) => paths.map((path) => ({ path, thumbnail_pending: true }));
  const settle = async () => { for (let i = 0; i < 6; i++) await advance(0); };

  await check("thumbnails are fetched in small batches, in the order asked", async () => {
    apiCalls = [];
    apiReplies = { a: "A", c: "C", d: "D", e: "E", f: "F", g: "G" };
    const got = [];
    const loader = api.createThumbnailLoader((path, png) => got.push(`${path}=${png}`));
    loader.request([
      ...pending("a"),
      { path: "b", thumbnail_pending: false, thumbnail: "B" },
      ...pending("c", "d", "e", "f", "g"),
    ]);
    await settle();
    return apiCalls.join() === "get_thumbnails:a+c+d+e,get_thumbnails:f+g" &&
      got.join() === "a=A,c=C,d=D,e=E,f=F,g=G";
  });

  await check("a thumbnail already asked for is not asked for twice", async () => {
    apiCalls = [];
    apiReplies = { a: "A" };
    const loader = api.createThumbnailLoader(() => {});
    loader.request(pending("a"));
    loader.request(pending("a"));
    await settle();
    return apiCalls.join() === "get_thumbnails:a";
  });

  await check("a batch that fails clears its placeholders and the rest still load", async () => {
    apiCalls = [];
    apiReplies = { a: new Error("bridge"), b: "B", c: "C", d: "D", e: "E" };
    const got = [];
    const loader = api.createThumbnailLoader((path, png) => got.push(`${path}=${png}`));
    loader.request(pending("a", "b", "c", "d", "e"));
    await settle();
    return got.join() === "a=null,b=null,c=null,d=null,e=E";
  });

  await check("rows drawn after a thumbnail arrived are drawn with it", async () => {
    apiCalls = [];
    apiReplies = { a: "A", b: null };
    const loader = api.createThumbnailLoader(() => {});
    const entries = [...pending("a", "b"), ...pending("c")];
    loader.request(entries.slice(0, 2));
    await settle();
    const merged = loader.withLoaded(entries);
    return merged[0].thumbnail === "A" && merged[0].thumbnail_pending === false &&
      merged[1].thumbnail === null && merged[1].thumbnail_pending === false &&
      merged[2].thumbnail_pending === true && entries[0].thumbnail_pending === true;
  });

  await check("a file with no thumbnail clears its placeholder", async () => {
    apiCalls = [];
    apiReplies = {};
    const got = [];
    const loader = api.createThumbnailLoader((path, png) => got.push(`${path}=${png}`));
    loader.request(pending("a"));
    await settle();
    return got.join() === "a=null";
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class LibraryLoadingBehaviourTests(unittest.TestCase):
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
