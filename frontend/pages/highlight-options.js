// The highlight tool's choices (docs/DESIGN_SYSTEM.md, "Highlight tool"):
//
//   The tool      a split button on the rail. The button toggles the tool in
//                 the current style, its icon that style's; the badge on its
//                 corner (or a right-click on the button) opens the style card.
//   Style card    Highlight · Underline · Strikethrough, each with its key
//                 (H, U, S): what a drag over text writes (a PDF /Highlight,
//                 /Underline or /StrikeOut). Choosing one turns the tool on.
//                 Over anything else the tool always draws a freehand stroke.
//   Preview       the button under the tool: a line in the color at the
//                 style's opacity (for Highlight as thick as the freehand
//                 preset), which opens the settings card.
//   Settings card the five colors; the opacity slider (10–100%, remembered per
//                 style, its default tick emphasised, a drag settling on it
//                 within ±3%, a double-click on the thumb going back to it;
//                 freehand strokes use the Highlight opacity); the thickness
//                 presets, for drawings only.
//
// One card at a time. Every change applies at once and is remembered (api.py's
// set_highlight_*), so it is still the reader's choice in the next PDF.
//
// The opacity and thickness controls are built here and reused by the
// highlight editor (highlight-colors.js), which shows the same two for an
// existing highlight.

const hlToolBtn = document.getElementById("highlightBtn");
const highlightStyleBtn = document.getElementById("highlightStyleBtn");
const highlightColorBtn = document.getElementById("highlightColorBtn");
const highlightPreviewLine = document.getElementById("highlightPreviewLine");
const highlightStyleCard = document.getElementById("highlightStyleCard");
const highlightOptions = document.getElementById("highlightOptions");
const highlightOptionsSwatches = document.getElementById("highlightOptionsSwatches");
const highlightOpacityControl = document.getElementById("highlightOpacityControl");
const highlightThicknessGroup = document.getElementById("highlightThicknessGroup");

const HIGHLIGHT_STYLE_LABELS = { highlight: "Highlight", underline: "Underline", strikethrough: "Strikethrough" };
// The key that chooses each style (reading.js's keydown), shown in the tool's
// tooltip and the style card, nowhere else.
const HIGHLIGHT_STYLE_KEYS = { h: "highlight", u: "underline", s: "strikethrough" };
// The thickness presets are drawn proportional to their width: the widest
// (36pt) this tall, the rest scaled the same, never thinner than the floor.
const THICKNESS_PREVIEW_MAX_PX = 16;
const THICKNESS_PREVIEW_MIN_PX = 2;
// The preview button's line: a Highlight as thick as the freehand preset (on
// the same scale, a little thicker at the least), a line style 2px.
const PREVIEW_HIGHLIGHT_MIN_PX = 3;
const PREVIEW_LINE_PX = 2;
// The opacity slider's Page Up / Page Down step, in percent (the arrows step 1).
const OPACITY_PAGE_STEP = 10;
// How long the opacity slider waits after the last step before writing the
// setting (and once more when the drag ends): a drag is a few dozen steps.
const OPACITY_SAVE_DELAY_MS = 150;

let highlightOptionsState = null; // api.get_highlight_options()'s reply
let activeHighlightColor = null;
let activeHighlightStyle = "highlight";
let popoverOpacity = null; // the settings card's opacity control
let popoverThickness = null; // the settings card's thickness control
let opacitySaveTimer = null;
let styleCardOpener = null; // the badge or the tool button, for Esc to return to
// reading.js's highlight tool: { isOn(), setOn(on) }.
let highlightToolHooks = { isOn: () => false, setOn: () => {} };

function configureHighlightTool(hooks) {
  highlightToolHooks = hooks;
}

function highlightStyleLabel(style) {
  return HIGHLIGHT_STYLE_LABELS[style] || HIGHLIGHT_STYLE_LABELS.highlight;
}

function highlightStyleKey(style) {
  const key = Object.keys(HIGHLIGHT_STYLE_KEYS).find((k) => HIGHLIGHT_STYLE_KEYS[k] === style) || "h";
  return key.toUpperCase();
}

// The style a key chooses ("h", "U"...), or undefined.
function highlightStyleForKey(key) {
  return HIGHLIGHT_STYLE_KEYS[String(key || "").toLowerCase()];
}

function defaultOpacityPct(style) {
  const defaults = highlightOptionsState ? highlightOptionsState.default_opacity : {};
  return defaults[style] || 100;
}

