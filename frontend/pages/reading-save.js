// Saving and leaving: the unsaved-changes question every way out asks
// (promptSaveIfDirty, which Python's close gatekeeper calls too), Save and
// Save a copy, and the hand-offs to Organise, the file tools, Home, Settings
// and another PDF.

// ------------------------------------------------------------------ //
// Save flow                                                            //
// ------------------------------------------------------------------ //

// Every way of leaving the reader (Library, another PDF, the window closing)
// asks this first. An Organise draft is applied before the question, so the
// question covers it; staying after all (Cancel) reloads the reader onto the
// changed pages, which the view behind the draft no longer shows.
async function promptSaveIfDirty(allowDiscard) {
  if (!isOrganiseOpen()) return askToSaveIfDirty(allowDiscard);
  const applied = await applyOrganiseBeforeLeaving();
  // Organise has already adopted the state Done answered (organise.js).
  if (!applied) return false;
  const leaving = await askToSaveIfDirty(allowDiscard);
  if (!leaving && applied.changed) {
    // Its "older changes" toast, if any, was already shown when it applied.
    await runPageChange(
      async () => ({ ...state, pages_changed: true, history_trimmed: false }),
      { unsavedNote: state.is_dirty },
    );
  }
  return leaving;
}

async function askToSaveIfDirty(allowDiscard) {
  if (!state.is_dirty) return true;

  const behavior = await callApi("get_save_behavior");
  let mode;
  let remember = false;
  if (behavior === "ask") {
    await callApi("set_voice_context", "save_confirm");
    const result = await SaveDialog.open({ allowDiscard });
    await callApi("set_voice_context", "reading");
    if (result.action === "discard") return true;
    if (result.action !== "save") return false;
    mode = result.mode;
    remember = result.remember;
    if (remember) {
      await callApi("set_save_behavior", mode);
    }
  } else {
    mode = behavior;
  }

  // Overwrite needs no path; a copy does, and used to come from the native
  // dialog's own return value — now gathered here instead.
  let path = null;
  if (mode === "copy") {
    path = await pickCopyPath();
    if (!path) return false;
  }

  return finishSave(await callApi("perform_save", mode, path));
}

// What perform_save answered, acted on: the new state, or the reason it
// didn't happen.
function finishSave(outcome) {
  if (outcome.ok) {
    adoptState(outcome.state);
    return true;
  }
  // `error` is already a sentence for the reader (api.perform_save): what
  // happened, and that the file on disk is unchanged.
  if (!outcome.cancelled) {
    alert(outcome.error);
  }
  // The one exception: the highlights were saved but the PDF couldn't be
  // opened again, so there is no document left to read here.
  if (outcome.closed) {
    window.location.href = "../index.html";
  }
  return false;
}

// The More menu's Save (and Ctrl+S): the usual flow, which asks how to save
// if it hasn't been told. With nothing unsaved there is nothing to do, which
// a click is told rather than left wondering.
async function saveFromMenu() {
  if (!state.is_dirty) {
    showToast("No unsaved changes to save.");
    return;
  }
  await promptSaveIfDirty(false);
}

// Save a copy… (the More menu and Ctrl+Shift+S): straight to the Save-As
// screen, whether or not anything is unsaved, since a copy is wanted for its
// own sake. The reader's own choice of "overwrite" in Settings doesn't apply.
async function saveCopy() {
  const path = await pickCopyPath();
  if (!path) return false;
  return finishSave(await callApi("perform_save", "copy", path));
}

// A file a tool wrote, opened in the reader: after the usual unsaved-
// highlights question, like any PDF dropped on the window.
async function openToolOutput(path) {
  if (!(await promptSaveIfDirty(true))) return;
  if (!(await openPdf(path))) return;
  window.location.reload();
}

// Home sets this before opening a PDF to organise it (F2.7).
const OPEN_ORGANISE_NOTE = "lector-open-organise";

// Organise pages (organise.js): the More menu, Ctrl+Shift+O, Home's Tools
// page and Recent menus (through a note this page reads on load). What the
// reader has open over the page goes first, and Focus mode, which would hide
// the footer and its mic pill.
function startOrganise() {
  if (document.querySelector(".dialog-scrim:not([hidden])") || isPageChangeBusy()) return;
  if (state.organise_blocked) {
    showToast(state.organise_blocked);
    return;
  }
  openOrganise();
}

// Compress, Make searchable, Export as images (tool-dialogs.js), for the PDF
// as it is on screen.
function openTool(tool) {
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  ToolDialog.open(tool, "open");
}

// The Save-As screen, asked again until it gives a path that is free or that
// the reader agrees to replace — as a native Save-As does, Cancel on "Replace
// this file?" goes back to the screen, on the same folder and name. Null when
// the reader cancels the screen itself. The open PDF's own path is let
// through: perform_save refuses it with its own explanation.
async function pickCopyPath() {
  let { dir, filename } = await callApi("get_save_dialog_start");
  for (;;) {
    const picked = await saveAsDialog.open({ dir, filename });
    if (!picked || picked.cancelled) return null;
    const target = await callApi("describe_save_target", picked.path);
    if (!target.exists || target.is_open_document) return picked.path;
    if (await ReplaceDialog.open({ name: target.name, folder: target.folder })) {
      return picked.path;
    }
    dir = target.folder;
    filename = target.name;
  }
}

// Shared by the back button and the voice "go home" command (Milestone 8.4)
// so the two ways of leaving the reading view go through the identical dirty
// check rather than voice getting its own, easier-to-drift copy of it.
async function goHome() {
  if (await promptSaveIfDirty(true)) {
    window.location.href = "../index.html";
  }
}

// The voice "close app"/"quit" command (Milestone 8.11). No click/keyboard
// wiring of its own — the OS window's own close button and Alt+F4 already
// are this command's mouse/keyboard equivalent, and `close_app()` re-enters
// the exact same `handle_window_closing()` dirty check and save-or-discard
// prompt those already trigger.
async function closeApp() {
  await callApi("close_app");
}

// Leaves for another screen, but only once any unsaved highlights have been
// dealt with. `openDialog` asks Home to open its Open dialog on arrival (the
// dialog only exists there) via the one-shot note home.js reads and clears.
async function leaveReadingTo(url, { openDialog = false } = {}) {
  if (!(await promptSaveIfDirty(true))) return;
  if (openDialog) sessionStorage.setItem("lector-open-dialog", "1");
  window.location.href = url;
}

// A PDF dropped anywhere on the window (frontend/js/filedrop.js) replaces the
// open document, after the same unsaved-highlights check as leaving. Not while
// a dialog is up: the reader is mid-decision about something else.

async function onPdfDropped(path) {
  // Not while page changes are being applied (the card takes keys and clicks,
  // not drops from the OS).
  if (isPageChangeBusy()) return;
  if (document.querySelector(".dialog-scrim:not([hidden])")) {
    showToast("Close this window first, then drop the file again.");
    return;
  }
  if (!(await promptSaveIfDirty(true))) return;
  // On a failure or a cancelled password the PDF on screen stays open as it
  // was (Api.open_pdf only lets it go once the new one has opened).
  if (!(await openPdf(path))) return;
  // The page reads the open document on load, so reloading shows the new one.
  window.location.reload();
}
