// The outline and pages panel, opened from the header's outline button and
// sitting between the progress bar and the page.
//
// * Outline: the PDF's bookmarks, nested and collapsible, the section being
//   read marked. A PDF without bookmarks opens on Pages instead.
// * Pages: a thumbnail per page, the one being read marked, each rendered only
//   once it scrolls near the panel's view (an IntersectionObserver, a few
//   renders at a time), so a 900-page book costs nothing until scrolled.
// * Voice: "show outline" and "show pages" open the panel on that tab and put
//   the numbered-overlay picker over it (the "picker" voice scope, the same
//   one the Recent grid uses): outline entries are numbered in the order shown,
//   thumbnails by their page number, and saying a number jumps there. "cancel"
//   takes the numbers away. Clicking works the same with or without them.
//
// * Width: dragged at the panel's right edge (or that edge focused and the
//   arrow keys), 168-400px, 200 by default. Thumbnails are at most 128px wide
//   (the panel never makes them smaller: 168 less its padding is 136), one
//   column, or two once the panel has room for two and the gap between them.
// * The progress bar, while the panel is open, does not widen or show its
//   tip; pointing at it marks that page here instead (previewOutlinePage):
//   its thumbnail on Pages, the entry holding it on Outline.
//
// Whether the panel is open, and its width, are remembered
// (api.set_outline_open, api.set_outline_width). reading.js owns navigation:
// it hands this file jumpTo(index) and tells it what changed through
// updateOutlinePanel(state). The page re-centres, and a fit zoom refits, from
// reading.js's watch on the page area, which the panel's width changes.

const outlineToggleBtn = document.getElementById("outlineToggleBtn");
const outlinePanel = document.getElementById("outlinePanel");
const outlineTab = document.getElementById("outlineTab");
const pagesTab = document.getElementById("pagesTab");
const outlineTabPanel = document.getElementById("outlineTabPanel");
const pagesTabPanel = document.getElementById("pagesTabPanel");
const outlineTree = document.getElementById("outlineTree");
const outlineEmpty = document.getElementById("outlineEmpty");
const outlineBrowsePagesBtn = document.getElementById("outlineBrowsePagesBtn");
const pagesList = document.getElementById("pagesList");
const outlineResizer = document.getElementById("outlineResizer");

const OUTLINE_TAB = "outline";
const PAGES_TAB = "pages";
// A thumbnail's widest (reading.css .pages-list's columns): it is drawn at
// this times the screen's pixel ratio, never at the page's own size.
const PAGE_THUMB_MAX_CSS_PX = 128;
// What an arrow key moves the panel's edge by.
const OUTLINE_RESIZE_STEP_PX = 16;
// Room left above or below a thumbnail or entry scrolled into view for the
// progress bar.
const OUTLINE_REVEAL_MARGIN_PX = 8;
// A page whose size is unknown is drawn this tall for its width (A4-ish).
const PAGE_THUMB_FALLBACK_RATIO = 1.3;
// How far outside the panel's view a thumbnail starts rendering, so scrolling
// rarely meets a blank one.
const PAGE_THUMB_MARGIN = "400px 0px";
// Thumbnails rendered at once; the rest wait their turn.
const PAGE_THUMB_CONCURRENCY = 3;

let outlineHooks = null;
let outlineEntries = [];
// Every entry in document order: { entry, li, button, row, parents: [li] }.
let outlineFlat = [];
let outlinePageCount = 0;
let outlinePageSizes = [];
let outlineCurrentPage = 0;
let outlineHighlighted = new Set();
let outlineActiveTab = OUTLINE_TAB;
let pagesBuilt = false;
let pageThumbEls = [];
let pageThumbObserver = null;
const pageThumbQueue = [];
let pageThumbsRendering = 0;
let outlinePickerTab = null;
let outlinePickerTargets = [];
let outlineWidth = 200;
let outlineWidthLimits = { min: 168, max: 400 };
// What the progress bar is pointing at (previewOutlinePage), if anything.
let outlinePreviewed = null;
const outlinePickerScope = createVoiceScope("picker");

