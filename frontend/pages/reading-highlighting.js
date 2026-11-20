// The highlight tool on the page: turning it on and off, the characters under
// a drag, the drag selection, writing highlights and freehand strokes,
// undo/redo, and bringing the page images in step after any of them.

let highlightMode = false;

let selection = null; // the drag in progress, if any — see beginSelection()

function setHighlightMode(on) {
  highlightMode = on;
  highlightBtn.classList.toggle("active", on);
  highlightBtn.setAttribute("aria-pressed", String(on));
  const surfaces = [...document.querySelectorAll(".page-surface")];
  surfaces.forEach((el) => {
    el.classList.toggle("selecting", on);
  });
  setHighlightTargetsPassive(on);
  if (!on) {
    cancelSelection();
    cancelInkStroke();
    clearInkCursors(surfaces);
  }
}

// ------------------------------------------------------------------ //
// Drag selection                                                       //
// ------------------------------------------------------------------ //
//
// Highlighting is two distinct beats, the way Adobe Reader and every other
// desktop annotator does it: while the mouse is held the characters under
// the drag are *selected* — washed in the selection color, following the
// cursor character by character (a drag can stop mid-word, the same as
// dragging over text in Adobe Reader) — and only on release does that
// selection become a highlight. Before this, nothing at all happened until
// the mouse came up, so there was no way to tell what was about to be
// highlighted (or that the drag had registered) until it already had been.
//
// The preview is drawn entirely on the frontend: it has to follow the cursor
// on every mousemove, which is far too often to go back across the bridge
// for. Python still owns the character boxes (api.py's get_page_chars, per
// ARCHITECTURE.md's "bounding-box data sent to the frontend") and still owns
// the actual annotation write — what crosses back on release is the
// *character index range* the preview had drawn, so the highlight that gets
// written is by construction the one the reader was looking at.

// Character boxes per page, keyed by page index. The promise itself is
// cached, so a second drag on the same page while the first fetch is still
// in flight joins it rather than issuing another bridge call. No
// invalidation: boxes are in PDF points, so zoom doesn't touch them, and
// highlighting adds an annotation without moving any text. Opening another
// document reloads this page, and with it the cache.
const charCache = new Map();
// The same replies once they have arrived, so a press can settle the tool's
// mode (freehand.js) synchronously when the reader has already pointed there.
const charData = new Map();

function getPageChars(pageIndex) {
  if (!charCache.has(pageIndex)) {
    const request = callApi("get_page_chars", pageIndex).then((page) => {
      if (charCache.get(pageIndex) === request) charData.set(pageIndex, page);
      return page;
    });
    charCache.set(pageIndex, request);
  }
  return charCache.get(pageIndex);
}

// After a scanned page has been read, its characters are new.
function forgetPageChars(pageIndex) {
  charCache.delete(pageIndex);
  charData.delete(pageIndex);
}

// Index of the character nearest a point given in PDF points — 0 distance
// for a point inside a character's box, and a point out in the margin picks
// the nearest character on the line it's level with. Used only to extend a
// live drag, never to decide what was pressed on: it always returns *some*
// character, however far away, which is the wrong answer for a press (see
// charIndexAt below).
function nearestCharIndex(chars, x, y) {
  let bestIdx = null;
  let bestDist = Infinity;
  for (let i = 0; i < chars.length; i++) {
    const c = chars[i];
    const dx = Math.max(c.x0 - x, 0, x - c.x1);
    const dy = Math.max(c.y0 - y, 0, y - c.y1);
    const dist = dx * dx + dy * dy;
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = i;
    }
  }
  return bestIdx;
}

// How far outside a character's own box a press still counts as landing on
// it, in PDF points (so it tracks the document's scale, not the zoom level).
// Characters on a line tile edge-to-edge with no horizontal gap between them
// (even the space character gets its own box), so this pad mostly matters
// for the margin before the first character of a line, past the last one,
// and the thin band between lines — 1.5pt is enough to forgive a press that
// lands just short of a line without reaching the next one.
const HIT_PAD = 1.5;

