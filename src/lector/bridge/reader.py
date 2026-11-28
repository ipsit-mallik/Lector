"""The reader: opening a PDF, its pages, outline, position and zoom,
scanned pages' OCR and find in document (docs/ARCHITECTURE.md § "Bridge
layout"). Every page turn and zoom answers with the reader's state."""
import os

from lector.bridge import checks
from lector.bridge.core import Controller
from lector.features.reading import ocr, open_errors
from lector.features.reading import outline as reading_outline
from lector.features.settings import store as settings

# Longer than any phrase a reader would look for; a pasted paragraph is cut
# here rather than handed to MuPDF to scan every page with.
MAX_FIND_QUERY_CHARS = 200


class ReaderController(Controller):
    def open_pdf(self, path: str, password: str | None = None) -> dict:
        refused = self._open(path, password, for_organise=False)
        return refused if refused is not None else self._state()

    def open_pdf_to_organise(self, path: str, password: str | None = None) -> dict:
        """Open `path` only to organise its pages (Home's Tools page, a
        Recent or Favorites menu): as `open_pdf`, but Recent and the saved
        reading position are left alone until Organise's Done makes it an
        ordinary open (`BridgeCore.adopt_organise_open`); its Cancel closes
        it again (`organise_cancel`)."""
        refused = self._open(path, password, for_organise=True)
        return refused if refused is not None else self._state()

    def _open(self, path: str, password: str | None, *, for_organise: bool) -> dict | None:
        """Open `path` (unlocked with `password`, for a protected PDF); None
        once it is the open document. A PDF that can't be opened comes back as
        `{"error": sentence, "reason": code}` (`open_errors`), with nothing
        else changed: the open document stays open and Recent is untouched.
        `reason` "password" asks the page for the password; the password is
        held only by the open document and never logged or stored."""
        if password is not None and not isinstance(password, str):
            # The bridge takes any JSON value; only the page's own field is
            # expected here, so anything else is a mistake, not a password.
            return {"error": open_errors.MESSAGES[open_errors.UNKNOWN], "reason": open_errors.UNKNOWN}
        try:
            self._doc.open(path, password)
        except open_errors.OpenError as exc:
            return {"error": exc.message, "reason": exc.reason}
        self._organise = None  # a draft of the PDF that was open
        # A viewport names pages of the document it was measured on; kept
        # across an open, the next vocabulary rebuild would ask the new one
        # for pages it may not have.
        self._vocabulary.viewport = []
        self._vocabulary.ocr_pages = []
        self._core.title = os.path.splitext(os.path.basename(path))[0]
        self._core.path = path
        # A PDF never opened before (or opened with "Always start at the
        # beginning") comes up in the reader's default view (Settings >
        # Reading); one with a saved position gets its own back below.
        self._core.layout_mode = settings.get_default_view()
        self._core.persisted_position = None
        # One whose pages can't be changed goes to the reader instead (which
        # says why), so it is an ordinary open from the start.
        self._core.opened_for_organise = for_organise and self._core.organise_block_message() is None
        if not self._core.opened_for_organise:
            settings.add_recent_file(path)
        if settings.get_reopen_behavior() == settings.CONTINUE:
            self._restore_position(path)
        self._remember_position()
        return None

    def _restore_position(self, path: str) -> None:
        """Reopen `path` where the reader left it, if that is still valid.

        The stored page is clamped rather than trusted: the file may have been
        edited (by Lector or anything else) since it was last read, and a page
        index past the end of a now-shorter document would otherwise be
        rejected outright and silently drop the reader back on page 1.
        """
        position = settings.get_document_position(path)
        if position is None:
            return
        self._core.layout_mode = position["layout_mode"]
        page_index = min(position["page_index"], self._doc.page_count - 1)
        if page_index > 0:
            self._doc.go_to_page(page_index)
        self._doc.set_zoom(position["zoom"])

    def set_layout_mode(self, mode: str) -> dict:
        """Told by the frontend whenever the book/strip toggle is used."""
        if mode in (settings.BOOK, settings.STRIP):
            self._core.layout_mode = mode
            self._remember_position()
        return self._state()

    def get_state(self) -> dict:
        return self._state()

    def get_page_image(self, index: int, device_scale: float = 1.0) -> dict:
        """Page `index` at the current zoom. `width` and `height` are the
        size to show it at in CSS pixels (100% is the paper's real size); the
        bitmap itself is drawn for `device_scale` (the web view's
        devicePixelRatio, clamped to 1-4) so it is sharp on a high DPI screen."""
        rendered = self._doc.render_page_sized(index, device_scale)
        if rendered is None:
            return {"image": None, "width": 0, "height": 0}
        pix, width, height = rendered
        return {"image": checks.pixmap_to_png_b64(pix), "width": width, "height": height}

    def get_page_sizes(self) -> list[dict]:
        return [{"width": w, "height": h} for w, h in self._doc.page_sizes()]

    def get_page_size_points(self, index: int) -> dict:
        """Page `index`'s size in PDF points, whatever the zoom: what Fit width
        and Fit page divide the page area by (`frontend/pages/zoom-fit.js`).
        The rendered sizes above are rounded to whole pixels at the current
        zoom, too coarse to work back from at 25%. A page past either end of
        the document is the nearest real one, as `goto_page` treats it."""
        if not self._doc.is_open:
            return {"width": 0.0, "height": 0.0}
        index = min(max(int(index), 0), self._doc.page_count - 1)
        width, height = self._doc.page_size_points(index)
        return {"width": width, "height": height}

    def get_page_thumbnail(self, index: int, width: int) -> dict:
        """Page `index` rendered `width` pixels wide (the outline panel's
        Pages tab, the progress bar's hover preview). The width is clamped:
        this is a bridge boundary, and a thumbnail is never page-sized."""
        width = max(checks.MIN_THUMBNAIL_PX, min(int(width), checks.MAX_THUMBNAIL_PX))
        pix = self._doc.render_thumbnail(int(index), width)
        if pix is None:
            return {"image": None, "width": 0, "height": 0}
        return {"image": checks.pixmap_to_png_b64(pix), "width": pix.width, "height": pix.height}

    def get_outline(self) -> dict:
        """The open PDF's bookmarks, for the outline panel, and the progress
        bar's chapter segments built from its top level (both empty for a PDF
        without an outline — see `features/reading/outline.py`)."""
        if not self._doc.is_open:
            return {"entries": [], "chapters": []}
        entries = self._doc.outline()
        return {
            "entries": entries,
            "chapters": reading_outline.chapter_segments(entries, self._doc.page_count),
        }

    def next_page(self) -> dict:
        self._doc.next_page()
        self._remember_position()
        return self._state()

    def prev_page(self) -> dict:
        self._doc.prev_page()
        self._remember_position()
        return self._state()

    def goto_page(self, index: int) -> dict:
        self._doc.go_to_page(index)
        self._remember_position()
        return self._state()

    def zoom_in(self) -> dict:
        self._doc.zoom_in()
        self._remember_position()
        return self._state()

    def zoom_out(self) -> dict:
        self._doc.zoom_out()
        self._remember_position()
        return self._state()

    def set_zoom(self, value: float) -> dict:
        self._doc.set_zoom(value)
        self._remember_position()
        return self._state()

    def get_page_chars(self, page_index: int) -> dict:
        """Character boxes for one page, in PDF points, in reading order.

        The frontend needs these locally to paint the live selection as the
        reader drags: hit-testing the cursor against the character list has
        to happen on every mousemove, which is far too often to go back
        across the bridge for. Positions are in points (not rendered pixels)
        so the same payload stays correct at any zoom, and `line` groups
        characters that share a line of text so a selection can be drawn as
        one continuous bar per line rather than one box per character.

        Character *indices* into this list are the currency of the highlight
        call below — the frontend sends back the range it had drawn, so what
        gets written is what the reader saw selected. Character granularity
        (rather than word) is what lets a drag stop mid-word, the way Adobe
        Reader and other desktop PDF viewers select text.

        `ocr` is None for a page with a text layer. For a scanned page it is
        one of `ocr`'s statuses, and `chars` is empty until it is "done" —
        `ocr_page` is how the frontend asks for it to be read.
        """
        if not self._doc.is_open:
            return {"width": 0, "height": 0, "chars": [], "ocr": None}
        width, height = self._doc.page_size_points(page_index)
        # First, because for a scanned page read in an earlier session this
        # is what loads its cached text — and with it the chars below.
        ocr_status = self._doc.ocr_status(page_index)
        chars = self._doc.chars_on_page(page_index)
        shown = self._doc.as_shown(page_index, [c.rect for c in chars])
        return {
            "width": width,
            "height": height,
            "ocr": ocr_status,
            "chars": [
                {
                    "x0": rect.x0,
                    "y0": rect.y0,
                    "x1": rect.x1,
                    "y1": rect.y1,
                    "line": f"{c.block_no}:{c.line_no}",
                }
                for c, rect in zip(chars, shown)
            ],
        }

    def ocr_page(self, page_index: int) -> dict:
        """Start reading a scanned page's text, if it needs it.

        Returns at once with `{"page_index", "status"}`: "running" when a
        read was started or is already under way, otherwise where the page
        already stands ("done", "no_text", or None for a page with its own
        text). A read that was started ends with a `lector:ocr` page event
        carrying the same two keys. Never called on file open — the frontend
        asks when a highlight is attempted on a scanned page, or, with voice
        on, once one has stayed on screen.
        """
        page_index = int(page_index)
        job = self._doc.begin_ocr(page_index)
        if job is not None:
            self._ocr.submit(job)
        return {"page_index": page_index, "status": self._doc.ocr_status(page_index)}

    def on_ocr_finished(self, job: ocr.OcrJob, status: str, payload: dict | None) -> None:
        """Runs on the OCR thread. Takes the result in, widens the voice
        vocabulary with the page's new words (a wake-phrase recognizer is
        built before anything is said, so it has to hear about them now),
        and tells the page."""
        if not self._doc.finish_ocr(job, status, payload):
            return
        with self._vocabulary.lock:
            self._vocabulary.refresh()
        self._dispatch_page_event(
            "lector:ocr",
            {"page_index": job.page_index, "status": self._doc.ocr_status(job.page_index)},
        )

    def find_in_document(self, query: str, start_page: int = 0, end_page: int | None = None) -> dict:
        """One slice of a find-in-document search, from `start_page` up to
        (not including) `end_page`, or to the last page when that is None.

        The frontend keeps calling with the returned `next_page` until it
        comes back None, and simply stops calling once the reader has typed
        something else — so a superseded search costs at most one slice.
        Page numbers are clamped rather than rejected, and an overlong query
        is cut: this is a bridge boundary, and a bad value from it should
        find nothing, not raise.
        """
        if not self._doc.is_open:
            return {"pages": [], "next_page": None, "text_pages": 0, "page_count": 0}
        page_count = self._doc.page_count
        needle = (query if isinstance(query, str) else "").strip()[:MAX_FIND_QUERY_CHARS]
        start = max(0, min(int(start_page), page_count))
        end = page_count if end_page is None else max(start, min(int(end_page), page_count))
        if not needle:
            return {"pages": [], "next_page": None, "text_pages": 0, "page_count": page_count}
        return {**self._doc.search(needle, start, end), "page_count": page_count}
