// Shared in-app file-browser overlay for the Open/Save-As dialogs (Milestone
// 8.7). docs/ARCHITECTURE.md: the native OS file choosers are replaced
// because the voice router can only see and drive the app's own UI, not OS
// chrome, so folders/PDFs are listed in Python (features/dialogs/browser.py)
// and drawn as regular numbered rows here — the same Milestone 8.6
// numbered-overlay picker mechanic, reused row-for-row rather than rebuilt.
//
// One implementation backs both Home's "Open PDF" and the reading view's
// "Save a copy" flow: both are "list folders + PDFs, number the rows, let a
// click or a spoken number act on one." Only the footer (a plain Open vs. a
// filename field + Save) and the starting directory differ, which is why
// `mode` ("open" | "save") is the one branch point below rather than two
// near-duplicate modules.
//
// Every row is numbered and left visible for as long as the dialog is open,
// unlike Home's Recent grid: Recent has one card with a natural spoken label
// (Milestone 8.5's "open recent"), so its numbers are an opt-in fallback for
// everything else. Nothing here has a natural spoken label — the badges are
// not a togglable extra, they are the only way in.
function createFileBrowser({
  scrimId,
  listId,
  pathId,
  upBtnId,
  cancelBtnId,
  confirmBtnId,
  filenameId,
  mode,
  voiceContext,
  restingContext,
}) {
  const scrim = document.getElementById(scrimId);
  const listEl = document.getElementById(listId);
  const pathLabel = document.getElementById(pathId);
  const upBtn = document.getElementById(upBtnId);
  const cancelBtn = document.getElementById(cancelBtnId);
  const confirmBtn = confirmBtnId ? document.getElementById(confirmBtnId) : null;
  const filenameInput = filenameId ? document.getElementById(filenameId) : null;

  let entries = [];
  let currentDir = null;
  let parentDir = null;
  let settleOpen = null;

  // `browse_directory`'s `path` is already an OS-native absolute path
  // (`str(Path(...).resolve())` in browser.py) — Windows backslashes or POSIX
  // forward slashes, never mixed — so a plain string join matching whichever
  // separator it already uses is enough; no path library is needed just for
  // appending one filename.
  function joinPath(dir, name) {
    const sep = dir.includes("\\") ? "\\" : "/";
    return dir.endsWith(sep) ? dir + name : dir + sep + name;
  }

  function renderRows() {
    listEl.innerHTML = "";
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "browser-empty";
      empty.textContent = "No folders or PDFs here.";
      listEl.appendChild(empty);
      return;
    }
    entries.forEach((entry, i) => {
      const row = document.createElement("div");
      row.className = "browser-row";

      const icon = document.createElement("span");
      icon.className = "browser-row-icon";
      icon.dataset.icon = entry.is_dir ? "folder" : "empty_doc";
      row.appendChild(icon);

      const name = document.createElement("span");
      name.className = "browser-row-name";
      name.textContent = entry.name;
      row.appendChild(name);

      const badge = document.createElement("span");
      badge.className = "browser-row-badge";
      badge.textContent = String(i + 1);
      row.appendChild(badge);

      row.addEventListener("click", () => activateRow(i));
      listEl.appendChild(row);
    });
    mountIcons(listEl);
  }

  async function navigateTo(dir) {
    const listing = await callApi("browse_directory", dir);
    currentDir = listing.path;
    parentDir = listing.parent;
    entries = listing.entries;
    pathLabel.textContent = listing.error ? `${currentDir} — couldn't be read` : currentDir;
    upBtn.disabled = !parentDir;
    renderRows();
  }

  // A folder row navigates into it, in both modes. A PDF file row opens it
  // directly in `open` mode (the same "a number does what a click already
  // does" contract Milestone 8.6 established) — there is nothing to open in
  // `save` mode, so there it pre-fills the filename field instead, the same
  // convenience a native Save-As dialog offers when you click an existing
  // file.
  function activateRow(index) {
    const entry = entries[index];
    if (!entry) return;
    if (entry.is_dir) {
      navigateTo(entry.path);
      return;
    }
    if (mode === "open") {
      closeWith({ path: entry.path });
    } else if (filenameInput) {
      filenameInput.value = entry.name;
    }
  }

  function goUp() {
    if (parentDir) navigateTo(parentDir);
  }

  function confirmSave() {
    if (!filenameInput) return;
    let name = filenameInput.value.trim();
    if (!name) return;
    if (!name.toLowerCase().endsWith(".pdf")) name += ".pdf";
    closeWith({ path: joinPath(currentDir, name) });
  }

  function closeWith(result) {
    scrim.hidden = true;
    callApi("set_voice_context", restingContext);
    const resolve = settleOpen;
    settleOpen = null;
    if (resolve) resolve(result);
  }

  function isOpen() {
    return !scrim.hidden;
  }

  function open({ dir, filename }) {
    return new Promise((resolve) => {
      settleOpen = resolve;
      if (filenameInput) filenameInput.value = filename || "";
      scrim.hidden = false;
      callApi("set_voice_context", voiceContext);
      navigateTo(dir);
    });
  }

  // DIALOG_PICK/DIALOG_UP/CANCEL/DIALOG_CONFIRM only ever arrive while this
  // dialog is the active voice context (router.py scopes them there), so the
  // caller only needs to route to this while `isOpen()` is true.
  function handleCommand(command) {
    switch (command.intent) {
      case "DIALOG_PICK":
        activateRow(command.index - 1);
        break;
      case "DIALOG_UP":
        goUp();
        break;
      case "CANCEL":
        closeWith({ cancelled: true });
        break;
      case "DIALOG_CONFIRM":
        confirmSave();
        break;
    }
  }

  upBtn.addEventListener("click", goUp);
  cancelBtn.addEventListener("click", () => closeWith({ cancelled: true }));
  if (confirmBtn) confirmBtn.addEventListener("click", confirmSave);

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && isOpen()) closeWith({ cancelled: true });
  });

  return { open, handleCommand, isOpen };
}