// Index of the character actually *under* a point, or null when the point is
// in whitespace outside any line. This is a containment test, not a
// nearest-character one, and that distinction is the whole point: pressing
// in the margin, between paragraphs or in the empty half of a page below the
// last line must do nothing at all. Resolving the press with
// nearestCharIndex() instead is what made a click anywhere on the page
// highlight whichever character happened to be closest to it, including
// text the reader never clicked on.
function charIndexAt(chars, x, y) {
  let bestIdx = null;
  let bestDist = Infinity;
  for (let i = 0; i < chars.length; i++) {
    const c = chars[i];
    const dx = Math.max(c.x0 - x, 0, x - c.x1);
    const dy = Math.max(c.y0 - y, 0, y - c.y1);
    if (dx > HIT_PAD || dy > HIT_PAD) continue;
    const dist = dx * dx + dy * dy;
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = i;
    }
  }
  return bestIdx;
}

// PDF points -> displayed CSS pixels for a given page image. Derived from the
// image's own measured width rather than state.zoom because the two disagree
// mid-zoom: applyZoomPreview() resizes the image ahead of the debounced
// re-render, so for a moment the page on screen is not the size its zoom
// level says it is.
function pageScale(el, page) {
  const rect = el.getBoundingClientRect();
  return page.width ? rect.width / page.width : 0;
}

// The character range covered by the drag: from the character the drag is
// anchored to, to the character under the cursor now, inclusive, in reading
// order.
//
// The two ends are resolved by different rules, deliberately. The anchor has
// to be a character the reader actually pressed on (charIndexAt), so that a
// click on empty page selects nothing; the far end snaps to the nearest
// character (nearestCharIndex), so that dragging out past the end of a line
// — or off the page altogether — keeps extending the selection the way
// dragging in a text editor does, instead of stopping dead at the last
// glyph.
//
// A drag that begins in whitespace and runs into text is still a selection:
// the anchor is left unresolved until the cursor first touches a character,
// and that character becomes it.
function selectionRange(sel, page, ev) {
  if (!page || !page.chars.length) return null;
  const rect = sel.el.getBoundingClientRect();
  const scale = pageScale(sel.el, page);
  if (!scale) return null;
  const x = (ev.clientX - rect.left) / scale;
  const y = (ev.clientY - rect.top) / scale;
  if (sel.anchor === null) {
    sel.anchor = charIndexAt(page.chars, sel.startOffset.x / scale, sel.startOffset.y / scale);
  }
  if (sel.anchor === null) {
    sel.anchor = charIndexAt(page.chars, x, y);
  }
  if (sel.anchor === null) return null;
  const end = nearestCharIndex(page.chars, x, y);
  if (end === null) return null;
  return { lo: Math.min(sel.anchor, end), hi: Math.max(sel.anchor, end) };
}

// One box per line of text, spanning the selected characters on that line —
// mirrors merge_line_rects() in features/annotations/highlighter.py, which
// composes the annotation quads the same way. They have to agree: the wash
// the reader sees while dragging and the highlight they get on release are
// meant to be the same shape, so neither may leave a gap that a
// box-per-character would.
function mergeLineBoxes(chars, range) {
  const boxes = [];
  for (let i = range.lo; i <= range.hi; i++) {
    const c = chars[i];
    const last = boxes[boxes.length - 1];
    if (last && last.line === c.line) {
      last.x0 = Math.min(last.x0, c.x0);
      last.y0 = Math.min(last.y0, c.y0);
      last.x1 = Math.max(last.x1, c.x1);
      last.y1 = Math.max(last.y1, c.y1);
    } else {
      boxes.push({ line: c.line, x0: c.x0, y0: c.y0, x1: c.x1, y1: c.y1 });
    }
  }
  return boxes;
}

function paintSelection(sel, applied) {
  const page = sel.page;
  if (!page || !sel.range) return;
  const scale = pageScale(sel.el, page);
  if (!scale) return;
  // Positioned against .page-area (the selection layer's offset parent)
  // rather than against the page image, so the same code serves book layout
  // and the strip without caring how either one nests its pages.
  const rect = sel.el.getBoundingClientRect();
  const areaRect = pageArea.getBoundingClientRect();
  const fragment = document.createDocumentFragment();
  // On release the wash takes the look of what is about to be written: a
  // highlight's fill, or an underline's or strikethrough's line (reading.css).
  selectionLayer.dataset.style = applied ? activeHighlightStyle : "";
  for (const box of mergeLineBoxes(page.chars, sel.range)) {
    const div = document.createElement("div");
    div.className = applied ? "selection-rect applied" : "selection-rect";
    div.style.left = `${rect.left - areaRect.left + box.x0 * scale}px`;
    div.style.top = `${rect.top - areaRect.top + box.y0 * scale}px`;
    div.style.width = `${(box.x1 - box.x0) * scale}px`;
    div.style.height = `${(box.y1 - box.y0) * scale}px`;
    fragment.appendChild(div);
  }
  selectionLayer.replaceChildren(fragment);
}

