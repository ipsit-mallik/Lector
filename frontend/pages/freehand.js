// The highlight tool's second mode: a freehand marker stroke, for what has no
// text to snap to (a photo, a barcode, blank paper). docs/DESIGN_SYSTEM.md,
// "Freehand marker".
//
// Which mode a press is in is settled by what is under the press, once, and
// kept for the whole drag (chooseHighlightMode): on a character (a text layer
// or OCR text), a text highlight, the selection in reading.js; anywhere else,
// a stroke; Alt held, a stroke regardless. A press on a scanned page whose
// text has not been read yet is a text press, which reads the page (the
// reader presses again once it is done), since until then nothing can say
// whether it was on text; once read, the same rule as any page, and a scan
// with no text to find is all picture. A press before the page's characters have arrived
// is held as a stroke that draws nothing until they have, and is handed to
// the selection if it turns out to have been on text.
//
// While drawing, points are kept as fractions of the page image, so a zoom
// or a scroll mid-stroke does not move them, and shown in an SVG over the
// page whose viewBox is the page in PDF points, so the 14pt width is the same
// on paper at every zoom. One path in one SVG drawn at 60% through Multiply:
// where the stroke crosses itself it does not double up, as in the PDF.
// Shift draws a straight line from the press to the pointer. On release
// (ink-geometry.js's finishStroke) a nearly straight stroke becomes a straight
// line, which the preview eases into over INK_STRAIGHTEN_MS, and both the
// stroke as drawn and the line go to api.add_ink_stroke, so the first Undo
// gives the drawn stroke back. The preview stays until the re-rendered page
// image shows the real annotation (reading.js's commit hook resolves then).
//
// A press and release in one place draws nothing (a click is not a stroke):
// on an existing highlight or drawing it opens the editor, as in text mode.
//
// It knows nothing about the layouts: reading.js tells it, through
// configureFreehand(), how to read a page's text and how to commit.

const inkLayer = document.getElementById("inkLayer");
const SVG_NS = "http://www.w3.org/2000/svg";

// How far the pointer may drift between press and release and still count as
// a click, in CSS pixels (reading.js's CLICK_SLOP_PX for a text press).
const INK_CLICK_SLOP_PX = 3;
// The ease from a stroke as drawn to the straight line it becomes: --dur-fast
// on --ease-out (theme.css), skipped under prefers-reduced-motion.
const INK_STRAIGHTEN_MS = 120;
// Points each path is resampled to for that ease.
const INK_STRAIGHTEN_SAMPLES = 64;
// The brush cursor's diameter in CSS pixels is the stroke's width at the
// current zoom, kept within these: under 6 it is a dot that hides the
// hotspot, and Chromium does not draw a cursor image over 128.
const INK_CURSOR_MIN_PX = 6;
const INK_CURSOR_MAX_PX = 128;

let inkHooks = null;
let inkStyle = { width: 14, opacity: 0.6 }; // api.get_ink_style() replaces it
let inkStroke = null; // the stroke being drawn, from press to release
// Strokes on screen, until the page shows each for real: a second can be
// drawn while the first is still being written.
const inkShown = new Set();
let inkPaintQueued = false;
let inkSettling = Promise.resolve(); // the last released stroke's commit and ease
let inkHover = null; // { el, pageIndex, clientX, clientY, alt } under the pointer
const inkCursorCache = new Map();

// hooks: {
//   loadedPage(i)         the page's get_page_chars reply if it has arrived, else null,
//   pageData(i)           a promise of it (cached by reading.js),
//   isOnText(page, x, y)  whether a point in PDF points is on a character,
//   beginText(el, i, ev)  start a text selection (reading.js's beginSelection),
//   moveText(ev)          / endText(ev): carry it on and release it,
//   commit(i, drawn, straight)  write the stroke; resolves once the page shows it,
//                         to false if nothing was drawn,
//   commitFailed()        tell the reader a stroke was not drawn,
//   clickAt(i, x, y)      a press that did not move: opens the editor of a
//                         highlight there, if there is one,
//   activeColor()         the active highlight colour's name,
//   colorValue(name)      its CSS value (theme.css's --highlight-<name>),
//   toolOn()              whether the highlight tool is on,
//   textDragging()        whether a text selection is being dragged,
//   reducedMotion()       prefers-reduced-motion,
// }
function configureFreehand(hooks) {
  inkHooks = hooks;
}

