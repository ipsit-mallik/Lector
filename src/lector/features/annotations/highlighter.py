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
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

import pymupdf

from lector.shared import theme


def _rgb(hex_color: str) -> tuple[float, float, float]:
    """`#RRGGBB` as the 0..1 floats PyMuPDF's Annot.set_colors takes."""
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


# The palette's values are theme.css's `--highlight-<name>` tokens, read from
# there so the swatch a reader picks and the colour written into the PDF
# cannot drift apart.
COLOR_NAMES = theme.HIGHLIGHT_COLOR_NAMES
DEFAULT_COLOR = theme.DEFAULT_HIGHLIGHT_COLOR
HIGHLIGHT_COLORS: dict[str, tuple[float, float, float]] = {
    name: _rgb(theme.shared_token(f"highlight-{name}")) for name in COLOR_NAMES
}

# How far (per 0..1 channel) a stored /C may sit from a palette colour and
# still count as it. PDF stores colours as reals, and PyMuPDF hands them back
# through float32 (0.6 reads as 0.6000000238), so exact comparison would
# never match; a highlight another app wrote in its own yellow does not match
# either, and is shown with no swatch selected rather than mislabelled.
_COLOR_TOLERANCE = 0.02


class LineBounded(Protocol):
    """Whatever `merge_line_rects`/`add_highlight` need: a box plus the line
    it sits on, so adjacent items on the same line can be merged into one bar
    regardless of whether they're characters or whole words."""

    rect: pymupdf.Rect
    block_no: int
    line_no: int


@dataclass(frozen=True)
class Char:
    rect: pymupdf.Rect
    text: str
    block_no: int
    line_no: int
    char_no: int


@dataclass(frozen=True)
class Word:
    rect: pymupdf.Rect
    text: str
    block_no: int
    line_no: int
    word_no: int


def chars_on_page(page: pymupdf.Page) -> list[Char]:
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
                    chars.append(Char(pymupdf.Rect(x0, y0, x1, y1), ch["c"], block_no, line_no, char_no))
                    char_no += 1
    return chars


def chars_between(chars: list[Char], start_idx: int, end_idx: int) -> list[Char]:
    """Contiguous reading-order run covering both indices, inclusive."""
    lo, hi = sorted((start_idx, end_idx))
    return chars[lo:hi + 1]


def words_on_page(page: pymupdf.Page) -> list[Word]:
    """All words on `page` in reading order (top-to-bottom, left-to-right)."""
    raw = page.get_text("words")
    words = [
        Word(pymupdf.Rect(x0, y0, x1, y1), text, block_no, line_no, word_no)
        for x0, y0, x1, y1, text, block_no, line_no, word_no in raw
    ]
    words.sort(key=lambda w: (w.block_no, w.line_no, w.word_no))
    return words


def words_between(words: list[Word], start_idx: int, end_idx: int) -> list[Word]:
    """Contiguous reading-order run covering both indices, inclusive."""
    lo, hi = sorted((start_idx, end_idx))
    return words[lo:hi + 1]


def merge_line_rects(items: Sequence[LineBounded]) -> list[pymupdf.Rect]:
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
    merged: list[tuple[tuple[int, int], pymupdf.Rect]] = []
    for item in items:
        key = (item.block_no, item.line_no)
        if merged and merged[-1][0] == key:
            merged[-1][1].include_rect(item.rect)
        else:
            merged.append((key, pymupdf.Rect(item.rect)))
    return [rect for _, rect in merged]


def _set_rectangular_appearance(
    page: pymupdf.Page, annot, rects: list[pymupdf.Rect], rgb: tuple[float, float, float]
) -> None:
    """Repaint `annot` as one plain rectangle per line, replacing MuPDF's default.

    MuPDF draws a /Highlight annotation marker-pen style: each quad is filled
    as a path with bezier end-caps that bulge sideways by about a sixth of the
    line height. Across a whole word that reads as a soft edge, but a single
    character is narrower than the caps themselves, so the highlight becomes
    an oval roughly twice the glyph's width that visibly washes over the
    characters on either side — measured at +5.8pt of overspill per side,
    independent of how much text is selected. Adobe Reader fills a tight
    rectangle instead, which is the only shape that stays inside the glyph
    once selection is character-level.

    The quads themselves are already correct; only the appearance stream is
    wrong, so this rewrites that stream and leaves /Subtype, /QuadPoints and
    /C untouched — it stays a real highlight that other viewers list as one.
    """
    # MuPDF's own stream references an ExtGState carrying /BM /Multiply, which
    # is what keeps the glyphs readable under the fill. Reuse whatever name it
    # generated rather than assuming one: emitting no `gs` at all would paint
    # an opaque block over the text.
    gs_name = re.search(rb"/(\w+)\s+gs", annot._getAP() or b"")
    ops = ["q"]
    if gs_name:
        ops.append(f"/{gs_name.group(1).decode()} gs")
    ops.append("{:g} {:g} {:g} rg".format(*rgb))
    to_pdf_space = ~page.transformation_matrix
    for rect in rects:
        box = rect * to_pdf_space
        ops.append(
            f"{box.x0:g} {min(box.y0, box.y1):g} "
            f"{box.x1 - box.x0:g} {abs(box.y1 - box.y0):g} re"
        )
    ops += ["f", "Q"]
    annot._setAP(("\n".join(ops) + "\n").encode())


