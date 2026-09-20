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

  function close(result) {
    scrim.hidden = true;
    if (resolvePromise) {
      resolvePromise(result);
      resolvePromise = null;
    }
  }

  cancelBtn.addEventListener("click", () => close({ action: "cancel" }));
  discardBtn.addEventListener("click", () => close({ action: "discard" }));
  saveBtn.addEventListener("click", () => {
    const mode = overwriteRow.classList.contains("selected") ? "overwrite" : "copy";
    close({ action: "save", mode, remember: rememberCheck.checked });
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

  return { open };
})();