function setInkStyle(style) {
  if (style && style.width > 0 && style.opacity > 0) inkStyle = { width: style.width, opacity: style.opacity };
  inkCursorCache.clear();
}

// A scanned page whose text has not been read yet, is being read, or failed
// to read (which a press tries again: a timeout may not happen twice): a press
// there cannot tell text from picture until it has been read.
const INK_UNREAD_OCR = new Set(["needed", "running", "error"]);
// How far outside a character's box a press still lands on it, in PDF points
// (reading.js's HIT_PAD, for the same reason).
const INK_TEXT_HIT_PAD = 1.5;
// OCR finds "characters" in pictures too: on a scanned ID card, one letter
// as wide as the photo, and a real line of text whose boxes run 98pt down
// through the barcode under it. A text layer's boxes are its glyphs', but
// on an OCR page a box only counts as text when it is shaped like a glyph:
// no taller than this many times the page's median character height...
const INK_OCR_MAX_HEIGHT_RATIO = 3;
// ...and no wider than this many times its own height.
const INK_OCR_MAX_ASPECT = 2.5;
const inkMedianHeights = new WeakMap();

function medianCharHeight(page) {
  if (!inkMedianHeights.has(page)) {
    const heights = page.chars.map((c) => c.y1 - c.y0).sort((a, b) => a - b);
    inkMedianHeights.set(page, heights.length ? heights[Math.floor(heights.length / 2)] : 0);
  }
  return inkMedianHeights.get(page);
}

// Whether a point (PDF points) is on the page's text: inside a character's
// box, and on an OCR page one shaped like a glyph.
function isPointOnText(page, x, y) {
  const ocr = page.ocr === "done";
  const maxHeight = ocr ? INK_OCR_MAX_HEIGHT_RATIO * medianCharHeight(page) : Infinity;
  return page.chars.some((c) => {
    if (x < c.x0 - INK_TEXT_HIT_PAD || x > c.x1 + INK_TEXT_HIT_PAD) return false;
    if (y < c.y0 - INK_TEXT_HIT_PAD || y > c.y1 + INK_TEXT_HIT_PAD) return false;
    if (!ocr) return true;
    const height = c.y1 - c.y0;
    return height <= maxHeight && c.x1 - c.x0 <= INK_OCR_MAX_ASPECT * height;
  });
}

// "ink" or "text", for a press on `page` (a get_page_chars reply). A scan
// OCR has finished with and found no text on ("no_text"), or that cannot be
// read at all here ("unavailable": no language data), has nothing to
// highlight, so it is drawn on.
function chooseHighlightMode(page, onText, altKey) {
  if (altKey) return "ink";
  // Not read yet: a text press is what reads it.
  if (INK_UNREAD_OCR.has(page.ocr)) return "text";
  return onText ? "text" : "ink";
}

function isDrawingInk() {
  return inkStroke !== null;
}

function inkFraction(rect, ev) {
  const fx = rect.width ? (ev.clientX - rect.left) / rect.width : 0;
  const fy = rect.height ? (ev.clientY - rect.top) / rect.height : 0;
  return [Math.min(1, Math.max(0, fx)), Math.min(1, Math.max(0, fy))];
}

// A fraction of the page image back to a viewport point, where the page is now.
function inkClientPoint(el, [fx, fy]) {
  const rect = el.getBoundingClientRect();
  return { clientX: rect.left + fx * rect.width, clientY: rect.top + fy * rect.height };
}

function inkPagePoints(s) {
  return s.points.map(([fx, fy]) => [fx * s.page.width, fy * s.page.height]);
}

// The press. Settles the mode now if it can, otherwise once the page's
// characters arrive.
function pressHighlightTool(el, pageIndex, ev) {
  const page = inkHooks.loadedPage(pageIndex);
  if (!ev.altKey && page) {
    const [fx, fy] = inkFraction(el.getBoundingClientRect(), ev);
    if (chooseHighlightMode(page, inkHooks.isOnText(page, fx * page.width, fy * page.height), false) === "text") {
      inkHooks.beginText(el, pageIndex, ev);
      return;
    }
  }
  beginInkStroke(el, pageIndex, ev, !ev.altKey && !page);
}

