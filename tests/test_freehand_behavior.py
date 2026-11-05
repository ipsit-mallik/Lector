"""The highlight tool's freehand mode (`frontend/pages/freehand.js`) under Node.

A press settles the mode once, by what is under it: on a character a text
highlight (handed to reading.js's selection), anywhere else a marker stroke,
and with Alt held a stroke over text too. A page that has not been read yet
(OCR) is a text press, which reads it. A press before the page's characters
have arrived waits for them and then goes whichever way they say. Shift draws
a straight line, snapped to the axes; a nearly straight stroke is straightened
on release and committed with the stroke as drawn (so Undo can give it back),
the preview easing into the line unless reduced motion is on; the preview
stays until the commit has shown the real annotation. Esc cancels a stroke,
and a press that does not move is a click. The cursor is a highlighter over
text and a brush the stroke's on-screen size elsewhere.

The page here is 600x800pt shown at 1200x1600px (a scale of 2) at (100, 50).
Skipped when Node is not installed.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "frontend" / "pages"
GEOMETRY_JS = PAGES / "ink-geometry.js"
FREEHAND_JS = PAGES / "freehand.js"
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
READING_HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
READING_CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
COLORS_JS = (PAGES / "highlight-colors.js").read_text(encoding="utf-8")
OPTIONS_JS = (PAGES / "highlight-options.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function fakeElement(rect) {
  return {
    style: {}, attrs: {}, children: [], parent: null, rect,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; },
    getBoundingClientRect() { return this.rect; },
  };
}

const listeners = {};
const layer = fakeElement({ left: 0, top: 0, width: 2000, height: 2000 });
const page = fakeElement({ left: 100, top: 50, width: 1200, height: 1600 });
let frames = [];
let now = 0;
const context = vm.createContext({
  console, Promise, encodeURIComponent,
  document: {
    getElementById: (id) => (id === "inkLayer" ? layer : null),
    createElementNS: () => fakeElement(null),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  },
  requestAnimationFrame: (fn) => { frames.push(fn); },
});
for (const file of process.argv.slice(1)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
const api = vm.runInContext(
  "({ configureFreehand, setInkStyle, pressHighlightTool, cancelInkStroke, isDrawingInk, noteInkHover, chooseHighlightMode, isPointOnText, settled: () => inkSettling })",
  context);

const fire = (type, ev) => (listeners[type] || []).forEach((fn) => fn(ev));
const flushFrames = (ms = 16) => { const run = frames; frames = []; now += ms; run.forEach((fn) => fn(now)); };
const tick = () => new Promise((r) => setImmediate(r));

// "On text" is a word at 100..300 x 100..120 pt.
const PAGE = { width: 600, height: 800, ocr: null, chars: [] };
const onText = (p, x, y) => x >= 100 && x <= 300 && y >= 100 && y <= 120;
const at = (x, y, extra = {}) => ({ clientX: 100 + x * 2, clientY: 50 + y * 2, button: 0, altKey: false, shiftKey: false, ...extra });

let log;
let loaded;
let pageReply;
let commitResolve;
let commitReject;
let reduced;
function reset({ known = true, ocr = null, reducedMotion = false } = {}) {
  log = [];
  loaded = known ? { ...PAGE, ocr } : null;
  pageReply = { ...PAGE, ocr };
  reduced = reducedMotion;
  // Run, not dropped: freehand.js waits for a queued frame before queueing another.
  while (frames.length) flushFrames(1000);
  layer.children = [];
  page.style = {};
  api.configureFreehand({
    loadedPage: () => loaded,
    pageData: () => Promise.resolve(pageReply),
    isOnText: onText,
    beginText: (el, i, ev) => log.push(["beginText", i, Math.round(ev.clientX), Math.round(ev.clientY)]),
    moveText: (ev) => log.push(["moveText", Math.round(ev.clientX)]),
    endText: (ev) => log.push(["endText", Math.round(ev.clientX)]),
    commit: (i, drawn, straight) => {
      log.push(["commit", i, drawn, straight]);
      return new Promise((resolve, reject) => { commitResolve = resolve; commitReject = reject; });
    },
    commitFailed: () => log.push(["commitFailed"]),
    clickAt: (i, x, y) => log.push(["clickAt", i, x, y]),
    activeColor: () => "pink",
    colorValue: () => "#F6A5CB",
    toolOn: () => true,
    textDragging: () => false,
    reducedMotion: () => reduced,
  });
  api.setInkStyle({ width: 14, opacity: 0.6 });
}

function drag(points, extra = {}) {
  api.pressHighlightTool(page, 0, at(...points[0], extra));
  for (const p of points.slice(1)) fire("pointermove", at(...p, extra));
}

const previewD = () => (layer.children[0] ? layer.children[0].children[0].attrs.d : null);
const commits = () => log.filter((e) => e[0] === "commit");
const underline = () => {
  const pts = [];
  for (let x = 50; x <= 350; x += 5) pts.push([x, 500 + 3 * Math.sin(x / 20)]);
  return pts;
};
const results = {};
const checks = [];
const check = (name, fn) => checks.push([name, fn]);

check("press on text", async () => {
  reset();
  api.pressHighlightTool(page, 0, at(150, 110));
  return { log, drawing: api.isDrawingInk() };
});

check("press on blank", async () => {
  reset();
  drag([[50, 400], [80, 430], [60, 470], [30, 440], [50, 400]]);
  await tick(); flushFrames();
  const live = previewD();
  const svg = layer.children[0];
  fire("mouseup", at(50, 400));
  return {
    live, view: svg.attrs.viewBox, opacity: svg.style.opacity, width: svg.children[0].attrs["stroke-width"],
    stroke: svg.children[0].style.stroke, place: [svg.style.left, svg.style.top, svg.style.width],
    commit: commits().map((c) => [c[1], c[2].length > 4, c[3]]), others: log.filter((e) => e[0] !== "commit"),
  };
});

check("alt over text", async () => {
  reset();
  drag([[150, 110], [250, 160], [150, 200]], { altKey: true });
  await tick();
  fire("mouseup", at(150, 200, { altKey: true }));
  return { texts: log.filter((e) => e[0] === "beginText").length, commits: commits().length };
});

check("no switch mid-drag", async () => {
  reset();
  drag([[50, 300], [150, 110], [250, 115], [280, 300]]);
  await tick();
  fire("mouseup", at(280, 300));
  return { others: log.filter((e) => e[0] !== "commit").length, commits: commits().length };
});

check("shift line", async () => {
  reset();
  drag([[50, 400], [120, 380], [200, 404]], { shiftKey: true });
  await tick(); flushFrames();
  const live = previewD();
  fire("mouseup", at(200, 404, { shiftKey: true }));
  return { live, commit: commits()[0].slice(2) };
});

check("straightened and eased", async () => {
  reset();
  const pts = underline();
  drag(pts);
  await tick(); flushFrames();
  fire("mouseup", at(...pts[pts.length - 1]));
  const [, , drawn, straight] = commits()[0];
  flushFrames(0); // the ease's first frame
  flushFrames(60);
  const mid = previewD();
  flushFrames(80);
  const end = previewD();
  const stillShown = layer.children.length;
  commitResolve(); await tick(); await api.settled();
  return { drawnPoints: drawn.length, straight, mid, end, stillShown, afterCommit: layer.children.length };
});

// The three ends of a commit: drawn, refused (nothing drawn), and failed.
const commitEnding = (finish) => async () => {
  reset();
  drag([[50, 400], [150, 420], [90, 470]]);
  await tick();
  fire("mouseup", at(90, 470));
  const shownWhileWriting = layer.children.length;
  const errors = [];
  const realError = console.error;
  console.error = (...args) => errors.push(args.map(String).join(" "));
  try {
    finish();
    await tick(); await api.settled();
  } finally {
    console.error = realError;
  }
  return { shownWhileWriting, preview: layer.children.length, failed: log.filter((e) => e[0] === "commitFailed").length, errors };
};
check("commit drawn", commitEnding(() => commitResolve(true)));
check("commit refused", commitEnding(() => commitResolve(false)));
check("commit failed", commitEnding(() => commitReject(new Error("bridge down"))));

check("reduced motion", async () => {
  reset({ reducedMotion: true });
  const pts = underline();
  drag(pts);
  await tick();
  while (frames.length) flushFrames();
  fire("mouseup", at(...pts[pts.length - 1]));
  return { d: previewD(), framesQueued: frames.length };
});

check("a curve", async () => {
  reset();
  const pts = [];
  for (let a = 0; a <= 360; a += 10) pts.push([300 + 60 * Math.cos(a * Math.PI / 180), 400 + 60 * Math.sin(a * Math.PI / 180)]);
  drag(pts);
  await tick();
  fire("mouseup", at(...pts[pts.length - 1]));
  return { straight: commits()[0][3] };
});

check("esc", async () => {
  reset();
  drag([[50, 400], [100, 450]]);
  await tick(); flushFrames();
  const cancelled = api.cancelInkStroke();
  fire("mouseup", at(100, 450));
  return { cancelled, commits: commits().length, preview: layer.children.length };
});

check("other button", async () => {
  reset();
  drag([[50, 400], [150, 420], [90, 470]]);
  await tick();
  fire("mouseup", { ...at(90, 470), button: 2 });
  const afterRight = { commits: commits().length, drawing: api.isDrawingInk() };
  fire("mouseup", { ...at(90, 470), button: 0 });
  return { afterRight, afterLeft: commits().length };
});

check("click", async () => {
  reset();
  drag([[50, 400], [50.5, 400.5]]);
  await tick();
  fire("mouseup", at(50.5, 400.5));
  return { log };
});

check("pending on text", async () => {
  reset({ known: false });
  drag([[150, 110], [200, 112]]);
  fire("mouseup", at(200, 112));
  await tick();
  return { log, preview: layer.children.length };
});

check("pending blank", async () => {
  reset({ known: false });
  drag([[50, 400], [150, 420], [90, 470]]);
  fire("mouseup", at(90, 470));
  await tick();
  return { commits: commits().length, others: log.filter((e) => e[0] !== "commit").length };
});

check("unread scan", async () => {
  reset({ ocr: "needed" });
  api.pressHighlightTool(page, 0, at(50, 400));
  return { log, altMode: api.chooseHighlightMode({ ocr: "needed" }, false, true) };
});

check("scan with no text", async () => {
  const modes = {};
  for (const ocr of ["no_text", "unavailable", "error", "running", "done"]) {
    reset({ ocr });
    api.pressHighlightTool(page, 0, at(50, 400));
    modes[ocr] = log.length ? "text" : (api.isDrawingInk() ? "ink" : "none");
    api.cancelInkStroke();
  }
  return modes;
});

check("on text", async () => {
  // Body text 10pt tall; on the OCR page also a "letter" as wide as a photo,
  // and a real word whose box OCR stretched down through a barcode.
  const glyphs = Array.from({ length: 20 }, (_, i) => ({ x0: 300 + i * 6, y0: 100, x1: 306 + i * 6, y1: 110 }));
  const photoLetter = { x0: 30, y0: 90, x1: 200, y1: 121 };
  const stretched = { x0: 230, y0: 200, x1: 236, y1: 300 };
  const chars = [...glyphs, photoLetter, stretched];
  const native = { ocr: null, chars };
  const scanned = { ocr: "done", chars };
  const on = (page, x, y) => api.isPointOnText(page, x, y);
  return {
    glyph: [on(native, 303, 105), on(scanned, 303, 105)],
    padded: [on(scanned, 299, 105), on(scanned, 298, 105)],
    photoLetter: [on(native, 100, 100), on(scanned, 100, 100)],
    stretched: [on(native, 233, 250), on(scanned, 233, 250)],
    blank: on(native, 500, 500),
  };
});

check("cursor", async () => {
  reset();
  api.noteInkHover(page, 0, at(50, 400));
  const brush = page.style.cursor;
  api.noteInkHover(page, 0, at(150, 110));
  const marker = page.style.cursor;
  api.noteInkHover(page, 0, at(150, 110, { altKey: true }));
  const altBrush = page.style.cursor;
  return { brush, marker, altBrush };
});

(async () => {
  for (const [name, fn] of checks) {
    try { results[name] = { ok: true, value: await fn() }; } catch (err) { results[name] = { ok: false, error: String((err && err.stack) || err) }; }
  }
  console.log(JSON.stringify(results));
})();
"""


