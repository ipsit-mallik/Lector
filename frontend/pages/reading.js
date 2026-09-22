// Reading view chrome: topbar, toolbar rail, page area (book + strip
// layouts), footer. Mirrors the behavior previously implemented in
// src/lector/features/reading/view.py (PySide6 ReadingView) — page
// rendering/annotation logic itself lives in Python (src/lector/api.py,
// which wraps features/reading/document.py + features/annotations
// unchanged); this file only drives the DOM.

const backBtn = document.getElementById("backBtn");
const docTitle = document.getElementById("docTitle");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const pageInput = document.getElementById("pageInput");
const pageSuffix = document.getElementById("pageSuffix");
const zoomLabel = document.getElementById("zoomLabel");

const highlightBtn = document.getElementById("highlightBtn");
const undoBtn = document.getElementById("undoBtn");
const redoBtn = document.getElementById("redoBtn");
const zoomInBtn = document.getElementById("zoomInBtn");
const zoomOutBtn = document.getElementById("zoomOutBtn");
const zoomPopover = document.getElementById("zoomPopover");
const zoomSlider = document.getElementById("zoomSlider");
const layoutBtn = document.getElementById("layoutBtn");
const themeBtn = document.getElementById("themeBtn");
const referenceRailBtn = document.getElementById("referenceRailBtn");
const referenceFooterBtn = document.getElementById("referenceFooterBtn");

const pageArea = document.getElementById("pageArea");
const bookScroll = document.getElementById("bookScroll");
const pageCard = document.getElementById("pageCard");
const pageSurface = document.getElementById("pageSurface");
const stripScroll = document.getElementById("stripScroll");
const stripContainer = document.getElementById("stripContainer");
const selectionLayer = document.getElementById("selectionLayer");
const voiceFocusLayer = document.getElementById("voiceFocusLayer");
const highlightCountLbl = document.getElementById("highlightCount");

let state = { is_open: false };
let layoutMode = "book"; // or "strip"
let highlightMode = false;
let stripDirty = true;
let stripBuildPromise = null; // guards against overlapping refreshStrip() builds (e.g. rapid nav)
let selection = null; // the drag in progress, if any — see beginSelection()
let renderedZoom = null; // the zoom level the currently-displayed page images were actually rendered at

const THEME_ORDER = ["light", "dark", "sepia"];

function b64ToDataUrl(b64) {
  return `data:image/png;base64,${b64}`;
}

// Point an <img> at freshly rendered page bytes and resolve only once they
// are actually decoded and ready to paint. Callers need that guarantee to
// hand over cleanly from the selection overlay to the re-rendered page: if
// the overlay is torn down the instant .src is assigned, the still-displayed
// old image shows for a frame or two with nothing on it, which reads as the
// highlight flickering off before it appears.
async function showImage(img, b64) {
  img.src = b64ToDataUrl(b64);
  try {
    await img.decode();
  } catch {
    // Rejects if this src was superseded by a newer one (rapid nav/zoom) —
    // that render's own showImage() call is the one that matters then.
  }
}

function updateChrome() {
  docTitle.textContent = state.title || "";
  pageInput.max = state.page_count;
  pageInput.value = state.page_index + 1;
  pageSuffix.textContent = `of ${state.page_count}`;
  zoomLabel.textContent = `${state.zoom_pct}%`;
  zoomSlider.min = state.zoom_min_pct;
  zoomSlider.max = state.zoom_max_pct;
  zoomSlider.value = state.zoom_pct;
  prevBtn.disabled = state.page_index <= 0;
  nextBtn.disabled = state.page_index >= state.page_count - 1;
  undoBtn.disabled = !state.can_undo;
  redoBtn.disabled = !state.can_redo;
}

async function refresh() {
  updateChrome();

  if (layoutMode === "book") {
    highlightCountLbl.textContent = `${state.highlight_count_page} ${state.highlight_count_page === 1 ? "highlight" : "highlights"} on this page`;
    await refreshBook();
  } else {
    highlightCountLbl.textContent = `${state.highlight_count_total} ${state.highlight_count_total === 1 ? "highlight" : "highlights"} in this document`;
    await refreshStrip();
  }
  scheduleViewportPush();
}

async function refreshBook() {
  bookScroll.hidden = false;
  stripScroll.hidden = true;
  const { image, width, height } = await callApi("get_page_image", state.page_index);
  if (image) {
    pageSurface.width = width;
    pageSurface.height = height;
    pageSurface.style.width = "";
    pageSurface.style.height = "";
    await showImage(pageSurface, image);
  }
  renderedZoom = state.zoom;
}

let stripObserver = null;

// Rendering and base64-encoding every page up front (a real pixmap render
// per page) made switching to strip mode take seconds on a large document,
// and the whole strip sat blank until every single page had finished. Sizes
// are cheap (no rasterization), so placeholders for every page are laid out
// immediately from those, and each page's actual image is only rendered
// lazily, once it scrolls near the viewport.
async function buildStrip() {
  const sizes = await callApi("get_page_sizes");
  const fragment = document.createDocumentFragment();
  const imgs = [];
  for (let i = 0; i < sizes.length; i++) {
    const { width, height } = sizes[i];
    const img = document.createElement("img");
    img.className = "strip-page page-surface";
    img.dataset.pageIndex = String(i);
    img.width = width;
    img.height = height;
    img.draggable = false;
    bindSelection(img, () => i);
    imgs.push(img);
    fragment.appendChild(img);
    if (i < sizes.length - 1) {
      const gap = document.createElement("div");
      gap.className = "strip-gap";
      fragment.appendChild(gap);
    }
  }
  stripContainer.replaceChildren(fragment);
  stripDirty = false;
  renderedZoom = state.zoom;
  observeStripPages(imgs);
}

