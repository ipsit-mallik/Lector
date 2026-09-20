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

const bookScroll = document.getElementById("bookScroll");
const pageCard = document.getElementById("pageCard");
const pageSurface = document.getElementById("pageSurface");
const stripScroll = document.getElementById("stripScroll");
const stripContainer = document.getElementById("stripContainer");
const highlightCountLbl = document.getElementById("highlightCount");

let state = { is_open: false };
let layoutMode = "book"; // or "strip"
let highlightMode = false;
let stripDirty = true;
let stripBuildPromise = null; // guards against overlapping refreshStrip() builds (e.g. rapid nav)
let dragStart = null; // {pageIndex, x, y}
let renderedZoom = null; // the zoom level the currently-displayed page images were actually rendered at

const THEME_ORDER = ["light", "dark", "sepia"];

function b64ToDataUrl(b64) {
  return `data:image/png;base64,${b64}`;
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
}

async function refreshBook() {
  bookScroll.hidden = false;
  stripScroll.hidden = true;
  const { image, width, height } = await callApi("get_page_image", state.page_index);
  if (image) {
    pageSurface.src = b64ToDataUrl(image);
    pageSurface.width = width;
    pageSurface.height = height;
    pageSurface.style.width = "";
    pageSurface.style.height = "";
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
  if (image) img.src = b64ToDataUrl(image);
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
  img.src = b64ToDataUrl(image);
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
}

function bindSelection(el, getPageIndex) {
  el.addEventListener("mousedown", (ev) => {
    if (!highlightMode) return;
    const rect = el.getBoundingClientRect();
    dragStart = { pageIndex: getPageIndex(), x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  });
  el.addEventListener("mouseup", async (ev) => {
    const pageIndex = getPageIndex();
    if (!highlightMode || !dragStart || dragStart.pageIndex !== pageIndex) {
      dragStart = null;
      return;
    }
    const rect = el.getBoundingClientRect();
    const end = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    const start = dragStart;
    dragStart = null;
    state = await callApi(
      "highlight_range", pageIndex, state.zoom, start.x, start.y, end.x, end.y
    );
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
  });
}

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