@unittest.skipUnless(NODE, "Node is not installed")
class FreehandBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run(
            [NODE, "-e", HARNESS, str(GEOMETRY_JS), str(FREEHAND_JS)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        cls.results = json.loads(run.stdout)

    def value(self, name):
        result = self.results[name]
        self.assertTrue(result["ok"], result.get("error"))
        return result["value"]

    def test_a_press_on_text_is_a_text_highlight(self):
        v = self.value("press on text")
        self.assertEqual(v["log"], [["beginText", 0, 400, 270]])
        self.assertFalse(v["drawing"])

    def test_a_press_on_blank_paper_draws_a_stroke_in_page_points(self):
        v = self.value("press on blank")
        self.assertTrue(v["live"].startswith("M50 400L80 430"), v["live"])
        self.assertEqual(v["view"], "0 0 600 800")  # the page in points: 14pt at every zoom
        self.assertEqual((v["opacity"], v["width"], v["stroke"]), ("0.6", "14", "var(--highlight-pink)"))
        self.assertEqual(v["place"], ["100px", "50px", "1200px"])
        self.assertEqual(v["commit"], [[0, True, None]])
        self.assertEqual(v["others"], [])

    def test_alt_draws_over_text(self):
        self.assertEqual(self.value("alt over text"), {"texts": 0, "commits": 1})

    def test_the_mode_never_switches_mid_drag(self):
        self.assertEqual(self.value("no switch mid-drag"), {"others": 0, "commits": 1})

    def test_shift_draws_a_straight_line_snapped_to_the_axis_from_the_press(self):
        v = self.value("shift line")
        self.assertEqual(v["live"], "M50 400L200 400")
        self.assertEqual(v["commit"], [[[50, 400], [200, 400]], None])

    def test_a_nearly_straight_stroke_is_straightened_and_eased(self):
        v = self.value("straightened and eased")
        self.assertGreater(v["drawnPoints"], 2)
        (x0, y0), (x1, y1) = v["straight"]
        self.assertEqual(y0, y1)
        self.assertEqual((x0, x1), (50, 350))
        self.assertNotEqual(v["mid"], v["end"])
        self.assertEqual(len(re.findall("L", v["mid"])), 63)  # resampled for the ease
        self.assertEqual(v["end"], f"M50 {round(y0, 2):g}L350 {round(y1, 2):g}")
        self.assertEqual(v["stillShown"], 1, "kept until the page shows the real stroke")
        self.assertEqual(v["afterCommit"], 0)

    def test_a_stroke_that_was_not_drawn_says_so_and_the_preview_goes(self):
        drawn = self.value("commit drawn")
        self.assertEqual(drawn, {"shownWhileWriting": 1, "preview": 0, "failed": 0, "errors": []})
        refused = self.value("commit refused")
        self.assertEqual((refused["preview"], refused["failed"], refused["errors"]), (0, 1, []))
        failed = self.value("commit failed")
        self.assertEqual((failed["preview"], failed["failed"]), (0, 1))
        self.assertEqual(len(failed["errors"]), 1)
        self.assertIn("bridge down", failed["errors"][0])

    def test_reduced_motion_shows_the_line_at_once(self):
        v = self.value("reduced motion")
        self.assertEqual(len(re.findall("L", v["d"])), 1)
        self.assertEqual(v["framesQueued"], 0)

    def test_a_curve_stays_as_drawn(self):
        self.assertIsNone(self.value("a curve")["straight"])

    def test_esc_cancels_a_stroke(self):
        self.assertEqual(self.value("esc"), {"cancelled": True, "commits": 0, "preview": 0})

    def test_only_the_left_button_ends_a_stroke(self):
        v = self.value("other button")
        self.assertEqual(v["afterRight"], {"commits": 0, "drawing": True})
        self.assertEqual(v["afterLeft"], 1)

    def test_a_press_that_does_not_move_is_a_click(self):
        self.assertEqual(self.value("click")["log"], [["clickAt", 0, 50, 400]])

    def test_a_press_before_the_text_arrives_goes_the_way_it_says(self):
        on_text = self.value("pending on text")
        self.assertEqual(on_text["log"], [["beginText", 0, 400, 270], ["moveText", 500], ["endText", 500]])
        self.assertEqual(on_text["preview"], 0)
        self.assertEqual(self.value("pending blank"), {"commits": 1, "others": 0})

    def test_an_unread_scanned_page_is_a_text_press(self):
        v = self.value("unread scan")
        self.assertEqual(v["log"][0][0], "beginText")
        self.assertEqual(v["altMode"], "ink", "Alt draws there all the same")

    def test_a_scan_ocr_is_done_with_is_drawn_on_where_it_has_no_text(self):
        self.assertEqual(
            self.value("scan with no text"),
            {"no_text": "ink", "unavailable": "ink", "error": "text", "running": "text", "done": "ink"},
        )

    def test_ocr_boxes_count_as_text_only_when_shaped_like_glyphs(self):
        v = self.value("on text")
        self.assertEqual(v["glyph"], [True, True])
        self.assertEqual(v["padded"], [True, False])
        self.assertEqual(v["photoLetter"], [True, False], "a text layer's boxes are trusted as they are")
        self.assertEqual(v["stretched"], [True, False])
        self.assertFalse(v["blank"])

    def test_the_cursor_says_which_mode_a_press_would_be_in(self):
        v = self.value("cursor")
        # 14pt at a scale of 2 is a 28px brush, in an image 4px larger.
        self.assertIn("width%3D'32'", v["brush"])
        self.assertTrue(v["brush"].endswith(", crosshair"))
        self.assertIn("rotate(45", v["marker"].replace("%20", " "))
        self.assertTrue(v["marker"].endswith(", text"))
        self.assertEqual(v["altBrush"], v["brush"])


class WiringTests(unittest.TestCase):
    def test_the_scripts_load_before_the_reader(self):
        names = ("ink-geometry.js", "highlight-colors.js", "freehand.js", "reading.js")
        order = [READING_HTML.index(f'src="{name}"') for name in names]
        self.assertEqual(order, sorted(order))
        self.assertIn('id="inkLayer"', READING_HTML)

    def test_the_reader_presses_through_the_hybrid_tool(self):
        self.assertIn("pressHighlightTool(el, getPageIndex(), ev)", READING_JS)
        self.assertIn("isOnText: isPointOnText", READING_JS)
        self.assertIn('callApi("add_ink_stroke", pageIndex, drawn, straight)', READING_JS)
        # A stroke the bridge drew nothing for is reported, not just dropped.
        self.assertIn("if (state.id == null) return false;", READING_JS)
        self.assertIn("commitFailed: () => showToast(INK_NOT_DRAWN)", READING_JS)
        self.assertIn("const INK_NOT_DRAWN = \"Couldn't draw that\";", READING_JS)
        self.assertIn('setInkStyle({ width: highlightOptionsState.ink_width, opacity: inkOpacity })', OPTIONS_JS)
        self.assertRegex(READING_JS, r"else if \(isDrawingInk\(\)\) cancelInkStroke\(\);")
        self.assertIn('img.classList.toggle("selecting", highlightMode)', READING_JS)

    def test_the_preview_multiplies_and_drawings_are_targets(self):
        # On the layer: the layer is a stacking context, so a blend inside it
        # would not reach the page image.
        self.assertRegex(READING_CSS, r"\.ink-layer \{[^}]*mix-blend-mode: multiply")
        self.assertNotRegex(READING_CSS, r"\.ink-preview \{[^}]*mix-blend-mode")
        self.assertRegex(READING_CSS, r"\.ink-target-hit \{[^}]*pointer-events: stroke")
        self.assertIn('highlight.kind === "ink"', COLORS_JS)
        self.assertIn("distanceToPath([x, y], stroke)", COLORS_JS)
        # The editor opens under a drawing's box, which has no line boxes.
        self.assertIn('target.classList.contains("ink-target") ? [target]', COLORS_JS)
        self.assertIn("drawing", COLORS_JS)


if __name__ == "__main__":
    unittest.main()