function observeStripPages(imgs) {
  if (stripObserver) stripObserver.disconnect();
  stripObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        stripObserver.unobserve(entry.target);
        renderStripPage(entry.target);
      });
    },
    { root: stripScroll, rootMargin: "800px 0px" }
  );
  imgs.forEach((img) => stripObserver.observe(img));
}

async function renderStripPage(img) {
  const index = Number(img.dataset.pageIndex);
  const { image } = await callApi("get_page_image", index);
  if (image) await showImage(img, image);
}

async function refreshStrip() {
  bookScroll.hidden = true;
  stripScroll.hidden = false;

  // If a build is already in flight (e.g. the user navigated again before the
  // first strip render finished), wait for that one rather than starting a
  // second concurrent build — two overlapping builds would both clear and
  // append into stripContainer, interleaving pages and dropping others.
  if (stripBuildPromise) {
    await stripBuildPromise;
  } else if (stripDirty) {
    stripRebuilding = true;
    stripBuildPromise = buildStrip();
    try {
      await stripBuildPromise;
    } finally {
      stripBuildPromise = null;
      stripRebuilding = false;
    }
  }

  const target = stripContainer.querySelector(`[data-page-index="${state.page_index}"]`);
  if (target) {
    stripScroll.scrollTop = target.offsetTop - stripContainer.offsetTop;
  }
}

// Re-renders a single already-mounted strip page in place (e.g. right after a
// highlight is added to it), instead of rebuilding the whole 357-page strip
// via markStripDirty()/buildStrip() — a full rebuild would blank the strip
// and reload every page from scratch just to reflect one page's change.
async function updateStripPageImage(index) {
  const img = stripContainer.querySelector(`[data-page-index="${index}"]`);
  if (!img) return;
  if (stripObserver) stripObserver.unobserve(img);
  const { image, width, height } = await callApi("get_page_image", index);
  if (!image) return;
  img.width = width;
  img.height = height;
  await showImage(img, image);
}

let stripScrollTicking = false;
let stripRebuilding = false;

// A strip page's top edge in the strip's scroll coordinates, so it can be
// compared against (and assigned to) stripScroll.scrollTop directly.
function stripPageTop(pageEl) {
  return pageEl.offsetTop - stripContainer.offsetTop;
}

// The page the strip currently considers "current": the last one whose top
// has scrolled past the top of the viewport. Shared by the page-indicator
// tracking below and by the zoom scroll anchor, so the two can never disagree
// about which page the view is sitting on.
function currentStripPage(scrollTop = stripScroll.scrollTop) {
  const pages = stripContainer.querySelectorAll(".strip-page");
  if (!pages.length) return null;
  let current = pages[0];
  for (const p of pages) {
    if (stripPageTop(p) <= scrollTop + 4) {
      current = p;
    } else {
      break;
    }
  }
  return { el: current, top: stripPageTop(current) };
}

// Tracks which page has scrolled past the top of the viewport and keeps the
// page indicator (and the backend's notion of the current page, so
// Previous/Next/Save stay correct) in sync as the user scrolls the strip.
function onStripScroll() {
  // While buildStrip() is clearing/repopulating stripContainer, the browser
  // clamps scrollTop to 0 (scrollHeight briefly drops to ~0), firing scroll
  // events that would otherwise be misread as "the user scrolled to page 1".
  if (layoutMode !== "strip" || stripScrollTicking || stripRebuilding) return;
  stripScrollTicking = true;
  requestAnimationFrame(() => {
    stripScrollTicking = false;
    const current = currentStripPage();
    if (!current) return;
    const index = Number(current.el.dataset.pageIndex);
    if (index === state.page_index) return;
    state.page_index = index;
    updateChrome();
    callApi("goto_page", index);
  });
}

stripScroll.addEventListener("scroll", onStripScroll);

function markStripDirty() {
  stripDirty = true;
}

