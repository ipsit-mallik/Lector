// The file tools' list of input files (docs/DESIGN_SYSTEM.md § "File tools"
// → "File list"): a drop zone with Add files…, and a reorderable list of the
// files chosen, in the order they become pages. Shared by Convert to PDF and
// Combine files (Home's Tools page).
//
// A row: a drag handle, its position, a thumbnail, the name and a muted line
// the dialog supplies ("JPEG · 2.1 MB", "PDF · 58 pages"), then Move up,
// Move down and Remove, shown on hover or focus. Rows reorder by dragging,
// by those buttons, or with Alt+↑/↓ on a focused row. The dialog keeps the
// files and re-renders; this only builds the nodes and says what was asked
// for (`onMove(from, to)`, `onRemove(index)`).
//
// Every control carries `data-keep`, so a dialog that re-renders can put
// focus back on the same one (convert-dialog.js's renderKeepingFocus).
//
// Built from DOM calls with textContent: names come from the reader's disk.
"use strict";

function fileListEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fileListIcon(name, className = "") {
  const span = fileListEl("span", className);
  span.dataset.icon = name;
  span.setAttribute("aria-hidden", "true");
  return span;
}

// The dashed zone: an upload icon, `text` ("Drop images or an e-book here"),
// and Add files…, which calls `onAdd`.
function buildDropZone({ text, onAdd }) {
  const zone = fileListEl("div", "drop-zone");
  const zoneText = fileListEl("span", "drop-zone-text");
  zoneText.append(fileListIcon("upload"), fileListEl("span", "", text));
  const add = fileListEl("button", "btn btn-secondary", "Add files…");
  add.type = "button";
  add.dataset.keep = "add";
  add.addEventListener("click", onAdd);
  zone.append(zoneText, add);
  return zone;
}

// One list's behaviour. `label` is the list's accessible name; `meta(file)`
// the muted line under a file's name. `build(files)` makes the list for the
// files as they are now; the dialog calls it on every render.
function createFileList({ label, meta, onMove, onRemove }) {
  let dragIndex = null;
  let listEl = null;

  function rowButton(icon, action, name, disabled, onClick, keep) {
    const button = fileListEl("button", "icon-btn icon-btn--sm");
    button.type = "button";
    button.disabled = disabled;
    button.dataset.keep = keep;
    button.dataset.tooltip = action;
    button.setAttribute("aria-label", `${action}: ${name}`);
    button.appendChild(fileListIcon(icon));
    button.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onClick();
    });
    return button;
  }

  function clearDropMarks() {
    if (!listEl) return;
    for (const row of listEl.children) row.classList.remove("is-drop-before", "is-drop-after");
  }

  // Drag a row to another place. Only drags that start on a row count; a file
  // dragged in from outside carries "Files" and is the window's drop.
  function bindRowDrag(row, index) {
    row.addEventListener("dragstart", (ev) => {
      dragIndex = index;
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/x-lector-row", String(index));
      row.classList.add("is-dragging");
    });
    row.addEventListener("dragend", () => {
      dragIndex = null;
      row.classList.remove("is-dragging");
      clearDropMarks();
    });
    row.addEventListener("dragover", (ev) => {
      if (dragIndex === null) return;
      ev.preventDefault();
      const box = row.getBoundingClientRect();
      const after = ev.clientY > box.top + box.height / 2;
      row.classList.toggle("is-drop-after", after);
      row.classList.toggle("is-drop-before", !after);
    });
    row.addEventListener("dragleave", () => row.classList.remove("is-drop-before", "is-drop-after"));
    row.addEventListener("drop", (ev) => {
      if (dragIndex === null) return;
      ev.preventDefault();
      const after = row.classList.contains("is-drop-after");
      let to = index + (after ? 1 : 0);
      if (dragIndex < to) to -= 1;
      const from = dragIndex;
      dragIndex = null;
      clearDropMarks();
      if (from !== to) onMove(from, to);
    });
  }

  function buildRow(file, i, count) {
    const row = fileListEl("div", "file-list-row");
    row.setAttribute("role", "listitem");
    row.tabIndex = 0;
    row.draggable = true;
    row.dataset.keep = `row-${i}`;
    row.setAttribute("aria-label", `${i + 1}. ${file.name}. Alt+Up or Alt+Down moves it.`);
    const thumb = fileListEl("span", "file-list-thumb");
    if (file.thumbnail) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = `data:image/png;base64,${file.thumbnail}`;
      thumb.appendChild(img);
    }
    const text = fileListEl("span", "file-list-text");
    text.append(fileListEl("span", "file-list-name", file.name), fileListEl("span", "file-list-meta", meta(file)));
    const actions = fileListEl("span", "file-list-actions");
    actions.append(
      rowButton("chevron_up", "Move up", file.name, i === 0, () => onMove(i, i - 1), `up-${i}`),
      rowButton("chevron_down", "Move down", file.name, i === count - 1, () => onMove(i, i + 1), `down-${i}`),
      rowButton("close", "Remove", file.name, false, () => onRemove(i), `remove-${i}`)
    );
    row.append(
      fileListIcon("drag_handle", "file-list-handle"),
      fileListEl("span", "file-list-number", String(i + 1)),
      thumb,
      text,
      actions
    );
    row.addEventListener("keydown", (ev) => {
      if (ev.target !== row || !ev.altKey) return;
      if (ev.key === "ArrowUp" || ev.key === "ArrowDown") {
        ev.preventDefault();
        ev.stopPropagation();
        const to = ev.key === "ArrowUp" ? i - 1 : i + 1;
        if (to >= 0 && to < count) onMove(i, to);
      }
    });
    bindRowDrag(row, i);
    return row;
  }

  function build(files) {
    dragIndex = null;
    listEl = fileListEl("div", "file-list");
    listEl.setAttribute("role", "list");
    listEl.setAttribute("aria-label", label);
    files.forEach((file, i) => listEl.appendChild(buildRow(file, i, files.length)));
    return listEl;
  }

  // The row now at `index`, to focus after a move or a removal.
  function row(index) {
    return listEl ? listEl.children[index] || null : null;
  }

  return { build, row };
}

// A list's files with one moved from `from` to `to` (a new array).
function moveFileListItem(files, from, to) {
  if (to < 0 || to >= files.length || from === to) return files;
  const moved = files.slice();
  const [item] = moved.splice(from, 1);
  moved.splice(to, 0, item);
  return moved;
}
