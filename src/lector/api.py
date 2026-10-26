"""The bridge object exposed to the frontend as `window.pywebview.api`.

Per docs/ARCHITECTURE.md, this is the only file both sides import against:
frontend JS never reaches into `features/` directly, and Python features
never touch `frontend/` files. Every method here is called from
`frontend/js/bridge.js` (or a page's own JS) and returns JSON-serializable
data — no PySide6/Qt types, no live PyMuPDF objects.
"""
import base64
import json
import logging
import os
import threading
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from lector.features.annotations import highlight_matcher, highlighter
from lector.features.annotations.highlighter import words_between
from lector.features.dialogs import browser as dialog_browser
from lector.features.home import file_drop
from lector.features.home import recent as home_recent
from lector.features.onboarding import state as onboarding
from lector.features.reading import ocr, open_errors
from lector.features.reading import outline as reading_outline
from lector.features.reading import safe_write
from lector.features.reading.document import PdfDocument
from lector.features.settings import store as settings
from lector.features.voice import command_grammar, reference, router
from lector.features.voice import engine as engine_module
from lector.features.voice.engine import VoiceEngine
from lector.shared import window_chrome

log = logging.getLogger(__name__)


# Longer than any phrase a reader would look for; a pasted paragraph is cut
# here rather than handed to MuPDF to scan every page with.
MAX_FIND_QUERY_CHARS = 200

# Bounds on a requested thumbnail's width in pixels: the Pages tab asks for its
# column width times the screen's pixel ratio, the progress bar's preview for a
# little more.
MIN_THUMBNAIL_PX = 16
MAX_THUMBNAIL_PX = 480

# `request_microphone`'s error when no capture could be opened. A code, not
# the audio library's text, which can name devices and drivers.
MICROPHONE_UNAVAILABLE = "microphone_unavailable"


def _pixmap_to_png_b64(pix) -> str:
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