function setHighlightMode(on) {
  highlightMode = on;
  highlightBtn.classList.toggle("active", on);
  document.querySelectorAll(".page-surface").forEach((el) => {
    el.classList.toggle("selecting", on);
  });
  if (!on) cancelSelection();
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

function getPageChars(pageIndex) {
  if (!charCache.has(pageIndex)) {
    charCache.set(pageIndex, callApi("get_page_chars", pageIndex));
  }
  return charCache.get(pageIndex);
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

// Draws the amber "listening here" outline over `rects` (screen-pixel boxes,
// {left, top, width, height}, relative to the viewport). One tag is enough
// even when strip layout hands back two partially-visible regions, since the
// point is "voice can hear all of this," not "here is region N."
function renderVoiceFocus(rects) {
  const areaRect = pageArea.getBoundingClientRect();
  const fragment = document.createDocumentFragment();
  rects.forEach((r, i) => {
    const div = document.createElement("div");
    div.className = "voice-focus-rect";
    div.style.left = `${r.left - areaRect.left}px`;
    div.style.top = `${r.top - areaRect.top}px`;
    div.style.width = `${r.width}px`;
    div.style.height = `${r.height}px`;
    if (i === 0) {
      const tag = document.createElement("span");
      tag.className = "voice-focus-tag";
      tag.textContent = "Listening here";
      div.appendChild(tag);
    }
    fragment.appendChild(div);
  });
  voiceFocusLayer.replaceChildren(fragment);
}

function clearVoiceFocus() {
  voiceFocusLayer.replaceChildren();
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
  };
  selection = sel;
  clearSelection();
  getPageChars(pageIndex).then((page) => {
    // The drag can be over (or cancelled) before the character list arrives;
    // only the drag that asked for it may adopt it.
    if (selection === sel) sel.page = page;
  });
}

function onSelectionMove(ev) {
  if (!selection || !selection.page) return;
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
  sel.range = selectionRange(sel, page, ev);
  if (!sel.range) {
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
  state = await callApi("highlight_chars", pageIndex, range.lo, range.hi);
  updateChrome();
  if (layoutMode === "strip") {
    highlightCountLbl.textContent = `${state.highlight_count_total} ${state.highlight_count_total === 1 ? "highlight" : "highlights"} in this document`;
    await updateStripPageImage(pageIndex);
  } else {
    highlightCountLbl.textContent = `${state.highlight_count_page} ${state.highlight_count_page === 1 ? "highlight" : "highlights"} on this page`;
    // Keep the strip in sync even though it isn't visible right now, by
    // patching just the one page that changed — flagging the whole strip
    // dirty (forcing a full rebuild the next time it's shown) was overkill
    // for a single-page change and, before the buildStrip() fix above, was
    // what made switching to strip mode right after highlighting look like
    // it had jumped back to page 1.
    if (!stripDirty) {
      await updateStripPageImage(pageIndex);
    }
    await refreshBook();
  }
}

function bindSelection(el, getPageIndex) {
  el.addEventListener("mousedown", (ev) => {
    if (!highlightMode || ev.button !== 0) return;
    ev.preventDefault(); // no drag-the-image ghost while selecting
    beginSelection(el, getPageIndex(), ev);
  });
}

// Tracked on the document, not on the page image: a drag that runs off the
// edge of the page (or off the window) should still select up to where it
// left and still commit on release, the same way it does in a text editor.
document.addEventListener("mousemove", onSelectionMove);
document.addEventListener("mouseup", endSelection);

// The overlay is positioned from the page image's measured position, so it
// has to be redrawn when the page moves under a drag that isn't moving.
function repaintSelectionOnScroll() {
  if (selection && selection.range) paintSelection(selection, false);
}

bookScroll.addEventListener("scroll", repaintSelectionOnScroll);
stripScroll.addEventListener("scroll", repaintSelectionOnScroll);
bookScroll.addEventListener("scroll", scheduleViewportPush);
stripScroll.addEventListener("scroll", scheduleViewportPush);

// ------------------------------------------------------------------ //
// Navigation / zoom                                                    //
// ------------------------------------------------------------------ //

async function goNext() {
  state = await callApi("next_page");
  await refresh();
}

async function goPrev() {
  state = await callApi("prev_page");
  await refresh();
}

async function goToPage(n) {
  state = await callApi("goto_page", n - 1);
  await refresh();
}

// Re-rendering every page at a new resolution (a full strip rebuild, for
// strip layout) is expensive, so repeatedly clicking zoom in/out or dragging
// the slider doesn't re-render on every single step. Instead each step:
//  1) updates the cheap backend zoom value and the chrome (% label/slider)
//     immediately, so the UI never feels unresponsive, and
//  2) resizes the already-rendered images as an instant visual preview,
//     while the actual re-render is debounced until the zoom level settles.
//
// Resizing the pages is not enough on its own: the browser does not adjust a
// scroll container's scrollTop when content *above* the viewport changes
// size, so growing every page (zoom in) slides the content the user was
// looking at downward past the fixed scroll offset — the view lands on an
// earlier page — and shrinking them (zoom out) lands on a later one, both
// compounding over a burst of zoom steps. The scroll position therefore has
// to be re-anchored in the same synchronous step that resizes the pages,
// before the browser ever paints the resized-but-unscrolled layout.
//
// This is also why .page-surface must not carry a width/height CSS
// transition: an animated resize means the sizes read back here are still
// mid-flight rather than final, so the anchor would be restored against the
// wrong geometry and the view would drift over the animation's duration.
function applyZoomPreview() {
  if (renderedZoom == null || renderedZoom === state.zoom) return;
  const scale = state.zoom / renderedZoom;
  const targets = layoutMode === "book"
    ? [pageSurface]
    : stripContainer.querySelectorAll(".strip-page");
  const stripAnchor = layoutMode === "strip" ? captureStripAnchor() : null;
  const bookFraction = layoutMode === "book" ? getPageScrollFraction() : null;
  // A CSS transform scales pixels without resizing the element's layout box,
  // which is fine for the lone page in book layout but makes strip pages
  // visually overlap their neighbors (their box in the vertical flow never
  // grew to match). Scaling width/height directly instead resizes the box,
  // so surrounding pages/gaps reflow to make room for the enlarged preview.
  targets.forEach((el) => {
    // Read the width/height *attributes* (the size at renderedZoom), not the
    // .width/.height IDL properties — those reflect the current rendered
    // size, which a previous preview step may have already scaled, and
    // scaling that would compound instead of previewing the true target size.
    const baseWidth = Number(el.getAttribute("width"));
    const baseHeight = Number(el.getAttribute("height"));
    el.style.width = `${baseWidth * scale}px`;
    el.style.height = `${baseHeight * scale}px`;
  });

  if (stripAnchor) restoreStripAnchor(stripAnchor);
  if (bookFraction != null) applyPageScrollFraction(bookFraction);
}

let zoomRefreshTimer = null;
const ZOOM_REFRESH_DEBOUNCE_MS = 300;

// Where the strip is parked, captured right before a zoom-triggered rebuild
// so the rebuild can land back on the same spot instead of snapping to a page
// top — expressed as the current page plus how far into that page the top of
// the viewport sits, as a fraction of the page's own height.
//
// Anchoring instead to the scrollbar's raw position ratio (scrollTop over the
// scrollable range), as this used to, is subtly but consistently wrong: the
// scrollable range is `scrollHeight - clientHeight`, and neither the viewport
// height nor the strip's fixed-size furniture (the 20px .strip-gap between
// every pair of pages, the scroller's 24px padding) scales with zoom. Holding
// that ratio constant therefore lands at scrollTop * (k*H - V) / (H - V)
// rather than the correct k * scrollTop, an error of roughly
// `ratio * viewportHeight * (k - 1)` that grows the further into the document
// you are: positive when zooming in (the view creeps downward, so the page
// appears to shift up) and negative when zooming out (the view creeps above
// the current page's top, which reads back as the *previous* page).
//
// A page-relative anchor has no such drift, but the earlier attempt at one
// could flip a whole page near a boundary. Two rules keep that from coming
// back: the anchor records the page element's own index at capture time and
// restores against that exact element (rather than re-deriving a "current
// page" from state.page_index, which the scroll tracker may have moved), and
// the fraction is clamped to [0, 1] so the restored position can never fall
// above the anchor page's top or past its bottom edge.
function captureStripAnchor() {
  const current = currentStripPage();
  if (!current) return null;
  const height = current.el.getBoundingClientRect().height;
  if (!height) return null;
  const fraction = (stripScroll.scrollTop - current.top) / height;
  return {
    index: Number(current.el.dataset.pageIndex),
    fraction: Math.min(1, Math.max(0, fraction)),
  };
}

function restoreStripAnchor(anchor) {
  if (!anchor) return;
  const el = stripContainer.querySelector(`.strip-page[data-page-index="${anchor.index}"]`);
  if (!el) return;
  stripScroll.scrollTop = stripPageTop(el) + anchor.fraction * el.getBoundingClientRect().height;
}

function scheduleZoomRefresh() {
  if (zoomRefreshTimer) clearTimeout(zoomRefreshTimer);
  zoomRefreshTimer = setTimeout(async () => {
    zoomRefreshTimer = null;
    // Both layouts re-render the page at a new pixel size, so both need the
    // view re-anchored afterwards; only the way the anchor is measured
    // differs (book layout has a single page, so the generic
    // page-scroll-fraction helpers cover it).
    const stripAnchor = layoutMode === "strip" ? captureStripAnchor() : null;
    const bookFraction = layoutMode === "book" ? getPageScrollFraction() : null;
    markStripDirty();
    await refresh();
    if (stripAnchor) restoreStripAnchor(stripAnchor);
    if (bookFraction != null) applyPageScrollFraction(bookFraction);
  }, ZOOM_REFRESH_DEBOUNCE_MS);
}

// Zoom never changes which page is "current" — scrolling does, and
// onStripScroll already tracks that locally the instant it happens. But it
// reports the new page to the backend with a fire-and-forget callApi(), so a
// zoom click made right after scrolling can race it: the backend's reply to
// zoom_in/zoom_out/set_zoom may still carry the page index from *before* the
// scroll, and blindly trusting it here snapped the strip back to that stale
// page once the debounced rebuild ran. The locally-tracked page_index is
// always at least as fresh, so it's preserved across the zoom call instead.
async function zoomIn() {
  const pageIndex = state.page_index;
  state = await callApi("zoom_in");
  state.page_index = pageIndex;
  updateChrome();
  applyZoomPreview();
  scheduleZoomRefresh();
}

async function zoomOut() {
  const pageIndex = state.page_index;
  state = await callApi("zoom_out");
  state.page_index = pageIndex;
  updateChrome();
  applyZoomPreview();
  scheduleZoomRefresh();
}

async function setZoomPct(pct) {
  const pageIndex = state.page_index;
  state = await callApi("set_zoom", pct / 100);
  state.page_index = pageIndex;
  updateChrome();
  applyZoomPreview();
  scheduleZoomRefresh();
}

function toggleZoomPopover(show) {
  zoomPopover.hidden = show === undefined ? !zoomPopover.hidden : !show;
}

async function undo() {
  state = await callApi("undo_highlight");
  await afterAnnotationChange(state.affected_page);
}

async function redo() {
  state = await callApi("redo_highlight");
  await afterAnnotationChange(state.affected_page);
}

// Undo/redo only ever affect the one page whose highlight was added/removed
// (the backend tells us which). Forcing a full strip rebuild for that — as
// this used to do via markStripDirty() — meant every Undo/Redo click while
// in (or heading into) strip mode paid the cost described in buildStrip()'s
// comment above, which looked like the view snapping back to page 1.
async function afterAnnotationChange(affectedPage) {
  updateChrome();
  if (layoutMode === "strip") {
    highlightCountLbl.textContent = `${state.highlight_count_total} ${state.highlight_count_total === 1 ? "highlight" : "highlights"} in this document`;
    if (stripDirty) {
      await refreshStrip();
    } else if (affectedPage != null) {
      await updateStripPageImage(affectedPage);
    }
  } else {
    highlightCountLbl.textContent = `${state.highlight_count_page} ${state.highlight_count_page === 1 ? "highlight" : "highlights"} on this page`;
    await refreshBook();
    if (!stripDirty && affectedPage != null) {
      await updateStripPageImage(affectedPage);
    }
  }
}

// The current layout's scroll container and the element representing the
// current page's rendered image — book and strip use different DOM shapes
// (one bounded page-card vs. many stacked <img>s), but both always have
// exactly one element that *is* the current page's image at its true
// rendered size, which is what getPageScrollFraction/applyPageScrollFraction
// key off of.
function getCurrentPageEl() {
  return layoutMode === "book"
    ? pageSurface
    : stripContainer.querySelector(`[data-page-index="${state.page_index}"]`);
}

// How far down the current page the user has scrolled, as a fraction of the
// page's own rendered height (0 = its top is at the viewport top, 1 = its
// bottom is). Measured with getBoundingClientRect() so it's meaningful
// identically in both layouts, regardless of each one's different padding/
// container nesting.
function getPageScrollFraction() {
  const scrollEl = layoutMode === "book" ? bookScroll : stripScroll;
  const pageEl = getCurrentPageEl();
  if (!pageEl) return 0;
  const pageRect = pageEl.getBoundingClientRect();
  if (!pageRect.height) return 0;
  const scrollRect = scrollEl.getBoundingClientRect();
  return (scrollRect.top - pageRect.top) / pageRect.height;
}

function applyPageScrollFraction(fraction) {
  const scrollEl = layoutMode === "book" ? bookScroll : stripScroll;
  const pageEl = getCurrentPageEl();
  if (!pageEl) return;
  const pageRect = pageEl.getBoundingClientRect();
  const scrollRect = scrollEl.getBoundingClientRect();
  const currentOffset = scrollRect.top - pageRect.top;
  const desiredOffset = fraction * pageRect.height;
  scrollEl.scrollTop += desiredOffset - currentOffset;
}

// Point the toolbar's layout toggle at whichever layout is active. Split out
// of toggleLayout() because a document reopened via the "Continue where I
// left off" preference can start in strip layout, so init() has to be able to
// dress the button without going through a toggle.
function applyLayoutChrome() {
  const isStrip = layoutMode === "strip";
  layoutBtn.dataset.icon = isStrip ? "strip_layout" : "book_layout";
  mountIcon(layoutBtn, layoutBtn.dataset.icon);
  layoutBtn.title = isStrip
    ? "Continuous strip — scroll through the whole document"
    : "Book layout — one page at a time";
}

async function toggleLayout() {
  const fraction = getPageScrollFraction();
  layoutMode = layoutMode === "book" ? "strip" : "book";
  applyLayoutChrome();
  // Fire-and-forget, and deliberately not assigned to `state`: the reply
  // carries the backend's page index, which during a strip scroll can still
  // be the pre-scroll one (see the note on the zoom handlers), and adopting
  // it here would drag the view back to a stale page.
  callApi("set_layout_mode", layoutMode);
  await refresh();
  applyPageScrollFraction(fraction);
}

async function cycleTheme() {
  const current = document.documentElement.dataset.theme;
  const next = THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  document.documentElement.dataset.theme = next;
  await callApi("set_theme", next);
}

// ------------------------------------------------------------------ //
// Save flow                                                            //
// ------------------------------------------------------------------ //

async function promptSaveIfDirty(allowDiscard) {
  if (!state.is_dirty) return true;

  const behavior = await callApi("get_save_behavior");
  let mode;
  let remember = false;
  if (behavior === "ask") {
    const result = await SaveDialog.open({ allowDiscard });
    if (result.action === "discard") return true;
    if (result.action !== "save") return false;
    mode = result.mode;
    remember = result.remember;
    if (remember) {
      await callApi("set_save_behavior", mode);
    }
  } else {
    mode = behavior;
  }

  const outcome = await callApi("perform_save", mode);
  if (outcome.ok) {
    state = outcome.state;
    return true;
  }
  if (!outcome.cancelled) {
    alert(`Could not save the PDF:\n${outcome.error}`);
  }
  return false;
}

// ------------------------------------------------------------------ //
// Wiring                                                               //
// ------------------------------------------------------------------ //

backBtn.addEventListener("click", async () => {
  if (await promptSaveIfDirty(true)) {
    window.location.href = "../index.html";
  }
});

prevBtn.addEventListener("click", goPrev);
nextBtn.addEventListener("click", goNext);
pageInput.addEventListener("change", () => goToPage(Number(pageInput.value)));
zoomInBtn.addEventListener("click", zoomIn);
zoomOutBtn.addEventListener("click", zoomOut);
zoomLabel.addEventListener("click", (ev) => {
  ev.stopPropagation();
  toggleZoomPopover();
});
zoomSlider.addEventListener("input", () => setZoomPct(Number(zoomSlider.value)));
document.addEventListener("click", (ev) => {
  if (!zoomPopover.hidden && !zoomPopover.contains(ev.target) && ev.target !== zoomLabel) {
    toggleZoomPopover(false);
  }
});
layoutBtn.addEventListener("click", toggleLayout);
themeBtn.addEventListener("click", cycleTheme);
undoBtn.addEventListener("click", undo);
redoBtn.addEventListener("click", redo);
highlightBtn.addEventListener("click", () => setHighlightMode(!highlightMode));
// Two ways to the same panel, deliberately: the rail is where a reader
// hunting for help looks, and the footer button sits next to the mic pill,
// which is where the question "what can I say?" actually occurs to them.
referenceRailBtn.addEventListener("click", openCommandReference);
referenceFooterBtn.addEventListener("click", openCommandReference);

document.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT") return;
  switch (ev.key) {
    case "ArrowRight":
    case "PageDown":
      goNext();
      break;
    case "ArrowLeft":
    case "PageUp":
      goPrev();
      break;
    case "ArrowDown":
      if (layoutMode === "strip") stripScroll.scrollTop += 60;
      else goNext();
      break;
    case "ArrowUp":
      if (layoutMode === "strip") stripScroll.scrollTop -= 60;
      else goPrev();
      break;
    case "=":
      zoomIn();
      break;
    case "-":
      zoomOut();
      break;
    case "h":
    case "H":
      setHighlightMode(!highlightMode);
      break;
    case "?":
      // The conventional key for "what are my options", and the panel's own
      // promise of a keyboard equivalent for everything applies to opening
      // the panel too.
      openCommandReference();
      break;
    case "Escape":
      // Abandons a selection mid-drag: the wash disappears and the release
      // that follows writes nothing, so a drag started by mistake costs
      // nothing to back out of.
      cancelSelection();
      break;
    case "z":
      if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
      break;
    case "y":
      if (ev.ctrlKey) { ev.preventDefault(); redo(); }
      break;
    case "s":
      if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); promptSaveIfDirty(false); }
      break;
  }
});

