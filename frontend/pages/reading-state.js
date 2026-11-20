// The reader's state: one owner, this file (docs/ARCHITECTURE.md § "Reader
// page layout"). Every other script reads `state` and changes it only
// through adoptState() or trackStripPage() below.

let state = { is_open: false };

// `state` has one owner, this file, and one way in. Every bridge call that
// answers with the reader's whole state (api._state(): a page turn, a zoom, a
// highlight, the active colour, a save, Organise's Done…) hands its reply
// here, from whichever file made the call, so nothing is drawn from an old
// copy: updateChrome() redraws the colour pill from `state` on every page
// boundary, and once put a colour chosen in the card back to the one before.
//
// `keepPage` is for a call that never moves the page (a zoom, the colour, the
// layout, the strip's own page report). Scrolling the strip tells the backend
// its page without waiting (onStripScroll), so such a reply can still carry
// the page from before the scroll; the page tracked here is never older.
// Anything that isn't a state (a failed call's error, null) is ignored.
function adoptState(next, { keepPage = false } = {}) {
  if (!next || typeof next !== "object" || typeof next.is_open !== "boolean") return state;
  state = keepPage && state.is_open && next.is_open ? { ...next, page_index: state.page_index } : next;
  return state;
}

// The strip's scroll moving the tracked page on ahead of the backend
// (onStripScroll, reading-layout.js): a new object, never an edit of an
// adopted one.
function trackStripPage(index) {
  state = { ...state, page_index: index };
}
