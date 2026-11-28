// The reader's top bar and footer: what they show for the current state, page
// turns (the buttons, the page box, keys, voice and the outline all go through
// these), and the theme button.

const backBtn = document.getElementById("backBtn");
const docTitle = document.getElementById("docTitle");
const unsavedDot = document.getElementById("unsavedDot");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const pageInput = document.getElementById("pageInput");
const pageSuffix = document.getElementById("pageSuffix");

const highlightBtn = document.getElementById("highlightBtn");
const undoBtn = document.getElementById("undoBtn");
const redoBtn = document.getElementById("redoBtn");
const zoomInBtn = document.getElementById("zoomInBtn");
const zoomOutBtn = document.getElementById("zoomOutBtn");
const layoutBtn = document.getElementById("layoutBtn");
const themeBtn = document.getElementById("themeBtn");
const referenceFooterBtn = document.getElementById("referenceFooterBtn");

const THEME_ORDER = ["light", "dark", "sepia"];

function updateChrome() {
  docTitle.textContent = state.title || "";
  updateUnsavedDot(Boolean(state.is_dirty));
  pageInput.maxLength = String(state.page_count).length;
  // Never under the reader's fingers: the strip's scroll tracking calls this
  // on every page boundary, and would otherwise overwrite a half-typed number.
  if (document.activeElement !== pageInput) pageInput.value = state.page_index + 1;
  pageSuffix.textContent = `of ${state.page_count}`;
  updateZoomControl(state);
  prevBtn.disabled = state.page_index <= 0;
  nextBtn.disabled = state.page_index >= state.page_count - 1;
  updateUndoButtons();
  showActiveHighlightColor(state.highlight_color);
  // The progress bar and the outline panel follow every page and highlight
  // change from here, strip scrolling included.
  updateProgressBar(state);
  updateProgressScroll(currentPageReadFraction());
  updateOutlinePanel(state);
}

// The one unsaved flag (state.is_dirty, Python's), shown as a dot after the
// name; Python gives the window's title its "*" from the same flag. Hidden,
// not removed, so the name never moves; the tooltip only while it shows.
function updateUnsavedDot(dirty) {
  unsavedDot.classList.toggle("is-shown", dirty);
  unsavedDot.setAttribute("aria-hidden", String(!dirty));
  if (dirty) unsavedDot.dataset.tooltip = "Unsaved changes";
  else delete unsavedDot.dataset.tooltip;
}

stateAdoptedListeners.push((next) => updateUnsavedDot(Boolean(next.is_open && next.is_dirty)));

// Undo and redo are always in the toolbar. What dims one is the undo stack
// having nothing for it (state.can_undo / can_redo), not unsaved changes:
// after a save there may still be edits to take back. aria-disabled rather
// than disabled, so the button keeps its tooltip saying why.
// Named after what they would take back: a highlight, or a set of page
// changes applied with Done in Organise pages (one step either way).
function updateUndoButtons() {
  const undoWhat = state.undo_kind === "page_changes" ? "page changes" : "highlight";
  const redoWhat = state.redo_kind === "page_changes" ? "page changes" : "highlight";
  undoBtn.setAttribute("aria-label", `Undo ${undoWhat}`);
  redoBtn.setAttribute("aria-label", `Redo ${redoWhat}`);
  setUndoButton(undoBtn, state.can_undo, `Undo ${undoWhat}  (Ctrl+Z)`, "Nothing to undo");
  setUndoButton(redoBtn, state.can_redo, `Redo ${redoWhat}  (Ctrl+Y)`, "Nothing to redo");
}

function setUndoButton(button, enabled, title, emptyTitle) {
  button.setAttribute("aria-disabled", String(!enabled));
  button.title = enabled ? title : emptyTitle;
}

// The footer's count: this page's in Book, the document's in Strip, where
// there is no one page. Drawings are highlights here too.
function renderHighlightCount() {
  const [count, where] = layoutMode === "strip"
    ? [state.highlight_count_total, "in this document"]
    : [state.highlight_count_page, "on this page"];
  highlightCountLbl.textContent = `${count} ${count === 1 ? "highlight" : "highlights"} ${where}`;
}

// Every page change the reader asks for (keyboard, voice, the toolbar, an
// outline entry) goes through one of these, and widens the progress bar for a
// moment to show where it landed. Scrolling the strip does not.
async function goNext() {
  const before = state.page_index;
  adoptState(await callApi("next_page"));
  await refresh({ animate: true });
  if (state.page_index !== before) flashProgressBar();
}

async function goPrev() {
  const before = state.page_index;
  adoptState(await callApi("prev_page"));
  await refresh({ animate: true });
  if (state.page_index !== before) flashProgressBar();
}

async function goToPage(n) {
  const before = state.page_index;
  adoptState(await callApi("goto_page", n - 1));
  await refresh({ animate: true });
  if (state.page_index !== before) flashProgressBar();
}

// The progress bar's click and drag: lands at once (a drag asks for many pages
// in a row), and the bar is already open, so there is nothing to flash.
async function jumpToPageIndex(index) {
  adoptState(await callApi("goto_page", index));
  await refresh();
}

// The page box: focusing it selects the number, so typing replaces it; Enter
// jumps, Esc (or leaving the box) puts back the page being read. A number past
// either end goes to the first or last page, and anything that isn't a number
// is dropped as it is typed, so there is never an error to show.
function revertPageInput() {
  pageInput.value = state.page_index + 1;
}

function commitPageInput() {
  const typed = Number.parseInt(pageInput.value, 10);
  if (!Number.isFinite(typed) || !state.page_count) {
    revertPageInput();
    return;
  }
  const page = Math.min(Math.max(typed, 1), state.page_count);
  pageInput.value = page;
  if (page - 1 !== state.page_index) goToPage(page);
}

async function cycleTheme() {
  const current = currentTheme();
  const next = THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  applyTheme(next);
  await callApi("set_theme", next);
}