function clearSelection() {
  selectionLayer.replaceChildren();
}

function cancelSelection() {
  selection = null;
  clearSelection();
}

function beginSelection(el, pageIndex, ev) {
  const rect = el.getBoundingClientRect();
  const sel = {
    el,
    pageIndex,
    page: null,
    // Kept as an offset within the page image, not as a viewport point, so
    // scrolling mid-drag doesn't move where the selection started.
    startOffset: { x: ev.clientX - rect.left, y: ev.clientY - rect.top },
    // Resolved on the first move (or on release) rather than here, because
    // the character list may not have arrived yet, and because a drag
    // starting in whitespace only earns an anchor once it reaches text.
    anchor: null,
    range: null,
    // Whether the pointer has travelled since the press: a press and release
    // in one place on an existing highlight opens its editor (see
    // endSelection) instead of highlighting the character under it again.
    moved: false,
  };
  selection = sel;
  clearSelection();
  getPageChars(pageIndex).then((page) => {
    // The drag can be over (or cancelled) before the character list arrives;
    // only the drag that asked for it may adopt it.
    if (selection === sel) sel.page = page;
  });
}

// How far the pointer may drift between press and release and still count as
// a click, in CSS pixels — a hand on a mouse is never perfectly still.
const CLICK_SLOP_PX = 3;

function onSelectionMove(ev) {
  if (!selection) return;
  if (!selection.moved) {
    const rect = selection.el.getBoundingClientRect();
    const dx = ev.clientX - rect.left - selection.startOffset.x;
    const dy = ev.clientY - rect.top - selection.startOffset.y;
    selection.moved = Math.hypot(dx, dy) > CLICK_SLOP_PX;
  }
  if (!selection.page) return;
  const range = selectionRange(selection, selection.page, ev);
  if (!range) return;
  selection.range = range;
  paintSelection(selection, false);
}

async function endSelection(ev) {
  const sel = selection;
  selection = null;
  if (!sel) return;
  // A drag shorter than the first character fetch still has to highlight
  // something, so fall back to awaiting the list here — this is also the
  // path a plain click (no mousemove at all) takes, which highlights the one
  // character clicked, as it did before.
  const page = sel.page || (await getPageChars(sel.pageIndex));
  sel.page = page;
  // A click on an existing highlight is a question about that highlight
  // (change its color, remove it), not a request for a one-character
  // highlight on top of it — the same as a click on it with the tool off.
  const scale = pageScale(sel.el, page);
  if (!sel.moved && scale) {
    const at = highlightAtPoint(sel.pageIndex, sel.startOffset.x / scale, sel.startOffset.y / scale);
    if (at) {
      clearSelection();
      openHighlightEditor(sel.pageIndex, at);
      return;
    }
  }
  // A scanned page has no characters until its text has been read. Pressing
  // on it is what asks for that; the reader drags again once it is done
  // rather than having this drag replayed against boxes it never saw.
  if (page.ocr && page.ocr !== "done") {
    clearSelection();
    readPageForSelection(sel.pageIndex);
    return;
  }
  sel.range = selectionRange(sel, page, ev);
  if (!sel.range) {
    // Only a press on a character is a text press (freehand.js), so this is
    // a press that had nothing under it after all: no highlight.
    clearSelection();
    return;
  }
  // Repaint in the highlight color before the round trip, so the highlight
  // lands under the cursor the instant the mouse is released rather than
  // after the page has been re-rendered with the real annotation in it.
  paintSelection(sel, true);
  await applyHighlight(sel.pageIndex, sel.range);
  clearSelection();
}

// Writes the selected range as a real PDF annotation and brings the view
// back in step with it.
async function applyHighlight(pageIndex, range) {
  adoptState(await callApi("highlight_chars", pageIndex, range.lo, range.hi));
  updateChrome();
  forgetPageHighlights(pageIndex);
  forgetPageThumbnails(pageIndex);
  paintHighlightTargets();
  if (layoutMode === "strip") {
    renderHighlightCount();
    await updateStripPageImage(pageIndex);
  } else {
    renderHighlightCount();
    // Keep the strip in sync even though it isn't visible right now, by
    // patching just the one page that changed — flagging the whole strip
    // dirty (forcing a full rebuild the next time it's shown) was overkill
    // for a single-page change and, before the buildStrip() fix (reading-layout.js), was
    // what made switching to strip mode right after highlighting look like
    // it had jumped back to page 1.
    if (!stripDirty) {
      await updateStripPageImage(pageIndex);
    }
    await refreshBook();
  }
}

