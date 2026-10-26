import logging
import math
import os
import threading
from dataclasses import dataclass

import pymupdf

from lector.features.annotations import highlighter
from lector.features.reading import ocr, ocr_cache, open_errors, safe_write, search
from lector.features.reading.open_errors import OpenError
from lector.features.reading.outline import OutlineEntry, outline_tree
from lector.features.reading.safe_write import SaveError

log = logging.getLogger(__name__)

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
class _Edit:
    """One reversible change to one highlight: its colour going from `before`
    to `after`, where None means "no highlight". An add is None -> colour, a
    removal colour -> None, a recolour colour -> colour, so undo and redo are
    the same operation run in opposite directions.

    Colours are raw RGB rather than palette names because a highlight another
    app wrote can be recoloured or removed too, and undoing that has to put
    back its own colour. `rects` and `info` are what a removal needs to put
    the highlight back: its per-line boxes, and the note/author another app
    may have attached.
    """

    page_index: int
    ref: _HighlightRef
    rects: tuple[pymupdf.Rect, ...]
    info: tuple[tuple[str, str], ...]
    before: highlighter.RGB | None
    after: highlighter.RGB | None


# What `Annot.set_info` accepts back from `Annot.info`; the rest (id, dates)
# is regenerated for a re-added annotation.
_RESTORED_INFO_KEYS = ("content", "title", "subject")


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

    def open(self, path: str, password: str | None = None):
        """Open `path`, unlocking it with `password` if it has one. Raises
        OpenError, and then the document that was open is still open, as it
        was: the new file is checked in full before the old one is let go."""
        doc, needed_password = _open_checked(path, password)
        # Never in the middle of a save, which closes and reopens the PDF
        # around moving the new file into place.
        with self._save_lock:
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

    def close(self):
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

    def page_css_size(self, index: int) -> tuple[int, int]:
        """Page `index`'s size on screen in CSS pixels at the current zoom."""
        rect = self._doc[index].rect
        scale = self.css_scale()
        return (self._css_px(rect.width * scale), self._css_px(rect.height * scale))

    def render_page(self, index: int, device_scale: float = 1.0):
        """Page `index` as a bitmap for a screen with `device_scale` device
        pixels per CSS pixel (the web view's devicePixelRatio), so it is
        drawn 1:1 with the screen's pixels at its CSS size
        (`page_css_size`), down to MAX_RENDER_PIXELS."""
        if not self._doc:
            return None
        page = self._doc[index]
        scale = self.css_scale() * min(max(float(device_scale), 1.0), 4.0)
        area = page.rect.width * page.rect.height * scale * scale
        if area > self.MAX_RENDER_PIXELS:
            scale *= (self.MAX_RENDER_PIXELS / area) ** 0.5
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    def render_thumbnail(self, index: int, width_px: int):
        """Page `index` scaled to `width_px` pixels wide, whatever the zoom —
        for the outline panel's Pages tab and the progress bar's hover
        preview. None for a page that isn't there."""
        if not self._doc or not 0 <= index < len(self._doc):
            return None
        page = self._doc[index]
        scale = max(1, width_px) / page.rect.width if page.rect.width else 1
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    def outline(self) -> list[OutlineEntry]:
        """The PDF's bookmarks as a nested tree (see outline.py); [] when it
        has none."""
        if not self._doc:
            return []
        return outline_tree(self._doc.get_toc(simple=True), len(self._doc))

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

    def chars_on_page(self, page_index: int) -> list[highlighter.Char]:
        if not self._doc:
            return []
        ocr_text = self._ocr_text_for(page_index)
        if ocr_text is not None:
            return list(ocr_text[0])
        return highlighter.chars_on_page(self._doc[page_index])

    def words_on_current_page(self) -> list[highlighter.Word]:
        return self.words_on_page(self._page_index)

    def words_on_page(self, page_index: int) -> list[highlighter.Word]:
        if not self._doc:
            return []
        ocr_text = self._ocr_text_for(page_index)
        if ocr_text is not None:
            return list(ocr_text[1])
        return highlighter.words_on_page(self._doc[page_index])

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

    def highlight_char_indices(
        self, page_index: int, start_idx: int, end_idx: int, color: str = highlighter.DEFAULT_COLOR
    ):
        """Highlight the reading-order run between two character indices.

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
        return self._commit_highlight(page_index, selected, color)

    def highlight_word_indices(
        self, page_index: int, start_idx: int, end_idx: int, color: str = highlighter.DEFAULT_COLOR
    ):
        """Highlight the reading-order run between two word indices.

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
        return self._commit_highlight(page_index, selected, color)

    def _commit_highlight(
        self, page_index: int, selected: list[highlighter.LineBounded], color: str
    ) -> int | None:
        """Write the highlight and record it; returns its xref, or None when
        there was nothing to highlight."""
        if color not in highlighter.HIGHLIGHT_COLORS:
            color = highlighter.DEFAULT_COLOR
        rgb = highlighter.HIGHLIGHT_COLORS[color]
        page = self._doc[page_index]  # held while `annot` is used (see _move_highlight)
        annot = highlighter.add_highlight(page, selected, color)
        if annot is None:
            return None
        rects = tuple(highlighter.merge_line_rects(selected))
        self._record(_Edit(page_index, self._ref(annot.xref), rects, (), None, rgb))
        return annot.xref

    def highlights_on_page(self, page_index: int) -> list[highlighter.Highlight]:
        if not self._doc or not 0 <= page_index < len(self._doc):
            return []
        return highlighter.highlights_on_page(self._doc[page_index])

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
        for annot in page.annots(types=[pymupdf.PDF_ANNOT_HIGHLIGHT]) or []:
            if annot.xref == xref:
                return page, annot
        return None

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
        rects = tuple(highlighter.highlight_rects(annot))
        highlighter.recolor_highlight(page, annot, after)
        self._record(_Edit(page_index, self._ref(xref), rects, (), before, after))
        return True

    def remove_highlight(self, page_index: int, xref: int) -> bool:
        """Delete an existing highlight, undoably. False if there is none."""
        found = self._load_highlight(page_index, xref)
        if found is None:
            return False
        page, annot = found
        # A highlight with no /C of its own (a viewer picks one) comes back
        # from an undo in the default colour, the nearest thing to "unset".
        before = highlighter.stroke_rgb(annot) or highlighter.HIGHLIGHT_COLORS[highlighter.DEFAULT_COLOR]
        rects = tuple(highlighter.highlight_rects(annot))
        info = tuple((key, annot.info.get(key, "")) for key in _RESTORED_INFO_KEYS)
        page.delete_annot(annot)
        # Popped, not just looked up: MuPDF can hand a freed xref to the next
        # annotation, which is a different highlight and must get its own ref.
        ref = self._refs.pop(xref, None) or _HighlightRef(xref)
        self._record(_Edit(page_index, ref, rects, info, before, None))
        return True

    def _ref(self, xref: int) -> _HighlightRef:
        return self._refs.setdefault(xref, _HighlightRef(xref))

    def _record(self, edit: _Edit) -> None:
        self._undo_stack.append(edit)
        self._redo_stack.clear()
        self._dirty = True

    def _move_highlight(self, edit: _Edit, frm: highlighter.RGB | None, to: highlighter.RGB | None) -> None:
        """Take `edit`'s highlight from colour `frm` to colour `to` (None:
        absent), re-adding or deleting the annotation when either is None."""
        if frm is None:
            # A named local, not `self._doc[i]` inline: the Annot is unbound
            # once that Page wrapper is collected, and set_info below needs it.
            page = self._doc[edit.page_index]
            annot = highlighter.add_highlight_rects(page, edit.rects, to)
            if any(value for _, value in edit.info):
                annot.set_info(dict(edit.info))
            edit.ref.xref = annot.xref
            self._refs[annot.xref] = edit.ref
            return
        # Present by construction: the stacks are the only thing that changes
        # highlights between an edit and its undo or redo.
        page, annot = self._load_highlight(edit.page_index, edit.ref.xref)
        if to is None:
            page.delete_annot(annot)
            self._refs.pop(edit.ref.xref, None)
        else:
            highlighter.recolor_highlight(page, annot, to)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo_highlight(self) -> int | None:
        """Takes back the most recent highlight change — an add, a recolour
        or a removal. Returns the page index it was on, or None if there's
        nothing to undo."""
        if not self._undo_stack:
            return None
        edit = self._undo_stack.pop()
        self._move_highlight(edit, edit.after, edit.before)
        self._redo_stack.append(edit)
        self._dirty = True
        return edit.page_index

    def redo_highlight(self) -> int | None:
        """Re-applies the most recently undone highlight change. Returns the
        page index it was on, or None if there's nothing to redo."""
        if not self._redo_stack:
            return None
        edit = self._redo_stack.pop()
        self._move_highlight(edit, edit.before, edit.after)
        self._undo_stack.append(edit)
        self._dirty = True
        return edit.page_index

    def highlight_count_on_current_page(self) -> int:
        if not self._doc:
            return 0
        return highlighter.highlight_count(self._doc[self._page_index])

    def highlight_count_total(self) -> int:
        return sum(self.highlight_counts().values())

    def highlight_counts(self) -> dict[int, int]:
        """How many highlights each page has, for the pages that have any —
        including ones not saved yet, since this reads the open document."""
        if not self._doc:
            return {}
        counts = {i: highlighter.highlight_count(self._doc[i]) for i in range(len(self._doc))}
        return {i: n for i, n in counts.items() if n}

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
        with self._save_lock:
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
        with self._save_lock:
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
        # No garbage collection: it renumbers objects in the *live*
        # pymupdf.Document, and the undo/redo stacks name highlights by xref,
        # which a save without it keeps (so do the reopens around an
        # overwrite).
        self._doc.save(path, deflate=True, **options)

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
            for annot in page.annots(types=[pymupdf.PDF_ANNOT_HIGHLIGHT]) or []
        }
        if not set(self._refs) <= live:
            self._clear_history()
