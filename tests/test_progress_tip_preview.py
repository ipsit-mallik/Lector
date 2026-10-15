"""The progress bar tip's page preview (docs/DESIGN_SYSTEM.md, "Reader progress
bar"): while a page's image is being rendered the slot shimmers like Recent's
thumbnails, and the <img> is never shown without a source -- Chromium draws a
broken-image icon for one.
"""
import re
import unittest
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "frontend" / "pages"
READING_HTML = (PAGES / "reading.html").read_text(encoding="utf-8")
READING_CSS = (PAGES / "reading.css").read_text(encoding="utf-8")
PROGRESS_JS = (PAGES / "progress-bar.js").read_text(encoding="utf-8")


def function_body(name: str) -> str:
    body = PROGRESS_JS[PROGRESS_JS.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


class ProgressTipPreviewTests(unittest.TestCase):
    def test_the_image_sits_in_a_slot_and_starts_hidden(self):
        slot = re.search(r'<span class="progress-tip-thumb[^"]*" id="progressTipThumb">\s*(<img[^>]*>)',
                         READING_HTML)
        self.assertIsNotNone(slot)
        self.assertIn('id="progressTipImage"', slot.group(1))
        self.assertIn(" hidden", slot.group(1))
        self.assertNotRegex(slot.group(1), r"\ssrc=")

    def test_the_image_is_hidden_whenever_it_has_no_source(self):
        body = function_body("setProgressThumb")
        self.assertIn('progressTipImage.removeAttribute("src")', body)
        self.assertIn("progressTipImage.hidden = !loaded", body)

    def test_loading_shows_the_shared_shimmer(self):
        self.assertIn('progressTipThumb.classList.toggle("skeleton", state === "loading")',
                      function_body("setProgressThumb"))
        self.assertIn('setProgressThumb("loading")', function_body("showProgressThumb"))
        # Over white paper the translucent sweep would not show.
        rule = re.search(r"\.progress-tip-thumb\.skeleton \{([^}]*)\}", READING_CSS).group(1)
        self.assertIn("background-color: var(--state-hover)", rule)
        self.assertIn("box-shadow: none", rule)

    def test_a_failed_or_empty_render_ends_the_shimmer(self):
        body = function_body("showProgressThumb")
        self.assertIn('setProgressThumb(url || "empty")', body)
        self.assertNotRegex(body, r"catch \(err\) \{[^}]*return;")

    def test_nothing_writes_the_slot_directly(self):
        self.assertNotIn("progressTipThumb.src", PROGRESS_JS)


if __name__ == "__main__":
    unittest.main()
