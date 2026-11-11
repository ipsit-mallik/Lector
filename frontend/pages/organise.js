// Organise pages (docs/PRD.md § "Post-v1: file tools" → Tier 2): rotate,
// delete, reorder, extract and insert pages, in a grid that replaces the
// reader's chrome while it is open (docs/DESIGN_SYSTEM.md § "Organise pages").
//
// Everything here edits a draft held in Python (Api.organise_*, reading/
// page_plan.py), never the document: Done applies it as one undoable change
// ("Page changes"), through page-change-busy.js. The grid itself is
// organise-grid.js; reading.js opens this view and asks it, before the reader
// is left or the window closes, to apply what is pending.
"use strict";

const ORGANISE_HINT = "Drag to reorder · Ctrl+←/→ moves the selection · Ctrl+A selects all";
const ORGANISE_NEEDS_SELECTION = "Select pages first";
// How long a toast with a file's buttons (Open, Show in folder) stays up.
const ORGANISE_FILE_TOAST_MS = 8000;
// Applying failed in a way Python couldn't put in words (the bridge itself).
const ORGANISE_APPLY_FAILED = "Couldn't apply the page changes. The PDF is as it was; your changes are still here.";
const ORGANISE_REASONS = {
  every_page: "A PDF needs at least one page. Leave one unselected.",
};

const organiseView = document.getElementById("organiseView");
const organiseDocName = document.getElementById("organiseDocName");
const organiseUndoBtn = document.getElementById("organiseUndoBtn");
const organiseRedoBtn = document.getElementById("organiseRedoBtn");
const organiseDoneBtn = document.getElementById("organiseDoneBtn");
const organiseCancelBtn = document.getElementById("organiseCancelBtn");
const organiseDeleteBtn = document.getElementById("organiseDeleteBtn");
const organiseSelectionText = document.getElementById("organiseSelectionText");
const organiseSelectAllBtn = document.getElementById("organiseSelectAllBtn");
const organiseCount = document.getElementById("organiseCount");
const organiseNeedsSelection = [...organiseView.querySelectorAll("[data-needs-selection]")];

let organiseHooks = {
  title: () => "",
  onOpen: () => {},
  onClose: () => {},
  // Opens a PDF a file job wrote (Extract's Open), as the reader does.
  openOutput: () => {},
};
let organiseOpen = false;
let organiseDraft = { pages: [], can_undo: false, can_redo: false, steps: 0 };
let organiseActionToast = null;

function configureOrganise(hooks) {
  organiseHooks = { ...organiseHooks, ...hooks };
}

function isOrganiseOpen() {
  return organiseOpen;
}

const organiseGrid = createOrganiseGrid({
  grid: document.getElementById("organiseGrid"),
  scroller: document.getElementById("organiseScroll"),
  insertionBar: document.getElementById("organiseInsertion"),
  thumbnail: (page) => callApi("organise_thumbnail", page.source, page.index, Math.round(120 * devicePixelRatio)),
  onMove: ({ kind, ...args }) => organiseAct(kind, args),
  onSelectionChange: () => updateOrganiseChrome(),
  onDelete: () => organiseDelete(),
});

function organisePlural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

// Undo and Redo, the action bar's enabled states, the selection line and
// the footer's count.
function updateOrganiseChrome() {
  const total = organiseGrid.pageCount();
  const chosen = organiseGrid.selectedPositions().length;
  const pagesText = organisePlural(total, "page", "pages");
  organiseSelectionText.textContent = chosen ? `${chosen} of ${total} selected` : pagesText;
  organiseSelectAllBtn.textContent = organiseGrid.allSelected() ? "Select none" : "Select all";
  for (const button of organiseNeedsSelection) {
    button.setAttribute("aria-disabled", String(!chosen));
    button.title = chosen ? "" : ORGANISE_NEEDS_SELECTION;
  }
  setOrganiseHistoryButton(organiseUndoBtn, organiseDraft.can_undo, "Undo  (Ctrl+Z)", "Nothing to undo");
  setOrganiseHistoryButton(organiseRedoBtn, organiseDraft.can_redo, "Redo  (Ctrl+Y)", "Nothing to redo");
  const changes = organiseDraft.steps;
  organiseCount.textContent = changes
    ? `${pagesText} · ${organisePlural(changes, "unsaved change", "unsaved changes")}`
    : pagesText;
}

function setOrganiseHistoryButton(button, enabled, title, emptyTitle) {
  button.setAttribute("aria-disabled", String(!enabled));
  button.title = enabled ? title : emptyTitle;
}