function opacityPct(style) {
  return highlightOptionsState ? highlightOptionsState.opacity[style] : defaultOpacityPct(style);
}

// How tall a preset's line is drawn for a stroke `width` points wide.
function thicknessPreviewPx(width, minPx = THICKNESS_PREVIEW_MIN_PX) {
  const widest = Math.max(...highlightOptionsState.ink_widths);
  return Math.max(minPx, (width / widest) * THICKNESS_PREVIEW_MAX_PX);
}

// --- The opacity control (settings card and editor) ------------------------ //

// Builds "Opacity        60%" over [slider with ticks] into `root`. hooks: {
//   onChange(pct)  a new value, by drag, key, tick or reset,
//   onGesture()    a new drag, key press, tick or reset begins (the editor
//                  makes each one undo step),
//   onRelease()    a drag ended,
// }
// Returns { set(pct, defaultPct), input }.
function createOpacityControl(root, hooks) {
  const range = highlightOptionsState;
  const label = document.createElement("span");
  label.className = "hl-options-label";
  label.id = `${root.id}Label`;
  label.textContent = "Opacity";
  const value = document.createElement("output");
  value.className = "opacity-value";
  value.setAttribute("aria-hidden", "true"); // the slider's own valuetext says it
  const head = document.createElement("div");
  head.className = "hl-options-head";
  head.append(label, value);
  const input = document.createElement("input");
  input.type = "range";
  input.className = "range-slider";
  input.min = String(range.opacity_min);
  input.max = String(range.opacity_max);
  input.step = "1";
  input.setAttribute("aria-labelledby", label.id);
  const ticks = document.createElement("div");
  ticks.className = "range-ticks";
  for (let pct = range.opacity_min; pct <= range.opacity_max; pct += range.opacity_tick_step) {
    const tick = document.createElement("button");
    tick.type = "button";
    tick.className = "range-tick";
    tick.tabIndex = -1;
    tick.dataset.value = String(pct);
    tick.dataset.tooltip = `${pct}%`;
    tick.setAttribute("aria-label", `Opacity ${pct}%`);
    ticks.append(tick);
  }
  const wrap = document.createElement("div");
  wrap.className = "range-slider-wrap";
  wrap.append(input, ticks);
  root.replaceChildren(head, wrap);
  placeRangeTicks(ticks, (pct) => (pct - range.opacity_min) / (range.opacity_max - range.opacity_min));

  let defaultPct = range.opacity_max;
  const show = (pct) => {
    input.value = String(pct);
    value.textContent = `${pct}%`;
    input.setAttribute("aria-valuetext", `${pct}%`);
    paintRangeFill(input);
  };
  const choose = (pct) => {
    show(pct);
    hooks.onChange(pct);
  };
  const gesture = () => { if (hooks.onGesture) hooks.onGesture(); };
  input.addEventListener("pointerdown", (ev) => { if (ev.button === 0) gesture(); });
  input.addEventListener("keydown", (ev) => {
    if (!ev.repeat) gesture();
    // Page Up / Page Down move a tick's worth, whatever the browser's own step.
    const page = { PageUp: OPACITY_PAGE_STEP, PageDown: -OPACITY_PAGE_STEP }[ev.key];
    if (!page) return;
    ev.preventDefault();
    ev.stopPropagation();
    choose(Math.min(range.opacity_max, Math.max(range.opacity_min, Number(input.value) + page)));
  });
  bindRangeSlider(input, ticks, {
    // Only a drag settles on the default, so the arrow keys can step past it.
    onInput: (pct, dragging) => choose(dragging && Math.abs(pct - defaultPct) <= range.opacity_snap ? defaultPct : pct),
    onReset: () => { gesture(); choose(defaultPct); },
    onTick: (pct) => { gesture(); choose(pct); },
    onRelease: () => { if (hooks.onRelease) hooks.onRelease(); },
  });

  return {
    input,
    set(pct, newDefault) {
      defaultPct = newDefault;
      for (const tick of ticks.querySelectorAll(".range-tick")) {
        const isDefault = Number(tick.dataset.value) === newDefault;
        tick.classList.toggle("range-tick--major", isDefault);
        tick.dataset.tooltip = isDefault ? `${tick.dataset.value}% (default)` : `${tick.dataset.value}%`;
      }
      show(pct);
    },
  };
}

// --- The thickness control (settings card and editor) ---------------------- //

