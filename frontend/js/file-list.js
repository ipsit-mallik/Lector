// The file tools' list of input files (docs/DESIGN_SYSTEM.md § "File tools"
// → "File list"), shared by Convert to PDF and Combine files (Home's Tools
// page): the files chosen, in the order they become pages.
//
// With no files it is the dashed drop zone with Add files…. With files it is
// one slim strip ("7 files · 7 pages", "Drop more files anywhere on the
// list", Add files…) over the list, which takes the dialog's spare height and
// scrolls, with a hairline and a fade at an edge that has more beyond it.
// Files dragged in from outside the app light the list up; their drop is the
// window's (filedrop.js), which hands them to the open dialog.
//
// A row: a drag handle, its position, a thumbnail, the name and a muted line
// the dialog supplies ("JPEG · 2.1 MB", "PDF · 58 pages"), then Move up,
// Move down and Remove, shown on hover or focus. Rows reorder by dragging
// (js/pointer-reorder.js, never the browser's drag-and-drop), by those
// buttons, or with Alt+↑/↓ on a focused row, and slide to their new places
// (FLIP). The dialog keeps the files and re-renders; this says what was asked
// for (`onMove(from, to)`, `onRemove(index)`) and keeps its own nodes, the
// scroll position and each file's thumbnail image, across renders.
//
// Every control carries `data-keep`, so a dialog that re-renders can put
// focus back on the same one (convert-dialog.js's renderKeepingFocus).
//
// Built from DOM calls with textContent: names come from the reader's disk.
"use strict";

// The dragged row's preview hangs this far right of and below the pointer,
// so the insertion line under it stays in sight.
const FILE_LIST_PREVIEW_OFFSET = { dx: 40, dy: 16 };
const FILE_LIST_BAR_THICKNESS_PX = 2;

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

function fileListAddButton(onAdd, className = "btn btn-secondary") {
  const add = fileListEl("button", className, "Add files…");
  add.type = "button";
  add.dataset.keep = "add";
  add.addEventListener("click", onAdd);
  return add;
}

// The dashed zone: an upload icon, `text` ("Drop images or an e-book here"),
// and Add files…, which calls `onAdd`.
function buildDropZone({ text, onAdd }) {
  const zone = fileListEl("div", "drop-zone");
  const zoneText = fileListEl("span", "drop-zone-text");
  zoneText.append(fileListIcon("upload"), fileListEl("span", "", text));
  zone.append(zoneText, fileListAddButton(onAdd));
  return zone;
}

// A list's files with one moved from `from` to `to` (a new array).
function moveFileListItem(files, from, to) {
  if (to < 0 || to >= files.length || from === to) return files;
  const moved = files.slice();
  const [item] = moved.splice(from, 1);
  moved.splice(to, 0, item);
  return moved;
}

// Where a row dragged from `from` lands when dropped in slot `gap` (0 is
// before the first row): the index `onMove` is given, the same a run of Move
// up / Move down presses would reach.
function fileListDropIndex(from, gap) {
  return gap > from ? gap - 1 : gap;
}

