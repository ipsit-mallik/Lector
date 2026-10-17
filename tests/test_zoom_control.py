"""The reader's zoom: true size, the popover and the zoom modes
(docs/DESIGN_SYSTEM.md, "Zoom control").

* The backend: 100% is the paper's real size (96 CSS px per 72pt, so A4 is
  794px wide), the bitmap is drawn for the screen's pixel ratio within a pixel
  budget, the range is 25-400% kept to 0.01%, and a page's size in points.
* `frontend/pages/zoom-fit.js` under Node: the slider is logarithmic with
  100% at its centre; ticks at a quarter, half and three quarters; dragging
  snaps to 100% within 4% of it; Fit width and Fit page leave 24px either
  side at true size, rounded down, never past the range.
* `frontend/pages/zoom-control.js` under Node, with a stand-in DOM: opening
  focuses the slider; Fit width and Fit page are modes shown in the top bar
  and on their chip, close the popover and fit again when asked; the slider,
  a tick, 100% and leaving a fit end the mode; only a drag snaps; a
  double-click on the thumb resets to 100%; 100% leaves the popover open; a
  fit overtaken by a newer choice is dropped.
* Voice: "fit width", "fit page", "actual size" route in the reader only,
  are handled by reading.js and are in "What can I say?".
* The wiring and the look: Ctrl+0/1/2, Ctrl+scroll, the refit on resize and
  on a layout change, the page drawn at the screen's pixel ratio, the
  popover markup and its styling tokens.

The Node tests are skipped when Node is not installed.
"""
import base64
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import command_grammar as grammar  # noqa: E402
from lector.features.voice import reference, router  # noqa: E402

PAGES = ROOT / "frontend" / "pages"
FIT_JS = PAGES / "zoom-fit.js"
CONTROL_JS = PAGES / "zoom-control.js"
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
READING_HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
READING_CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
THEME_CSS = (ROOT / "frontend" / "shared" / "theme.css").read_text(encoding="utf-8")
NODE = shutil.which("node")

A4 = (595, 842)

