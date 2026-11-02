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

A highlight is one of two shapes: a `TextShape` (a /Highlight over lines of
text) or an `InkShape` (a /Ink marker stroke, drawn where there is no text to
snap to: a photo, a barcode, blank paper). Both are "highlights" to the rest
of the app — counted, listed, recoloured, removed and undone the same way.
"""
import math
import re
from dataclasses import dataclass
from itertools import islice
from typing import Iterator, Protocol, Sequence

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
Point = tuple[float, float]

# The freehand marker: a 14pt stroke, so it covers a line of body text with a
# little to spare, in the same `HIGHLIGHT_COLORS` as a text highlight, at 60%
# through Multiply, so what is under it stays visible and darkens the way a
# real marker does, and a stroke reads as the same colour as a text highlight
# beside it (a text highlight is the colour itself, multiplied; 40% looked a
# pale tint of it). The colour is never pre-lightened: the opacity is applied
# once, here. One stroke is one path drawn once, so where it crosses itself it
# does not double up. Read by the frontend's preview (`api.get_ink_style`) so
# the two cannot drift.
INK_WIDTH_PT = 14.0
INK_OPACITY = 0.6
INK_BLEND_MODE = pymupdf.PDF_BM_Multiply


@dataclass(frozen=True)
class TextShape:
    """A /Highlight's per-line boxes, in MuPDF's unrotated page space."""

    rects: tuple[pymupdf.Rect, ...]


@dataclass(frozen=True)
class InkShape:
    """An /Ink annotation's strokes (each a polyline, in MuPDF's unrotated
    page space) and how they are drawn. Width, opacity and blend mode are
    kept rather than assumed so another app's ink, removed and undone, comes
    back drawn as it was; Lector's own strokes are always one stroke with the
    `INK_*` values."""

    strokes: tuple[tuple[Point, ...], ...]
    width: float = INK_WIDTH_PT
    opacity: float = INK_OPACITY
    blend_mode: str | None = INK_BLEND_MODE


Shape = TextShape | InkShape


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


def add_ink(page: pymupdf.Page, shape: InkShape, rgb: RGB):
    """Write `shape` as one real PDF /Ink annotation: its colour in /C, its
    width in /BS, its opacity in /CA and its blend mode in /BM, all set
    before `update()` so the appearance stream MuPDF generates carries them
    (round caps and joins are MuPDF's own for ink), and another viewer draws
    the same stroke Lector does."""
    strokes = [[tuple(point) for point in stroke] for stroke in shape.strokes if stroke]
    if not strokes:
        return None
    annot = page.add_ink_annot(strokes)
    annot.set_colors(stroke=rgb)
    annot.set_border(width=shape.width)
    annot.set_opacity(shape.opacity)
    if shape.blend_mode:
        annot.set_blendmode(shape.blend_mode)
    annot.update()
    return annot


def add_shape(page: pymupdf.Page, shape: Shape, rgb: RGB):
    """Write `shape` as the annotation it describes; the live Annot."""
    if isinstance(shape, InkShape):
        return add_ink(page, shape, rgb)
    return add_highlight_rects(page, shape.rects, rgb)


def is_ink(annot) -> bool:
    return annot.type[0] == pymupdf.PDF_ANNOT_INK


# Bounds on another app's ink as the editor lists it (a target's SVG, a hit
# test per stroke): it comes from a PDF Lector did not write, so what is
# listed is kept to what a real drawing is. Not applied to what an undone
# removal writes back — that is the drawing as the PDF had it, every stroke
# of a long handwritten note included, so undo loses nothing.
INK_MAX_STROKES = 64
INK_MAX_STROKE_POINTS = 4096
# Applied to both: a width, unlike a stroke, is not the reader's content, and
# an absurd one only makes MuPDF build an appearance the size of the page.
INK_MAX_WIDTH_PT = 100.0


def shape_of(annot, *, bounded: bool) -> Shape:
    """What `annot` looks like. `bounded=False` is the drawing as the PDF
    has it (only points that are not finite numbers dropped), enough to write
    it again exactly: an undone removal or recolour. `bounded=True` is what
    the editor lists: kept on the page and within INK_MAX_*."""
    if not is_ink(annot):
        return TextShape(tuple(highlight_rects(annot)))
    width = (annot.border or {}).get("width")
    opacity = annot.opacity
    return InkShape(
        tuple(_iter_ink_strokes(annot, bounded)),
        min(float(width), INK_MAX_WIDTH_PT) if isinstance(width, (int, float)) and width > 0 else 1.0,
        float(opacity) if isinstance(opacity, (int, float)) and 0 <= opacity <= 1 else 1.0,
        annot.blendmode or None,
    )


def _ink_point(value) -> Point | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = value
    if not all(isinstance(c, (int, float)) and math.isfinite(c) for c in (x, y)):
        return None
    return (float(x), float(y))


def _iter_ink_strokes(annot, bounded: bool) -> Iterator[tuple[Point, ...]]:
    """An ink annotation's strokes, read defensively: each a tuple of finite
    points, strokes with none left out. `bounded` also keeps them on the page
    and within INK_MAX_* (see `shape_of`). Lazy, so `_is_listed` can stop at
    the first stroke.

    `annot.vertices` reads /Vertices, /L, /QuadPoints or /CL before /InkList
    when an annotation has them, and then returns one flat list of points
    rather than a list of strokes, which is read here as one stroke."""
    raw = annot.vertices or []
    if raw and all(_ink_point(item) is not None for item in raw):
        raw = [raw]
    page = annot.parent
    bounds = page.rect * page.derotation_matrix if bounded and page is not None else None
    budget = INK_MAX_STROKE_POINTS if bounded else None
    for stroke in islice(raw, INK_MAX_STROKES if bounded else None):
        if not isinstance(stroke, (list, tuple)):
            continue
        points = [p for p in map(_ink_point, islice(stroke, budget)) if p is not None]
        if bounds is not None:
            points = [
                (min(max(x, bounds.x0), bounds.x1), min(max(y, bounds.y0), bounds.y1)) for x, y in points
            ]
        if not points:
            continue
        yield tuple(points)
        if budget is not None:
            budget -= len(points)
            if budget <= 0:
                return


def ink_bounds(shape: InkShape) -> pymupdf.Rect:
    """The box the stroke paints: its points, out by half its width. Not the
    annotation's /Rect, which MuPDF pads further for the round caps."""
    points = [point for stroke in shape.strokes for point in stroke]
    if not points:
        return pymupdf.Rect()
    half = shape.width / 2
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return pymupdf.Rect(min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)


def recolor_highlight(page: pymupdf.Page, annot, rgb: RGB) -> None:
    """Repaint an existing highlight in `rgb`, keeping its shape, its /NM id
    and anything another app attached to it (author, note). An ink stroke
    keeps its own width, opacity and blend mode: `update()` regenerates the
    appearance from the entries already on it."""
    if is_ink(annot):
        annot.set_colors(stroke=rgb)
        annot.update()
        return
    _paint(page, annot, highlight_rects(annot), rgb)


@dataclass(frozen=True)
class Highlight:
    """One highlight annotation on a page, as the editor needs it. `xref` is
    the annotation's PDF object number: stable for the life of the open
    document, so it is what the frontend sends back to name a highlight.
    `rects` is a text highlight's per-line boxes, or an ink stroke's bounding
    box (what the editor is placed under); `shape` says which it is."""

    xref: int
    rects: tuple[pymupdf.Rect, ...]
    color: str | None
    shape: Shape | None = None


# The annotation types Lector treats as highlights. Everything else on a page
# (links, notes, another app's shapes) is left alone and not counted.
HIGHLIGHT_TYPES = (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_INK)


def highlight_annots(page: pymupdf.Page):
    return page.annots(types=list(HIGHLIGHT_TYPES)) or []


def _is_listed(annot) -> bool:
    """Whether `highlights_on_page` lists `annot`: every text highlight, and
    an ink annotation with something drawable in it (one with nothing has
    nothing to click either)."""
    return not is_ink(annot) or next(_iter_ink_strokes(annot, bounded=True), None) is not None


def highlights_on_page(page: pymupdf.Page) -> list[Highlight]:
    """Every highlight and ink stroke on `page`, Lector's and other apps'
    alike, in the order they are drawn (the last is on top)."""
    found = []
    for annot in highlight_annots(page):
        if not _is_listed(annot):
            continue
        shape = shape_of(annot, bounded=True)
        rects = shape.rects if isinstance(shape, TextShape) else (ink_bounds(shape),)
        found.append(Highlight(annot.xref, tuple(rects), color_name(stroke_rgb(annot)), shape))
    return found


def highlight_count(page: pymupdf.Page) -> int:
    """How many highlights `highlights_on_page` lists, without building them:
    the footer's count and the editor's targets always agree."""
    return sum(1 for annot in highlight_annots(page) if _is_listed(annot))
