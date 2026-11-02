// Freehand marker geometry: pure arithmetic, no DOM, run under Node by
// tests/test_ink_geometry.py (the same split as progress-layout.js and
// zoom-fit.js). freehand.js draws and commits strokes with it, and
// highlight-colors.js hit-tests existing ones with distanceToPath().
//
// Every point is `[x, y]` in PDF points on the page as shown — the space
// get_page_chars' boxes are in and api.add_ink_stroke takes — so nothing here
// depends on zoom.

// Points closer than this to the line through their neighbours are dropped
// before smoothing (Ramer–Douglas–Peucker), in PDF points: hand jitter
// smaller than a fifth of a millimetre, which a 14pt stroke would not show.
const INK_SIMPLIFY_PT = 0.5;
// Chaikin corner-cutting passes: two round off the corners a mouse's sampling
// leaves without visibly shortening a curve.
const INK_SMOOTH_PASSES = 2;
// Straightening on release (docs/DESIGN_SYSTEM.md, "Freehand marker"): a
// stroke at least this long, end to end, in PDF points...
const INK_STRAIGHT_MIN_PT = 24;
// ...that never strays further from the line between its ends than this
// share of that line's length...
const INK_STRAIGHT_MAX_DEVIATION = 0.06;
// ...and is not much longer than that line, so a stroke that goes back and
// forth along one line (filling a band in) stays as drawn, though no point
// of it is far from the line.
const INK_STRAIGHT_MAX_PATH_RATIO = 1.5;
// A straight line this close to horizontal or vertical, in degrees, becomes
// exactly horizontal or vertical.
const INK_AXIS_SNAP_DEG = 5;
// Most points a stroke is sent with (api.MAX_INK_POINTS is the bridge's own
// limit); the simplification is rerun coarser until a stroke fits.
const INK_MAX_POINTS = 4000;

function inkDistance(a, b) {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

// Distance from `p` to the segment a–b.
function distanceToSegment(p, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSq = dx * dx + dy * dy;
  if (!lengthSq) return inkDistance(p, a);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lengthSq));
  // No intermediate point array: this runs for every point of a stroke.
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

// Distance from `p` to the nearest point of a polyline: what a click on a
// stroke is tested against (within half its width, it is on the stroke).
function distanceToPath(p, points) {
  if (!points.length) return Infinity;
  if (points.length === 1) return inkDistance(p, points[0]);
  let best = Infinity;
  for (let i = 1; i < points.length; i++) {
    best = Math.min(best, distanceToSegment(p, points[i - 1], points[i]));
  }
  return best;
}

function pathLength(points) {
  let length = 0;
  for (let i = 1; i < points.length; i++) length += inkDistance(points[i - 1], points[i]);
  return length;
}

// Points closer than `tolerance` to the last one kept are dropped, ends kept:
// a linear pass that thins a slow, dense drag before the costlier one below.
function thinPath(points, tolerance) {
  if (points.length <= 2) return points.slice();
  const out = [points[0]];
  for (let i = 1; i < points.length - 1; i++) {
    if (inkDistance(points[i], out[out.length - 1]) >= tolerance) out.push(points[i]);
  }
  out.push(points[points.length - 1]);
  return out;
}

// Ramer–Douglas–Peucker, iterative (a long stroke would recurse deep), after
// thinPath. Keeps both ends, and every point further than `tolerance` from
// the simplified line.
function simplifyPath(input, tolerance = INK_SIMPLIFY_PT) {
  const points = thinPath(input, tolerance);
  if (points.length <= 2) return points;
  const keep = new Array(points.length).fill(false);
  keep[0] = true;
  keep[points.length - 1] = true;
  const spans = [[0, points.length - 1]];
  while (spans.length) {
    const [first, last] = spans.pop();
    let farthest = -1;
    let farthestDist = tolerance;
    for (let i = first + 1; i < last; i++) {
      const d = distanceToSegment(points[i], points[first], points[last]);
      if (d > farthestDist) {
        farthest = i;
        farthestDist = d;
      }
    }
    if (farthest < 0) continue;
    keep[farthest] = true;
    spans.push([first, farthest], [farthest, last]);
  }
  return points.filter((_, i) => keep[i]);
}

// Chaikin's corner cutting, ends kept where they were: a polyline again (a
// PDF ink stroke is one), dense enough at the corners to read as a curve.
function smoothPath(points, passes = INK_SMOOTH_PASSES) {
  let path = points.slice();
  for (let pass = 0; pass < passes && path.length > 2; pass++) {
    const next = [path[0]];
    for (let i = 0; i < path.length - 1; i++) {
      const [ax, ay] = path[i];
      const [bx, by] = path[i + 1];
      next.push([0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by]);
      next.push([0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by]);
    }
    next.push(path[path.length - 1]);
    path = next;
  }
  return path;
}

