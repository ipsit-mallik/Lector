// The zoom popover's arithmetic. No DOM here: zoom-control.js measures the
// page area and draws what this returns, and tests/test_zoom_control.py runs
// it under Node.
//
// The slider is logarithmic, so every stretch of it multiplies the zoom by the
// same amount: with the 25-400% range, 100% sits at its centre and each
// quarter of the track is a doubling. A linear slider gives 25-100% a fifth
// of the track and makes small sizes hard to pick.

// Slider positions, 0 to ZOOM_SLIDER_STEPS. A step is about 2.8% of the
// current zoom, fine enough to drag through and coarse enough that the arrow
// keys move it visibly.
const ZOOM_SLIDER_STEPS = 100;

// The two fit modes. Any other zoom is a fixed percentage (mode null).
const ZOOM_FIT_WIDTH = "width";
const ZOOM_FIT_PAGE = "page";

// 100% is the paper's real size: CSS pixels per PDF point (96 / 72), the same
// scale the backend renders at (PdfDocument.CSS_PX_PER_POINT).
const CSS_PX_PER_POINT = 96 / 72;

// Space a fitted page leaves on each side of the page area (and above and
// below it, for Fit page).
const ZOOM_FIT_MARGIN_PX = 24;

// The zooms marked under the slider: 50%, 100% and 200%, which fall at a
// quarter, half and three quarters of the track.
const ZOOM_TICKS = [50, 100, 200];

// Dragging this close to 100% lands on 100%. Nowhere else snaps.
const ZOOM_SNAP_PCT = 100;
const ZOOM_SNAP_RANGE_PCT = 4;

function zoomPctFromSlider(position, minPct, maxPct) {
  const t = Math.min(1, Math.max(0, position / ZOOM_SLIDER_STEPS));
  return Math.round(minPct * Math.pow(maxPct / minPct, t));
}

function sliderFromZoomPct(pct, minPct, maxPct) {
  const clamped = Math.min(maxPct, Math.max(minPct, pct));
  return Math.round((ZOOM_SLIDER_STEPS * Math.log(clamped / minPct)) / Math.log(maxPct / minPct));
}

// Where a zoom falls along the slider, 0 to 1: a tick's place, and how far
// the filled part of the track runs.
function zoomTrackFraction(pct, minPct, maxPct) {
  const clamped = Math.min(maxPct, Math.max(minPct, pct));
  return Math.log(clamped / minPct) / Math.log(maxPct / minPct);
}

// A dragged percentage, pulled to 100% when it is within reach of it.
function snapZoomPct(pct) {
  return Math.abs(pct - ZOOM_SNAP_PCT) <= ZOOM_SNAP_RANGE_PCT ? ZOOM_SNAP_PCT : pct;
}

// The zoom (1 = 100%) at which a page of `pageWidth` x `pageHeight` points
// fits an area of `areaWidth` x `areaHeight` CSS pixels with
// ZOOM_FIT_MARGIN_PX to spare on each side: across its width for Fit width,
// wholly for Fit page. To 0.01%, rounded down and a pixel short: the page
// box is its size rounded up to a whole pixel, which must never come out
// bigger than the room and scroll. null when there is nothing to measure yet.
function fitZoom(mode, { areaWidth, areaHeight, pageWidth, pageHeight }, minPct, maxPct) {
  if (!(pageWidth > 0) || !(pageHeight > 0) || !(areaWidth > 0)) return null;
  const room = (length) => length - 2 * ZOOM_FIT_MARGIN_PX - 1;
  let zoom = room(areaWidth) / (pageWidth * CSS_PX_PER_POINT);
  if (mode === ZOOM_FIT_PAGE) {
    if (!(areaHeight > 0)) return null;
    zoom = Math.min(zoom, room(areaHeight) / (pageHeight * CSS_PX_PER_POINT));
  }
  const clamped = Math.min(maxPct / 100, Math.max(minPct / 100, zoom));
  return Math.floor(clamped * 10000) / 10000;
}

// What the topbar shows for the zoom: the mode's name while one is on, the
// whole percentage otherwise.
function zoomLabelText(mode, pct) {
  if (mode === ZOOM_FIT_WIDTH) return "Fit width";
  if (mode === ZOOM_FIT_PAGE) return "Fit page";
  return `${Math.round(pct)}%`;
}
