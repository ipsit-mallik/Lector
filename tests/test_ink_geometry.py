"""The freehand marker's geometry (docs/DESIGN_SYSTEM.md, "Freehand marker").

`frontend/pages/ink-geometry.js` under Node. On release a stroke at least
24pt long, end to end, that never strays more than 6% of that length from the
line between its ends, and is not more than 1.5 times that length along its
path, becomes that straight line; within 5 degrees of horizontal or vertical
the line is made exactly horizontal or vertical. Anything else (a curve, a
circle, a scribble, a short dab) is kept as drawn, smoothed. The strokes here
are generated with a fixed pseudo-random jitter, the way a hand on a mouse
draws them, so the results do not change between runs.

Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOMETRY_JS = ROOT / "frontend" / "pages" / "ink-geometry.js"
NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const g = (name) => vm.runInContext(name, context);
const [finishStroke, straightLine, snapToAxis, simplifyPath, smoothPath, distanceToPath,
       resamplePath, lerpPath, pathLength, pathToSvgD] =
  ["finishStroke", "straightLine", "snapToAxis", "simplifyPath", "smoothPath", "distanceToPath",
   "resamplePath", "lerpPath", "pathLength", "pathToSvgD"].map(g);

// A fixed pseudo-random sequence, so the "hand" is the same on every run.
let seed = 7;
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) - 0.5;

// A line from (x0, y0) at `deg` degrees, `length` long, sampled every 2pt,
// with one slow wave of `wave` pt and hand jitter of `jitter` pt across it.
function handLine(x0, y0, deg, length, wave, jitter) {
  const rad = deg * Math.PI / 180, ux = Math.cos(rad), uy = Math.sin(rad);
  const points = [];
  for (let s = 0; s <= length; s += 2) {
    const off = wave * Math.sin((s / length) * Math.PI * 2) + jitter * rand();
    points.push([x0 + ux * s - uy * off, y0 + uy * s + ux * off]);
  }
  return points;
}

function circle(cx, cy, r) {
  const points = [];
  for (let a = 0; a <= 360; a += 5) points.push([cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)]);
  return points;
}

// Back and forth along one horizontal band, as when filling something in.
function fill() {
  const points = [];
  for (let pass = 0; pass < 4; pass++) {
    for (let s = 0; s <= 200; s += 4) points.push([100 + (pass % 2 ? 200 - s : s), 300 + pass * 1.5]);
  }
  return points;
}

const ends = (p) => [p[0], p[p.length - 1]];
const wobblyUnderline = handLine(100, 200, 1.5, 240, 4, 1.5);
const diagonal = handLine(100, 100, 30, 200, 3, 1);
const result = {
  underline: finishStroke(wobblyUnderline),
  underlineEnds: ends(wobblyUnderline),
  diagonal: finishStroke(diagonal),
  diagonalEnds: ends(diagonal),
  nearVertical: finishStroke(handLine(300, 100, 87, 150, 2, 1)).straight,
  circle: finishStroke(circle(200, 200, 40)).straight,
  arc: finishStroke(handLine(100, 100, 0, 200, 25, 1)).straight,
  fill: finishStroke(fill()).straight,
  dab: finishStroke(handLine(100, 100, 0, 20, 0.5, 0.5)).straight,
  justLongEnough: straightLine([[0, 0], [24, 0]]),
  justTooShort: straightLine([[0, 0], [23.9, 0]]),
  deviationUnder: straightLine([[0, 0], [50, 5.9], [100, 0]]),
  deviationAt: straightLine([[0, 0], [50, 6], [100, 0]]),
  snap4: snapToAxis([0, 0], [100, Math.tan(4 * Math.PI / 180) * 100]),
  snap7: snapToAxis([0, 0], [100, Math.tan(7 * Math.PI / 180) * 100]),
  snapVertical: snapToAxis([0, 0], [Math.tan(3 * Math.PI / 180) * 100, -100]),
  snapLeftward: snapToAxis([100, 0], [0, 3]),
  simplifiedEnds: (() => { const s = simplifyPath(wobblyUnderline); return [s.length < wobblyUnderline.length, s[0], s[s.length - 1]]; })(),
  smoothedEnds: (() => { const s = smoothPath([[0, 0], [10, 10], [20, 0]]); return [s.length, s[0], s[s.length - 1]]; })(),
  onPath: distanceToPath([50, 3], [[0, 0], [100, 0]]),
  offEnd: distanceToPath([103, 4], [[0, 0], [100, 0]]),
  onePoint: distanceToPath([3, 4], [[0, 0]]),
  resampled: resamplePath([[0, 0], [10, 0], [10, 10]], 5),
  resampledLength: Math.round(pathLength(resamplePath(circle(0, 0, 10), 64)) * 10) / 10,
  lerpHalf: lerpPath([[0, 0], [10, 0]], [[0, 10], [10, 10]], 0.5),
  // Every point a corner, so none can be dropped at the first tolerance:
  // 1500 smoothed twice would be 6000, over the 4000 a stroke is sent with.
  manyPoints: finishStroke(Array.from({ length: 1500 }, (_, i) => [i * 0.5, (i % 2) * 3 + i * 0.01])).drawn.length,
  svgD: pathToSvgD([[1.234, 2], [3, 4.567]]),
  svgDot: pathToSvgD([[5, 5]]),
};
console.log(JSON.stringify(result));
"""