function beginInkStroke(el, pageIndex, ev, pending) {
  const rect = el.getBoundingClientRect();
  const s = {
    el,
    pageIndex,
    page: null,
    pending, // the mode is not known yet
    points: [inkFraction(rect, ev)],
    shift: ev.shiftKey,
    moved: false,
    last: null, // the latest pointer position, as a viewport point
    ended: false,
    cancelled: false,
    color: inkHooks.activeColor(),
    svg: null,
    path: null,
  };
  inkStroke = s;
  inkHooks.pageData(pageIndex).then((page) => {
    if (s.cancelled) return;
    s.page = page;
    if (s.pending) settlePendingPress(s);
    else if (s.ended) completeInkStroke(s);
    else queueInkPaint();
  });
}

function settlePendingPress(s) {
  const [fx, fy] = s.points[0];
  const onText = inkHooks.isOnText(s.page, fx * s.page.width, fy * s.page.height);
  if (chooseHighlightMode(s.page, onText, false) === "ink") {
    s.pending = false;
    if (s.ended) completeInkStroke(s);
    else queueInkPaint();
    return;
  }
  // A text press after all: the selection takes over from where it began.
  s.cancelled = true;
  if (inkStroke === s) inkStroke = null;
  inkHooks.beginText(s.el, s.pageIndex, inkClientPoint(s.el, s.points[0]));
  if (s.last) inkHooks.moveText(s.last);
  if (s.ended) inkHooks.endText(s.last || inkClientPoint(s.el, s.points[0]));
}

function onInkPointerMove(ev) {
  const s = inkStroke;
  if (!s) return;
  const rect = s.el.getBoundingClientRect();
  // Every position the pointer passed through since the last frame, not only
  // the last one, so a fast stroke keeps its shape.
  const coalesced = typeof ev.getCoalescedEvents === "function" ? ev.getCoalescedEvents() : [];
  for (const e of coalesced.length ? coalesced : [ev]) s.points.push(inkFraction(rect, e));
  s.shift = ev.shiftKey;
  s.last = { clientX: ev.clientX, clientY: ev.clientY };
  if (!s.moved) {
    const [fx, fy] = s.points[0];
    const [lx, ly] = s.points[s.points.length - 1];
    s.moved = Math.hypot((lx - fx) * rect.width, (ly - fy) * rect.height) > INK_CLICK_SLOP_PX;
  }
  queueInkPaint();
}

function onInkRelease(ev) {
  const s = inkStroke;
  // Only the button that started the stroke ends it.
  if (!s || (ev && ev.button !== 0)) return;
  inkStroke = null;
  s.ended = true;
  if (ev) s.shift = ev.shiftKey;
  // Not settled yet: the page's characters are still on their way.
  if (s.pending || !s.page) return;
  completeInkStroke(s);
}

// Esc mid-stroke: nothing is drawn and nothing written.
function cancelInkStroke() {
  const s = inkStroke;
  if (!s) return false;
  s.cancelled = true;
  inkStroke = null;
  clearInkPreview(s);
  return true;
}

// The straight line a Shift drag draws: from the press to the pointer, made
// horizontal or vertical when close to either, its start kept under the press.
function inkShiftLine(points) {
  const { start, end } = snapToAxis(points[0], points[points.length - 1], { keepStart: true });
  return [start, end];
}

function completeInkStroke(s) {
  const points = inkPagePoints(s);
  if (!s.moved) {
    clearInkPreview(s);
    inkHooks.clickAt(s.pageIndex, points[0][0], points[0][1]);
    return;
  }
  let drawn;
  let straight = null;
  if (s.shift) drawn = inkShiftLine(points);
  else ({ drawn, straight } = finishStroke(points));
  const eased = straight ? easeInkStroke(s, drawn, straight) : (paintInkPath(s, drawn), Promise.resolve());
  const committed = commitInkPreview(s.pageIndex, drawn, straight);
  inkSettling = Promise.all([eased, committed]).then(() => clearInkPreview(s));
}

