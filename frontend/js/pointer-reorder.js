// Reordering by dragging, with pointer events: one module for Organise pages'
// grid (pages/organise-drag.js) and the file tools' list (js/file-list.js,
// Convert to PDF and Combine files). docs/DESIGN_SYSTEM.md § "Reordering by
// dragging"; docs/ARCHITECTURE.md § "Reordering by pointer events".
//
// Not HTML5 drag-and-drop, on purpose. A native drag starts an OS drag
// session with a drag image of its own, and its drop, even one inside the
// page, reaches pywebview's document-level drop listener (Api.bind_file_drop),
// which serialises the whole event, images included, and sends it to Python
// before the frame can paint: seconds of frozen grid or list. Items dragged
// here touch none of that; files dropped from outside the app still go that
// way (filedrop.js), unchanged.
//
// A press becomes a drag once the pointer has moved 5px, so a click still
// clicks. The item is lifted as one preview made from what is already on
// screen (an image is never drawn again) and moved by a transform, once a
// frame; the drop slot comes from item rectangles measured when the drag
// starts (and again only if the items are drawn anew or the window resized),
// so nothing reads layout while the pointer moves. Near the scroller's top or
// bottom edge the drag scrolls it. Esc, a cancelled pointer or the window
// losing focus calls it off and the preview goes home. What a drop does is
// the caller's (`drop`), and so is the motion after it (FLIP).
//
// The two layouts differ only in where a slot is and how the insertion bar
// is drawn (REORDER_LAYOUTS): Organise's grid has a vertical bar in the column
// gap, chosen by the pointer's x within a row; the list a horizontal line
// between rows, chosen by its y.
"use strict";

const REORDER_DRAG_THRESHOLD_PX = 5;
// Within this far of the scroller's top or bottom edge, a drag scrolls it, up
// to this many pixels a frame at the very edge.
const REORDER_AUTOSCROLL_EDGE_PX = 48;
const REORDER_AUTOSCROLL_MAX_PX = 20;

// The defaults when theme.css's motion tokens can't be read.
const REORDER_MOTION_DEFAULTS = { "--dur-reorder": 180, "--dur-quick": 140, "--dur-return": 150 };
const REORDER_EASE_DEFAULT = "cubic-bezier(0.2, 0.8, 0.2, 1)";

// What a drag needs of motion (Organise passes its own, which has the same
// three): reduced motion, a duration token in ms, the easing.
function createReorderMotion() {
  function token(name) {
    const style = typeof getComputedStyle === "function" ? getComputedStyle(document.documentElement) : null;
    return style && typeof style.getPropertyValue === "function" ? style.getPropertyValue(name).trim() : "";
  }
  return {
    reduced: () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches,
    tokenMs(name) {
      const raw = token(name);
      const value = parseFloat(raw);
      if (!Number.isFinite(value)) return REORDER_MOTION_DEFAULTS[name];
      return raw.endsWith("ms") ? value : value * 1000;
    },
    easing: () => token("--ease-out") || REORDER_EASE_DEFAULT,
  };
}

