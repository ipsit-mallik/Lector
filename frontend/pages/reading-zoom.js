// Zoom: the steps, the preview while the re-render waits, the strip's anchor,
// one set_zoom on the bridge at a time, Fit width / Fit page geometry, and
// Ctrl+wheel.

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
  paintFindMarks();
  paintHighlightTargets();
  repaintInkStroke();
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
// always at least as fresh, so it's preserved across the zoom call instead
// (adoptState's keepPage).
async function zoomIn() {
  leaveZoomFit();
  adoptState(await callApi("zoom_in"), { keepPage: true });
  updateChrome();
  applyZoomPreview();
  scheduleZoomRefresh();
}

async function zoomOut() {
  leaveZoomFit();
  adoptState(await callApi("zoom_out"), { keepPage: true });
  updateChrome();
  applyZoomPreview();
  scheduleZoomRefresh();
}

// The percentage asked for and not yet sent or answered; Ctrl+scroll goes on
// from it rather than from a state.zoom_pct one reply behind.
let requestedZoomPct = null;
let zoomCallInFlight = false;

// One set_zoom on the bridge at a time, and only ever the latest percentage.
// A slider drag, or a fit following a window being dragged, asks for one a
// frame; the bridge answers each on its own thread, and replies overtaking
// each other could leave the backend at an older size than the top bar shows.
async function setZoomPct(pct) {
  requestedZoomPct = pct;
  if (zoomCallInFlight) return;
  zoomCallInFlight = true;
  try {
    while (requestedZoomPct !== null) {
      const wanted = requestedZoomPct;
      adoptState(await callApi("set_zoom", wanted / 100), { keepPage: true });
      // A newer one is already waiting: drawing this would flick the top-bar
      // value back to an older percentage on its way forward.
      if (requestedZoomPct !== wanted) continue;
      requestedZoomPct = null;
      updateChrome();
      applyZoomPreview();
      scheduleZoomRefresh();
    }
  } finally {
    zoomCallInFlight = false;
  }
}

// The page area and the current page in PDF points: what Fit width and Fit
// page fit the page to (zoom-control.js). The page area is the room between
// the progress bar and the toolbar, less the outline panel when it is open.
const pagePointSizes = new Map();
async function zoomFitGeometry() {
  if (!state.is_open) return null;
  const index = state.page_index;
  if (!pagePointSizes.has(index)) pagePointSizes.set(index, await callApi("get_page_size_points", index));
  const { width, height } = pagePointSizes.get(index);
  return {
    areaWidth: pageArea.clientWidth,
    areaHeight: pageArea.clientHeight,
    pageWidth: width,
    pageHeight: height,
  };
}

// Ctrl+scroll, and a touchpad pinch, which arrives as one. Gathered over a
// frame and turned into a factor, so a mouse notch (100px of delta) is about
// one − / + step and a pinch zooms smoothly; a movement too small to change
// the whole percentage is kept until it does.
const WHEEL_ZOOM_DOUBLING_PX = 300;
let wheelZoomDelta = 0;
let wheelZoomFrame = 0;

function applyWheelZoom() {
  wheelZoomFrame = 0;
  if (!state.is_open) return;
  const from = requestedZoomPct ?? state.zoom_pct;
  const wanted = Math.round(from * Math.pow(2, -wheelZoomDelta / WHEEL_ZOOM_DOUBLING_PX));
  if (wanted === from) return;
  wheelZoomDelta = 0;
  const pct = Math.min(state.zoom_max_pct, Math.max(state.zoom_min_pct, wanted));
  if (pct === from) return;
  leaveZoomFit();
  setZoomPct(pct);
}

// Ctrl+wheel and a touchpad pinch, anywhere in the window (registered in
// reading.js).
function onWheelZoom(ev) {
  if (!(ev.ctrlKey || ev.metaKey)) return;
  // Never the web view's own zoom, wherever the pointer is.
  ev.preventDefault();
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  wheelZoomDelta += ev.deltaY;
  if (!wheelZoomFrame) wheelZoomFrame = requestAnimationFrame(applyWheelZoom);
}