// hooks: {
//   jumpTo(index)  goes to a page (a jump: animated, flashes the progress bar),
//   say(text)      reports a voice outcome in the mic pill,
//   onToggle(open) after the panel opens or closes,
// }
function configureOutlinePanel(hooks) {
  outlineHooks = hooks;
}

function initOutlinePanel({ entries, pageCount, pageSizes, currentPage, open, width, minWidth, maxWidth }) {
  if (minWidth && maxWidth) outlineWidthLimits = { min: minWidth, max: maxWidth };
  outlineResizer.setAttribute("aria-valuemin", String(outlineWidthLimits.min));
  outlineResizer.setAttribute("aria-valuemax", String(outlineWidthLimits.max));
  if (width) setOutlineWidth(width);
  outlineEntries = entries || [];
  outlinePageCount = pageCount;
  outlinePageSizes = pageSizes || [];
  outlineCurrentPage = currentPage || 0;
  buildOutlineTree();
  outlineActiveTab = hasOutline() ? OUTLINE_TAB : PAGES_TAB;
  if (open) openOutlinePanel(outlineActiveTab, { remember: false });
}

function hasOutline() {
  return outlineEntries.length > 0;
}

function isOutlinePanelOpen() {
  return !outlinePanel.hidden;
}

// --- Opening, closing, tabs ---------------------------------------------------- //

function rememberOutlineOpen(isOpen) {
  callApi("set_outline_open", isOpen).catch((err) => console.error("set_outline_open failed:", err));
}

function openOutlinePanel(tab = outlineActiveTab, { remember = true } = {}) {
  outlinePanel.hidden = false;
  outlineToggleBtn.setAttribute("aria-expanded", "true");
  outlineToggleBtn.classList.add("active");
  selectOutlineTab(tab);
  if (remember) rememberOutlineOpen(true);
  if (outlineHooks && outlineHooks.onToggle) outlineHooks.onToggle(true);
}

function closeOutlinePanel() {
  if (!isOutlinePanelOpen()) return;
  endOutlinePicker();
  outlinePanel.hidden = true;
  outlineToggleBtn.setAttribute("aria-expanded", "false");
  outlineToggleBtn.classList.remove("active");
  previewOutlinePage(null);
  rememberOutlineOpen(false);
  if (outlineHooks && outlineHooks.onToggle) outlineHooks.onToggle(false);
}

function toggleOutlinePanel() {
  if (isOutlinePanelOpen()) closeOutlinePanel();
  else openOutlinePanel();
}

function selectOutlineTab(tab) {
  if (tab !== outlinePickerTab) endOutlinePicker();
  previewOutlinePage(null);
  outlineActiveTab = tab;
  const pages = tab === PAGES_TAB;
  outlineTab.setAttribute("aria-selected", String(!pages));
  pagesTab.setAttribute("aria-selected", String(pages));
  outlineTab.tabIndex = pages ? -1 : 0;
  pagesTab.tabIndex = pages ? 0 : -1;
  outlineTabPanel.hidden = pages;
  pagesTabPanel.hidden = !pages;
  if (pages) {
    buildPagesList();
    revealCurrentThumb();
  } else {
    revealCurrentEntry();
  }
}

outlineToggleBtn.addEventListener("click", toggleOutlinePanel);
outlineTab.addEventListener("click", () => selectOutlineTab(OUTLINE_TAB));
pagesTab.addEventListener("click", () => selectOutlineTab(PAGES_TAB));
outlineBrowsePagesBtn.addEventListener("click", () => {
  selectOutlineTab(PAGES_TAB);
  pagesTab.focus();
});