bindSelection(pageSurface, () => state.page_index);

// --- Voice navigation ---------------------------------------------------- //
// Python recognizes the phrase and parses it into an intent
// (src/lector/features/voice/command_grammar.py); this end decides what that
// intent means for this view. Both halves of that split matter: the grammar
// has no idea there are two layouts, and this file has no idea which
// phrasings map to NEXT_PAGE.

// How far "scroll down" moves: most of a screen, but not all of it. Leaving
// an overlap means the line you were reading when you spoke is still on
// screen afterwards, so you never have to hunt for your place — the same
// reason Page Down in a text editor doesn't advance by a full viewport.
const VOICE_SCROLL_FRACTION = 0.8;

// The page regions actually on screen right now, in PDF points, for the
// backend to scope voice matching to (docs/PRD.md: matching only ever looks
// at "the currently visible viewport"). Reuses getPageChars()'s cached
// {width, height, chars} for each page's PDF-point size and pageScale() for
// the same pixels-per-point conversion the character-selection code already
// relies on, rather than introducing a second notion of page geometry.
// Resolves to the point-based regions (what start_listening() sends to
// Python) and, as a side effect, paints the same regions as the on-page
// "listening here" outline (docs/DESIGN_SYSTEM.md) — the two are measured in
// one pass so the outline can never drift from what is actually in scope for
// matching, which repainting them separately after a scroll could risk.
// --- Keeping the recognizer's vocabulary current -------------------------- //
// Push-to-talk can gather the visible words at the moment the key goes down,
// because there is a moment. A wake has none: the recognizer is built before
// the phrase is spoken, and a Vosk grammar can only ever return words it was
// built with, so words that reach the screen afterwards are words the reader
// cannot highlight by voice. This pushes them across as the reader moves
// instead.
//
// Only while the wake phrase is actually enabled: with push-to-talk alone it
// is pure overhead, since key-down already does the same work with better
// timing.
const VIEWPORT_PUSH_DEBOUNCE_MS = 400;
let viewportPushTimer = null;
let wakeListening = false;

