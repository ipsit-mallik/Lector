// Highlight colors: five, fixed (docs/PRD.md — no free picker). Two places
// choose one:
//
// * The settings card the rail's preview button opens (highlight-options.js). That choice is the *active* color every new
//   highlight is drawn in, by mouse or by voice, and it is an app-wide setting
//   (api.py's set_highlight_color), so it is still the reader's color in the
//   next PDF.
// * The editor an existing highlight, underline, strikethrough or drawing
//   opens, which recolors it, changes its opacity (and a drawing's
//   thickness) or removes it. Every change is undoable, the same as adding
//   one (features/reading/document.py); one slider drag is one undo step.
//
// Every highlight on a visible page gets a transparent <button> laid exactly
// over it (a "target"). That one element is what makes a highlight both
// clickable and reachable with Tab, and it carries the highlight's accessible
// name ("Green highlight: “quick brown fox”"), so the page image, which has no
// DOM text of its own, still exposes its highlights to a keyboard and to a
// screen reader. While the highlight tool is on, targets stop taking the
// pointer so a drag can start on top of a highlight; a press there with no
// drag still opens the editor, through highlightAtPoint() (reading.js).
//
// The color values themselves are theme.css's --highlight-<name> tokens; this
// file only ever names them. It knows nothing about the two layouts either:
// reading.js tells it, through configureHighlightColors(), which page elements
// are on screen and what to do once a highlight has changed.

const highlightTargetLayer = document.getElementById("highlightTargetLayer");
const highlightEditor = document.getElementById("highlightEditor");
const highlightEditorSwatches = document.getElementById("highlightEditorSwatches");
const highlightEditorRemoveBtn = document.getElementById("highlightEditorRemoveBtn");
const highlightEditorOpacity = document.getElementById("highlightEditorOpacity");
const highlightEditorThickness = document.getElementById("highlightEditorThickness");
const highlightEditorThicknessGroup = document.getElementById("highlightEditorThicknessGroup");

// The longest stretch of highlighted text read out in a target's name; the
// rest is cut with an ellipsis, so a highlighted paragraph is not read in full
// every time focus passes over it.
const HIGHLIGHT_NAME_MAX_CHARS = 80;
// How far outside a highlight's own box a press still counts as landing on it,
// in PDF points — the same allowance reading.js gives a press on a character.
const HIGHLIGHT_HIT_PAD = 1.5;
// Gap between a highlight and the editor it opens, and between the editor and
// the page area's edges.
const HIGHLIGHT_EDITOR_GAP_PX = 8;

let highlightPalette = []; // color names, in swatch order
let highlightHooks = null;
// get_page_highlights' reply per page index, as the promise (so a repaint while
// a fetch is in flight joins it) and, once it has arrived, the value (so
// highlightAtPoint can answer synchronously, mid-mouseup). No invalidation on
// zoom: rects are in PDF points. Dropped for a page whenever its highlights
// change (forgetPageHighlights).
const highlightRequests = new Map();
const highlightData = new Map();
// The target <button>s now in the layer, by "<page>:<id>". Kept and moved on a
// repaint rather than rebuilt, so the one holding keyboard focus keeps it while
// the reader scrolls.
const highlightTargets = new Map();
let highlightPaintQueued = false;
let highlightEditing = null; // { pageIndex, highlight, key } while the editor is open
// The editor's opacity and thickness controls (highlight-options.js).
let editorOpacity = null;
let editorThickness = null;
// The gesture the editor's next change belongs to (a slider drag, a key
// press, a tick, a preset): api.set_highlight_appearance makes each one undo
// step. At most one change is in flight; the newest waiting one replaces any
// other waiting.
let editorGesture = null;
let editorGestureSeq = 0;
let appearanceInFlight = false;
let appearancePending = null;

// hooks: {
//   visiblePages()     [{ index, el }] for every page image on screen now,
//   afterChange(state) brings the view in step after a recolor or removal
//                      (the reply carries `affected_page`),
//   returnFocus()      puts focus back on the reader,
// }
function configureHighlightColors(hooks) {
  highlightHooks = hooks;
}

function highlightColorLabel(name) {
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : "";
}

function swatchColor(name) {
  return `var(--highlight-${name})`;
}

// --- Swatches -------------------------------------------------------------- //
// One per palette color, as a radiogroup (ui.js's initRadioCards: one Tab stop,
// the arrows move, Enter, Space or a click chooses). The arrows only move:
// choosing writes a setting or an undo step, and one for every swatch passed
// over on the way to the wanted one would be noise.