// Tabs: one Tab stop, the arrow keys move between them. Stopped here so the
// reader's own arrow keys do not turn a page as well.
[outlineTab, pagesTab].forEach((tab) => {
  tab.addEventListener("keydown", (ev) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) return;
    ev.preventDefault();
    ev.stopPropagation();
    const other = tab === outlineTab ? pagesTab : outlineTab;
    const target = ev.key === "Home" ? outlineTab : ev.key === "End" ? pagesTab : other;
    selectOutlineTab(target === pagesTab ? PAGES_TAB : OUTLINE_TAB);
    target.focus();
  });
});

// --- Outline tree ---------------------------------------------------------------- //

function buildOutlineTree() {
  outlineFlat = [];
  outlineEmpty.hidden = hasOutline();
  outlineTree.replaceChildren();
  if (!hasOutline()) return;
  outlineTree.append(buildOutlineList(outlineEntries, []));
  mountIcons(outlineTree);
  // Open the way down to the section being read, and nothing else.
  const current = currentOutlineItem();
  if (current) current.parents.forEach((li) => setOutlineExpanded(li, true));
  markCurrentEntry();
}

function buildOutlineList(entries, parents) {
  const list = document.createElement("ul");
  list.className = "outline-list";
  for (const entry of entries) {
    const li = document.createElement("li");
    const row = document.createElement("div");
    row.className = "outline-row";
    if (entry.children.length) {
      const twisty = document.createElement("button");
      twisty.type = "button";
      twisty.className = "outline-twisty";
      twisty.setAttribute("aria-expanded", "false");
      twisty.setAttribute("aria-label", `Sections in ${entry.title}`);
      const chevron = document.createElement("span");
      chevron.dataset.icon = "chevron_right";
      twisty.append(chevron);
      twisty.addEventListener("click", () => {
        setOutlineExpanded(li, twisty.getAttribute("aria-expanded") !== "true");
      });
      row.append(twisty);
    } else {
      const spacer = document.createElement("span");
      spacer.className = "outline-twisty-spacer";
      row.append(spacer);
    }
    row.append(buildOutlineEntry(entry));
    li.append(row);
    outlineFlat.push({ entry, li, row, button: row.lastChild, parents });
    if (entry.children.length) {
      const children = buildOutlineList(entry.children, [...parents, li]);
      children.hidden = true;
      li.append(children);
    }
    list.append(li);
  }
  return list;
}

function buildOutlineEntry(entry) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "outline-entry";
  button.title = entry.title;
  const title = document.createElement("span");
  title.className = "outline-entry-title";
  title.textContent = entry.title;
  button.append(title);
  if (entry.page_index === null) {
    // A bookmark that points nowhere this file can resolve: listed, so the
    // outline is complete, but there is nowhere to go.
    button.disabled = true;
    return button;
  }
  const page = document.createElement("span");
  page.className = "outline-entry-page";
  page.textContent = String(entry.page_index + 1);
  button.append(page);
  button.setAttribute("aria-label", `${entry.title}, page ${entry.page_index + 1}`);
  button.addEventListener("click", () => {
    if (outlineHooks) outlineHooks.jumpTo(entry.page_index);
  });
  return button;
}

function isOutlineExpanded(li) {
  const twisty = li.querySelector(":scope > .outline-row > .outline-twisty");
  return Boolean(twisty) && twisty.getAttribute("aria-expanded") === "true";
}

function setOutlineExpanded(li, expanded) {
  const twisty = li.querySelector(":scope > .outline-row > .outline-twisty");
  const children = li.querySelector(":scope > .outline-list");
  if (!twisty || !children) return;
  twisty.setAttribute("aria-expanded", String(expanded));
  children.hidden = !expanded;
  markCurrentEntry();
  if (outlinePickerTab === OUTLINE_TAB) numberOutlinePicker();
}

// The section holding a page: the last entry, in document order, that starts
// on or before it. With no page given, the page being read.
function currentOutlineItem(page = outlineCurrentPage) {
  let current = null;
  for (const item of outlineFlat) {
    if (item.entry.page_index !== null && item.entry.page_index <= page) current = item;
  }
  return current;
}

