"""The bridge object exposed to the frontend as `window.pywebview.api`.

Per docs/ARCHITECTURE.md, this is the only file both sides import against:
frontend JS never reaches into `features/` directly, and Python features
never touch `frontend/` files. Every method here is called from
`frontend/js/bridge.js` (or a page's own JS) and returns JSON-serializable
data — no PySide6/Qt types, no live PyMuPDF objects.
"""
import base64
import os

import webview

from lector.features.home import recent as home_recent
from lector.features.reading.document import PdfDocument
from lector.features.settings import store as settings


def _pixmap_to_png_b64(pix) -> str:
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


class Api:
    def __init__(self):
        self._doc = PdfDocument()
        self._title = ""
        self._path = ""
        # Which reading layout the frontend is currently showing. The frontend
        # owns the DOM for it, but the value lives here too so it can be
        # persisted with the reading position and handed back on reopen.
        self._layout_mode = settings.BOOK
        # Last (page_index, layout_mode) actually written to settings.json, so
        # that page turns which don't change anything — and the flurry of
        # goto_page calls strip layout makes while scrolling — don't each cost
        # a file write.
        self._persisted_position = None

    # ------------------------------------------------------------------ #
    # Home / recent files                                                  #
    # ------------------------------------------------------------------ #

    def get_recent_files(self) -> list[dict]:
        return home_recent.list_recent()

    def open_pdf_dialog(self) -> str | None:
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("PDF Files (*.pdf)",)
        )
        return result[0] if result else None

    # ------------------------------------------------------------------ #
    # Document lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def open_pdf(self, path: str) -> dict:
        self._doc.open(path)
        self._title = os.path.splitext(os.path.basename(path))[0]
        self._path = path
        self._layout_mode = settings.BOOK
        self._persisted_position = None
        settings.add_recent_file(path)
        if settings.get_reopen_behavior() == settings.CONTINUE:
            self._restore_position(path)
        self._remember_position()
        return self._state()

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
        self._layout_mode = position["layout_mode"]
        page_index = min(position["page_index"], self._doc.page_count - 1)
        if page_index > 0:
            self._doc.go_to_page(page_index)
        self._doc.set_zoom(position["zoom"])

    def _remember_position(self) -> None:
        """Persist the current page/layout/zoom, but only when it actually moved."""
        if not self._path or not self._doc.is_open:
            return
        current = (self._doc.page_index, self._layout_mode, self._doc.zoom)
        if current == self._persisted_position:
            return
        settings.set_document_position(self._path, current[0], current[1], current[2])
        self._persisted_position = current

    def set_layout_mode(self, mode: str) -> dict:
        """Told by the frontend whenever the book/strip toggle is used."""
        if mode in (settings.BOOK, settings.STRIP):
            self._layout_mode = mode
            self._remember_position()
        return self._state()

    def get_state(self) -> dict:
        return self._state()

    def _state(self) -> dict:
        if not self._doc.is_open:
            return {"is_open": False}
        return {
            "is_open": True,
            "title": self._title,
            "page_index": self._doc.page_index,
            "page_count": self._doc.page_count,
            "zoom": self._doc.zoom,
            "zoom_pct": round(self._doc.zoom * 100),
            "zoom_min_pct": round(self._doc.MIN_ZOOM * 100),
            "zoom_max_pct": round(self._doc.MAX_ZOOM * 100),
            "is_dirty": self._doc.is_dirty,
            "can_undo": self._doc.can_undo,
            "can_redo": self._doc.can_redo,
            "highlight_count_page": self._doc.highlight_count_on_current_page(),
            "highlight_count_total": self._doc.highlight_count_total(),
            "layout_mode": self._layout_mode,
        }

    def get_page_image(self, index: int) -> dict:
        pix = self._doc.render_page(index)
        if pix is None:
            return {"image": None, "width": 0, "height": 0}
        return {"image": _pixmap_to_png_b64(pix), "width": pix.width, "height": pix.height}

    def get_page_sizes(self) -> list[dict]:
        return [{"width": w, "height": h} for w, h in self._doc.page_sizes()]

    # ------------------------------------------------------------------ #
    # Navigation / zoom                                                    #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Highlighting                                                         #
    # ------------------------------------------------------------------ #

    def get_page_words(self, page_index: int) -> dict:
        """Word boxes for one page, in PDF points, in reading order.

        The frontend needs these locally to paint the live selection as the
        reader drags: hit-testing the cursor against the word list has to
        happen on every mousemove, which is far too often to go back across
        the bridge for. Positions are in points (not rendered pixels) so the
        same payload stays correct at any zoom, and `line` groups words that
        share a line of text so a selection can be drawn as one continuous
        bar per line rather than one box per word.

        Word *indices* into this list are the currency of the highlight call
        below — the frontend sends back the range it had drawn, so what gets
        written is what the reader saw selected.
        """
        if not self._doc.is_open:
            return {"width": 0, "height": 0, "words": []}
        width, height = self._doc.page_size_points(page_index)
        return {
            "width": width,
            "height": height,
            "words": [
                {
                    "x0": w.rect.x0,
                    "y0": w.rect.y0,
                    "x1": w.rect.x1,
                    "y1": w.rect.y1,
                    "line": f"{w.block_no}:{w.line_no}",
                }
                for w in self._doc.words_on_page(page_index)
            ],
        }

    def highlight_words(self, page_index: int, start_idx: int, end_idx: int) -> dict:
        self._doc.highlight_word_indices(page_index, start_idx, end_idx)
        return self._state()

    def undo_highlight(self) -> dict:
        affected_page = self._doc.undo_highlight()
        state = self._state()
        state["affected_page"] = affected_page
        return state

    def redo_highlight(self) -> dict:
        affected_page = self._doc.redo_highlight()
        state = self._state()
        state["affected_page"] = affected_page
        return state

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    def get_save_behavior(self) -> str:
        return settings.get_save_behavior()

    def set_save_behavior(self, mode: str) -> None:
        settings.set_save_behavior(mode)

    def get_reopen_behavior(self) -> str:
        return settings.get_reopen_behavior()

    def set_reopen_behavior(self, mode: str) -> None:
        settings.set_reopen_behavior(mode)

    def perform_save(self, mode: str) -> dict:
        try:
            if mode == settings.OVERWRITE:
                self._doc.save_overwrite()
            else:
                suggested = self._suggested_copy_path()
                window = webview.windows[0]
                result = window.create_file_dialog(
                    webview.FileDialog.SAVE,
                    save_filename=os.path.basename(suggested),
                    directory=os.path.dirname(suggested),
                    file_types=("PDF Files (*.pdf)",),
                )
                path = result[0] if result else None
                if not path:
                    return {"ok": False, "cancelled": True}
                self._doc.save_copy(path)
            return {"ok": True, "state": self._state()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _suggested_copy_path(self) -> str:
        base, ext = os.path.splitext(self._doc.path or "untitled.pdf")
        return f"{base} (highlighted){ext}"

    # ------------------------------------------------------------------ #
    # Settings / theme                                                     #
    # ------------------------------------------------------------------ #

    def get_theme(self) -> str:
        return settings.get_theme()

    def set_theme(self, name: str) -> None:
        settings.set_theme(name)