function showOrganiseDraft(view, { selection } = {}) {
  organiseDraft = view;
  organiseGrid.render(view.pages, { selection });
  updateOrganiseChrome();
}

// Opens the view, focused on the page being read. False, with a toast saying
// why, for a PDF whose pages can't be changed.
async function openOrganise() {
  if (organiseOpen || isPageChangeBusy()) return false;
  const view = await callApi("organise_begin");
  if (!view.ok) {
    showToast(view.message);
    return false;
  }
  organiseOpen = true;
  // Its own grammar ("done", "cancel"), the globals still heard.
  callApi("set_voice_context", "organise");
  organiseDocName.textContent = organiseHooks.title();
  organiseHooks.onOpen();
  organiseView.hidden = false;
  document.querySelector(".reading-view").classList.add("is-organising");
  organiseCount.hidden = false;
  showOrganiseDraft(view);
  organiseGrid.focusPosition(Math.min(view.page_index || 0, view.pages.length - 1));
  return true;
}

function closeOrganiseView() {
  organiseOpen = false;
  callApi("set_voice_context", "reading");
  if (organiseActionToast) organiseActionToast();
  organiseView.hidden = true;
  organiseCount.hidden = true;
  document.querySelector(".reading-view").classList.remove("is-organising");
  organiseHooks.onClose();
}

// One action on the draft: `rotate`, `move` or `shift` (a delete comes from
// its confirmation, organiseDeleteConfirmed). A refusal says why; a move
// offers Undo.
async function organiseAct(kind, args) {
  if (!organiseOpen || isPageChangeBusy()) return null;
  const view = await callApi("organise_action", kind, args);
  if (!view.ok) {
    const reason = ORGANISE_REASONS[view.reason];
    if (reason) showToast(reason);
    return view;
  }
  showOrganiseDraft(view, { selection: view.selection });
  if (view.selection && view.selection.length) organiseGrid.focusPosition(view.selection[0]);
  if (view.changed && (kind === "move" || kind === "shift")) {
    showOrganiseUndoToast(`Moved ${organisePlural(args.positions.length, "page", "pages")}`);
  }
  return view;
}

function showOrganiseUndoToast(text) {
  showOrganiseToast(text, { actionLabel: "Undo", onAction: () => organiseUndo() });
}

// One toast of Organise's at a time: a new one takes the last one's place.
function showOrganiseToast(text, options) {
  if (organiseActionToast) organiseActionToast();
  organiseActionToast = showToast(text, options);
}

function organiseRotate(degrees) {
  const positions = organiseGrid.selectedPositions();
  if (positions.length) organiseAct("rotate", { positions, degrees });
}

// Delete (the button, the Delete key): always asks first, unless it would
// leave no page at all, which is refused before the question. Deleting is
// one step of the draft, which Organise's Undo (or the toast's) takes back.
async function organiseDelete() {
  const positions = organiseGrid.selectedPositions();
  if (!organiseOpen || !positions.length || isPageChangeBusy() || OrganiseDeleteDialog.isOpen()) return;
  if (positions.length === organiseGrid.pageCount()) {
    showToast(ORGANISE_REASONS.every_page);
    return;
  }
  const confirmed = await OrganiseDeleteDialog.open({
    pages: positions.map((position) => organiseGrid.pageView(position)),
    docName: organiseHooks.title(),
  });
  if (!confirmed) {
    organiseGrid.focusCurrent();
    return;
  }
  const view = await organiseAct("delete", { positions });
  // Focus where the deleted pages were: the page now in the first one's place.
  if (view && view.ok) organiseGrid.focusPosition(Math.min(positions[0], organiseGrid.pageCount() - 1));
  if (view && view.ok && view.changed) {
    showOrganiseUndoToast(`Deleted ${organisePlural(positions.length, "page", "pages")}`);
  }
}

let organiseExtracting = false;