// Five buttons as a radiogroup, each a short round-capped line as thick
// (relative to the others) as its stroke. `selectOnArrow`: the settings card's
// arrows choose as they move (a setting); the editor's only move, since each
// choice there is an undo step. hooks: { onPick(width) }.
// Returns { set(width), paint(colorCss, opacity) }.
function createThicknessControl(root, { selectOnArrow }, hooks) {
  root.replaceChildren(...highlightOptionsState.ink_widths.map((width) => {
    const preset = document.createElement("button");
    preset.type = "button";
    preset.className = "thickness-preset";
    preset.dataset.width = String(width);
    preset.dataset.tooltip = `${width} pt`;
    preset.setAttribute("aria-label", `${width} pt`);
    const stroke = document.createElement("span");
    stroke.className = "thickness-line";
    stroke.style.height = `${thicknessPreviewPx(width)}px`;
    preset.append(stroke);
    preset.addEventListener("click", () => hooks.onPick(width));
    return preset;
  }));
  initRadioCards(root, { selectOnArrow });
  return {
    set(width) {
      root.querySelectorAll(".thickness-preset").forEach((preset) => {
        preset.classList.toggle("selected", Number(preset.dataset.width) === width);
      });
    },
    paint(colorCss, opacity) {
      root.style.setProperty("--preset-color", colorCss);
      root.style.setProperty("--preset-opacity", String(opacity));
    },
  };
}

// --- What the options change elsewhere -------------------------------------- //

// The rail (the tool's icon and name, the preview line), the style card's
// check, the settings card, the drag's release wash and the freehand preview,
// brought in line with the options and the active color.
function showHighlightOptions() {
  if (!highlightOptionsState) return;
  const style = highlightOptionsState.style;
  activeHighlightStyle = style;
  const label = highlightStyleLabel(style);
  if (hlToolBtn.dataset.icon !== style) {
    hlToolBtn.dataset.icon = style;
    mountIcon(hlToolBtn, style);
  }
  hlToolBtn.title = `${label}  (${highlightStyleKey(style)})`;
  hlToolBtn.setAttribute("aria-label", label);
  highlightStyleCard.querySelectorAll(".style-item").forEach((item) => {
    item.setAttribute("aria-checked", String(item.dataset.style === style));
  });
  popoverOpacity.set(opacityPct(style), defaultOpacityPct(style));
  popoverThickness.set(highlightOptionsState.ink_width);
  const inkOpacity = opacityPct("highlight") / 100;
  if (activeHighlightColor) popoverThickness.paint(swatchColor(activeHighlightColor), inkOpacity);
  const previewPx = style === "highlight"
    ? thicknessPreviewPx(highlightOptionsState.ink_width, PREVIEW_HIGHLIGHT_MIN_PX)
    : PREVIEW_LINE_PX;
  highlightPreviewLine.style.setProperty("--preview-height", `${previewPx}px`);
  highlightPreviewLine.style.setProperty("--preview-opacity", String(opacityPct(style) / 100));
  // The wash a drag turns into on release (reading.css, .selection-rect.applied)
  // shows the highlight before the re-rendered page does, so it is drawn at
  // the opacity that page is about to show.
  document.documentElement.style.setProperty("--applied-opacity", String(opacityPct(style) / 100));
  setInkStyle({ width: highlightOptionsState.ink_width, opacity: inkOpacity });
}

function adoptHighlightOptions(options) {
  if (options && options.styles) highlightOptionsState = options;
  showHighlightOptions();
}

function showActiveHighlightColor(name) {
  if (!highlightPalette.includes(name)) return;
  activeHighlightColor = name;
  highlightColorBtn.style.setProperty("--swatch", swatchColor(name));
  markSwatch(highlightOptionsSwatches, name);
  // The wash a drag turns into on release is drawn in the color that page is
  // about to show.
  document.documentElement.style.setProperty("--applied-highlight", swatchColor(name));
  showHighlightOptions();
}

// Also what the voice "highlight color <color>" command calls.
async function setActiveHighlightColor(name) {
  const state = await callApi("set_highlight_color", name);
  showActiveHighlightColor((state && state.highlight_color) || name);
  return state;
}

async function setActiveHighlightStyle(style) {
  if (!highlightOptionsState || style === highlightOptionsState.style) return;
  highlightOptionsState = { ...highlightOptionsState, style };
  showHighlightOptions();
  // An opacity still waiting to be written goes first: the reply below
  // carries every stored option, and would otherwise put the old one back.
  await flushOpacitySave();
  adoptHighlightOptions(await callApi("set_highlight_style", style));
}