function scheduleViewportPush() {
  if (!wakeListening) return;
  clearTimeout(viewportPushTimer);
  viewportPushTimer = setTimeout(async () => {
    if (!state.is_open) return;
    // Deliberately not computeVoiceViewport(): that paints the "listening
    // here" outline as a side effect, and nothing is being listened *to*
    // here — an outline drawn while the reader merely scrolls would claim a
    // capture that is not happening.
    const { regions } =
      layoutMode === "book" ? await computeBookViewport() : await computeStripViewport();
    callApi("update_voice_viewport", regions).catch(() => {
      // A vocabulary that could not be refreshed leaves the previous one in
      // place, which is worse aim rather than a broken feature.
    });
  }, VIEWPORT_PUSH_DEBOUNCE_MS);
}

async function computeVoiceViewport() {
  if (!state.is_open) {
    clearVoiceFocus();
    return [];
  }
  const { regions, rects } =
    layoutMode === "book" ? await computeBookViewport() : await computeStripViewport();
  renderVoiceFocus(rects);
  return regions;
}

async function computeBookViewport() {
  const empty = { regions: [], rects: [] };
  const pageIndex = state.page_index;
  const page = await getPageChars(pageIndex);
  if (!page || !page.width) return empty;
  const scale = pageScale(pageSurface, page);
  if (!scale) return empty;
  const pageRect = pageSurface.getBoundingClientRect();
  const scrollRect = bookScroll.getBoundingClientRect();
  const topPx = Math.max(pageRect.top, scrollRect.top);
  const bottomPx = Math.min(pageRect.bottom, scrollRect.bottom);
  if (bottomPx <= topPx) return empty;
  return {
    regions: [{
      page_index: pageIndex,
      y0: (topPx - pageRect.top) / scale,
      y1: (bottomPx - pageRect.top) / scale,
    }],
    rects: [{ left: pageRect.left, top: topPx, width: pageRect.width, height: bottomPx - topPx }],
  };
}