// An item as it shows: itself, or, inside a collapsed section, the outermost
// collapsed section holding it.
function shownOutlineItem(item) {
  if (!item) return null;
  const collapsed = item.parents.find((li) => !isOutlineExpanded(li));
  return collapsed ? outlineFlat.find((f) => f.li === collapsed) || item : item;
}

function isOutlineItemShown(item) {
  return item.parents.every(isOutlineExpanded);
}

// Marks the current section, or, when it is inside a collapsed one, the
// outermost collapsed section holding it, so the mark is always on screen.
function markCurrentEntry() {
  const item = shownOutlineItem(currentOutlineItem());
  for (const f of outlineFlat) {
    if (f === item) f.button.setAttribute("aria-current", "location");
    else f.button.removeAttribute("aria-current");
  }
  return item;
}

function revealCurrentEntry() {
  const item = markCurrentEntry();
  if (item && isOutlinePanelOpen() && outlineActiveTab === OUTLINE_TAB) {
    item.button.scrollIntoView({ block: "nearest" });
  }
}

// --- Pages ----------------------------------------------------------------------- //

function buildPagesList() {
  if (pagesBuilt) return;
  pagesBuilt = true;
  const fragment = document.createDocumentFragment();
  pageThumbEls = [];
  for (let i = 0; i < outlinePageCount; i++) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "page-thumb";
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-label", `Page ${i + 1}`);
    button.dataset.pageIndex = String(i);
    const img = document.createElement("img");
    img.className = "page-thumb-image";
    img.alt = "";
    img.draggable = false;
    // The page's own shape before its image exists, so the list never jumps
    // as thumbnails arrive.
    const size = outlinePageSizes[i];
    img.style.aspectRatio = size && size.width ? `${size.width} / ${size.height}` : `1 / ${PAGE_THUMB_FALLBACK_RATIO}`;
    const label = document.createElement("span");
    label.className = "page-thumb-label";
    label.textContent = String(i + 1);
    button.append(img, label);
    button.addEventListener("click", () => {
      if (outlineHooks) outlineHooks.jumpTo(i);
    });
    pageThumbEls.push(button);
    fragment.append(button);
  }
  pagesList.replaceChildren(fragment);
  markPageThumbs();
  pageThumbObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      pageThumbObserver.unobserve(entry.target);
      pageThumbQueue.push(entry.target);
    }
    pumpPageThumbs();
  }, { root: pagesTabPanel, rootMargin: PAGE_THUMB_MARGIN });
  pageThumbEls.forEach((el) => pageThumbObserver.observe(el));
}

function pumpPageThumbs() {
  while (pageThumbsRendering < PAGE_THUMB_CONCURRENCY && pageThumbQueue.length) {
    const el = pageThumbQueue.shift();
    pageThumbsRendering += 1;
    renderPageThumb(el).finally(() => {
      pageThumbsRendering -= 1;
      pumpPageThumbs();
    });
  }
}

// A thumbnail's bitmap width: its display size at the screen's pixel ratio.
function pageThumbPixelWidth() {
  return Math.round(PAGE_THUMB_MAX_CSS_PX * (window.devicePixelRatio || 1));
}

async function renderPageThumb(el) {
  const index = Number(el.dataset.pageIndex);
  const width = pageThumbPixelWidth();
  try {
    const thumb = await callApi("get_page_thumbnail", index, width);
    if (!thumb.image) return;
    const img = el.querySelector(".page-thumb-image");
    img.src = `data:image/png;base64,${thumb.image}`;
    el.dataset.renderedWidth = String(width);
  } catch (err) {
    // Left as its blank placeholder; scrolling it out and back retries.
    console.error("get_page_thumbnail failed:", err);
    pageThumbObserver.observe(el);
  }
}

// After a highlight change on a page: its thumbnail shows highlights, so it is
// rendered again the next time it is in view.
function forgetPageThumb(index) {
  const el = pageThumbEls[index];
  if (!el || !pageThumbObserver) return;
  pageThumbObserver.unobserve(el);
  pageThumbObserver.observe(el);
}

