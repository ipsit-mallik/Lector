// Window-wide drag-and-drop of a PDF (Milestone 8.12), shared by every page.
//
// Two halves, and they live on opposite sides of the bridge on purpose:
//
//   * Python (Api.bind_file_drop) hears the actual drop. It has to: the page's
//     JavaScript is only ever told a dropped file's *name*, never where it is
//     on disk, while pywebview gives Python the real path. Python checks it is
//     a PDF and sends the outcome here as a `lector:filedrop` event.
//   * This file does what only the page can: tell the WebView a drop is
//     allowed here at all (without that, the browser navigates to the dropped
//     file and replaces the whole app with a bare PDF viewer), and show the
//     reader that the window is a drop target while a file is over it.
//
// Pages say what a drop *means* on their screen with `onFileDrop(handler)` —
// Home opens the file, Reading asks about unsaved highlights first — since
// this file can't know that.

const FILE_DRAG_CLASS = "is-file-dragging";

let fileDropHandler = null;
// dragenter/dragleave fire for every element the pointer crosses, so a plain
// on/off flag flickers; counting enters against leaves is the standard fix.
let fileDragDepth = 0;

function dragCarriesFiles(ev) {
  return Array.from((ev.dataTransfer && ev.dataTransfer.types) || []).includes("Files");
}

function setFileDragging(active) {
  document.body.classList.toggle(FILE_DRAG_CLASS, active);
}

function resetFileDrag() {
  fileDragDepth = 0;
  setFileDragging(false);
}

// Registers what a successfully dropped PDF should do on this page. One
// handler per page: a page has one meaning for "open this file".
function onFileDrop(handler) {
  fileDropHandler = handler;
}

// Capture phase on window, so this runs ahead of anything on the page and of
// pywebview's own document-level drop listener.
window.addEventListener(
  "dragenter",
  (ev) => {
    if (!dragCarriesFiles(ev)) return;
    ev.preventDefault();
    fileDragDepth += 1;
    setFileDragging(true);
  },
  true,
);

window.addEventListener(
  "dragover",
  (ev) => {
    if (!dragCarriesFiles(ev)) return;
    // Cancelling dragover is what marks the page as a valid drop target.
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
  },
  true,
);

window.addEventListener(
  "dragleave",
  (ev) => {
    if (!dragCarriesFiles(ev)) return;
    fileDragDepth = Math.max(0, fileDragDepth - 1);
    if (fileDragDepth === 0) setFileDragging(false);
  },
  true,
);

window.addEventListener(
  "drop",
  (ev) => {
    if (!dragCarriesFiles(ev)) return;
    ev.preventDefault();
    resetFileDrag();
  },
  true,
);

// A drag cancelled with Esc, or dropped outside the window, ends without a drop.
window.addEventListener("dragend", resetFileDrag, true);

// The outcome Python reports for a drop: `{path}` for a PDF to open, or
// `{error}` with a reader-facing reason it can't be.
window.addEventListener("lector:filedrop", (ev) => {
  const { path, error } = ev.detail || {};
  resetFileDrag();
  if (error) {
    showToast(error);
    return;
  }
  if (path && fileDropHandler) fileDropHandler(path);
});

// The overlay is built here rather than in each page's HTML so every screen
// gets the same cue from one place. It never takes pointer events: a drop is
// received by the window, not by this.
(function mountDropOverlay() {
  const overlay = document.createElement("div");
  overlay.className = "drop-overlay";
  overlay.setAttribute("aria-hidden", "true");
  const label = document.createElement("div");
  label.className = "drop-overlay-label";
  label.textContent = "Drop a PDF to open it";
  overlay.appendChild(label);
  document.body.appendChild(overlay);
})();
