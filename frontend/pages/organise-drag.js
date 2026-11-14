// Organise pages' mouse dragging (docs/DESIGN_SYSTEM.md § "Organise pages" →
// Dragging; docs/ARCHITECTURE.md § "Organise pages"), by pointer events.
//
// Not HTML5 drag-and-drop, on purpose. A native drag starts an OS drag
// session with a drag image of its own, and its drop, even one inside the
// page, reaches pywebview's document-level drop listener (Api.bind_file_drop),
// which serialises the whole event, the thumbnails' images included, and
// sends it to Python before the frame can paint: megabytes for a short PDF,
// over a hundred for a long scanned one, and seconds of frozen grid. Pages
// dragged here touch none of that; files dropped from outside the app still
// go that way (filedrop.js), unchanged.
//
// A press becomes a drag once the pointer has moved 5px, so a click still
// selects. The pages are lifted as one preview made from the image already on
// screen (never drawn again) and moved by a transform, once a frame; the drop
// slot comes from cell rectangles measured when the drag starts (and again
// only if the grid is drawn anew or the window resized), so nothing reads
// layout while the pointer moves. The drop is a move like Ctrl+→: the grid
// shows it at once and Python confirms it in the background.
"use strict";

const ORGANISE_DRAG_THRESHOLD_PX = 5;
// Within this far of the grid's top or bottom edge, a drag scrolls it, up to
// this many pixels a frame at the very edge.
const ORGANISE_AUTOSCROLL_EDGE_PX = 48;
const ORGANISE_AUTOSCROLL_MAX_PX = 20;
const ORGANISE_LIFT_SCALE = 0.9;
// The insertion bar: 3px wide, in the 8px column gap, from the paper's top
// to above the page number.
const ORGANISE_BAR_INSET_TOP_PX = 10;
const ORGANISE_BAR_INSET_BOTTOM_PX = 34;
const ORGANISE_BAR_WIDTH_PX = 3;