// Extract… (needs a selection): the pages as a new PDF, by a file job, in
// its dialog (organise-extract.js), the draft untouched. Then "Saved <name>
// beside the original" with Open and Show in folder; with "Also delete"
// ticked, the pages are deleted as one step of the draft (no question: the
// tick was the answer) and the toast offers Undo instead of Open.
async function organiseExtract() {
  const positions = organiseGrid.selectedPositions();
  if (!organiseOpen || !positions.length || organiseExtracting || isPageChangeBusy()) return;
  organiseExtracting = true;
  try {
    const done = await OrganiseExtractDialog.open({ positions });
    if (!done || !organiseOpen) {
      if (organiseOpen) organiseGrid.focusCurrent();
      return;
    }
    const { path, name, where } = done.result;
    const saved = `Saved ${name}${savePlaceClause(where)}`;
    const showInFolder = { label: "Show in folder", onAction: () => showFileJobOutput(path) };
    if (done.alsoDelete) {
      const view = await organiseAct("delete", { positions });
      if (view && view.ok && view.changed) {
        organiseGrid.focusPosition(Math.min(positions[0], organiseGrid.pageCount() - 1));
        showOrganiseToast(`${saved} and removed ${positions.length === 1 ? "it" : "them"} here`, {
          actions: [{ label: "Undo", onAction: () => organiseUndo() }, showInFolder],
          durationMs: ORGANISE_FILE_TOAST_MS,
        });
        return;
      }
    }
    organiseGrid.focusCurrent();
    showOrganiseToast(saved, {
      actions: [{ label: "Open", onAction: () => organiseHooks.openOutput(path) }, showInFolder],
      durationMs: ORGANISE_FILE_TOAST_MS,
    });
  } finally {
    organiseExtracting = false;
  }
}

// Insert…'s file: chosen in the Open dialog (PDFs, images, e-books) and opened
// in Python, a protected PDF asking for its password (password-dialog.js;
// only held in memory, with the file). `Api.organise_add_source`'s answer, or
// null when cancelled or when the file can't be used (the toast says why).
async function chooseInsertSource() {
  const picked = await ensureOpenFileDialog("organise").open({ dir: null, kind: "insert", title: "Insert pages from" });
  if (!picked || !picked.path || !organiseOpen) return null;
  let answer = await callApi("organise_add_source", picked.path);
  if (!answer.ok && answer.reason === "password") {
    answer = await PasswordDialog.ask({
      name: answer.name,
      attempt: async (password) => {
        const retry = await callApi("organise_add_source", picked.path, password);
        if (!retry.ok && retry.reason === "wrong_password") return { error: retry.error };
        return { done: retry };
      },
    });
    if (!answer) return null;
  }
  if (!answer.ok) {
    showToast(answer.error);
    return null;
  }
  return answer;
}

function forgetInsertSource(source) {
  callApi("organise_forget_source", source).catch((err) => console.error("organise: letting go of a file", err));
}

let organiseInserting = false;

// Insert… (always available): a file, then where its pages go, then every
// page of it is put in as one step of the draft, selected and scrolled to,
// each with the "New" pill until Done.
async function organiseInsert() {
  if (!organiseOpen || organiseInserting || isPageChangeBusy()) return;
  organiseInserting = true;
  try {
    const source = await chooseInsertSource();
    if (!source || !organiseOpen) {
      if (source) forgetInsertSource(source.source);
      if (organiseOpen) organiseGrid.focusCurrent();
      return;
    }
    const chosen = await OrganiseInsertDialog.open({
      source,
      positions: organiseGrid.selectedPositions(),
      total: organiseGrid.pageCount(),
      choose: chooseInsertSource,
      forget: forgetInsertSource,
    });
    if (!chosen) {
      organiseGrid.focusCurrent();
      return;
    }
    const view = await organiseAct("insert", { source: chosen.source, gap: chosen.gap });
    if (view && view.ok && view.changed) {
      showOrganiseUndoToast(`Inserted ${organisePlural(chosen.pages, "page", "pages")} from ${chosen.name}`);
    }
  } finally {
    organiseInserting = false;
  }
}

// After an Undo or Redo from somewhere that is gone now (the toast's button),
// the keyboard goes back to the grid rather than being lost.
function keepOrganiseFocus() {
  if (!organiseView.contains(document.activeElement)) organiseGrid.focusCurrent();
}

async function organiseUndo() {
  if (!organiseOpen || !organiseDraft.can_undo || isPageChangeBusy()) return;
  const view = await callApi("organise_undo");
  if (view.ok) showOrganiseDraft(view);
  keepOrganiseFocus();
}

async function organiseRedo() {
  if (!organiseOpen || !organiseDraft.can_redo || isPageChangeBusy()) return;
  const view = await callApi("organise_redo");
  if (view.ok) showOrganiseDraft(view);
  keepOrganiseFocus();
}

