"""Tests for spoken-phrase-to-page-text matching (Milestone 7).

`unittest`, matching `test_command_grammar.py` and `test_voice_engine.py`.
Run with:

    python -m unittest discover -s tests

`Word` needs only a `fitz.Rect`, not a real page, so these build word lists by
hand rather than opening a PDF — the geometry is what's under test, not
PyMuPDF's text extraction.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402

from lector.features.annotations import highlight_matcher as hm  # noqa: E402
from lector.features.annotations.highlighter import Word  # noqa: E402


def _line_of_words(texts: list[str], y0: float = 0.0, y1: float = 10.0,
                    block_no: int = 0, line_no: int = 0) -> list[Word]:
    """One line of words, left to right, 10pt apart, all on the same line."""
    return [
        Word(fitz.Rect(i * 10, y0, i * 10 + 8, y1), text, block_no, line_no, i)
        for i, text in enumerate(texts)
    ]


class NormalizeWordTests(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(hm.normalize_word("Fox"), "fox")

    def test_strips_punctuation(self):
        self.assertEqual(hm.normalize_word("fox,"), "fox")
        self.assertEqual(hm.normalize_word("fox."), "fox")
        self.assertEqual(hm.normalize_word('"fox"'), "fox")


class VisibleWordIndicesTests(unittest.TestCase):
    def test_selects_words_whose_box_overlaps_the_range(self):
        words = _line_of_words(["a", "b"], y0=100, y1=110) + _line_of_words(
            ["c", "d"], y0=200, y1=210
        )
        self.assertEqual(hm.visible_word_indices(words, 90, 120), [0, 1])
        self.assertEqual(hm.visible_word_indices(words, 190, 220), [2, 3])

    def test_excludes_words_entirely_outside_the_range(self):
        words = _line_of_words(["a"], y0=100, y1=110)
        self.assertEqual(hm.visible_word_indices(words, 200, 300), [])

    def test_includes_a_word_only_partially_overlapping_the_range(self):
        words = _line_of_words(["a"], y0=100, y1=110)
        self.assertEqual(hm.visible_word_indices(words, 105, 300), [0])


class FindWordMatchTests(unittest.TestCase):
    def test_exact_phrase_match(self):
        words = _line_of_words(["the", "quick", "brown", "fox"])
        pages = [(0, words, [0, 1, 2, 3])]
        match = hm.find_word_match(pages, "quick brown")
        self.assertIsNotNone(match)
        self.assertEqual((match.page_index, match.start_idx, match.end_idx), (0, 1, 2))

    def test_tolerates_a_misheard_word(self):
        words = _line_of_words(["the", "quick", "brown", "fox"])
        pages = [(0, words, [0, 1, 2, 3])]
        match = hm.find_word_match(pages, "quick brow")
        self.assertIsNotNone(match)
        self.assertEqual((match.start_idx, match.end_idx), (1, 2))

    def test_no_match_below_threshold_returns_none(self):
        words = _line_of_words(["the", "quick", "brown", "fox"])
        pages = [(0, words, [0, 1, 2, 3])]
        self.assertIsNone(hm.find_word_match(pages, "elephant sandwich"))

    def test_ignores_words_outside_the_viewport(self):
        words = _line_of_words(["the", "quick", "brown", "fox"])
        # Only "the quick" is visible; "brown fox" is off-screen.
        pages = [(0, words, [0, 1])]
        self.assertIsNone(hm.find_word_match(pages, "brown fox"))

    def test_ties_go_to_the_earliest_occurrence(self):
        words = _line_of_words(["fox", "ran", "fox", "hid"])
        pages = [(0, words, [0, 1, 2, 3])]
        match = hm.find_word_match(pages, "fox")
        self.assertEqual(match.start_idx, 0)

    def test_match_never_spans_a_visibility_gap(self):
        # Indices 0-1 and 3-4 are two separate visible runs; "fox jumped"
        # would require bridging the gap at index 2, which must not happen.
        words = _line_of_words(["the", "fox", "lazily", "jumped", "high"])
        pages = [(0, words, [0, 1, 3, 4])]
        self.assertIsNone(hm.find_word_match(pages, "fox jumped"))

    def test_searches_later_pages_in_viewport_order(self):
        page0_words = _line_of_words(["hello", "world"])
        page1_words = _line_of_words(["quick", "brown", "fox"])
        pages = [(0, page0_words, [0, 1]), (2, page1_words, [0, 1, 2])]
        match = hm.find_word_match(pages, "brown fox")
        self.assertEqual((match.page_index, match.start_idx, match.end_idx), (2, 1, 2))

    def test_empty_query_matches_nothing(self):
        words = _line_of_words(["fox"])
        self.assertIsNone(hm.find_word_match([(0, words, [0])], ""))


class ExtendToSentenceTests(unittest.TestCase):
    def test_extends_backward_and_forward_to_sentence_punctuation(self):
        words = _line_of_words(["See", "Spot", "run.", "The", "fox", "hid.", "End"])
        # Match is "fox" at index 4, inside the second sentence.
        start, end = hm.extend_to_sentence(words, 4, 4)
        self.assertEqual((start, end), (3, 5))
        self.assertEqual([w.text for w in words[start:end + 1]], ["The", "fox", "hid."])

    def test_already_at_sentence_boundaries_is_unchanged(self):
        words = _line_of_words(["See", "Spot", "run."])
        start, end = hm.extend_to_sentence(words, 0, 2)
        self.assertEqual((start, end), (0, 2))

    def test_no_punctuation_extends_to_the_page_edges(self):
        words = _line_of_words(["quick", "brown", "fox"])
        start, end = hm.extend_to_sentence(words, 1, 1)
        self.assertEqual((start, end), (0, 2))

    def test_stops_at_the_first_sentence_boundary_encountered(self):
        words = _line_of_words(["First.", "Second", "word.", "Third."])
        start, end = hm.extend_to_sentence(words, 1, 1)
        self.assertEqual((start, end), (1, 2))


if __name__ == "__main__":
    unittest.main()