function buildSwatches(group, onPick) {
  group.replaceChildren(...highlightPalette.map((name) => {
    const swatch = document.createElement("button");
    swatch.type = "button";
    swatch.className = "swatch";
    swatch.dataset.color = name;
    swatch.style.setProperty("--swatch", swatchColor(name));
    swatch.setAttribute("aria-label", highlightColorLabel(name));
    swatch.title = highlightColorLabel(name);
    // A check as well as the ring: which swatch is chosen must not be told by
    // color alone, least of all in a row of colors.
    const check = document.createElement("span");
    check.className = "swatch-check";
    check.dataset.icon = "check";
    swatch.append(check);
    swatch.addEventListener("click", () => onPick(name));
    return swatch;
  }));
  initRadioCards(group, { selectOnArrow: false });
  return mountIcons(group);
}

function markSwatch(group, name) {
  group.querySelectorAll(".swatch").forEach((swatch) => {
    swatch.classList.toggle("selected", swatch.dataset.color === name);
  });
}

function focusChosenSwatch(group) {
  const swatch = group.querySelector(".swatch.selected") || group.querySelector(".swatch");
  if (swatch) swatch.focus();
}

// --- Targets over existing highlights -------------------------------------- //

function pageHighlights(pageIndex) {
  if (!highlightRequests.has(pageIndex)) {
    const request = callApi("get_page_highlights", pageIndex).then((page) => {
      // Only the request still on file may store its answer: one that was
      // forgotten while in flight answers for highlights that have changed.
      if (highlightRequests.get(pageIndex) === request) highlightData.set(pageIndex, page);
      return page;
    });
    highlightRequests.set(pageIndex, request);
  }
  return highlightRequests.get(pageIndex);
}

// After a highlight is added, recolored, removed, undone or redone on a page;
// with no page, after anything that could have changed them all.
function forgetPageHighlights(pageIndex) {
  if (pageIndex == null) {
    highlightRequests.clear();
    highlightData.clear();
    return;
  }
  highlightRequests.delete(pageIndex);
  highlightData.delete(pageIndex);
}

// While the highlight tool is on, a press on a highlight has to start a drag
// (reading.js), so targets let the pointer through to the page.
function setHighlightTargetsPassive(passive) {
  highlightTargetLayer.classList.toggle("is-selecting", passive);
}

function paintHighlightTargets() {
  if (highlightPaintQueued) return;
  highlightPaintQueued = true;
  requestAnimationFrame(() => {
    highlightPaintQueued = false;
    drawHighlightTargets();
  });
}

// What each kind of markup is called in a target's name: "Green underline:
// “quick brown fox”".
const HIGHLIGHT_KIND_NAMES = { highlight: "highlight", underline: "underline", strikethrough: "strikethrough" };

function highlightTargetName(highlight) {
  if (highlight.kind === "ink") {
    return highlight.color ? `${highlightColorLabel(highlight.color)} drawing` : "Drawing";
  }
  const kind = HIGHLIGHT_KIND_NAMES[highlight.style] || HIGHLIGHT_KIND_NAMES.highlight;
  const color = highlight.color ? `${highlightColorLabel(highlight.color)} ${kind}` : highlightColorLabel(kind);
  let text = highlight.text || "";
  if (text.length > HIGHLIGHT_NAME_MAX_CHARS) text = `${text.slice(0, HIGHLIGHT_NAME_MAX_CHARS).trimEnd()}…`;
  return text ? `${color}: “${text}”` : color;
}

function createHighlightTarget(key) {
  const target = document.createElement("button");
  target.type = "button";
  target.className = "highlight-target";
  target.setAttribute("aria-haspopup", "dialog");
  target.addEventListener("click", () => {
    const { pageIndex, highlight } = target._highlight;
    if (highlightEditing && highlightEditing.key === key) closeHighlightEditor({ returnFocus: true });
    else openHighlightEditor(pageIndex, highlight);
  });
  highlightTargets.set(key, target);
  return target;
}