FIT_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const run = (expr) => vm.runInContext(expr, context);
const toPct = run("zoomPctFromSlider"), toSlider = run("sliderFromZoomPct");
const fit = run("fitZoom"), label = run("zoomLabelText"), along = run("zoomTrackFraction"), snap = run("snapZoomPct");
const W = run("ZOOM_FIT_WIDTH"), P = run("ZOOM_FIT_PAGE");
// An A4 page (595 x 842 pt) in a 1000 x 700 area.
const a4 = { areaWidth: 1000, areaHeight: 700, pageWidth: 595, pageHeight: 842 };
const positions = Array.from({ length: 101 }, (_, i) => i);
console.log(JSON.stringify({
  ends: [toPct(0, 25, 400), toPct(50, 25, 400), toPct(100, 25, 400)],
  quarters: [toPct(25, 25, 400), toPct(75, 25, 400)],
  beyond: [toPct(-10, 25, 400), toPct(140, 25, 400)],
  back: [toSlider(25, 25, 400), toSlider(100, 25, 400), toSlider(400, 25, 400), toSlider(10, 25, 400), toSlider(900, 25, 400)],
  roundTrip: positions.every((p) => toPct(toSlider(toPct(p, 25, 400), 25, 400), 25, 400) === toPct(p, 25, 400)),
  rising: positions.slice(1).every((p) => toPct(p, 25, 400) >= toPct(p - 1, 25, 400)),
  ticks: run("ZOOM_TICKS").map((t) => along(t, 25, 400)),
  snap: [95, 96, 98, 100, 103.5, 104, 105, 50, 200].map(snap),
  fitWidth: fit(W, a4, 25, 400),
  fitPage: fit(P, a4, 25, 400),
  fitPageNarrow: fit(P, { ...a4, areaWidth: 400 }, 25, 400),
  fitWidthNarrowWindow: fit(W, { ...a4, areaWidth: 120 }, 25, 400),
  fitWidthHuge: fit(W, { ...a4, areaWidth: 4000 }, 25, 400),
  fitPageNoHeight: fit(P, { ...a4, areaHeight: 0 }, 25, 400),
  fitNoPage: fit(W, { ...a4, pageWidth: 0 }, 25, 400),
  labels: [label(W, 137), label(P, 62), label(null, 150), label(null, 119.93)],
}));
"""

CONTROL_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function fakeElement(id, parent = null) {
  const attrs = {};
  const listeners = {};
  const classes = new Set();
  const props = {};
  const el = {
    id, parent, textContent: "", value: "50", hidden: false, dataset: {}, children: [],
    style: { setProperty: (k, v) => { props[k] = v; }, getPropertyValue: (k) => props[k] },
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    setAttribute: (k, v) => { attrs[k] = String(v); },
    getAttribute: (k) => attrs[k],
    addEventListener: (type, fn) => { listeners[type] = fn; },
    fire: (type, ev = {}) => listeners[type] && listeners[type]({ target: el, button: 0, ...ev }),
    focus: () => { document.activeElement = el; },
    querySelectorAll: () => el.children,
    getBoundingClientRect: () => ({ left: 0, width: 216 }),
    contains(other) { for (let n = other; n; n = n.parent) if (n === this) return true; return false; },
  };
  return el;
}

const docListeners = {};
const document = {
  activeElement: null,
  addEventListener: (type, fn) => { docListeners[type] = fn; },
};
const label = fakeElement("zoomLabel");
const popover = fakeElement("zoomPopover");
popover.hidden = true;
const slider = fakeElement("zoomSlider", popover);
const ticks = fakeElement("zoomTicks", popover);
ticks.children = [50, 100, 200].map((z) => { const t = fakeElement(`tick${z}`, ticks); t.dataset.zoom = String(z); return t; });
const widthBtn = fakeElement("zoomFitWidthBtn", popover);
const pageBtn = fakeElement("zoomFitPageBtn", popover);
const actualBtn = fakeElement("zoomActualBtn", popover);
const page = fakeElement("pageArea");
const els = { zoomLabel: label, zoomPopover: popover, zoomSlider: slider, zoomTicks: ticks,
  zoomFitWidthBtn: widthBtn, zoomFitPageBtn: pageBtn, zoomActualBtn: actualBtn };
document.getElementById = (id) => els[id];

const context = vm.createContext({ document, console });
for (const file of process.argv.slice(1)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
vm.runInContext(
  "this.api = { configureZoomControl, updateZoomControl, chooseZoomFit, chooseActualSize, leaveZoomFit, " +
  "refitZoom, getZoomMode, isZoomPopoverOpen, openZoomPopover, closeZoomPopover, ZOOM_FIT_WIDTH, ZOOM_FIT_PAGE };",
  context,
);
const api = context.api;

const zoomed = [];
let closes = 0;
let geometry = { areaWidth: 1000, areaHeight: 700, pageWidth: 595, pageHeight: 842 };
let gate = null; // when set, fitGeometry waits on it
api.configureZoomControl({
  setZoomPct: async (pct) => { zoomed.push(pct); },
  fitGeometry: async () => { if (gate) await gate; return geometry; },
  onClose: () => { closes += 1; },
});
const state = (pct) => ({ zoom: pct / 100, zoom_pct: Math.round(pct), zoom_min_pct: 25, zoom_max_pct: 400 });
api.updateZoomControl(state(150));
const last = () => zoomed[zoomed.length - 1];
const tick = () => new Promise((r) => setImmediate(r));

const ui = () => ({
  label: label.textContent, aria: label.getAttribute("aria-label"),
  open: api.isZoomPopoverOpen(), expanded: label.getAttribute("aria-expanded"),
  slider: Number(slider.value), fill: slider.style.getPropertyValue("--zoom-fill"),
  valuetext: slider.getAttribute("aria-valuetext"),
  pressed: [widthBtn, pageBtn, actualBtn].map((b) => b.getAttribute("aria-pressed")),
  mode: api.getZoomMode(), focus: document.activeElement && document.activeElement.id,
  dragging: slider.classList.contains("is-dragging"),
});

(async () => {
  const out = {};
  out.start = ui();
  out.tickPlaces = ticks.children.map((t) => Number(t.style.getPropertyValue("--at")));

  label.fire("click");
  out.opened = ui();

  widthBtn.focus();
  widthBtn.fire("click");
  await tick();
  out.fitWidth = { ...ui(), zoomed: zoomed.slice(), closes };

  // The window narrows: asked to fit again, it follows.
  geometry = { ...geometry, areaWidth: 700 };
  await api.refitZoom();
  out.refitNarrower = { ...ui(), zoomed: zoomed.slice() };
  // Nothing moved: nothing asked of the backend.
  await api.refitZoom();
  out.refitSame = zoomed.length;

  await api.chooseZoomFit(api.ZOOM_FIT_PAGE);
  out.fitPage = { ...ui(), zoomed: zoomed.slice() };

  // A percentage ends the mode; afterwards a resize changes nothing.
  api.leaveZoomFit();
  geometry = { ...geometry, areaWidth: 1400, areaHeight: 1000 };
  await api.refitZoom();
  out.afterLeave = { ...ui(), zoomed: zoomed.length };

  // The slider: the mode ends and the value follows the thumb at once.
  await api.chooseZoomFit(api.ZOOM_FIT_WIDTH);
  label.fire("click");
  slider.value = "75";
  slider.fire("input");
  out.slider = { ...ui(), last: last() };

  // Dragging near 100% snaps there; the thumb is put on the centre.
  slider.fire("pointerdown");
  out.pressDragging = slider.classList.contains("is-dragging");
  slider.value = "49";
  slider.fire("input");
  out.dragSnap = { ...ui(), last: last() };
  slider.value = "51";
  slider.fire("input");
  out.dragSnapAbove = { ...ui(), last: last() };
  slider.value = "52";
  slider.fire("input");
  out.dragPastSnap = { ...ui(), last: last() };
  docListeners.pointerup({});
  out.released = slider.classList.contains("is-dragging");
  // The arrow keys (no pointer down) step across 100% without sticking.
  slider.value = "49";
  slider.fire("input");
  out.keyNoSnap = { ...ui(), last: last() };

  // A double-click on the thumb resets to 100%; elsewhere on the track it doesn't.
  slider.value = "75";
  slider.fire("input");
  const before = zoomed.length;
  slider.fire("dblclick", { clientX: 20 });
  out.dblclickOffThumb = zoomed.length - before;
  // The thumb's centre: 6 + 0.75 x (216 - 12) = 159.
  slider.fire("dblclick", { clientX: 160 });
  out.dblclickThumb = { ...ui(), last: last() };

  // A tick jumps to its zoom, and ends a fit.
  await api.chooseZoomFit(api.ZOOM_FIT_PAGE);
  label.fire("click");
  ticks.children[2].fire("click");
  out.tick200 = { ...ui(), last: last() };
  ticks.children[0].fire("click");
  out.tick50 = { ...ui(), last: last() };

  // 100% keeps the popover open.
  actualBtn.fire("click");
  await tick();
  out.actual = { ...ui(), last: last() };
  api.updateZoomControl(state(100));
  out.actualAnswered = ui();

  // Near 25%, two steps round to one percent: the thumb stays where it is put.
  slider.value = "2";
  slider.fire("input");
  api.updateZoomControl(state(last()));
  out.lowEnd = ui();

  // A pointer elsewhere closes it; on the value it is left to the value's click.
  docListeners.pointerdown({ target: label });
  out.pointerOnLabel = api.isZoomPopoverOpen();
  docListeners.pointerdown({ target: page });
  out.pointerElsewhere = api.isZoomPopoverOpen();

  // Tabbing out of it closes it, back onto the value does not.
  label.fire("click");
  popover.fire("focusout", { relatedTarget: label });
  out.focusToLabel = api.isZoomPopoverOpen();
  popover.fire("focusout", { relatedTarget: page });
  out.focusOut = api.isZoomPopoverOpen();

  // Esc's close hands focus back to the value.
  label.fire("click");
  api.closeZoomPopover({ returnFocus: true });
  out.escClose = ui();

  // A fit still measuring when the reader picks a percentage is dropped.
  const count = zoomed.length;
  let release;
  gate = new Promise((r) => { release = r; });
  geometry = { ...geometry, areaWidth: 500 };
  const pending = api.chooseZoomFit(api.ZOOM_FIT_WIDTH);
  api.leaveZoomFit();
  release();
  await pending;
  out.stale = { zoomed: zoomed.length - count, mode: api.getZoomMode() };

  console.log(JSON.stringify(out));
})();
"""


