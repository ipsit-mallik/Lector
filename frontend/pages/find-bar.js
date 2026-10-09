// Find in document: the compact bar over the page area's top-right corner,
// opened with Ctrl+F or the rail's magnifier.
//
// The search itself is PyMuPDF's, in Python (api.py's find_in_document,
// features/reading/search.py). It runs in short time-boxed slices, each
// reporting the pages it got through, so a long document shows its first
// matches straight away and never holds up the bridge. A new query bumps
// `findGeneration`; a slice that comes back for an older generation is
// dropped, and its search asks for nothing further — that is the whole of
// cancelling, and it is also what makes out-of-order replies harmless.
//
// The search starts at the page being read and wraps round, so the first
// match found is the next one from where the reader is, and it is the one the
// view moves to.
//
// This file knows nothing about the two layouts. reading.js tells it, through
// configureFindBar(), which element is showing a given page, how to bring a
// page on screen, and which element scrolls.
//
// Keyboard and mouse only: voice search is a Milestone 8 follow-up
// (docs/TASKS.md).

const findBar = document.getElementById("findBar");
const findInput = document.getElementById("findInput");
const findStatus = document.getElementById("findStatus");
const findPrevBtn = document.getElementById("findPrevBtn");
const findNextBtn = document.getElementById("findNextBtn");
const findCloseBtn = document.getElementById("findCloseBtn");
const findBtn = document.getElementById("findBtn");
const findLayer = document.getElementById("findLayer");

// Typing pauses this long before a search starts, so a word typed quickly is
// one search rather than one per letter.
const FIND_DEBOUNCE_MS = 250;
// A match within this distance of the scroller's edge is moved into view too:
// the find bar itself covers the top of the page area.
const FIND_REVEAL_MARGIN_PX = 64;
// Where a match that has to be scrolled to lands: a third of the way down,
// clear of the bar, with the lines before it still in view for context.
const FIND_REVEAL_AT = 1 / 3;

let findHooks = null;
let findOpen = false;
let findGeneration = 0;
let findTimer = null;
let findQuery = "";
let findMatches = []; // {page_index, width, height, rects}, in document order
let findCurrent = null; // one of findMatches
let findSearching = false;
let findTextPages = 0;
let findFailed = false;
let findPaintQueued = false;

// hooks: {
//   pageElement(index)  the element showing that page now, or null,
//   showPage(index)     resolves once that page is on screen,
//   scroller()          the element that scrolls the pages,
//   currentPage()       the page being read (0-based),
//   returnFocus()       puts focus back on the reader,
// }
function configureFindBar(hooks) {
  findHooks = hooks;
}

function isFindBarOpen() {
  return findOpen;
}

function openFindBar() {
  if (!findOpen) {
    findOpen = true;
    findBar.classList.add("is-open");
    findBtn.classList.add("active");
    findBtn.setAttribute("aria-expanded", "true");
    // Reopening keeps the last query, as a browser's find bar does, so
    // looking for the same word again is Ctrl+F, Enter.
    const query = findInput.value.trim();
    if (query) runFindSearch(query);
  }
  findInput.focus();
  findInput.select();
}

function closeFindBar() {
  if (!findOpen) return;
  findOpen = false;
  clearTimeout(findTimer);
  findTimer = null;
  findGeneration++; // whatever is in flight is now stale
  findSearching = false;
  findMatches = [];
  findCurrent = null;
  findQuery = "";
  findBar.classList.remove("is-open");
  findBtn.classList.remove("active");
  findBtn.setAttribute("aria-expanded", "false");
  findLayer.replaceChildren();
  renderFindStatus();
  findHooks.returnFocus();
}

// --- Searching ---------------------------------------------------------------

function scheduleFindSearch() {
  clearTimeout(findTimer);
  const query = findInput.value.trim();
  if (!query) {
    // Clearing the field is immediate: there is nothing to wait for.
    findTimer = null;
    runFindSearch("");
    return;
  }
  findTimer = setTimeout(() => {
    findTimer = null;
    runFindSearch(query);
  }, FIND_DEBOUNCE_MS);
}