// The commit, never rejecting: a stroke that was not drawn (the bridge
// failed, or refused it and drew nothing) is reported through commitFailed,
// so the reader is told rather than seeing the preview just vanish.
function commitInkPreview(pageIndex, drawn, straight) {
  // Called now, not a microtask later: the write starts on the release.
  return Promise.resolve(inkHooks.commit(pageIndex, drawn, straight))
    .then((wasDrawn) => {
      if (wasDrawn === false) inkHooks.commitFailed();
    }, (err) => {
      console.error("add_ink_stroke failed:", err);
      inkHooks.commitFailed();
    });
}

// The preview eased from the stroke as drawn to the straight line it became.
function easeInkStroke(s, from, to) {
  if (inkHooks.reducedMotion()) {
    paintInkPath(s, to);
    return Promise.resolve();
  }
  const a = resamplePath(from, INK_STRAIGHTEN_SAMPLES);
  const b = resamplePath(to, INK_STRAIGHTEN_SAMPLES);
  return new Promise((resolve) => {
    let startedAt = null;
    const step = (now) => {
      if (startedAt === null) startedAt = now;
      const t = Math.min(1, (now - startedAt) / INK_STRAIGHTEN_MS);
      const eased = 1 - (1 - t) ** 3; // close to --ease-out
      paintInkPath(s, t < 1 ? lerpPath(a, b, eased) : to);
      if (t < 1) requestAnimationFrame(step);
      else resolve();
    };
    paintInkPath(s, from);
    requestAnimationFrame(step);
  });
}

// --- The preview ----------------------------------------------------------- //

function queueInkPaint() {
  if (inkPaintQueued) return;
  inkPaintQueued = true;
  requestAnimationFrame(() => {
    inkPaintQueued = false;
    const s = inkStroke;
    if (!s || !s.page || s.pending) return;
    const points = inkPagePoints(s);
    paintInkPath(s, s.shift ? inkShiftLine(points) : points);
  });
}

function ensureInkSvg(s) {
  if (s.svg) return;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "ink-preview");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.style.opacity = String(inkStyle.opacity);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("stroke-width", String(inkStyle.width));
  path.style.stroke = `var(--highlight-${s.color})`;
  svg.appendChild(path);
  inkLayer.appendChild(svg);
  s.svg = svg;
  s.path = path;
}

// Laid over the page image wherever it is now, its viewBox the page in PDF
// points, so the path is in the same points the stroke is committed in.
function placeInkSvg(s) {
  const rect = s.el.getBoundingClientRect();
  const area = inkLayer.getBoundingClientRect();
  s.svg.setAttribute("viewBox", `0 0 ${s.page.width} ${s.page.height}`);
  s.svg.style.left = `${rect.left - area.left}px`;
  s.svg.style.top = `${rect.top - area.top}px`;
  s.svg.style.width = `${rect.width}px`;
  s.svg.style.height = `${rect.height}px`;
}

function paintInkPath(s, points) {
  if (s.cancelled) return;
  ensureInkSvg(s);
  placeInkSvg(s);
  s.path.setAttribute("d", pathToSvgD(points));
  inkShown.add(s);
}

// After a scroll, a resize or a zoom: the preview follows the page.
function repaintInkStroke() {
  inkShown.forEach((s) => {
    if (s.svg && !s.cancelled) placeInkSvg(s);
  });
}

function clearInkPreview(s) {
  if (s.svg) s.svg.remove();
  s.svg = null;
  s.path = null;
  inkShown.delete(s);
}

// --- The cursor ------------------------------------------------------------ //
// While the tool is on, the pointer says which mode a press there would be
// in: a highlighter over text, a round brush the size of the stroke at this
// zoom elsewhere, and the brush anywhere while Alt is held. Not changed while
// dragging, since the mode is not either.

function inkSvgCursor(svg, hotX, hotY, fallback) {
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") ${hotX} ${hotY}, ${fallback}`;
}