// A style chosen from the style card or by voice ("underline mode"): that
// style, with the tool on.
function chooseHighlightStyle(style) {
  highlightToolHooks.setOn(true);
  return setActiveHighlightStyle(style);
}

// H, U or S: that style with the tool on, or, when it is that style already
// and on, the tool off.
function pressHighlightStyleKey(style) {
  if (highlightToolHooks.isOn() && style === activeHighlightStyle) {
    highlightToolHooks.setOn(false);
    return Promise.resolve();
  }
  return chooseHighlightStyle(style);
}

// Writes the opacity waiting on the slider to rest, if there is one;
// resolves once it is stored.
function flushOpacitySave() {
  if (!opacitySaveTimer) return Promise.resolve();
  clearTimeout(opacitySaveTimer.timer);
  const { style, pct } = opacitySaveTimer;
  opacitySaveTimer = null;
  return callApi("set_highlight_opacity", style, pct);
}

// The slider shows the new value at once; the setting is written once the
// slider has rested (or the drag has ended).
function setActiveHighlightOpacity(pct) {
  const style = highlightOptionsState.style;
  highlightOptionsState = {
    ...highlightOptionsState,
    opacity: { ...highlightOptionsState.opacity, [style]: pct },
  };
  showHighlightOptions();
  if (opacitySaveTimer) clearTimeout(opacitySaveTimer.timer);
  opacitySaveTimer = { style, pct, timer: setTimeout(flushOpacitySave, OPACITY_SAVE_DELAY_MS) };
}

// The voice "opacity <n>": the current style's, written at once.
async function setHighlightOpacityNow(pct) {
  setActiveHighlightOpacity(pct);
  await flushOpacitySave();
}

async function setActiveInkWidth(width) {
  if (!highlightOptionsState || width === highlightOptionsState.ink_width) return;
  highlightOptionsState = { ...highlightOptionsState, ink_width: width };
  showHighlightOptions();
  await flushOpacitySave();
  adoptHighlightOptions(await callApi("set_ink_width", width));
}

// The voice "thicker" / "thinner": one preset up (+1) or down (-1). Resolves
// to { width, moved }, `moved` false at either end.
async function stepInkWidth(direction) {
  const widths = highlightOptionsState.ink_widths;
  const at = widths.indexOf(highlightOptionsState.ink_width);
  const next = widths[Math.min(widths.length - 1, Math.max(0, at + direction))];
  if (next === highlightOptionsState.ink_width) return { width: next, moved: false };
  await setActiveInkWidth(next);
  return { width: next, moved: true };
}

// --- The two cards ---------------------------------------------------------- //

function isStyleCardOpen() {
  return !highlightStyleCard.hidden;
}

function isSettingsCardOpen() {
  return !highlightOptions.hidden;
}

// Either card: what reading.js's Esc and Focus mode close.
function isHighlightOptionsOpen() {
  return isStyleCardOpen() || isSettingsCardOpen();
}

// The caret points at the middle of the button the card belongs to (the tool
// for the style card, whichever way it was opened), wherever the rail put it.
function pointCaretAt(card, button) {
  const box = button.getBoundingClientRect();
  const frame = card.parentElement.getBoundingClientRect();
  card.style.setProperty("--caret-y", `${box.top - frame.top + box.height / 2}px`);
}

function styleItems() {
  return [...highlightStyleCard.querySelectorAll(".style-item")];
}

function openStyleCard(opener = highlightStyleBtn) {
  closeHighlightEditor();
  closeSettingsCard();
  styleCardOpener = opener;
  highlightStyleCard.hidden = false;
  pointCaretAt(highlightStyleCard, hlToolBtn);
  highlightStyleBtn.setAttribute("aria-expanded", "true");
  const items = styleItems();
  (items.find((item) => item.dataset.style === activeHighlightStyle) || items[0]).focus();
}

function closeStyleCard({ returnFocus = false } = {}) {
  if (!isStyleCardOpen()) return;
  highlightStyleCard.hidden = true;
  highlightStyleBtn.setAttribute("aria-expanded", "false");
  if (returnFocus) (styleCardOpener || highlightStyleBtn).focus();
}

