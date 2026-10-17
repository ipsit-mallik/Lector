// The topbar's zoom value and the popover it opens: a logarithmic slider with
// ticks at 50%, 100% and 200%, and three chips, Fit width, Fit page and 100%.
// Also owns the zoom mode. Fit width and Fit page are modes rather than
// one-off sizes: until the reader picks a percentage (the slider or a tick,
// − / +, Ctrl+scroll, 100%), the page is fitted again whenever the space for
// it changes (reading.js calls refitZoom()). The arithmetic is zoom-fit.js.
// Nothing is stored: every PDF opens at its remembered percentage, with no
// mode on.
//
// reading.js does the zooming itself and hands it over through
// configureZoomControl(), the same split as find-bar.js:
//   setZoomPct(pct)   zoom to a percentage (the backend call and the preview)
//   fitGeometry()     resolves to { areaWidth, areaHeight, pageWidth,
//                     pageHeight }: the page area in CSS pixels and the
//                     current page in PDF points
//   onClose()         after the popover closes (Focus mode's header may go)

const zoomLabel = document.getElementById("zoomLabel");
const zoomPopover = document.getElementById("zoomPopover");
const zoomSlider = document.getElementById("zoomSlider");
const zoomFitWidthBtn = document.getElementById("zoomFitWidthBtn");
const zoomFitPageBtn = document.getElementById("zoomFitPageBtn");
const zoomActualBtn = document.getElementById("zoomActualBtn");
const zoomTicks = document.getElementById("zoomTicks");

const ZOOM_ACTUAL_PCT = 100;
// How near the thumb's centre a double-click has to land to reset to 100%:
// half the 20px hit area, wider than the 12px thumb (reading.css).
const ZOOM_THUMB_HIT_PX = 10;
const ZOOM_THUMB_PX = 12;

let zoomHooks = {
  setZoomPct: async () => {},
  fitGeometry: async () => null,
  onClose: () => {},
};
let zoomMode = null; // ZOOM_FIT_WIDTH, ZOOM_FIT_PAGE, or null for a percentage
let zoomRange = { min: 25, max: 400 };
let zoomPct = 100; // to 0.01%: a fit is rarely a whole percent
// Bumped by every fit and every change of mode, so a fit still waiting on its
// measurements is dropped once something newer has happened.
let zoomFitSeq = 0;
// A pointer is down on the slider: only a drag snaps to 100%, so the arrow
// keys can still step across it.
let zoomDragging = false;

function configureZoomControl(hooks) {
  zoomHooks = { ...zoomHooks, ...hooks };
}

function getZoomMode() {
  return zoomMode;
}

// The state the reader just received: the zoom and its range.
function updateZoomControl(state) {
  zoomRange = { min: state.zoom_min_pct, max: state.zoom_max_pct };
  zoomPct = Math.round(state.zoom * 10000) / 100;
  for (const tick of zoomTicks.querySelectorAll(".zoom-tick")) {
    tick.style.setProperty("--at", String(zoomTrackFraction(Number(tick.dataset.zoom), zoomRange.min, zoomRange.max)));
  }
  renderZoomControl();
}

function renderZoomControl() {
  const text = zoomLabelText(zoomMode, zoomPct);
  const shown = Math.round(zoomPct);
  zoomLabel.textContent = text;
  zoomLabel.setAttribute("aria-label", `Zoom: ${text}`);
  // Only when the thumb stands for another percentage. Near 25% two steps can
  // round to the same whole percent, and snapping the thumb back to the first
  // would stop the arrow keys there.
  if (zoomPctFromSlider(Number(zoomSlider.value), zoomRange.min, zoomRange.max) !== shown) {
    zoomSlider.value = sliderFromZoomPct(zoomPct, zoomRange.min, zoomRange.max);
  }
  zoomSlider.style.setProperty("--zoom-fill", String(Number(zoomSlider.value) / ZOOM_SLIDER_STEPS));
  zoomSlider.setAttribute("aria-valuetext", `${shown}%`);
  zoomFitWidthBtn.setAttribute("aria-pressed", String(zoomMode === ZOOM_FIT_WIDTH));
  zoomFitPageBtn.setAttribute("aria-pressed", String(zoomMode === ZOOM_FIT_PAGE));
  zoomActualBtn.setAttribute("aria-pressed", String(zoomMode === null && shown === ZOOM_ACTUAL_PCT));
}

// A percentage from anywhere but a fit: the mode ends there.
function leaveZoomFit() {
  if (zoomMode === null) return;
  zoomMode = null;
  zoomFitSeq += 1;
  renderZoomControl();
}