function markPageThumbs() {
  pageThumbEls.forEach((el, i) => {
    if (i === outlineCurrentPage) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
    el.classList.toggle("has-highlights", outlineHighlighted.has(i));
  });
}

function revealCurrentThumb() {
  const el = pageThumbEls[outlineCurrentPage];
  if (el && isOutlinePanelOpen() && outlineActiveTab === PAGES_TAB) el.scrollIntoView({ block: "nearest" });
}

// --- Width --------------------------------------------------------------------------- //

function setOutlineWidth(width) {
  outlineWidth = Math.round(Math.min(outlineWidthLimits.max, Math.max(outlineWidthLimits.min, width)));
  outlinePanel.style.setProperty("--outline-width", `${outlineWidth}px`);
  outlineResizer.setAttribute("aria-valuenow", String(outlineWidth));
}

// After a resize: remembered. Thumbnails keep their size, so nothing is
// rendered again.
function finishOutlineResize() {
  callApi("set_outline_width", outlineWidth).catch((err) => console.error("set_outline_width failed:", err));
}

// Thumbnails drawn for another pixel ratio (the window moved to another
// screen) are drawn again as they next come into view.
function redrawPageThumbsForPixelRatio() {
  if (!pageThumbObserver) return;
  const width = String(pageThumbPixelWidth());
  for (const el of pageThumbEls) {
    if (el.dataset.renderedWidth && el.dataset.renderedWidth !== width) forgetPageThumb(Number(el.dataset.pageIndex));
  }
}

outlineResizer.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  ev.preventDefault();
  outlineResizer.setPointerCapture(ev.pointerId);
  outlinePanel.classList.add("is-resizing");
  const startX = ev.clientX;
  const startWidth = outlineWidth;
  const move = (e) => setOutlineWidth(startWidth + e.clientX - startX);
  const end = () => {
    outlineResizer.removeEventListener("pointermove", move);
    outlineResizer.removeEventListener("pointerup", end);
    outlineResizer.removeEventListener("pointercancel", end);
    outlinePanel.classList.remove("is-resizing");
    finishOutlineResize();
  };
  outlineResizer.addEventListener("pointermove", move);
  outlineResizer.addEventListener("pointerup", end);
  outlineResizer.addEventListener("pointercancel", end);
});

// The keyboard's way to the same: arrow keys by a step, Home / End to the
// limits. Stopped here so the reader's own arrow keys do not turn a page too.
outlineResizer.addEventListener("keydown", (ev) => {
  const targets = {
    ArrowLeft: outlineWidth - OUTLINE_RESIZE_STEP_PX,
    ArrowRight: outlineWidth + OUTLINE_RESIZE_STEP_PX,
    Home: outlineWidthLimits.min,
    End: outlineWidthLimits.max,
  };
  if (!(ev.key in targets)) return;
  ev.preventDefault();
  ev.stopPropagation();
  setOutlineWidth(targets[ev.key]);
  finishOutlineResize();
});

// --- The progress bar pointing here --------------------------------------------- //

// Marks page `index` as the progress bar's pointer is over it (null: nothing):
// its thumbnail takes the hover treatment on Pages, the entry holding it the
// row tint on Outline, and is brought into view if it is out of it.
function previewOutlinePage(index) {
  if (outlinePreviewed) outlinePreviewed.classList.remove("is-previewed");
  outlinePreviewed = null;
  if (index === null || index === undefined || !isOutlinePanelOpen()) return;
  const pages = outlineActiveTab === PAGES_TAB;
  const el = pages ? pageThumbEls[index] : (shownOutlineItem(currentOutlineItem(index)) || {}).row;
  if (!el) return;
  el.classList.add("is-previewed");
  outlinePreviewed = el;
  revealInPanel(el, pages ? pagesTabPanel : outlineTabPanel);
}

