import fitz  # PyMuPDF

from lector.features.annotations import highlighter


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
        # Undo entries keep the live Annot handle *and* the Page object it
        # came from — fitz.Document.__getitem__ hands back a fresh Page
        # wrapper on every call, and an Annot handle is silently invalidated
        # once the Page wrapper it was created from is garbage collected, so
        # the Page must be kept alive alongside its Annot. Redo entries only
        # keep the word list, since PyMuPDF invalidates an Annot handle the
        # moment it's deleted — redoing re-adds a fresh annotation instead.
        self._undo_stack: list[tuple[int, list[highlighter.Word], object, object]] = []
        self._redo_stack: list[tuple[int, list[highlighter.Word]]] = []

    def open(self, path: str):
        if self._doc:
            self._doc.close()
        self._doc = fitz.open(path)
        self._path = path
        self._page_index = 0
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()

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

    # ------------------------------------------------------------------ #
    # Annotations (Milestone 3)                                           #
    # ------------------------------------------------------------------ #

    def words_on_current_page(self) -> list[highlighter.Word]:
        return self.words_on_page(self._page_index)

    def words_on_page(self, page_index: int) -> list[highlighter.Word]:
        if not self._doc:
            return []
        return highlighter.words_on_page(self._doc[page_index])

    def highlight_word_range(
        self, page_index: int, words: list[highlighter.Word], start_idx: int, end_idx: int
    ):
        selected = highlighter.words_between(words, start_idx, end_idx)
        page = self._doc[page_index]
        annot = highlighter.add_highlight(page, selected)
        if annot is not None:
            self._dirty = True
            self._undo_stack.append((page_index, selected, annot, page))
            self._redo_stack.clear()
        return annot

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo_highlight(self) -> int | None:
        """Removes the most recently added highlight. Returns the page index
        it was on, or None if there's nothing to undo."""
        if not self._undo_stack:
            return None
        page_index, words, annot, page = self._undo_stack.pop()
        page.delete_annot(annot)
        self._redo_stack.append((page_index, words))
        self._dirty = True
        return page_index

    def redo_highlight(self) -> int | None:
        """Re-applies the most recently undone highlight. Returns the page
        index it was applied to, or None if there's nothing to redo."""
        if not self._redo_stack:
            return None
        page_index, words = self._redo_stack.pop()
        page = self._doc[page_index]
        annot = highlighter.add_highlight(page, words)
        self._undo_stack.append((page_index, words, annot, page))
        self._dirty = True
        return page_index

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
