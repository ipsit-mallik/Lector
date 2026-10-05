// "Save changes?" confirmation. Mirrors the behavior previously implemented
// in src/lector/features/annotations/save_dialog.py (PySide6 QDialog):
// "Save a copy" pre-selected (the non-destructive default, so "don't ask
// again" can never silently arm an overwrite), an optional "Don't Save"
// button only offered when leaving via the back button, and a returned
// {mode, remember} choice.

const SaveDialog = (() => {
  const scrim = document.getElementById("saveDialog");
  const copyRow = document.getElementById("copyRow");
  const overwriteRow = document.getElementById("overwriteRow");
  const rememberCheck = document.getElementById("rememberCheck");
  const cancelBtn = document.getElementById("dialogCancelBtn");
  const discardBtn = document.getElementById("dialogDiscardBtn");
  const saveBtn = document.getElementById("dialogSaveBtn");
  const rows = [copyRow, overwriteRow];

  let resolvePromise = null;

  function selectRow(row) {
    rows.forEach((r) => r.classList.toggle("selected", r === row));
  }

  rows.forEach((row) => {
    row.addEventListener("click", () => selectRow(row));
  });
  // Arrows move focus only: "Overwrite the original" must not be one stray
  // (wrapping) keypress away, least of all with "don't ask again" ticked.
  initRadioCards(document.getElementById("saveDialogOptions"), { selectOnArrow: false });

  function close(result) {
    scrim.hidden = true;
    if (resolvePromise) {
      resolvePromise(result);
      resolvePromise = null;
    }
  }

  function saveWithMode(mode) {
    selectRow(mode === "overwrite" ? overwriteRow : copyRow);
    close({ action: "save", mode, remember: rememberCheck.checked });
  }

  cancelBtn.addEventListener("click", () => close({ action: "cancel" }));
  discardBtn.addEventListener("click", () => close({ action: "discard" }));
  saveBtn.addEventListener("click", () => {
    const mode = overwriteRow.classList.contains("selected") ? "overwrite" : "copy";
    saveWithMode(mode);
  });

  // allow_discard: true when reached via the "Library" back button, which
  // (unlike the explicit Ctrl+S flow) needs a way to leave without saving.
  function open({ allowDiscard = false } = {}) {
    selectRow(copyRow);
    rememberCheck.checked = false;
    discardBtn.hidden = !allowDiscard;
    scrim.hidden = false;
    return new Promise((resolve) => {
      resolvePromise = resolve;
    });
  }

  function isOpen() {
    return !scrim.hidden;
  }

  // SAVE_COPY/SAVE_OVERWRITE select and confirm in one spoken command rather
  // than requiring a separate "confirm" step — the mode itself is the whole
  // decision here, so this mirrors clicking a row and then Save in one go
  // (docs/TASKS.md 8.9: "no new dialog logic needed"). DONT_SAVE is a no-op
  // when the button isn't offered, same as it being un-clickable by mouse.
  function handleCommand(command) {
    switch (command.intent) {
      case "SAVE_COPY":
        saveWithMode("copy");
        break;
      case "SAVE_OVERWRITE":
        saveWithMode("overwrite");
        break;
      case "DONT_SAVE":
        if (!discardBtn.hidden) close({ action: "discard" });
        break;
      case "CANCEL":
        close({ action: "cancel" });
        break;
    }
  }

  return { open, isOpen, handleCommand };
})();
