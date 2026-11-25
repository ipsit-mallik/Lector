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

// Add files… may pick files already in the list: they are left out, and a
// toast says how many ("1 file was already in the list"). Paths compare
// without regard to case or slash direction, as Windows does.
function fileListPathKey(path) {
  return String(path || "").replace(/\\/g, "/").toLowerCase();
}

// A path's file name, shown on its row while Python is still describing it.
function fileListName(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function skipListedFiles(files, paths) {
  const listed = new Set(files.map((f) => fileListPathKey(f.path)));
  const fresh = [];
  for (const path of paths) {
    const key = fileListPathKey(path);
    if (listed.has(key)) continue;
    listed.add(key);
    fresh.push(path);
  }
  const skipped = paths.length - fresh.length;
  if (skipped) showToast(skipped === 1 ? "1 file was already in the list" : `${skipped} files were already in the list`);
  return fresh;
}

// `files` with the pending row for `path` replaced by its description
// (`entry`), or without it (null). A row removed meanwhile stays removed.
function settlePendingFile(files, path, entry) {
  const key = fileListPathKey(path);
  const index = files.findIndex((f) => f.pending && fileListPathKey(f.path) === key);
  if (index < 0) return files;
  return entry ? files.map((f, i) => (i === index ? entry : f)) : files.filter((_, i) => i !== index);
}

// Whether `path` is still waiting in the list (not removed meanwhile).
function hasPendingFile(files, path) {
  const key = fileListPathKey(path);
  return files.some((f) => f.pending && fileListPathKey(f.path) === key);
}

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

// Make `parent`'s children `nodes`, in that order, moving and removing as
// little as it can: a child already in its place is left alone (its focus,
// its images, its animations with it). Shared with the dialogs that hold a
// list (Combine, Convert), which update their bodies the same way.
function syncChildren(parent, nodes) {
  nodes.forEach((node, i) => {
    const at = parent.children[i];
    if (at !== node) parent.insertBefore(node, at || null);
  });
  while (parent.children.length > nodes.length) parent.children[parent.children.length - 1].remove();
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
  // Each file's row and thumbnail <img>, by path, made once: a render keeps
  // the rows that are still listed (moving them, not making them again), so
  // nothing reloads or flashes.
  const rowsByKey = new Map();
  const images = new Map();
  let lastScrollTop = 0;
  let flipFrom = null; // path -> its row's top before a move, for FLIP
  let currentAdd = () => {};
  let dropZone = null;
  let dropZoneText = null;
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

  function rowButton(icon, action, onClick) {
    const button = fileListEl("button", "icon-btn icon-btn--sm");
    button.type = "button";
    button.dataset.tooltip = action;
    button.appendChild(fileListIcon(icon));
    button.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onClick();
    });
    return button;
  }

  function thumbnailOf(file) {
    if (!file.thumbnail) return null;
    const key = fileListPathKey(file.path);
    let img = images.get(key);
    if (!img) {
      img = document.createElement("img");
      img.alt = "";
      img.draggable = false;
      img.src = `data:image/png;base64,${file.thumbnail}`;
      images.set(key, img);
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
    flipFrom = new Map(rows.map((row, i) => [fileListPathKey(files[i].path), row.offsetTop]));
    onMove(from, to);
  }

  // A file's row, made once. Where it is in the list is looked up when it
  // is used (its buttons and keys), since the same row moves.
  function buildRow() {
    const row = fileListEl("div", "file-list-row");
    row.setAttribute("role", "listitem");
    row.tabIndex = 0;
    const at = () => rows.indexOf(row);
    const thumb = fileListEl("span", "file-list-thumb");
    const name = fileListEl("span", "file-list-name");
    const metaEl = fileListEl("span", "file-list-meta");
    const text = fileListEl("span", "file-list-text");
    text.append(name, metaEl);
    const up = rowButton("chevron_up", "Move up", () => move(at(), at() - 1));
    const down = rowButton("chevron_down", "Move down", () => move(at(), at() + 1));
    const remove = rowButton("close", "Remove", () => {
      noteScroll();
      onRemove(at());
    });
    const actions = fileListEl("span", "file-list-actions");
    actions.append(up, down, remove);
    const number = fileListEl("span", "file-list-number");
    row.append(fileListIcon("drag_handle", "file-list-handle"), number, thumb, text, actions);
    row.parts = { thumb, name, metaEl, number, up, down, remove };
    row.addEventListener("keydown", (ev) => {
      if (ev.target !== row || !ev.altKey) return;
      if (ev.key === "ArrowUp" || ev.key === "ArrowDown") {
        ev.preventDefault();
        ev.stopPropagation();
        const from = at();
        const to = ev.key === "ArrowUp" ? from - 1 : from + 1;
        if (to >= 0 && to < rows.length) move(from, to);
      }
    });
    // A press on a row is the drag's (it keeps the browser from focusing it),
    // so a click focuses it here.
    row.addEventListener("click", (ev) => {
      if (!ev.target.closest("button")) row.focus({ preventScroll: true });
    });
    return row;
  }

  // The row drawn as `file` at `i` of `total`: only what differs from last
  // time is touched. A file still being described (`pending`) shows its
  // name, with its thumbnail and its line as placeholders the size of what
  // they stand for.
  function updateRow(row, file, i, total) {
    const p = row.parts;
    const drawn = `${i}|${total}|${file.name}|${Boolean(file.pending)}|${file.pending ? "" : meta(file)}|${Boolean(file.thumbnail)}`;
    if (row.drawn === drawn) return;
    row.drawn = drawn;
    row.dataset.keep = `row-${i}`;
    row.setAttribute("aria-label", `${i + 1}. ${file.name}. Alt+Up or Alt+Down moves it.`);
    row.setAttribute("aria-busy", String(Boolean(file.pending)));
    p.number.textContent = String(i + 1);
    p.name.textContent = file.name;
    if (file.pending) {
      const line = fileListEl("span", "skeleton skeleton-text");
      line.style.width = "6em";
      p.metaEl.replaceChildren(line);
    } else {
      p.metaEl.textContent = meta(file);
    }
    const img = thumbnailOf(file);
    if (img) {
      if (p.thumb.firstChild !== img) p.thumb.replaceChildren(img);
      p.thumb.classList.remove("skeleton");
    } else {
      p.thumb.replaceChildren();
      p.thumb.classList.toggle("skeleton", Boolean(file.pending));
    }
    const buttons = [
      [p.up, "Move up", `up-${i}`, i === 0],
      [p.down, "Move down", `down-${i}`, i === total - 1],
      [p.remove, "Remove", `remove-${i}`, false],
    ];
    for (const [button, action, keep, disabled] of buttons) {
      button.dataset.keep = keep;
      button.disabled = disabled;
      button.setAttribute("aria-label", `${action}: ${file.name}`);
    }
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

  // After a render: the scroll position as it was (a row moved in the DOM
  // can nudge it), the focused row in view, and the rows that moved slide
  // from where they were, all before the frame paints.
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
      const from = before.get(fileListPathKey(files[i].path));
      if (from === undefined || typeof row.animate !== "function") return;
      const dy = from - row.offsetTop;
      if (!dy) return;
      row.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], {
        duration: base, easing: motion.easing(),
      });
    });
  }

  // The list area for `next`, updated in place: a file already listed keeps
  // its row (and its thumbnail image), moved if it has moved; only rows for
  // new files are made, and rows for files gone are removed.
  function build(next, { emptyText, onAdd, summary }) {
    if (drag.active()) drag.cancel();
    files = next;
    currentAdd = onAdd;
    const keys = new Set(files.map((file) => fileListPathKey(file.path)));
    for (const key of Array.from(rowsByKey.keys())) {
      if (!keys.has(key)) {
        rowsByKey.delete(key);
        images.delete(key);
      }
    }
    if (!files.length) {
      flipFrom = null;
      lastScrollTop = 0;
      rows = [];
      syncChildren(listEl, []);
      area.classList.remove("has-files");
      if (!dropZone || dropZoneText !== emptyText) {
        dropZone = buildDropZone({ text: emptyText, onAdd: () => currentAdd() });
        dropZoneText = emptyText;
      }
      syncChildren(area, [dropZone]);
      return area;
    }
    rows = files.map((file, i) => {
      const key = fileListPathKey(file.path);
      let row = rowsByKey.get(key);
      if (!row) {
        row = buildRow();
        rowsByKey.set(key, row);
      }
      updateRow(row, file, i, files.length);
      return row;
    });
    syncChildren(listEl, [...rows, bar]);
    count.textContent = summary;
    if (!stripAdd) {
      stripAdd = fileListAddButton(() => currentAdd(), "btn btn-secondary btn-sm");
      strip.append(count, hint, stripAdd);
    }
    area.classList.add("has-files");
    syncChildren(area, [strip, frame]);
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