// Scrolls `el` just into `scroller`'s view, smoothly (scrollElementTo, which
// skips the animation under reduced motion), and only when it is not already
// wholly in view.
function revealInPanel(el, scroller) {
  const view = scroller.getBoundingClientRect();
  const box = el.getBoundingClientRect();
  if (box.top >= view.top && box.bottom <= view.bottom) return;
  const above = box.top < view.top;
  const delta = above ? box.top - view.top - OUTLINE_REVEAL_MARGIN_PX
                      : box.bottom - view.bottom + OUTLINE_REVEAL_MARGIN_PX;
  scrollElementTo(scroller, scroller.scrollTop + delta);
}

// --- Keeping up with the reader --------------------------------------------------- //

function updateOutlinePanel(state) {
  if (!state || !state.is_open) return;
  const pageChanged = state.page_index !== outlineCurrentPage;
  outlineCurrentPage = state.page_index;
  outlineHighlighted = new Set(state.highlighted_pages || []);
  markPageThumbs();
  if (!pageChanged) return;
  revealCurrentEntry();
  revealCurrentThumb();
}

// --- Numbered-overlay picker -------------------------------------------------------- //

function clearOutlineBadges() {
  outlinePanel.classList.remove("is-picking");
  outlinePanel.querySelectorAll(".outline-badge").forEach((badge) => badge.remove());
}

function addOutlineBadge(host, number) {
  const badge = document.createElement("span");
  badge.className = "outline-badge";
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = String(number);
  host.append(badge);
}

// Outline entries are numbered in the order shown (the rows of a collapsed
// section are not shown, so not numbered), and only ones with a page.
function numberOutlinePicker() {
  clearOutlineBadges();
  const shown = outlineFlat.filter((item) => item.entry.page_index !== null && isOutlineItemShown(item));
  outlinePickerTargets = shown.map((item) => item.entry.page_index);
  shown.forEach((item, i) => addOutlineBadge(item.row, i + 1));
  outlinePanel.classList.add("is-picking");
}

// Thumbnails carry their page number, so "twelve" goes to page 12.
function numberPagesPicker() {
  clearOutlineBadges();
  pageThumbEls.forEach((el, i) => addOutlineBadge(el, i + 1));
}

function startOutlinePicker(tab) {
  const wanted = tab === OUTLINE_TAB && !hasOutline() ? PAGES_TAB : tab;
  openOutlinePanel(wanted);
  outlinePickerTab = wanted;
  if (wanted === OUTLINE_TAB) numberOutlinePicker();
  else numberPagesPicker();
  outlinePickerScope.enter();
  if (!outlineHooks) return;
  if (wanted !== tab) outlineHooks.say("This PDF has no outline. Say a page number, or “cancel”");
  else if (wanted === PAGES_TAB) outlineHooks.say("Say a page number, or “cancel”");
  else outlineHooks.say("Say a number to jump there, or “cancel”");
}

function endOutlinePicker() {
  if (outlinePickerTab === null) return;
  outlinePickerTab = null;
  outlinePickerTargets = [];
  clearOutlineBadges();
  outlinePickerScope.leave();
}

function isOutlinePickerActive() {
  return outlinePickerTab !== null;
}

// PICK and CANCEL while the picker is up. True when the command was the
// picker's to handle.
function handleOutlinePickerCommand(command) {
  if (!isOutlinePickerActive()) return false;
  if (command.intent === "CANCEL") {
    endOutlinePicker();
    return true;
  }
  if (command.intent !== "PICK") return false;
  const number = command.index;
  let page = null;
  if (outlinePickerTab === PAGES_TAB) {
    if (number >= 1 && number <= outlinePageCount) page = number - 1;
  } else if (number >= 1 && number <= outlinePickerTargets.length) {
    page = outlinePickerTargets[number - 1];
  }
  if (page === null) {
    if (outlineHooks) outlineHooks.say(`There's no number ${number} here`);
    return true;
  }
  endOutlinePicker();
  if (outlineHooks) outlineHooks.jumpTo(page);
  return true;
}
