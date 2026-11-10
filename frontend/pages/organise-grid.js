// Organise pages' grid (docs/DESIGN_SYSTEM.md § "Organise pages"): one cell
// per page of the draft, the selection, keyboard focus and dragging. It draws
// what organise.js hands it and reports what the reader did; the draft itself
// lives in Python (reading/page_plan.py), so a move or a rotation is a call
// there, never a change made here first.
//
// Cells are buttons (role "option") in a multi-select listbox, with one tab
// stop that the arrow keys move (roving focus). Thumbnails are drawn lazily,
// as cells come near the visible part of the grid, and kept by page (not by
// position), so a move or a turn never draws one again: a turn is a CSS
// rotation of the same image.
"use strict";

// The thumbnail box is 132px square; a page is fitted into 120px of it, so
// its ring and shadow stay inside the box at any turn (portrait A4: 85×120).
const ORGANISE_THUMB_BOX_PX = 120;
// How many thumbnails are drawn at once; the rest wait their turn.
const ORGANISE_THUMB_CONCURRENCY = 3;
// Dragging within this far of the grid's top or bottom edge scrolls it, up to
// this many pixels a frame at the very edge.
const ORGANISE_AUTOSCROLL_EDGE_PX = 56;
const ORGANISE_AUTOSCROLL_MAX_PX = 18;
const ORGANISE_DRAG_TYPE = "text/x-lector-pages";
// The insertion bar: 3px wide, in the 8px column gap, from the paper's top
// to above the page number.
const ORGANISE_BAR_INSET_TOP_PX = 10;
const ORGANISE_BAR_INSET_BOTTOM_PX = 34;