const REORDER_LAYOUTS = {
  // Organise's grid: the gap before the item under the pointer when over its
  // left half, after it over the right half; the item nearest along the row
  // when between items; after the last below the last row. Null in the gap
  // between rows (the last slot stands). The bar: `width` wide, in the column
  // gap, from `insetTop` below the item's top to `insetBottom` above its
  // bottom.
  grid({ width, insetTop, insetBottom }) {
    return {
      measure(container) {
        return { columnGap: parseFloat(getComputedStyle(container).columnGap) || 0 };
      },
      slotAt(rects, x, y) {
        let best = null;
        rects.forEach((rect, position) => {
          if (y < rect.top || y > rect.bottom) return;
          const distance = x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0;
          if (best && distance >= best.distance) return;
          const after = x >= (rect.left + rect.right) / 2;
          best = { rect, after, gap: after ? position + 1 : position, distance };
        });
        if (best) return best;
        const last = rects[rects.length - 1];
        if (last && y > last.bottom) return { rect: last, after: true, gap: rects.length, distance: 0 };
        return null;
      },
      placeBar(bar, slot, origin, extra) {
        const { rect } = slot;
        const edge = slot.after ? rect.right + extra.columnGap / 2 : rect.left - extra.columnGap / 2;
        bar.style.left = `${edge - origin.left - width / 2}px`;
        bar.style.top = `${rect.top - origin.top + insetTop}px`;
        bar.style.height = `${rect.bottom - rect.top - insetTop - insetBottom}px`;
      },
    };
  },

  // The file list: before the row under the pointer over its top half, after
  // it over the bottom half; above the first row, the first slot; below the
  // last, the last. Never null. The bar: `thickness` tall, the rows' width,
  // centred on the line between two rows.
  list({ thickness }) {
    return {
      measure(container) {
        return { rowGap: parseFloat(getComputedStyle(container).rowGap) || 0 };
      },
      slotAt(rects, x, y) {
        for (let position = 0; position < rects.length; position++) {
          const rect = rects[position];
          if (y <= rect.bottom || position === rects.length - 1) {
            const after = y >= (rect.top + rect.bottom) / 2;
            return { rect, after, gap: after ? position + 1 : position, distance: 0 };
          }
        }
        return null;
      },
      placeBar(bar, slot, origin, extra) {
        const { rect } = slot;
        const edge = slot.after ? rect.bottom + extra.rowGap / 2 : rect.top - extra.rowGap / 2;
        bar.style.left = `${rect.left - origin.left}px`;
        bar.style.width = `${rect.right - rect.left}px`;
        bar.style.top = `${edge - origin.top - thickness / 2}px`;
      },
    };
  },
};

