// Organise pages' mouse dragging (docs/DESIGN_SYSTEM.md § "Organise pages" →
// Dragging; docs/ARCHITECTURE.md § "Reordering by pointer events"): the
// shared js/pointer-reorder.js, set up for the page grid.
//
// A press becomes a drag once the pointer has moved 5px, so a click still
// selects. The pages are lifted as one preview made from the image already on
// screen (never drawn again), grabbed where they were pressed and scaled to
// 0.9; the insertion bar is vertical, in the column gap. The drop is a move
// like Ctrl+→: the grid shows it at once and Python confirms it in the
// background.
"use strict";

const ORGANISE_LIFT_SCALE = 0.9;
// The insertion bar: 3px wide, in the 8px column gap, from the paper's top
// to above the page number.
const ORGANISE_BAR_INSET_TOP_PX = 10;
const ORGANISE_BAR_INSET_BOTTOM_PX = 34;
const ORGANISE_BAR_WIDTH_PX = 3;

function createOrganiseDrag({ grid, scroller, insertionBar, motion, liveCells, cellAt, pickUp, drop }) {
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

  return createPointerReorder({
    container: grid,
    scroller,
    indicator: insertionBar,
    layout: REORDER_LAYOUTS.grid({
      width: ORGANISE_BAR_WIDTH_PX, insetTop: ORGANISE_BAR_INSET_TOP_PX, insetBottom: ORGANISE_BAR_INSET_BOTTOM_PX,
    }),
    motion,
    items: liveCells,
    itemAt: cellAt,
    pickUp: (cell) => pickUp(cell.dataset.key),
    liftedItems: (keys) => liveCells().filter((c) => keys.includes(c.dataset.key)),
    buildPreview: (cell, keys) => lift(cell, keys.length),
    previewHome: paperBox,
    previewAt: "grab",
    liftScale: ORGANISE_LIFT_SCALE,
    drop,
  });
}
