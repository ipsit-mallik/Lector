import functools
import logging
import math
import os
import threading
from dataclasses import dataclass, replace

import pymupdf

from lector.features.annotations import highlighter
from lector.features.file_tools import engine as file_tool_engine
from lector.features.reading import doc_gate, ocr, ocr_cache, open_errors, page_changes, page_plan, safe_write, search
from lector.features.reading.open_errors import OpenError
from lector.features.reading.outline import OutlineEntry, outline_tree
from lector.features.reading.safe_write import SaveError
from lector.shared import highlight_options

log = logging.getLogger(__name__)

def _reads(method):
    """A method that reads the open document's pages: it waits while a page
    change is applied, undone or redone, and the change waits for it
    (`doc_gate.py`)."""

    @functools.wraps(method)
    def read(self, *args, **kwargs):
        with self._gate.reading():
            return method(self, *args, **kwargs)

    return read


@dataclass
class _HighlightRef:
    """Which annotation a run of edits is about, by xref.

    Mutable on purpose, and shared by every edit to the same highlight: an
    undone removal (or a redone add) writes a *new* annotation with a new
    xref, and each later edit to that highlight has to find the new one.
    Holding live Annot handles instead does not work — PyMuPDF invalidates
    one the moment it is deleted, and also once the Page wrapper it came from
    is garbage collected.
    """

    xref: int


@dataclass(frozen=True)
class _Look:
    """What a highlight is at one point in its history: its colour and its
    shape (a text highlight's boxes, or an ink stroke's path)."""

    rgb: highlighter.RGB
    shape: highlighter.Shape


@dataclass(frozen=True)
class _Edit:
    """One reversible change to one highlight: from `before` to `after`,
    where None means "no highlight". An add is None -> look, a removal
    look -> None, a recolour a change of colour, and an ink stroke
    straightened on release a change of shape, so undo and redo are the
    same operation run in opposite directions.

    Colours are raw RGB rather than palette names because a highlight another
    app wrote can be recoloured or removed too, and undoing that has to put
    back its own colour. `info` is the note/author another app may have
    attached, which a re-added annotation gets back.
    """

    page_index: int
    ref: _HighlightRef
    info: tuple[tuple[str, str], ...]
    before: _Look | None
    after: _Look | None
    # Which reader gesture made it (one drag of the opacity slider), so the
    # changes one gesture makes are one undo step (`set_highlight_appearance`).
    gesture: str | None = None


# What `Annot.set_info` accepts back from `Annot.info`; the rest (id, dates)
# is regenerated for a re-added annotation.
_RESTORED_INFO_KEYS = ("content", "title", "subject")

# Opacities closer than this are the same: a PDF stores /CA as a real and
# PyMuPDF reads it back through float32 (0.6 as 0.6000000238).
_OPACITY_TOLERANCE = 0.002


def _valid_opacity(opacity, style: str) -> float:
    """`opacity` (0..1) when it is one the popover offers, else `style`'s
    default."""
    low = highlight_options.OPACITY_MIN_PCT / 100
    if isinstance(opacity, (int, float)) and not isinstance(opacity, bool) and low <= opacity <= 1:
        return float(opacity)
    return highlight_options.opacity_fraction(highlight_options.DEFAULT_OPACITY_PCT[style])


def _same_look(a: highlighter.Shape, b: highlighter.Shape) -> bool:
    """Whether two shapes draw the same: the same geometry, opacity and (for
    ink) width."""
    if not highlighter.same_geometry(a, b) or abs(a.opacity - b.opacity) > _OPACITY_TOLERANCE:
        return False
    return not isinstance(a, highlighter.InkShape) or abs(a.width - b.width) < 1e-6


def _open_checked(path: str, password: str | None) -> tuple["pymupdf.Document", bool]:
    """`path` opened, unlocked and checked to have a first page that loads,
    and whether it needed a password; or OpenError. Asked in this order so the reader hears the real reason:
    whether the file is there and readable is Python's question (MuPDF only
    has one error for all of it), what is in it is MuPDF's."""
    if not os.path.isfile(path):
        raise OpenError(open_errors.MISSING)
    try:
        with open(path, "rb") as f:
            empty = not f.read(1)
    except FileNotFoundError as exc:  # gone since the check above
        raise OpenError(open_errors.MISSING) from exc
    except OSError as exc:
        raise OpenError(open_errors.UNREADABLE) from exc
    if empty:
        raise OpenError(open_errors.EMPTY)
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except pymupdf.FileNotFoundError as exc:  # gone since the check above
        raise OpenError(open_errors.MISSING) from exc
    except pymupdf.FileDataError as exc:  # EmptyFileError included
        raise OpenError(open_errors.NOT_A_PDF) from exc
    except ValueError as exc:
        # What MuPDF says of some files it can't make sense of, but also of
        # a mistake in how it was called: logged, so the second is visible.
        log.warning("MuPDF refused a PDF", exc_info=True)
        raise OpenError(open_errors.NOT_A_PDF) from exc
    except Exception as exc:
        log.exception("MuPDF could not open a PDF")
        raise OpenError(open_errors.UNKNOWN) from exc
    try:
        needed = _check_opened(doc, password)
    except BaseException:
        doc.close()
        raise
    return doc, needed


def _check_opened(doc: "pymupdf.Document", password: str | None) -> bool:
    if not doc.is_pdf:
        raise OpenError(open_errors.NOT_A_PDF)
    # A PDF with only an owner password (printing or copying restricted)
    # has needs_pass 0 and opens like any other; only one that can't be read
    # without a password asks for it. Either of its passwords unlocks it.
    needed = _unlock(doc, password)
    if len(doc) == 0:
        raise OpenError(open_errors.NO_PAGES)
    try:
        doc.load_page(0)
    except Exception as exc:
        raise OpenError(open_errors.NOT_A_PDF) from exc
    return needed


def _unlock(doc: "pymupdf.Document", password: str | None) -> bool:
    """Unlock `doc` with `password` if it needs one, and say whether it did;
    OpenError when it does and the password is missing or wrong.

    `needs_pass` is read once, before `authenticate`: PyMuPDF 1.28 locks the
    document again when it is read afterwards, and every page then comes
    back empty, with no error."""
    if not doc.needs_pass:
        return False
    if password is None:
        raise OpenError(open_errors.PASSWORD)
    try:
        unlocked = doc.authenticate(password)
    except (TypeError, ValueError) as exc:  # e.g. a lone surrogate from the page
        raise OpenError(open_errors.WRONG_PASSWORD) from exc
    if not unlocked:
        raise OpenError(open_errors.WRONG_PASSWORD)
    return True


