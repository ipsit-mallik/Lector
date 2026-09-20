"""Word selection and PyMuPDF highlight-annotation writing.

Milestone 3 drives this from mouse drag selection only. Milestone 7 adds
speech-to-match on top of the same `add_highlight` writer — per
docs/ARCHITECTURE.md, annotations/ owns both the matching logic and the
PyMuPDF write layer together because a match result becomes annotation
coordinates directly, so keeping them in one feature avoids indirection.
"""
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF

# docs/DESIGN_SYSTEM.md "Highlight background" token, as 0..1 floats for
# PyMuPDF's Annot.set_colors.
HIGHLIGHT_COLOR = (0xF7 / 255, 0xDE / 255, 0x7A / 255)


@dataclass(frozen=True)
class Word:
    rect: fitz.Rect
    text: str
    block_no: int
    line_no: int
    word_no: int


def words_on_page(page: fitz.Page) -> list[Word]:
    """All words on `page` in reading order (top-to-bottom, left-to-right)."""
    raw = page.get_text("words")
    words = [
        Word(fitz.Rect(x0, y0, x1, y1), text, block_no, line_no, word_no)
        for x0, y0, x1, y1, text, block_no, line_no, word_no in raw
    ]
    words.sort(key=lambda w: (w.block_no, w.line_no, w.word_no))
    return words


def nearest_word_index(words: list[Word], point: fitz.Point) -> Optional[int]:
    """Index of the word whose bounding box is closest to `point`.

    Distance is 0 whenever the point already falls inside a word's box, so a
    press/release squarely on a word always resolves to that word.
    """
    if not words:
        return None
    best_idx = 0
    best_dist = None
    for i, w in enumerate(words):
        dx = max(w.rect.x0 - point.x, 0.0, point.x - w.rect.x1)
        dy = max(w.rect.y0 - point.y, 0.0, point.y - w.rect.y1)
        dist = dx * dx + dy * dy
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def words_between(words: list[Word], start_idx: int, end_idx: int) -> list[Word]:
    """Contiguous reading-order run covering both indices, inclusive."""
    lo, hi = sorted((start_idx, end_idx))
    return words[lo:hi + 1]


def add_highlight(page: fitz.Page, words: list[Word]):
    """Write `words` as one real PDF highlight annotation (not a UI overlay).

    One quad per word so a selection spanning multiple lines highlights
    correctly, the same way desktop PDF annotators compose multi-line
    highlights.
    """
    if not words:
        return None
    quads = [w.rect.quad for w in words]
    annot = page.add_highlight_annot(quads=quads)
    annot.set_colors(stroke=HIGHLIGHT_COLOR)
    annot.update()
    return annot


def highlight_count(page: fitz.Page) -> int:
    return sum(1 for a in (page.annots() or []) if a.type[0] == fitz.PDF_ANNOT_HIGHLIGHT)