// One child box per line of the highlight, positioned inside a button that
// spans them all. The pointer lands on the lines only (reading.css), so the
// space beside a highlight that starts or ends mid-line stays plain page.
function layoutHighlightTarget(target, highlight, pageRect, areaRect, scale) {
  const boxes = highlight.rects.map((r) => ({
    left: pageRect.left - areaRect.left + r.x0 * scale,
    top: pageRect.top - areaRect.top + r.y0 * scale,
    width: (r.x1 - r.x0) * scale,
    height: (r.y1 - r.y0) * scale,
  }));
  const left = Math.min(...boxes.map((b) => b.left));
  const top = Math.min(...boxes.map((b) => b.top));
  target.style.left = `${left}px`;
  target.style.top = `${top}px`;
  target.style.width = `${Math.max(...boxes.map((b) => b.left + b.width)) - left}px`;
  target.style.height = `${Math.max(...boxes.map((b) => b.top + b.height)) - top}px`;
  const lines = boxes.map((b) => {
    const line = document.createElement("span");
    line.className = "highlight-target-line";
    line.style.left = `${b.left - left}px`;
    line.style.top = `${b.top - top}px`;
    line.style.width = `${b.width}px`;
    line.style.height = `${b.height}px`;
    return line;
  });
  target.replaceChildren(...lines);
}

// A drawing's target is the box it paints, and inside it an SVG in PDF points
// whose stroke-shaped path is what takes the pointer, so the paper inside a
// circle drawn round something stays plain page. Its hover and focus rings
// are the stroke's outline, cut out of a wider stroke with a mask, so they
// ring the drawing as a text highlight's ring its lines.
const INK_SVG_NS = "http://www.w3.org/2000/svg";

