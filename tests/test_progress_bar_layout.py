"""The reader's progress bar's layout (docs/DESIGN_SYSTEM.md, "Reader progress
bar"): by page, never by chapter.

`frontend/pages/progress-layout.js` under Node. A page's pitch is the track's
height over the page count; the gap between pages is a quarter of the pitch,
kept to 1.5-6px; a pill is the pitch less the gap. At 3px or more each page is
a pill (pill mode), below that the bar is one continuous fill. Pill mode puts
one marker per highlighted page, centred in its pill at 40% of its height (at
least 2px, never taller than the pill), and fills the current page's pill as
far as the reader has scrolled through it; continuous mode puts a 3px tick on
each highlighted page and merges ticks only when their edges would touch.

The track is 700px here, about what a 1280x820 window gives it. The Node tests
are skipped when Node is not installed.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "frontend" / "pages"
LAYOUT_JS = PAGES / "progress-layout.js"
PROGRESS_JS = (PAGES / "progress-bar.js").read_text(encoding="utf-8")
READING_HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
READING_CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const run = (expr) => vm.runInContext(expr, context);
const layout = run("progressLayout");
const pill = run("pillBox");
const markers = run("progressMarkerBoxes");
const fillEnd = run("progressFillEnd");
const shape = (l) => l && { pitch: l.pitch, gap: l.gap, pill: l.pill, pills: l.pills };
const two = layout(700, 2), twenty = layout(700, 20), sixty = layout(700, 60), book = layout(700, 357);
console.log(JSON.stringify({
  two: shape(two), twenty: shape(twenty), sixty: shape(sixty), book: shape(book),
  atThreshold: shape(layout(450, 100)),
  belowThreshold: shape(layout(449, 100)),
  wideGap: shape(layout(800, 4)),
  midGap: shape(layout(480, 40)),
  empty: [layout(0, 10), layout(700, 0)],
  twoPills: [pill(two, 0), pill(two, 1)],
  sixtyPills: Array.from({ length: 60 }, (_, i) => pill(sixty, i)),
  sixtyPillsHiDpi: Array.from({ length: 60 }, (_, i) => pill(layout(700, 60, 1.5), i)),
  twentyMarkers: markers(twenty, new Set([0, 5])),
  adjacentPillMarkers: markers(sixty, new Set([4, 3])),
  smallestPillMarker: markers(layout(450, 100), new Set([7])),
  adjacentTicks: markers(book, new Set([10, 11])),
  ticksTwoApart: markers(book, new Set([10, 12])),
  ticksThreeApart: markers(book, new Set([10, 13])),
  fill: {
    pillTop: pill(twenty, 5).top,
    pillHeight: pill(twenty, 5).height,
    start: fillEnd(twenty, 5, 0),
    half: fillEnd(twenty, 5, 0.5),
    end: fillEnd(twenty, 5, 1),
    over: fillEnd(twenty, 5, 1.7),
    under: fillEnd(twenty, 5, -0.3),
    continuousStart: fillEnd(book, 100, 0),
    continuousHalf: fillEnd(book, 100, 0.5),
  },
  // Two 1000px pages 20px apart under 24px of padding: content 2068px tall.
  read: (() => {
    const at = run("progressReadPosition");
    const pages = [{ top: 24, height: 1000 }, { top: 1044, height: 1000 }];
    const view = (scrollTop, clientHeight = 700) => at({ scrollTop, scrollHeight: 2068, clientHeight, pages });
    return {
      top: view(0),
      middle: view(684),
      bottom: view(1368),
      pastBottom: view(1400),
      fits: at({ scrollTop: 0, scrollHeight: 600, clientHeight: 700, pages: [{ top: 24, height: 300 }] }),
      noPages: at({ scrollTop: 0, scrollHeight: 0, clientHeight: 700, pages: [] }),
      // A page wholly above the view is behind the line at any scroll.
      aboveAlwaysFilled: [1100, 1200, 1300].every((t) => view(t).page === 1),
    };
  })(),
}));
"""


