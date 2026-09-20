"""Word selection and PyMuPDF highlight-annotation writing.

Milestone 3 drives this from mouse drag selection only. Milestone 7 adds
speech-to-match on top of the same `add_highlight` writer — per
docs/ARCHITECTURE.md, annotations/ owns both the matching logic and the
PyMuPDF write layer together because a match result becomes annotation
coordinates directly, so keeping them in one feature avoids indirection.
"""
from dataclasses import dataclass

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


def words_between(words: list[Word], start_idx: int, end_idx: int) -> list[Word]:
    """Contiguous reading-order run covering both indices, inclusive."""
    lo, hi = sorted((start_idx, end_idx))
    return words[lo:hi + 1]


def merge_line_rects(words: list[Word]) -> list[fitz.Rect]:
    """One rect per line of text, spanning the selected words on that line.

    `words` is a contiguous reading-order run (what `words_between` returns),
    so words belonging to the same line are always adjacent in it and a single
    pass is enough. Merging matters because a rect per *word* leaves a gap at
    every space, which reads as a row of disconnected blocks rather than the
    continuous bar desktop annotators draw over a highlighted line — and it is
    also what the frontend's live selection preview paints, so the applied
    highlight has to be composed the same way or the shape would change under
    the reader's cursor the moment they release the mouse.
    """
    merged: list[tuple[tuple[int, int], fitz.Rect]] = []
    for w in words:
        key = (w.block_no, w.line_no)
        if merged and merged[-1][0] == key:
            merged[-1][1].include_rect(w.rect)
        else:
            merged.append((key, fitz.Rect(w.rect)))
    return [rect for _, rect in merged]


def add_highlight(page: fitz.Page, words: list[Word]):
    """Write `words` as one real PDF highlight annotation (not a UI overlay).

    One quad per line of the selection (see `merge_line_rects`) so a selection
    spanning multiple lines highlights correctly, the same way desktop PDF
    annotators compose multi-line highlights.
    """
    if not words:
        return None
    quads = [rect.quad for rect in merge_line_rects(words)]
    annot = page.add_highlight_annot(quads=quads)
    annot.set_colors(stroke=HIGHLIGHT_COLOR)
    annot.update()
    return annot


def highlight_count(page: fitz.Page) -> int:
    return sum(1 for a in (page.annots() or []) if a.type[0] == fitz.PDF_ANNOT_HIGHLIGHT)
