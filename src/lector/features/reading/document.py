import fitz  # PyMuPDF


class PdfDocument:
    DEFAULT_ZOOM = 1.5
    ZOOM_STEP = 0.25
    MIN_ZOOM = 0.5
    MAX_ZOOM = 4.0

    def __init__(self):
        self._doc = None
        self._page_index = 0
        self._zoom = self.DEFAULT_ZOOM

    def open(self, path: str):
        if self._doc:
            self._doc.close()
        self._doc = fitz.open(path)
        self._page_index = 0

    def close(self):
        if self._doc:
            self._doc.close()
            self._doc = None

    @property
    def is_open(self) -> bool:
        return self._doc is not None

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

    def render_current_page(self):
        if not self._doc:
            return None
        page = self._doc[self._page_index]
        mat = fitz.Matrix(self._zoom, self._zoom)
        return page.get_pixmap(matrix=mat, alpha=False)