@unittest.skipUnless(NODE, "Node is not installed")
class ProgressLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([NODE, "-e", HARNESS, str(LAYOUT_JS)], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_a_2_page_pdf_is_two_tall_pills_6px_apart(self):
        self.assertEqual(self.out["two"], {"pitch": 350, "gap": 6, "pill": 344, "pills": True})
        self.assertEqual(self.out["twoPills"], [{"top": 3, "height": 344}, {"top": 353, "height": 344}])

    def test_a_20_page_pdf_is_pills(self):
        self.assertEqual(self.out["twenty"], {"pitch": 35, "gap": 6, "pill": 29, "pills": True})

    def test_a_60_page_pdf_is_pills_with_a_quarter_pitch_gap(self):
        sixty = self.out["sixty"]
        self.assertTrue(sixty["pills"])
        self.assertAlmostEqual(sixty["gap"], 700 / 60 / 4)
        self.assertAlmostEqual(sixty["pill"], 700 / 60 * 0.75)

    def test_a_357_page_pdf_is_continuous(self):
        self.assertFalse(self.out["book"]["pills"])
        self.assertEqual(self.out["book"]["gap"], 1.5)

    def test_pill_mode_starts_at_a_3px_pill(self):
        # 4.5px pitch: 1.5px gap, 3px pill.
        self.assertEqual(self.out["atThreshold"], {"pitch": 4.5, "gap": 1.5, "pill": 3, "pills": True})
        self.assertFalse(self.out["belowThreshold"]["pills"])

    def test_the_gap_is_a_quarter_pitch_within_1_5_and_6px(self):
        self.assertEqual(self.out["wideGap"]["gap"], 6)
        self.assertEqual(self.out["midGap"]["gap"], 3)

    def test_nothing_to_lay_out_without_a_height_or_pages(self):
        self.assertEqual(self.out["empty"], [None, None])

    def test_pills_land_on_whole_device_pixels_and_never_overlap(self):
        for key, dpr in (("sixtyPills", 1), ("sixtyPillsHiDpi", 1.5)):
            pills = self.out[key]
            for box in pills:
                for edge in (box["top"], box["top"] + box["height"]):
                    self.assertAlmostEqual(edge * dpr, round(edge * dpr), places=6)
            for above, below in zip(pills, pills[1:]):
                self.assertGreater(below["top"], above["top"] + above["height"])

    def test_a_pill_marker_is_40_percent_of_its_pill_and_centred(self):
        first, sixth = self.out["twentyMarkers"]
        # 29px pill: 11.6px, to the pixel 12, in a pill from 3 to 32.
        self.assertEqual(first, {"top": 12, "height": 12})
        self.assertEqual(sixth["height"], 12)
        self.assertEqual(sixth["top"] - first["top"], 5 * 35)

    def test_a_pill_marker_is_at_least_2px_and_never_taller_than_its_pill(self):
        [marker] = self.out["smallestPillMarker"]
        self.assertGreaterEqual(marker["height"], 2)
        self.assertLessEqual(marker["height"], 3)

    def test_adjacent_highlighted_pages_keep_a_marker_each_in_pill_mode(self):
        self.assertEqual(len(self.out["adjacentPillMarkers"]), 2)

    def test_continuous_ticks_are_3px_and_merge_only_when_their_edges_touch(self):
        [merged] = self.out["adjacentTicks"]
        self.assertGreater(merged["height"], 3)
        self.assertEqual(len(self.out["ticksTwoApart"]), 1)
        apart = self.out["ticksThreeApart"]
        self.assertEqual([t["height"] for t in apart], [3, 3])
        # More than the two 1px edges between them.
        self.assertGreater(apart[1]["top"] - (apart[0]["top"] + 3), 2)

    def test_the_current_pill_fills_with_the_scroll_through_its_page(self):
        fill = self.out["fill"]
        self.assertEqual(fill["start"], fill["pillTop"])
        self.assertEqual(fill["half"], fill["pillTop"] + fill["pillHeight"] / 2)
        self.assertEqual(fill["end"], fill["pillTop"] + fill["pillHeight"])
        self.assertEqual(fill["over"], fill["end"])
        self.assertEqual(fill["under"], fill["start"])

    def test_scrolled_to_the_bottom_the_last_page_is_full(self):
        read = self.out["read"]
        self.assertEqual(read["bottom"], {"page": 1, "fraction": 1})
        self.assertEqual(read["pastBottom"], {"page": 1, "fraction": 1})

    def test_at_the_top_the_fill_starts_at_the_first_page(self):
        # Progress 0: the line is the view's top, 24px above the first page.
        self.assertEqual(self.out["read"]["top"], {"page": 0, "fraction": 0})

    def test_halfway_the_line_is_halfway_down_the_view(self):
        # Progress 0.5: line 684 + 350 = 1034, in the gap after page 1, so
        # page 1 is full and page 2 not yet started.
        self.assertEqual(self.out["read"]["middle"], {"page": 0, "fraction": 1})
        self.assertTrue(self.out["read"]["aboveAlwaysFilled"])

    def test_a_document_that_fits_is_100_percent(self):
        self.assertEqual(self.out["read"]["fits"], {"page": 0, "fraction": 1})
        self.assertIsNone(self.out["read"]["noPages"])

    def test_the_continuous_fill_ends_with_the_current_page_whatever_the_scroll(self):
        fill = self.out["fill"]
        self.assertAlmostEqual(fill["continuousStart"], 101 * 700 / 357)
        self.assertEqual(fill["continuousHalf"], fill["continuousStart"])


class ProgressBarByPageTests(unittest.TestCase):
    def test_the_bar_has_no_chapter_segments_or_page_hairlines(self):
        self.assertNotIn("progressTicks", READING_HTML)
        self.assertNotIn("progress-ticks", READING_CSS)
        build = PROGRESS_JS[PROGRESS_JS.index("function buildSegments("):]
        self.assertNotIn("progressChapters", build[:build.index("\n}\n")])

    def test_the_layout_loads_before_the_bar(self):
        scripts = re.findall(r'<script src="([^"]+)"></script>', READING_HTML)
        self.assertLess(scripts.index("progress-layout.js"), scripts.index("progress-bar.js"))

    def test_pill_mode_is_a_class_on_the_bar_and_pills_have_rounded_ends(self):
        self.assertIn('readingProgress.classList.toggle("is-paged", progressGeometry.pills)', PROGRESS_JS)
        self.assertRegex(
            READING_CSS,
            r"\.reading-progress\.is-paged \.progress-segment \{ border-radius: calc\(var\(--progress-collapsed\) / 2\); \}",
        )

    def test_the_bar_is_laid_out_again_on_every_resize(self):
        self.assertIn("new ResizeObserver(() => measureProgressTrack()).observe(progressTrack);", PROGRESS_JS)


if __name__ == "__main__":
    unittest.main()
