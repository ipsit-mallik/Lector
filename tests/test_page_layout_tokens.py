"""One page shell, one set of layout tokens (docs/DESIGN_SYSTEM.md, "Page layout").

Every page that has a header band (Recent, Favorites, Settings) sits in the same
`.content` shell and the same `.page-header`, so the title never moves between
pages, and the gap from the header to what follows is one token, not a number each
page sets for itself. Dialogs share their side and top padding, and the reader's
toolbar and footer share one side padding. Checked from the source files; the real
geometry is measured separately in the running app.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SPACE_SCALE = {f"--space-{i}": px for i, px in enumerate((4, 8, 12, 16, 24, 32, 40, 48), start=1)}


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def _token_px(name: str) -> int:
    """The pixel value of a layout token defined as `var(--space-N)` in theme.css."""
    match = re.search(rf"{re.escape(name)}:\s*var\((--space-\d)\)", _read("shared", "theme.css"))
    assert match, f"{name} is not defined as a spacing-scale step in theme.css"
    return SPACE_SCALE[match.group(1)]


def _rule(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector list is exactly `selector`."""
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


class PageShellTokensTest(unittest.TestCase):
    def test_the_header_to_content_gap_is_32px_from_one_token(self):
        self.assertEqual(_token_px("--page-header-gap"), 32)

    def test_page_header_takes_its_bottom_gap_from_the_token(self):
        rule = _rule(_read("shared", "library-header.css"), ".page-header")
        self.assertIn("margin-bottom: var(--page-header-gap)", rule)

    def test_no_page_sets_its_own_header_gap(self):
        for page_css in ("home.css", "settings.css", "onboarding.css"):
            css = _read("pages", page_css)
            for declarations in re.findall(r"\.page-header\s*\{([^}]*)\}", css):
                self.assertNotIn("margin", declarations, page_css)

    def test_the_shell_padding_comes_from_tokens(self):
        rule = _rule(_read("pages", "home.css"), ".content")
        self.assertIn("padding: var(--page-pad-top) var(--page-pad-x) var(--page-pad-bottom)", rule)
        self.assertEqual(
            (_token_px("--page-pad-top"), _token_px("--page-pad-x"), _token_px("--page-pad-bottom")),
            (24, 32, 32),
        )

    def test_settings_sections_are_spaced_by_the_section_token(self):
        rule = _rule(_read("pages", "settings.css"), ".section")
        self.assertIn("margin-bottom: var(--section-gap)", rule)
        self.assertGreater(_token_px("--section-gap"), 16)  # more than the gap between cards inside one

    def test_settings_stays_left_aligned_on_the_title_with_a_reading_width(self):
        css = _read("pages", "settings.css")
        self.assertRegex(css, r"\.content > \*\s*\{\s*max-width:\s*760px")
        self.assertNotRegex(css, r"\.content\s*\{[^}]*margin:\s*0 auto")


class DialogTokensTest(unittest.TestCase):
    def test_dialog_sides_are_one_token(self):
        self.assertEqual(_token_px("--dialog-pad-x"), 32)
        components = _read("shared", "components.css")
        self.assertIn("padding: var(--dialog-pad-y) var(--dialog-pad-x) var(--dialog-pad-y)", _rule(components, ".dialog-body"))
        self.assertIn("var(--dialog-pad-x)", _rule(components, ".dialog-footer"))

    def test_the_open_and_reference_dialogs_use_the_same_side_padding(self):
        self.assertIn("var(--dialog-pad-x)", _rule(_read("shared", "file-browser.css"), ".browser-crumbs"))
        reference = _read("shared", "reference.css")
        self.assertIn("var(--dialog-pad-x)", _rule(reference, ".reference-band"))
        self.assertIn("var(--dialog-pad-x)", _rule(reference, ".reference-body"))

    def test_the_browser_dialog_has_no_dead_bottom_padding_rule(self):
        # It was overridden by .dialog-body (components.css is read after file-browser.css),
        # so the Open dialog has always had the shared 24px; the rule only misled.
        self.assertNotRegex(_read("shared", "file-browser.css"), r"\.dialog-body--browser\s*\{[^}]*padding-bottom")


class ReaderChromeTokensTest(unittest.TestCase):
    def test_toolbar_and_footer_share_one_side_padding(self):
        reading = _read("pages", "reading.css")
        self.assertIn("var(--chrome-pad-x)", _rule(reading, ".topbar"))
        self.assertIn("var(--chrome-pad-x)", _rule(reading, ".footer"))
        self.assertEqual(_token_px("--chrome-pad-x"), 24)

    def test_the_back_button_icon_lines_up_with_the_footer(self):
        # The toolbar's left padding is the footer's less the back button's own 8px inset,
        # so the icon and the footer's first control share a left edge.
        rule = _rule(_read("pages", "reading.css"), ".topbar")
        self.assertIn("calc(var(--chrome-pad-x) - var(--space-2))", rule)


if __name__ == "__main__":
    unittest.main()
