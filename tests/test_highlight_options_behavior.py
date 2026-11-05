"""The highlight tool's split button and cards (`frontend/pages/highlight-
options.js` on the shared `range-slider.js`) under Node, with a stand-in DOM,
plus their markup and look.

What is held here:

* **The opacity slider** has a tick every 10% from 10 to 100; the tick at the
  style's default is the emphasised one and moves when the style changes;
  a click on a tick jumps there; a drag settles on the default within ±3%
  and nowhere else; the arrow keys never snap and Page Up/Down move 10; a
  double-click on the thumb (and only on the thumb) resets to the default.
  The setting is written once the slider rests or the drag ends, and before
  the card closes.
* **The thickness presets** are drawn proportional to their width (36pt is
  16px, 4pt 2px at the least), each named "<n> pt", the chosen one marked,
  the arrows choosing as they move; a pick is written and reaches the
  freehand preview.
* **The style** changes the tool's icon, name and tooltip key, the style
  card's check, the opacity shown, the preview line and the release wash's
  opacity, and is written.
* **The split button**: the badge (and a right-click on the tool) opens the
  style card on the chosen style; the arrows move, a row or its letter
  chooses (tool on, card closed), Esc closes back to the badge; a press
  outside closes it. One card at a time.
* **The keys** H, U, S: that style with the tool on, or off when it already
  is; reading.js only lets them through outside text fields and dialogs.
* **Voice's helpers**: an opacity written at once, a thickness one preset
  along and no further.
* **The look** is the zoom popover's surface, the cards' sizes and rows.

Skipped (the Node part) when Node is not installed.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "frontend" / "pages"
SLIDER_JS = PAGES / "range-slider.js"
OPTIONS_JS = PAGES / "highlight-options.js"
READING_HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
READING_CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const docListeners = {};
const document = {
  activeElement: null,
  documentElement: null,
  addEventListener: (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn); },
  createElement: (tag) => fakeElement(tag),
};

function fakeElement(tag = "div", id = "") {
  const classes = new Set();
  const props = {};
  const el = {
    tagName: tag.toUpperCase(), id, children: [], parent: null, hidden: false, textContent: "",
    value: "", min: "", max: "", step: "", type: "", tabIndex: 0, title: "",
    dataset: {}, attrs: {}, listeners: {},
    rect: { left: 0, top: 0, width: 212, height: 20 },
    style: { setProperty: (k, v) => { props[k] = v; }, getPropertyValue: (k) => props[k] },
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, force) => { const on = force === undefined ? !classes.has(c) : force; on ? classes.add(c) : classes.delete(c); return on; },
    },
    get className() { return [...classes].join(" "); },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get parentElement() { return el.parent; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    fire(type, ev = {}) {
      const event = { target: el, button: 0, key: "", repeat: false, defaultPrevented: false,
                      stopPropagation() {}, preventDefault() { this.defaultPrevented = true; }, ...ev };
      (this.listeners[type] || []).forEach((fn) => fn(event));
      return event;
    },
    click() { return el.fire("click"); },
    append(...kids) { kids.forEach((k) => { k.parent = el; el.children.push(k); }); },
    replaceChildren(...kids) { el.children = []; el.append(...kids); },
    contains(other) { for (let n = other; n; n = n.parent) if (n === el) return true; return false; },
    focus() { document.activeElement = el; },
    getBoundingClientRect() { return el.rect; },
    matches(sel) { return matchOne(el, sel); },
    querySelectorAll(sel) { const out = []; walk(el, (n) => { if (n !== el && matchOne(n, sel)) out.push(n); }); return out; },
    querySelector(sel) { return el.querySelectorAll(sel)[0] || null; },
  };
  return el;
}

function matchOne(el, sel) {
  const [tag, ...cls] = sel.split(".");
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  return cls.every((c) => el.classList.contains(c));
}
function walk(el, fn) { fn(el); el.children.forEach((c) => walk(c, fn)); }
const fireDoc = (type, ev) => (docListeners[type] || []).forEach((fn) => fn({ button: 0, ...ev }));

document.documentElement = fakeElement("html");
const els = {};
for (const id of ["highlightBtn", "highlightStyleBtn", "highlightColorBtn", "highlightPreviewLine", "highlightStyleCard",
                  "highlightOptions", "highlightOptionsSwatches", "highlightOpacityControl", "highlightThicknessGroup"]) {
  els[id] = fakeElement(id.endsWith("Btn") ? "button" : "div", id);
}
// The rail item the cards are placed in: the tool at its top, the preview
// button 10px under the tool's 40px.
const rail = fakeElement("div", "railHighlight");
rail.rect = { left: 0, top: 100, width: 40, height: 74 };
els.highlightBtn.rect = { left: 0, top: 100, width: 40, height: 40 };
els.highlightColorBtn.rect = { left: 0, top: 150, width: 40, height: 24 };
rail.append(els.highlightBtn, els.highlightStyleBtn, els.highlightColorBtn, els.highlightStyleCard, els.highlightOptions);
els.highlightColorBtn.append(els.highlightPreviewLine);
els.highlightStyleCard.hidden = true;
els.highlightOptions.hidden = true;
els.highlightBtn.dataset.icon = "highlight";
for (const id of ["highlightOptionsSwatches", "highlightOpacityControl", "highlightThicknessGroup"]) {
  els.highlightOptions.append(els[id]);
}
for (const style of ["highlight", "underline", "strikethrough"]) {
  const item = fakeElement("button");
  item.className = "style-item";
  item.dataset.style = style;
  els.highlightStyleCard.append(item);
}
document.getElementById = (id) => els[id];
const outside = fakeElement("div", "page");

let timers = [];
const calls = [];
const icons = [];
const radio = {};
let inkStyle = null;
let editorClosed = 0;
const OPTIONS = {
  style: "highlight", styles: ["highlight", "underline", "strikethrough"],
  opacity: { highlight: 60, underline: 100, strikethrough: 100 },
  default_opacity: { highlight: 60, underline: 100, strikethrough: 100 },
  opacity_min: 10, opacity_max: 100, opacity_snap: 3, opacity_tick_step: 10,
  ink_width: 14, ink_widths: [4, 8, 14, 24, 36],
};
let options = JSON.parse(JSON.stringify(OPTIONS));
const tool = { on: false };

const context = vm.createContext({
  document, console, Promise, JSON,
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  clearTimeout: () => { timers = []; },
  callApi: async (name, ...args) => {
    calls.push([name, ...args]);
    if (name === "set_highlight_style") options.style = args[0];
    if (name === "set_highlight_opacity") options.opacity[args[0]] = args[1];
    if (name === "set_ink_width") options.ink_width = args[0];
    if (name === "set_highlight_color") return { highlight_color: args[0] };
    return JSON.parse(JSON.stringify(options));
  },
  initRadioCards: (group, opts = {}) => { radio[group.id] = opts.selectOnArrow !== false; },
  mountIcon: (el, name) => { icons.push(name); },
  mountIcons: async () => {},
  buildSwatches: async () => {},
  markSwatch: () => {},
  swatchColor: (name) => `var(--highlight-${name})`,
  highlightPalette: ["yellow", "green", "blue", "pink", "orange"],
  closeHighlightEditor: () => { editorClosed += 1; },
  initHighlightEditorControls: () => {},
  setInkStyle: (style) => { inkStyle = style; },
});
for (const file of process.argv.slice(1)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
const run = (expr) => vm.runInContext(expr, context);
const tick = () => new Promise((r) => setImmediate(r));
const preview = () => ({ height: els.highlightPreviewLine.style.getPropertyValue("--preview-height"),
                         opacity: els.highlightPreviewLine.style.getPropertyValue("--preview-opacity") });

(async () => {
  const out = {};
  run("configureHighlightTool")({ isOn: () => tool.on, setOn: (on) => { tool.on = on; } });
  await run("initHighlightOptions")(JSON.parse(JSON.stringify(OPTIONS)), "yellow");
  const control = els.highlightOpacityControl;
  const input = control.querySelector("input");
  const ticks = control.querySelectorAll(".range-tick");
  const major = () => control.querySelectorAll(".range-tick--major").map((t) => t.dataset.value);
  const shown = () => ({ value: Number(input.value), text: control.querySelector("output").textContent });
  const presets = els.highlightThicknessGroup.querySelectorAll(".thickness-preset");
  const items = els.highlightStyleCard.querySelectorAll(".style-item");
  const checked = () => items.filter((i) => i.getAttribute("aria-checked") === "true").map((i) => i.dataset.style);

  out.ticks = ticks.map((t) => [Number(t.dataset.value), Number(t.style.getPropertyValue("--at")).toFixed(3)]);
  out.head = control.children.map((c) => c.className);
  out.major = major();
  out.initial = shown();
  out.tool = { title: els.highlightBtn.title, label: els.highlightBtn.getAttribute("aria-label") };
  out.checked = checked();
  out.preview = preview();
  out.presets = presets.map((p) => [p.dataset.width, p.children[0].style.height, p.getAttribute("aria-label"), p.dataset.tooltip]);
  out.presetSelected = presets.filter((p) => p.classList.contains("selected")).map((p) => p.dataset.width);
  out.radio = radio;
  out.inkStyle = { ...inkStyle };
  out.appliedOpacity = document.documentElement.style.getPropertyValue("--applied-opacity");

  // A drag: snaps within 3 of the default, nowhere else; written once, on release.
  calls.length = 0;
  input.fire("pointerdown");
  const dragged = [];
  for (const v of [62, 64, 57, 56]) { input.value = String(v); input.fire("input"); dragged.push(Number(input.value)); }
  out.dragged = dragged;
  out.savesBeforeRelease = calls.filter((c) => c[0] === "set_highlight_opacity").length;
  fireDoc("pointerup", {});
  out.savesAfterRelease = calls.filter((c) => c[0] === "set_highlight_opacity");
  out.inkAfterDrag = inkStyle.opacity;
  out.previewAfterDrag = preview();

  // The arrow keys: no snap; written once the slider rests.
  calls.length = 0;
  input.fire("keydown", { key: "ArrowRight" });
  input.value = "61"; input.fire("input");
  out.keyed = Number(input.value);
  timers.splice(0).forEach((fn) => fn());
  out.keyedSaves = calls.filter((c) => c[0] === "set_highlight_opacity");

  // Page Up / Page Down: ten at a time, never past the ends.
  const paged = [];
  for (const key of ["PageUp", "PageUp", "PageUp", "PageUp", "PageDown"]) {
    const ev = input.fire("keydown", { key });
    paged.push([Number(input.value), ev.defaultPrevented]);
  }
  out.paged = paged;
  timers.splice(0).forEach((fn) => fn());

  // A tick, then a double-click on the thumb (and one beside it).
  ticks.find((t) => t.dataset.value === "30").fire("click");
  out.afterTick = shown();
  input.fire("dblclick", { clientX: 150 });
  out.afterDblclickOffThumb = Number(input.value);
  input.fire("dblclick", { clientX: 6 + (20 / 90) * 200 });
  out.afterDblclickThumb = Number(input.value);

  // The style card, from the badge: open on the chosen style, the arrows
  // move, a row chooses (the tool on, the card closed, focus back).
  const card = els.highlightStyleCard;
  els.highlightOptions.hidden = false; // a settings card left open closes
  editorClosed = 0;
  els.highlightStyleBtn.click();
  out.styleCardOpened = { hidden: card.hidden, expanded: els.highlightStyleBtn.getAttribute("aria-expanded"),
                          focus: document.activeElement.dataset.style, caret: card.style.getPropertyValue("--caret-y"),
                          settingsHidden: els.highlightOptions.hidden, editorClosed };
  const down = card.fire("keydown", { key: "ArrowDown" });
  out.arrowMoved = { focus: document.activeElement.dataset.style, prevented: down.defaultPrevented };
  card.fire("keydown", { key: "End" });
  out.endMoved = document.activeElement.dataset.style;
  card.fire("keydown", { key: "ArrowDown" });
  out.wrapped = document.activeElement.dataset.style;
  calls.length = 0;
  items[1].click();
  await tick();
  out.underline = { major: major(), shown: shown(), title: els.highlightBtn.title,
                    label: els.highlightBtn.getAttribute("aria-label"), icon: icons[icons.length - 1],
                    applied: document.documentElement.style.getPropertyValue("--applied-opacity"),
                    inkOpacity: inkStyle.opacity, calls: calls.map((c) => c[0]), checked: checked(),
                    toolOn: tool.on, cardHidden: card.hidden, focusIsBadge: document.activeElement === els.highlightStyleBtn,
                    preview: preview() };

  // A right-click on the tool opens it too; Esc closes it back to the tool.
  const menu = els.highlightBtn.fire("contextmenu");
  out.rightClick = { prevented: menu.defaultPrevented, hidden: card.hidden };
  card.fire("keydown", { key: "Escape" });
  out.rightClickEsc = { hidden: card.hidden, focusIsTool: document.activeElement === els.highlightBtn };

  // Its letters choose; a press outside closes it.
  els.highlightStyleBtn.click();
  card.fire("keydown", { key: "s" });
  await tick();
  out.letterChose = { style: run("activeHighlightStyle"), hidden: card.hidden };
  els.highlightStyleBtn.click();
  fireDoc("pointerdown", { target: items[0] });
  out.afterInsideCardPress = card.hidden;
  fireDoc("pointerdown", { target: outside });
  out.afterOutsideCardPress = card.hidden;

  // The keys: the style with the tool on; the same key again, off.
  const keys = [];
  for (const key of ["u", "U", "h", "s", "s"]) {
    const style = run("highlightStyleForKey")(key);
    await run("pressHighlightStyleKey")(style);
    await tick();
    keys.push([key, run("activeHighlightStyle"), tool.on]);
  }
  out.keys = keys;
  out.nonKeys = ["a", "", "Shift", "S"].map((k) => run("highlightStyleForKey")(k) || null);

  // Thickness.
  calls.length = 0;
  presets.find((p) => p.dataset.width === "36").fire("click");
  await tick();
  out.thickness = { calls: calls.slice(), selected: presets.filter((p) => p.classList.contains("selected")).map((p) => p.dataset.width),
                    ink: { ...inkStyle } };

  // Voice's helpers.
  calls.length = 0;
  out.thicker = await run("stepInkWidth")(1);
  out.thinner = await run("stepInkWidth")(-1);
  out.thinnerAgain = await run("stepInkWidth")(-1);
  out.stepCalls = calls.filter((c) => c[0] === "set_ink_width");
  calls.length = 0;
  await run("setHighlightOpacityNow")(40);
  out.voiceOpacity = { calls: calls.slice(), shown: shown() };

  // The settings card.
  const pop = els.highlightOptions;
  card.hidden = false; // a style card left open closes
  els.highlightColorBtn.click();
  out.opened = { hidden: pop.hidden, expanded: els.highlightColorBtn.getAttribute("aria-expanded"),
                 caret: pop.style.getPropertyValue("--caret-y"), styleHidden: card.hidden };
  fireDoc("pointerdown", { target: input });
  out.afterInsidePress = pop.hidden;
  // A value waiting to be written is written as the card closes.
  calls.length = 0;
  input.fire("keydown", { key: "ArrowLeft" });
  input.value = "45"; input.fire("input");
  pop.fire("keydown", { key: "Escape" });
  out.afterEsc = { hidden: pop.hidden, focusIsButton: document.activeElement === els.highlightColorBtn,
                   saves: calls.filter((c) => c[0] === "set_highlight_opacity") };
  els.highlightColorBtn.click();
  fireDoc("pointerdown", { target: els.highlightColorBtn });
  out.afterButtonPress = pop.hidden;
  fireDoc("pointerdown", { target: outside });
  out.afterOutsidePress = pop.hidden;
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""


@unittest.skipUnless(NODE, "Node is not installed")
class HighlightOptionsBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run(
            [NODE, "-e", HARNESS, str(SLIDER_JS), str(OPTIONS_JS)], capture_output=True, text=True, timeout=30
        )
        if run.returncode != 0:
            raise AssertionError(run.stderr)
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    # --- The opacity slider ------------------------------------------------- #

    def test_a_tick_every_ten_percent_placed_along_the_track(self):
        self.assertEqual([value for value, _ in self.out["ticks"]], list(range(10, 101, 10)))
        self.assertEqual(self.out["ticks"][0][1], "0.000")
        self.assertEqual(self.out["ticks"][-1][1], "1.000")
        self.assertEqual(self.out["ticks"][5][1], f"{50 / 90:.3f}")

    def test_the_label_and_value_sit_over_the_slider(self):
        self.assertEqual(self.out["head"], ["hl-options-head", "range-slider-wrap"])

    def test_it_starts_at_the_style_s_opacity_with_its_default_emphasised(self):
        self.assertEqual(self.out["major"], ["60"])
        self.assertEqual(self.out["initial"], {"value": 60, "text": "60%"})
        self.assertEqual(self.out["appliedOpacity"], "0.6")

    def test_a_drag_snaps_to_the_default_only_within_three_percent(self):
        self.assertEqual(self.out["dragged"], [60, 64, 60, 56])

    def test_a_drag_is_written_once_when_it_ends(self):
        self.assertEqual(self.out["savesBeforeRelease"], 0)
        self.assertEqual(self.out["savesAfterRelease"], [["set_highlight_opacity", "highlight", 56]])
        self.assertAlmostEqual(self.out["inkAfterDrag"], 0.56)
        self.assertEqual(self.out["previewAfterDrag"]["opacity"], "0.56")

    def test_the_arrow_keys_never_snap_and_are_written_once_rested(self):
        self.assertEqual(self.out["keyed"], 61)
        self.assertEqual(self.out["keyedSaves"], [["set_highlight_opacity", "highlight", 61]])

    def test_page_up_and_down_move_ten(self):
        self.assertEqual(self.out["paged"], [[71, True], [81, True], [91, True], [100, True], [90, True]])

    def test_a_tick_jumps_and_a_double_click_on_the_thumb_resets(self):
        self.assertEqual(self.out["afterTick"], {"value": 30, "text": "30%"})
        self.assertEqual(self.out["afterDblclickOffThumb"], 30)
        self.assertEqual(self.out["afterDblclickThumb"], 60)

    # --- The tool and the style card --------------------------------------- #

    def test_the_tool_names_its_style_and_key(self):
        self.assertEqual(self.out["tool"], {"title": "Highlight  (H)", "label": "Highlight"})
        self.assertEqual(self.out["checked"], ["highlight"])

    def test_the_badge_opens_the_style_card_on_the_chosen_style(self):
        opened = self.out["styleCardOpened"]
        self.assertFalse(opened["hidden"])
        self.assertEqual(opened["expanded"], "true")
        self.assertEqual(opened["focus"], "highlight")
        self.assertEqual(opened["caret"], "20px")  # the tool's middle: 40px tall, at the top
        # One card at a time, and never over the editor.
        self.assertTrue(opened["settingsHidden"])
        self.assertEqual(opened["editorClosed"], 1)

    def test_the_arrows_move_through_the_rows_and_wrap(self):
        self.assertEqual(self.out["arrowMoved"], {"focus": "underline", "prevented": True})
        self.assertEqual(self.out["endMoved"], "strikethrough")
        self.assertEqual(self.out["wrapped"], "highlight")

    def test_choosing_a_row_switches_style_turns_the_tool_on_and_closes(self):
        underline = self.out["underline"]
        self.assertTrue(underline["toolOn"])
        self.assertTrue(underline["cardHidden"])
        self.assertTrue(underline["focusIsBadge"])
        self.assertEqual(underline["checked"], ["underline"])
        self.assertEqual(underline["title"], "Underline  (U)")
        self.assertEqual(underline["label"], "Underline")
        self.assertEqual(underline["icon"], "underline")

    def test_switching_style_moves_the_emphasised_tick(self):
        underline = self.out["underline"]
        self.assertEqual(underline["major"], ["100"])
        self.assertEqual(underline["shown"], {"value": 100, "text": "100%"})
        self.assertEqual(underline["applied"], "1")
        self.assertIn("set_highlight_style", underline["calls"])
        # Strokes keep the Highlight opacity whichever style is chosen: the
        # 60% the double-click put back, written as the settings card closed
        # (opening the style card closes it), so the style's reply does not
        # bring back an older one.
        self.assertAlmostEqual(underline["inkOpacity"], 0.6)
        self.assertEqual(underline["calls"], ["set_highlight_style"])

    def test_the_preview_is_the_style_s_line(self):
        # Highlight: as thick as the 14pt preset on the presets' scale.
        self.assertEqual(self.out["preview"], {"height": f"{max(3, 14 / 36 * 16)}px", "opacity": "0.6"})
        # A line style: 2px, at its own opacity.
        self.assertEqual(self.out["underline"]["preview"], {"height": "2px", "opacity": "1"})

    def test_a_right_click_on_the_tool_opens_it_and_esc_returns_there(self):
        self.assertEqual(self.out["rightClick"], {"prevented": True, "hidden": False})
        self.assertEqual(self.out["rightClickEsc"], {"hidden": True, "focusIsTool": True})

    def test_a_style_s_letter_chooses_it_from_the_card(self):
        self.assertEqual(self.out["letterChose"], {"style": "strikethrough", "hidden": True})

    def test_the_style_card_closes_on_a_press_outside_only(self):
        self.assertFalse(self.out["afterInsideCardPress"])
        self.assertTrue(self.out["afterOutsideCardPress"])

    # --- The keys ------------------------------------------------------------ #

    def test_a_style_key_turns_it_on_and_again_turns_the_tool_off(self):
        # From Strikethrough with the tool on (the card's letter chose it).
        self.assertEqual(self.out["keys"], [
            ["u", "underline", True],
            ["U", "underline", False],
            ["h", "highlight", True],
            ["s", "strikethrough", True],
            ["s", "strikethrough", False],
        ])

    def test_only_h_u_and_s_are_style_keys(self):
        self.assertEqual(self.out["nonKeys"], [None, None, None, "strikethrough"])

    # --- Thickness and voice's helpers -------------------------------------- #

    def test_presets_are_proportional_and_named(self):
        heights = {width: float(height[:-2]) for width, height, _, _ in self.out["presets"]}
        self.assertEqual(heights["36"], 16)
        self.assertEqual(heights["4"], 2)
        self.assertAlmostEqual(heights["14"], 16 * 14 / 36)
        self.assertAlmostEqual(heights["24"], 16 * 24 / 36)
        self.assertEqual([label for _, _, label, _ in self.out["presets"]], ["4 pt", "8 pt", "14 pt", "24 pt", "36 pt"])
        self.assertEqual([tip for _, _, _, tip in self.out["presets"]], ["4 pt", "8 pt", "14 pt", "24 pt", "36 pt"])
        self.assertEqual(self.out["presetSelected"], ["14"])

    def test_the_arrows_choose_a_preset_as_they_move(self):
        self.assertTrue(self.out["radio"]["highlightThicknessGroup"])

    def test_a_preset_is_written_and_reaches_the_freehand_preview(self):
        thickness = self.out["thickness"]
        self.assertEqual(thickness["calls"], [["set_ink_width", 36]])
        self.assertEqual(thickness["selected"], ["36"])
        self.assertEqual(thickness["ink"]["width"], 36)
        self.assertEqual(self.out["inkStyle"], {"width": 14, "opacity": 0.6})

    def test_thicker_and_thinner_move_one_preset_and_stop_at_the_end(self):
        self.assertEqual(self.out["thicker"], {"width": 36, "moved": False})
        self.assertEqual(self.out["thinner"], {"width": 24, "moved": True})
        self.assertEqual(self.out["thinnerAgain"], {"width": 14, "moved": True})
        self.assertEqual(self.out["stepCalls"], [["set_ink_width", 24], ["set_ink_width", 14]])

    def test_a_spoken_opacity_is_the_current_style_s_and_written_at_once(self):
        voice = self.out["voiceOpacity"]
        self.assertEqual(voice["calls"][0], ["set_highlight_opacity", "strikethrough", 40])
        self.assertEqual(voice["shown"], {"value": 40, "text": "40%"})

    # --- The settings card --------------------------------------------------- #

    def test_the_settings_card_opens_with_its_caret_on_the_preview(self):
        opened = self.out["opened"]
        self.assertFalse(opened["hidden"])
        self.assertEqual(opened["expanded"], "true")
        self.assertEqual(opened["caret"], "62px")  # 50px down, 24px tall
        self.assertTrue(opened["styleHidden"])

    def test_it_closes_on_esc_or_a_press_outside_only(self):
        self.assertFalse(self.out["afterInsidePress"])
        self.assertTrue(self.out["afterEsc"]["hidden"])
        self.assertTrue(self.out["afterEsc"]["focusIsButton"])
        self.assertEqual(self.out["afterEsc"]["saves"], [["set_highlight_opacity", "strikethrough", 45]])
        self.assertFalse(self.out["afterButtonPress"])
        self.assertTrue(self.out["afterOutsidePress"])


class StyleKeyGuardTests(unittest.TestCase):
    """reading.js lets H, U and S reach the tool only outside text fields,
    dialogs and modifier chords (Ctrl+S is Save)."""

    def handler(self):
        start = READING_JS.index("const toolStyle =")
        return READING_JS[start:READING_JS.index("switch (ev.key)", start)]

    def test_the_guard(self):
        block = self.handler()
        self.assertIn("!ev.ctrlKey && !ev.metaKey && !ev.altKey && highlightStyleForKey(ev.key)", block)
        self.assertIn("ev.repeat || dialogOpenAtKey || isTypingTarget(ev.target)", block)
        self.assertIn("pressHighlightStyleKey(toolStyle)", block)

    def test_it_runs_before_the_other_keys_and_h_no_longer_toggles_alone(self):
        self.assertLess(READING_JS.index("const toolStyle ="), READING_JS.index('if (ev.target.tagName === "INPUT") return;'))
        self.assertNotIn('case "h":', READING_JS)
        # Ctrl+S is still Save.
        self.assertIn('case "s":\n      if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); promptSaveIfDirty(false); }', READING_JS)

    def test_the_tool_reports_its_state(self):
        self.assertIn('highlightBtn.setAttribute("aria-pressed", String(on));', READING_JS)
        self.assertIn("configureHighlightTool({ isOn: () => highlightMode, setOn: setHighlightMode });", READING_JS)


class HighlightOptionsLookTests(unittest.TestCase):
    def rule(self, selector):
        start = READING_CSS.index(selector + " {")
        return READING_CSS[start:READING_CSS.index("}", start)]

    def block(self, start_marker, end_marker):
        return READING_HTML[READING_HTML.index(start_marker):READING_HTML.index(end_marker)]

    def test_the_cards_are_the_zoom_popover_s_surface(self):
        shared = re.search(r"\.rail-card,\s*\.highlight-editor \{([^}]*)\}", READING_CSS).group(1)
        for decl in ("border-radius: var(--radius-lg);", "background-color: var(--popover-surface);",
                     "box-shadow: var(--popover-shadow);", "border: 1px solid var(--border);"):
            with self.subTest(decl=decl):
                self.assertIn(decl, self.rule(".zoom-popover"))
                self.assertIn(decl, shared)

    def test_the_cards_sizes(self):
        style = self.rule(".style-card")
        for decl in ("width: 210px;", "padding: 6px;"):
            self.assertIn(decl, style)
        self.assertIn("height: 36px;", self.rule(".style-item"))
        settings = self.rule(".highlight-options")
        for decl in ("width: 252px;", "padding: 14px;", "gap: 14px;"):
            self.assertIn(decl, settings)
        self.assertIn("width: 26px; height: 26px;", READING_CSS[READING_CSS.index(".highlight-options .swatch {"):])
        self.assertIn("animation: rail-card-in var(--dur-quick) var(--ease-out);", self.rule(".rail-card"))

    def test_the_split_button(self):
        split = self.block('<div class="tool-split">', '<button class="preview-btn"')
        self.assertIn('id="highlightBtn" data-icon="highlight" title="Highlight  (H)"', split)
        self.assertIn('aria-pressed="false"', split)
        badge = re.search(r'<button class="style-badge" id="highlightStyleBtn"[^>]*>', split).group(0)
        for attr in ('title="Choose style"', 'aria-haspopup="menu"', 'aria-controls="highlightStyleCard"'):
            self.assertIn(attr, badge)
        self.assertIn('class="style-badge-face" data-icon="chevron_left"', split)
        rule = self.rule(".style-badge")
        # An 18px chip overlapping the button's corner by 3px, its 24px hit
        # area centred on it.
        for decl in ("--badge-face: 18px;", "--badge-overlap: 3px;", "--badge-hit: 24px;",
                     "right: calc(-1 * var(--badge-overlap) - (var(--badge-hit) - var(--badge-face)) / 2);",
                     "bottom: calc(-1 * var(--badge-overlap) - (var(--badge-hit) - var(--badge-face)) / 2);",
                     "justify-content: center;"):
            self.assertIn(decl, rule)
        face = self.rule(".style-badge-face")
        self.assertIn("background-color: var(--popover-surface);", face)
        self.assertIn("box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);", face)
        hover = READING_CSS[READING_CSS.index(".style-badge:hover .style-badge-face"):]
        hover = hover[:hover.index("}")]
        self.assertIn("transform: scale(1.12);", hover)
        self.assertIn("border-color: var(--accent);", hover)
        self.assertIn("border: 1px solid var(--badge-border);", face)
        theme = (ROOT / "frontend" / "shared" / "theme.css").read_text(encoding="utf-8")
        # The popover surface in every theme; in dark a lighter border than the
        # faint --border, never white.
        self.assertEqual(re.findall(r"--badge-border: (var\(--[a-z-]+\));", theme),
                         ["var(--border)", "var(--border-strong)", "var(--border)"])

    def test_the_tool_and_badge_share_one_hover(self):
        self.assertIn("border-radius: var(--radius-md);", self.rule(".tool-split"))
        self.assertIn("background-color: var(--state-hover);", self.rule(".tool-split:hover .icon-btn:not(.active)"))
        self.assertIn("color-mix(in srgb, var(--tool-tint), var(--text-primary) 4%)",
                      self.rule(".tool-split:hover .icon-btn.active::before"))

    def test_the_style_card_rows(self):
        card = self.block('id="highlightStyleCard"', 'id="highlightOptions"')
        self.assertIn('role="menu"', card)
        for style, label, key in (("highlight", "Highlight", "H"), ("underline", "Underline", "U"),
                                  ("strikethrough", "Strikethrough", "S")):
            with self.subTest(style=style):
                self.assertRegex(card, rf'role="menuitemradio"[^>]*data-style="{style}">\s*'
                                       rf'<span class="style-item-icon" data-icon="{style}"></span>'
                                       rf'<span class="style-item-name">{label}</span><kbd class="key-badge">{key}</kbd>'
                                       rf'<span class="style-item-check" data-icon="check"></span>')

    def test_the_preview_button_opens_the_settings_card(self):
        tag = re.search(r'<button class="preview-btn" id="highlightColorBtn"[^>]*>', READING_HTML).group(0)
        for attr in ('aria-haspopup="dialog"', 'aria-controls="highlightOptions"', 'title="Color, opacity, thickness"'):
            self.assertIn(attr, tag)
        self.assertIn("width: 26px;", self.rule(".preview-line"))

    def test_the_settings_card_rows_in_order_with_no_style_switcher(self):
        card = self.block('id="highlightOptions"', '<!-- Always shown.')
        order = [card.index(marker) for marker in (
            'id="highlightOptionsSwatches"', 'class="hl-options-divider"',
            'id="highlightOpacityControl"', '>Thickness<', '>drawing only<')]
        self.assertEqual(order, sorted(order))
        for absent in ("data-style", "segment", "kbd", "Say "):
            self.assertNotIn(absent, card)

    def test_the_icons_exist(self):
        for name in ("highlight", "underline", "strikethrough", "chevron_left", "check"):
            self.assertTrue((ROOT / "frontend" / "shared" / "icons" / f"{name}.svg").is_file())

    def test_the_scripts_load_in_order(self):
        order = [READING_HTML.index(f'<script src="{name}"></script>')
                 for name in ("command-reference.js", "range-slider.js", "highlight-colors.js",
                              "highlight-options.js", "freehand.js", "reading.js")]
        self.assertEqual(order, sorted(order))

    def test_the_selected_preset_has_the_accent_ring(self):
        self.assertIn("box-shadow: inset 0 0 0 2px var(--accent);", self.rule(".thickness-preset.selected"))
        line = self.rule(".thickness-line")
        self.assertIn("width: 26px;", line)
        self.assertIn("border-radius: 999px;", line)
        self.assertIn("opacity: var(--preset-opacity, 1);", line)

    def test_ticks_are_the_asked_strengths(self):
        minor = self.rule(".range-tick::before")
        for decl in ("width: 2px;", "height: 5px;", "opacity: 0.5;"):
            self.assertIn(decl, minor)
        major = self.rule(".range-tick--major::before")
        for decl in ("height: 8px;", "background-color: var(--text-primary);", "opacity: 0.75;"):
            self.assertIn(decl, major)

    def test_lines_land_as_lines_on_release(self):
        for style in ("underline", "strikethrough"):
            self.assertIn(f'.selection-layer[data-style="{style}"] .selection-rect.applied::after', READING_CSS)
        self.assertIn("opacity: var(--applied-opacity, 1);", self.rule(".selection-rect.applied"))


if __name__ == "__main__":
    unittest.main()
