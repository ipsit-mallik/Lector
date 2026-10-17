"""The outline and pages panel's look and the reader's scroll gutter
(docs/DESIGN_SYSTEM.md, "Outline and pages panel" and "Reading view").

* The panel: 200px by default, 168-400px, dragged at its right edge (or the
  edge focused and the arrow keys), the width remembered; 16px padding with
  the tabs on the same left edge.
* Thumbnails: 128px wide at most, at the page's own shape, centred in 128px
  columns 16px apart (two once there is room), drawn at 128px x the pixel
  ratio; no box behind them; hover, keyboard focus and the progress bar
  pointing at one give the Recent grid card's hover exactly; the current page
  an accent ring and number.
* Outline rows: a tint on hover (or the progress bar pointing at a page in
  the section), the current one in accent with a bar; titles wrap to two
  lines and keep the whole title as a tooltip.
* The progress bar with the panel open: it never widens, shows no tip, and
  pointing at it marks that page in the panel instead.
* The empty outline: the Recent empty state's icon circle, a title, one
  muted line, "Browse pages" and a "show pages" chip.
* The scrollers keep 24px inside their scroll range on every side.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "frontend" / "pages"
HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
OUTLINE_JS = (PAGES / "outline-panel.js").read_text(encoding="utf-8")
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
CENTERING_JS = (PAGES / "page-centering.js").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r" \{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


class PanelWidthTests(unittest.TestCase):
    def test_200px_by_default(self):
        self.assertIn("width: var(--outline-width, 200px);", rule(".outline-panel"))

    def test_the_right_edge_is_a_resize_handle(self):
        tag = re.search(r'<div class="outline-resizer"[^>]*>', HTML).group(0)
        for attr in ('role="separator"', 'tabindex="0"', 'aria-orientation="vertical"',
                     'aria-valuemin="168"', 'aria-valuemax="400"', 'aria-valuenow="200"'):
            self.assertIn(attr, tag)
        resizer = rule(".outline-resizer")
        self.assertIn("cursor: col-resize;", resizer)
        self.assertIn("right: -4px;", resizer)

    def test_drag_and_keys_resize_within_the_limits(self):
        self.assertIn('outlineResizer.addEventListener("pointerdown"', OUTLINE_JS)
        self.assertIn("setPointerCapture", OUTLINE_JS)
        self.assertIn("Math.min(outlineWidthLimits.max, Math.max(outlineWidthLimits.min, width))", OUTLINE_JS)
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            self.assertIn(f"{key}:", OUTLINE_JS)

    def test_the_width_is_remembered_when_the_resize_ends(self):
        body = OUTLINE_JS[OUTLINE_JS.index("function finishOutlineResize()"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('callApi("set_outline_width", outlineWidth)', body)
        # Thumbnails keep their size, so a resize renders nothing again.
        self.assertNotIn("forgetPageThumb(", body)
        # Not on every pixel of the drag: only the end calls it.
        move = OUTLINE_JS[OUTLINE_JS.index("const move = (e) =>"):]
        self.assertNotIn("finishOutlineResize", move[:move.index("\n")])
        self.assertIn("width: prefs.outline_width,", READING_JS)

    def test_16px_padding_and_one_left_edge(self):
        # The right 16px is counted to the outer edge, the 1px border included.
        self.assertIn("padding: 0 calc(var(--space-4) - 1px) var(--space-4) var(--space-4);", rule(".outline-tabpanel"))
        self.assertIn("padding: var(--space-3) var(--space-4) var(--space-2);", rule(".outline-tabs"))



class ThumbnailTests(unittest.TestCase):
    def test_thumbnails_are_128px_wide_at_the_pages_shape(self):
        listing = rule(".pages-list")
        self.assertIn("--page-thumb-width: 128px;", listing)
        self.assertIn("width: var(--page-thumb-width);", rule(".page-thumb"))
        self.assertIn("width: 100%;", rule(".page-thumb-image"))
        self.assertIn("img.style.aspectRatio =", OUTLINE_JS)

    def test_one_column_or_two_once_there_is_room(self):
        listing = rule(".pages-list")
        self.assertIn("display: grid;", listing)
        # Whole 128px columns only: auto-fill never stretches them.
        self.assertIn("grid-template-columns: repeat(auto-fill, var(--page-thumb-width));", listing)
        self.assertIn("justify-content: center;", listing)
        self.assertIn("column-gap: var(--space-4);", listing)

    def test_drawn_at_their_display_size_times_the_pixel_ratio(self):
        self.assertIn("const PAGE_THUMB_MAX_CSS_PX = 128;", OUTLINE_JS)
        self.assertIn("Math.round(PAGE_THUMB_MAX_CSS_PX * (window.devicePixelRatio || 1))", OUTLINE_JS)
        self.assertIn("redrawPageThumbsForPixelRatio();", READING_JS)

    def test_no_box_behind_them(self):
        self.assertIn("background: transparent;", rule(".page-thumb"))
        self.assertIn("padding: 0;", rule(".page-thumb"))
        self.assertNotIn("background-color", rule(".page-thumb:hover"))

    def test_hover_focus_and_the_bar_give_the_recent_cards_hover(self):
        lifted = rule(".page-thumb:is(:hover, :focus-visible, .is-previewed) .page-thumb-image")
        # components.css .card-lift:hover: the same edge colour, highlight,
        # shadow and lift, over the same duration and easing.
        self.assertIn("transform: translateY(var(--lift));", lifted)
        self.assertIn("box-shadow: 0 0 0 1px var(--card-hover-border), var(--card-highlight), var(--shadow-2);", lifted)
        image = rule(".page-thumb-image")
        self.assertIn("transform var(--dur-base) var(--ease-out)", image)
        self.assertIn("box-shadow var(--dur-base) var(--ease-out)", image)
        components = (ROOT / "frontend" / "shared" / "components.css").read_text(encoding="utf-8")
        card = re.search(r"\.card-lift:hover,\s*\.card-lift:focus-visible \{([^}]*)\}", components).group(1)
        for token in ("var(--card-hover-border)", "var(--card-highlight), var(--shadow-2)", "translateY(var(--lift))"):
            self.assertIn(token, card)
        self.assertIn("transform var(--dur-base) var(--ease-out)", re.search(r"\.card-lift \{([^}]*)\}", components).group(1))

    def test_the_current_page_is_a_ring_and_lifts_with_it(self):
        self.assertIn("box-shadow: 0 0 0 2px var(--accent);", rule('.page-thumb[aria-current="page"] .page-thumb-image'))
        self.assertIn("0 0 0 2px var(--accent), var(--card-highlight), var(--shadow-2)",
                      rule('.page-thumb[aria-current="page"]:is(:hover, :focus-visible, .is-previewed) .page-thumb-image'))
        self.assertIn("color: var(--accent-strong);", rule('.page-thumb[aria-current="page"]'))

    def test_the_number_is_centred_under_the_page(self):
        self.assertIn("align-self: center;", rule(".page-thumb-label"))


class OutlineRowTests(unittest.TestCase):
    def test_a_row_tints_on_hover_or_when_the_bar_points_into_it(self):
        self.assertIn("background-color: var(--state-hover);", rule(".outline-row:is(:hover, .is-previewed)"))

    def test_the_current_section_is_accent_with_a_bar(self):
        self.assertIn("color: var(--accent-strong);", rule('.outline-entry[aria-current="location"]'))
        self.assertNotIn("background-color", rule('.outline-entry[aria-current="location"]'))
        bar = rule('.outline-entry[aria-current="location"]::before')
        self.assertIn("width: 3px;", bar)
        self.assertIn("background-color: var(--accent);", bar)

    def test_titles_wrap_to_two_lines_with_the_whole_title_as_a_tooltip(self):
        title = rule(".outline-entry-title")
        self.assertIn("-webkit-line-clamp: 2;", title)
        self.assertIn("overflow: hidden;", title)
        self.assertIn("button.title = entry.title;", OUTLINE_JS)


PROGRESS_JS = (PAGES / "progress-bar.js").read_text(encoding="utf-8")


def function_body(source: str, signature: str) -> str:
    body = source[source.index(signature):]
    return body[:body.index("\n}\n")]


class ProgressBarWithThePanelOpenTests(unittest.TestCase):
    def test_it_never_widens(self):
        body = function_body(PROGRESS_JS, "function expandProgress()")
        self.assertLess(body.index("if (progressPanelOpen()) return;"), body.index('classList.add("is-expanded")'))

    def test_pointing_at_it_marks_the_page_in_the_panel_instead_of_a_tip(self):
        move = PROGRESS_JS[PROGRESS_JS.index('readingProgress.addEventListener("pointermove"'):]
        move = move[:move.index("\n});\n")]
        panel = move[move.index("if (progressPanelOpen()) {"):move.index("return;")]
        self.assertIn("previewProgressPage(index);", panel)
        self.assertNotIn("showProgressTip", panel)
        leave = PROGRESS_JS[PROGRESS_JS.index('readingProgress.addEventListener("pointerleave"'):]
        self.assertIn("previewProgressPage(null);", leave[:leave.index("\n});\n")])

    def test_a_click_still_jumps(self):
        down = PROGRESS_JS[PROGRESS_JS.index('readingProgress.addEventListener("pointerdown"'):]
        down = down[:down.index("\n});\n")]
        self.assertIn("if (progressPanelOpen()) previewProgressPage(index);", down)
        self.assertIn("jumpProgressTo(index);", down)

    def test_opening_the_panel_narrows_it_and_closing_ends_the_preview(self):
        body = function_body(PROGRESS_JS, "function progressPanelToggled(open)")
        self.assertIn("if (open) collapseProgress();", body)
        self.assertIn("else previewProgressPage(null);", body)
        self.assertIn("onToggle: (open) => progressPanelToggled(open),", READING_JS)
        self.assertIn("panelOpen: () => isOutlinePanelOpen(),", READING_JS)
        self.assertIn("preview: (index) => previewOutlinePage(index),", READING_JS)

    def test_the_panel_marks_the_thumbnail_or_the_entry_and_brings_it_into_view(self):
        body = function_body(OUTLINE_JS, "function previewOutlinePage(index)")
        self.assertIn("pageThumbEls[index]", body)
        self.assertIn("shownOutlineItem(currentOutlineItem(index))", body)
        self.assertIn('el.classList.add("is-previewed");', body)
        reveal = function_body(OUTLINE_JS, "function revealInPanel(el, scroller)")
        self.assertIn("if (box.top >= view.top && box.bottom <= view.bottom) return;", reveal)
        self.assertIn("scrollElementTo(scroller,", reveal)


class EmptyOutlineTests(unittest.TestCase):
    def section(self):
        start = HTML.index('<div class="outline-empty" id="outlineEmpty" hidden>')
        return HTML[start:HTML.index('<div class="outline-tabpanel" id="pagesTabPanel"')]

    def test_its_parts(self):
        empty = self.section()
        self.assertIn('data-icon="list_view"', empty)
        self.assertIn(">No outline in this PDF<", empty)
        self.assertIn(">This document doesn't include chapters or bookmarks.<", empty)
        self.assertRegex(empty, r'class="btn btn-secondary outline-empty-action" id="outlineBrowsePagesBtn"[^>]*>Browse pages<')
        self.assertIn("Try saying", empty)
        self.assertIn("<li>“show pages”</li>", empty)
        self.assertNotIn("This PDF has no outline.", HTML)

    def test_it_looks_like_the_recent_empty_state(self):
        icon = rule(".outline-empty-icon")
        for decl in ("width: 40px;", "border-radius: 50%;", "background-color: var(--accent-soft);"):
            self.assertIn(decl, icon)
        self.assertIn("font-weight: 600;", rule(".outline-empty-title"))
        self.assertIn("color: var(--text-muted);", rule(".outline-empty-body"))
        self.assertIn("background-color: var(--voice-soft);", rule(".outline-empty-chips li"))
        self.assertIn("justify-content: center;", rule(".outline-empty"))

    def test_browse_pages_opens_the_pages_tab(self):
        body = OUTLINE_JS[OUTLINE_JS.index('outlineBrowsePagesBtn.addEventListener("click"'):]
        self.assertIn("selectOutlineTab(PAGES_TAB);", body[:body.index("});")])


class ScrollGutterTests(unittest.TestCase):
    def test_24px_on_every_side_inside_the_scroll_range(self):
        scroller = rule(".book-scroll,\n.strip-scroll")
        self.assertIn("--page-gutter: var(--space-5);", scroller)
        self.assertIn("padding: var(--page-gutter) 0;", scroller)
        self.assertIn("padding-left: calc(var(--page-gutter) + max(0px, 2 * var(--page-offset, 0px)));", scroller)
        self.assertIn("padding-right: calc(var(--page-gutter) + max(0px, -2 * var(--page-offset, 0px)));", scroller)

    def test_the_strip_column_is_as_tall_as_its_pages(self):
        # Stretched to the scroller instead, the bottom gutter would follow the
        # column's box and the last page would end flush with the view.
        self.assertIn("height: fit-content;", rule(".strip-container"))
        self.assertIn("height: fit-content;", rule(".page-card"))

    def test_centring_keeps_to_the_same_gutter(self):
        self.assertIn("const PAGE_CENTER_CLEARANCE_PX = 24;", CENTERING_JS)


if __name__ == "__main__":
    unittest.main()