async function runFindSearch(query) {
  const generation = ++findGeneration;
  findQuery = query;
  findMatches = [];
  findCurrent = null;
  findTextPages = 0;
  findFailed = false;
  findSearching = Boolean(query);
  renderFindStatus();
  paintFindMarks();
  if (!query) return;

  // From the page being read to the end, then from the start back round to it.
  const origin = findHooks.currentPage();
  const legs = [[origin, null], [0, origin]];
  for (const [from, to] of legs) {
    if (to !== null && from >= to) continue;
    let start = from;
    while (start !== null) {
      let slice;
      try {
        slice = await callApi("find_in_document", query, start, to);
      } catch (err) {
        if (generation !== findGeneration) return;
        console.error("find_in_document failed:", err);
        findFailed = true;
        findSearching = false;
        renderFindStatus();
        return;
      }
      if (generation !== findGeneration) return;
      findTextPages += slice.text_pages;
      addFindResults(slice.pages);
      start = slice.next_page;
    }
  }
  findSearching = false;
  renderFindStatus();
}

function addFindResults(pages) {
  if (!pages.length) return;
  for (const page of pages) {
    for (const rects of page.hits) {
      findMatches.push({ page_index: page.page_index, width: page.width, height: page.height, rects });
    }
  }
  // The wrapped second leg finds pages that come before the first leg's; a
  // stable sort keeps each page's own hits in reading order.
  findMatches.sort((a, b) => a.page_index - b.page_index);
  const first = !findCurrent;
  if (first) findCurrent = findMatches.find((m) => m.page_index >= findHooks.currentPage()) || findMatches[0];
  renderFindStatus();
  paintFindMarks();
  if (first) revealFindMatch(findCurrent);
}

// --- Moving between matches ---------------------------------------------------

function stepFindMatch(direction) {
  // Enter pressed before the pause that would have started a search: search
  // now, and land on its first match, rather than step through stale ones.
  const query = findInput.value.trim();
  if (findTimer || query !== findQuery) {
    clearTimeout(findTimer);
    findTimer = null;
    runFindSearch(query);
    return;
  }
  if (!findMatches.length) return;
  const count = findMatches.length;
  const index = findMatches.indexOf(findCurrent);
  findCurrent = findMatches[(index + direction + count) % count];
  renderFindStatus();
  paintFindMarks();
  revealFindMatch(findCurrent);
}

function matchBounds(match) {
  return match.rects.reduce(
    (b, [x0, y0, x1, y1]) => ({
      x0: Math.min(b.x0, x0), y0: Math.min(b.y0, y0),
      x1: Math.max(b.x1, x1), y1: Math.max(b.y1, y1),
    }),
    { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity },
  );
}

async function revealFindMatch(match) {
  await findHooks.showPage(match.page_index);
  // The reader may have stepped on (or closed the bar) while the page loaded.
  if (match !== findCurrent) return;
  const el = findHooks.pageElement(match.page_index);
  if (!el) return;
  const scroller = findHooks.scroller();
  const pageRect = el.getBoundingClientRect();
  const view = scroller.getBoundingClientRect();
  const scale = pageRect.width / match.width;
  const box = matchBounds(match);
  const top = pageRect.top + box.y0 * scale;
  const bottom = pageRect.top + box.y1 * scale;
  if (top < view.top + FIND_REVEAL_MARGIN_PX || bottom > view.bottom - FIND_REVEAL_MARGIN_PX) {
    const target = scroller.scrollTop + (top - view.top) - scroller.clientHeight * FIND_REVEAL_AT;
    scrollElementTo(scroller, target, { animate: true });
  }
  // Only matters zoomed in past the window's width; no animation, since the
  // vertical move above already carries the eye.
  const left = pageRect.left + box.x0 * scale;
  const right = pageRect.left + box.x1 * scale;
  if (left < view.left || right > view.right) {
    scroller.scrollLeft += left - view.left - scroller.clientWidth / 2 + (right - left) / 2;
  }
  paintFindMarks();
}

