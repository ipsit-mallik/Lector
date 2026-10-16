// The reader's progress bar, on the window's left edge.
//
// Collapsed, it is a 6px bar just inside the edge: how far through the
// document the reader is (read fill over the unread track) and a marker on
// every page with a highlight; where the fill ends is the reader's place. It
// is at full strength on any scroll, page change, pointer near the edge or
// hover, and fades to idle PROGRESS_IDLE_MS after the last of those.
//
// Pointing at it for PROGRESS_EXPAND_DELAY_MS widens it, over the page, into a
// bar with the current page's number, one segment per chapter
// (the PDF's top-level outline) or page ticks, and a preview of the page under
// the pointer; it narrows again PROGRESS_COLLAPSE_DELAY_MS after the pointer
// leaves, never mid-drag. A click or a drag goes to the page under the
// pointer. A page change the reader asked for elsewhere (keyboard, voice, a
// jump) widens it for PROGRESS_FLASH_MS so the new place is seen.
//
// Positions are fractions of the document: page i of n spans i/n to (i+1)/n
// of the bar's height, so the bar needs no layout of its own beyond the track's
// height (for the tick spacing and the read fill). reading.js owns the
// document and tells this file what changed (updateProgressBar) and how to go
// to a page (configureProgressBar's jumpTo); this file never calls the
// navigation API itself.

const readingProgress = document.getElementById("readingProgress");
const progressEdge = document.getElementById("progressEdge");
const progressTrack = document.getElementById("progressTrack");
const progressSegments = document.getElementById("progressSegments");
const progressFillClip = document.getElementById("progressFillClip");
const progressFillSegments = document.getElementById("progressFillSegments");
const progressTicks = document.getElementById("progressTicks");
const progressMarkers = document.getElementById("progressMarkers");
const progressLabel = document.getElementById("progressLabel");
const progressTip = document.getElementById("progressTip");
const progressTipThumb = document.getElementById("progressTipThumb");
const progressTipImage = document.getElementById("progressTipImage");
const progressTipPage = document.getElementById("progressTipPage");
const progressTipChapter = document.getElementById("progressTipChapter");

// Hover intent: long enough that crossing the edge on the way to the outline
// button does not open the bar, short enough to feel direct.
const PROGRESS_EXPAND_DELAY_MS = 150;
const PROGRESS_COLLAPSE_DELAY_MS = 400;
// How long the bar stays open to show where a page change landed.
const PROGRESS_FLASH_MS = 1500;
// Pages closer together than this on the bar get no tick: a solid comb of
// hairlines says nothing about where a page is.
const PROGRESS_MIN_TICK_PX = 3;
// The hover preview's width in CSS pixels (reading.css .progress-tip-thumb).
const PROGRESS_THUMB_CSS_PX = 56;
// A preview is fetched only once the pointer has rested on a page this long,
// so sweeping along the bar does not render every page it passes.
const PROGRESS_THUMB_DELAY_MS = 80;
// The tip keeps this far from the body row's top and bottom edges.
const PROGRESS_TIP_MARGIN_PX = 8;
// Page Up / Page Down on the focused bar move this many pages.
const PROGRESS_KEY_PAGE_STEP = 10;
// How long after the last scroll, page change or nearby pointer the bar
// fades to idle (reading.css .is-idle).
const PROGRESS_IDLE_MS = 3000;
// A pointer this close to the window's left edge wakes the bar.
const PROGRESS_WAKE_ZONE_PX = 40;
// A highlight marker's height, centred on its page (reading.css).
const PROGRESS_MARKER_PX = 3;

let progressHooks = null;
let progressPageCount = 0;
let progressChapters = [];
let progressHasOutline = false;
let progressPage = 0;
let progressHighlighted = new Set();
let progressHovering = false;
let progressDragging = false;
let progressExpandTimer = null;
let progressCollapseTimer = null;
let progressFlashTimer = null;
let progressThumbTimer = null;
let progressIdleTimer = null;
let progressTipIndex = null;
// Page index -> data URL of its preview.
const progressThumbs = new Map();
// The drag's jumps are serialized: one in flight, the latest wanted one next,
// so a fast drag lands where the pointer stopped, not on a stale page.
let progressJumpInFlight = false;
let progressJumpWanted = null;

// hooks: { jumpTo(index) goes to a page without animating }
function configureProgressBar(hooks) {
  progressHooks = hooks;
}