async function computeStripViewport() {
  const scrollRect = stripScroll.getBoundingClientRect();
  const regions = [];
  const rects = [];
  for (const el of stripContainer.querySelectorAll(".strip-page")) {
    const rect = el.getBoundingClientRect();
    if (rect.bottom <= scrollRect.top || rect.top >= scrollRect.bottom) continue;
    const pageIndex = Number(el.dataset.pageIndex);
    const page = await getPageChars(pageIndex);
    if (!page || !page.width) continue;
    const scale = pageScale(el, page);
    if (!scale) continue;
    const topPx = Math.max(rect.top, scrollRect.top);
    const bottomPx = Math.min(rect.bottom, scrollRect.bottom);
    if (bottomPx <= topPx) continue;
    regions.push({
      page_index: pageIndex,
      y0: (topPx - rect.top) / scale,
      y1: (bottomPx - rect.top) / scale,
    });
    rects.push({ left: rect.left, top: topPx, width: rect.width, height: bottomPx - topPx });
  }
  return { regions, rects };
}

function activeScrollEl() {
  return layoutMode === "book" ? bookScroll : stripScroll;
}

// Scrolling that turns the page when it runs out of room. In book layout a
// page that fits the window entirely cannot scroll at all, so without this
// fallback "scroll down" would be a command that visibly does nothing on the
// app's default layout — which reads as a broken microphone rather than as a
// document that has no more to scroll.
async function voiceScroll(delta) {
  const el = activeScrollEl();
  const before = el.scrollTop;
  el.scrollTop = before + delta * el.clientHeight * VOICE_SCROLL_FRACTION;
  if (el.scrollTop !== before) return;
  if (layoutMode !== "book") return; // The strip is one continuous scroller.

  const page = state.page_index;
  await (delta > 0 ? goNext() : goPrev());
  if (state.page_index === page) return; // Already on the first or last page.
  // Land where reading continues from: the top of the next page going down,
  // the bottom of the previous one going up. Keeping the old offset would
  // drop the reader into the middle of a page they haven't read yet.
  el.scrollTop = delta > 0 ? 0 : el.scrollHeight;
}