function openSettingsCard() {
  closeHighlightEditor();
  closeStyleCard();
  highlightOptions.hidden = false;
  pointCaretAt(highlightOptions, highlightColorBtn);
  highlightColorBtn.setAttribute("aria-expanded", "true");
  const chosen = highlightOptionsSwatches.querySelector(".swatch.selected")
    || highlightOptionsSwatches.querySelector(".swatch");
  if (chosen) chosen.focus();
}

function closeSettingsCard({ returnFocus = false } = {}) {
  if (!isSettingsCardOpen()) return;
  flushOpacitySave();
  highlightOptions.hidden = true;
  highlightColorBtn.setAttribute("aria-expanded", "false");
  if (returnFocus) highlightColorBtn.focus();
}

function closeHighlightOptions({ returnFocus = false } = {}) {
  closeStyleCard({ returnFocus });
  closeSettingsCard({ returnFocus });
}

highlightStyleBtn.addEventListener("click", () => {
  if (isStyleCardOpen()) closeStyleCard();
  else openStyleCard(highlightStyleBtn);
});

// A right-click on the tool is the other way to the style card.
hlToolBtn.addEventListener("contextmenu", (ev) => {
  ev.preventDefault();
  openStyleCard(hlToolBtn);
});

highlightColorBtn.addEventListener("click", () => {
  if (isSettingsCardOpen()) closeSettingsCard();
  else openSettingsCard();
});

styleItems().forEach((item) => {
  item.addEventListener("click", () => {
    closeStyleCard({ returnFocus: true });
    chooseHighlightStyle(item.dataset.style);
  });
});

// A menu's keys: the arrows (and Home/End) move, Enter or Space choose (the
// button's own click), a style's letter chooses it, Esc closes. Each stops
// here, so the page does not also turn or toggle on it.
const STYLE_CARD_MOVES = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
highlightStyleCard.addEventListener("keydown", (ev) => {
  const items = styleItems();
  const at = items.indexOf(document.activeElement);
  let next = null;
  if (ev.key in STYLE_CARD_MOVES) next = items[(at + STYLE_CARD_MOVES[ev.key] + items.length) % items.length];
  else if (ev.key === "Home") next = items[0];
  else if (ev.key === "End") next = items[items.length - 1];
  if (next) {
    ev.preventDefault();
    ev.stopPropagation();
    next.focus();
    return;
  }
  if (ev.key === "Escape") {
    ev.stopPropagation();
    closeStyleCard({ returnFocus: true });
    return;
  }
  const style = !ev.ctrlKey && !ev.metaKey && !ev.altKey && highlightStyleForKey(ev.key);
  if (style) {
    ev.preventDefault();
    ev.stopPropagation();
    items.find((item) => item.dataset.style === style).click();
  }
});

highlightOptions.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  // Esc belongs to the card here: reading.js's own Esc closes the find bar.
  ev.stopPropagation();
  closeSettingsCard({ returnFocus: true });
});

// A press anywhere else closes a card, the way a menu does; its own button's
// click toggles it.
document.addEventListener("pointerdown", (ev) => {
  if (isStyleCardOpen() && !highlightStyleCard.contains(ev.target) && !highlightStyleBtn.contains(ev.target)) {
    closeStyleCard();
  }
  if (isSettingsCardOpen() && !highlightOptions.contains(ev.target) && !highlightColorBtn.contains(ev.target)) {
    closeSettingsCard();
  }
});

// Tabbing out of a card closes it too, as a click elsewhere would.
function closeOnFocusLeaving(card, opener, close) {
  card.addEventListener("focusout", (ev) => {
    const next = ev.relatedTarget;
    if (!next || card.contains(next) || next === opener) return;
    close();
  });
}
closeOnFocusLeaving(highlightStyleCard, highlightStyleBtn, () => closeStyleCard());
closeOnFocusLeaving(highlightOptions, highlightColorBtn, () => closeSettingsCard());

// Called once by reading.js, after initHighlightColors (the palette).
async function initHighlightOptions(options, activeColor) {
  highlightOptionsState = options;
  popoverOpacity = createOpacityControl(highlightOpacityControl, {
    onChange: setActiveHighlightOpacity,
    onRelease: flushOpacitySave,
  });
  popoverThickness = createThicknessControl(highlightThicknessGroup, { selectOnArrow: true }, {
    onPick: setActiveInkWidth,
  });
  initHighlightEditorControls();
  await buildSwatches(highlightOptionsSwatches, setActiveHighlightColor);
  showActiveHighlightColor(activeColor);
}