function initProgressBar({ pageCount, chapters, visible }) {
  progressPageCount = Math.max(0, pageCount || 0);
  progressHasOutline = Boolean(chapters && chapters.length);
  progressChapters = progressHasOutline
    ? chapters
    : [{ title: null, start: 0, end: Math.max(0, progressPageCount - 1) }];
  renderProgressSegments();
  readingProgress.setAttribute("aria-valuemax", String(Math.max(1, progressPageCount)));
  setProgressBarVisible(visible);
}

function setProgressBarVisible(on) {
  progressEdge.hidden = !on;
  if (on) {
    measureProgressTrack();
    wakeProgressBar();
  } else {
    collapseProgress();
    clearTimeout(progressIdleTimer);
  }
}

function isProgressBarVisible() {
  return !progressEdge.hidden;
}

// --- Drawing ----------------------------------------------------------------- //

function pagePercent(index) {
  return progressPageCount ? (index / progressPageCount) * 100 : 0;
}

function buildSegments(container) {
  container.replaceChildren(...progressChapters.map((chapter) => {
    const el = document.createElement("div");
    el.className = "progress-segment";
    el.style.top = `${pagePercent(chapter.start)}%`;
    el.style.height = `${pagePercent(chapter.end + 1) - pagePercent(chapter.start)}%`;
    return el;
  }));
}

function renderProgressSegments() {
  buildSegments(progressSegments);
  buildSegments(progressFillSegments);
}

// The read fill's copy of the segments is laid out against the whole track,
// not against the clip that cuts it at the reading position; the ticks need
// the real distance between pages.
function measureProgressTrack() {
  const height = progressTrack.clientHeight;
  if (!height || !progressPageCount) return;
  progressFillSegments.style.height = `${height}px`;
  const step = height / progressPageCount;
  progressTicks.style.setProperty("--tick-step", `${step}px`);
  progressTicks.classList.toggle("has-ticks", !progressHasOutline && step >= PROGRESS_MIN_TICK_PX);
}

new ResizeObserver(measureProgressTrack).observe(progressTrack);

// Consecutive highlighted pages as [start, end] runs, so neighbours read as one
// marker rather than a row of ticks with hairline gaps between them.
function highlightRuns(pages) {
  const runs = [];
  for (const page of [...pages].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && page === last[1] + 1) last[1] = page;
    else runs.push([page, page]);
  }
  return runs;
}

function renderProgressMarkers() {
  progressMarkers.replaceChildren(...highlightRuns(progressHighlighted).map(([start, end]) => {
    const el = document.createElement("div");
    // From the middle of the run's first page to the middle of its last,
    // PROGRESS_MARKER_PX tall: one page is a 3px tick on its page.
    const first = pagePercent(start + 0.5);
    el.className = "progress-marker";
    el.style.top = `calc(${first}% - ${PROGRESS_MARKER_PX / 2}px)`;
    el.style.height = `calc(${pagePercent(end + 0.5) - first}% + ${PROGRESS_MARKER_PX}px)`;
    return el;
  }));
}

function chapterAt(index) {
  if (!progressHasOutline) return null;
  return progressChapters.find((c) => index >= c.start && index <= c.end) || null;
}

function renderProgressPosition() {
  const middle = pagePercent(progressPage + 0.5);
  progressFillClip.style.height = `${pagePercent(progressPage + 1)}%`;
  progressLabel.style.top = `${middle}%`;
  progressLabel.textContent = String(progressPage + 1);
  const chapter = chapterAt(progressPage);
  readingProgress.setAttribute("aria-valuenow", String(progressPage + 1));
  readingProgress.setAttribute(
    "aria-valuetext",
    `Page ${progressPage + 1} of ${progressPageCount}${chapter && chapter.title ? `, ${chapter.title}` : ""}`,
  );
}

// From the state every page change and highlight change already returns, so
// markers follow adds, removals, undo and redo, saved or not.
function updateProgressBar(state) {
  if (!state || !state.is_open) return;
  if (state.page_count !== progressPageCount) {
    progressPageCount = state.page_count;
    readingProgress.setAttribute("aria-valuemax", String(progressPageCount));
    renderProgressSegments();
    measureProgressTrack();
  }
  if (state.page_index !== progressPage) wakeProgressBar();
  progressPage = state.page_index;
  const pages = state.highlighted_pages || [];
  const changed = pages.length !== progressHighlighted.size || pages.some((p) => !progressHighlighted.has(p));
  if (changed) {
    progressHighlighted = new Set(pages);
    renderProgressMarkers();
    if (progressTipIndex !== null) {
      progressTip.classList.toggle("has-highlights", progressHighlighted.has(progressTipIndex));
    }
  }
  renderProgressPosition();
}