@unittest.skipUnless(NODE, "Node is not installed")
class InkGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run(
            [NODE, "-e", HARNESS, str(GEOMETRY_JS)], capture_output=True, text=True, encoding="utf-8", check=True
        )
        cls.r = json.loads(run.stdout)

    def test_a_wobbly_underline_becomes_exactly_horizontal(self):
        start, end = self.r["underline"]["straight"]
        self.assertEqual(start[1], end[1])
        drawn_start, drawn_end = self.r["underlineEnds"]
        self.assertEqual([start[0], end[0]], [drawn_start[0], drawn_end[0]])
        self.assertAlmostEqual(start[1], (drawn_start[1] + drawn_end[1]) / 2)

    def test_a_near_straight_diagonal_straightens_between_its_ends_without_snapping(self):
        self.assertEqual(self.r["diagonal"]["straight"], self.r["diagonalEnds"])

    def test_a_near_vertical_line_becomes_exactly_vertical(self):
        start, end = self.r["nearVertical"]
        self.assertEqual(start[0], end[0])

    def test_curves_scribbles_and_dabs_stay_as_drawn(self):
        for name in ("circle", "arc", "fill", "dab"):
            with self.subTest(name):
                self.assertIsNone(self.r[name])

    def test_the_length_and_deviation_limits(self):
        self.assertIsNotNone(self.r["justLongEnough"])
        self.assertIsNone(self.r["justTooShort"])
        self.assertIsNotNone(self.r["deviationUnder"])
        self.assertIsNone(self.r["deviationAt"])

    def test_axis_snap_within_five_degrees_only(self):
        self.assertEqual(self.r["snap4"]["start"][1], self.r["snap4"]["end"][1])
        self.assertNotEqual(self.r["snap7"]["start"][1], self.r["snap7"]["end"][1])
        self.assertEqual(self.r["snapVertical"]["start"][0], self.r["snapVertical"]["end"][0])
        self.assertEqual(self.r["snapLeftward"], {"start": [100, 1.5], "end": [0, 1.5]})

    def test_simplifying_and_smoothing_keep_the_ends(self):
        fewer, first, last = self.r["simplifiedEnds"]
        self.assertTrue(fewer)
        self.assertEqual([first, last], self.r["underlineEnds"])
        self.assertEqual(self.r["smoothedEnds"], [12, [0, 0], [20, 0]])

    def test_the_drawn_stroke_is_its_smoothed_path(self):
        drawn = self.r["underline"]["drawn"]
        self.assertEqual([drawn[0], drawn[-1]], self.r["underlineEnds"])

    def test_distance_to_a_stroke(self):
        self.assertAlmostEqual(self.r["onPath"], 3)
        self.assertAlmostEqual(self.r["offEnd"], 5)
        self.assertAlmostEqual(self.r["onePoint"], 5)

    def test_resampling_and_blending(self):
        self.assertEqual(self.r["resampled"], [[0, 0], [5, 0], [10, 0], [10, 5], [10, 10]])
        self.assertAlmostEqual(self.r["resampledLength"], 62.8, delta=0.3)
        self.assertEqual(self.r["lerpHalf"], [[0, 5], [10, 5]])

    def test_a_very_long_stroke_fits_the_bridge(self):
        self.assertLessEqual(self.r["manyPoints"], 4000)
        self.assertGreaterEqual(self.r["manyPoints"], 2)

    def test_svg_path_data(self):
        self.assertEqual(self.r["svgD"], "M1.23 2L3 4.57")
        self.assertEqual(self.r["svgDot"], "M5 5L5 5")


if __name__ == "__main__":
    unittest.main()