RGB = tuple[float, float, float]


def _paint(page: pymupdf.Page, annot, rects: list[pymupdf.Rect], rgb: RGB) -> None:
    """Give `annot` the colour `rgb` in both places a viewer can read it from:
    /C, which a viewer that draws its own highlights uses, and the appearance
    stream, which every other viewer (and Lector) renders as-is. Writing only
    one of them is how a highlight ends up a different colour in another app."""
    annot.set_colors(stroke=rgb)
    # Must precede the appearance rewrite: update() is what generates the
    # stream, so overwriting it any earlier would simply be undone here.
    annot.update()
    _set_rectangular_appearance(page, annot, rects, rgb)


def add_highlight_rects(page: pymupdf.Page, rects: Sequence[pymupdf.Rect], rgb: RGB):
    """Write one real PDF highlight annotation covering `rects`, one per line.

    Takes a raw colour rather than a palette name because undoing the removal
    of another app's highlight has to put back that app's own colour."""
    if not rects:
        return None
    rects = [pymupdf.Rect(rect) for rect in rects]
    annot = page.add_highlight_annot(quads=[rect.quad for rect in rects])
    _paint(page, annot, rects, rgb)
    return annot


def add_highlight(page: pymupdf.Page, items: Sequence[LineBounded], color: str = DEFAULT_COLOR):
    """Write `items` as one real PDF highlight annotation (not a UI overlay).

    One quad per line of the selection (see `merge_line_rects`) so a selection
    spanning multiple lines highlights correctly, the same way desktop PDF
    annotators compose multi-line highlights.
    """
    if not items:
        return None
    return add_highlight_rects(page, merge_line_rects(items), HIGHLIGHT_COLORS[color])


def highlight_rects(annot) -> list[pymupdf.Rect]:
    """The per-line boxes a highlight covers, read back from its /QuadPoints
    (four vertices per line, in the same page space `add_highlight_rects`
    was given) — what a recolour repaints and an undone removal re-adds."""
    points = annot.vertices or []
    return [pymupdf.Quad(points[i:i + 4]).rect for i in range(0, len(points) - 3, 4)]


def stroke_rgb(annot) -> RGB | None:
    """`annot`'s /C colour, or None when it has none (a viewer then picks)."""
    stroke = (annot.colors or {}).get("stroke") or []
    return tuple(stroke) if len(stroke) == 3 else None


def color_name(rgb: RGB | None) -> str | None:
    """Which palette colour `rgb` is, or None for a colour Lector did not
    write (another app's highlight in its own shade)."""
    if rgb is None:
        return None
    for name, palette_rgb in HIGHLIGHT_COLORS.items():
        if all(abs(a - b) <= _COLOR_TOLERANCE for a, b in zip(rgb, palette_rgb)):
            return name
    return None


def recolor_highlight(page: pymupdf.Page, annot, rgb: RGB) -> None:
    """Repaint an existing highlight in `rgb`, keeping its shape, its /NM id
    and anything another app attached to it (author, note)."""
    _paint(page, annot, highlight_rects(annot), rgb)


@dataclass(frozen=True)
class Highlight:
    """One highlight annotation on a page, as the editor needs it. `xref` is
    the annotation's PDF object number: stable for the life of the open
    document, so it is what the frontend sends back to name a highlight."""

    xref: int
    rects: tuple[pymupdf.Rect, ...]
    color: str | None


def highlights_on_page(page: pymupdf.Page) -> list[Highlight]:
    """Every highlight annotation on `page`, Lector's and other apps' alike."""
    return [
        Highlight(annot.xref, tuple(highlight_rects(annot)), color_name(stroke_rgb(annot)))
        for annot in (page.annots(types=[pymupdf.PDF_ANNOT_HIGHLIGHT]) or [])
    ]


def highlight_count(page: pymupdf.Page) -> int:
    return sum(1 for a in (page.annots() or []) if a.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT)