// A line within INK_AXIS_SNAP_DEG of horizontal or vertical made exactly so;
// any other line as it is. About its middle by default, which is where a
// drawn line straightened on release sits best; `keepStart` instead keeps its
// start where it is, as for a Shift line, whose start is under the press while
// its end follows the pointer.
function snapToAxis(start, end, { keepStart = false } = {}) {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  if (!dx && !dy) return { start, end };
  const angle = Math.abs(Math.atan2(dy, dx) * 180 / Math.PI); // 0..180
  const offHorizontal = Math.min(angle, 180 - angle);
  const offVertical = Math.abs(90 - angle);
  if (offHorizontal <= INK_AXIS_SNAP_DEG) {
    const y = keepStart ? start[1] : (start[1] + end[1]) / 2;
    return { start: [start[0], y], end: [end[0], y] };
  }
  if (offVertical <= INK_AXIS_SNAP_DEG) {
    const x = keepStart ? start[0] : (start[0] + end[0]) / 2;
    return { start: [x, start[1]], end: [x, end[1]] };
  }
  return { start, end };
}

// The straight line a stroke becomes on release, `{ start, end }`, or null
// when it is to stay as drawn: too short (a dab), too far from straight (a
// curve, a circle), or doubling back on itself (a scribble).
function straightLine(points) {
  if (points.length < 2) return null;
  const start = points[0];
  const end = points[points.length - 1];
  const chord = inkDistance(start, end);
  if (chord < INK_STRAIGHT_MIN_PT) return null;
  if (pathLength(points) > chord * INK_STRAIGHT_MAX_PATH_RATIO) return null;
  for (const p of points) {
    if (distanceToSegment(p, start, end) >= chord * INK_STRAIGHT_MAX_DEVIATION) return null;
  }
  return snapToAxis(start, end);
}

// `count` points spaced evenly along a polyline, ends included: two strokes
// resampled to the same count can be blended point by point.
function resamplePath(points, count) {
  if (!points.length) return [];
  const total = pathLength(points);
  if (points.length === 1 || count < 2 || !total) return new Array(Math.max(count, 1)).fill(points[0]);
  const out = [points[0]];
  let segment = 1;
  let travelled = 0;
  for (let k = 1; k < count - 1; k++) {
    const target = (total * k) / (count - 1);
    while (segment < points.length - 1 && travelled + inkDistance(points[segment - 1], points[segment]) < target) {
      travelled += inkDistance(points[segment - 1], points[segment]);
      segment++;
    }
    const a = points[segment - 1];
    const b = points[segment];
    const t = Math.min(1, (target - travelled) / (inkDistance(a, b) || 1));
    out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
  }
  out.push(points[points.length - 1]);
  return out;
}

// Point-by-point blend of two paths of the same length, `t` from 0 (a) to 1 (b).
function lerpPath(a, b, t) {
  return a.map((p, i) => [p[0] + (b[i][0] - p[0]) * t, p[1] + (b[i][1] - p[1]) * t]);
}

// `points` simplified, coarser and coarser until smoothing it (which doubles
// its segments on every pass) stays within INK_MAX_POINTS. Simplified rather
// than smoothed points are coarsened: there are fewer of them.
function simplifyWithinBudget(points) {
  const growth = 2 ** INK_SMOOTH_PASSES;
  let tolerance = INK_SIMPLIFY_PT;
  let path = simplifyPath(points, tolerance);
  while (path.length * growth > INK_MAX_POINTS) {
    tolerance *= 2;
    path = simplifyPath(path, tolerance);
  }
  return path;
}

// What a hand-drawn stroke is committed as: `drawn`, the smoothed path, and
// `straight`, the line it becomes on release, or null to keep it as drawn.
// Straightness is judged on the simplified path, before smoothing rounds off
// its corners.
function finishStroke(points) {
  const simplified = simplifyWithinBudget(points);
  const drawn = smoothPath(simplified);
  const line = straightLine(simplified);
  return { drawn, straight: line ? [line.start, line.end] : null };
}

// SVG path data for a polyline. A single point is a zero-length segment,
// which round caps draw as a dot.
function pathToSvgD(points) {
  if (!points.length) return "";
  const fmt = (n) => Math.round(n * 100) / 100;
  const [first, ...rest] = points;
  const tail = rest.length ? rest : [first];
  return `M${fmt(first[0])} ${fmt(first[1])}` + tail.map((p) => `L${fmt(p[0])} ${fmt(p[1])}`).join("");
}