// --- Expanding and collapsing ------------------------------------------------ //

function expandProgress() {
  clearTimeout(progressExpandTimer);
  clearTimeout(progressCollapseTimer);
  readingProgress.classList.add("is-expanded");
}

function collapseProgress() {
  clearTimeout(progressExpandTimer);
  clearTimeout(progressCollapseTimer);
  clearTimeout(progressFlashTimer);
  readingProgress.classList.remove("is-expanded");
  hideProgressTip();
}

function isProgressHeldOpen() {
  return progressHovering || progressDragging || document.activeElement === readingProgress;
}

// After a page change the reader asked for somewhere else: show where it
// landed, then get out of the way. Not while the bar is already in use.
function flashProgressBar() {
  if (!isProgressBarVisible() || isProgressHeldOpen()) return;
  expandProgress();
  clearTimeout(progressFlashTimer);
  progressFlashTimer = setTimeout(() => {
    if (!isProgressHeldOpen()) collapseProgress();
  }, PROGRESS_FLASH_MS);
}

// --- Active and idle ---------------------------------------------------------- //
// Active, the bar is at full strength; idle, it fades back (reading.css
// .is-idle) and the header's page number is the place to look. reading.js
// calls wakeProgressBar on every scroll.

function wakeProgressBar() {
  if (!isProgressBarVisible()) return;
  readingProgress.classList.remove("is-idle");
  clearTimeout(progressIdleTimer);
  progressIdleTimer = setTimeout(idleProgressBar, PROGRESS_IDLE_MS);
}

// Not while the bar is open or in use: look again once it has been left alone.
function idleProgressBar() {
  if (isProgressHeldOpen() || readingProgress.classList.contains("is-expanded")) {
    wakeProgressBar();
    return;
  }
  readingProgress.classList.add("is-idle");
}

document.addEventListener("pointermove", (ev) => {
  if (!isProgressBarVisible()) return;
  if (ev.clientX - progressEdge.getBoundingClientRect().left <= PROGRESS_WAKE_ZONE_PX) wakeProgressBar();
}, { passive: true });

// --- Pointer ----------------------------------------------------------------- //

function pageAtY(clientY) {
  const rect = progressTrack.getBoundingClientRect();
  if (!rect.height || !progressPageCount) return 0;
  const fraction = (clientY - rect.top) / rect.height;
  return Math.min(progressPageCount - 1, Math.max(0, Math.floor(fraction * progressPageCount)));
}

async function jumpProgressTo(index) {
  if (!progressHooks) return;
  progressJumpWanted = index;
  if (progressJumpInFlight) return;
  progressJumpInFlight = true;
  try {
    while (progressJumpWanted !== null) {
      const next = progressJumpWanted;
      progressJumpWanted = null;
      if (next !== progressPage) await progressHooks.jumpTo(next);
    }
  } catch (err) {
    console.error("progress bar jump failed:", err);
  } finally {
    progressJumpInFlight = false;
  }
}

readingProgress.addEventListener("pointerenter", () => {
  progressHovering = true;
  wakeProgressBar();
  clearTimeout(progressCollapseTimer);
  clearTimeout(progressFlashTimer);
  if (!readingProgress.classList.contains("is-expanded")) {
    progressExpandTimer = setTimeout(expandProgress, PROGRESS_EXPAND_DELAY_MS);
  }
});

readingProgress.addEventListener("pointerleave", () => {
  progressHovering = false;
  clearTimeout(progressExpandTimer);
  if (progressDragging) return;
  hideProgressTip();
  if (document.activeElement === readingProgress) return;
  progressCollapseTimer = setTimeout(collapseProgress, PROGRESS_COLLAPSE_DELAY_MS);
});

readingProgress.addEventListener("pointermove", (ev) => {
  if (!readingProgress.classList.contains("is-expanded") && !progressDragging) return;
  const index = pageAtY(ev.clientY);
  showProgressTip(index, ev.clientY);
  if (progressDragging) jumpProgressTo(index);
});

readingProgress.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  ev.preventDefault();
  progressDragging = true;
  readingProgress.setPointerCapture(ev.pointerId);
  expandProgress();
  const index = pageAtY(ev.clientY);
  showProgressTip(index, ev.clientY);
  jumpProgressTo(index);
});

