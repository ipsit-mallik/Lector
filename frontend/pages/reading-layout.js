// Drawing the pages, in Book and in Strip, and where the view is scrolled:
// the strip's lazy page images and its page tracking, the page-scroll
// fractions a zoom or a layout switch keeps, and switching layout.

const pageArea = document.getElementById("pageArea");
const bookScroll = document.getElementById("bookScroll");
const pageCard = document.getElementById("pageCard");
const pageSurface = document.getElementById("pageSurface");
const stripScroll = document.getElementById("stripScroll");
const stripContainer = document.getElementById("stripContainer");
const selectionLayer = document.getElementById("selectionLayer");
const voiceFocusLayer = document.getElementById("voiceFocusLayer");
const highlightCountLbl = document.getElementById("highlightCount");

let layoutMode = "book"; // or "strip"

let stripDirty = true;
let stripBuildPromise = null; // guards against overlapping refreshStrip() builds (e.g. rapid nav)

let renderedZoom = null; // the zoom level the currently-displayed page images were actually rendered at

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

// `animate` is for a page jump the reader asked for (next, previous, go to):
// the strip glides to the page instead of cutting. Everything else that calls
// this is positioning a view that has just opened or been rebuilt (opening the
// document, switching layout), where an animation from the top would only be
// noise.
async function refresh({ animate = false } = {}) {
  updateChrome();

  if (layoutMode === "book") {
    renderHighlightCount();
    await refreshBook();
  } else {
    renderHighlightCount();
    await refreshStrip(animate);
  }
  scheduleViewportPush();
  scheduleOcrPrefetch();
  paintFindMarks();
  paintHighlightTargets();
}

// Pages are drawn for the screen's pixel density (the backend renders the
// bitmap at devicePixelRatio and the <img> is given the page's CSS size), so
// moving the window to a screen with another density redraws them.
function watchPixelRatio() {
  matchMedia(`(resolution: ${devicePixelRatio}dppx)`).addEventListener("change", () => {
    if (state.is_open) {
      markStripDirty();
      refresh();
      redrawPageThumbsForPixelRatio();
    }
    watchPixelRatio();
  }, { once: true });
}

async function refreshBook() {
  bookScroll.hidden = false;
  stripScroll.hidden = true;
  const { image, width, height } = await callApi("get_page_image", state.page_index, devicePixelRatio);
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
// The strip's page images by page index, so the find bar can look one up on
// every scroll frame without searching the whole strip for it.
let stripPageEls = [];

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
    // Built with the tool already on, a page still needs its cursor.
    img.classList.toggle("selecting", highlightMode);
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
  stripPageEls = imgs;
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
  const { image } = await callApi("get_page_image", index, devicePixelRatio);
  if (image) await showImage(img, image);
}

async function refreshStrip(animate) {
  bookScroll.hidden = true;
  stripScroll.hidden = false;
  // A rebuilt strip starts again from the top: that is positioning, not a jump.
  const rebuilding = Boolean(stripBuildPromise) || stripDirty;

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
    scrollElementTo(stripScroll, target.offsetTop - stripContainer.offsetTop, {
      animate: animate && !rebuilding,
    });
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
  const { image, width, height } = await callApi("get_page_image", index, devicePixelRatio);
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
    trackStripPage(index);
    updateChrome();
    // Not waited for, and the page in its reply is kept as tracked here: the
    // strip may already have moved on by the time it lands.
    callApi("goto_page", index)
      .then((reply) => adoptState(reply, { keepPage: true }))
      .catch((err) => console.error("goto_page failed:", err));
  });
}

function markStripDirty() {
  stripDirty = true;
}

function centerActivePage() {
  return layoutMode === "book"
    ? centerPageOnWindow(bookScroll, pageCard)
    : centerPageOnWindow(stripScroll, stripContainer);
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

// Where the progress bar's read fill ends: a page and how far through it
// (progress-layout.js's progressReadPosition). Strip: the scroll's overall
// progress, so scrolled to the bottom is exactly 100% and a document that fits
// without scrolling is 100% too. Book: the page being read, as far down it as
// it has been scrolled (all of it for a page that fits), and the last page is
// always 100%.
function currentPageReadFraction() {
  if (layoutMode === "book") {
    if (state.page_index >= state.page_count - 1) return { page: state.page_index, fraction: 1 };
    const range = bookScroll.scrollHeight - bookScroll.clientHeight;
    return { page: state.page_index, fraction: range > 0 ? bookScroll.scrollTop / range : 1 };
  }
  return progressReadPosition({
    scrollTop: stripScroll.scrollTop,
    scrollHeight: stripScroll.scrollHeight,
    clientHeight: stripScroll.clientHeight,
    pages: stripPageEls.map((el) => ({ top: stripPageTop(el), height: el.getBoundingClientRect().height })),
  }) || { page: state.page_index, fraction: 0 };
}

// Once a frame while either view scrolls, and only while the bar is shown.
let progressScrollTicking = false;
function scheduleProgressScroll() {
  if (progressScrollTicking || !progressFollowsScroll()) return;
  progressScrollTicking = true;
  requestAnimationFrame(() => {
    progressScrollTicking = false;
    if (state.is_open) updateProgressScroll(currentPageReadFraction());
  });
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
  // Fire-and-forget, and its page index is not adopted: during a strip
  // scroll it can still be the pre-scroll one (see the note on the zoom
  // handlers), and taking it would drag the view back to a stale page. The
  // rest of the reply is adopted (keepPage).
  callApi("set_layout_mode", layoutMode)
    .then((reply) => adoptState(reply, { keepPage: true }))
    .catch((err) => console.error("set_layout_mode failed:", err));
  await refresh();
  applyPageScrollFraction(fraction);
  refitZoom();
}

function activeScrollEl() {
  return layoutMode === "book" ? bookScroll : stripScroll;
}

// The layout the document opens in, which the backend has already resolved
// from the reopen preference (init(), reading.js).
function startInSavedLayout() {
  layoutMode = state.layout_mode === "strip" ? "strip" : "book";
  applyLayoutChrome();
}
