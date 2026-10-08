"""The header's control group sits against the page's right edge on every page.

Open PDF belongs to Recent only. It used to hide on Favorites with `.is-absent`
(visibility), which keeps its slot, so search and the grid/list toggle stopped
one button-width short of the list's right edge. A control the page does not have
must leave the layout (`hidden`, display: none); `.is-absent` stays for a control
the page has but cannot use yet (loading, empty list), so those do not shift when
the list arrives.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
HOME_JS = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
HEADER_CSS = (FRONTEND / "shared" / "library-header.css").read_text(encoding="utf-8")
COMPONENTS_CSS = (FRONTEND / "shared" / "components.css").read_text(encoding="utf-8")


def _function_body(source, name):
    match = re.search(rf"function {name}\([^)]*\)\s*\{{(.*?)\n\}}", source, re.S)
    assert match, f"{name} not found"
    return match.group(1)


class OpenPdfLeavesTheLayoutTest(unittest.TestCase):
    def test_switching_section_shows_or_removes_open_pdf_at_once(self):
        body = _function_body(HOME_JS, "syncSectionChrome")
        self.assertRegex(body, r'openPdfBtn\.hidden\s*=\s*homeSection\s*!==\s*"recent"')

    def test_waiting_for_the_list_still_only_hides_it_by_visibility(self):
        body = _function_body(HOME_JS, "syncHeaderControls")
        self.assertRegex(body, r'openPdfBtn\.classList\.toggle\("is-absent", !hasItems\)')

    def test_the_list_arriving_does_not_decide_whether_open_pdf_has_a_slot(self):
        self.assertNotIn("homeSection", _function_body(HOME_JS, "syncHeaderControls"))

    def test_hidden_really_removes_the_button(self):
        self.assertRegex(COMPONENTS_CSS, r"\.btn\[hidden\]\s*\{\s*display:\s*none;?\s*\}")

    def test_the_header_open_button_rule_does_not_override_hidden(self):
        rule = re.search(r"\.header-open-btn\s*\{([^}]*)\}", HEADER_CSS)
        self.assertIsNotNone(rule)
        self.assertNotIn("display", rule.group(1))


class FirstPaintTest(unittest.TestCase):
    """Favorites arriving from Settings must not paint Open PDF's slot, then
    drop it when the script at the bottom of the page runs."""

    def _inline_script(self):
        scripts = re.findall(r"<script>(.*?)</script>", INDEX_HTML, re.S)
        return next((s for s in scripts if "lector-home-section" in s), "")

    def test_inline_hand_off_script_removes_open_pdf_from_the_layout(self):
        self.assertRegex(self._inline_script(), r'getElementById\("openPdfBtn"\)\.hidden\s*=\s*true')

    def test_inline_hand_off_script_follows_the_open_pdf_button(self):
        self.assertLess(INDEX_HTML.index('id="openPdfBtn"'), INDEX_HTML.index("lector-home-section"))


if __name__ == "__main__":
    unittest.main()