def png_size(b64: str) -> tuple[int, int]:
    return struct.unpack(">II", base64.b64decode(b64)[16:24])


@unittest.skipUnless(NODE, "Node is not installed")
class ZoomFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([NODE, "-e", FIT_HARNESS, str(FIT_JS)], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_the_slider_is_logarithmic_with_100_percent_at_its_centre(self):
        self.assertEqual(self.out["ends"], [25, 100, 400])
        # Each quarter of the track is a doubling.
        self.assertEqual(self.out["quarters"], [50, 200])
        self.assertTrue(self.out["rising"])

    def test_the_slider_stays_inside_its_range(self):
        self.assertEqual(self.out["beyond"], [25, 400])
        self.assertEqual(self.out["back"], [0, 50, 100, 0, 100])

    def test_a_percentage_and_its_thumb_agree_both_ways(self):
        self.assertTrue(self.out["roundTrip"])

    def test_the_ticks_fall_at_a_quarter_a_half_and_three_quarters(self):
        self.assertEqual(self.out["ticks"], [0.25, 0.5, 0.75])

    def test_only_the_neighbourhood_of_100_percent_snaps(self):
        self.assertEqual(self.out["snap"], [95, 100, 100, 100, 100, 100, 105, 50, 200])

    def test_fit_width_leaves_24px_either_side_at_true_size(self):
        # (1000 - 48 - 1) / (595 x 96/72) = 1.19874 -> 1.1987, rounded down.
        self.assertEqual(self.out["fitWidth"], 1.1987)

    def test_fit_page_fits_the_height_too(self):
        # min(1.1987, (700 - 49) / (842 x 96/72) = 0.57987) -> 0.5798.
        self.assertEqual(self.out["fitPage"], 0.5798)
        # Narrow enough and the width is what limits it: (400 - 49) / 793.33.
        self.assertEqual(self.out["fitPageNarrow"], 0.4424)

    def test_a_fit_never_leaves_the_zoom_range(self):
        self.assertEqual(self.out["fitWidthNarrowWindow"], 0.25)
        self.assertEqual(self.out["fitWidthHuge"], 4.0)

    def test_nothing_to_measure_is_no_fit(self):
        self.assertIsNone(self.out["fitPageNoHeight"])
        self.assertIsNone(self.out["fitNoPage"])

    def test_the_top_bar_names_the_mode_or_a_whole_percentage(self):
        self.assertEqual(self.out["labels"], ["Fit width", "Fit page", "150%", "120%"])


@unittest.skipUnless(NODE, "Node is not installed")
class ZoomControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [NODE, "-e", CONTROL_HARNESS, str(FIT_JS), str(CONTROL_JS)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_it_starts_as_a_percentage_with_the_popover_shut(self):
        start = self.out["start"]
        self.assertEqual((start["label"], start["aria"], start["open"]), ("150%", "Zoom: 150%", False))
        self.assertEqual(start["pressed"], ["false", "false", "false"])
        self.assertIsNone(start["mode"])

    def test_the_ticks_are_placed_along_the_track(self):
        self.assertEqual(self.out["tickPlaces"], [0.25, 0.5, 0.75])

    def test_opening_moves_focus_to_the_slider(self):
        opened = self.out["opened"]
        self.assertEqual((opened["open"], opened["expanded"], opened["focus"]), (True, "true", "zoomSlider"))

    def test_fit_width_is_a_mode_shown_on_the_value_and_its_chip_and_closes_the_popover(self):
        fit = self.out["fitWidth"]
        self.assertEqual(fit["label"], "Fit width")
        self.assertEqual(fit["pressed"], ["true", "false", "false"])
        self.assertEqual(fit["zoomed"], [119.87])
        self.assertFalse(fit["open"])
        self.assertEqual(fit["closes"], 1)
        # Chosen from inside the popover, focus goes back to the value.
        self.assertEqual(fit["focus"], "zoomLabel")

    def test_a_fit_mode_fits_again_when_the_space_changes(self):
        narrower = self.out["refitNarrower"]
        # (700 - 49) / 793.33 = 0.82059 -> 82.05%.
        self.assertEqual(narrower["zoomed"][-1], 82.05)
        self.assertEqual(narrower["label"], "Fit width")
        self.assertEqual(self.out["refitSame"], len(narrower["zoomed"]))

    def test_fit_page(self):
        fit = self.out["fitPage"]
        self.assertEqual((fit["label"], fit["pressed"]), ("Fit page", ["false", "true", "false"]))
        self.assertEqual(fit["zoomed"][-1], 57.98)

    def test_a_percentage_ends_the_mode(self):
        after = self.out["afterLeave"]
        self.assertIsNone(after["mode"])
        self.assertEqual(after["label"], "58%")
        self.assertEqual(after["zoomed"], len(self.out["fitPage"]["zoomed"]))

    def test_moving_the_slider_ends_the_mode_and_updates_the_value_at_once(self):
        slider = self.out["slider"]
        self.assertIsNone(slider["mode"])
        self.assertEqual((slider["label"], slider["last"], slider["valuetext"]), ("200%", 200, "200%"))
        self.assertEqual(slider["fill"], "0.75")
        self.assertTrue(slider["open"])

    def test_a_drag_near_100_percent_snaps_to_it(self):
        self.assertTrue(self.out["pressDragging"])
        for key in ("dragSnap", "dragSnapAbove"):
            snapped = self.out[key]
            self.assertEqual((snapped["label"], snapped["last"], snapped["slider"]), ("100%", 100, 50), key)
            self.assertTrue(snapped["dragging"])
        past = self.out["dragPastSnap"]
        # Position 52 is 106%, past the 4% either side of 100%.
        self.assertEqual((past["label"], past["last"]), ("106%", 106))
        self.assertFalse(self.out["released"])

    def test_the_arrow_keys_are_not_snapped(self):
        self.assertEqual((self.out["keyNoSnap"]["label"], self.out["keyNoSnap"]["last"]), ("97%", 97))

    def test_a_double_click_on_the_thumb_resets_to_100_percent(self):
        self.assertEqual(self.out["dblclickOffThumb"], 0)
        reset = self.out["dblclickThumb"]
        self.assertEqual((reset["label"], reset["last"], reset["slider"]), ("100%", 100, 50))

    def test_a_tick_jumps_to_its_zoom_and_ends_a_fit(self):
        self.assertEqual((self.out["tick200"]["label"], self.out["tick200"]["last"]), ("200%", 200))
        self.assertIsNone(self.out["tick200"]["mode"])
        self.assertEqual((self.out["tick50"]["label"], self.out["tick50"]["slider"]), ("50%", 25))

    def test_100_percent_keeps_the_popover_open_and_is_selected(self):
        actual = self.out["actual"]
        self.assertEqual((actual["last"], actual["label"], actual["open"]), (100, "100%", True))
        self.assertEqual(actual["pressed"], ["false", "false", "true"])
        self.assertEqual(self.out["actualAnswered"]["slider"], 50)

    def test_the_thumb_is_not_snapped_back_near_the_bottom_of_the_range(self):
        self.assertEqual(self.out["lowEnd"]["slider"], 2)
        self.assertEqual(self.out["lowEnd"]["label"], "26%")

    def test_a_click_elsewhere_closes_it(self):
        self.assertTrue(self.out["pointerOnLabel"])
        self.assertFalse(self.out["pointerElsewhere"])

    def test_tabbing_out_closes_it(self):
        self.assertTrue(self.out["focusToLabel"])
        self.assertFalse(self.out["focusOut"])

    def test_esc_returns_focus_to_the_value(self):
        esc = self.out["escClose"]
        self.assertEqual((esc["open"], esc["expanded"], esc["focus"]), (False, "false", "zoomLabel"))

    def test_a_fit_overtaken_by_a_percentage_is_dropped(self):
        self.assertEqual(self.out["stale"], {"zoomed": 0, "mode": None})


class ZoomBackendTests(unittest.TestCase):
    def setUp(self):
        settings_dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        self.addCleanup(settings_dir.cleanup)
        patcher = mock.patch.object(store, "_settings_path", lambda: Path(settings_dir.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)

        pdf_dir = tempfile.TemporaryDirectory(prefix="lector-pdfs-")
        self.addCleanup(pdf_dir.cleanup)
        path = str(Path(pdf_dir.name) / "mixed.pdf")
        doc = fitz.open()
        doc.new_page(width=A4[0], height=A4[1])
        doc.new_page(width=A4[1], height=A4[0])
        doc.save(path)
        doc.close()

        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)
        self.state = self.api.open_pdf(path)

    def test_a_new_pdf_opens_at_100_percent(self):
        self.assertEqual((self.state["zoom"], self.state["zoom_pct"]), (1.0, 100))

    def test_100_percent_is_the_papers_real_size(self):
        # A4 is 8.27 in wide: 595pt / 72 x 96 = 793.3 CSS px, a box of 794
        # (rounded up, as PyMuPDF sizes the bitmap, so at 1x it is drawn 1:1).
        page = self.api.get_page_image(0)
        self.assertEqual((page["width"], page["height"]), (794, 1123))
        self.assertEqual(png_size(page["image"]), (794, 1123))
        self.assertEqual(self.api.get_page_sizes(), [{"width": 794, "height": 1123}, {"width": 1123, "height": 794}])

    def test_the_bitmap_is_drawn_for_the_screens_pixel_ratio(self):
        page = self.api.get_page_image(0, 1.5)
        # Shown at the same CSS size, with 1.5x the pixels.
        self.assertEqual((page["width"], page["height"]), (794, 1123))
        self.assertEqual(png_size(page["image"]), (1190, 1684))

    def test_a_huge_bitmap_is_kept_to_the_pixel_budget(self):
        self.api.set_zoom(4)
        page = self.api.get_page_image(0, 3)
        width, height = png_size(page["image"])
        self.assertEqual((page["width"], page["height"]), (3174, 4491))
        self.assertLessEqual(width * height, self.api._doc.MAX_RENDER_PIXELS * 1.01)
        self.assertGreater(width, page["width"])

    def test_every_zoom_is_scaled_from_true_size(self):
        self.api.set_zoom(0.5)
        self.assertEqual(self.api.get_page_sizes()[0], {"width": 397, "height": 562})

    def test_the_range_is_25_to_400_percent(self):
        state = self.api.set_zoom(0.1)
        self.assertEqual((state["zoom_pct"], state["zoom_min_pct"], state["zoom_max_pct"]), (25, 25, 400))
        self.assertEqual(self.api.set_zoom(9)["zoom_pct"], 400)

    def test_a_fit_is_kept_to_a_hundredth_of_a_percent(self):
        state = self.api.set_zoom(1.199374)
        self.assertEqual((state["zoom"], state["zoom_pct"]), (1.1994, 120))

    def test_zooming_out_reaches_25_percent(self):
        self.api.set_zoom(0.5)
        self.assertEqual(self.api.zoom_out()["zoom_pct"], 25)
        self.assertEqual(self.api.zoom_out()["zoom_pct"], 25)

    def test_a_pages_size_in_points_whatever_the_zoom(self):
        self.api.set_zoom(0.37)
        self.assertEqual(self.api.get_page_size_points(0), {"width": 595, "height": 842})
        self.assertEqual(self.api.get_page_size_points(1), {"width": 842, "height": 595})
        # Past either end: the nearest real page.
        self.assertEqual(self.api.get_page_size_points(7), {"width": 842, "height": 595})
        self.assertEqual(self.api.get_page_size_points(-1), {"width": 595, "height": 842})


class ZoomVoiceTests(unittest.TestCase):
    PHRASES = {
        "fit width": grammar.FIT_WIDTH, "fit to width": grammar.FIT_WIDTH,
        "fit page": grammar.FIT_PAGE, "fit to page": grammar.FIT_PAGE,
        "actual size": grammar.ACTUAL_SIZE,
    }

    def test_they_route_in_the_reader_only(self):
        for phrase, intent in self.PHRASES.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(router.resolve(router.READING, phrase)["command"]["intent"], intent)
                self.assertIsNone(router.resolve(router.HOME, phrase)["command"])

    def test_they_do_not_take_the_page_turns(self):
        self.assertEqual(grammar.parse("next page")["intent"], grammar.NEXT_PAGE)
        self.assertEqual(grammar.parse("page three")["intent"], grammar.GOTO_PAGE)

    def test_the_recognizer_can_hear_them(self):
        for word in ("fit", "width", "to", "page", "actual", "size"):
            self.assertIn(word, grammar.VOCABULARY)

    def test_reading_js_acts_on_them(self):
        self.assertIn("FIT_WIDTH: () => chooseZoomFit(ZOOM_FIT_WIDTH)", READING_JS)
        self.assertIn("FIT_PAGE: () => chooseZoomFit(ZOOM_FIT_PAGE)", READING_JS)
        self.assertIn("ACTUAL_SIZE: () => chooseActualSize()", READING_JS)

    def test_listed_in_what_can_i_say_with_their_keys(self):
        zoom = next(s for s in reference.sections(router.READING) if s["id"] == "zoom")
        text = json.dumps(zoom)
        for phrase in self.PHRASES:
            self.assertIn(phrase, text.lower())
        for key in ("Ctrl+0", "Ctrl+1", "Ctrl+2"):
            self.assertIn(key, text)


class ZoomWiringTests(unittest.TestCase):
    def test_the_acrobat_shortcuts(self):
        self.assertIn("0: () => chooseZoomFit(ZOOM_FIT_PAGE)", READING_JS)
        self.assertIn("1: () => chooseActualSize()", READING_JS)
        self.assertIn("2: () => chooseZoomFit(ZOOM_FIT_WIDTH)", READING_JS)
        # Ahead of the guard that skips keys typed into inputs, as Ctrl+= is.
        self.assertLess(READING_JS.index("const zoomShortcut ="), READING_JS.index('if (ev.target.tagName === "INPUT") return;'))

    def test_ctrl_scroll_zooms_and_ends_a_fit(self):
        self.assertIn('window.addEventListener("wheel"', READING_JS)
        self.assertIn("{ passive: false }", READING_JS)
        body = READING_JS[READING_JS.index("function applyWheelZoom()"):]
        self.assertIn("leaveZoomFit();", body[:body.index("\n}\n")])

    def test_the_steps_end_a_fit(self):
        for name in ("zoomIn", "zoomOut"):
            body = READING_JS[READING_JS.index(f"async function {name}()"):]
            self.assertIn("leaveZoomFit();", body[:body.index("\n}\n")])

    def test_a_fit_follows_the_page_area_and_the_layout(self):
        self.assertIn("}).observe(pageArea);", READING_JS)
        body = READING_JS[READING_JS.index("async function toggleLayout()"):]
        self.assertIn("refitZoom();", body[:body.index("\n}\n")])

    def test_pages_are_drawn_for_the_screens_pixel_ratio(self):
        self.assertEqual(READING_JS.count('callApi("get_page_image", '), 3)
        self.assertEqual(READING_JS.count("devicePixelRatio)"), 3)
        self.assertIn("matchMedia(`(resolution: ${devicePixelRatio}dppx)`)", READING_JS)

    def test_the_popover_hangs_from_the_value(self):
        value = READING_HTML[READING_HTML.index('<div class="zoom-value">'):READING_HTML.index('id="zoomInBtn"')]
        for marker in ('id="zoomLabel"', 'aria-haspopup="dialog"', 'aria-expanded="false"',
                       'id="zoomPopover" role="dialog"', 'id="zoomSlider" type="range" min="0" max="100"',
                       'id="zoomFitWidthBtn"', 'id="zoomFitPageBtn"', 'id="zoomActualBtn"'):
            self.assertIn(marker, value)
        self.assertLess(value.index('id="zoomFitWidthBtn"'), value.index('id="zoomFitPageBtn"'))
        self.assertLess(value.index('id="zoomFitPageBtn"'), value.index('id="zoomActualBtn"'))

    def test_the_ticks_and_their_tooltips(self):
        ticks = READING_HTML[READING_HTML.index('id="zoomTicks"'):READING_HTML.index('<div class="zoom-chips"')]
        for zoom, tip in (("50", "50%"), ("100", "Reset to 100%"), ("200", "200%")):
            self.assertRegex(ticks, rf'tabindex="-1" data-zoom="{zoom}"\s+data-tooltip="{tip}"')
        self.assertIn('class="zoom-tick zoom-tick--major"', ticks)

    def test_the_scripts_load_before_reading_js(self):
        order = [READING_HTML.index(f'<script src="{name}"></script>') for name in ("zoom-fit.js", "zoom-control.js", "reading.js")]
        self.assertEqual(order, sorted(order))


class ZoomLookTests(unittest.TestCase):
    def rule(self, selector):
        start = READING_CSS.index(selector + " {")
        return READING_CSS[start:READING_CSS.index("}", start)]

    def test_it_overlays_the_page_without_moving_it(self):
        popover = self.rule(".zoom-popover")
        for decl in ("position: absolute;", "right: 0;", "width: 250px;", "gap: 10px;",
                     "padding: var(--space-3);", "border-radius: var(--radius-lg);",
                     "background-color: var(--popover-surface);", "box-shadow: var(--popover-shadow);"):
            self.assertIn(decl, popover)

    def test_it_fades_and_drops_in(self):
        self.assertIn("animation: popover-drop var(--dur-quick) var(--ease-out);", self.rule(".zoom-popover"))
        self.assertRegex(READING_CSS, r"@keyframes popover-drop \{\s*from \{ opacity: 0; transform: translateY\(var\(--drop-distance\)\); \}")
        self.assertIn("--dur-quick: 140ms;", THEME_CSS)
        self.assertIn("--drop-distance: -4px;", THEME_CSS)
        reduced = THEME_CSS[THEME_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("--drop-distance: 0px;", reduced[:reduced.index("}")])

    def test_the_caret(self):
        caret = self.rule(".zoom-popover::before")
        for decl in ("width: 10px;", "height: 10px;", "transform: rotate(45deg);", "background-color: var(--popover-surface);"):
            self.assertIn(decl, caret)

    def test_dark_rises_by_lightness_and_a_top_highlight(self):
        dark = THEME_CSS[THEME_CSS.index('[data-theme="dark"] {'):THEME_CSS.index('[data-theme="sepia"] {')]
        self.assertIn("--popover-surface: var(--surface-input);", dark)
        self.assertIn("--popover-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);", dark)
        light = THEME_CSS[THEME_CSS.index('[data-theme="light"] {'):THEME_CSS.index('[data-theme="dark"] {')]
        self.assertIn("--popover-shadow: var(--shadow-2);", light)

    def test_the_track_is_filled_up_to_the_thumb(self):
        track = self.rule(".zoom-slider::-webkit-slider-runnable-track")
        self.assertIn("height: 4px;", track)
        self.assertIn("var(--accent) 0 var(--zoom-fill-at)", track)
        self.assertIn("var(--border-strong) var(--zoom-fill-at) 100%", track)

    def test_the_thumb_sits_over_the_ticks_with_a_20px_hit_area(self):
        self.assertIn("z-index: 1;", self.rule(".zoom-slider"))
        self.assertIn("height: 20px;", self.rule(".zoom-slider"))
        self.assertIn("z-index: 0;", self.rule(".zoom-ticks"))
        self.assertIn("isolation: isolate;", self.rule(".zoom-slider-wrap"))
        self.assertIn("margin-top: calc((4px - var(--zoom-thumb)) / 2);", self.rule(".zoom-slider::-webkit-slider-thumb"))

    def test_the_thumb_is_the_popovers_colour_with_an_accent_ring(self):
        thumb = self.rule(".zoom-slider::-webkit-slider-thumb")
        for decl in ("width: var(--zoom-thumb);", "border-radius: 50%;",
                     "background-color: var(--popover-surface);", "border: 2px solid var(--accent);",
                     "box-shadow: var(--zoom-thumb-shadow);"):
            self.assertIn(decl, thumb)
        self.assertIn("--zoom-thumb: 12px;", self.rule(".zoom-slider-wrap"))
        self.assertIn("--zoom-thumb-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);", self.rule(".zoom-slider-wrap"))
        self.assertIn("--zoom-halo: 0 0 0 5px color-mix(in srgb, var(--accent) 20%, transparent);", self.rule(".zoom-slider-wrap"))

    def test_hover_drag_and_focus(self):
        self.assertIn("transform: scale(1.15);", self.rule(".zoom-slider::-webkit-slider-thumb:hover"))
        drag = self.rule(".zoom-slider.is-dragging::-webkit-slider-thumb")
        self.assertIn("transform: scale(1.25);", drag)
        self.assertIn("var(--zoom-halo)", drag)
        self.assertIn("var(--zoom-halo)", self.rule(".zoom-slider:focus-visible::-webkit-slider-thumb"))

    def test_the_tick_lines(self):
        tick = self.rule(".zoom-tick")
        self.assertIn("width: 14px;", tick)
        self.assertIn("height: 12px;", tick)
        minor = self.rule(".zoom-tick::before")
        for decl in ("width: 2px;", "height: 5px;", "background-color: var(--text-muted);", "opacity: 0.45;"):
            self.assertIn(decl, minor)
        major = self.rule(".zoom-tick--major::before")
        for decl in ("height: 8px;", "background-color: var(--text-primary);", "opacity: 0.7;"):
            self.assertIn(decl, major)

    def test_the_chips(self):
        chip = self.rule(".zoom-chip")
        self.assertIn("flex: 1;", chip)
        self.assertIn("border-radius: var(--radius-md);", chip)
        active = self.rule('.zoom-chip[aria-pressed="true"]')
        for decl in ("background-color: var(--accent-soft);", "color: var(--accent-strong);", "font-weight: 600;"):
            self.assertIn(decl, active)


if __name__ == "__main__":
    unittest.main()