function inkSvgElement(name, attrs) {
  const el = document.createElementNS(INK_SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

function layoutInkTarget(target, highlight, key, pageRect, areaRect, scale) {
  const box = highlight.rects[0];
  target.style.left = `${pageRect.left - areaRect.left + box.x0 * scale}px`;
  target.style.top = `${pageRect.top - areaRect.top + box.y0 * scale}px`;
  target.style.width = `${(box.x1 - box.x0) * scale}px`;
  target.style.height = `${(box.y1 - box.y0) * scale}px`;
  // Rebuilt only when the drawing or the zoom changed, so a scroll just moves
  // it. The path is part of it: a straightened stroke, undone, can come back
  // under the xref it had (MuPDF reuses a freed one).
  const d = highlight.strokes.map((stroke) => pathToSvgD(stroke)).join("");
  const signature = `${scale.toFixed(4)}:${highlight.width}:${d}`;
  if (target._inkSignature === signature) return;
  target._inkSignature = signature;
  // Ring widths are screen pixels, the same as a text highlight's.
  const ringPt = (px) => highlight.width + (2 * px) / scale;
  const pad = ringPt(3);
  const maskId = `ink-ring-${key.replace(":", "-")}`;
  const svg = inkSvgElement("svg", {
    class: "ink-target-svg",
    viewBox: `${box.x0} ${box.y0} ${box.x1 - box.x0} ${box.y1 - box.y0}`,
    preserveAspectRatio: "none",
    "aria-hidden": "true",
  });
  const area = {
    x: box.x0 - pad,
    y: box.y0 - pad,
    width: box.x1 - box.x0 + 2 * pad,
    height: box.y1 - box.y0 + 2 * pad,
  };
  const mask = inkSvgElement("mask", { id: maskId, maskUnits: "userSpaceOnUse", ...area });
  mask.append(
    inkSvgElement("rect", { ...area, fill: "white" }),
    inkSvgElement("path", { d, fill: "none", stroke: "black", "stroke-width": highlight.width, "stroke-linecap": "round", "stroke-linejoin": "round" }),
  );
  const ring = (cls, px) => inkSvgElement("path", {
    class: cls, d, fill: "none", mask: `url(#${maskId})`,
    "stroke-width": ringPt(px), "stroke-linecap": "round", "stroke-linejoin": "round",
  });
  const hit = inkSvgElement("path", {
    class: "ink-target-hit", d, fill: "none", stroke: "transparent",
    "stroke-width": highlight.width, "stroke-linecap": "round", "stroke-linejoin": "round",
  });
  svg.append(mask, ring("ink-target-ring is-hover", 1), ring("ink-target-ring is-focus", 2), hit);
  target.replaceChildren(svg);
}

async function drawHighlightTargets() {
  if (!highlightHooks) return;
  const pages = highlightHooks.visiblePages();
  const replies = await Promise.all(pages.map(({ index }) => pageHighlights(index)));
  const areaRect = highlightTargetLayer.getBoundingClientRect();
  const seen = new Set();
  pages.forEach(({ index, el }, i) => {
    const page = replies[i];
    if (!page || !page.width) return;
    const pageRect = el.getBoundingClientRect();
    const scale = pageRect.width / page.width;
    if (!scale) return;
    for (const highlight of page.highlights) {
      if (!highlight.rects.length) continue;
      const key = `${index}:${highlight.id}`;
      seen.add(key);
      const target = highlightTargets.get(key) || createHighlightTarget(key);
      target._highlight = { pageIndex: index, highlight };
      // The editor works on the newest reply, so a change it made itself
      // (an opacity drag) is what it shows and starts from.
      if (highlightEditing && highlightEditing.key === key) highlightEditing.highlight = highlight;
      target.setAttribute("aria-label", highlightTargetName(highlight));
      target.classList.toggle("is-editing", Boolean(highlightEditing && highlightEditing.key === key));
      target.classList.toggle("ink-target", highlight.kind === "ink");
      if (highlight.kind === "ink") {
        layoutInkTarget(target, highlight, key, pageRect, areaRect, scale);
      } else {
        target._inkSignature = null;
        layoutHighlightTarget(target, highlight, pageRect, areaRect, scale);
      }
      if (target.parentNode !== highlightTargetLayer) highlightTargetLayer.append(target);
    }
  });
  for (const [key, target] of highlightTargets) {
    if (seen.has(key)) continue;
    target.remove();
    highlightTargets.delete(key);
  }
  if (highlightEditing) {
    if (highlightTargets.has(highlightEditing.key)) positionHighlightEditor();
    else closeHighlightEditor();
  }
}

// The highlight under a point given in PDF points on one page, if any — the
// topmost (last drawn) when highlights overlap. Answers only from what has
// already been fetched, which for any page the reader can see is everything.
function highlightAtPoint(pageIndex, x, y) {
  const page = highlightData.get(pageIndex);
  if (!page) return null;
  for (let i = page.highlights.length - 1; i >= 0; i--) {
    const highlight = page.highlights[i];
    if (highlight.kind === "ink") {
      // On the stroke itself, not anywhere in the box round it.
      const reach = highlight.width / 2 + HIGHLIGHT_HIT_PAD;
      if (highlight.strokes.some((stroke) => distanceToPath([x, y], stroke) <= reach)) return highlight;
      continue;
    }
    const hit = highlight.rects.some((r) =>
      x >= r.x0 - HIGHLIGHT_HIT_PAD && x <= r.x1 + HIGHLIGHT_HIT_PAD
        && y >= r.y0 - HIGHLIGHT_HIT_PAD && y <= r.y1 + HIGHLIGHT_HIT_PAD);
    if (hit) return highlight;
  }
  return null;
}

// --- The editor ------------------------------------------------------------ //

function isHighlightEditorOpen() {
  return highlightEditing !== null;
}

function openHighlightEditor(pageIndex, highlight) {
  closeHighlightOptions();
  const key = `${pageIndex}:${highlight.id}`;
  highlightEditing = { pageIndex, highlight, key };
  highlightTargets.forEach((target, k) => target.classList.toggle("is-editing", k === key));
  // A color another app wrote is none of the five, so no swatch is chosen.
  markSwatch(highlightEditorSwatches, highlight.color);
  showEditedAppearance(highlight);
  highlightEditor.setAttribute("aria-label", highlightTargetName(highlight));
  highlightEditor.hidden = false;
  positionHighlightEditor();
  focusChosenSwatch(highlightEditorSwatches);
}

function closeHighlightEditor({ returnFocus = false } = {}) {
  if (!highlightEditing) return;
  const { key } = highlightEditing;
  highlightEditing = null;
  highlightEditor.hidden = true;
  highlightTargets.forEach((target) => target.classList.remove("is-editing"));
  if (!returnFocus) return;
  const target = highlightTargets.get(key);
  if (target) target.focus({ preventScroll: true });
  else if (highlightHooks) highlightHooks.returnFocus();
}

// Under the highlight's last line, or over its first when there is no room
// below; kept inside the page area either way.
function positionHighlightEditor() {
  const target = highlightTargets.get(highlightEditing.key);
  if (!target) return;
  // A drawing has no lines: it is placed under the box it paints.
  const lines = target.classList.contains("ink-target") ? [target] : target.querySelectorAll(".highlight-target-line");
  if (!lines.length) return;
  const area = highlightTargetLayer.getBoundingClientRect();
  const first = lines[0].getBoundingClientRect();
  const last = lines[lines.length - 1].getBoundingClientRect();
  const width = highlightEditor.offsetWidth;
  const height = highlightEditor.offsetHeight;
  const maxLeft = area.width - width - HIGHLIGHT_EDITOR_GAP_PX;
  const left = Math.max(HIGHLIGHT_EDITOR_GAP_PX, Math.min(last.left - area.left, maxLeft));
  let top = last.bottom - area.top + HIGHLIGHT_EDITOR_GAP_PX;
  const above = top + height > area.height - HIGHLIGHT_EDITOR_GAP_PX;
  if (above) {
    top = Math.max(HIGHLIGHT_EDITOR_GAP_PX, first.top - area.top - height - HIGHLIGHT_EDITOR_GAP_PX);
  }
  // Its entrance grows from the highlight, so from its bottom edge up here.
  highlightEditor.classList.toggle("is-above", above);
  highlightEditor.style.left = `${left}px`;
  highlightEditor.style.top = `${top}px`;
}

// The editor's opacity slider and (for a drawing) its thickness presets, set
// to the highlight's own. The emphasised tick is its style's default; a
// drawing's is the Highlight default, as new strokes use.
function showEditedAppearance(highlight) {
  if (!editorOpacity) return;
  const ink = highlight.kind === "ink";
  const style = ink ? "highlight" : highlight.style || "highlight";
  const pct = Math.round((highlight.opacity == null ? 1 : highlight.opacity) * 100);
  editorOpacity.set(Math.min(100, Math.max(10, pct)), defaultOpacityPct(style));
  highlightEditorThickness.hidden = !ink;
  if (ink) {
    editorThickness.set(highlight.width);
    editorThickness.paint(swatchColor(highlight.color || activeHighlightColor), highlight.opacity);
  }
}

function newEditorGesture() {
  editorGestureSeq += 1;
  editorGesture = `edit-${editorGestureSeq}`;
}

// A live change to the edited highlight's opacity (whole percent) or width.
async function changeEditedAppearance(change) {
  if (!highlightEditing) return;
  const { pageIndex, highlight } = highlightEditing;
  appearancePending = { ...(appearancePending || {}), pageIndex, id: highlight.id, gesture: editorGesture, ...change };
  if (appearanceInFlight) return;
  appearanceInFlight = true;
  try {
    while (appearancePending) {
      const next = appearancePending;
      appearancePending = null;
      const state = await callApi(
        "set_highlight_appearance", next.pageIndex, next.id,
        next.opacity == null ? null : next.opacity, next.width == null ? null : next.width, next.gesture,
      );
      if (state && state.affected_page != null) await highlightHooks.afterChange(state);
    }
  } finally {
    appearanceInFlight = false;
  }
}

async function recolorEditedHighlight(name) {
  if (!highlightEditing) return;
  const { pageIndex, highlight } = highlightEditing;
  closeHighlightEditor({ returnFocus: true });
  if (name === highlight.color) return;
  const state = await callApi("recolor_highlight", pageIndex, highlight.id, name);
  await highlightHooks.afterChange(state);
}

async function removeEditedHighlight() {
  if (!highlightEditing) return;
  const { pageIndex, highlight } = highlightEditing;
  closeHighlightEditor();
  // Its target is about to go, so focus goes back to the page, where Tab
  // reaches the next highlight.
  highlightHooks.returnFocus();
  const state = await callApi("remove_highlight", pageIndex, highlight.id);
  await highlightHooks.afterChange(state);
}

highlightEditorRemoveBtn.addEventListener("click", removeEditedHighlight);

highlightEditor.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  ev.stopPropagation();
  closeHighlightEditor({ returnFocus: true });
});

// A press anywhere else closes the editor, the way a menu does. A press on a
// target is left to the target's own click, which opens that highlight's
// editor (or closes this one, if it is the same highlight).
document.addEventListener("pointerdown", (ev) => {
  if (highlightEditing && !highlightEditor.contains(ev.target) && !ev.target.closest(".highlight-target")) {
    closeHighlightEditor();
  }
});

// Called once by reading.js, before initHighlightOptions (which builds the
// popover's own swatches from the palette fetched here).
async function initHighlightColors() {
  highlightPalette = await callApi("get_highlight_palette");
  await buildSwatches(highlightEditorSwatches, recolorEditedHighlight);
}

// Called by initHighlightOptions once the options (the slider's range, the
// presets) have arrived.
function initHighlightEditorControls() {
  editorOpacity = createOpacityControl(highlightEditorOpacity, {
    onGesture: newEditorGesture,
    onChange: (pct) => changeEditedAppearance({ opacity: pct }),
  });
  editorThickness = createThicknessControl(highlightEditorThicknessGroup, { selectOnArrow: false }, {
    onPick: (width) => {
      newEditorGesture();
      editorThickness.set(width);
      changeEditedAppearance({ width });
    },
  });
}