// One list's behaviour. `label` is the list's accessible name; `meta(file)`
// the muted line under a file's name. `build(files, { emptyText, onAdd,
// summary })` makes the list area for the files as they are now; the dialog
// calls it on every render and puts what it returns in its body.
function createFileList({ label, meta, onMove, onRemove }) {
  const area = fileListEl("div", "file-list-area");
  const strip = fileListEl("div", "file-list-strip");
  const count = fileListEl("span", "file-list-count");
  count.setAttribute("aria-live", "polite");
  const hint = fileListEl("span", "file-list-hint");
  hint.append(fileListIcon("upload"), fileListEl("span", "", "Drop more files anywhere on the list"));
  let stripAdd = null;
  // The frame draws the hairlines at an edge with more beyond it; the
  // scroller inside it fades there (a mask, which would hide the lines too).
  const frame = fileListEl("div", "file-list-frame");
  const scroller = fileListEl("div", "file-list-scroll");
  frame.appendChild(scroller);
  const listEl = fileListEl("div", "file-list");
  listEl.setAttribute("role", "list");
  listEl.setAttribute("aria-label", label);
  const bar = fileListEl("div", "file-list-insertion");
  bar.setAttribute("aria-hidden", "true");
  bar.hidden = true;
  scroller.appendChild(listEl);

  let files = [];
  let rows = [];
  // Each file's thumbnail <img>, made once: a move or a re-render puts the
  // same, already decoded image in its new row.
  const images = new WeakMap();
  let lastScrollTop = 0;
  let flipFrom = null; // file -> its row's top before a move, for FLIP
  let settlePending = false;
  const motion = createReorderMotion();

  // ---- Scroll edges --------------------------------------------------------

  function updateEdges() {
    const max = scroller.scrollHeight - scroller.clientHeight;
    frame.classList.toggle("is-overflowing", max > 1);
    frame.classList.toggle("is-scrolled", scroller.scrollTop > 0);
    frame.classList.toggle("is-at-end", scroller.scrollTop >= max - 1);
  }

  scroller.addEventListener("scroll", () => {
    lastScrollTop = scroller.scrollTop;
    updateEdges();
  });
  if (typeof ResizeObserver === "function") new ResizeObserver(updateEdges).observe(scroller);

  // ---- Files dragged in from outside --------------------------------------

  // Counted, since dragenter/dragleave fire for every element crossed. The
  // drop itself is the window's (filedrop.js); this only shows where.
  let fileDragDepth = 0;
  function carriesFiles(ev) {
    return Array.from((ev.dataTransfer && ev.dataTransfer.types) || []).includes("Files");
  }
  function setDropTarget(on) {
    area.classList.toggle("is-drop-target", on);
  }
  area.addEventListener("dragenter", (ev) => {
    if (!carriesFiles(ev)) return;
    fileDragDepth += 1;
    setDropTarget(true);
  });
  area.addEventListener("dragleave", (ev) => {
    if (!carriesFiles(ev)) return;
    fileDragDepth = Math.max(0, fileDragDepth - 1);
    if (!fileDragDepth) setDropTarget(false);
  });
  const resetFileDrag = () => {
    fileDragDepth = 0;
    setDropTarget(false);
  };
  window.addEventListener("drop", resetFileDrag, true);
  window.addEventListener("dragend", resetFileDrag, true);
  window.addEventListener("lector:filedrop", resetFileDrag);

  // ---- Rows -------------------------------------------------------------------

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

  function thumbnailOf(file) {
    if (!file.thumbnail) return null;
    let img = images.get(file);
    if (!img) {
      img = document.createElement("img");
      img.alt = "";
      img.draggable = false;
      img.src = `data:image/png;base64,${file.thumbnail}`;
      images.set(file, img);
    }
    return img;
  }

  // Before a change the dialog will re-render for: the scroll position as it
  // is now (a scroll event may still be on its way, after an auto-scroll
  // step), since taking the list out of the page loses it.
  function noteScroll() {
    lastScrollTop = scroller.scrollTop;
  }

  // Every move, by drag, button or key, goes through here: where each row
  // was is noted first, so the rows can slide from there once redrawn.
  function move(from, to) {
    noteScroll();
    flipFrom = new Map(rows.map((row, i) => [files[i], row.offsetTop]));
    onMove(from, to);
  }

  function buildRow(file, i, total) {
    const row = fileListEl("div", "file-list-row");
    row.setAttribute("role", "listitem");
    row.tabIndex = 0;
    row.dataset.keep = `row-${i}`;
    row.setAttribute("aria-label", `${i + 1}. ${file.name}. Alt+Up or Alt+Down moves it.`);
    const thumb = fileListEl("span", "file-list-thumb");
    const img = thumbnailOf(file);
    if (img) thumb.appendChild(img);
    const text = fileListEl("span", "file-list-text");
    text.append(fileListEl("span", "file-list-name", file.name), fileListEl("span", "file-list-meta", meta(file)));
    const actions = fileListEl("span", "file-list-actions");
    actions.append(
      rowButton("chevron_up", "Move up", file.name, i === 0, () => move(i, i - 1), `up-${i}`),
      rowButton("chevron_down", "Move down", file.name, i === total - 1, () => move(i, i + 1), `down-${i}`),
      rowButton("close", "Remove", file.name, false, () => {
        noteScroll();
        onRemove(i);
      }, `remove-${i}`)
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
        if (to >= 0 && to < total) move(i, to);
      }
    });
    // A press on a row is the drag's (it keeps the browser from focusing it),
    // so a click focuses it here.
    row.addEventListener("click", (ev) => {
      if (!ev.target.closest("button")) row.focus({ preventScroll: true });
    });
    return row;
  }

  // ---- Dragging ------------------------------------------------------------------

  function buildPreview(row) {
    const preview = fileListEl("div", "file-list-lift");
    preview.setAttribute("aria-hidden", "true");
    const thumb = fileListEl("span", "file-list-thumb");
    const img = row.querySelector(".file-list-thumb img");
    if (img) {
      const copy = document.createElement("img");
      copy.alt = "";
      copy.draggable = false;
      copy.src = img.src;
      thumb.appendChild(copy);
    }
    preview.append(thumb, fileListEl("span", "file-list-name", row.querySelector(".file-list-name").textContent));
    return preview;
  }

  const drag = createPointerReorder({
    container: listEl,
    scroller,
    indicator: bar,
    layout: REORDER_LAYOUTS.list({ thickness: FILE_LIST_BAR_THICKNESS_PX }),
    motion,
    items: () => rows,
    itemAt: (target) => (target.closest("button") ? null : target.closest(".file-list-row")),
    pickUp: (row) => {
      const index = rows.indexOf(row);
      return index < 0 ? [] : [index];
    },
    liftedItems: (keys) => keys.map((index) => rows[index]).filter(Boolean),
    buildPreview,
    previewHome: (row) => {
      const r = row.getBoundingClientRect();
      return { left: r.left, top: r.top };
    },
    previewAt: FILE_LIST_PREVIEW_OFFSET,
    drop: ([from], gap) => {
      const to = fileListDropIndex(from, gap);
      if (to !== from) move(from, to);
    },
  });

  // ---- After a render ----------------------------------------------------------

  // The dialog takes the area out and puts it back on every render, which
  // loses the scroll position; it is put back before the frame paints, and
  // the rows that moved slide from where they were.
  function settle() {
    settlePending = false;
    if (!area.isConnected) {
      flipFrom = null;
      return;
    }
    if (scroller.scrollTop !== lastScrollTop) scroller.scrollTop = lastScrollTop;
    keepFocusInView();
    updateEdges();
    flip();
  }

  function keepFocusInView() {
    const focused = document.activeElement;
    const row = focused && focused.closest ? focused.closest(".file-list-row") : null;
    if (!row || !listEl.contains(row)) return;
    const top = row.offsetTop;
    const bottom = top + row.offsetHeight;
    if (top < scroller.scrollTop) scroller.scrollTop = top;
    else if (bottom > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = bottom - scroller.clientHeight;
    lastScrollTop = scroller.scrollTop;
  }

  function flip() {
    const before = flipFrom;
    flipFrom = null;
    if (!before || motion.reduced()) return;
    const base = motion.tokenMs("--dur-reorder");
    rows.forEach((row, i) => {
      const from = before.get(files[i]);
      if (from === undefined || typeof row.animate !== "function") return;
      const dy = from - row.offsetTop;
      if (!dy) return;
      row.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], {
        duration: base, easing: motion.easing(),
      });
    });
  }

  function build(next, { emptyText, onAdd, summary }) {
    if (drag.active()) drag.cancel();
    files = next;
    if (!files.length) {
      flipFrom = null;
      lastScrollTop = 0;
      rows = [];
      listEl.replaceChildren();
      area.classList.remove("has-files");
      area.replaceChildren(buildDropZone({ text: emptyText, onAdd }));
      return area;
    }
    rows = files.map((file, i) => buildRow(file, i, files.length));
    listEl.replaceChildren(...rows, bar);
    count.textContent = summary;
    const add = fileListAddButton(onAdd, "btn btn-secondary btn-sm");
    if (stripAdd) stripAdd.replaceWith(add);
    else strip.append(count, hint, add);
    stripAdd = add;
    area.classList.add("has-files");
    area.replaceChildren(strip, frame);
    if (!settlePending) {
      settlePending = true;
      queueMicrotask(settle);
    }
    return area;
  }

  // The row now at `index`, to focus after a move or a removal.
  function row(index) {
    return rows[index] || null;
  }

  return { build, row, cancelDrag: () => drag.cancel() };
}
