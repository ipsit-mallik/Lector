"""The reader's progress bar's shape and its active and idle states
(docs/DESIGN_SYSTEM.md, "Reader progress bar").

Collapsed it is a 6px bar with rounded ends, 2px in from the window's edge,
with a hover zone of at least 12px (its pill and continuous modes are in
tests/test_progress_bar_layout.py). Highlight markers stay within the bar's
width; they are drawn outside the track's clip only so its rounded ends don't
cut their edge. There is no position notch: the end of the fill is the place.
Active (any scroll, page change, pointer near the edge, hover) the bar is at
full strength; 3s after the last of those it fades to 55% over --dur-base,
instantly under reduced motion.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "frontend" / "pages"
READING_HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
READING_CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
PROGRESS_JS = (PAGES / "progress-bar.js").read_text(encoding="utf-8")
LAYOUT_JS = (PAGES / "progress-layout.js").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    # Not the selector as one of a list ("a,\nb {").
    match = re.search(r"(?:^|(?<!,)\n)%s \{([^}]*)\}" % re.escape(selector), READING_CSS)
    if not match:
        raise AssertionError(f"reading.css has no rule for {selector}")
    return match.group(1)


def px(body: str, prop: str) -> int:
    return int(re.search(r"%s:\s*(\d+)px" % re.escape(prop), body).group(1))


def function_body(name: str) -> str:
    body = PROGRESS_JS[PROGRESS_JS.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def constant(name: str, source: str = PROGRESS_JS) -> int:
    return int(re.search(r"const %s = (\d+);" % name, source).group(1))


class ProgressBarShapeTests(unittest.TestCase):
    def test_the_collapsed_bar_is_6px_and_inset_2px_with_a_12px_or_wider_hover_zone(self):
        edge = rule(".progress-edge")
        self.assertEqual(px(edge, "--progress-collapsed"), 6)
        self.assertEqual(px(edge, "--progress-inset"), 2)
        self.assertEqual(px(edge, "--progress-end-inset"), 4)
        self.assertEqual(px(edge, "--progress-gap"), 4)
        self.assertGreaterEqual(px(edge, "--progress-hit"), 12)
        self.assertIn(
            "width: calc(var(--progress-inset) + var(--progress-collapsed) + var(--progress-gap))", edge
        )

    def test_the_track_keeps_off_the_window_edge_and_has_rounded_ends(self):
        placed = rule(".progress-track,\n.progress-overlay")
        self.assertIn("left: var(--progress-inset)", placed)
        for side in ("top", "bottom"):
            self.assertIn(f"{side}: var(--progress-end-inset)", placed)
        self.assertIn("border-radius: calc(var(--progress-collapsed) / 2)", rule(".progress-track"))

    def test_markers_are_outside_the_tracks_clip(self):
        track = re.search(r'<div class="progress-track" id="progressTrack">(.*?)\n          </div>',
                          READING_HTML, re.S).group(1)
        self.assertNotIn("progressMarkers", track)
        overlay = re.search(r'<div class="progress-overlay">(.*?)</div>\s*</div>', READING_HTML, re.S).group(1)
        self.assertIn('id="progressMarkers"', overlay)

    def test_markers_have_an_edge_and_stay_within_the_bar(self):
        self.assertIn("width: var(--progress-bar);", rule(".progress-overlay"))
        marker = rule(".progress-marker")
        # The 1px edge is drawn outside the box, so 1px in on each side puts it
        # flush with the bar's sides.
        self.assertIn("left: 1px", marker)
        self.assertIn("right: 1px", marker)
        self.assertIn("box-shadow: 0 0 0 1px var(--progress-marker-edge)", marker)
        # The continuous bar's ticks; their sizes and merging, and the pill
        # markers', are in tests/test_progress_bar_layout.py.
        self.assertEqual(constant("PROGRESS_TICK_PX", LAYOUT_JS), 3)
        self.assertEqual(constant("PROGRESS_MARKER_EDGE_PX", LAYOUT_JS), 1)

    def test_markers_are_laid_out_by_page_in_pixels(self):
        self.assertIn("progressMarkerBoxes(progressGeometry, progressHighlighted)", function_body("renderProgressMarkers"))

    def test_there_is_no_position_notch(self):
        for source in (READING_HTML, READING_CSS, PROGRESS_JS):
            self.assertNotIn("notch", source.lower())


class ProgressBarIdleTests(unittest.TestCase):
    def test_idle_fades_the_whole_bar_to_55_percent_over_200ms(self):
        self.assertIn("opacity: 0.55", rule(".reading-progress.is-idle"))
        self.assertIn("transition: opacity var(--dur-base)", rule(".reading-progress"))

    def test_the_fill_does_not_animate(self):
        for selector in (".progress-fill-clip", ".progress-fill-segments"):
            self.assertNotIn("transition", READING_CSS[READING_CSS.index(selector + " {"):].split("}")[0])

    def test_reduced_motion_changes_opacity_instantly(self):
        reduced = READING_CSS[READING_CSS.index("@media (prefers-reduced-motion: reduce) {\n  .reading-progress"):]
        reduced = reduced[:reduced.index("}")]
        for selector in (".reading-progress", ".progress-overlay"):
            self.assertIn(selector, reduced)

    def test_it_idles_3s_after_the_last_activity(self):
        self.assertEqual(constant("PROGRESS_IDLE_MS"), 3000)
        wake = function_body("wakeProgressBar")
        self.assertIn('classList.remove("is-idle")', wake)
        self.assertIn("setTimeout(idleProgressBar, PROGRESS_IDLE_MS)", wake)

    def test_it_does_not_idle_while_open_or_in_use(self):
        idle = function_body("idleProgressBar")
        self.assertIn("isProgressHeldOpen()", idle)
        self.assertIn('"is-expanded"', idle)

    def test_a_page_change_wakes_it(self):
        self.assertIn("if (state.page_index !== progressPage) wakeProgressBar();", function_body("updateProgressBar"))

    def test_a_pointer_near_the_left_edge_wakes_it(self):
        self.assertEqual(constant("PROGRESS_WAKE_ZONE_PX"), 40)
        self.assertRegex(PROGRESS_JS, r'document\.addEventListener\("pointermove"[^}]*PROGRESS_WAKE_ZONE_PX\) wakeProgressBar\(\)')

    def test_hover_and_focus_wake_it(self):
        for event in ("pointerenter", "focus"):
            handler = PROGRESS_JS[PROGRESS_JS.index(f'readingProgress.addEventListener("{event}"'):]
            self.assertIn("wakeProgressBar();", handler[:handler.index("\n});")])

    def test_scrolling_either_view_wakes_it(self):
        for scroller in ("bookScroll", "stripScroll"):
            self.assertIn(f'{scroller}.addEventListener("scroll", wakeProgressBar', READING_JS)


if __name__ == "__main__":
    unittest.main()
