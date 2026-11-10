// Loaded in the reader's <head>, before anything is drawn. A page change
// (Done in Organise pages, Undo or Redo page changes) reloads the reader once
// it has been applied; this keeps "Applying page changes…" on screen from the
// very first frame of the reloaded page until it has drawn the document again
// (pages/page-change-busy.js takes it down), so the reader never sees the old
// layout flash in between.
//
// The note is taken out of sessionStorage at once and kept on the window for
// this load only, so it can never outlive the load it was written for (a
// later, unrelated reader page showing the card or a stale toast).
"use strict";

window.lectorPageChangeNote = null;
{
  const raw = sessionStorage.getItem("lector-page-change");
  if (raw !== null) {
    sessionStorage.removeItem("lector-page-change");
    try {
      window.lectorPageChangeNote = JSON.parse(raw) || {};
    } catch {
      window.lectorPageChangeNote = {};
    }
    document.documentElement.classList.add("page-change-pending");
  }
}
