"""Find in document: PyMuPDF text search, one time-boxed slice at a time.

`unittest` from the standard library, matching the rest of the suite. Run with:

    python -m unittest discover -s tests

The search runs in slices so a large document never holds the bridge (or the
reader) for the length of a whole-document scan: each call covers as many pages
as fit in its time budget and says where the next one starts. These tests pin
that contract, the grouping of a hit that wraps onto a second line into one
match (PyMuPDF hands back one rect per line, with nothing saying which belong
together, so the "3 of 17" counter would otherwise over-count), and the
difference between "no matches" and "this PDF has no text to search".
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402

from lector import api as api_module  # noqa: E402
from lector.features.reading import search  # noqa: E402
from lector.features.settings import store  # noqa: E402

# For tests about what a slice finds, not when it stops.
UNLIMITED = float("inf")

# Wraps "brown fox" across a line break: "...A brown" / "fox again here."
WRAPPING_TEXT = (
    "The quick brown fox jumps over the lazy dog. Brown bears are not foxes.\n"
    "A brown\nfox again here."
)


def _make_pdf(path: str, pages: list[str | None]) -> str:
    """One page per entry; None makes a page with no text layer at all."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text is not None:
            page.insert_textbox(fitz.Rect(50, 50, 200, 400), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path


class GroupFragmentsTests(unittest.TestCase):
    def _r(self, n):
        return fitz.Rect(n, n, n + 1, n + 1)

    def test_each_whole_hit_is_its_own_match(self):
        frags = [(self._r(1), "Brown"), (self._r(2), "brown")]
        self.assertEqual(search.group_fragments(frags, "brown"), [[self._r(1)], [self._r(2)]])

    def test_a_hit_split_across_lines_is_one_match(self):
        frags = [(self._r(1), "brown fox"), (self._r(2), "brown"), (self._r(3), "fox")]
        self.assertEqual(
            search.group_fragments(frags, "brown fox"),
            [[self._r(1)], [self._r(2), self._r(3)]],
        )

    def test_a_hyphenated_break_joins_without_a_space(self):
        frags = [(self._r(1), "exam-"), (self._r(2), "ple")]
        self.assertEqual(search.group_fragments(frags, "example"), [[self._r(1), self._r(2)]])

    def test_a_fragment_that_never_completes_still_counts_once(self):
        # Better one match drawn slightly wrong than a hit that vanishes.
        frags = [(self._r(1), "brown"), (self._r(2), "bear")]
        self.assertEqual(
            search.group_fragments(frags, "brown fox"), [[self._r(1)], [self._r(2)]]
        )


class SearchSliceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-search-")
        self.addCleanup(self.dir.cleanup)

    def _open(self, pages):
        doc = fitz.open(_make_pdf(str(Path(self.dir.name) / "doc.pdf"), pages))
        self.addCleanup(doc.close)
        return doc

    def test_matches_are_case_insensitive_and_grouped(self):
        doc = self._open([WRAPPING_TEXT])
        result = search.search_slice(doc, "BROWN FOX", 0, None, budget_s=UNLIMITED)
        self.assertEqual(result["next_page"], None)
        self.assertEqual(result["text_pages"], 1)
        (page,) = result["pages"]
        self.assertEqual(page["page_index"], 0)
        self.assertEqual(len(page["hits"]), 2)
        self.assertEqual([len(hit) for hit in page["hits"]], [1, 2])
        self.assertEqual((page["width"], page["height"]), (doc[0].rect.width, doc[0].rect.height))
        x0, y0, x1, y1 = page["hits"][0][0]
        self.assertTrue(0 <= x0 < x1 <= page["width"] and 0 <= y0 < y1 <= page["height"])

    def test_pages_without_a_match_are_left_out(self):
        doc = self._open(["nothing here", WRAPPING_TEXT, "nor here"])
        result = search.search_slice(doc, "fox", 0, None, budget_s=UNLIMITED)
        self.assertEqual([p["page_index"] for p in result["pages"]], [1])
        self.assertEqual(result["text_pages"], 3)

    def test_a_scanned_page_counts_as_having_no_text(self):
        doc = self._open([None, None])
        result = search.search_slice(doc, "fox", 0, None, budget_s=UNLIMITED)
        self.assertEqual(result, {"pages": [], "next_page": None, "text_pages": 0})

    def test_a_slice_stops_when_its_time_is_up_and_says_where_to_resume(self):
        doc = self._open(["fox"] * 5)
        # Read once at the start, then once after each page searched.
        ticks = iter([0.0, 0.01, 0.2, 0.3, 0.4])
        result = search.search_slice(doc, "fox", 1, 4, budget_s=0.05, clock=lambda: next(ticks))
        # The budget is checked after each page, so a slice always makes progress.
        self.assertEqual([p["page_index"] for p in result["pages"]], [1, 2])
        self.assertEqual(result["next_page"], 3)

    def test_the_end_page_is_exclusive_so_a_wrapped_search_can_stop_where_it_began(self):
        doc = self._open(["fox"] * 4)
        result = search.search_slice(doc, "fox", 0, 2, budget_s=UNLIMITED)
        self.assertEqual([p["page_index"] for p in result["pages"]], [0, 1])
        self.assertIsNone(result["next_page"])


class ApiFindInDocumentTests(unittest.TestCase):
    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.settings_dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.settings_dir.cleanup)
        self.pdf_dir = tempfile.TemporaryDirectory(prefix="lector-pdfs-")
        self.addCleanup(self.pdf_dir.cleanup)

        # Each call here should cover the whole (tiny) document in one slice,
        # however slow the machine running the suite is.
        budget = mock.patch.object(search, "SLICE_BUDGET_S", UNLIMITED)
        budget.start()
        self.addCleanup(budget.stop)

        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)

    def _open(self, pages):
        self.api.open_pdf(_make_pdf(str(Path(self.pdf_dir.name) / "doc.pdf"), pages))

    def test_finds_across_the_document(self):
        self._open(["a fox", "no", "two fox fox"])
        result = self.api.find_in_document("fox", 0)
        self.assertEqual(result["page_count"], 3)
        self.assertEqual([len(p["hits"]) for p in result["pages"]], [1, 2])
        self.assertIsNone(result["next_page"])

    def test_a_blank_query_searches_nothing(self):
        self._open(["a fox"])
        result = self.api.find_in_document("   ", 0)
        self.assertEqual(result["pages"], [])
        self.assertIsNone(result["next_page"])

    def test_out_of_range_pages_are_clamped_not_raised(self):
        self._open(["a fox", "fox"])
        self.assertEqual(self.api.find_in_document("fox", -5)["pages"][0]["page_index"], 0)
        self.assertEqual(self.api.find_in_document("fox", 99)["pages"], [])
        self.assertEqual(len(self.api.find_in_document("fox", 0, 99)["pages"]), 2)

    def test_an_overlong_query_is_cut_rather_than_scanned(self):
        self._open(["a fox"])
        result = self.api.find_in_document("fox" + " " * 10 + "x" * 1000, 0)
        self.assertEqual(result["pages"], [])

    def test_with_no_document_open_there_is_nothing_to_search(self):
        result = self.api.find_in_document("fox", 0)
        self.assertEqual(result, {"pages": [], "next_page": None, "text_pages": 0, "page_count": 0})


if __name__ == "__main__":
    unittest.main()