// A fixed percentage from the popover (the slider, a tick, 100%).
function chooseZoomPct(pct) {
  zoomPct = pct;
  leaveZoomFit();
  renderZoomControl();
  return zoomHooks.setZoomPct(pct);
}

// Fit width or Fit page, from the chips, Ctrl+2 / Ctrl+0 or voice. Choosing
// one closes the popover: the reader has what they came for.
function chooseZoomFit(mode) {
  zoomMode = mode;
  renderZoomControl();
  closeZoomPopover({ returnFocus: zoomPopover.contains(document.activeElement) });
  return refitZoom();
}

// 100%, from the chip, Ctrl+1 or voice. The popover stays open, so the slider
// can carry on from there.
function chooseActualSize() {
  return chooseZoomPct(ZOOM_ACTUAL_PCT);
}

// Fits the page again for the mode that is on; nothing in a percentage.
async function refitZoom() {
  if (zoomMode === null) return;
  const seq = ++zoomFitSeq;
  const mode = zoomMode;
  const geometry = await zoomHooks.fitGeometry();
  if (seq !== zoomFitSeq || !geometry) return;
  const zoom = fitZoom(mode, geometry, zoomRange.min, zoomRange.max);
  if (zoom == null) return;
  const pct = Math.round(zoom * 10000) / 100;
  if (Math.abs(pct - zoomPct) < 0.005) return;
  zoomPct = pct;
  renderZoomControl();
  await zoomHooks.setZoomPct(pct);
}

// --- The popover ---------------------------------------------------------- //

function isZoomPopoverOpen() {
  return !zoomPopover.hidden;
}

function openZoomPopover() {
  if (isZoomPopoverOpen()) return;
  zoomPopover.hidden = false;
  zoomLabel.setAttribute("aria-expanded", "true");
  zoomSlider.focus({ preventScroll: true });
}

function closeZoomPopover({ returnFocus = false } = {}) {
  if (!isZoomPopoverOpen()) return;
  zoomPopover.hidden = true;
  zoomLabel.setAttribute("aria-expanded", "false");
  if (returnFocus) zoomLabel.focus({ preventScroll: true });
  zoomHooks.onClose();
}

zoomLabel.addEventListener("click", () => {
  if (isZoomPopoverOpen()) closeZoomPopover();
  else openZoomPopover();
});

function endZoomDrag() {
  zoomDragging = false;
  zoomSlider.classList.remove("is-dragging");
}

zoomSlider.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  zoomDragging = true;
  zoomSlider.classList.add("is-dragging");
});
document.addEventListener("pointerup", endZoomDrag);
document.addEventListener("pointercancel", endZoomDrag);

// The top-bar value follows the thumb at once; the page follows as fast as
// the backend answers.
zoomSlider.addEventListener("input", () => {
  const pct = zoomPctFromSlider(Number(zoomSlider.value), zoomRange.min, zoomRange.max);
  chooseZoomPct(zoomDragging ? snapZoomPct(pct) : pct);
});

// A double-click on the thumb puts it back to 100%.
zoomSlider.addEventListener("dblclick", (ev) => {
  const rect = zoomSlider.getBoundingClientRect();
  const fraction = Number(zoomSlider.value) / ZOOM_SLIDER_STEPS;
  const thumbX = rect.left + ZOOM_THUMB_PX / 2 + fraction * (rect.width - ZOOM_THUMB_PX);
  if (Math.abs(ev.clientX - thumbX) <= ZOOM_THUMB_HIT_PX) chooseActualSize();
});

// A tick (placed where its zoom falls on the track, updateZoomControl) jumps
// there.
for (const tick of zoomTicks.querySelectorAll(".zoom-tick")) {
  tick.addEventListener("click", () => chooseZoomPct(Number(tick.dataset.zoom)));
}

zoomFitWidthBtn.addEventListener("click", () => chooseZoomFit(ZOOM_FIT_WIDTH));
zoomFitPageBtn.addEventListener("click", () => chooseZoomFit(ZOOM_FIT_PAGE));
zoomActualBtn.addEventListener("click", () => chooseActualSize());

// A click anywhere else closes it. The value is left out: its own click
// toggles the popover, and closing here first would reopen it.
document.addEventListener("pointerdown", (ev) => {
  if (!isZoomPopoverOpen()) return;
  if (zoomPopover.contains(ev.target) || zoomLabel.contains(ev.target)) return;
  closeZoomPopover();
});

// Tabbing out of it closes it too, as a click elsewhere would.
zoomPopover.addEventListener("focusout", (ev) => {
  const next = ev.relatedTarget;
  if (!next || zoomPopover.contains(next) || next === zoomLabel) return;
  closeZoomPopover();
});
