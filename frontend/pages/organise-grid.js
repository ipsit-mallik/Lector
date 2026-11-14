// Organise pages' grid (docs/DESIGN_SYSTEM.md § "Organise pages"): one cell
// per page of the draft, the selection, keyboard focus and dragging. It draws
// what organise.js hands it, which is the page's own prediction of the draft
// (organise-plan.js) the moment an action is taken, and Python's draft when
// the two differ; it reports what the reader did.
//
// Cells are buttons (role "option") in a multi-select listbox, with one tab
// stop that the arrow keys move (roving focus). Each cell belongs to a page
// (its key: the source page), never to a position, so a change reorders the
// cells already there: no cell or image is made again for a move, and
// thumbnails (organise-thumbs.js) are never fetched again for one. Changes
// are animated by organise-motion.js; mouse dragging is organise-drag.js.
"use strict";

function createOrganiseGrid({
  grid, scroller, insertionBar, thumbnail, onMove, onSelectionChange, onDelete,
  onRendered = () => {}, onSettled = () => {},
}) {
  let pages = [];
  let positions = new Map(); // key -> position
  let selected = new Set(); // keys
  let anchorKey = null;
  let focusKey = null;

  const cellsByKey = new Map();
  // Cells of pages taken out of the draft, kept so that an Undo brings back
  // the same cell, image and all.
  const retired = new Map();
  const motion = createOrganiseMotion({ grid, scroller, onSettled });
  const thumbs = createOrganiseThumbs({
    scroller,
    thumbnail,
    pageOf: (cell) => pages[positions.get(cell.dataset.key)] || null,
    cellOf: (key) => cellsByKey.get(key) || null,
    turnMs: motion.turnMs,
  });

  function positionOf(key) {
    return positions.has(key) ? positions.get(key) : -1;
  }

  function selectedPositions() {
    return pages.map((page, i) => (selected.has(page.key) ? i : -1)).filter((i) => i >= 0);
  }

  // The cells of the draft, in order (not the ones fading out of it).
  function liveCells() {
    return pages.map((page) => cellsByKey.get(page.key));
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
    cell.thumbState = thumbs.newState();
    mountIcon(check, "check");
    cell.addEventListener("click", (ev) => clickCell(ev, cell.dataset.key));
    return cell;
  }

  // A cell drawn as `page` at `position`. One whose number, selection, focus
  // and turn are as last drawn is left alone: a move touches the few cells
  // it moved, not all of them (a long document has hundreds).
  function updateCell(cell, page, position) {
    const on = selected.has(page.key);
    const drawn = `${position}|${on}|${page.key === focusKey}|${page.turn}|${Boolean(page.new)}`;
    if (cell.drawn === drawn) return;
    cell.drawn = drawn;
    cell.dataset.position = String(position);
    cell.setAttribute("aria-selected", String(on));
    cell.setAttribute("aria-label", cellLabel(page, position));
    cell.classList.toggle("is-selected", on);
    cell.classList.toggle("is-new", Boolean(page.new));
    cell.tabIndex = page.key === focusKey ? 0 : -1;
    cell.num.textContent = String(position + 1);
    thumbs.paint(cell, page);
  }

  // The grid's children made `order` (with the cells fading out kept where
  // they are), moving as few cells as it simply can: walking back from the
  // end, a cell is moved only when the one after it isn't already right.
  function arrange(order, leaving) {
    const rest = [...order];
    const wanted = [];
    for (const child of [...grid.children]) {
      if (leaving.has(child)) wanted.push(child);
      else if (rest.length) wanted.push(rest.shift());
    }
    wanted.push(...rest);
    let next = null;
    for (let i = wanted.length - 1; i >= 0; i -= 1) {
      const cell = wanted[i];
      if (cell.parentNode !== grid || cell.nextElementSibling !== next) grid.insertBefore(cell, next);
      next = cell;
    }
  }

  // Cells of pages no longer in the draft: out of the grid's bookkeeping at
  // once (so nothing can select, focus or drop on them), still on screen
  // while they fade.
  function retire(cell) {
    cellsByKey.delete(cell.dataset.key);
    retired.set(cell.dataset.key, cell);
    thumbs.forget(cell);
    cell.classList.add("is-leaving");
    cell.setAttribute("aria-hidden", "true");
    cell.tabIndex = -1;
  }

  // A page back in the draft: its old cell if there is one, else a new one.
  function cellFor(page) {
    const kept = retired.get(page.key);
    if (!kept) return buildCell(page);
    retired.delete(page.key);
    kept.classList.remove("is-leaving");
    kept.removeAttribute("aria-hidden");
    return kept;
  }

  // Draws `next` (a plan: the page's prediction or Python's draft), keeping
  // each page's cell. `selection` is the positions to select now, `focus`
  // the position to move focus to; `lead` the positions whose pages were
  // moved (they land first); `rotated` the positions just turned by Rotate,
  // whose image at the new turn is fetched in the background.
  function render(next, { selection, focus, lead = [], rotated = [] } = {}) {
    motion.finish();
    const animate = pages.length > 0; // opening draws the grid without motion
    const hadFocus = grid.contains(document.activeElement);
    const first = animate ? motion.snapshot(liveCells()) : new Map();
    pages = next;
    positions = new Map(pages.map((page, i) => [page.key, i]));
    if (selection) selected = new Set(selection.map((i) => pages[i] && pages[i].key).filter(Boolean));
    selected = new Set([...selected].filter((key) => positions.has(key)));
    if (!positions.has(anchorKey)) anchorKey = null;
    if (typeof focus === "number" && pages[focus]) focusKey = pages[focus].key;
    if (!positions.has(focusKey)) {
      const firstSelected = selectedPositions()[0];
      focusKey = pages.length ? pages[firstSelected ?? 0].key : null;
    }
    const gone = [...cellsByKey.values()].filter((cell) => !positions.has(cell.dataset.key));
    gone.forEach(retire);
    const entering = new Set();
    const order = pages.map((page, position) => {
      let cell = cellsByKey.get(page.key);
      if (!cell) {
        cell = cellFor(page);
        cellsByKey.set(page.key, cell);
        entering.add(cell);
      }
      updateCell(cell, page, position);
      return cell;
    });
    arrange(order, new Set(gone));
    for (const cell of entering) thumbs.observe(cell);
    for (const position of rotated) {
      const page = pages[position];
      if (page) thumbs.fetchTurned(cellsByKey.get(page.key), page);
    }
    if (hadFocus || typeof focus === "number") focusCell(focusKey, { scroll: typeof focus === "number" });
    if (animate) {
      const leadCells = new Set(lead.map((i) => pages[i] && cellsByKey.get(pages[i].key)).filter(Boolean));
      motion.slide(first, order, { lead: leadCells, entering });
    }
    if (gone.length) {
      motion.leave(gone, (slide) => {
        const before = slide ? motion.snapshot(liveCells()) : null;
        for (const cell of gone) cell.remove();
        if (before) motion.slide(before, liveCells());
      });
    }
    drag.refresh();
    onRendered();
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
    if (!positions.has(key)) return; // a deleted page, fading out
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
    const cells = liveCells();
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
      const chosen = selectedPositions();
      if (chosen.length) onMove({ kind: "shift", positions: chosen, step: STEP_KEYS[ev.key], at: ev.timeStamp });
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

  // ---- Dragging (organise-drag.js) ---------------------------------------

  // The live cell `target` is in, if any (not one fading out).
  function cellAt(target) {
    for (let n = target; n && n !== grid; n = n.parentNode) {
      if (n.parentNode === grid) return positions.has(n.dataset.key) ? n : null;
    }
    return null;
  }

  // A drag picks up the selection, in its order; a page pressed outside it
  // is selected alone first. Where the cells are drawn is where they are:
  // no motion is left running under a drag.
  function pickUp(key) {
    if (!positions.has(key)) return [];
    motion.finish();
    if (!selected.has(key)) setSelection([key], { anchor: key, focus: key });
    return pages.filter((page) => selected.has(page.key)).map((page) => page.key);
  }

  // Dropped in `gap`: a move, unless onto the block's own place.
  function dropKeys(keys, gap, at) {
    const moved = keys.map(positionOf).filter((i) => i >= 0).sort((a, b) => a - b);
    if (!moved.length) return;
    const lo = moved[0];
    const hi = moved[moved.length - 1];
    if (hi - lo + 1 === moved.length && gap >= lo && gap <= hi + 1) return;
    onMove({ kind: "move", positions: moved, gap, at });
  }

  const drag = createOrganiseDrag({
    grid, scroller, insertionBar, motion, liveCells, cellAt, pickUp, drop: dropKeys,
  });

  return {
    render,
    selectedPositions,
    selectAll: () => setSelection(pages.map((page) => page.key)),
    selectNone: () => setSelection([], { anchor: null }),
    allSelected: () => pages.length > 0 && selected.size === pages.length,
    pageCount: () => pages.length,
    focusPosition: (position) => pages[position] && focusCell(pages[position].key),
    // What the delete confirmation shows of a page: its thumbnail if drawn
    // (`src`, `width`, `height`, or null), the turn it is shown at and its
    // number.
    pageView: (position) => {
      const page = pages[position];
      if (!page) return null;
      return { ...thumbs.view(cellsByKey.get(page.key), page), number: position + 1 };
    },
    // Back to the page that had focus (after a dialog over the grid closes).
    focusCurrent: () => focusKey && focusCell(focusKey, { scroll: false }),
    // Done has started: no new thumbnail requests from here on.
    freeze: thumbs.freeze,
    // Done failed and the grid stays: thumbnails load again.
    thaw: thumbs.thaw,
    handleKey,
    // A drag under way ends with nothing changed (Organise is closing).
    cancelDrag: () => drag.cancel(),
  };
}
