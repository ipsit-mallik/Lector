// The reader's More menu (⋯, at the right end of the header) and the footer's
// file-job pill (docs/PRD.md § "Post-v1: file tools").
//
// The menu is the file's: Save, Save a copy…, the Tier 1 tools, Show in
// folder. It is the shared popup menu (js/popup-menu.js), so its keys are
// that one's. Save and Save a copy had only a shortcut and a voice command
// before; this is their mouse route.
//
// The pill stands for a job sent to the background with Keep reading: a
// spinner and "Compressing 34 / 120", left of the highlight count. Clicking
// it brings the tool's dialog back as it is now. It goes when the job ends
// (js/file-jobs.js then says how in a toast).
//
// reading.js hands in what the items do (`configureFileMenu`), the same
// configure…() split as find-bar.js.
"use strict";

const moreBtn = document.getElementById("moreBtn");
const fileJobPill = document.getElementById("fileJobPill");
const fileJobPillLabel = document.getElementById("fileJobPillLabel");

let fileMenuHooks = {
  save: () => {},
  saveCopy: () => {},
  openTool: () => {},
  onClose: () => {},
};

// How many pages have no text: asked once per document, the first time the
// menu opens, and kept (the reader page is loaded afresh for every PDF).
let textlessCount = null;

function configureFileMenu(hooks) {
  fileMenuHooks = { ...fileMenuHooks, ...hooks };
}

function isFileMenuOpen() {
  return isPopupMenuOpen(moreBtn);
}

function fileMenuItem(icon, label, onSelect, { sublabel, shortcut } = {}) {
  return buildPopupMenuItem({ icon, label, sublabel, shortcut, onSelect, className: "file-menu-item" });
}

function textlessLine() {
  if (textlessCount === null) return "Counting pages without text…";
  if (textlessCount === 0) return "All pages have text";
  return `${textlessCount} ${textlessCount === 1 ? "page has" : "pages have"} no text`;
}

async function fetchTextlessCount() {
  try {
    const { textless } = await callApi("get_file_tool_menu");
    textlessCount = textless;
  } catch (err) {
    console.error("file-menu: couldn't count pages without text", err);
    return;
  }
  // Still open: fill in the line that said it was counting.
  const sub = document.querySelector(".file-menu [data-tool='searchable'] .file-menu-item-sub");
  if (sub) sub.textContent = textlessLine();
}

async function showOpenDocumentInFolder() {
  const { error } = await callApi("show_open_document_in_folder");
  if (error) showToast(error);
}

function openFileMenu() {
  const searchable = fileMenuItem("searchable", "Make searchable…", () => fileMenuHooks.openTool("searchable"), {
    sublabel: textlessLine(),
  });
  searchable.dataset.tool = "searchable";
  // 4px below the header itself, not below the button inside it.
  const headerBottom = moreBtn.closest(".topbar").getBoundingClientRect().bottom;
  openPopupMenu({
    trigger: moreBtn,
    className: "file-menu",
    label: "File",
    gap: 4 + Math.max(0, headerBottom - moreBtn.getBoundingClientRect().bottom),
    children: [
      fileMenuItem("save", "Save", () => fileMenuHooks.save(), { shortcut: "Ctrl+S" }),
      fileMenuItem("save_copy", "Save a copy…", () => fileMenuHooks.saveCopy(), { shortcut: "Ctrl+Shift+S" }),
      buildPopupMenuSeparator("file-menu-separator"),
      fileMenuItem("compress", "Compress…", () => fileMenuHooks.openTool("compress")),
      searchable,
      fileMenuItem("export_images", "Export as images…", () => fileMenuHooks.openTool("export")),
      buildPopupMenuSeparator("file-menu-separator"),
      fileMenuItem("folder", "Show in folder", () => showOpenDocumentInFolder()),
    ],
    // The page scrolls under a menu that hangs from the fixed header, so
    // only a resize or a press elsewhere closes it.
    closesOnScroll: () => false,
    onClose: () => {
      moreBtn.classList.remove("active");
      fileMenuHooks.onClose();
    },
  });
  moreBtn.classList.add("active");
  if (textlessCount === null) fetchTextlessCount();
}

moreBtn.addEventListener("click", (ev) => {
  ev.stopPropagation();
  if (isFileMenuOpen()) closePopupMenu();
  else openFileMenu();
});

// ---- The footer's job pill -------------------------------------------------

function showFileJobPill(event) {
  if (!event || event.state !== "working") {
    hideFileJobPill();
    return;
  }
  fileJobPillLabel.textContent = fileJobPillText(event);
  fileJobPill.hidden = false;
}

function hideFileJobPill() {
  fileJobPill.hidden = true;
}

fileJobPill.addEventListener("click", () => {
  if (ToolDialog.isWorking()) {
    hideFileJobPill();
    ToolDialog.reopen();
    return;
  }
  // A job started before this page loaded (from the PDF read before this
  // one): its dialog went with that page, so only its progress can be told.
  const event = FileJobs.current();
  if (event) showToast(`${fileJobPhaseText(event)}. You'll be told when it's done.`);
});

FileJobs.subscribe((event) => {
  // While its dialog is on screen the dialog shows the job; the pill is for
  // a job running behind the page.
  if (event.state === "working" && !ToolDialog.isOpen()) showFileJobPill(event);
  else hideFileJobPill();
});

// A job still running from before this page loaded gets its pill back.
FileJobs.sync().then((event) => {
  if (event && !ToolDialog.isOpen()) showFileJobPill(event);
});
