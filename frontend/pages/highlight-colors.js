// Highlight colors: five, fixed (docs/PRD.md — no free picker). Two places
// choose one:
//
// * The rail's color button, under the highlight tool, and its flyout of
//   swatches. That choice is the *active* color every new highlight is drawn
//   in, by mouse or by voice, and it is an app-wide setting (api.py's
//   set_highlight_color), so it is still the reader's color in the next PDF.
// * The editor an existing highlight opens, which recolors it or removes it.
//   Both are undoable, the same as adding one (features/reading/document.py).
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

const highlightColorBtn = document.getElementById("highlightColorBtn");
const highlightColorFlyout = document.getElementById("highlightColorFlyout");
const highlightTargetLayer = document.getElementById("highlightTargetLayer");
const highlightEditor = document.getElementById("highlightEditor");
const highlightEditorSwatches = document.getElementById("highlightEditorSwatches");
const highlightEditorRemoveBtn = document.getElementById("highlightEditorRemoveBtn");

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
let activeHighlightColor = null;
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

// --- The active color (rail button + flyout) -------------------------------- //

function showActiveHighlightColor(name) {
  if (!highlightPalette.includes(name)) return;
  activeHighlightColor = name;
  highlightColorBtn.style.setProperty("--swatch", swatchColor(name));
  const label = `Highlight color: ${highlightColorLabel(name)}`;
  highlightColorBtn.title = label;
  highlightColorBtn.setAttribute("aria-label", label);
  markSwatch(highlightColorFlyout, name);
  // The wash a drag turns into on release (reading.css, .selection-rect.applied)
  // shows the highlight before the re-rendered page does, so it has to be
  // drawn in the color that page is about to show.
  document.documentElement.style.setProperty("--applied-highlight", swatchColor(name));
}

// Also what the voice "highlight color <color>" command calls.
async function setActiveHighlightColor(name) {
  const state = await callApi("set_highlight_color", name);
  showActiveHighlightColor((state && state.highlight_color) || name);
  return state;
}

function isColorFlyoutOpen() {
  return !highlightColorFlyout.hidden;
}

function openColorFlyout() {
  closeHighlightEditor();
  highlightColorFlyout.hidden = false;
  highlightColorBtn.setAttribute("aria-expanded", "true");
  focusChosenSwatch(highlightColorFlyout);
}

function closeColorFlyout({ returnFocus = false } = {}) {
  if (!isColorFlyoutOpen()) return;
  highlightColorFlyout.hidden = true;
  highlightColorBtn.setAttribute("aria-expanded", "false");
  if (returnFocus) highlightColorBtn.focus();
}

highlightColorBtn.addEventListener("click", () => {
  if (isColorFlyoutOpen()) closeColorFlyout();
  else openColorFlyout();
});

highlightColorFlyout.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  // Esc belongs to the flyout here: reading.js's own Esc closes the find bar.
  ev.stopPropagation();
  closeColorFlyout({ returnFocus: true });
});

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

function highlightTargetName(highlight) {
  const color = highlight.color ? `${highlightColorLabel(highlight.color)} highlight` : "Highlight";
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
      target.setAttribute("aria-label", highlightTargetName(highlight));
      target.classList.toggle("is-editing", Boolean(highlightEditing && highlightEditing.key === key));
      layoutHighlightTarget(target, highlight, pageRect, areaRect, scale);
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
  closeColorFlyout();
  const key = `${pageIndex}:${highlight.id}`;
  highlightEditing = { pageIndex, highlight, key };
  highlightTargets.forEach((target, k) => target.classList.toggle("is-editing", k === key));
  // A color another app wrote is none of the five, so no swatch is chosen.
  markSwatch(highlightEditorSwatches, highlight.color);
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
  const lines = target.querySelectorAll(".highlight-target-line");
  if (!lines.length) return;
  const area = highlightTargetLayer.getBoundingClientRect();
  const first = lines[0].getBoundingClientRect();
  const last = lines[lines.length - 1].getBoundingClientRect();
  const width = highlightEditor.offsetWidth;
  const height = highlightEditor.offsetHeight;
  const maxLeft = area.width - width - HIGHLIGHT_EDITOR_GAP_PX;
  const left = Math.max(HIGHLIGHT_EDITOR_GAP_PX, Math.min(last.left - area.left, maxLeft));
  let top = last.bottom - area.top + HIGHLIGHT_EDITOR_GAP_PX;
  if (top + height > area.height - HIGHLIGHT_EDITOR_GAP_PX) {
    top = Math.max(HIGHLIGHT_EDITOR_GAP_PX, first.top - area.top - height - HIGHLIGHT_EDITOR_GAP_PX);
  }
  highlightEditor.style.left = `${left}px`;
  highlightEditor.style.top = `${top}px`;
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

// A press anywhere else closes whichever of the two is open, the way a menu
// does. A press on a target is left to the target's own click, which opens
// that highlight's editor (or closes this one, if it is the same highlight).
document.addEventListener("pointerdown", (ev) => {
  if (isColorFlyoutOpen() && !highlightColorFlyout.contains(ev.target) && !highlightColorBtn.contains(ev.target)) {
    closeColorFlyout();
  }
  if (highlightEditing && !highlightEditor.contains(ev.target) && !ev.target.closest(".highlight-target")) {
    closeHighlightEditor();
  }
});

// Called once by reading.js, with the active color from its first state.
async function initHighlightColors(activeColor) {
  highlightPalette = await callApi("get_highlight_palette");
  await Promise.all([
    buildSwatches(highlightColorFlyout, async (name) => {
      closeColorFlyout({ returnFocus: true });
      await setActiveHighlightColor(name);
    }),
    buildSwatches(highlightEditorSwatches, recolorEditedHighlight),
  ]);
  showActiveHighlightColor(activeColor);
}
