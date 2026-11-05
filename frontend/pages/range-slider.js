// The reader's slider: a range input with tick marks under it (reading.css,
// "The slider"), used by the zoom popover and by the highlight opacity
// slider in the highlight settings card and the highlight editor. One behaviour for
// all of them, so the three cannot drift:
//
// * A press on the input starts a drag (the thumb grows, .is-dragging) until
//   the pointer is let go anywhere. Only a drag snaps (the caller's
//   onInput(value, dragging) decides where to), so the arrow keys can still
//   step across a snap point.
// * A double-click on the thumb itself (not the track beside it) resets.
// * A click on a tick jumps to its value (data-value).
// * The filled part of the track runs to the thumb: --range-fill, 0 to 1.
//
// It knows nothing about what the value means: the zoom slider's positions
// are logarithmic (zoom-fit.js), the opacity slider's are percent.

// The thumb's diameter (reading.css's --range-thumb) and how near its centre
// a double-click must land: half the input's 20px hit area, wider than the
// thumb.
const RANGE_THUMB_PX = 12;
const RANGE_THUMB_HIT_PX = 10;

// How far along the track the input's value is, 0 to 1.
function rangeFraction(input) {
  const min = Number(input.min || 0);
  const max = Number(input.max || 100);
  return max > min ? (Number(input.value) - min) / (max - min) : 0;
}

function paintRangeFill(input) {
  input.style.setProperty("--range-fill", String(rangeFraction(input)));
}

// Each tick placed where `fractionOf(its value)` falls along the track.
function placeRangeTicks(ticks, fractionOf) {
  for (const tick of ticks.querySelectorAll(".range-tick")) {
    tick.style.setProperty("--at", String(fractionOf(Number(tick.dataset.value))));
  }
}

// Whether a pointer at `clientX` is on the thumb.
function isOnRangeThumb(input, clientX) {
  const rect = input.getBoundingClientRect();
  const thumbX = rect.left + RANGE_THUMB_PX / 2 + rangeFraction(input) * (rect.width - RANGE_THUMB_PX);
  return Math.abs(clientX - thumbX) <= RANGE_THUMB_HIT_PX;
}

// hooks: {
//   onInput(value, dragging)  the input moved, by a drag or a key,
//   onReset()                 a double-click on the thumb,
//   onTick(value)             a click on a tick,
//   onRelease()               a drag ended (optional),
// }
// Returns { isDragging() }.
function bindRangeSlider(input, ticks, hooks) {
  let dragging = false;
  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    input.classList.remove("is-dragging");
    if (hooks.onRelease) hooks.onRelease();
  };
  input.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    dragging = true;
    input.classList.add("is-dragging");
  });
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  input.addEventListener("input", () => hooks.onInput(Number(input.value), dragging));
  input.addEventListener("dblclick", (ev) => {
    if (isOnRangeThumb(input, ev.clientX)) hooks.onReset();
  });
  if (ticks) {
    for (const tick of ticks.querySelectorAll(".range-tick")) {
      tick.addEventListener("click", () => hooks.onTick(Number(tick.dataset.value)));
    }
  }
  return { isDragging: () => dragging };
}