class PdfDocument:
    DEFAULT_ZOOM = 1.0
    ZOOM_STEP = 0.25
    # 25% to 400%: a factor of 4 either side of 100%, which is where the zoom
    # popover's logarithmic slider puts its centre (frontend/pages/zoom-fit.js).
    MIN_ZOOM = 0.25
    MAX_ZOOM = 4.0
    # Kept to 0.01%, finer than the whole percent the reader sees, so Fit
    # width and Fit page can land within a pixel of their margins.
    ZOOM_DECIMALS = 4
    # 100% is the paper's real size: a PDF point is 1/72 inch and a CSS pixel
    # 1/96, so an A4 page (595pt) is 794 CSS px wide at 100%.
    CSS_PX_PER_POINT = 96 / 72
    # The most pixels one page bitmap may have. A page is drawn at the
    # screen's device pixel ratio so it is sharp; past this (400% on a high
    # DPI screen would be over 30 million) it is drawn at fewer and the web
    # view scales it up, rather than taking seconds to render and encode.
    MAX_RENDER_PIXELS = 16_000_000

    def __init__(self):
        self._doc = None
        self._path = None
        self._password: str | None = None
        self._page_index = 0
        self._zoom = self.DEFAULT_ZOOM
        self._dirty = False
        # Every highlight change — adding one, recolouring it, removing it —
        # is one `_Edit` on these stacks, so Undo takes back whichever came
        # last. Edits name their annotation by xref through a shared
        # `_HighlightRef` rather than holding Annot handles (see there).
        self._undo_stack: list[_Edit] = []
        self._redo_stack: list[_Edit] = []
        # The one `_HighlightRef` per highlight currently in the document that
        # any edit names, by its current xref — so every edit to a highlight
        # shares the ref that gets repointed when it is re-added.
        self._refs: dict[int, _HighlightRef] = {}
        # Page changes (Organise pages, Milestone F2; page_changes.py). The
        # stacks above also hold `page_changes.PageChange` entries. Once one
        # has been applied, saves collect unused objects so a deleted page
        # doesn't stay in the file; `_history_trimmed` is the one-shot "older
        # changes can no longer be undone"; `_page_sources` are the files
        # Organise's Insert opened, by source id, until Done.
        self._pages_changed = False
        self._history_trimmed = False
        self._page_sources: dict[str, "pymupdf.Document"] = {}
        self._next_source = 0
        # Scanned pages (see `ocr.py`), per page index of the open document.
        # `_ocr_text` holds the boxes OCR found and is what chars_on_page/
        # words_on_page hand out in place of the (empty) text layer. The lock
        # is there because results land from the OCR thread while bridge
        # threads read; `_generation` tells a result for this document apart
        # from one for a PDF that was open when its job started.
        self._ocr_lock = threading.Lock()
        self._generation = 0
        self._scanned: dict[int, bool] = {}
        self._ocr_keys: dict[int, str] = {}
        self._ocr_status: dict[int, str] = {}
        self._ocr_text: dict[int, tuple[list[highlighter.Char], list[highlighter.Word]]] = {}
        # Bridge calls each run on their own thread, so two saves (Ctrl+S
        # pressed twice, or a close during a save) must not interleave.
        self._save_lock = threading.Lock()
        # Page reads (`@_reads`) and page changes, kept apart (doc_gate.py).
        self._gate = doc_gate.DocGate()

    def open(self, path: str, password: str | None = None):
        """Open `path`, unlocking it with `password` if it has one. Raises
        OpenError, and then the document that was open is still open, as it
        was: the new file is checked in full before the old one is let go."""
        doc, needed_password = _open_checked(path, password)
        # Never in the middle of a save, which closes and reopens the PDF
        # around moving the new file into place.
        with self._gate.changing(), self._save_lock:
            self._switch_to(doc, path, password if needed_password else None)

    def _switch_to(self, doc: "pymupdf.Document", path: str, password: str | None) -> None:
        if self._doc:
            self._doc.close()
        with self._ocr_lock:
            # Together, so nothing sees the new document with the old one's
            # password or OCR state. The password is kept only while this PDF
            # is open: a save has to open the file it wrote to check it, and
            # that file needs the same password.
            self._doc = doc
            self._path = path
            self._password = password
            self._generation += 1
            self._scanned.clear()
            self._ocr_keys.clear()
            self._ocr_status.clear()
            self._ocr_text.clear()
        self._page_index = 0
        # Per-document state: a PDF with no saved position must start at the
        # default zoom, not at whatever the previously open one was left on.
        # `Api._restore_position` overrides this when a record exists.
        self._zoom = self.DEFAULT_ZOOM
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._refs.clear()
        self._pages_changed = False
        self._history_trimmed = False
        self.drop_page_sources()

    def close(self):
        # As a change: no page read is left holding the closed document.
        with self._gate.changing():
            self.drop_page_sources()
            if self._doc:
                self._doc.close()
                self._doc = None
        with self._ocr_lock:
            # A job still running for the closed PDF is then dropped.
            self._generation += 1
            self._password = None

    @property
    def is_password_protected(self) -> bool:
        return self._password is not None

    @property
    def is_open(self) -> bool:
        return self._doc is not None

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def page_count(self) -> int:
        return len(self._doc) if self._doc else 0

    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def zoom(self) -> float:
        return self._zoom

    def go_to_page(self, index: int) -> bool:
        if self._doc and 0 <= index < len(self._doc):
            self._page_index = index
            return True
        return False

    def next_page(self) -> bool:
        return self.go_to_page(self._page_index + 1)

    def prev_page(self) -> bool:
        return self.go_to_page(self._page_index - 1)

    def zoom_in(self):
        self._zoom = min(self.MAX_ZOOM, round(self._zoom + self.ZOOM_STEP, 2))

    def zoom_out(self):
        self._zoom = max(self.MIN_ZOOM, round(self._zoom - self.ZOOM_STEP, 2))

    def set_zoom(self, value: float):
        self._zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, round(value, self.ZOOM_DECIMALS)))

    def render_current_page(self):
        return self.render_page(self._page_index)

    def css_scale(self) -> float:
        """CSS pixels per PDF point at the current zoom."""
        return self._zoom * self.CSS_PX_PER_POINT

    @staticmethod
    def _css_px(length: float) -> int:
        """Whole CSS pixels for a length, rounded up as PyMuPDF sizes a
        pixmap, so at a device pixel ratio of 1 the bitmap is drawn 1:1 rather
        than squeezed by a pixel. The epsilon keeps float noise (794.0000001)
        from adding one."""
        return math.ceil(length - 1e-6)

    @_reads
    def page_css_size(self, index: int) -> tuple[int, int]:
        """Page `index`'s size on screen in CSS pixels at the current zoom."""
        rect = self._doc[index].rect
        scale = self.css_scale()
        return (self._css_px(rect.width * scale), self._css_px(rect.height * scale))

    @_reads
    def render_page(self, index: int, device_scale: float = 1.0):
        """Page `index` as a bitmap for a screen with `device_scale` device
        pixels per CSS pixel (the web view's devicePixelRatio), so it is
        drawn 1:1 with the screen's pixels at its CSS size
        (`page_css_size`), down to MAX_RENDER_PIXELS."""
        # A page past the end: a request made before a page change shortened
        # the document (the strip's look-ahead), answered after it.
        if not self._doc or not 0 <= index < len(self._doc):
            return None
        page = self._doc[index]
        scale = self.css_scale() * min(max(float(device_scale), 1.0), 4.0)
        area = page.rect.width * page.rect.height * scale * scale
        if area > self.MAX_RENDER_PIXELS:
            scale *= (self.MAX_RENDER_PIXELS / area) ** 0.5
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    @_reads
    def render_page_sized(self, index: int, device_scale: float = 1.0):
        """`render_page` and `page_css_size` from one read of the document,
        so a page change can't fall between them: (pixmap, width, height),
        or None for a page that isn't there."""
        pix = self.render_page(index, device_scale)
        if pix is None:
            return None
        return (pix, *self.page_css_size(index))

    @_reads
    def render_thumbnail(self, index: int, width_px: int):
        """Page `index` scaled to `width_px` pixels wide, whatever the zoom —
        for the outline panel's Pages tab and the progress bar's hover
        preview. None for a page that isn't there."""
        if not self._doc or not 0 <= index < len(self._doc):
            return None
        return self._thumbnail(self._doc[index], width_px)

    @staticmethod
    def _thumbnail(page: "pymupdf.Page", width_px: int):
        scale = max(1, width_px) / page.rect.width if page.rect.width else 1
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    @_reads
    def outline(self) -> list[OutlineEntry]:
        """The PDF's bookmarks as a nested tree (see outline.py); [] when it
        has none."""
        if not self._doc:
            return []
        return outline_tree(self._doc.get_toc(simple=True), len(self._doc))

    @_reads
    def page_sizes(self) -> list[tuple[int, int]]:
        """Page dimensions in CSS pixels at the current zoom, without
        rendering pixmaps.

        Lets the strip layout lay out correctly-sized placeholders for every
        page up front (cheap: only reads each page's bounding box) before
        lazily rendering actual page images as they scroll into view.
        """
        if not self._doc:
            return []
        scale = self.css_scale()
        return [
            (self._css_px(page.rect.width * scale), self._css_px(page.rect.height * scale))
            for page in self._doc
        ]

    @_reads
    def search(self, needle: str, start: int, end: int | None) -> dict:
        """One time-boxed slice of a find-in-document search (see search.py)."""
        if not self._doc:
            return {"pages": [], "next_page": None, "text_pages": 0}
        return search.search_slice(self._doc, needle, start, end)

    # ------------------------------------------------------------------ #
    # Annotations (Milestone 3)                                           #
    # ------------------------------------------------------------------ #

    def chars_on_current_page(self) -> list[highlighter.Char]:
        return self.chars_on_page(self._page_index)

    @_reads
    def chars_on_page(self, page_index: int) -> list[highlighter.Char]:
        if not self._doc:
            return []
        ocr_text = self._ocr_text_for(page_index)
        if ocr_text is not None:
            return list(ocr_text[0])
        return highlighter.chars_on_page(self._doc[page_index])

    def words_on_current_page(self) -> list[highlighter.Word]:
        return self.words_on_page(self._page_index)

    @_reads
    def words_on_page(self, page_index: int) -> list[highlighter.Word]:
        if not self._doc:
            return []
        ocr_text = self._ocr_text_for(page_index)
        if ocr_text is not None:
            return list(ocr_text[1])
        return highlighter.words_on_page(self._doc[page_index])

    @_reads
    def page_size_points(self, page_index: int) -> tuple[float, float]:
        """A page's size in PDF points (i.e. at zoom 1.0).

        The frontend needs this to map the word boxes it is handed onto the
        page image it is actually displaying, whose pixel size depends on the
        current zoom (and, mid-zoom, on a preview scale that has not been
        re-rendered yet) — dividing by this gives it a zoom-independent
        conversion instead of one that has to be kept in step with rendering.
        """
        if not self._doc:
            return (0.0, 0.0)
        rect = self._doc[page_index].rect
        return (rect.width, rect.height)

    @_reads
    def as_shown(self, page_index: int, rects) -> list[pymupdf.Rect]:
        """`rects` moved from where MuPDF reports text to where the reader sees it.

        MuPDF gives text, search hits and highlight quads on the *unrotated*
        page, and highlights are written there too, but it renders the page
        turned by its /Rotate, and `page_size_points` is that turned size.
        Every box the reading view lays over the page image has to be on the
        image, so each one crossing the bridge goes through this. A page with
        no rotation comes back as it was.
        """
        if not rects:  # also what a closed document's empty word list gets
            return []
        to_shown = self._doc[page_index].rotation_matrix
        return [pymupdf.Rect(rect) * to_shown for rect in rects]

    @_reads
    def words_as_shown(self, page_index: int) -> list[highlighter.Word]:
        """`words_on_page`, each box `as_shown`, in the same order: for
        telling which words are in a region of the page the reader sees."""
        words = self.words_on_page(page_index)
        shown = self.as_shown(page_index, [w.rect for w in words])
        return [replace(word, rect=rect) for word, rect in zip(words, shown)]

    @_reads
    def highlight_char_indices(
        self,
        page_index: int,
        start_idx: int,
        end_idx: int,
        color: str = highlighter.DEFAULT_COLOR,
        *,
        style: str = highlight_options.DEFAULT_STYLE,
        opacity: float | None = None,
    ):
        """Highlight (or underline, or strike through: `style`) the
        reading-order run between two character indices, at `opacity` (0..1;
        None is the style's default).

        Indices are into this page's `chars_on_page` list — the same list the
        frontend was handed to draw its live selection, so what gets written
        is exactly the run the reader saw selected under the cursor.
        """
        chars = self.chars_on_page(page_index)
        if not chars:
            return None
        last = len(chars) - 1
        start_idx = max(0, min(int(start_idx), last))
        end_idx = max(0, min(int(end_idx), last))
        selected = highlighter.chars_between(chars, start_idx, end_idx)
        return self._commit_highlight(page_index, selected, color, style, opacity)

    @_reads
    def highlight_word_indices(
        self,
        page_index: int,
        start_idx: int,
        end_idx: int,
        color: str = highlighter.DEFAULT_COLOR,
        *,
        style: str = highlight_options.DEFAULT_STYLE,
        opacity: float | None = None,
    ):
        """Highlight (or underline, or strike through) the reading-order run
        between two word indices; `style` and `opacity` as for
        `highlight_char_indices`.

        Indices are into this page's `words_on_page` list — the same list the
        frontend was handed to draw its live selection, so what gets written
        is exactly the run the reader saw selected under the cursor.
        """
        words = self.words_on_page(page_index)
        if not words:
            return None
        last = len(words) - 1
        start_idx = max(0, min(int(start_idx), last))
        end_idx = max(0, min(int(end_idx), last))
        selected = highlighter.words_between(words, start_idx, end_idx)
        return self._commit_highlight(page_index, selected, color, style, opacity)

    def _commit_highlight(
        self,
        page_index: int,
        selected: list[highlighter.LineBounded],
        color: str,
        style: str,
        opacity: float | None,
    ) -> int | None:
        """Write the markup and record it; returns its xref, or None when
        there was nothing to mark. An unknown colour or style is the default
        one, and an opacity out of range the style's default: what reaches
        the PDF is always something the popover offers."""
        if color not in highlighter.HIGHLIGHT_COLORS:
            color = highlighter.DEFAULT_COLOR
        if not highlight_options.is_style(style):
            style = highlight_options.DEFAULT_STYLE
        rgb = highlighter.HIGHLIGHT_COLORS[color]
        shape = highlighter.TextShape(
            tuple(highlighter.merge_line_rects(selected)), style, _valid_opacity(opacity, style)
        )
        page = self._doc[page_index]  # held while `annot` is used (see _move_highlight)
        annot = highlighter.add_text_markup(page, shape, rgb, self._chars_for(page_index, shape))
        if annot is None:
            return None
        self._record(_Edit(page_index, self._ref(annot.xref), (), None, _Look(rgb, shape)))
        return annot.xref

    def _chars_for(self, page_index: int, shape: highlighter.Shape) -> list[highlighter.Char]:
        """The page's text when writing `shape` needs it (an underline or a
        strike-through is placed on the lines' baselines), else nothing."""
        return self.chars_on_page(page_index) if highlighter.needs_text(shape) else []

    @_reads
    def add_ink_stroke(
        self,
        page_index: int,
        drawn,
        final=None,
        color: str = highlighter.DEFAULT_COLOR,
        *,
        width: float = highlighter.INK_WIDTH_PT,
        opacity: float = highlighter.INK_OPACITY,
    ) -> int | None:
        """Draw a freehand marker stroke; returns its xref, or None when there
        is no page or no point to draw.

        `drawn` is the stroke as the reader drew it (smoothed), and `final`,
        when given, what it became on release (straightened). Both arrive on
        the page as shown, the space the reader drew in, and are taken back
        to MuPDF's unrotated page space here — the inverse of `as_shown`.
        A straightened stroke is recorded as two edits, drawing it and then
        straightening it, so the first Undo gives back the line as drawn and
        the second removes it.
        """
        if not self._doc or not 0 <= page_index < len(self._doc):
            return None
        drawn_path = self._from_shown(page_index, drawn)
        if not drawn_path:
            return None
        if color not in highlighter.HIGHLIGHT_COLORS:
            color = highlighter.DEFAULT_COLOR
        rgb = highlighter.HIGHLIGHT_COLORS[color]
        if not (isinstance(width, (int, float)) and 0 < width <= highlighter.INK_MAX_WIDTH_PT):
            width = highlighter.INK_WIDTH_PT
        opacity = _valid_opacity(opacity, highlight_options.HIGHLIGHT)
        page = self._doc[page_index]  # held while `annot` is used
        drawn_look = _Look(rgb, highlighter.InkShape((drawn_path,), float(width), opacity))
        annot = highlighter.add_ink(page, drawn_look.shape, rgb)
        if annot is None:
            return None
        ref = self._ref(annot.xref)
        self._record(_Edit(page_index, ref, (), None, drawn_look))
        final_path = self._from_shown(page_index, final)
        if final_path:
            final_look = _Look(rgb, replace(drawn_look.shape, strokes=(final_path,)))
            edit = _Edit(page_index, ref, (), drawn_look, final_look)
            self._move_highlight(edit, drawn_look, final_look)
            self._record(edit)
        return ref.xref

    def _from_shown(self, page_index: int, points) -> tuple[highlighter.Point, ...]:
        """`points` on the page as shown, kept on it, in unrotated page space."""
        if not points:
            return ()
        page = self._doc[page_index]
        shown = page.rect
        to_page = page.derotation_matrix
        path = []
        for x, y in points:
            point = pymupdf.Point(
                min(max(float(x), shown.x0), shown.x1),
                min(max(float(y), shown.y0), shown.y1),
            ) * to_page
            path.append((point.x, point.y))
        return tuple(path)

    def shown_strokes(self, page_index: int, shape: highlighter.InkShape) -> list[list[highlighter.Point]]:
        """An ink shape's strokes on the page as shown, as `as_shown` does
        for boxes."""
        to_shown = self._doc[page_index].rotation_matrix
        return [
            [tuple(pymupdf.Point(x, y) * to_shown) for x, y in stroke]
            for stroke in shape.strokes
        ]

    @_reads
    def highlights_on_page(self, page_index: int) -> list[highlighter.Highlight]:
        if not self._doc or not 0 <= page_index < len(self._doc):
            return []
        return highlighter.highlights_on_page(self._doc[page_index])

    @_reads
    def text_in_rects(self, page_index: int, rects) -> str:
        """The page text inside `rects`, one line each, joined into one line."""
        if not self._doc or not 0 <= page_index < len(self._doc):
            return ""
        ocr_text = self._ocr_text_for(page_index)
        if ocr_text is not None:
            # A scanned page's image has no text to clip, so the words OCR
            # found are matched to the rects by where each one's centre is.
            return " ".join(
                w.text for rect in rects for w in ocr_text[1]
                if pymupdf.Rect(rect).contains((w.rect.tl + w.rect.br) / 2)
            ).strip()
        page = self._doc[page_index]
        return " ".join(" ".join(page.get_textbox(rect).split()) for rect in rects).strip()

    # ------------------------------------------------------------------ #
    # Scanned pages (OCR)                                                  #
    # ------------------------------------------------------------------ #

    @_reads
    def ocr_status(self, page_index: int) -> str | None:
        """Where a page stands on OCR (one of `ocr`'s statuses), or None for
        a page with its own text layer or nothing on it to read.

        The first ask about a scanned page also looks in the on-disk cache,
        so a page read in an earlier session — or in the file this one was
        saved from — has its text at once instead of being read again.
        """
        if not self._doc or not 0 <= page_index < len(self._doc):
            return None
        with self._ocr_lock:
            status = self._ocr_status.get(page_index)
        if status is not None:
            return status
        if not self._is_scanned(page_index):
            return None
        payload = ocr_cache.load(self._ocr_key(page_index))
        with self._ocr_lock:
            if page_index not in self._ocr_status:
                if payload is None or not self._install_ocr(page_index, payload):
                    self._ocr_status[page_index] = ocr.NEEDED
            return self._ocr_status[page_index]

    @_reads
    def begin_ocr(self, page_index: int) -> ocr.OcrJob | None:
        """Mark a scanned page as being read and return the job that reads
        it, or None when there is nothing to start: a page with a text layer,
        one already being read, or one whose reading already finished."""
        if self.ocr_status(page_index) not in ocr.RESTARTABLE:
            return None
        with self._ocr_lock:
            if self._ocr_status.get(page_index) not in ocr.RESTARTABLE:
                return None
            self._ocr_status[page_index] = ocr.RUNNING
            generation = self._generation
            cacheable = self._password is None
        return ocr.OcrJob(
            generation,
            page_index,
            self._ocr_key(page_index),
            ocr.single_page_pdf(self._doc, page_index),
            cacheable=cacheable,
        )

    def finish_ocr(self, job: ocr.OcrJob, status: str, payload: dict | None) -> bool:
        """Take in a job's outcome. False when the job belonged to a document
        that is no longer open, which is then simply forgotten. A result is
        cached on disk whether or not it held legible text, so a page that
        could not be read is not read again either; never one of a
        password-protected PDF."""
        with self._ocr_lock:
            if job.generation != self._generation:
                return False
            installed = status == ocr.DONE and self._install_ocr(job.page_index, payload)
            if not installed:
                self._ocr_status[job.page_index] = ocr.ERROR if status == ocr.DONE else status
        # Not for a password-protected PDF (`cacheable` False): the cache is
        # plain text on disk, and its pages are only for someone who has the
        # password.
        if installed and job.cacheable:
            ocr_cache.store(job.key, payload)
        return True

    def _install_ocr(self, page_index: int, payload: dict) -> bool:
        """Adopt an OCR payload for a page; call with `_ocr_lock` held. False
        if the payload is malformed (a hand-edited cache file, say)."""
        try:
            chars, words = ocr.text_from_payload(payload)
        except (KeyError, ValueError, TypeError):
            return False
        if ocr.is_readable(words):
            self._ocr_text[page_index] = (chars, words)
            self._ocr_status[page_index] = ocr.DONE
        else:
            self._ocr_status[page_index] = ocr.NO_TEXT
        return True

    def _ocr_text_for(self, page_index: int):
        """The OCR boxes standing in for a scanned page's text layer, or None.
        Goes through `ocr_status` so that text cached in an earlier session
        is loaded by whichever call reaches the page first — reading
        `_ocr_text` directly missed it until something asked for the status."""
        if self.ocr_status(page_index) != ocr.DONE:
            return None
        return self._ocr_text.get(page_index)

    def _is_scanned(self, page_index: int) -> bool:
        if page_index not in self._scanned:
            self._scanned[page_index] = ocr.page_needs_ocr(self._doc[page_index])
        return self._scanned[page_index]

    def _ocr_key(self, page_index: int) -> str:
        if page_index not in self._ocr_keys:
            self._ocr_keys[page_index] = ocr.page_key(self._doc, self._doc[page_index])
        return self._ocr_keys[page_index]

    def _load_highlight(self, page_index: int, xref: int):
        """The page and its highlight annotation `xref`, or None if there is
        no such highlight there (a stale id from the frontend). The Page is
        returned too because the Annot is only valid while it is alive."""
        if not self._doc or not 0 <= page_index < len(self._doc):
            return None
        page = self._doc[page_index]
        for annot in highlighter.highlight_annots(page):
            if annot.xref == xref:
                return page, annot
        return None

    @_reads
    def set_highlight_color(self, page_index: int, xref: int, color: str) -> bool:
        """Recolour an existing highlight. False if there is no such highlight,
        the colour is not in the palette, or it already is that colour."""
        found = self._load_highlight(page_index, xref)
        if found is None or color not in highlighter.HIGHLIGHT_COLORS:
            return False
        page, annot = found
        before = highlighter.stroke_rgb(annot)
        if highlighter.color_name(before) == color:
            return False
        after = highlighter.HIGHLIGHT_COLORS[color]
        shape = highlighter.shape_of(annot, bounded=False)
        # No /C of its own: an undo gives it the default colour, the nearest
        # thing to "unset" (as an undone removal does).
        before = before or highlighter.HIGHLIGHT_COLORS[highlighter.DEFAULT_COLOR]
        highlighter.restyle_highlight(page, annot, after, shape, self._chars_for(page_index, shape))
        self._record(_Edit(page_index, self._ref(xref), (), _Look(before, shape), _Look(after, shape)))
        return True

    @_reads
    def set_highlight_appearance(
        self,
        page_index: int,
        xref: int,
        *,
        opacity: float | None = None,
        width: float | None = None,
        gesture: str | None = None,
    ) -> bool:
        """Change how an existing highlight is drawn — its opacity (0..1),
        and a stroke's width in points — in place and undoably. False if
        there is no such highlight, a value is out of range, a width is asked
        of a text markup, or nothing would change.

        Every change made under one `gesture` (a drag of the opacity slider,
        sending a value per step) is one undo step: the step on top of the
        undo stack is replaced by one from the gesture's first look to its
        latest, and dropped if the gesture ends where it began."""
        found = self._load_highlight(page_index, xref)
        if found is None:
            return False
        page, annot = found
        shape = highlighter.shape_of(annot, bounded=False)
        new = shape
        if opacity is not None:
            if not (isinstance(opacity, (int, float)) and 0 < opacity <= 1):
                return False
            new = replace(new, opacity=float(opacity))
        if width is not None:
            valid = isinstance(width, (int, float)) and 0 < width <= highlighter.INK_MAX_WIDTH_PT
            if not valid or not isinstance(new, highlighter.InkShape):
                return False
            new = replace(new, width=float(width))
        if _same_look(new, shape):
            return False
        rgb = highlighter.stroke_rgb(annot) or highlighter.HIGHLIGHT_COLORS[highlighter.DEFAULT_COLOR]
        highlighter.restyle_highlight(page, annot, rgb, new, self._chars_for(page_index, new))
        ref = self._ref(xref)
        # A page change on top is never part of a gesture.
        top = self._undo_stack[-1] if self._undo_stack and isinstance(self._undo_stack[-1], _Edit) else None
        if gesture is not None and top is not None and top.gesture == gesture and top.ref is ref:
            self._undo_stack.pop()
            self._redo_stack.clear()
            self._dirty = True
            if top.before is not None and _same_look(new, top.before.shape) and top.before.rgb == rgb:
                return True
            self._undo_stack.append(replace(top, after=_Look(rgb, new)))
            return True
        self._record(_Edit(page_index, ref, (), _Look(rgb, shape), _Look(rgb, new), gesture))
        return True

    @_reads
    def remove_highlight(self, page_index: int, xref: int) -> bool:
        """Delete an existing highlight, undoably. False if there is none."""
        found = self._load_highlight(page_index, xref)
        if found is None:
            return False
        page, annot = found
        # A highlight with no /C of its own (a viewer picks one) comes back
        # from an undo in the default colour, the nearest thing to "unset".
        before = highlighter.stroke_rgb(annot) or highlighter.HIGHLIGHT_COLORS[highlighter.DEFAULT_COLOR]
        look = _Look(before, highlighter.shape_of(annot, bounded=False))
        info = tuple((key, annot.info.get(key, "")) for key in _RESTORED_INFO_KEYS)
        page.delete_annot(annot)
        # Popped, not just looked up: MuPDF can hand a freed xref to the next
        # annotation, which is a different highlight and must get its own ref.
        ref = self._refs.pop(xref, None) or _HighlightRef(xref)
        self._record(_Edit(page_index, ref, info, look, None))
        return True

    def _ref(self, xref: int) -> _HighlightRef:
        return self._refs.setdefault(xref, _HighlightRef(xref))

    def _record(self, edit: _Edit) -> None:
        self._undo_stack.append(edit)
        self._redo_stack.clear()
        self._dirty = True

    def _move_highlight(self, edit: _Edit, frm: _Look | None, to: _Look | None) -> None:
        """Take `edit`'s highlight from look `frm` to look `to` (None:
        absent): re-adding it, deleting it, restyling it in place (colour,
        opacity, width), or — for a change of shape — writing it again in its
        new shape, under a new xref."""
        if frm is not None:
            # Present by construction: the stacks are the only thing that
            # changes highlights between an edit and its undo or redo.
            page, annot = self._load_highlight(edit.page_index, edit.ref.xref)
            if to is not None and highlighter.same_geometry(to.shape, frm.shape):
                highlighter.restyle_highlight(
                    page, annot, to.rgb, to.shape, self._chars_for(edit.page_index, to.shape)
                )
                return
            page.delete_annot(annot)
            self._refs.pop(edit.ref.xref, None)
            if to is None:
                return
        # A named local, not `self._doc[i]` inline: the Annot is unbound once
        # that Page wrapper is collected, and set_info below needs it.
        page = self._doc[edit.page_index]
        annot = highlighter.add_shape(page, to.shape, to.rgb, self._chars_for(edit.page_index, to.shape))
        if any(value for _, value in edit.info):
            annot.set_info(dict(edit.info))
        edit.ref.xref = annot.xref
        self._refs[annot.xref] = edit.ref

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_kind(self) -> str | None:
        """What Undo would take back (`page_changes.HIGHLIGHT` or
        `PAGE_CHANGES`), or None: the reader's "Undo page changes" label."""
        return self._kind_of(self._undo_stack)

    @property
    def redo_kind(self) -> str | None:
        return self._kind_of(self._redo_stack)

    @staticmethod
    def _kind_of(stack: list) -> str | None:
        if not stack:
            return None
        return page_changes.PAGE_CHANGES if isinstance(stack[-1], page_changes.PageChange) else page_changes.HIGHLIGHT

    def undo_highlight(self) -> int | None:
        """Takes back the most recent change — a highlight's add, recolour or
        removal, or one applied set of page changes. Returns the page index
        it was on (for page changes, the page the reader is now on), or None
        if there's nothing to undo."""
        return self.step_history(forward=False)[1]

    def redo_highlight(self) -> int | None:
        """Re-applies the most recently undone change. Returns the page
        index it was on, or None if there's nothing to redo."""
        return self.step_history(forward=True)[1]

    def step_history(self, *, forward: bool) -> tuple[str | None, int | None]:
        """Undo (or, `forward`, redo) the most recent change and say what it
        was: (`page_changes.HIGHLIGHT` or `PAGE_CHANGES`, the page index it
        was on; for page changes the page the reader is now on), or
        (None, None) when there is nothing to take back.

        Taken as a change of the document, under the gate and the save lock:
        reads already running end first, and the entry is popped, applied
        and pushed in one piece, so two steps (Ctrl+Z pressed twice) or an
        open arriving meanwhile can never interleave. On a failure the entry
        goes back where it was."""
        if forward:
            source, target = self._redo_stack, self._undo_stack
        else:
            source, target = self._undo_stack, self._redo_stack
        with self._gate.changing(), self._save_lock:
            if not source:
                return None, None
            edit = source.pop()
            if isinstance(edit, page_changes.PageChange):
                page = self._step_page_change(edit, source, target, forward=forward)
                return page_changes.PAGE_CHANGES, page
            try:
                if forward:
                    self._move_highlight(edit, edit.before, edit.after)
                else:
                    self._move_highlight(edit, edit.after, edit.before)
            except BaseException:
                source.append(edit)
                raise
            target.append(edit)
            self._dirty = True
            return page_changes.HIGHLIGHT, edit.page_index

    def highlight_count_on_current_page(self) -> int:
        if not self._doc:
            return 0
        return highlighter.highlight_count(self._doc[self._page_index])

    def highlight_count_total(self) -> int:
        return sum(self.highlight_counts().values())

    @_reads
    def highlight_counts(self) -> dict[int, int]:
        """How many highlights each page has, for the pages that have any —
        including ones not saved yet, since this reads the open document."""
        if not self._doc:
            return {}
        counts = {i: highlighter.highlight_count(self._doc[i]) for i in range(len(self._doc))}
        return {i: n for i, n in counts.items() if n}

    # ------------------------------------------------------------------ #
    # Page changes (Organise pages, Milestone F2)                          #
    # ------------------------------------------------------------------ #

    @_reads
    def page_change_block(self) -> str | None:
        """None when this PDF's pages may be changed; otherwise why not
        (`page_changes.NOT_LOADED`, `RESTRICTED`). MuPDF doesn't enforce a
        PDF's permissions, so the reader does: changing pages needs either
        the modify or the assemble permission. A PDF unlocked with its owner
        password has every permission."""
        if not self._doc:
            return page_changes.NOT_LOADED
        if not self._doc.permissions & (pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE):
            return page_changes.RESTRICTED
        return None

    def add_page_source(self, data: bytes, password: str | None = None) -> tuple[str, int]:
        """Hold a PDF Organise's Insert is putting in (its bytes; images
        arrive already made into one), unlocked with `password`, and return
        its source id and page count. Raises OpenError."""
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise OpenError(open_errors.NOT_A_PDF) from exc
        try:
            _check_opened(doc, password)
        except BaseException:
            doc.close()
            raise
        source = f"insert-{self._next_source}"
        self._next_source += 1
        self._page_sources[source] = doc
        return source, len(doc)

    def page_source_count(self, source: str) -> int | None:
        """How many pages a source Insert holds has; None if it holds none
        by that id."""
        doc = self._page_sources.get(source)
        return None if doc is None else len(doc)

    @_reads
    def page_source_bytes(self, source: str) -> bytes | None:
        """A file Insert holds, as PDF bytes for a file job (Extract): already
        unlocked, so the job needs no password for it; never written as they
        are. None if it holds none by that id."""
        doc = self._page_sources.get(source)
        return None if doc is None else doc.tobytes(deflate=True)

    def drop_page_source(self, source: str) -> None:
        """Let go of one PDF Insert opened (it was chosen, then not put in).
        As a change, so no thumbnail is still being drawn from it."""
        with self._gate.changing():
            doc = self._page_sources.pop(source, None)
        if doc is not None:
            doc.close()

    def drop_page_sources(self) -> None:
        """Let go of every PDF Insert opened (Organise has ended)."""
        sources, self._page_sources = self._page_sources, {}
        for doc in sources.values():
            doc.close()

    @_reads
    def render_plan_thumbnail(self, source: str, index: int, width_px: int):
        """A page of the plan, `width_px` wide, as it is now (Organise turns
        it on screen): the open document's own page, or one of a source
        Insert holds. None for a page that isn't there."""
        if source == page_plan.DOC:
            return self.render_thumbnail(index, width_px)
        doc = self._page_sources.get(source)
        if doc is None or not 0 <= index < len(doc):
            return None
        return self._thumbnail(doc[index], width_px)

    def _plan_pages(self, plan: page_plan.Plan) -> list[tuple[str | None, int, int]]:
        """`plan` as `engine.apply_page_plan` takes it; ValueError unless
        every page in it exists, and each of this document's at most once."""
        own = [page.index for page in plan if page.source == page_plan.DOC]
        if not plan or len(set(own)) != len(own) or not all(0 <= i < len(self._doc) for i in own):
            raise ValueError("not a plan of this document")
        for page in plan:
            source = self._page_sources.get(page.source)
            if page.source != page_plan.DOC and (source is None or not 0 <= page.index < len(source)):
                raise ValueError("not a plan of this document")
        return [(None if p.source == page_plan.DOC else p.source, p.index, p.turn) for p in plan]

    def apply_page_plan(self, plan: page_plan.Plan) -> bool:
        """Make the open document's pages `plan`, as one undoable edit
        ("Page changes"). False when it would change nothing. Raises
        ValueError for a plan that isn't this document's, or one that may
        not be applied (`page_change_block`); on any failure the document is
        as it was.

        Every page read already running ends first and new ones wait (the
        gate), and the plan is checked against the document inside the same
        lock that applies it, so nothing can change it in between."""
        with self._gate.changing(), self._save_lock:
            if self.page_change_block() is not None:
                raise ValueError("this PDF's pages can't be changed")
            if page_plan.is_unchanged(plan, len(self._doc)):
                return False
            pages = self._plan_pages(plan)
            order = tuple(page.index if page.source == page_plan.DOC else None for page in plan)
            held = self._capture_state()
            try:
                file_tool_engine.apply_page_plan(self._doc, pages, self._page_sources)
                # A highlight on a deleted page keeps its ref only in the held
                # state: left here, the check after an overwrite save would
                # see a ref with no highlight and clear the whole history.
                live = self._live_highlight_xrefs()
                refs = {xref: ref for xref, ref in self._refs.items() if xref in live}
            except BaseException:
                self._restore_state(held)
                raise
            # Nothing below can fail: the change is recorded with the pages.
            self._refs = refs
            self._record(page_changes.PageChange(held, order))
            self._pages_changed = True
            self._after_page_change(page_changes.page_after(order, self._page_index))
            self._trim_history()
        return True

    def _step_page_change(
        self, change: page_changes.PageChange, source: list, target: list, *, forward: bool
    ) -> int:
        """Undo (or redo) `change`, just popped off `source`, with the gate
        and the save lock held (`step_history`): swap the open document with
        the state it holds, then put it on `target`. Back on `source`, and
        the document as it was, if the swap fails."""
        locate = page_changes.page_after if forward else page_changes.page_before
        try:
            current = self._capture_state()
            index = locate(change.order, self._page_index)
            # The last step that can fail: until it has succeeded the open
            # document and `change.held` are as they were.
            self._restore_state(change.held)
            change.held = current
            self._after_page_change(index)
        except BaseException:
            source.append(change)
            raise
        target.append(change)
        self._dirty = True
        # The state now held may be bigger than the one it replaced.
        self._trim_history()
        return self._page_index

    def _refs_in_play(self) -> list[_HighlightRef]:
        """Every ref the stacks or the live map name, once each."""
        refs = {id(ref): ref for ref in self._refs.values()}
        for edit in self._undo_stack + self._redo_stack:
            if isinstance(edit, _Edit):
                refs[id(edit.ref)] = edit.ref
        return list(refs.values())

    def _capture_state(self) -> page_changes.DocState:
        """The open document as a `DocState`; call with `_save_lock` held."""
        data = self._doc.tobytes(deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        refs = self._refs_in_play()
        return page_changes.DocState(data, dict(self._refs), tuple((ref, ref.xref) for ref in refs))

    def _restore_state(self, state: page_changes.DocState) -> None:
        """Make `state` the open document; call with `_save_lock` held. The
        bytes are opened first, so if that fails nothing has changed."""
        doc = self._open_unlocked(stream=state.data, filetype="pdf")
        old, self._doc = self._doc, doc
        old.close()
        self._refs = dict(state.refs)
        for ref, xref in state.ref_xrefs:
            ref.xref = xref

    def _live_highlight_xrefs(self) -> set[int]:
        return {annot.xref for page in self._doc for annot in highlighter.highlight_annots(page)}

    def _after_page_change(self, page_index: int) -> None:
        """What every page change does besides the pages: the in-memory OCR
        state is per page index, so it is forgotten (and a read in flight
        dropped); the disk cache, keyed by page content, fills it again."""
        with self._ocr_lock:
            self._generation += 1
            self._scanned.clear()
            self._ocr_keys.clear()
            self._ocr_status.clear()
            self._ocr_text.clear()
        self._page_index = min(max(page_index, 0), len(self._doc) - 1)

    def _trim_history(self) -> None:
        undo, redo, trimmed = page_changes.trim(self._undo_stack, self._redo_stack)
        if trimmed:
            self._undo_stack, self._redo_stack = undo, redo
            self._history_trimmed = True

    def take_history_trimmed(self) -> bool:
        """Whether older changes were dropped since this was last asked (the
        reader says "Older changes can no longer be undone." once)."""
        trimmed, self._history_trimmed = self._history_trimmed, False
        return trimmed

    # ------------------------------------------------------------------ #
    # File tools (features/file_tools/)                                    #
    # ------------------------------------------------------------------ #

    @_reads
    def snapshot(self) -> tuple[bytes, str | None]:
        """The document as it is now, unsaved highlights included, as PDF
        bytes for a file tool to work on, and the password they need (held
        in memory only). The open document, its unsaved state and its undo
        history are left exactly as they were: no garbage collection (it
        renumbers the xrefs the undo stack names), and the encryption kept."""
        with self._save_lock:
            data = self._doc.tobytes(deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            return data, self._password

    @_reads
    def describe_for_tools(self, page_index: int) -> dict:
        """What a tool dialog shows about the open document (see
        `file_tools.engine.describe_document`), using the per-page "has a
        text layer" answers already cached here, so it never runs OCR."""
        return file_tool_engine.describe_document(self._doc, page_index, self._is_scanned)

    @_reads
    def unsaved_highlight_count(self) -> int:
        """How many more highlights the open document has than its file on
        disk: what a tool's "Includes your N unsaved highlights" counts.
        0 when nothing is unsaved, or when the change was a recolour or a
        removal rather than a new highlight."""
        if not self._doc or not self._dirty:
            return 0
        try:
            saved = self._open_unlocked(self._path)
        except Exception:
            return 0
        try:
            on_disk = sum(highlighter.highlight_count(page) for page in saved)
        finally:
            saved.close()
        return max(0, self.highlight_count_total() - on_disk)

    @_reads
    def textless_pages(self) -> list[int]:
        """The pages with no text layer that show an image, worked out once
        per page and cached for as long as the document is open."""
        if not self._doc:
            return []
        return [i for i in range(len(self._doc)) if self._is_scanned(i)]

    @_reads
    def ocr_words(self, page_index: int) -> list | None:
        """The words OCR read on a scanned page (in memory, or cached from an
        earlier session) in `ocr.recognize`'s form, or None if it hasn't been
        read. Never starts a read."""
        if not self._doc or not 0 <= page_index < len(self._doc):
            return None
        text = self._ocr_text_for(page_index)
        if text is None:
            return None
        return [
            [w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1, w.text, w.block_no, w.line_no, w.word_no]
            for w in text[1]
        ]

    def is_open_document(self, path: str) -> bool:
        """Whether `path` is the PDF that is open, however it is spelled."""
        return bool(self._path) and safe_write.same_file(path, self._path)

    def save_overwrite(self):
        """Write the highlights into the open PDF itself.

        Not `saveIncr()`, which appends to the file in place (and refuses a
        file MuPDF had to repair on open): the whole document is written
        beside it, checked, and moved over it (`safe_write`), so the file is
        never half-written. A signed PDF is refused, since rewriting it would
        break its signature. Raises SaveError; on any failure the file on
        disk is as it was and the highlights stay open, unsaved.
        """
        with self._gate.changing(), self._save_lock:
            if self._doc.get_sigflags() > 0:
                raise SaveError(safe_write.SIGNED)
            safe_write.check_writable(self._path)
            safe_write.write_atomically(
                self._path,
                # KEEP, as saveIncr() did: a permissions-only encryption on
                # the original stays on it.
                lambda tmp: self._write_full(tmp, encryption=pymupdf.PDF_ENCRYPT_KEEP),
                self._verify_saved,
                swap=self._swap_into_place,
            )
            self._dirty = False

    def save_copy(self, new_path: str):
        """Write the document, highlights included, to `new_path`, which may
        already exist (the reader has confirmed replacing it). Never the open
        PDF itself under another spelling: on a Mac that could unlink it
        before writing. Raises SaveError."""
        with self._gate.changing(), self._save_lock:
            if self.is_open_document(new_path):
                raise SaveError(safe_write.IS_OPEN_DOCUMENT)
            # KEEP, as overwrite: MuPDF's default writes a copy without its
            # encryption, which would make a copy of a password-protected PDF
            # an unprotected one, and drop a restricted one's restrictions.
            safe_write.write_atomically(
                new_path,
                lambda tmp: self._write_full(tmp, encryption=pymupdf.PDF_ENCRYPT_KEEP),
                self._verify_saved,
            )
            self._dirty = False

    def _write_full(self, path: str, **options) -> None:
        # No compacting garbage collection (2 and up): it renumbers objects
        # in the *live* pymupdf.Document, and the undo/redo stacks name
        # highlights by xref, which a save without it keeps (so do the
        # reopens around an overwrite). Once pages have been changed, level 1
        # drops the objects nothing uses any more — a deleted page's content
        # would otherwise stay in the file — without renumbering anything
        # (tests/test_page_changes.py).
        garbage = 1 if self._pages_changed else 0
        self._doc.save(path, deflate=True, garbage=garbage, **options)

    def _verify_saved(self, path: str) -> None:
        """Raise SaveError unless the file at `path` opens, with the open
        document's password if it has one and with none otherwise, and has
        the open document's pages and highlights."""
        try:
            saved = pymupdf.open(path)
        except Exception as exc:
            raise SaveError(safe_write.VERIFY_FAILED) from exc
        try:
            try:
                needed = _unlock(saved, self._password)
            except OpenError:
                needed = None
            counts = (
                {i: highlighter.highlight_count(saved[i]) for i in range(len(saved))}
                if needed is not None
                else {}
            )
            matches = (
                needed == (self._password is not None)
                and len(saved) == len(self._doc)
                and {i: n for i, n in counts.items() if n} == self.highlight_counts()
            )
        finally:
            saved.close()
        if not matches:
            raise SaveError(safe_write.VERIFY_FAILED)

    def _swap_into_place(self, tmp: str, dest: str) -> None:
        """Move the checked file over the open original. The original has to
        be let go of first (Windows will not replace a file that is open), so
        the document is closed and reopened around the move; page, zoom, OCR
        state and the undo/redo stacks live on this object and carry over."""
        self._doc.close()
        try:
            safe_write.replace(tmp, dest)
        except BaseException as exc:
            # The original is untouched. Reopen what was just written, from
            # memory (the temporary file is about to be deleted), so the
            # reader carries on with every highlight still there to save.
            kept = self._reopen_from_memory(tmp, dest)
            if kept or not isinstance(exc, Exception):
                raise
            reason = safe_write.HIGHLIGHTS_LOST if self._doc is not None else safe_write.UNSAVED_NOT_REOPENED
            raise SaveError(reason) from exc
        # From here on the save has happened: nothing below may report the
        # file as unchanged, or leave the closed document behind.
        self._dirty = False
        try:
            self._doc = self._reopen_saved(dest)
        except Exception as exc:
            log.exception("Saved the PDF but could not reopen it")
            self._doc = None
            self._password = None
            raise SaveError(safe_write.SAVED_NOT_REOPENED) from exc
        try:
            self._forget_history_if_stale()
        except Exception:
            log.exception("Could not check undo/redo against the saved PDF")
            self._clear_history()

    def _open_unlocked(self, *args, **kwargs) -> "pymupdf.Document":
        """`pymupdf.open(*args, **kwargs)` (a file this object wrote, or the
        original again), unlocked with the open document's password. Raises
        OpenError when it won't unlock, which callers treat as any other
        failure to reopen."""
        doc = pymupdf.open(*args, **kwargs)
        try:
            _unlock(doc, self._password)
        except OpenError:
            doc.close()
            raise
        return doc

    def _reopen_saved(self, dest: str) -> "pymupdf.Document":
        """The file just moved into place, opened again — retried while a
        scanner may be holding it, then from its bytes."""
        try:
            return safe_write.retry_briefly(lambda: self._open_unlocked(dest))
        except Exception:
            with open(dest, "rb") as f:
                return self._open_unlocked(stream=f.read(), filetype="pdf")

    def _reopen_from_memory(self, tmp: str, original: str) -> bool:
        """Whether the highlights were kept: False when only the original as
        it was on disk could be reopened, or (with `_doc` None) not even
        that — never the closed document."""
        try:
            with open(tmp, "rb") as f:
                self._doc = self._open_unlocked(stream=f.read(), filetype="pdf")
            return True
        except Exception:
            # Unreadable even now: fall back on the original as it was on
            # disk, without this session's highlights.
            self._clear_history()
            self._dirty = False
        try:
            self._doc = self._open_unlocked(original)
        except Exception:
            log.exception("Could not reopen the PDF after a failed save")
            self._doc = None
            self._password = None
        return False

    def _clear_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._refs.clear()

    def _forget_history_if_stale(self) -> None:
        """Clear undo/redo if any highlight they name is no longer where its
        xref says. A save without garbage collection keeps xrefs, so this is
        a guard: an undo that found the wrong annotation would be worse than
        none."""
        live = {
            annot.xref
            for page in self._doc
            for annot in highlighter.highlight_annots(page)
        }
        if not set(self._refs) <= live:
            self._clear_history()