// --- Drawing ------------------------------------------------------------------

// The marks are drawn over the page area from each page's measured position,
// like the selection wash, so they follow both layouts, zoom previews and
// scrolling without living inside either layout's DOM. Batched to one paint
// per frame: scrolling fires far more often than that.
function paintFindMarks() {
  if (findPaintQueued) return;
  findPaintQueued = true;
  requestAnimationFrame(() => {
    findPaintQueued = false;
    drawFindMarks();
  });
}

function drawFindMarks() {
  if (!findOpen || !findMatches.length) {
    findLayer.replaceChildren();
    return;
  }
  const area = findLayer.getBoundingClientRect();
  const fragment = document.createDocumentFragment();
  let pageIndex = -1;
  let pageRect = null;
  let scale = 0;
  for (const match of findMatches) {
    if (match.page_index !== pageIndex) {
      pageIndex = match.page_index;
      const el = findHooks.pageElement(pageIndex);
      pageRect = el ? el.getBoundingClientRect() : null;
      // Matches are in page order and pages stack downwards, so once one
      // starts below the view, so does every match after it.
      if (pageRect && pageRect.top >= area.bottom) break;
      const onScreen = pageRect && pageRect.bottom > area.top;
      scale = onScreen ? pageRect.width / match.width : 0;
    }
    if (!scale) continue;
    for (const [x0, y0, x1, y1] of match.rects) {
      const mark = document.createElement("div");
      mark.className = match === findCurrent ? "find-mark is-current" : "find-mark";
      mark.style.left = `${pageRect.left - area.left + x0 * scale}px`;
      mark.style.top = `${pageRect.top - area.top + y0 * scale}px`;
      mark.style.width = `${(x1 - x0) * scale}px`;
      mark.style.height = `${(y1 - y0) * scale}px`;
      fragment.appendChild(mark);
    }
  }
  findLayer.replaceChildren(fragment);
}

function renderFindStatus() {
  const count = findMatches.length;
  let text = "";
  if (findFailed) text = "Couldn't search";
  else if (!findQuery) text = "";
  else if (count) text = `${findMatches.indexOf(findCurrent) + 1} of ${count}${findSearching ? "…" : ""}`;
  else if (findSearching) text = "Searching…";
  // A PDF that is only page images has nothing to find, and "0 results" would
  // wrongly suggest the word just isn't in it.
  else if (!findTextPages) text = "No searchable text in this PDF";
  else text = "No results";
  findStatus.textContent = text;
  findBar.classList.toggle("is-searching", findSearching);
  findPrevBtn.disabled = !count;
  findNextBtn.disabled = !count;
}

// --- Wiring -------------------------------------------------------------------

findInput.addEventListener("input", scheduleFindSearch);
findPrevBtn.addEventListener("click", () => stepFindMatch(-1));
findNextBtn.addEventListener("click", () => stepFindMatch(1));
findCloseBtn.addEventListener("click", closeFindBar);
findBtn.addEventListener("click", openFindBar);

findBar.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    ev.preventDefault();
    ev.stopPropagation();
    closeFindBar();
  } else if (ev.key === "Enter" && ev.target === findInput) {
    ev.preventDefault();
    stepFindMatch(ev.shiftKey ? -1 : 1);
  }
});

// Ctrl+F from anywhere in the reader, including while typing in the page box.
// Not over a dialog: the "What can I say?" list takes Ctrl+F for its own search
// while it is open, and stops it reaching here.
document.addEventListener("keydown", (ev) => {
  if (!(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.key.toLowerCase() !== "f") return;
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  ev.preventDefault();
  openFindBar();
});

window.addEventListener("resize", paintFindMarks);
