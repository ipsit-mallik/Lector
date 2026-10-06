"""Programmatic scrolling (docs/DESIGN_SYSTEM.md, "Scrollbars").

The helper in frontend/js/ui.js (`scrollElementTo` and friends) is run under
Node against a fake scroller and a fake animation-frame clock, so the timing,
the "too far to animate" cutoff, reduced motion and takeover are checked
exactly. Skipped when Node is not installed. The wiring (which callers animate
and which must not) is pinned from the source below it.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
UI_JS = FRONTEND / "js" / "ui.js"
READING_JS = FRONTEND / "pages" / "reading.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const full = fs.readFileSync(process.argv[1], "utf8");
const start = full.indexOf("// Programmatic scrolling:");
const end = full.indexOf("// Radio semantics for a group");
const source = full.slice(start, end);

let reduced = false;
let queue = [];
let nextId = 1;
const context = vm.createContext({
  window: { matchMedia: () => ({ matches: reduced }) },
  document: { documentElement: {} },
  getComputedStyle: () => ({ getPropertyValue: () => "200ms" }),
  requestAnimationFrame: (fn) => { queue.push({ id: nextId, fn }); return nextId++; },
  cancelAnimationFrame: (id) => { queue = queue.filter((q) => q.id !== id); },
  Math, WeakMap, WeakSet,
});
vm.runInContext(
  source + "\nthis.api = { scrollElementTo, scrollElementBy, scrollDestination };",
  context,
);
const { scrollElementTo, scrollElementBy, scrollDestination } = context.api;

// Runs every queued frame as if the clock read `ms`.
const tick = (ms) => { const run = queue; queue = []; run.forEach((q) => q.fn(ms)); };
const scroller = (over = {}) => {
  const listeners = {};
  return {
    scrollTop: 0, scrollHeight: 10000, clientHeight: 800,
    addEventListener(type, fn) { listeners[type] = fn; },
    fire(type) { if (listeners[type]) listeners[type](); },
    ...over,
  };
};

const results = {};
const check = (name, fn) => {
  reduced = false;
  queue = [];
  try { results[name] = fn() === true; } catch (err) { results[name] = "threw: " + err.message; }
};

check("animate:false lands at once", () => {
  const el = scroller();
  return scrollElementTo(el, 500, { animate: false }) === true && el.scrollTop === 500 && queue.length === 0;
});

check("a short jump holds until the first frame, then eases out over 200ms", () => {
  const el = scroller();
  scrollElementTo(el, 800);
  if (el.scrollTop !== 0) return false;
  tick(1000);                       // first frame: the tween starts
  tick(1100);                       // halfway through 200ms
  const mid = el.scrollTop;
  tick(1200);                       // done
  // Ease-out: past the halfway distance at the halfway time.
  return mid > 400 && mid < 800 && el.scrollTop === 800 && queue.length === 0;
});

check("a short jump takes the base duration whatever its distance", () => {
  const a = scroller();
  const b = scroller();
  scrollElementTo(a, 100);
  scrollElementTo(b, 2000);
  tick(0);
  tick(199);
  const unfinished = a.scrollTop < 100 && b.scrollTop < 2000;
  tick(200);
  return unfinished && a.scrollTop === 100 && b.scrollTop === 2000;
});

check("a jump over three viewports is instant, however far", () => {
  const el = scroller();
  scrollElementTo(el, 800 * 3 + 1);
  const just = el.scrollTop === 2401 && queue.length === 0;
  const far = scroller();
  scrollElementTo(far, 8000);       // page 3 to page 300
  return just && far.scrollTop === 8000 && queue.length === 0;
});

check("exactly three viewports still animates", () => {
  const el = scroller();
  scrollElementTo(el, 2400);
  return el.scrollTop === 0 && queue.length === 1;
});

check("reduced motion is instant", () => {
  reduced = true;
  const el = scroller();
  scrollElementTo(el, 300);
  return el.scrollTop === 300 && queue.length === 0;
});

check("clamps to the scroller's range and reports no movement at either end", () => {
  const el = scroller({ scrollTop: 9200 });                  // max = 10000 - 800
  const moved = scrollElementTo(el, 99999);
  const down = scrollElementBy(scroller({ scrollTop: 9200 }), 500, { animate: false });
  const up = scrollElementBy(scroller(), -500, { animate: false });
  return moved === false && el.scrollTop === 9200 && down === false && up === false;
});

check("half a pixel from the end counts as already there", () => {
  const el = scroller({ scrollTop: 9199.5 });
  return scrollElementBy(el, 500) === false;
});

check("the destination is the tween's end while it runs", () => {
  const el = scroller();
  scrollElementTo(el, 600);
  tick(0);
  tick(50);
  const during = scrollDestination(el) === 600 && el.scrollTop < 600;
  tick(300);
  return during && scrollDestination(el) === 600 && el.scrollTop === 600;
});

check("repeated presses accumulate against the running destination", () => {
  const el = scroller();
  scrollElementBy(el, 60);
  tick(0);
  tick(20);
  scrollElementBy(el, 60);
  tick(30);
  tick(300);
  return el.scrollTop === 120;
});

check("a new scroll replaces a running one rather than stacking frames", () => {
  const el = scroller();
  scrollElementTo(el, 600);
  scrollElementTo(el, 200);
  return queue.length === 1;
});

check("the wheel, a pointer press or a touch takes over from a tween", () => {
  const out = [];
  for (const type of ["wheel", "pointerdown", "touchstart"]) {
    const el = scroller();
    scrollElementTo(el, 600);
    tick(0);
    tick(40);
    const at = el.scrollTop;
    el.fire(type);
    tick(80);
    tick(300);
    out.push(el.scrollTop === at && scrollDestination(el) === at);
  }
  return out.every(Boolean);
});

check("the takeover listeners are bound once per scroller", () => {
  let added = 0;
  const el = scroller({ addEventListener() { added++; } });
  scrollElementTo(el, 300);
  scrollElementTo(el, 500);
  return added === 3;
});

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class ScrollHelperBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(UI_JS)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 13)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


class ScrollWiringTests(unittest.TestCase):
    JS = READING_JS.read_text(encoding="utf-8")

    def test_the_wheel_is_never_taken_over(self):
        # Native wheel scrolling only: nothing may cancel a wheel event or turn
        # one into a scripted scroll.
        for path in sorted(FRONTEND.rglob("*.js")):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'addEventListener\("(?:wheel|mousewheel)"[^\n]*', text):
                self.assertNotIn("passive: false", match.group(0), path.name)
            self.assertNotRegex(text, r'"wheel"[^;]*preventDefault', path.name)

    def test_no_stylesheet_turns_smooth_scrolling_on_globally(self):
        # scroll-behavior: smooth would animate every scrollTop assignment,
        # including the ones that must be exact (zoom re-anchoring) or instant
        # (a view opening), and its duration cannot be set.
        for path in sorted(FRONTEND.rglob("*.css")):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"scroll-behavior:\s*smooth", path.name)

    def test_a_requested_page_jump_animates_and_nothing_else_does(self):
        for fn in ("goNext", "goPrev", "goToPage"):
            body = self.JS[self.JS.index(f"async function {fn}("):]
            self.assertIn("refresh({ animate: true })", body[: body.index("\n}\n")], fn)
        self.assertEqual(self.JS.count("animate: true"), 3)
        strip = self.JS[self.JS.index("async function refreshStrip("):]
        strip = strip[: strip.index("\n}\n")]
        self.assertIn("animate: animate && !rebuilding", strip)

    def test_zoom_anchoring_and_the_landing_after_a_page_turn_stay_exact(self):
        # These set the position directly on purpose: a tween would drift.
        self.assertIn("stripScroll.scrollTop = stripPageTop(el)", self.JS)
        self.assertIn("scrollEl.scrollTop += desiredOffset - currentOffset", self.JS)
        self.assertIn("{ animate: false }", self.JS)

    def test_arrow_keys_and_voice_scroll_use_the_helper(self):
        self.assertIn("scrollElementBy(stripScroll, 60)", self.JS)
        self.assertIn("scrollElementBy(stripScroll, -60)", self.JS)
        self.assertIn("scrollElementBy(el, delta * el.clientHeight * VOICE_SCROLL_FRACTION)", self.JS)


if __name__ == "__main__":
    unittest.main()
