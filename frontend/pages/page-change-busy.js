// "Applying page changes…": what Done in Organise pages, Undo page changes and
// Redo page changes show while they run (docs/ARCHITECTURE.md § "Page changes
// (Milestone F2)"). The card is up within a frame of the click, blocks the
// mouse and keyboard (and voice: reading.js checks `isPageChangeBusy`), and
// stays up until the reader has drawn the document again.
//
// Every per-page cache in the reader (page images, the strip, characters and
// highlight targets, the outline and progress bar thumbnails, find results,
// OCR waits) is keyed by page index, so a real change reloads the reader page,
// as opening another PDF does: the page reads the open document on load. A
// note in sessionStorage carries the busy card (js/page-change-pending.js) and
// what to say (the unsaved changes, older changes dropped) across the reload.
"use strict";

const PAGE_CHANGE_NOTE = "lector-page-change";
const PAGE_CHANGES_UNSAVED = "Page changes are unsaved. Ctrl+S saves them with your highlights.";
const OLDER_CHANGES_DROPPED = "Older changes can no longer be undone.";

const pageChangeBusy = document.getElementById("pageChangeBusy");
let pageChangeRunning = false;

function isPageChangeBusy() {
  return pageChangeRunning || document.documentElement.classList.contains("page-change-pending");
}

function showPageChangeBusy() {
  pageChangeBusy.hidden = false;
  document.body.setAttribute("aria-busy", "true");
  // Whatever had focus behind the card would otherwise still take keys.
  pageChangeBusy.focus({ preventScroll: true });
}

function hidePageChangeBusy() {
  document.documentElement.classList.remove("page-change-pending");
  pageChangeBusy.hidden = true;
  document.body.removeAttribute("aria-busy");
}

// Runs `apply` (a bridge call answering the reader's state with
// `pages_changed` and `history_trimmed`) under the busy card. When pages did
// change, the page reloads and the note says what the reloaded page should
// tell the reader (`unsavedNote`: the "Page changes are unsaved" toast);
// unless `reload` is false, for a page about to be left, when the caller
// carries on with the state. Otherwise the card comes down and the state is
// handed back.
async function runPageChange(apply, { reload = true, unsavedNote = false } = {}) {
  if (pageChangeRunning) return null;
  pageChangeRunning = true;
  showPageChangeBusy();
  let state;
  try {
    state = await apply();
  } catch (err) {
    pageChangeRunning = false;
    hidePageChangeBusy();
    throw err;
  }
  if (state && state.pages_changed && reload) {
    sessionStorage.setItem(PAGE_CHANGE_NOTE, JSON.stringify({
      unsaved: unsavedNote,
      trimmed: Boolean(state.history_trimmed),
    }));
    window.location.reload();
    return state; // the card stays up until the page has gone
  }
  pageChangeRunning = false;
  hidePageChangeBusy();
  if (state && state.history_trimmed) showToast(OLDER_CHANGES_DROPPED);
  return state;
}

// The reloaded reader, once it has drawn the document (or failed to: the
// reader calls this whatever happened, so the card can never stay up): take
// the card down after the frame with the new pages has been painted, and say
// what the change left behind. Resolves once the card is down. The note was
// taken out of sessionStorage by js/page-change-pending.js.
function finishPageChangeAfterReload() {
  const note = window.lectorPageChangeNote;
  window.lectorPageChangeNote = null;
  if (!note) return Promise.resolve();
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      hidePageChangeBusy();
      if (note.unsaved) showToast(PAGE_CHANGES_UNSAVED);
      if (note.trimmed) showToast(OLDER_CHANGES_DROPPED);
      resolve();
    }));
  });
}

// Nothing behind the card takes a key while it is up.
document.addEventListener("keydown", (ev) => {
  if (!isPageChangeBusy()) return;
  ev.preventDefault();
  ev.stopImmediatePropagation();
}, true);
