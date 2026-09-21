"""Character and word selection, and PyMuPDF highlight-annotation writing.

Mouse-drag selection (Milestone 3) works in characters, not words, so a drag
can stop mid-word the way it does in Adobe Reader — snapping the selection to
whole words made it impossible to highlight anything less than a full word.
Milestone 7's speech-to-match still matches whole spoken words against
`words_on_page`, per docs/PRD.md's "word-level matching is the baseline", so
both granularities are kept side by side. `merge_line_rects` and
`add_highlight` are written against `LineBounded` rather than either concrete
type, since both a char run and a word run reduce to the same annotation
shape. Per docs/ARCHITECTURE.md, annotations/ owns both the matching logic
and the PyMuPDF write layer together because a match result becomes
annotation coordinates directly, so keeping them in one feature avoids
indirection.
"""
from dataclasses import dataclass
from typing import Protocol, Sequence

import fitz  # PyMuPDF

# docs/DESIGN_SYSTEM.md "Highlight background" token, as 0..1 floats for
# PyMuPDF's Annot.set_colors.
HIGHLIGHT_COLOR = (0xF7 / 255, 0xDE / 255, 0x7A / 255)


class LineBounded(Protocol):
    """Whatever `merge_line_rects`/`add_highlight` need: a box plus the line
    it sits on, so adjacent items on the same line can be merged into one bar
    regardless of whether they're characters or whole words."""

    rect: fitz.Rect
    block_no: int
    line_no: int


@dataclass(frozen=True)
class Char:
    rect: fitz.Rect
    text: str
    block_no: int
    line_no: int
    char_no: int


@dataclass(frozen=True)
class Word:
    rect: fitz.Rect
    text: str
    block_no: int
    line_no: int
    word_no: int


def chars_on_page(page: fitz.Page) -> list[Char]:
    """All characters on `page` in reading order (top-to-bottom, left-to-right).

    Drives mouse-drag selection: hit-testing per character (rather than per
    word) is what lets a drag stop mid-word. `page.get_text("words")` has no
    per-character equivalent, so this reads the raw glyph layout instead and
    assigns block/line numbers by position in `rawdict`'s own block/line
    nesting, which is already in reading order.
    """
    raw = page.get_text("rawdict")
    chars: list[Char] = []
    for block_no, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:  # skip image blocks, keep only text
            continue
        for line_no, line in enumerate(block.get("lines", [])):
            char_no = 0
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    x0, y0, x1, y1 = ch["bbox"]
                    chars.append(Char(fitz.Rect(x0, y0, x1, y1), ch["c"], block_no, line_no, char_no))
                    char_no += 1
    return chars


def chars_between(chars: list[Char], start_idx: int, end_idx: int) -> list[Char]:
    """Contiguous reading-order run covering both indices, inclusive."""
    lo, hi = sorted((start_idx, end_idx))
    return chars[lo:hi + 1]


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


def merge_line_rects(items: Sequence[LineBounded]) -> list[fitz.Rect]:
    """One rect per line of text, spanning the selected items on that line.

    `items` is a contiguous reading-order run (what `chars_between`/
    `words_between` return), so items belonging to the same line are always
    adjacent in it and a single pass is enough. Merging matters because a
    rect per *item* leaves a gap at every character or space, which reads as
    a row of disconnected slivers rather than the continuous bar desktop
    annotators draw over a highlighted line — and it is also what the
    frontend's live selection preview paints, so the applied highlight has to
    be composed the same way or the shape would change under the reader's
    cursor the moment they release the mouse.
    """
    merged: list[tuple[tuple[int, int], fitz.Rect]] = []
    for item in items:
        key = (item.block_no, item.line_no)
        if merged and merged[-1][0] == key:
            merged[-1][1].include_rect(item.rect)
        else:
            merged.append((key, fitz.Rect(item.rect)))
    return [rect for _, rect in merged]


def add_highlight(page: fitz.Page, items: Sequence[LineBounded]):
    """Write `items` as one real PDF highlight annotation (not a UI overlay).

    One quad per line of the selection (see `merge_line_rects`) so a selection
    spanning multiple lines highlights correctly, the same way desktop PDF
    annotators compose multi-line highlights.
    """
    if not items:
        return None
    quads = [rect.quad for rect in merge_line_rects(items)]
    annot = page.add_highlight_annot(quads=quads)
    annot.set_colors(stroke=HIGHLIGHT_COLOR)
    annot.update()
    return annot


def highlight_count(page: fitz.Page) -> int:
    return sum(1 for a in (page.annots() or []) if a.type[0] == fitz.PDF_ANNOT_HIGHLIGHT)
