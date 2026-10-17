// Where the progress bar's pieces go, in pixels down its track. No DOM here:
// progress-bar.js measures the track and draws what this returns, and
// tests/test_progress_bar_layout.py runs it under Node.
//
// Every page gets an equal share of the track, its pitch. When a page's share
// less the gap between pages is at least PROGRESS_MIN_PILL_PX, each page is a
// pill of its own (pill mode); below that the bar is one continuous fill
// (continuous mode). Page i's share runs from i × pitch to (i + 1) × pitch in
// both, so the page under the pointer is the same whichever is drawn.

// The shortest pill worth drawing; any shorter and the bar is continuous.
const PROGRESS_MIN_PILL_PX = 3;
// The gap between pills: this share of the pitch, kept within these bounds.
const PROGRESS_GAP_RATIO = 0.25;
const PROGRESS_MIN_GAP_PX = 1.5;
const PROGRESS_MAX_GAP_PX = 6;
// A highlight marker in a pill: this share of the pill's height, at least
// PROGRESS_MIN_PILL_MARKER_PX and never taller than the pill (the marker's
// box; its edge is drawn outside it).
const PROGRESS_PILL_MARKER_RATIO = 0.4;
const PROGRESS_MIN_PILL_MARKER_PX = 2;
// A highlight tick on the continuous bar, and the edge drawn round every
// marker (reading.css .progress-marker): ticks merge when their edges touch.
const PROGRESS_TICK_PX = 3;
const PROGRESS_MARKER_EDGE_PX = 1;

// To the nearest device pixel, so pill and marker edges land crisp.
function snapToDevice(value, dpr) {
  return Math.round(value * dpr) / dpr;
}

// { pitch, gap, pill, pills, dpr } for a track `height` px tall holding
// `pageCount` pages, or null while there is nothing to lay out.
function progressLayout(height, pageCount, dpr = 1) {
  if (!(height > 0) || !(pageCount > 0)) return null;
  const pitch = height / pageCount;
  const gap = Math.min(PROGRESS_MAX_GAP_PX, Math.max(PROGRESS_MIN_GAP_PX, pitch * PROGRESS_GAP_RATIO));
  const pill = pitch - gap;
  return { pitch, gap, pill, pills: pill >= PROGRESS_MIN_PILL_PX, dpr };
}

// Page `index`'s pill: centred in its share, half the gap clear at each end.
function pillBox(layout, index) {
  const top = snapToDevice(index * layout.pitch + layout.gap / 2, layout.dpr);
  const bottom = snapToDevice((index + 1) * layout.pitch - layout.gap / 2, layout.dpr);
  return { top, height: bottom - top };
}

// One marker per highlighted page, centred in its pill.
function pillMarkerBoxes(layout, pages) {
  return [...pages].sort((a, b) => a - b).map((index) => {
    const pill = pillBox(layout, index);
    const wanted = Math.max(PROGRESS_MIN_PILL_MARKER_PX, pill.height * PROGRESS_PILL_MARKER_RATIO);
    const height = Math.min(pill.height, snapToDevice(wanted, layout.dpr));
    return { top: snapToDevice(pill.top + (pill.height - height) / 2, layout.dpr), height };
  });
}

// A PROGRESS_TICK_PX tick centred on each highlighted page; ticks whose edges
// would touch or overlap on screen are drawn as one.
function tickMarkerBoxes(layout, pages) {
  const boxes = [];
  for (const index of [...pages].sort((a, b) => a - b)) {
    const top = snapToDevice((index + 0.5) * layout.pitch - PROGRESS_TICK_PX / 2, layout.dpr);
    const bottom = top + PROGRESS_TICK_PX;
    const last = boxes[boxes.length - 1];
    if (last && top - (last.top + last.height) <= 2 * PROGRESS_MARKER_EDGE_PX) {
      last.height = bottom - last.top;
    } else {
      boxes.push({ top, height: PROGRESS_TICK_PX });
    }
  }
  return boxes;
}

function progressMarkerBoxes(layout, pages) {
  return layout.pills ? pillMarkerBoxes(layout, pages) : tickMarkerBoxes(layout, pages);
}

// Where the read fill ends. In pill mode, inside the current page's pill by
// `fraction` (0 to 1, how far through the page the reader is); the continuous
// bar fills to the end of the current page whatever the fraction.
function progressFillEnd(layout, index, fraction) {
  if (!layout.pills) return (index + 1) * layout.pitch;
  const pill = pillBox(layout, index);
  return pill.top + Math.min(1, Math.max(0, fraction || 0)) * pill.height;
}

// Where the read fill ends in a scrolling view: { page, fraction }, or null
// with no pages. The overall progress is scrollTop over the scroll range (1
// when nothing scrolls), and the fill follows a reading line that moves from
// the top of the view at the start to its bottom at the end: scrollTop +
// progress × clientHeight. Pages wholly above the view are always above the
// line, so always filled; the page the line is on fills as far as the line
// has gone through it; and scrolled to the bottom the line is at the end of
// the content, so the last page is full. `pages` are { top, height } in the
// scroller's content coordinates, in order.
function progressReadPosition({ scrollTop, scrollHeight, clientHeight, pages }) {
  if (!pages || !pages.length) return null;
  const range = scrollHeight - clientHeight;
  const progress = range > 0 ? Math.min(1, Math.max(0, scrollTop / range)) : 1;
  const line = scrollTop + progress * clientHeight;
  let page = 0;
  for (let i = 0; i < pages.length; i++) {
    if (pages[i].top <= line) page = i;
    else break;
  }
  if (progress >= 1) return { page: pages.length - 1, fraction: 1 };
  const { top, height } = pages[page];
  const fraction = height > 0 ? Math.min(1, Math.max(0, (line - top) / height)) : 1;
  return { page, fraction };
}