// Matches and highlights `query` against the currently visible viewport
// (api.py's highlight_by_voice reuses the region frozen by the
// start_listening() call that began this utterance) and reports the outcome
// through the mic pill, the same way "heard" already does for navigation —
// a highlight that silently fails to match would otherwise look identical to
// a dropped command.
async function voiceHighlight(query, sentence) {
  state = await callApi("highlight_by_voice", query, sentence);
  const result = state.voice_highlight || { matched: false, query };
  if (result.matched) {
    await afterAnnotationChange(result.affected_page);
    setMicHint(`Highlighted “${result.text}”`);
  } else {
    updateChrome();
    setMicHint(`Couldn't find “${result.query}” on this page`);
  }
}

const VOICE_ACTIONS = {
  NEXT_PAGE: () => goNext(),
  PREV_PAGE: () => goPrev(),
  GOTO_PAGE: (command) => goToPage(command.page),
  SCROLL_DOWN: () => voiceScroll(1),
  SCROLL_UP: () => voiceScroll(-1),
  HIGHLIGHT: (command) => voiceHighlight(command.query, false),
  HIGHLIGHT_SENTENCE: (command) => voiceHighlight(command.query, true),
  // Voice must never save as a side effect of anything else (docs/PRD.md):
  // this reaches the exact same prompt-or-save flow Ctrl+S does, rather than
  // a shortcut that skips the dialog.
  SAVE: () => promptSaveIfDirty(false),
};