function createOrganiseDrag({ grid, scroller, insertionBar, motion, liveCells, cellAt, pickUp, drop }) {
  let press = null; // { cell, pointerId, x, y }: pressed, not (yet) a drag
  let drag = null; // a drag under way; see begin()
  let frame = 0;

  // ---- Measuring: at the start, and only when the grid has changed ---------

  // Cell rectangles in viewport coordinates as they were at `measuredAt`
  // (the scroll offset then); at another offset they are just shifted.
  function measure() {
    drag.rects = liveCells().map((cell) => {
      const r = cell.getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    });
    drag.measuredAt = scroller.scrollTop || 0;
    drag.scroll = drag.measuredAt;
    const g = grid.getBoundingClientRect();
    drag.gridLeft = g.left;
    drag.gridTop = g.top;
    const s = scroller.getBoundingClientRect();
    drag.edgeTop = s.top;
    drag.edgeBottom = s.bottom;
    drag.maxScroll = Math.max(0, (scroller.scrollHeight || 0) - (scroller.clientHeight || 0));
    drag.columnGap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    drag.barAt = null;
    drag.stale = false;
  }

  // Where a drop at (x, y) would land: the gap before the page under the
  // pointer when over its left half, after it over the right half; the page
  // nearest along the row when between pages; after the last page below the
  // last row. Null in the gap between rows (the last slot stands).
  function slotAt(x, y) {
    const my = y + (drag.scroll - drag.measuredAt);
    let best = null;
    drag.rects.forEach((rect, position) => {
      if (my < rect.top || my > rect.bottom) return;
      const distance = x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0;
      if (best && distance >= best.distance) return;
      const after = x >= (rect.left + rect.right) / 2;
      best = { rect, after, gap: after ? position + 1 : position, distance };
    });
    if (best) return best;
    const last = drag.rects[drag.rects.length - 1];
    if (last && my > last.bottom) return { rect: last, after: true, gap: drag.rects.length, distance: 0 };
    return null;
  }

  // The bar sits in the grid, which scrolls with it: placed once per slot.
  function showBar(slot) {
    const id = `${slot.gap}|${slot.after}`;
    if (drag.barAt === id) return;
    drag.barAt = id;
    const { rect } = slot;
    const edge = slot.after ? rect.right + drag.columnGap / 2 : rect.left - drag.columnGap / 2;
    const height = rect.bottom - rect.top;
    insertionBar.style.left = `${edge - drag.gridLeft - ORGANISE_BAR_WIDTH_PX / 2}px`;
    insertionBar.style.top = `${rect.top - drag.gridTop + ORGANISE_BAR_INSET_TOP_PX}px`;
    insertionBar.style.height = `${height - ORGANISE_BAR_INSET_TOP_PX - ORGANISE_BAR_INSET_BOTTOM_PX}px`;
    insertionBar.hidden = false;
  }

  // ---- The lifted preview ----------------------------------------------------

  // The pressed page as it is on screen: its paper at its size and turn, the
  // same image (already decoded, so it shows in the first frame), and how many
  // pages are coming along when more than one.
  function lift(cell, count) {
    const preview = document.createElement("div");
    preview.className = "organise-lift";
    preview.setAttribute("aria-hidden", "true");
    const paper = document.createElement("span");
    paper.className = "organise-paper";
    paper.style.width = cell.paper.style.width;
    paper.style.height = cell.paper.style.height;
    paper.style.setProperty("--turn", `${cell.thumbState.css}deg`);
    if (!cell.img.hidden && cell.img.src) {
      const img = document.createElement("img");
      img.alt = "";
      img.draggable = false;
      img.src = cell.img.src;
      paper.appendChild(img);
    }
    preview.appendChild(paper);
    if (count > 1) {
      const badge = document.createElement("span");
      badge.className = "organise-lift-count";
      badge.textContent = `${count} pages`;
      preview.appendChild(badge);
    }
    return preview;
  }

  // Where the pressed page's paper is, unturned (the rectangle of a turned
  // one is its turned outline; its centre is the same either way).
  function paperBox(cell) {
    const r = cell.paper.getBoundingClientRect();
    const width = parseFloat(cell.paper.style.width) || r.right - r.left;
    const height = parseFloat(cell.paper.style.height) || r.bottom - r.top;
    const cx = (r.left + r.right) / 2;
    const cy = (r.top + r.bottom) / 2;
    return { left: cx - width / 2, top: cy - height / 2 };
  }

  function liftTransform(x, y) {
    return `translate3d(${x - drag.grabX}px, ${y - drag.grabY}px, 0) scale(${ORGANISE_LIFT_SCALE})`;
  }

  // ---- Once a frame ----------------------------------------------------------

  function schedule() {
    if (!frame) frame = requestAnimationFrame(step);
  }

  function step() {
    frame = 0;
    if (!drag) return;
    if (drag.stale) measure();
    autoscroll();
    place();
    if (drag.speed) schedule();
  }

  function place() {
    const { x, y } = drag.pointer;
    drag.preview.style.transform = liftTransform(x, y);
    const slot = slotAt(x, y);
    if (!slot) return;
    drag.gap = slot.gap;
    showBar(slot);
  }

  // Faster the closer to the edge; stops at either end of the grid.
  function autoscroll() {
    const { y } = drag.pointer;
    const fromTop = y - drag.edgeTop;
    const fromBottom = drag.edgeBottom - y;
    let speed = 0;
    if (fromTop < ORGANISE_AUTOSCROLL_EDGE_PX) {
      speed = -ORGANISE_AUTOSCROLL_MAX_PX * (1 - Math.max(fromTop, 0) / ORGANISE_AUTOSCROLL_EDGE_PX);
    } else if (fromBottom < ORGANISE_AUTOSCROLL_EDGE_PX) {
      speed = ORGANISE_AUTOSCROLL_MAX_PX * (1 - Math.max(fromBottom, 0) / ORGANISE_AUTOSCROLL_EDGE_PX);
    }
    drag.speed = 0;
    if (!speed) return;
    const next = Math.min(Math.max(drag.scroll + speed, 0), drag.maxScroll);
    if (next === drag.scroll) return;
    scroller.scrollTop = next;
    // Read back before anything is written this frame: the browser may round.
    drag.scroll = scroller.scrollTop || next;
    drag.speed = speed;
  }

  // ---- Pointer and keys --------------------------------------------------------

  function onPointerDown(ev) {
    if (press || ev.button !== 0 || ev.isPrimary === false) return;
    const cell = cellAt(ev.target);
    if (!cell) return;
    // No native image drag or text selection from a press on a page; the
    // click that follows still selects (and focuses) it.
    ev.preventDefault();
    press = { cell, pointerId: ev.pointerId, x: ev.clientX, y: ev.clientY };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
  }

  function onPointerMove(ev) {
    if (!press || ev.pointerId !== press.pointerId) return;
    if (!drag) {
      const moved = Math.hypot(ev.clientX - press.x, ev.clientY - press.y);
      if (moved >= ORGANISE_DRAG_THRESHOLD_PX) begin(ev);
      return;
    }
    drag.pointer = { x: ev.clientX, y: ev.clientY };
    schedule();
  }

  function onPointerUp(ev) {
    if (!press || ev.pointerId !== press.pointerId) return;
    if (!drag) {
      end(); // a click: the click event selects
      return;
    }
    drag.pointer = { x: ev.clientX, y: ev.clientY };
    if (drag.stale) measure();
    const slot = slotAt(ev.clientX, ev.clientY);
    const gap = slot ? slot.gap : drag.gap;
    const { keys } = drag;
    swallowNextClick();
    end();
    if (gap !== null) drop(keys, gap, ev.timeStamp);
  }

  function onPointerCancel(ev) {
    if (press && ev.pointerId === press.pointerId) cancel(ev);
  }

  // Esc ends the drag, not Organise: taken before the view's own keys.
  function onKey(ev) {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    ev.stopPropagation();
    cancel(ev);
  }

  // Scrolled under a held drag (the wheel, or auto-scroll's own step): the
  // slot under a still pointer changes with it.
  function onScroll() {
    if (!drag) return;
    drag.scroll = scroller.scrollTop || 0;
    schedule();
  }

  function onResize() {
    if (!drag) return;
    drag.stale = true;
    schedule();
  }

  // The click that ends a drag would land on the grid's background (the
  // pointer is captured there, or the press and release were on different
  // pages) and clear the selection. It comes in the same task as the
  // pointerup, so a timer is enough to let go of it.
  function swallowNextClick() {
    const swallow = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      window.removeEventListener("click", swallow, true);
    };
    window.addEventListener("click", swallow, true);
    setTimeout(() => window.removeEventListener("click", swallow, true), 0);
  }

  function begin(ev) {
    const { cell, pointerId } = press;
    const keys = pickUp(cell.dataset.key);
    if (!keys.length) {
      end();
      return;
    }
    const box = paperBox(cell);
    drag = {
      keys,
      home: box,
      grabX: press.x - box.left,
      grabY: press.y - box.top,
      pointer: { x: ev.clientX, y: ev.clientY },
      gap: null,
      speed: 0,
      lifted: liveCells().filter((c) => keys.includes(c.dataset.key)),
      preview: lift(cell, keys.length),
    };
    measure();
    try {
      grid.setPointerCapture(pointerId);
    } catch {
      // Not a live pointer (it may have just been released): the window's
      // listeners still hear it.
    }
    grid.classList.add("is-pointer-dragging");
    for (const lifted of drag.lifted) lifted.classList.add("is-dragging");
    drag.preview.style.transformOrigin = `${drag.grabX}px ${drag.grabY}px`;
    document.body.appendChild(drag.preview);
    // Under the pointer from the drag's first frame, not the one after.
    place();
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", cancel);
    window.addEventListener("resize", onResize);
    scroller.addEventListener("scroll", onScroll);
  }

  // Everything a press or drag set up, undone. The preview is left to the
  // caller when `keepPreview` (a cancel animates it home first).
  function end({ keepPreview = false } = {}) {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerCancel);
    if (drag) {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("blur", cancel);
      window.removeEventListener("resize", onResize);
      scroller.removeEventListener("scroll", onScroll);
      try {
        if (grid.hasPointerCapture(press.pointerId)) grid.releasePointerCapture(press.pointerId);
      } catch {
        // Already released.
      }
      grid.classList.remove("is-pointer-dragging");
      for (const lifted of drag.lifted) lifted.classList.remove("is-dragging");
      insertionBar.hidden = true;
      if (!keepPreview) drag.preview.remove();
    }
    drag = null;
    press = null;
  }

  // A drag called off while the button is still down (Esc, the window losing
  // focus): its release is no click either. Forgotten at the next press, in
  // case the release happened outside the window.
  function swallowRelease() {
    const onUp = () => {
      forget();
      swallowNextClick();
    };
    const forget = () => {
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointerdown", forget, true);
    };
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointerdown", forget, true);
  }

  // Nothing changes; the preview goes back to the page it came from.
  function cancel(ev) {
    if (!drag) {
      end();
      return;
    }
    const { preview, pointer, home } = drag;
    const from = liftTransform(pointer.x, pointer.y);
    const to = `translate3d(${home.left}px, ${home.top - (drag.scroll - drag.measuredAt)}px, 0) scale(1)`;
    end({ keepPreview: true });
    if (!ev || ev.type !== "pointercancel") swallowRelease();
    if (motion.reduced() || typeof preview.animate !== "function") {
      preview.remove();
      return;
    }
    // Over --dur-return (150ms), the drag's own token in theme.css.
    const back = preview.animate([{ transform: from }, { transform: to }], {
      duration: motion.tokenMs("--dur-return"), easing: motion.easing(), fill: "forwards",
    });
    back.finished.then(() => preview.remove(), () => preview.remove());
  }

  grid.addEventListener("pointerdown", onPointerDown);

  return {
    // The grid was drawn anew under a drag: measure again before using it.
    refresh() {
      if (!drag) return;
      drag.stale = true;
      schedule();
    },
    active: () => Boolean(drag),
    cancel: () => cancel(),
  };
}