// Applies the draft (Python's organise_done) under the busy card. Null when
// it failed, after saying so: the draft and the grid stay as they were, and
// its thumbnails load again.
async function applyOrganiseDraft(options) {
  organiseGrid.freeze();
  let state = null;
  try {
    state = await runPageChange(() => callApi("organise_done"), options);
  } catch (err) {
    console.error("Applying the page changes failed:", err);
    state = { error: ORGANISE_APPLY_FAILED };
  }
  if (state && !state.error) return state;
  if (state) showToast(state.error);
  organiseGrid.thaw();
  return null;
}

// Done (and the spoken "done"): apply the draft and go back to reading. With
// nothing changed it just leaves: no snapshot, no busy card. With changes,
// the busy card, then the reader reloading on the changed document
// (page-change-busy.js). If applying fails the draft stays open.
async function finishOrganise() {
  if (!organiseOpen || isPageChangeBusy()) return;
  if (!organiseDraft.steps) {
    await callApi("organise_done");
    closeOrganiseView();
    return;
  }
  const state = await applyOrganiseDraft({ unsavedNote: true });
  if (state && !state.pages_changed) closeOrganiseView();
}

// Cancel (and Esc, and the spoken "cancel"): back to reading without
// applying anything. With no changes it just leaves; with changes it asks
// first ("Discard page changes?"), and Keep editing returns to the grid as it
// was. Discarding drops the draft: no snapshot, no busy card, and the reader
// is exactly as it was before Organise opened.
async function cancelOrganise() {
  if (!organiseOpen || isPageChangeBusy() || OrganiseDiscardDialog.isOpen()) return;
  if (organiseDraft.steps) {
    const discard = await OrganiseDiscardDialog.open({ changes: organiseDraft.steps });
    if (!discard) {
      organiseGrid.focusCurrent();
      return;
    }
  }
  await callApi("organise_cancel");
  closeOrganiseView();
}

// Before the reader is left (Library, another PDF, the window closing): the
// draft is applied first, without a reload, so the unsaved-changes question
// that follows covers it. Answers the reader's new state (null when Organise
// wasn't open) and whether pages changed; null when applying failed.
async function applyOrganiseBeforeLeaving() {
  if (!organiseOpen) return { state: null, changed: false };
  let state;
  if (organiseDraft.steps) {
    state = await applyOrganiseDraft({ reload: false });
    if (!state) return null;
  } else {
    state = await callApi("organise_done");
  }
  closeOrganiseView();
  return { state, changed: Boolean(state.pages_changed) };
}

// Organise's keys, from anywhere in the view. True when the key was one.
function handleOrganiseKey(ev) {
  const ctrl = ev.ctrlKey || ev.metaKey;
  const key = ev.key.toLowerCase();
  if (ev.key === "Escape") {
    cancelOrganise();
    return true;
  }
  if (ctrl && !ev.altKey && key === "a") {
    organiseGrid.selectAll();
    return true;
  }
  if (ctrl && !ev.altKey && (key === "y" || (key === "z" && ev.shiftKey))) {
    organiseRedo();
    return true;
  }
  if (ctrl && !ev.altKey && key === "z") {
    organiseUndo();
    return true;
  }
  return organiseGrid.handleKey(ev);
}

document.addEventListener("keydown", (ev) => {
  if (!organiseOpen || ev.defaultPrevented) return;
  // A dialog over the view (the delete confirmation, What can I say?) takes
  // its own keys.
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  if (handleOrganiseKey(ev)) ev.preventDefault();
});

// aria-disabled, not disabled, so each keeps its tooltip saying why.
function onOrganiseButton(button, action) {
  button.addEventListener("click", () => {
    if (button.getAttribute("aria-disabled") === "true") return;
    action();
  });
}

onOrganiseButton(document.getElementById("organiseRotateLeftBtn"), () => organiseRotate(-90));
onOrganiseButton(document.getElementById("organiseRotateRightBtn"), () => organiseRotate(90));
onOrganiseButton(document.getElementById("organiseDeleteBtn"), () => organiseDelete());
onOrganiseButton(document.getElementById("organiseExtractBtn"), () => organiseExtract());
onOrganiseButton(document.getElementById("organiseInsertBtn"), () => organiseInsert());
onOrganiseButton(organiseUndoBtn, () => organiseUndo());
onOrganiseButton(organiseRedoBtn, () => organiseRedo());
organiseDoneBtn.addEventListener("click", () => finishOrganise());
organiseCancelBtn.addEventListener("click", () => cancelOrganise());
organiseSelectAllBtn.addEventListener("click", () => {
  if (organiseGrid.allSelected()) organiseGrid.selectNone();
  else organiseGrid.selectAll();
});