window.addEventListener("lector:command", (ev) => {
  const { command } = ev.detail || {};
  // No command means the phrase matched nothing in the grammar. That is a
  // deliberate no-op: acting on a guess would move the reader somewhere they
  // didn't ask to go, and the mic pill already shows what was heard so they
  // can see why nothing happened.
  if (!command) return;
  const action = VOICE_ACTIONS[command.intent];
  if (!action) return;
  // A dialog owns the view while it is open; a page turn behind it would be
  // invisible and would apply to a document the reader is mid-decision about.
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  action(command);
});

// --- Voice indicator ------------------------------------------------------ //
// Reflects the engine's state in the footer's mic pill, for both ways of
// starting: a held key (Milestone 5) and the wake phrase (Milestone 8).

const micPill = document.getElementById("micPill");
const micLabel = document.getElementById("micLabel");
const micHint = document.getElementById("micHint");

// How long a recognized phrase stays on screen before the pill falls back to
// idle. Long enough to read a short command, short enough not to look stuck.
const HEARD_LINGER_MS = 2500;
let heardTimer = null;

// Overrides the mic pill's hint text once an async voice action (currently
// only highlighting) resolves, restarting the same linger timeout
// renderMicState's "heard" case uses — without this, a highlight's outcome
// would have to wait for the *next* utterance to ever be shown.
function setMicHint(text) {
  clearTimeout(heardTimer);
  micHint.textContent = text;
  heardTimer = setTimeout(() => voice.rest(), HEARD_LINGER_MS);
}

// What to tell the reader when nothing is happening. It has to name a way in
// they actually have: "Hold Space to talk" is useless advice for someone who
// switched push-to-talk off in favour of the wake phrase.
function idleHint(wake, pushToTalk) {
  if (wake && pushToTalk) return "Hold Space, or say “Hey Lector”";
  if (wake) return "Say “Hey Lector” to start";
  return "Hold Space to talk";
}

function renderMicState({ state: micState, text, error, command, wake, pushToTalk, viaWake }) {
  clearTimeout(heardTimer);
  micPill.classList.toggle("listening", micState === "listening");
  micPill.classList.toggle("unavailable", micState === "unavailable" || micState === "off");
  // Idle listening for the phrase is its own look: the microphone is open,
  // but nothing is being captured as a command, and showing the two the same
  // way would misrepresent one of them.
  micPill.classList.toggle("waking", micState === "idle" && Boolean(wake));
  if (Boolean(wake) !== wakeListening) {
    wakeListening = Boolean(wake);
    // Turning the wake phrase on mid-session leaves the recognizer holding
    // whatever vocabulary it was built with, so seed it with what is on
    // screen now rather than waiting for the reader to scroll.
    scheduleViewportPush();
  }
  // The outline is only ever painted by computeVoiceViewport() at the start
  // of a hold; every other state (heard, unavailable, idle) means the hold
  // is over, so this is the single place that takes it back down again.
  if (micState !== "listening") clearVoiceFocus();

  switch (micState) {
    case "unavailable":
      micLabel.textContent = "Voice unavailable";
      // The reason belongs in the tooltip, not the footer: docs/PRD.md makes
      // voice an accelerator, so its absence is a quiet fact, not an alert.
      micPill.title = error || "Speech model not installed";
      micHint.textContent = "Mouse and keyboard work as usual";
      break;
    case "listening":
      micLabel.textContent = text ? `"${text}"` : "Listening...";
      micPill.title = "";
      micHint.textContent = viaWake
        ? "Say your command"
        : "Release Space when you're done";
      break;
    case "heard":
      micLabel.textContent = text ? `"${text}"` : "Didn't catch that";
      micPill.title = "";
      // A phrase heard clearly but matching no command is worth naming out
      // loud: without it, "next pages" looks identical to a dead microphone,
      // and the reader has no way to tell that rephrasing is what's needed.
      micHint.textContent = text && !command
        ? "Not a command I know — try “next page”"
        : idleHint(wake, pushToTalk);
      // Back to rest through the engine rather than by rendering "idle"
      // directly: only it knows whether resting means ready, off, or
      // unavailable, and which activation modes to word the hint for.
      heardTimer = setTimeout(() => voice.rest(), HEARD_LINGER_MS);
      break;
    case "off":
      // Not a failure: docs/PRD.md makes voice an accelerator, and turning it
      // off is a supported choice, so this states the fact and stops there.
      micLabel.textContent = "Voice off";
      micPill.title = "Turn on push-to-talk or the wake phrase in Settings";
      micHint.textContent = "Mouse and keyboard work as usual";
      break;
    default:
      micLabel.textContent = wake ? "Listening for “Hey Lector”" : "Mic idle";
      micPill.title = "";
      micHint.textContent = idleHint(wake, pushToTalk);
  }
}

const voice = initVoice(renderMicState, computeVoiceViewport);

(async function init() {
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lector-theme", theme);
  state = await callApi("get_state");
  if (!state.is_open) {
    window.location.href = "../index.html";
    return;
  }
  // The backend has already resolved the reopen preference by this point, so
  // state.page_index/layout_mode are either the restored ones or the
  // start-at-the-beginning defaults; this view just renders what it is given.
  layoutMode = state.layout_mode === "strip" ? "strip" : "book";
  applyLayoutChrome();
  await refresh();
})();