// A press with the tool on is a text highlight or a freehand stroke, by what
// is under it (freehand.js), which also keeps the cursor saying which.
function bindSelection(el, getPageIndex) {
  el.addEventListener("mousedown", (ev) => {
    if (!highlightMode || ev.button !== 0) return;
    ev.preventDefault(); // no drag-the-image ghost while selecting
    pressHighlightTool(el, getPageIndex(), ev);
  });
  el.addEventListener("mousemove", (ev) => {
    if (highlightMode) noteInkHover(el, getPageIndex(), ev);
  });
  el.addEventListener("mouseleave", () => forgetInkHover(el));
}

// A stroke's commit: written, then the page brought in step with it, which
// is when freehand.js lets its preview go. False when the bridge drew
// nothing (it refuses a malformed stroke, or there is no document open);
// a failed call rejects. freehand.js reports either through commitFailed.
async function commitInkStroke(pageIndex, drawn, straight) {
  adoptState(await callApi("add_ink_stroke", pageIndex, drawn, straight));
  if (state.id == null) return false;
  try {
    await afterAnnotationChange(state.affected_page);
  } catch (err) {
    // Drawn all the same: only the page image is behind, so not "Couldn't draw that".
    console.error("Couldn't show the new stroke:", err);
  }
  return true;
}

const INK_NOT_DRAWN = "Couldn't draw that";

// The overlay is positioned from the page image's measured position, so it
// has to be redrawn when the page moves under a drag that isn't moving.
function repaintSelectionOnScroll() {
  if (selection && selection.range) paintSelection(selection, false);
  repaintInkStroke();
}

// The page previews in the progress bar and the Pages tab show highlights, so
// a page's are redrawn after its highlights change.
function forgetPageThumbnails(pageIndex) {
  if (pageIndex == null) return;
  forgetProgressThumb(pageIndex);
  forgetPageThumb(pageIndex);
}

// A highlight's undo redraws its page; a set of page changes rearranges the
// whole document, so it runs under "Applying page changes…" and the reader
// reloads on the result (page-change-busy.js).
// Steps run one after another, each deciding from the state the one before
// left (Ctrl+Z pressed twice must not send two "highlight" undos when the
// second reaches a page change). The backend says what each step really took
// back (`pages_changed`), and that decides how the page follows.
let historyQueue = Promise.resolve();

function stepHistory(forward) {
  historyQueue = historyQueue.then(async () => {
    const can = forward ? state.can_redo : state.can_undo;
    if (!can || isPageChangeBusy() || isOrganiseOpen()) return;
    const method = forward ? "redo_highlight" : "undo_highlight";
    const kind = forward ? state.redo_kind : state.undo_kind;
    if (kind === "page_changes") {
      // The reader reloads onto the changed pages; if nothing changed after
      // all, it carries on with what the backend answered.
      adoptState(await runPageChange(() => callApi(method)));
      return;
    }
    const next = await callApi(method);
    if (next.pages_changed) {
      // It reached a page change after all: the reader reloads onto it.
      await runPageChange(async () => next);
      return;
    }
    adoptState(next);
    await afterAnnotationChange(state.affected_page);
  }).catch((err) => console.error("Undo/redo failed:", err));
  return historyQueue;
}

function undo() {
  return stepHistory(false);
}

function redo() {
  return stepHistory(true);
}

// Undo/redo only ever affect the one page whose highlight was added/removed
// (the backend tells us which). Forcing a full strip rebuild for that — as
// this used to do via markStripDirty() — meant every Undo/Redo click while
// in (or heading into) strip mode paid the cost described in buildStrip()'s
// comment (reading-layout.js), which looked like the view snapping back to page 1.
async function afterAnnotationChange(affectedPage) {
  updateChrome();
  forgetPageHighlights(affectedPage);
  forgetPageThumbnails(affectedPage);
  paintHighlightTargets();
  if (layoutMode === "strip") {
    renderHighlightCount();
    if (stripDirty) {
      await refreshStrip();
    } else if (affectedPage != null) {
      await updateStripPageImage(affectedPage);
    }
  } else {
    renderHighlightCount();
    await refreshBook();
    if (!stripDirty && affectedPage != null) {
      await updateStripPageImage(affectedPage);
    }
  }
}
