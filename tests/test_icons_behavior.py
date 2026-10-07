"""Icon loading (frontend/js/icons.js) must never turn a failed request into a
permanently blank icon.

Run under Node against a fake `fetch`, the way tests/test_programmatic_scroll.py
runs ui.js. Skipped when Node is not installed. The bug it pins: an error
response (404/500, or an HTML page where the file should be) was inlined as the
icon and cached for the life of the page, so every icon that asked during a bad
moment stayed blank while the rest of the page worked.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
ICONS_JS = FRONTEND / "js" / "icons.js"
ICONS_DIR = FRONTEND / "shared" / "icons"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>';

// A scripted server: each icon name maps to a list of responses, consumed in order
// (the last one repeats).
let script = {};
let requests = [];
const fakeFetch = async (url) => {
  const name = String(url).split("/").pop().replace(".svg", "");
  requests.push(name);
  const queue = script[name] || [{ status: 200, body: SVG }];
  const next = queue.length > 1 ? queue.shift() : queue[0];
  if (next.reject) throw new TypeError("Failed to fetch");
  return { ok: next.status >= 200 && next.status < 300, status: next.status, text: async () => next.body };
};

const errors = [];
const context = vm.createContext({
  document: { currentScript: { src: "http://127.0.0.1:42001/js/icons.js" } },
  fetch: fakeFetch,
  URL, setTimeout: (fn) => setTimeout(fn, 0), console: { error: (m) => errors.push(m) },
});
vm.runInContext(source + "\nthis.api = { loadIcon, mountIcon, mountIcons };", context);
const { loadIcon, mountIcon, mountIcons } = context.api;

const element = () => ({ innerHTML: "", dataset: {}, parentElement: null });
const results = {};
const check = async (name, fn) => {
  // The cache is per page, so each scenario needs a fresh module state.
  script = {};
  requests = [];
  errors.length = 0;
  vm.runInContext("_iconCache.clear()", context);
  try { results[name] = (await fn()) === true; } catch (err) { results[name] = "threw: " + err.message; }
};

(async () => {
  await check("a good icon is inlined", async () => {
    const el = element();
    await mountIcon(el, "recent");
    return el.innerHTML === SVG;
  });

  await check("an error page is never inlined as the icon", async () => {
    script.recent = [{ status: 404, body: "<html>Not Found</html>" }];
    const el = element();
    await mountIcon(el, "recent");
    return el.innerHTML === "" && errors.length === 1;
  });

  await check("a 200 whose body is not an SVG is rejected", async () => {
    script.recent = [{ status: 200, body: "<!DOCTYPE html><html></html>" }];
    const el = element();
    await mountIcon(el, "recent");
    return el.innerHTML === "";
  });

  await check("an empty body is rejected", async () => {
    script.recent = [{ status: 200, body: "" }];
    const el = element();
    await mountIcon(el, "recent");
    return el.innerHTML === "";
  });

  await check("a transient failure is retried and the icon appears", async () => {
    script.recent = [{ status: 500, body: "boom" }, { reject: true }, { status: 200, body: SVG }];
    const el = element();
    await mountIcon(el, "recent");
    return el.innerHTML === SVG && requests.length === 3 && errors.length === 0;
  });

  await check("a failure is not cached: a later request succeeds", async () => {
    script.recent = [{ status: 500, body: "x" }];
    const bad = element();
    await mountIcon(bad, "recent");
    script.recent = [{ status: 200, body: SVG }];
    const good = element();
    await mountIcon(good, "recent");
    return bad.innerHTML === "" && good.innerHTML === SVG;
  });

  await check("one icon failing does not stop the others mounting", async () => {
    script.logo = [{ status: 404, body: "no" }];
    const a = element(), b = element(), c = element();
    await Promise.all([mountIcon(a, "recent"), mountIcon(b, "logo"), mountIcon(c, "search")]);
    return a.innerHTML === SVG && b.innerHTML === "" && c.innerHTML === SVG;
  });

  await check("mountIcons resolves even when an icon cannot be loaded", async () => {
    script.logo = [{ status: 404, body: "no" }];
    const els = ["recent", "logo", "search"].map((n) => { const e = element(); e.dataset.icon = n; return e; });
    const root = { querySelectorAll: () => els };
    await mountIcons(root);          // used to reject, aborting the page's init
    return els[0].innerHTML === SVG && els[1].innerHTML === "" && els[2].innerHTML === SVG;
  });

  await check("many elements asking for one icon share one request", async () => {
    const els = Array.from({ length: 6 }, element);
    await Promise.all(els.map((e) => mountIcon(e, "star")));
    return requests.length === 1 && els.every((e) => e.innerHTML === SVG);
  });

  await check("a cached icon is not fetched again", async () => {
    await loadIcon("recent");
    await loadIcon("recent");
    return requests.length === 1;
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class IconLoadingBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(ICONS_JS)], capture_output=True, text=True, timeout=30
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 10)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


class IconFilesTests(unittest.TestCase):
    def test_every_icon_file_is_svg_text_the_loader_will_accept(self):
        # The loader rejects anything that does not start with "<svg", so a file
        # that did not would silently be treated as a failed load.
        files = sorted(ICONS_DIR.glob("*.svg"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(icon=path.name):
                self.assertTrue(path.read_text(encoding="utf-8").lstrip().startswith("<svg"))


if __name__ == "__main__":
    unittest.main()