function createOrganiseGrid({ grid, scroller, insertionBar, thumbnail, onMove, onSelectionChange, onDelete }) {
  let pages = [];
  let selected = new Set(); // keys
  let anchorKey = null;
  let focusKey = null;
  let dragKeys = null;
  let dropGap = null;
  let autoscrollFrame = 0;
  let autoscrollSpeed = 0;
  let frozen = false;

  const thumbs = new Map(); // "source:index" -> {src, width, height}
  const thumbQueue = [];
  let thumbsDrawing = 0;
  const cellsByKey = new Map();

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      thumbQueue.push(entry.target);
    }
    drawQueuedThumbs();
  }, { root: scroller, rootMargin: "400px 0px" });

  function positionOf(key) {
    return pages.findIndex((page) => page.key === key);
  }

  function selectedPositions() {
    return pages.map((page, i) => (selected.has(page.key) ? i : -1)).filter((i) => i >= 0);
  }

  // ---- Thumbnails ---------------------------------------------------------

  function thumbKey(page) {
    return `${page.source}:${page.index}`;
  }

  function drawQueuedThumbs() {
    while (!frozen && thumbsDrawing < ORGANISE_THUMB_CONCURRENCY && thumbQueue.length) {
      const cell = thumbQueue.shift();
      const page = cell.isConnected ? pages[Number(cell.dataset.position)] : null;
      if (!page) continue;
      if (thumbs.has(thumbKey(page))) {
        paintThumb(cell, page);
        continue;
      }
      thumbsDrawing += 1;
      thumbnail(page)
        .then((result) => {
          if (result && result.image) {
            thumbs.set(thumbKey(page), {
              src: `data:image/png;base64,${result.image}`,
              width: result.width,
              height: result.height,
            });
          }
        })
        .catch((err) => console.error("organise: a thumbnail failed", err))
        .finally(() => {
          thumbsDrawing -= 1;
          const shown = cellsByKey.get(page.key);
          if (shown) paintThumb(shown, page);
          drawQueuedThumbs();
        });
    }
  }

  // The page fitted into the box, unturned; a turn rotates the same box,
  // which fits either way since the box is square.
  function paperSize(thumb) {
    if (!thumb) return { width: 85, height: ORGANISE_THUMB_BOX_PX };
    const scale = ORGANISE_THUMB_BOX_PX / Math.max(thumb.width, thumb.height);
    return { width: Math.round(thumb.width * scale), height: Math.round(thumb.height * scale) };
  }

  function paintThumb(cell, page) {
    const thumb = thumbs.get(thumbKey(page));
    const { width, height } = paperSize(thumb);
    cell.paper.style.width = `${width}px`;
    cell.paper.style.height = `${height}px`;
    if (thumb && cell.img.src !== thumb.src) cell.img.src = thumb.src;
    cell.img.hidden = !thumb;
  }

  // ---- Cells ----------------------------------------------------------------

  function cellLabel(page, position) {
    const parts = [`Page ${position + 1}`];
    if (page.new) parts.push("new");
    if (page.turn) parts.push(`rotated ${page.turn}°`);
    if (selected.has(page.key)) parts.push("selected");
    return parts.join(", ");
  }

  function span(className, { icon, text } = {}) {
    const el = document.createElement("span");
    el.className = className;
    if (icon) el.dataset.icon = icon;
    if (text) el.textContent = text;
    return el;
  }

  // A cell: the page in its 132px box, its number, the check badge and the
  // "New" pill (the last two shown by the cell's classes).
  function buildCell(page) {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "organise-page";
    cell.setAttribute("role", "option");
    cell.draggable = true;
    cell.dataset.key = page.key;
    const img = document.createElement("img");
    img.alt = "";
    img.draggable = false;
    img.hidden = true;
    const paper = span("organise-paper");
    paper.appendChild(img);
    const box = span("organise-thumb-box");
    box.appendChild(paper);
    const check = span("organise-check", { icon: "check" });
    const pill = span("organise-new", { text: "New" });
    check.setAttribute("aria-hidden", "true");
    pill.setAttribute("aria-hidden", "true");
    cell.append(box, span("organise-page-num"), check, pill);
    cell.paper = paper;
    cell.img = img;
    cell.num = cell.children[1];
    mountIcon(check, "check");
    cell.addEventListener("click", (ev) => clickCell(ev, cell.dataset.key));
    cell.addEventListener("dragstart", (ev) => startDrag(ev, cell.dataset.key));
    cell.addEventListener("dragend", endDrag);
    return cell;
  }

  function updateCell(cell, page, position) {
    const on = selected.has(page.key);
    cell.dataset.position = String(position);
    cell.setAttribute("aria-selected", String(on));
    cell.setAttribute("aria-label", cellLabel(page, position));
    cell.classList.toggle("is-selected", on);
    cell.classList.toggle("is-new", Boolean(page.new));
    cell.tabIndex = page.key === focusKey ? 0 : -1;
    cell.num.textContent = String(position + 1);
    cell.paper.style.setProperty("--turn", `${page.turn}deg`);
    paintThumb(cell, page);
  }

  // Draws `next` (the draft from Python), keeping each page's cell — and so
  // its thumbnail and a turn's animation — across moves. `selection` is the
  // positions to select now (where moved or inserted pages went).
  function render(next, { selection } = {}) {
    pages = next;
    const keys = new Set(pages.map((page) => page.key));
    if (selection) selected = new Set(selection.map((i) => pages[i] && pages[i].key).filter(Boolean));
    selected = new Set([...selected].filter((key) => keys.has(key)));
    if (!keys.has(anchorKey)) anchorKey = null;
    if (!keys.has(focusKey)) {
      const first = selectedPositions()[0];
      focusKey = pages.length ? pages[first ?? 0].key : null;
    }
    const fragment = document.createDocumentFragment();
    const fresh = [];
    pages.forEach((page, position) => {
      let cell = cellsByKey.get(page.key);
      if (!cell) {
        cell = buildCell(page);
        cellsByKey.set(page.key, cell);
        fresh.push(cell);
      }
      updateCell(cell, page, position);
      fragment.appendChild(cell);
    });
    for (const [key, cell] of cellsByKey) {
      if (!keys.has(key)) {
        observer.unobserve(cell);
        cellsByKey.delete(key);
      }
    }
    grid.replaceChildren(fragment);
    for (const cell of fresh) observer.observe(cell);
    onSelectionChange();
  }

  function refreshSelection() {
    pages.forEach((page, position) => {
      const cell = cellsByKey.get(page.key);
      if (cell) updateCell(cell, page, position);
    });
    onSelectionChange();
  }

  function setSelection(keys, { anchor = anchorKey, focus = focusKey } = {}) {
    selected = new Set(keys);
    anchorKey = anchor;
    focusKey = focus;
    refreshSelection();
  }

  function rangeKeys(fromKey, toKey) {
    const a = positionOf(fromKey);
    const b = positionOf(toKey);
    if (a < 0 || b < 0) return [toKey];
    const [lo, hi] = a <= b ? [a, b] : [b, a];
    return pages.slice(lo, hi + 1).map((page) => page.key);
  }

  function clickCell(ev, key) {
    if (ev.shiftKey && anchorKey) {
      setSelection(rangeKeys(anchorKey, key), { focus: key });
    } else if (ev.ctrlKey || ev.metaKey) {
      const next = new Set(selected);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      setSelection(next, { anchor: key, focus: key });
    } else {
      setSelection([key], { anchor: key, focus: key });
    }
    focusCell(key);
  }

  function focusCell(key, { scroll = true } = {}) {
    const cell = cellsByKey.get(key);
    if (!cell) return;
    focusKey = key;
    for (const other of cellsByKey.values()) other.tabIndex = other === cell ? 0 : -1;
    cell.focus({ preventScroll: true });
    if (scroll) cell.scrollIntoView({ block: "nearest" });
  }

  // A press on the grid's own background (not on a page) clears the selection.
  for (const background of [grid, scroller]) {
    background.addEventListener("click", (ev) => {
      if (ev.target === background) setSelection([], { anchor: null });
    });
  }

  // ---- Keyboard -------------------------------------------------------------

  function columns() {
    const cells = grid.children;
    if (!cells.length) return 1;
    const top = cells[0].offsetTop;
    let count = 0;
    while (count < cells.length && cells[count].offsetTop === top) count += 1;
    return Math.max(1, count);
  }

  const STEP_KEYS = { ArrowLeft: -1, ArrowRight: 1 };

  function toggleFocused() {
    const next = new Set(selected);
    if (next.has(focusKey)) next.delete(focusKey);
    else next.add(focusKey);
    setSelection(next, { anchor: focusKey });
  }

  // The grid's own keys; true when the key was one of them.
  function handleKey(ev) {
    if (!pages.length) return false;
    const ctrl = ev.ctrlKey || ev.metaKey;
    if (ctrl && ev.key in STEP_KEYS) {
      const positions = selectedPositions();
      if (positions.length) onMove({ kind: "shift", positions, step: STEP_KEYS[ev.key] });
      return true;
    }
    const at = Math.max(0, positionOf(focusKey));
    let target = null;
    if (ev.key in STEP_KEYS) target = at + STEP_KEYS[ev.key];
    else if (ev.key === "ArrowUp") target = at - columns();
    else if (ev.key === "ArrowDown") target = at + columns();
    else if (ev.key === "Home") target = 0;
    else if (ev.key === "End") target = pages.length - 1;
    if (target !== null && !ctrl) {
      const key = pages[Math.min(Math.max(target, 0), pages.length - 1)].key;
      if (ev.shiftKey) {
        const anchor = anchorKey || focusKey;
        setSelection(rangeKeys(anchor, key), { anchor, focus: key });
      }
      focusCell(key);
      return true;
    }
    if (ev.key === " " && !ctrl) {
      toggleFocused();
      return true;
    }
    if (ev.key === "Delete" && !ctrl) {
      onDelete();
      return true;
    }
    return false;
  }

  grid.addEventListener("keydown", (ev) => {
    if (handleKey(ev)) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  });

  // ---- Dragging -------------------------------------------------------------

  function startDrag(ev, key) {
    if (!selected.has(key)) setSelection([key], { anchor: key, focus: key });
    dragKeys = pages.filter((page) => selected.has(page.key)).map((page) => page.key);
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData(ORGANISE_DRAG_TYPE, dragKeys.join(" "));
    for (const dragged of dragKeys) cellsByKey.get(dragged).classList.add("is-dragging");
  }

  function endDrag() {
    dragKeys = null;
    dropGap = null;
    insertionBar.hidden = true;
    stopAutoscroll();
    for (const cell of cellsByKey.values()) cell.classList.remove("is-dragging");
  }

  // Where a drop at (x, y) would land: the gap before the page under the
  // pointer when it is over that page's left half, after it over the right
  // half; the page nearest along the row when it is between pages. The bar
  // is drawn beside `cell`, on `side`.
  function dropAt(x, y) {
    let best = null;
    for (const cell of grid.children) {
      const rect = cell.getBoundingClientRect();
      if (y < rect.top || y > rect.bottom) continue;
      const distance = x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0;
      if (best && distance >= best.distance) continue;
      const after = x >= rect.left + rect.width / 2;
      const position = Number(cell.dataset.position);
      best = { cell, side: after ? "after" : "before", gap: after ? position + 1 : position, distance };
    }
    if (best) return best;
    const last = grid.lastElementChild;
    if (last && y > last.getBoundingClientRect().bottom) {
      return { cell: last, side: "after", gap: pages.length, distance: 0 };
    }
    return null;
  }

  function showInsertionBar({ cell, side }) {
    const gridRect = grid.getBoundingClientRect();
    const rect = cell.getBoundingClientRect();
    const columnGap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    const edge = side === "after" ? rect.right + columnGap / 2 : rect.left - columnGap / 2;
    const width = insertionBar.offsetWidth || 3;
    insertionBar.style.left = `${edge - gridRect.left - width / 2}px`;
    insertionBar.style.top = `${rect.top - gridRect.top + ORGANISE_BAR_INSET_TOP_PX}px`;
    insertionBar.style.height = `${rect.height - ORGANISE_BAR_INSET_TOP_PX - ORGANISE_BAR_INSET_BOTTOM_PX}px`;
    insertionBar.hidden = false;
  }

  grid.addEventListener("dragover", (ev) => {
    if (!dragKeys) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
    const drop = dropAt(ev.clientX, ev.clientY);
    if (drop) {
      dropGap = drop.gap;
      showInsertionBar(drop);
    }
    updateAutoscroll(ev.clientY);
  });

  grid.addEventListener("dragleave", (ev) => {
    if (dragKeys && !grid.contains(ev.relatedTarget)) insertionBar.hidden = true;
  });

  grid.addEventListener("drop", (ev) => {
    if (!dragKeys) return;
    ev.preventDefault();
    const drop = dropAt(ev.clientX, ev.clientY);
    const gap = drop ? drop.gap : dropGap;
    const positions = dragKeys.map(positionOf).filter((i) => i >= 0);
    endDrag();
    if (gap === null || !positions.length) return;
    // Onto the block itself: nothing to do.
    const lo = positions[0];
    const hi = positions[positions.length - 1];
    if (hi - lo + 1 === positions.length && gap >= lo && gap <= hi + 1) return;
    onMove({ kind: "move", positions, gap });
  });

  function updateAutoscroll(y) {
    const rect = scroller.getBoundingClientRect();
    const fromTop = y - rect.top;
    const fromBottom = rect.bottom - y;
    if (fromTop < ORGANISE_AUTOSCROLL_EDGE_PX) {
      autoscrollSpeed = -ORGANISE_AUTOSCROLL_MAX_PX * (1 - Math.max(fromTop, 0) / ORGANISE_AUTOSCROLL_EDGE_PX);
    } else if (fromBottom < ORGANISE_AUTOSCROLL_EDGE_PX) {
      autoscrollSpeed = ORGANISE_AUTOSCROLL_MAX_PX * (1 - Math.max(fromBottom, 0) / ORGANISE_AUTOSCROLL_EDGE_PX);
    } else {
      autoscrollSpeed = 0;
    }
    if (autoscrollSpeed && !autoscrollFrame) autoscrollFrame = requestAnimationFrame(autoscrollStep);
  }

  function autoscrollStep() {
    autoscrollFrame = 0;
    if (!dragKeys || !autoscrollSpeed) return;
    scroller.scrollTop += autoscrollSpeed;
    autoscrollFrame = requestAnimationFrame(autoscrollStep);
  }

  function stopAutoscroll() {
    autoscrollSpeed = 0;
    if (autoscrollFrame) cancelAnimationFrame(autoscrollFrame);
    autoscrollFrame = 0;
  }

  return {
    render,
    selectedPositions,
    selectAll: () => setSelection(pages.map((page) => page.key)),
    selectNone: () => setSelection([], { anchor: null }),
    allSelected: () => pages.length > 0 && selected.size === pages.length,
    pageCount: () => pages.length,
    focusPosition: (position) => pages[position] && focusCell(pages[position].key),
    // Back to the page that had focus (after a dialog over the grid closes).
    focusCurrent: () => focusKey && focusCell(focusKey, { scroll: false }),
    // Done has started: no new thumbnail requests from here on.
    freeze: () => { frozen = true; },
    // Done failed and the grid stays: thumbnails load again.
    thaw: () => {
      frozen = false;
      drawQueuedThumbs();
    },
    handleKey,
  };
}