function brushCursor(diameter, color) {
  const key = `brush:${diameter}:${color}`;
  if (!inkCursorCache.has(key)) {
    const size = diameter + 4;
    const c = size / 2;
    const r = diameter / 2;
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='${size}' height='${size}'>`
      + `<circle cx='${c}' cy='${c}' r='${r}' fill='${color}' fill-opacity='${inkStyle.opacity}' stroke='#161A18' stroke-opacity='0.7'/>`
      + `<circle cx='${c}' cy='${c}' r='${Math.max(r - 1, 0.5)}' fill='none' stroke='#FFFFFF' stroke-opacity='0.85'/>`
      + "</svg>";
    inkCursorCache.set(key, inkSvgCursor(svg, Math.round(c), Math.round(c), "crosshair"));
  }
  return inkCursorCache.get(key);
}

// A chisel-tip marker leaning right, its tip (the hotspot) at bottom left,
// the tip in the active colour.
function highlighterCursor(color) {
  const key = `marker:${color}`;
  if (!inkCursorCache.has(key)) {
    const svg = "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'>"
      + "<g transform='rotate(45 12 12)' stroke='#161A18' stroke-width='1.2' stroke-linejoin='round'>"
      + "<rect x='8.5' y='1' width='7' height='13' rx='1.5' fill='#FFFFFF'/>"
      + `<path d='M8.5 14h7l-1.5 3.5h-4z' fill='${color}'/>`
      + `<path d='M10 17.5h4l-0.8 3.3h-2.4z' fill='${color}'/>`
      + "</g></svg>";
    inkCursorCache.set(key, inkSvgCursor(svg, 5, 19, "text"));
  }
  return inkCursorCache.get(key);
}

function noteInkHover(el, pageIndex, ev) {
  inkHover = { el, pageIndex, clientX: ev.clientX, clientY: ev.clientY, alt: ev.altKey };
  refreshInkCursor();
}

function forgetInkHover(el) {
  if (inkHover && inkHover.el === el) inkHover = null;
}

function refreshInkCursor() {
  const h = inkHover;
  if (!h || !inkHooks || !inkHooks.toolOn()) return;
  if (inkStroke || inkHooks.textDragging()) return;
  const color = inkHooks.colorValue(inkHooks.activeColor()) || "#F7DE7A";
  const page = inkHooks.loadedPage(h.pageIndex);
  if (!page) {
    // Asked for once, by the hover, so a press here usually finds it waiting.
    inkHooks.pageData(h.pageIndex).then(() => {
      if (inkHover === h) refreshInkCursor();
    });
    h.el.style.cursor = h.alt ? "crosshair" : "";
    return;
  }
  const rect = h.el.getBoundingClientRect();
  const scale = page.width ? rect.width / page.width : 0;
  const x = scale ? (h.clientX - rect.left) / scale : 0;
  const y = scale ? (h.clientY - rect.top) / scale : 0;
  const mode = chooseHighlightMode(page, inkHooks.isOnText(page, x, y), h.alt);
  if (mode === "text") {
    h.el.style.cursor = highlighterCursor(color);
    return;
  }
  const diameter = Math.round(Math.min(INK_CURSOR_MAX_PX, Math.max(INK_CURSOR_MIN_PX, inkStyle.width * scale)));
  h.el.style.cursor = brushCursor(diameter, color);
}

// The tool going off leaves the page's own cursor.
function clearInkCursors(els) {
  inkHover = null;
  els.forEach((el) => { el.style.cursor = ""; });
}

// --- Keys and the document's pointer --------------------------------------- //

document.addEventListener("pointermove", onInkPointerMove);
document.addEventListener("mouseup", onInkRelease);

function onInkModifierKey(ev) {
  if (ev.key === "Shift" && inkStroke) {
    inkStroke.shift = ev.type === "keydown";
    queueInkPaint();
    return;
  }
  if (ev.key !== "Alt" || !inkHooks || !inkHooks.toolOn()) return;
  // Alt is the tool's own modifier while it is on: kept from the web view,
  // which would otherwise hand its release to the window's menu.
  ev.preventDefault();
  if (inkHover) {
    inkHover.alt = ev.type === "keydown";
    refreshInkCursor();
  }
}

document.addEventListener("keydown", onInkModifierKey);
document.addEventListener("keyup", onInkModifierKey);