class Api:
    def __init__(self, log_dir: Path | None = None):
        """`log_dir` is the app's log folder (`shared/app_log.py`); the OCR
        worker writes its crash log there. None, as in tests, writes none."""
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
        # Push-to-talk speech recognition (Milestone 5). Constructed eagerly
        # but inert — the Vosk model is only loaded on the first call that
        # needs it, so a missing model costs nothing until voice is used.
        self._voice = VoiceEngine()
        self._voice.subscribe(self._on_voice_result)
        # The viewport `start_listening` was last called with, frozen for the
        # duration of the utterance and reused by `highlight_by_voice` — the
        # reader hears the page they were looking at when they started
        # talking, not whatever is on screen by the time recognition finishes.
        self._voice_viewport: list[dict] = []
        # Scanned pages in that viewport whose text was not ready when the
        # recognizer's vocabulary was last built — their words could not
        # have been heard, so a failed highlight there is explained by OCR,
        # not by the reader (see `_voice_ocr_outcome`).
        self._voice_ocr_pages: list[int] = []
        # Reads scanned pages in a worker process, one at a time, on demand.
        self._ocr = ocr.OcrRunner(
            self._on_ocr_finished,
            crash_log=None if log_dir is None else log_dir / "ocr-worker.log",
        )
        # Which screen/dialog is currently listening (Milestone 8.4). Starts
        # at `router.DEFAULT_CONTEXT` (`reading`) rather than `home` so a
        # caller that never makes a `set_voice_context` call — every test in
        # this codebase today, and any future one that doesn't need this —
        # keeps hearing exactly the grammar it did before this router
        # existed. Pages narrow this explicitly once they load.
        self._voice_context = router.DEFAULT_CONTEXT
        # The contexts a modal (the "What can I say?" panel, a dictation
        # session) has pushed itself on top of, innermost last, so closing it
        # can hand back whatever was listening before rather than a context
        # the closing code has to remember to hardcode (Milestone 8.12).
        self._voice_context_stack: list[str] = []
        # pywebview runs every bridge call on its own thread, so a push and a
        # pop (or a page's own `set_voice_context`) can overlap. One lock keeps
        # the context and its stack consistent with each other.
        self._voice_context_lock = threading.RLock()
        # Load the model now, on a background thread, rather than leaving it
        # to the first `get_voice_status()`. That call is made by every page
        # showing the mic indicator, and a bridge call that blocks for the
        # several seconds a load takes can be outlived by the page that made
        # it — see `VoiceEngine.warm_up`. Costs nothing when no model is
        # installed: the warm-up finds that out with a stat and stops.
        self._voice.warm_up()
        # Idle wake listening, if the reader has it on. Started here rather
        # than from the frontend so it survives navigation between pages —
        # the microphone should not close and reopen every time the reader
        # goes from Home into a document.
        self._apply_wake_activation(settings.get_voice_activation()["wake_phrase"])
        # Set once `handle_window_closing` has let a close through, so the
        # `window.destroy()` it issues doesn't re-run the dirty check.
        self._closing_confirmed = False
        # The last Recent entry removed, as (index, raw entry), kept so the
        # removal toast's Undo can put it back with its reading position.
        # Lives here rather than in the frontend so no raw settings entry
        # has to make a round trip through JS.
        self._last_removed_recent: tuple[int, dict] | None = None

    # ------------------------------------------------------------------ #
    # Home / recent files                                                  #
    # ------------------------------------------------------------------ #

    def get_recent_files(self) -> list[dict]:
        return home_recent.list_recent()

    def get_thumbnails(self, paths: list[str]) -> dict[str, str | None]:
        """The first-page thumbnails (base64 PNG, or None) of rows the list
        handed back with `thumbnail_pending`, by path. A few per call: each
        bridge round trip costs several times what rendering one thumbnail
        does. Only for files on the Recent or Favorites list, so the frontend
        can't use this to read an arbitrary path."""
        listed = home_recent.listed_paths()
        return {
            path: home_recent.load_thumbnail(path) if path in listed else None
            for path in paths
        }

    def remove_recent_file(self, path: str) -> dict:
        """Drops `path` from the Recent list only (Milestone 8.9) — never
        touches the file on disk, per docs/PRD.md's scope note. Returns
        `{"entries": [...], "removed": bool}`: the refreshed list so the
        frontend can re-render without a second round trip, and whether
        `path` was actually listed (False for a stale view), so it only
        offers Undo for a removal that happened."""
        removal = settings.remove_recent_file(path)
        # Kept as-is when nothing was removed, so a toast still on screen
        # from the previous removal keeps a working Undo.
        if removal is not None:
            self._last_removed_recent = removal
        return {"entries": home_recent.list_recent(), "removed": removal is not None}

    def undo_remove_recent_file(self) -> dict:
        """Puts the most recently removed Recent entry back where it was
        (the removal toast's Undo). One level only. Returns
        `{"entries": [...], "status": str}` where status is
        `settings.RESTORED`, `ALREADY_LISTED`, `LIST_FULL`, or None when
        there was nothing to undo."""
        status = None
        if self._last_removed_recent is not None:
            index, entry = self._last_removed_recent
            self._last_removed_recent = None
            status = settings.restore_recent_file(index, entry)
        return {"entries": home_recent.list_recent(), "status": status}

    def get_favorites(self) -> list[dict]:
        """The Favorites view's entries, in the same shape as
        `get_recent_files`."""
        return home_recent.list_favorites()

    def set_favorite(self, path: str, favorite: bool) -> dict:
        """Pins or unpins `path`. Only a file the reader can see — one in
        Recent or already a favorite — can be pinned, so the frontend can't
        use this to put an arbitrary path into settings.json. Returns
        `{"favorite": bool, "error": str | None}`, `favorite` being the state
        after the call."""
        if favorite:
            listed = any(e["path"] == path for e in settings.get_recent_files())
            if not listed and not settings.is_favorite(path):
                return {"favorite": False, "error": "That file is no longer in Recent."}
            settings.add_favorite(path)
        else:
            settings.remove_favorite(path)
        return {"favorite": settings.is_favorite(path), "error": None}

    def show_in_folder(self, path: str) -> dict:
        """Opens the OS file manager with `path` selected (Recent list's
        "Show in folder"). Only for a path that is in the Recent list and
        still on disk — the frontend can't use this to launch the file
        manager on an arbitrary location. Returns `{"error": str | None}`."""
        if not any(e["path"] == path for e in settings.get_recent_files()):
            return {"error": "That file is no longer in Recent."}
        if not os.path.isfile(path):
            return {"error": "That file has been moved or deleted."}
        try:
            home_recent.reveal_in_file_manager(path)
        except OSError:
            return {"error": "Couldn't open the folder."}
        return {"error": None}

    def browse_directory(self, path: str | None = None) -> dict:
        """Lists a directory for the in-app Open/Save-As screens (Milestone
        8.7), replacing the native `create_file_dialog()` the router could
        not see. `path=None` starts at `dialog_browser.default_open_dir()` —
        the Open dialog's own default; the Save-As dialog always passes an
        explicit starting path from `get_save_dialog_start()` instead, since
        its default is the current document's folder, not the most recent
        file's."""
        return dialog_browser.list_directory(path or dialog_browser.default_open_dir())

    def list_drives(self) -> dict:
        """Top-level nodes of the Open/Save-As directory tree — see
        `dialog_browser.list_drives()` for the platform-specific listing
        logic."""
        return dialog_browser.list_drives()

    def list_quick_access(self) -> dict:
        """Home/Desktop/Documents/Downloads for the Open/Save-As dialogs'
        Quick Access chips — see `dialog_browser.list_quick_access()`."""
        return dialog_browser.list_quick_access()

    def get_save_dialog_start(self) -> dict:
        """Where the in-app Save-As screen should start browsing and what
        filename to pre-fill — both derived from `_suggested_copy_path()`,
        the same suggestion the native dialog used to pre-fill."""
        suggested = self._suggested_copy_path()
        return {
            "dir": dialog_browser.default_save_dir(suggested),
            "filename": os.path.basename(suggested),
        }

    # ------------------------------------------------------------------ #
    # Document lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def open_pdf(self, path: str, password: str | None = None) -> dict:
        """Open `path` (unlocked with `password`, for a protected PDF) and
        return the reader's state. A PDF that can't be opened comes back as
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
        # A viewport names pages of the document it was measured on; kept
        # across an open, the next vocabulary rebuild would ask the new one
        # for pages it may not have.
        self._voice_viewport = []
        self._voice_ocr_pages = []
        self._title = os.path.splitext(os.path.basename(path))[0]
        self._path = path
        # A PDF never opened before (or opened with "Always start at the
        # beginning") comes up in the reader's default view (Settings >
        # Reading); one with a saved position gets its own back below.
        self._layout_mode = settings.get_default_view()
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
        highlight_counts = self._doc.highlight_counts()
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
            "highlight_count_page": highlight_counts.get(self._doc.page_index, 0),
            "highlight_count_total": sum(highlight_counts.values()),
            # The progress bar's markers: every page with a highlight on it,
            # saved or not, so undo and redo move them as they happen.
            "highlighted_pages": sorted(highlight_counts),
            "highlight_color": settings.get_highlight_color(),
            "layout_mode": self._layout_mode,
        }

    def get_page_image(self, index: int, device_scale: float = 1.0) -> dict:
        """Page `index` at the current zoom. `width` and `height` are the
        size to show it at in CSS pixels (100% is the paper's real size); the
        bitmap itself is drawn for `device_scale` (the web view's
        devicePixelRatio, clamped to 1-4) so it is sharp on a high DPI screen."""
        pix = self._doc.render_page(index, device_scale)
        if pix is None:
            return {"image": None, "width": 0, "height": 0}
        width, height = self._doc.page_css_size(index)
        return {"image": _pixmap_to_png_b64(pix), "width": width, "height": height}

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
        width = max(MIN_THUMBNAIL_PX, min(int(width), MAX_THUMBNAIL_PX))
        pix = self._doc.render_thumbnail(int(index), width)
        if pix is None:
            return {"image": None, "width": 0, "height": 0}
        return {"image": _pixmap_to_png_b64(pix), "width": pix.width, "height": pix.height}

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

    def _on_ocr_finished(self, job: ocr.OcrJob, status: str, payload: dict | None) -> None:
        """Runs on the OCR thread. Takes the result in, widens the voice
        vocabulary with the page's new words (a wake-phrase recognizer is
        built before anything is said, so it has to hear about them now),
        and tells the page."""
        if not self._doc.finish_ocr(job, status, payload):
            return
        with self._voice_context_lock:
            self._refresh_voice_vocabulary()
        self._dispatch_page_event(
            "lector:ocr",
            {"page_index": job.page_index, "status": self._doc.ocr_status(job.page_index)},
        )

    def _voice_ocr_outcome(self) -> dict | None:
        """Why a voice highlight could not have matched, when the reason is a
        scanned page's text: `{"page_index", "status"}` for the first such
        page in the viewport, or None when OCR is not the explanation.

        Covers the pages whose text was missing when the vocabulary was last
        built (`_voice_ocr_pages`). One not read yet is started now, so the
        reader who spoke before the dwell prefetch got there is not left
        waiting on nothing; one that finished since reports "done", which
        the frontend words as "say it again".
        """
        for page_index in self._voice_ocr_pages:
            job = self._doc.begin_ocr(page_index)
            if job is not None:
                self._ocr.submit(job)
            status = self._doc.ocr_status(page_index)
            if status is not None:
                return {"page_index": page_index, "status": status}
        return None

    def shutdown_ocr(self) -> None:
        """Stop the OCR worker process. Called once the window has closed."""
        self._ocr.shutdown()

    # ------------------------------------------------------------------ #
    # Find in document                                                     #
    # ------------------------------------------------------------------ #

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

    def highlight_chars(self, page_index: int, start_idx: int, end_idx: int) -> dict:
        self._doc.highlight_char_indices(page_index, start_idx, end_idx, settings.get_highlight_color())
        return self._state()

    def get_highlight_palette(self) -> list[str]:
        """The five colour names, in the order the swatches show them. Names
        only: each one's colour is theme.css's `--highlight-<name>`."""
        return list(highlighter.COLOR_NAMES)

    def get_highlight_color(self) -> str:
        return settings.get_highlight_color()

    def set_highlight_color(self, name: str) -> dict:
        """Make `name` the active colour. An unknown name is ignored rather
        than raised: this is a bridge boundary. Returns the state, whose
        `highlight_color` is what was actually stored."""
        if name in highlighter.COLOR_NAMES:
            settings.set_highlight_color(name)
        return self._state()

    def get_page_highlights(self, page_index: int) -> dict:
        """Every highlight on one page, so the frontend can make each one a
        clickable, focusable target: `{"width", "height", "highlights":
        [{"id", "color", "rects", "text"}]}`, sizes and rects in PDF points
        like `get_page_chars`'s. `color` is None for a colour another app
        wrote that is not in the palette; `text` is what the highlight covers,
        for the target's accessible name."""
        page_index = int(page_index)
        if not self._doc.is_open:
            return {"width": 0, "height": 0, "highlights": []}
        width, height = self._doc.page_size_points(page_index)
        return {
            "width": width,
            "height": height,
            "highlights": [
                {
                    "id": h.xref,
                    "color": h.color,
                    "rects": [
                        {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                        for r in self._doc.as_shown(page_index, h.rects)
                    ],
                    "text": self._doc.text_in_rects(page_index, h.rects),
                }
                for h in self._doc.highlights_on_page(page_index)
            ],
        }

    def recolor_highlight(self, page_index: int, highlight_id: int, color: str) -> dict:
        changed = self._doc.set_highlight_color(int(page_index), int(highlight_id), color)
        state = self._state()
        state["affected_page"] = int(page_index) if changed else None
        return state

    def remove_highlight(self, page_index: int, highlight_id: int) -> dict:
        removed = self._doc.remove_highlight(int(page_index), int(highlight_id))
        state = self._state()
        state["affected_page"] = int(page_index) if removed else None
        return state

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

    def highlight_by_voice(self, query: str, sentence: bool = False, color: str | None = None) -> dict:
        """Matches a spoken phrase against the viewport `start_listening` was
        last called with, and writes the highlight if one is found.

        `color` is a one-off ("highlight <words> in green"): it colours this
        highlight only and leaves the active colour as it was.

        `query` is already normalized (lowercase, no punctuation) by
        `command_grammar.parse` — the same treatment `highlight_matcher`
        gives every page word, so the two compare on equal footing. A miss
        is not an error: `docs/PRD.md` treats a wrong highlight as worse than
        no highlight, so this reports `matched: False` for the frontend to
        show as a "didn't catch that" hint rather than guessing.
        """
        pages = []
        for region in self._voice_viewport:
            # The region is part of the page as shown; indices are the same
            # either way, so the match below highlights the same words.
            words = self._doc.words_as_shown(region["page_index"])
            visible = highlight_matcher.visible_word_indices(words, region["y0"], region["y1"])
            pages.append((region["page_index"], words, visible))
        match = highlight_matcher.find_word_match(pages, query)
        state = self._state()
        if match is None:
            state["voice_highlight"] = {
                "matched": False,
                "query": query,
                "ocr": self._voice_ocr_outcome(),
            }
            return state

        words = self._doc.words_on_page(match.page_index)
        start_idx, end_idx = match.start_idx, match.end_idx
        if sentence:
            start_idx, end_idx = highlight_matcher.extend_to_sentence(words, start_idx, end_idx)
        if color not in highlighter.COLOR_NAMES:
            color = settings.get_highlight_color()
        self._doc.highlight_word_indices(match.page_index, start_idx, end_idx, color)

        state = self._state()
        state["voice_highlight"] = {
            "matched": True,
            "query": query,
            "color": color,
            "affected_page": match.page_index,
            "text": " ".join(w.text for w in words_between(words, start_idx, end_idx)),
        }
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

    def perform_save(self, mode: str, path: str | None = None) -> dict:
        """`path` is required when `mode` is `settings.COPY` — the in-app
        Save-As screen (Milestone 8.7) gathers it via `browse_directory()`/
        `get_save_dialog_start()` before calling this, the same way the
        native dialog used to hand back a chosen path.

        A failure comes back as `error`, a sentence for the reader saying what
        happened and that the file on disk is unchanged — never the library's
        own text, which names paths and MuPDF error codes."""
        try:
            if mode == settings.OVERWRITE:
                self._doc.save_overwrite()
            else:
                if not path:
                    return {"ok": False, "cancelled": True}
                self._doc.save_copy(path)
            return {"ok": True, "state": self._state()}
        except safe_write.SaveError as exc:
            if exc.closes_document:
                # The PDF couldn't be opened again, so no document is left
                # open: Reading has to leave.
                return {"ok": False, "error": exc.message, "closed": True}
            return {"ok": False, "error": exc.message}
        except Exception:
            log.exception("Saving the PDF failed (%s)", mode)
            return {"ok": False, "error": safe_write.MESSAGES[safe_write.UNKNOWN]}

    def describe_save_target(self, path: str) -> dict:
        """What the Save-As screen needs to know about a chosen path before
        saving there: whether a file is already there (the reader is asked
        before it is replaced), and whether that file is the open PDF itself
        (which a copy refuses, so no "replace?" question is asked for it).
        `name` and `folder` are what that question shows."""
        return {
            "exists": os.path.isfile(path),
            "is_open_document": self._doc.is_open_document(path),
            "name": os.path.basename(path),
            "folder": os.path.dirname(path),
        }

    def _suggested_copy_path(self) -> str:
        """`<name> (highlighted).pdf` beside the original, or, when that is
        taken, the first free `<name> (highlighted) (2).pdf`, (3), …, so the
        suggestion never names a file that is already there."""
        base, ext = os.path.splitext(self._doc.path or "untitled.pdf")
        candidate = f"{base} (highlighted){ext}"
        number = 2
        while os.path.exists(candidate):
            candidate = f"{base} (highlighted) ({number}){ext}"
            number += 1
        return candidate

    # ------------------------------------------------------------------ #
    # Settings / theme                                                     #
    # ------------------------------------------------------------------ #

    def get_theme(self) -> str:
        return settings.get_theme()

    def set_theme(self, name: str) -> None:
        settings.set_theme(name)
        window_chrome.apply(window_chrome.WINDOW_TITLE, settings.get_theme(), icon=False)

    def get_recent_view(self) -> str:
        return settings.get_recent_view()

    def set_recent_view(self, mode: str) -> None:
        settings.set_recent_view(mode)

    def get_reading_preferences(self) -> dict:
        """Settings > Reading, plus whether the outline panel was left open
        and how wide: `{"default_view", "progress_bar", "outline_open",
        "outline_width", "outline_width_min", "outline_width_max",
        "highlight_color", "highlight_palette"}`."""
        return {
            "default_view": settings.get_default_view(),
            "progress_bar": settings.get_progress_bar(),
            "outline_open": settings.get_outline_open(),
            "outline_width": settings.get_outline_width(),
            "outline_width_min": settings.OUTLINE_WIDTH_MIN,
            "outline_width_max": settings.OUTLINE_WIDTH_MAX,
            "highlight_color": settings.get_highlight_color(),
            "highlight_palette": list(highlighter.COLOR_NAMES),
        }

    def set_default_view(self, mode: str) -> dict:
        """Ignores anything but book or strip (a bridge boundary) and returns
        the preferences as stored."""
        if mode in (settings.BOOK, settings.STRIP):
            settings.set_default_view(mode)
        return self.get_reading_preferences()

    def set_progress_bar(self, on: bool) -> dict:
        settings.set_progress_bar(bool(on))
        return self.get_reading_preferences()

    def set_outline_open(self, is_open: bool) -> dict:
        settings.set_outline_open(bool(is_open))
        return self.get_reading_preferences()

    def set_outline_width(self, width: float) -> dict:
        """The outline panel's width after a drag. Anything that isn't a
        number is ignored (a bridge boundary); a number is kept within the
        panel's limits."""
        if isinstance(width, (int, float)) and not isinstance(width, bool) and width == width:
            settings.set_outline_width(width)
        return self.get_reading_preferences()

    # ------------------------------------------------------------------ #
    # Voice activation modes (Milestone 8)                                 #
    # ------------------------------------------------------------------ #

    def get_voice_activation(self) -> dict:
        """`{"push_to_talk": bool, "wake_phrase": bool}` — docs/PRD.md's two
        independently toggleable activation modes."""
        return settings.get_voice_activation()

    def set_voice_activation(self, push_to_talk: bool, wake_phrase: bool) -> dict:
        """Store both flags and bring the engine in line with them.

        Returns the stored pair rather than nothing, so the caller renders
        what was actually saved instead of what it optimistically sent.
        """
        settings.set_voice_activation(push_to_talk, wake_phrase)
        self._apply_wake_activation(wake_phrase)
        return settings.get_voice_activation()

    def _apply_wake_activation(self, enabled: bool) -> None:
        """Start or stop idle wake listening, off the calling thread.

        Off-thread because starting it can need the speech model, and a load
        takes seconds — the same reason `VoiceEngine.warm_up` exists. Blocking
        a bridge call for that long risks the page that made it navigating
        away before the reply lands, which kills pywebview's callback.
        """
        if not enabled:
            self._voice.stop_wake_listening()
            return
        threading.Thread(
            target=self._voice.start_wake_listening,
            name="voice-wake-start",
            daemon=True,
        ).start()

    def request_microphone(self) -> dict:
        """Open and immediately close the microphone, to make the OS ask.

        Neither Windows nor macOS has an API for "request microphone access"
        that is separate from *using* the microphone: the permission prompt
        is raised by the first capture attempt. So onboarding's "Allow
        microphone" button does the smallest real capture there is, and
        reports what happened.

        Returns `{"granted": bool, "error": str | None}`. A refusal is a
        normal outcome, not an exception — docs/PRD.md requires the app to
        stay fully usable when the reader says no. `error` is a fixed code,
        not the audio library's text (onboarding shows its own copy).
        """
        try:
            import sounddevice

            stream = sounddevice.RawInputStream(
                samplerate=engine_module.SAMPLE_RATE,
                blocksize=engine_module.BLOCK_SIZE,
                dtype="int16",
                channels=engine_module.CHANNELS,
            )
            stream.start()
            stream.stop()
            stream.close()
        except Exception:
            log.warning("The microphone could not be opened", exc_info=True)
            return {"granted": False, "error": MICROPHONE_UNAVAILABLE}
        return {"granted": True, "error": None}

    # ------------------------------------------------------------------ #
    # Command reference ("What can I say?")                                #
    # ------------------------------------------------------------------ #

    def get_command_reference(self) -> dict:
        """The categorized command list the discoverability panel renders.

        Assembled from the grammar itself (`voice/reference.py`) rather than
        written out in the frontend, so the panel cannot offer a phrasing the
        recognizer would not accept. Scoped to `self._voice_context`
        (Milestone 8.10), so opening the panel from a screen with its own
        command set shows that screen's commands rather than always the
        reading grammar's.
        """
        return reference.panel(self._voice_context)

    # ------------------------------------------------------------------ #
    # Voice (Milestone 5 — push-to-talk; Milestone 8 — wake phrase)        #
    # ------------------------------------------------------------------ #

    def get_voice_status(self) -> dict:
        """`{"available", "listening", "error"}` for the mic indicator.

        `available: False` is a normal state, not a failure to report loudly:
        docs/PRD.md requires the app to stay fully usable without voice, so
        the frontend renders this as "Voice unavailable" and carries on.
        """
        return self._voice.status()

    def start_listening(self, viewport: list[dict] | None = None) -> dict:
        """Called on push-to-talk keydown. Safe to call repeatedly — the key
        auto-repeats while held.

        `viewport` is the reading view's currently visible page regions —
        `[{"page_index", "y0", "y1"}, ...]` in PDF points on the page as
        shown, like `get_page_chars`'s boxes — so a spoken
        highlight phrase can be matched later. Home has no document open and
        passes nothing, which is also what widens nothing: `set_vocabulary`
        is only called with more than the navigation grammar when a document
        is actually open, so Home's recognizer is unaffected by this.
        """
        self._voice_viewport = viewport or []
        self._refresh_voice_vocabulary()
        return self._voice.start_listening()

    def stop_listening(self) -> dict:
        """Called on push-to-talk keyup. Returns the final recognized phrase,
        which is `""` when nothing intelligible was heard."""
        return self._voice.stop_listening()

    def update_voice_viewport(self, viewport: list[dict] | None = None) -> None:
        """Keep the wake-phrase path's idea of what is on screen current.

        Push-to-talk can gather the viewport at the moment the key goes down,
        because the reader's keypress is what starts the utterance. A wake
        phrase has no such moment: by the time "Hey Lector" has been heard,
        the recognizer for the command that follows is already being built,
        and a grammar-constrained recognizer can only ever return words that
        were in its vocabulary when it was built (see `engine.py`). So the
        reading view pushes the visible region here as it scrolls, and a wake
        simply uses the latest one.

        Cheap to call redundantly — it only recomputes the word list — but
        the frontend still debounces it, because on a fast scroll "latest"
        arrives many times a second.
        """
        self._voice_viewport = viewport or []
        self._refresh_voice_vocabulary()

    def set_voice_context(self, context: str) -> dict:
        """Told by a page or dialog when it gains focus (Milestone 8.4).

        Swaps the recognizer's active grammar to `context`'s own scoped
        commands plus the always-on global ones (undo/redo/help/go home) —
        the same grammar-swap `wake.py` already does between its idle and
        full vocabularies, generalized here to more than two grammars.
        `router.CONTEXTS` is the fixed set of names a caller may pass;
        anything else is rejected rather than silently accepted, since a
        typo'd context name would otherwise leave the reader talking to
        whatever grammar happened to be active before, with no signal that
        their intended context was never applied.

        Returns `{"context": ...}` — the context actually applied, so the
        caller can confirm it rather than assume its own request landed.
        """
        if context not in router.CONTEXTS:
            raise ValueError(f"unknown voice context: {context!r}")
        # A screen declaring itself is a fresh baseline: any modal still on
        # the stack belonged to a page that has since been left, and popping
        # into its context would put the wrong grammar on this one.
        with self._voice_context_lock:
            self._voice_context_stack.clear()
            self._voice_context = context
            self._refresh_voice_vocabulary()
            return {"context": self._voice_context}

    def push_voice_context(self, context: str) -> dict:
        """Enter a modal scope, remembering the context it covers (Milestone 8.12).

        For anything that opens on top of a screen and must give the screen's
        grammar back when it closes: the "What can I say?" panel, and dictation
        inside it. `pop_voice_context` undoes exactly one push, so nested modals
        unwind in order. An unknown name is rejected before anything changes.
        """
        if context not in router.CONTEXTS:
            raise ValueError(f"unknown voice context: {context!r}")
        with self._voice_context_lock:
            self._voice_context_stack.append(self._voice_context)
            self._voice_context = context
            self._refresh_voice_vocabulary()
            return {"context": self._voice_context}

    def pop_voice_context(self) -> dict:
        """Leave the innermost modal scope and restore the context beneath it.

        With nothing pushed this is a no-op that reports the current context,
        so a close handler that runs twice (Escape then the Close button) can
        never pop past the screen's own grammar.
        """
        with self._voice_context_lock:
            if self._voice_context_stack:
                self._voice_context = self._voice_context_stack.pop()
                self._refresh_voice_vocabulary()
            return {"context": self._voice_context}

    def _refresh_voice_vocabulary(self) -> None:
        """Recompute and apply the recognizer's active grammar.

        The single seam `start_listening`, `update_voice_viewport`, and
        `set_voice_context` all call through whenever something that affects
        the grammar changes — the active context or the visible viewport —
        so the fixed-vocabulary and viewport-vocabulary computation can't
        drift apart the way duplicating it at each call site would risk.

        `DICTATION` (Milestone 8.8) bypasses the usual computation entirely:
        `VoiceEngine.set_vocabulary(None)` puts the recognizer into
        open-vocabulary mode, so viewport widening (meaningless for free
        dictated text) is skipped rather than computed and then ignored.
        """
        if self._voice_context == router.DICTATION:
            self._voice.set_vocabulary(None)
            return
        # Asked before the vocabulary is built, because for a scanned page
        # read in an earlier session this is what loads its cached words.
        self._voice_ocr_pages = [
            region["page_index"]
            for region in self._voice_viewport
            if self._doc.ocr_status(region["page_index"]) not in (None, ocr.DONE)
        ]
        extra = self._viewport_vocabulary(self._voice_viewport) if self._doc.is_open else []
        self._voice.set_vocabulary(router.vocabulary_for(self._voice_context) + extra)

    def _viewport_vocabulary(self, viewport: list[dict]) -> list[str]:
        """Normalized words visible in `viewport`, for widening the grammar.

        Grammar-constrained recognition (docs/ARCHITECTURE.md, `engine.py`)
        can only ever return a word that was in its vocabulary when listening
        started — so a spoken highlight phrase needs the *visible* page's
        words added before `start_listening` begins, not looked up after the
        fact when it's too late for Vosk to have heard them.
        """
        words: set[str] = set()
        for region in viewport:
            page_words = self._doc.words_as_shown(region["page_index"])
            visible = highlight_matcher.visible_word_indices(
                page_words, region["y0"], region["y1"]
            )
            for i in visible:
                token = highlight_matcher.normalize_word(page_words[i].text)
                if token:
                    words.add(token)
        return sorted(words)

    def _on_voice_result(self, result: dict) -> None:
        """Fan a recognition result out to the frontend as a DOM event.

        This is the "discrete events only" bridge direction docs/TECH_STACK.md
        describes — Python pushing to JS, where every other method on this
        class is JS pulling from Python. Dispatching a CustomEvent rather than
        calling one named function means Milestone 6's navigation commands and
        Milestone 7's highlight matcher can each subscribe independently
        without this method growing a list of callees.

        Final results carry a `command` key — the parsed intent, or `None`
        when nothing in the grammar matched — and a `clarify` key, non-`None`
        only when Milestone 8.3's near-miss tier has a candidate worth
        surfacing as "did you mean '<candidate>'?" instead of staying silent.
        Interpretation happens here, on the Python side, because
        docs/ARCHITECTURE.md keeps command interpretation in `voice/` and
        leaves the frontend to decide what a given intent *does*: this sends
        `NEXT_PAGE`, not "scroll the strip". Resolution goes through
        `router.resolve` (Milestone 8.4) rather than `command_grammar`
        directly, so which commands a phrase can match depends on
        `self._voice_context` — global commands always, plus whatever the
        active context's own scoped grammar adds.

        Runs on the engine's worker thread, and deliberately swallows failures:
        no window yet (results arriving during teardown) must not kill the
        audio thread.
        """
        try:
            window = webview.windows[0]
        except IndexError:
            return
        if result.get("final"):
            resolved = router.resolve(
                self._voice_context, result.get("text", ""), result.get("alternatives")
            )
            result = {**result, "command": resolved["command"], "clarify": resolved["clarify"]}
            # "highlight <words>" on a scanned page whose text is not read
            # yet reaches here as a bare "highlight": the recognizer drops
            # every word outside its vocabulary. That is no command at all,
            # and would read as "not a command I know" without this.
            if resolved["command"] is None and command_grammar.is_bare_highlight(result.get("text", "")):
                result["ocr"] = self._voice_ocr_outcome()
        payload = json.dumps(result)
        try:
            window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('lector:voice', {{detail: {payload}}}))"
            )
        except Exception:
            # Usually a page mid-navigation or a window closing. Logged
            # without the payload: it holds what the reader said.
            log.debug("Could not deliver a voice result to the page", exc_info=True)

    # ------------------------------------------------------------------ #
    # Drag-and-drop of a PDF onto the window (Milestone 8.12)              #
    # ------------------------------------------------------------------ #

    def bind_file_drop(self, window) -> None:
        """Listen for a file dropped anywhere in `window`'s current page.

        Bound from `window.events.loaded` (see `__main__.py`), because
        pywebview forgets every DOM binding when a page navigates and Lector
        moves between Home, Settings and Reading by navigating. That same reset
        is why calling this once per load cannot stack duplicate handlers.

        `prevent_default` is what stops the WebView from navigating to the
        dropped file and replacing the whole app with a bare PDF viewer, on any
        page, whether or not it loads `filedrop.js`. The cost is that a *text*
        drag dropped into an input (the search boxes) is cancelled too; that is
        accepted, since the alternative is a file drop that can replace the
        whole app. `stop_propagation` keeps a page's own bubble-phase `drop`
        handling out of it (`filedrop.js` listens in the capture phase and is
        unaffected).
        """
        window.dom.document.on(
            "drop",
            DOMEventHandler(self._on_file_drop, prevent_default=True, stop_propagation=True),
        )

    def _on_file_drop(self, event: dict) -> None:
        """Tell the page which PDF was dropped, or why it can't be opened.

        Runs on a pywebview worker thread. Does not open the file: the page
        decides what opening means on its screen (Reading asks about unsaved
        highlights first), so this only reports.
        """
        outcome = file_drop.pdf_from_drop(event)
        if outcome is None:
            return
        self._dispatch_page_event("lector:filedrop", outcome)

    def open_file_from_launch(self, path: str) -> None:
        """A file Lector was started with, or one a second launch handed over
        (`shared/single_instance.py`). Sent to the page as a drop, so each
        screen keeps its own meaning for it (Reading asks about unsaved
        highlights first). Called from Python, by `__main__`."""
        self._dispatch_page_event("lector:filedrop", file_drop.pdf_from_path(path))

    def _dispatch_page_event(self, name: str, detail: dict) -> None:
        """Push a `CustomEvent` to the current page, the same direction
        `_on_voice_result` uses. Quietly does nothing with no window, which is
        what teardown looks like."""
        try:
            window = webview.windows[0]
        except IndexError:
            return
        payload = json.dumps(detail)
        try:
            window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('{name}', {{detail: {payload}}}))"
            )
        except Exception:
            log.debug("Could not deliver %s to the page", name, exc_info=True)

    def notify_background_error(self) -> None:
        """Tell the reader, once per page, that something failed off-screen.

        Called by `shared/app_log.py`'s `threading.excepthook`, from whichever
        thread died, after the failure is already logged. The page decides how
        to show it (`frontend/js/background-error.js`). Never raises: it runs
        inside the exception hook.
        """
        try:
            window = webview.windows[0]
            window.evaluate_js("window.lectorBackgroundError && window.lectorBackgroundError()")
        except Exception:
            log.debug("Could not report a background error to the page", exc_info=True)

    def shutdown_voice(self) -> None:
        """Release the microphone. Called when the window closes."""
        self._voice.shutdown()

    def handle_window_closing(self) -> bool | None:
        """Gatekeeper bound to `window.events.closing` (see `__main__.py`).

        pywebview registers this event with `should_lock=True`, so it runs
        synchronously on the platform's own UI thread, and a literal `False`
        return is pywebview's signal to cancel the close. There is no safe
        way to block that call waiting on `promptSaveIfDirty` — it's an async
        JS confirmation, and blocking the UI thread for it risks deadlocking
        against the very message loop that would deliver its result. So a
        dirty document always cancels the *first* close attempt, kicks off
        the save-or-discard prompt in the frontend, and — once that resolves
        — reissues the close with `window.destroy()`, which pywebview itself
        marshals back onto the UI thread (see `platforms/winforms.py`'s
        `destroy_window`, which wraps it in `Control.Invoke`). Only when a
        document is both open and dirty does any of this run; otherwise the
        close proceeds immediately, same as before this method existed.
        """
        if self._closing_confirmed or not self._doc.is_open or not self._doc.is_dirty:
            self.shutdown_voice()
            return None

        try:
            window = webview.windows[0]
        except IndexError:
            self.shutdown_voice()
            return None

        def _on_resolved(should_close) -> None:
            if not should_close:
                return
            self._closing_confirmed = True
            self.shutdown_voice()
            window.destroy()

        window.evaluate_js("promptSaveIfDirty(true)", callback=_on_resolved)
        return False

    def close_app(self) -> None:
        """The `CLOSE_APP` global voice command's ("close app"/"quit") entry
        point (Milestone 8.11).

        Calls `window.destroy()` — the exact same call
        `handle_window_closing()` above already makes to reissue a confirmed
        close. On Windows this is `Window.Close()`, which fires `FormClosing`
        the same way clicking the OS close button or Alt+F4 would, so it
        re-enters `handle_window_closing()` through `window.events.closing`
        and gets the identical dirty-document check and save-or-discard
        prompt for free. No separate close logic lives here.
        """
        try:
            window = webview.windows[0]
        except IndexError:
            return
        window.destroy()

    # ------------------------------------------------------------------ #
    # Onboarding (Milestone 4 stub — real flow is Milestone 8)             #
    # ------------------------------------------------------------------ #

    def get_onboarding_seen(self) -> bool:
        return onboarding.has_seen()

    def set_onboarding_seen(self) -> None:
        onboarding.mark_seen()