function endProgressDrag(ev) {
  if (!progressDragging) return;
  progressDragging = false;
  if (readingProgress.hasPointerCapture(ev.pointerId)) readingProgress.releasePointerCapture(ev.pointerId);
  // Released off the bar: it closes as it would have when the pointer left.
  if (!progressHovering) {
    hideProgressTip();
    if (document.activeElement !== readingProgress) {
      progressCollapseTimer = setTimeout(collapseProgress, PROGRESS_COLLAPSE_DELAY_MS);
    }
  }
}

readingProgress.addEventListener("pointerup", endProgressDrag);
readingProgress.addEventListener("pointercancel", endProgressDrag);

// --- Keyboard ---------------------------------------------------------------- //
// A slider: keyboard focus opens it, the arrows step a page, Page Up/Down ten,
// Home and End the ends. The keys stop here, so the reader's own arrow handling
// does not turn a second page.

readingProgress.addEventListener("focus", () => {
  wakeProgressBar();
  if (readingProgress.matches(":focus-visible")) expandProgress();
});

readingProgress.addEventListener("blur", () => {
  if (!progressHovering && !progressDragging) collapseProgress();
});

readingProgress.addEventListener("keydown", (ev) => {
  const steps = {
    ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1,
    PageUp: -PROGRESS_KEY_PAGE_STEP, PageDown: PROGRESS_KEY_PAGE_STEP,
  };
  let target = null;
  if (ev.key in steps) target = progressPage + steps[ev.key];
  else if (ev.key === "Home") target = 0;
  else if (ev.key === "End") target = progressPageCount - 1;
  if (target === null) return;
  ev.preventDefault();
  ev.stopPropagation();
  expandProgress();
  jumpProgressTo(Math.min(progressPageCount - 1, Math.max(0, target)));
});

// --- Tip (page, chapter, preview, highlights) -------------------------------- //

function showProgressTip(index, clientY) {
  progressTipIndex = index;
  progressTipPage.textContent = `Page ${index + 1} of ${progressPageCount}`;
  const chapter = chapterAt(index);
  progressTipChapter.textContent = chapter && chapter.title ? chapter.title : "";
  progressTip.classList.toggle("has-highlights", progressHighlighted.has(index));
  showProgressThumb(index);
  progressTip.classList.add("is-showing");
  placeProgressTip(clientY);
}

function placeProgressTip(clientY) {
  const edge = progressEdge.getBoundingClientRect();
  const height = progressTip.offsetHeight;
  const top = Math.min(
    Math.max(clientY - edge.top - height / 2, PROGRESS_TIP_MARGIN_PX),
    edge.height - height - PROGRESS_TIP_MARGIN_PX,
  );
  progressTip.style.top = `${Math.max(0, top)}px`;
}

function hideProgressTip() {
  progressTipIndex = null;
  clearTimeout(progressThumbTimer);
  progressTip.classList.remove("is-showing");
}

// The preview slot shows the page's image (url), the loading shimmer
// ("loading"), or plain paper when there is no image ("empty"). The <img> is
// hidden whenever it has no source, which would draw a broken-image icon;
// showing it again replays its fade-in (reading.css).
function setProgressThumb(state) {
  const loaded = state !== "loading" && state !== "empty";
  progressTipThumb.classList.toggle("skeleton", state === "loading");
  progressTipThumb.classList.toggle("is-empty", !loaded);
  if (loaded) progressTipImage.src = state;
  else progressTipImage.removeAttribute("src");
  progressTipImage.hidden = !loaded;
}

function showProgressThumb(index) {
  clearTimeout(progressThumbTimer);
  const cached = progressThumbs.get(index);
  if (cached) {
    setProgressThumb(cached);
    return;
  }
  setProgressThumb("loading");
  progressThumbTimer = setTimeout(async () => {
    const width = Math.round(PROGRESS_THUMB_CSS_PX * (window.devicePixelRatio || 1));
    let thumb;
    try {
      thumb = await callApi("get_page_thumbnail", index, width);
    } catch (err) {
      console.error("get_page_thumbnail failed:", err);
      thumb = {};
    }
    const url = thumb.image ? `data:image/png;base64,${thumb.image}` : null;
    if (url) progressThumbs.set(index, url);
    // Only if the pointer is still on that page.
    if (progressTipIndex === index) setProgressThumb(url || "empty");
  }, PROGRESS_THUMB_DELAY_MS);
}

// A highlight change redraws its page, and with it the preview.
function forgetProgressThumb(index) {
  if (index == null) progressThumbs.clear();
  else progressThumbs.delete(index);
}
