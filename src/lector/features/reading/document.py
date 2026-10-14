import threading
from dataclasses import dataclass

import fitz  # PyMuPDF

from lector.features.annotations import highlighter
from lector.features.reading import ocr, ocr_cache, search


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
    rects: tuple[fitz.Rect, ...]
    info: tuple[tuple[str, str], ...]
    before: highlighter.RGB | None
    after: highlighter.RGB | None


# What `Annot.set_info` accepts back from `Annot.info`; the rest (id, dates)
# is regenerated for a re-added annotation.
_RESTORED_INFO_KEYS = ("content", "title", "subject")


class PdfDocument:
    DEFAULT_ZOOM = 1.0
    ZOOM_STEP = 0.25
    MIN_ZOOM = 0.5
    MAX_ZOOM = 4.0

    def __init__(self):
        self._doc = None
        self._path = None
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

    def open(self, path: str):
        if self._doc:
            self._doc.close()
        self._doc = fitz.open(path)
        self._path = path
        self._page_index = 0
        # Per-document state: a PDF with no saved position must start at the
        # default zoom, not at whatever the previously open one was left on.
        # `Api._restore_position` overrides this when a record exists.
        self._zoom = self.DEFAULT_ZOOM
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._refs.clear()
        with self._ocr_lock:
            self._generation += 1
            self._scanned.clear()
            self._ocr_keys.clear()
            self._ocr_status.clear()
            self._ocr_text.clear()

    def close(self):
        if self._doc:
            self._doc.close()
            self._doc = None

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
        self._zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, round(value, 2)))

    def render_current_page(self):
        return self.render_page(self._page_index)

    def render_page(self, index: int):
        if not self._doc:
            return None
        page = self._doc[index]
        mat = fitz.Matrix(self._zoom, self._zoom)
        return page.get_pixmap(matrix=mat, alpha=False)

    def page_sizes(self) -> list[tuple[int, int]]:
        """Page dimensions at the current zoom, without rendering pixmaps.

        Lets the strip layout lay out correctly-sized placeholders for every
        page up front (cheap: only reads each page's bounding box) before
        lazily rendering actual page images as they scroll into view.
        """
        if not self._doc:
            return []
        return [
            (round(page.rect.width * self._zoom), round(page.rect.height * self._zoom))
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
                if fitz.Rect(rect).contains((w.rect.tl + w.rect.br) / 2)
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
        return ocr.OcrJob(
            generation,
            page_index,
            self._ocr_key(page_index),
            ocr.single_page_pdf(self._doc, page_index),
        )

    def finish_ocr(self, job: ocr.OcrJob, status: str, payload: dict | None) -> bool:
        """Take in a job's outcome. False when the job belonged to a document
        that is no longer open, which is then simply forgotten. A result is
        cached on disk whether or not it held legible text, so a page that
        could not be read is not read again either."""
        with self._ocr_lock:
            if job.generation != self._generation:
                return False
            installed = status == ocr.DONE and self._install_ocr(job.page_index, payload)
            if not installed:
                self._ocr_status[job.page_index] = ocr.ERROR if status == ocr.DONE else status
        if installed:
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
        for annot in page.annots(types=[fitz.PDF_ANNOT_HIGHLIGHT]) or []:
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
        if not self._doc:
            return 0
        return sum(highlighter.highlight_count(self._doc[i]) for i in range(len(self._doc)))

    def save_overwrite(self):
        self._doc.saveIncr()
        self._dirty = False

    def save_copy(self, new_path: str):
        # No garbage collection here: it renumbers objects in the *live*
        # fitz.Document, which corrupts a later saveIncr() back to the
        # original file if the user picks "overwrite" in a subsequent save
        # of the same session (verified: garbage=4 breaks the xref chain).
        self._doc.save(new_path, deflate=True)
        self._dirty = False