// container: where presses start, pointer capture is held and the bar is
//   placed (in its coordinates); it scrolls inside `scroller`.
// indicator: the insertion bar, a hidden child of `container`.
// layout: REORDER_LAYOUTS.grid(...) or .list(...).
// items(): the items in order. itemAt(target): the item a press is on, or
//   null when it isn't one (a button in a row, the background).
// pickUp(item): the keys being dragged (the selection, say), in order; none
//   means no drag. liftedItems(keys): the items that stay behind, dimmed.
// buildPreview(item, keys): the lifted element, made from what is on screen.
// previewHome(item): where the preview sits at rest (its top left, in the
//   viewport), which is where a called-off drag sends it back to.
// previewAt: "grab", the preview stays under the pointer where it was grabbed
//   (scaled by `liftScale` about that point); or { dx, dy }, it hangs that far
//   right of and below the pointer.
// drop(keys, gap, timeStamp): a release on slot `gap` (0 = before the first).
function createPointerReorder({
  container, scroller, indicator, layout, motion = createReorderMotion(),
  items, itemAt, pickUp, liftedItems, buildPreview, previewHome,
  previewAt = "grab", liftScale = 1, drop,
  draggingClass = "is-pointer-dragging", liftedClass = "is-dragging",
}) {
  let press = null; // { item, pointerId, x, y }: pressed, not (yet) a drag
  let drag = null; // a drag under way; see begin()
  let frame = 0;

  // ---- Measuring: at the start, and only when the items have changed -------

  // Item rectangles in viewport coordinates as they were at `measuredAt`
  // (the scroll offset then); at another offset they are just shifted.
  function measure() {
    drag.rects = items().map((item) => {
      const r = item.getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    });
    drag.measuredAt = scroller.scrollTop || 0;
    drag.scroll = drag.measuredAt;
    const c = container.getBoundingClientRect();
    drag.origin = { left: c.left, top: c.top };
    const s = scroller.getBoundingClientRect();
    drag.edgeTop = s.top;
    drag.edgeBottom = s.bottom;
    drag.maxScroll = Math.max(0, (scroller.scrollHeight || 0) - (scroller.clientHeight || 0));
    drag.extra = layout.measure(container);
    drag.barAt = null;
    drag.stale = false;
  }

  function slotAt(x, y) {
    return layout.slotAt(drag.rects, x, y + (drag.scroll - drag.measuredAt));
  }

  // The bar sits in the container, which scrolls with it: placed once per slot.
  function showBar(slot) {
    const id = `${slot.gap}|${slot.after}`;
    if (drag.barAt === id) return;
    drag.barAt = id;
    layout.placeBar(indicator, slot, drag.origin, drag.extra);
    indicator.hidden = false;
  }

  // ---- The lifted preview ------------------------------------------------------

  function previewTransform(x, y) {
    if (previewAt === "grab") return `translate3d(${x - drag.grabX}px, ${y - drag.grabY}px, 0) scale(${liftScale})`;
    return `translate3d(${x + previewAt.dx}px, ${y + previewAt.dy}px, 0)`;
  }

  // ---- Once a frame ----------------------------------------------------------------

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
    drag.preview.style.transform = previewTransform(x, y);
    const slot = slotAt(x, y);
    if (!slot) return;
    drag.gap = slot.gap;
    showBar(slot);
  }

  // Faster the closer to the edge; stops at either end.
  function autoscroll() {
    const { y } = drag.pointer;
    const fromTop = y - drag.edgeTop;
    const fromBottom = drag.edgeBottom - y;
    let speed = 0;
    if (fromTop < REORDER_AUTOSCROLL_EDGE_PX) {
      speed = -REORDER_AUTOSCROLL_MAX_PX * (1 - Math.max(fromTop, 0) / REORDER_AUTOSCROLL_EDGE_PX);
    } else if (fromBottom < REORDER_AUTOSCROLL_EDGE_PX) {
      speed = REORDER_AUTOSCROLL_MAX_PX * (1 - Math.max(fromBottom, 0) / REORDER_AUTOSCROLL_EDGE_PX);
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

  // ---- Pointer and keys ----------------------------------------------------------

  function onPointerDown(ev) {
    if (press || ev.button !== 0 || ev.isPrimary === false) return;
    const item = itemAt(ev.target);
    if (!item) return;
    // No native image drag or text selection from a press on an item; the
    // click that follows still clicks it.
    ev.preventDefault();
    press = { item, pointerId: ev.pointerId, x: ev.clientX, y: ev.clientY };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
  }

  function onPointerMove(ev) {
    if (!press || ev.pointerId !== press.pointerId) return;
    if (!drag) {
      const moved = Math.hypot(ev.clientX - press.x, ev.clientY - press.y);
      if (moved >= REORDER_DRAG_THRESHOLD_PX) begin(ev);
      return;
    }
    drag.pointer = { x: ev.clientX, y: ev.clientY };
    schedule();
  }

  function onPointerUp(ev) {
    if (!press || ev.pointerId !== press.pointerId) return;
    if (!drag) {
      end(); // a click: the click event does the rest
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

  // Esc ends the drag, not what it is in: taken before the view's own keys.
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

  // The click that ends a drag would land on the container's background (the
  // pointer is captured there, or the press and release were on different
  // items) and act there. It comes in the same task as the pointerup, so a
  // timer is enough to let go of it.
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
    const { item, pointerId } = press;
    const keys = pickUp(item);
    if (!keys.length) {
      end();
      return;
    }
    const home = previewHome(item);
    drag = {
      keys,
      home,
      grabX: press.x - home.left,
      grabY: press.y - home.top,
      pointer: { x: ev.clientX, y: ev.clientY },
      gap: null,
      speed: 0,
      lifted: liftedItems(keys),
      preview: buildPreview(item, keys),
    };
    measure();
    try {
      container.setPointerCapture(pointerId);
    } catch {
      // Not a live pointer (it may have just been released): the window's
      // listeners still hear it.
    }
    container.classList.add(draggingClass);
    for (const lifted of drag.lifted) lifted.classList.add(liftedClass);
    if (previewAt === "grab") drag.preview.style.transformOrigin = `${drag.grabX}px ${drag.grabY}px`;
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
        if (container.hasPointerCapture(press.pointerId)) container.releasePointerCapture(press.pointerId);
      } catch {
        // Already released.
      }
      container.classList.remove(draggingClass);
      for (const lifted of drag.lifted) lifted.classList.remove(liftedClass);
      indicator.hidden = true;
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

  // Nothing changes; the preview goes back to the item it came from.
  function cancel(ev) {
    if (!drag) {
      end();
      return;
    }
    const { preview, pointer, home } = drag;
    const from = previewTransform(pointer.x, pointer.y);
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

  container.addEventListener("pointerdown", onPointerDown);

  return {
    // The items were drawn anew under a drag: measure again before using them.
    refresh() {
      if (!drag) return;
      drag.stale = true;
      schedule();
    },
    active: () => Boolean(drag),
    cancel: () => cancel(),
  };
}
