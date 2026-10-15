"""Behaviour of frontend/js/scrollbars.js: the auto-hide logic and the overlay
thumb's geometry.

The script is a plain browser script, so it is run here under Node with a fake
window, a fake <html> element and a fake clock: that checks the timing and the
voice-event rules deterministically, without a browser. Skipped when Node is not
installed (the rest of the suite is Python-only).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "js" / "scrollbars.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// With `node -e`, argv is [node, <first extra argument>].
const source = fs.readFileSync(process.argv[1], "utf8");
// No window/document in this context, so the script does not auto-start.
const context = vm.createContext({});
vm.runInContext(
  source +
    "\nthis.initScrollbarAutoHide = initScrollbarAutoHide;" +
    "\nthis.scrollbarThumb = scrollbarThumb;" +
    "\nthis.scrollbarDragPosition = scrollbarDragPosition;" +
    "\nthis.scrollbarHitTest = scrollbarHitTest;",
  context,
);

const ACTIVITY = ["mousemove", "wheel", "mousedown", "click", "keydown", "touchstart", "scroll", "focusin"];

function setup({ forcedColors = false } = {}) {
  let time = 0;
  let shows = 0;
  let timers = [];
  let nextId = 1;
  const listeners = {};
  const registrations = [];
  const classes = new Set();
  const win = {
    addEventListener(type, fn, options) {
      (listeners[type] = listeners[type] || []).push(fn);
      registrations.push({ type, options });
    },
    matchMedia: (query) => ({
      matches: forcedColors && query.includes("forced-colors"),
      addEventListener() {},
    }),
  };
  const root = {
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
  };
  context.initScrollbarAutoHide({
    win,
    root,
    now: () => time,
    setTimer: (fn, ms) => {
      const id = nextId++;
      timers.push({ id, at: time + ms, fn });
      return id;
    },
    clearTimer: (id) => {
      timers = timers.filter((t) => t.id !== id);
    },
    onShow: () => shows++,
  });
  return {
    registrations,
    shows: () => shows,
    visible: () => classes.has("scrollbars-active"),
    fire: (type, detail) => (listeners[type] || []).forEach((fn) => fn({ type, detail })),
    advance(ms) {
      const target = time + ms;
      for (;;) {
        const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        timers = timers.filter((t) => t !== due);
        time = due.at;
        due.fn();
      }
      time = target;
    },
  };
}

const results = {};
const check = (name, fn) => {
  try {
    results[name] = fn() === true;
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};

check("starts hidden", () => !setup().visible());

for (const type of ACTIVITY) {
  check(`${type} shows scrollbars`, () => {
    const s = setup();
    s.fire(type);
    return s.visible();
  });
}

check("still visible just before 1.5s of inactivity", () => {
  const s = setup();
  s.fire("mousemove");
  s.advance(1499);
  return s.visible();
});

check("hidden once 1.5s of inactivity has passed", () => {
  const s = setup();
  s.fire("mousemove");
  s.advance(1500);
  return !s.visible();
});

check("activity restarts the countdown", () => {
  const s = setup();
  s.fire("mousemove");
  s.advance(1000);
  s.fire("wheel");
  s.advance(1400);
  const stillVisible = s.visible();
  s.advance(100);
  return stillVisible && !s.visible();
});

check("a steady stream of activity keeps them visible, then they fade", () => {
  const s = setup();
  for (let i = 0; i < 10; i++) {
    s.fire("mousemove");
    s.advance(500);
  }
  const during = s.visible();
  s.advance(1500);
  return during && !s.visible();
});

check("they can show again after hiding", () => {
  const s = setup();
  s.fire("keydown");
  s.advance(2000);
  s.fire("keydown");
  return s.visible();
});

check("scroll is observed in the capture phase (it does not bubble)", () => {
  const s = setup();
  const scroll = s.registrations.find((r) => r.type === "scroll");
  return Boolean(scroll && scroll.options && scroll.options.capture === true);
});

check("activity listeners are passive", () => {
  const s = setup();
  return ACTIVITY.every((type) => {
    const reg = s.registrations.find((r) => r.type === type);
    return reg && reg.options && reg.options.passive === true;
  });
});

// Voice: only real events count.
check("wake phrase detected shows scrollbars", () => {
  const s = setup();
  s.fire("lector:voice", { text: "", final: false, wake: true });
  return s.visible();
});

check("a partial transcript shows scrollbars", () => {
  const s = setup();
  s.fire("lector:voice", { text: "next pa", final: false, wake: false });
  return s.visible();
});

check("a final transcript shows scrollbars", () => {
  const s = setup();
  s.fire("lector:voice", { text: "next page", final: true, wake: false });
  return s.visible();
});

check("an executed command shows scrollbars", () => {
  const s = setup();
  s.fire("lector:command", { text: "next page", command: { intent: "NEXT_PAGE" } });
  return s.visible();
});

check("a voice event with no wake and no text does not show scrollbars", () => {
  const s = setup();
  s.fire("lector:voice", { text: "", final: false, wake: false });
  return !s.visible();
});

check("an empty final result (window closed in silence) does not show scrollbars", () => {
  const s = setup();
  s.fire("lector:voice", { text: "", final: true, wake: false });
  return !s.visible();
});

check("mic-level style updates do not show scrollbars", () => {
  const s = setup();
  s.fire("lector:voice", { level: 0.42 });
  s.fire("lector:voice", undefined);
  return !s.visible();
});

check("always-on wake listening (no events at all) lets them hide", () => {
  const s = setup();
  s.fire("keydown");
  s.advance(60000);
  return !s.visible();
});

// forced-colors: never hide.
check("forced colors: visible from the start", () => setup({ forcedColors: true }).visible());

check("forced colors: never hidden after activity", () => {
  const s = setup({ forcedColors: true });
  s.fire("mousemove");
  s.advance(60000);
  return s.visible();
});

// The overlay is re-measured each time the scrollbars appear, not on every
// mousemove while they are up.
check("onShow runs once per appearance", () => {
  const s = setup();
  s.fire("mousemove");
  s.fire("mousemove");
  s.fire("wheel");
  const once = s.shows() === 1;
  s.advance(2000);
  s.fire("keydown");
  return once && s.shows() === 2;
});

// Thumb geometry (the overlay's pure helpers).
const thumb = (m) => context.scrollbarThumb({ minThumb: 32, ...m });
const near = (a, b) => Math.abs(a - b) < 1e-9;

check("thumb length is the visible share of the track", () => {
  const t = thumb({ viewport: 500, content: 1000, position: 0, track: 400 });
  return near(t.length, 200) && near(t.offset, 0) && near(t.travel, 200) && near(t.range, 500);
});

check("thumb offset follows the scroll position", () => {
  const t = thumb({ viewport: 500, content: 1000, position: 250, track: 400 });
  return near(t.offset, 100);
});

check("thumb reaches the end of its travel at the bottom", () => {
  const t = thumb({ viewport: 500, content: 1000, position: 500, track: 400 });
  return near(t.offset, t.travel);
});

check("thumb is never shorter than the minimum on a very long document", () => {
  const t = thumb({ viewport: 716, content: 344804, position: 0, track: 700 });
  return near(t.length, 32) && near(t.travel, 668);
});

check("thumb never outgrows a track shorter than the minimum", () => {
  const t = thumb({ viewport: 100, content: 1000, position: 300, track: 20 });
  return near(t.length, 20) && near(t.offset, 0);
});

check("overscroll keeps the thumb inside the track", () => {
  const above = thumb({ viewport: 500, content: 1000, position: -40, track: 400 });
  const below = thumb({ viewport: 500, content: 1000, position: 540, track: 400 });
  return near(above.offset, 0) && near(below.offset, below.travel);
});

check("nothing to scroll means no thumb", () =>
  thumb({ viewport: 500, content: 500, position: 0, track: 400 }) === null &&
  thumb({ viewport: 500, content: 1000, position: 0, track: 0 }) === null);

check("dragging the thumb scrolls in proportion", () =>
  near(context.scrollbarDragPosition({ startPosition: 100, moved: 50, travel: 200, range: 500 }), 225));

check("dragging past either end stops at the end", () =>
  context.scrollbarDragPosition({ startPosition: 100, moved: -400, travel: 200, range: 500 }) === 0 &&
  context.scrollbarDragPosition({ startPosition: 100, moved: 400, travel: 200, range: 500 }) === 500);

check("a thumb with no travel does not scroll when dragged", () =>
  context.scrollbarDragPosition({ startPosition: 70, moved: 30, travel: 0, range: 500 }) === 70);

// The rails take no pointer events, so the one under the pointer is found by
// position: the 12px grab area, edges included on the near side only.
check("a pointer inside a rail's grab area hits it", () => {
  const area = { left: 1207, top: 57, right: 1219, bottom: 757 };
  return context.scrollbarHitTest(area, 1207, 57) && context.scrollbarHitTest(area, 1218.5, 400);
});

check("a pointer beside or past a rail misses it", () => {
  const area = { left: 1207, top: 57, right: 1219, bottom: 757 };
  return !context.scrollbarHitTest(area, 1206.5, 400) && !context.scrollbarHitTest(area, 1219, 400) &&
    !context.scrollbarHitTest(area, 1210, 757) && !context.scrollbarHitTest(null, 1210, 400);
});

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class ScrollbarAutoHideBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 38)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
