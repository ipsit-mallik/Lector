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
import math
import os
import threading
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from lector.features.annotations import highlight_matcher, highlighter
from lector.features.annotations.highlighter import words_between
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import choices as tool_choices
from lector.features.file_tools import destinations as tool_destinations
from lector.features.file_tools import engine as tool_engine
from lector.features.file_tools import formats as tool_formats
from lector.features.file_tools import jobs as file_jobs
from lector.features.file_tools import messages as tool_messages
from lector.features.file_tools import naming as tool_naming
from lector.features.file_tools import tools as file_tools
from lector.features.home import file_drop
from lector.features.home import recent as home_recent
from lector.features.onboarding import state as onboarding
from lector.features.reading import ocr, open_errors, page_changes, page_plan
from lector.features.reading import outline as reading_outline
from lector.features.reading import safe_write
from lector.features.reading.document import PdfDocument
from lector.features.settings import store as settings
from lector.features.voice import command_grammar, reference, router
from lector.features.voice import engine as engine_module
from lector.features.voice.engine import VoiceEngine
from lector.shared import highlight_options, window_chrome

log = logging.getLogger(__name__)


# Longer than any phrase a reader would look for; a pasted paragraph is cut
# here rather than handed to MuPDF to scan every page with.
MAX_FIND_QUERY_CHARS = 200

# Most points a freehand stroke may carry across the bridge. The frontend
# simplifies a stroke before sending it, so a long one is a few hundred; this
# only stops a runaway payload being written into the PDF.
MAX_INK_POINTS = 4096

# Longest gesture name `set_highlight_appearance` takes: the editor sends a
# short counter-based id per slider drag; this only bounds what is held on
# the undo stack.
MAX_GESTURE_CHARS = 64

# Bounds on a requested thumbnail's width in pixels: the Pages tab asks for its
# column width times the screen's pixel ratio, the progress bar's preview for a
# little more.
MIN_THUMBNAIL_PX = 16
MAX_THUMBNAIL_PX = 480

# `request_microphone`'s error when no capture could be opened. A code, not
# the audio library's text, which can name devices and drivers.
MICROPHONE_UNAVAILABLE = "microphone_unavailable"


# A file tool's `source` meaning "the PDF open in the reader, as it is now".
TOOL_SOURCE_OPEN = "open"

# Most files one Convert to PDF takes, so a runaway list can't be sent.
MAX_CONVERT_INPUTS = 500

# How long describing a file (in the worker, behind any OCR read) may take.
DESCRIBE_TIMEOUT_S = 60

# How long closing waits for a cancelled job to clean up before going on.
JOB_STOP_WAIT_S = 10

# What Done says when its page changes couldn't be applied.
PAGE_CHANGES_FAILED = "Couldn't apply the page changes. The PDF is as it was; your changes are still here."


class _BadToolRequest(Exception):
    """A tool request the bridge refuses, for one of `tool_messages`'
    reasons (or a `safe_write` one)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _file_size(path: str | None) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def _checked_pages(value, page_count: int) -> list[int]:
    """1-based page numbers from the page, as 0-based indices: refused
    unless every one is a whole number on the PDF, none repeated."""
    if not isinstance(value, list) or not value or len(value) > page_count:
        raise _BadToolRequest(tool_messages.WORKER_FAILED)
    indices = []
    for number in value:
        index = _page_index(number)
        if index is None or not 1 <= index <= page_count:
            raise _BadToolRequest(tool_messages.WORKER_FAILED)
        indices.append(index - 1)
    if len(set(indices)) != len(indices):
        raise _BadToolRequest(tool_messages.WORKER_FAILED)
    return sorted(indices)


def _with_place(run, where: str):
    """A job whose result also says where its output went ("in Documents"),
    for the dialog's result line and the finished toast."""

    def run_and_say_where(ctx):
        result = run(ctx)
        return {**result, "where": where} if isinstance(result, dict) else result

    return run_and_say_where


def _pixmap_to_png_b64(pix) -> str:
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def _finite(value) -> bool:
    """Whether `value` is a finite number (a bool is not; JSON `true` is no
    coordinate). An integer too large for a float is not finite enough."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _page_index(value) -> int | None:
    """`value` as a page index, or None if it is not a non-negative integer."""
    if not _finite(value) or value != int(value) or value < 0:
        return None
    return int(value)


def _ink_points(value, least: int, most: int = MAX_INK_POINTS) -> list[tuple[float, float]] | None:
    """`value` as a list of `least`..`most` finite `(x, y)` points, or None
    if it is anything else. What a stroke from the bridge must be before any
    of it is written into a PDF."""
    if not isinstance(value, (list, tuple)) or not least <= len(value) <= most:
        return None
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        if not all(_finite(c) for c in point):
            return None
        points.append((float(point[0]), float(point[1])))
    return points


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
        # File tools: one job at a time, run as steps in the same worker
        # process (features/file_tools/). What jobs wrote this session may be
        # shown in their folder; a Home PDF's password is held only for the
        # job on that file.
        self._jobs = file_jobs.FileJobRunner(self._ocr, self._on_file_job_event)
        self._tool_outputs: set[str] = set()
        self._tool_outputs_lock = threading.Lock()
        self._tool_source: tuple[str, str | None] | None = None
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
        # Organise pages' draft while it is open (Milestone F2): every plan
        # since it opened, for its own Undo/Redo. The open document is
        # untouched until Done applies the current plan.
        self._organise: page_plan.PlanHistory | None = None

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
        "Show in folder"), or, for a folder a file tool made (Export as
        images), on that folder. Only for a path in the Recent list or one a
        file tool wrote this session, still on disk — the frontend can't use
        this to launch the file manager on an arbitrary location. Returns
        `{"error": str | None}`."""
        if not isinstance(path, str):
            return {"error": "That file is no longer in Recent."}
        tool_output = self._is_tool_output(path)
        if not tool_output and not any(e["path"] == path for e in settings.get_recent_files()):
            return {"error": "That file is no longer in Recent."}
        is_folder = tool_output and os.path.isdir(path)
        if not is_folder and not os.path.isfile(path):
            return {"error": "That file has been moved or deleted."}
        try:
            if is_folder:
                home_recent.reveal_in_file_manager(path, open_folder=True)
            else:
                home_recent.reveal_in_file_manager(path)
        except OSError:
            return {"error": "Couldn't open the folder."}
        return {"error": None}

    def browse_directory(self, path: str | None = None, kind: str = dialog_browser.PDF_FILTER) -> dict:
        """Lists a directory for the in-app Open/Save-As screens (Milestone
        8.7), replacing the native `create_file_dialog()` the router could
        not see. `path=None` starts at `dialog_browser.default_open_dir()` —
        the Open dialog's own default; the Save-As dialog always passes an
        explicit starting path from `get_save_dialog_start()` instead, since
        its default is the current document's folder, not the most recent
        file's. `kind` "convert" lists the images and e-books Convert to PDF
        takes instead of PDFs; "folder" (the file tools' folder picker) lists
        folders only."""
        folder = path or dialog_browser.default_open_dir()
        if kind in (dialog_browser.CONVERT_FILTER, dialog_browser.FOLDER_FILTER):
            return dialog_browser.list_directory(folder, kind)
        return dialog_browser.list_directory(folder)

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
        self._organise = None  # a draft of the PDF that was open
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
            # "highlight" or "page_changes": Undo's label ("Undo page changes").
            "undo_kind": self._doc.undo_kind,
            "redo_kind": self._doc.redo_kind,
            # Why Organise pages is unavailable (its menu item's tooltip), or None.
            "organise_blocked": self._organise_block_message(),
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
        """Mark a dragged run of characters in the active colour and style
        (highlight, underline or strike-through) at that style's opacity."""
        style = settings.get_highlight_style()
        self._doc.highlight_char_indices(
            page_index, start_idx, end_idx, settings.get_highlight_color(),
            style=style, opacity=self._opacity_for(style),
        )
        return self._state()

    @staticmethod
    def _opacity_for(style: str) -> float:
        return highlight_options.opacity_fraction(settings.get_highlight_opacity(style))

    def add_ink_stroke(self, page_index, points, straightened=None) -> dict:
        """Draw a freehand marker stroke in the active colour, the reader's
        width and the Highlight style's opacity (so a stroke beside a text
        highlight is the same colour), whichever style is chosen. `points` is the
        stroke as drawn (`[[x, y], ...]` in PDF points on the page as shown,
        like `get_page_chars`'s boxes); `straightened`, when the frontend
        straightened it on release, its two end points. Anything malformed
        draws nothing: this is a bridge boundary. Returns the state with
        `affected_page` (None when nothing was drawn) and the stroke's `id`."""
        state_page, xref = None, None
        drawn = _ink_points(points, 1)
        final = None if straightened is None else _ink_points(straightened, 2, 2)
        index = _page_index(page_index)
        valid = (
            self._doc.is_open
            and drawn is not None
            and (straightened is None or final is not None)
            and index is not None
            and index < self._doc.page_count
        )
        if valid:
            xref = self._doc.add_ink_stroke(
                index, drawn, final, settings.get_highlight_color(),
                width=float(settings.get_ink_width()),
                opacity=self._opacity_for(highlight_options.HIGHLIGHT),
            )
            state_page = index if xref is not None else None
        state = self._state()
        state["affected_page"] = state_page
        state["id"] = xref
        return state

    def get_highlight_options(self) -> dict:
        """Everything the highlight tool's cards show, and the numbers
        it works with, from `highlight_options` so they are the ones written
        into the PDF: the active style, each style's opacity and default (in
        whole percent), the slider's range, snap and tick step, and the
        freehand width (PDF points) with its presets."""
        return {
            "style": settings.get_highlight_style(),
            "styles": list(highlight_options.STYLES),
            "opacity": settings.get_highlight_opacities(),
            "default_opacity": dict(highlight_options.DEFAULT_OPACITY_PCT),
            "opacity_min": highlight_options.OPACITY_MIN_PCT,
            "opacity_max": highlight_options.OPACITY_MAX_PCT,
            "opacity_snap": highlight_options.OPACITY_SNAP_PCT,
            "opacity_tick_step": highlight_options.OPACITY_TICK_STEP_PCT,
            "ink_width": settings.get_ink_width(),
            "ink_widths": list(highlight_options.INK_WIDTH_PRESETS_PT),
        }

    # The setters below ignore a value the popover does not offer rather
    # than raising, as `set_highlight_color` does: this is a bridge boundary.
    # Each returns the options as now stored.

    def set_highlight_style(self, style) -> dict:
        if highlight_options.is_style(style):
            settings.set_highlight_style(style)
        return self.get_highlight_options()

    def set_highlight_opacity(self, style, pct) -> dict:
        if highlight_options.is_style(style) and highlight_options.is_opacity_pct(pct):
            settings.set_highlight_opacity(style, pct)
        return self.get_highlight_options()

    def set_ink_width(self, width) -> dict:
        if highlight_options.is_ink_width(width):
            settings.set_ink_width(int(width))
        return self.get_highlight_options()

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
        [{"id", "kind", "color", "rects", "text"}]}`, sizes and rects in PDF
        points like `get_page_chars`'s (an ink stroke's entry: see
        `_highlight_entry`). `color` is None for a colour another app wrote
        that is not in the palette; `text` is what the highlight covers, for
        the target's accessible name."""
        page_index = _page_index(page_index)
        if not self._doc.is_open or page_index is None or page_index >= self._doc.page_count:
            return {"width": 0, "height": 0, "highlights": []}
        width, height = self._doc.page_size_points(page_index)
        return {
            "width": width,
            "height": height,
            "highlights": [self._highlight_entry(page_index, h) for h in self._doc.highlights_on_page(page_index)],
        }

    def _highlight_entry(self, page_index: int, h: highlighter.Highlight) -> dict:
        """One `get_page_highlights` entry. An ink stroke also carries its
        `strokes` (as shown) and `width`, and has no `text`; its one rect is
        the box it paints, which the editor is placed under."""
        ink = isinstance(h.shape, highlighter.InkShape)
        entry = {
            "id": h.xref,
            "kind": "ink" if ink else "text",
            # "highlight", "underline" or "strikethrough"; "ink" for a stroke.
            "style": "ink" if ink else h.shape.style,
            "opacity": round(h.shape.opacity, 4),
            "color": h.color,
            "rects": [
                {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                for r in self._doc.as_shown(page_index, h.rects)
            ],
        }
        if entry["kind"] == "ink":
            entry["strokes"] = self._doc.shown_strokes(page_index, h.shape)
            entry["width"] = h.shape.width
            entry["text"] = None
        else:
            entry["text"] = self._doc.text_in_rects(page_index, h.rects)
        return entry

    def recolor_highlight(self, page_index: int, highlight_id: int, color: str) -> dict:
        changed = self._doc.set_highlight_color(int(page_index), int(highlight_id), color)
        state = self._state()
        state["affected_page"] = int(page_index) if changed else None
        return state

    def set_highlight_appearance(self, page_index, highlight_id, opacity_pct=None, width=None, gesture=None) -> dict:
        """Change how an existing highlight is drawn, from its editor: its
        opacity (whole percent, as the slider gives it) and, for a stroke,
        its width (one of the presets). `gesture` names the slider drag or
        click it came from, so one gesture is one undo step. A value the
        editor does not offer changes nothing (`affected_page` None)."""
        index = _page_index(page_index)
        xref = _page_index(highlight_id)
        valid = (
            self._doc.is_open
            and index is not None
            and xref is not None
            and index < self._doc.page_count
            and (opacity_pct is None or highlight_options.is_opacity_pct(opacity_pct))
            and (width is None or highlight_options.is_ink_width(width))
            and (gesture is None or (isinstance(gesture, str) and len(gesture) <= MAX_GESTURE_CHARS))
        )
        changed = valid and self._doc.set_highlight_appearance(
            index,
            xref,
            opacity=None if opacity_pct is None else highlight_options.opacity_fraction(opacity_pct),
            width=None if width is None else float(width),
            gesture=gesture,
        )
        state = self._state()
        state["affected_page"] = index if changed else None
        return state

    def remove_highlight(self, page_index: int, highlight_id: int) -> dict:
        removed = self._doc.remove_highlight(int(page_index), int(highlight_id))
        state = self._state()
        state["affected_page"] = int(page_index) if removed else None
        return state

    def undo_highlight(self) -> dict:
        """Undo the last change: a highlight's, or a set of page changes
        (then `pages_changed` is true and the page reloads its layout).
        Nothing while Organise is open: its draft is of the document as it
        is, and its own Undo is `organise_undo`."""
        if self._organise is not None:
            return self._after_history_step(None, None)
        kind = self._doc.undo_kind
        return self._after_history_step(kind, self._doc.undo_highlight())

    def redo_highlight(self) -> dict:
        if self._organise is not None:
            return self._after_history_step(None, None)
        kind = self._doc.redo_kind
        return self._after_history_step(kind, self._doc.redo_highlight())

    def _after_pages_changed(self) -> None:
        """The page list just changed under everything that names pages by
        index: the voice viewport is the old document's (the page sends a
        new one once it has laid out again), and the reading position moved."""
        self._voice_viewport = []
        self._voice_ocr_pages = []
        self._refresh_voice_vocabulary()
        self._remember_position()

    def _after_history_step(self, kind: str | None, affected_page: int | None) -> dict:
        pages_changed = kind == page_changes.PAGE_CHANGES and affected_page is not None
        if pages_changed:
            self._after_pages_changed()
        state = self._state()
        state["affected_page"] = affected_page
        state["pages_changed"] = pages_changed
        state["history_trimmed"] = self._doc.take_history_trimmed()
        return state

    # ------------------------------------------------------------------ #
    # Organise pages (Milestone F2)                                        #
    # ------------------------------------------------------------------ #

    def _organise_block_message(self) -> str | None:
        reason = self._doc.page_change_block()
        return None if reason is None else page_changes.BLOCK_MESSAGES[reason]

    def _organise_view(self, **extra) -> dict:
        history = self._organise
        return {
            "ok": True,
            "pages": [
                {"key": p.key, "source": p.source, "index": p.index, "turn": p.turn, "new": p.is_new}
                for p in history.current
            ],
            "can_undo": history.can_undo,
            "can_redo": history.can_redo,
            "steps": history.steps,
            **extra,
        }

    def organise_begin(self) -> dict:
        """Open Organise on the open document (or carry on with its draft,
        when the page reloads). `{ok: False, message}` when its pages can't
        be changed."""
        message = self._organise_block_message()
        if message is not None:
            return {"ok": False, "message": message}
        if self._organise is None:
            self._organise = page_plan.PlanHistory(page_plan.initial(self._doc.page_count))
        return self._organise_view(page_index=self._doc.page_index)

    def organise_action(self, kind: str, args: dict) -> dict:
        """One Organise action on the draft: `rotate` (`positions`,
        `degrees` ±90), `delete` (`positions`), `move` (`positions`, `gap`)
        or `shift` (`positions`, `step` ±1). Returns the new draft and the
        pages to select (where the moved pages went). `{ok: False, reason}`
        for one the plan refuses (`page_plan.EVERY_PAGE`, ...)."""
        if self._organise is None or not isinstance(args, dict):
            return {"ok": False, "reason": page_plan.BAD_POSITIONS}
        plan = self._organise.current
        positions = args.get("positions")
        try:
            if not isinstance(positions, list):
                raise page_plan.PlanError(page_plan.BAD_POSITIONS)
            new, selection = self._organise_step(plan, kind, positions, args)
        except page_plan.PlanError as exc:
            return {"ok": False, "reason": exc.reason}
        changed = self._organise.push(new)
        return self._organise_view(changed=changed, selection=list(selection))

    @staticmethod
    def _one_of(value, allowed: tuple[int, ...]) -> int:
        """`value` if it is exactly one of `allowed` (an int: not a bool,
        not 90.0); PlanError otherwise."""
        if type(value) is not int or value not in allowed:
            raise page_plan.PlanError(page_plan.BAD_POSITIONS)
        return value

    @classmethod
    def _organise_step(cls, plan: page_plan.Plan, kind: str, positions: list, args: dict):
        if kind == "rotate":
            degrees = cls._one_of(args.get("degrees"), (page_plan.QUARTER_TURN, -page_plan.QUARTER_TURN))
            return page_plan.rotate(plan, positions, degrees), page_plan.checked_positions(plan, positions)
        if kind == "delete":
            return page_plan.delete(plan, positions), ()
        if kind == "move":
            return page_plan.move(plan, positions, args.get("gap"))
        if kind == "shift":
            return page_plan.shift(plan, positions, cls._one_of(args.get("step"), (-1, 1)))
        raise page_plan.PlanError(page_plan.BAD_POSITIONS)

    def organise_undo(self) -> dict:
        if self._organise is None or self._organise.undo() is None:
            return {"ok": False}
        return self._organise_view()

    def organise_redo(self) -> dict:
        if self._organise is None or self._organise.redo() is None:
            return {"ok": False}
        return self._organise_view()

    def organise_thumbnail(self, source: str, index: int, width: int) -> dict:
        """A page of the draft, `width` pixels wide (clamped), as the page
        is now; the grid turns it by the plan's `turn` on screen."""
        index = _page_index(index)
        if not isinstance(source, str) or index is None or not _finite(width):
            return {"image": None, "width": 0, "height": 0}
        width = max(MIN_THUMBNAIL_PX, min(int(width), MAX_THUMBNAIL_PX))
        pix = self._doc.render_plan_thumbnail(source, index, width)
        if pix is None:
            return {"image": None, "width": 0, "height": 0}
        return {"image": _pixmap_to_png_b64(pix), "width": pix.width, "height": pix.height}

    def organise_done(self) -> dict:
        """Leave Organise, applying the draft to the open document as one
        undoable edit. The reader's state comes back with `pages_changed`
        (whether anything changed) and `history_trimmed` (older changes
        were dropped). If applying fails, the document is as it was, the
        draft stays open and `error` says so."""
        history = self._organise
        if history is None or not self._doc.is_open:
            self._organise = None
            return {**self._state(), "pages_changed": False, "history_trimmed": False}
        try:
            applied = self._doc.apply_page_plan(history.current)
        except Exception:
            log.exception("Applying page changes failed")
            return {**self._state(), "pages_changed": False, "history_trimmed": False, "error": PAGE_CHANGES_FAILED}
        self._organise = None
        self._doc.drop_page_sources()
        if applied:
            self._after_pages_changed()
        return {
            **self._state(),
            "pages_changed": applied,
            "history_trimmed": self._doc.take_history_trimmed(),
        }

    def highlight_by_voice(
        self, query: str, sentence: bool = False, color: str | None = None, style: str | None = None
    ) -> dict:
        """Matches a spoken phrase against the viewport `start_listening` was
        last called with, and writes the highlight if one is found.

        `color` is a one-off ("highlight <words> in green"): it colours this
        highlight only and leaves the active colour as it was. `style` is the
        command's own ("underline <words>" is an underline whichever style
        the popover has), at that style's opacity; None is a highlight.

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
        if not highlight_options.is_style(style):
            style = highlight_options.HIGHLIGHT
        self._doc.highlight_word_indices(
            match.page_index, start_idx, end_idx, color, style=style, opacity=self._opacity_for(style)
        )

        state = self._state()
        state["voice_highlight"] = {
            "matched": True,
            "query": query,
            "color": color,
            "style": style,
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
        return tool_naming.free_file_name(self._doc.path or "untitled.pdf", tool_naming.HIGHLIGHTED)

    # ------------------------------------------------------------------ #
    # File tools (docs/PRD.md § "Post-v1: file tools")                     #
    # ------------------------------------------------------------------ #

    def get_file_tool_menu(self) -> dict:
        """The reader's More menu's second line under Make searchable: how
        many pages have no text. Worked out from each page's own text layer
        the first time it is asked, then cached with the document; never
        runs OCR."""
        if not self._doc.is_open:
            return {"textless": 0, "pages": 0}
        return {"textless": len(self._doc.textless_pages()), "pages": self._doc.page_count}

    def file_tool_info(self, tool: str, source: str, password: str | None = None) -> dict:
        """What a tool's Set up state shows about its PDF: `source` is
        `"open"` (the reader's document, as it is on screen) or a path from
        Home. A protected PDF from Home comes back as `{"ok": False,
        "reason": "password"}` (or "wrong_password") until called with the
        right password, which is then held here, in memory only, for that
        file's job; a missing one as `reason` "missing"."""
        if tool not in (tool_messages.COMPRESS, tool_messages.SEARCHABLE, tool_messages.EXPORT):
            return {"ok": False, "error": tool_messages.message(tool_messages.WORKER_FAILED, tool)}
        if password is not None and not isinstance(password, str):
            password = None
        if source == TOOL_SOURCE_OPEN:
            if not self._doc.is_open:
                return {"ok": False, "error": open_errors.MESSAGES[open_errors.MISSING], "reason": "missing"}
            path = self._doc.path
            info = self._doc.describe_for_tools(self._doc.page_index)
            info.update(
                unsaved=self._doc.unsaved_highlight_count(),
                dirty=self._doc.is_dirty,
                current_page=self._doc.page_index,
                current_textless=self._doc.page_index in info["textless"],
            )
            info["size"] = _file_size(path)
        else:
            outcome = self._describe_home_source(source, password)
            if not outcome.get("ok", True):
                return outcome
            path, info = source, outcome
            info.update(unsaved=0, dirty=False, current_page=None, current_textless=False)
        info["textless_pages"] = [i + 1 for i in info["textless"]]
        info["textless"] = len(info["textless"])
        folder = os.path.dirname(path)
        info.update(
            ok=True,
            name=os.path.basename(path),
            folder=folder,
            # The destination block's start: always beside the original.
            output={
                "folder": folder,
                "stem": tool_destinations.suggested_stem(tool, path, folder),
                "extension": tool_destinations.extension(tool),
            },
            prefs=settings.get_file_tool_prefs(),
        )
        return info

    def _describe_home_source(self, path, password: str | None) -> dict:
        if not isinstance(path, str) or not os.path.isfile(path):
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.MISSING], "reason": "missing"}
        try:
            info = self._ocr.call(tool_engine.describe_path, (path, password), timeout=DESCRIBE_TIMEOUT_S).result()
        except tool_engine.SourceError:
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.NOT_A_PDF], "reason": "not_a_pdf"}
        except Exception:
            log.exception("Couldn't describe a PDF for a file tool")
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.UNKNOWN], "reason": "unknown"}
        if info.get("locked"):
            reason = open_errors.PASSWORD if password is None else open_errors.WRONG_PASSWORD
            return {"ok": False, "reason": reason, "error": open_errors.MESSAGES[reason], "name": os.path.basename(path)}
        # Held for this file's job only; replaced by the next file chosen.
        self._tool_source = (path, password) if info.get("needs_pass") else None
        return info

    # ---- Where a tool saves (the dialogs' Name and "Save in" rows) ----------

    def get_save_places(self, origin_folder: str) -> dict:
        """The "Save in" menu, asked each time it opens: the origin folder,
        RECENT (folders that no longer exist are forgotten first) and ON
        THIS PC (Desktop, Documents, Downloads, as the OS has them)."""
        if not isinstance(origin_folder, str) or not origin_folder:
            origin_folder = ""
        return tool_destinations.menu(origin_folder)

    def remember_output_folder(self, folder: str) -> dict:
        """A folder picked from RECENT or with "Choose another folder…": to
        the top of RECENT. `{"ok": False}` for one that isn't there."""
        if not isinstance(folder, str) or not os.path.isabs(folder) or not os.path.isdir(folder):
            return {"ok": False}
        tool_destinations.remember(folder)
        return {"ok": True}

    def check_output_folder(self, folder: str) -> dict:
        """Whether a tool could save in `folder` (it is there, and Lector may
        write in it), asked as soon as a folder is chosen."""
        if tool_destinations.is_writable_folder(folder):
            return {"ok": True, "error": None}
        return {"ok": False, "error": tool_destinations.FOLDER_UNUSABLE}

    def suggest_tool_name(self, tool: str, source: str, folder: str) -> dict:
        """The Name field's suggestion in `folder`: "Report (compressed)",
        or the first free "… (2)" there. `source` is "open", a path, or
        Convert's first file."""
        origin = self._tool_origin(source)
        if tool not in tool_messages.TOOLS or origin is None:
            return {"stem": "", "extension": ""}
        if not isinstance(folder, str) or not os.path.isdir(folder):
            folder = os.path.dirname(origin)
        return {
            "stem": tool_destinations.suggested_stem(tool, origin, folder),
            "extension": tool_destinations.extension(tool),
        }

    def check_tool_destination(self, tool: str, source: str, folder: str, name: str, inputs=None) -> dict:
        """Checked as the primary button is pressed, before the job starts:
        `{"ok": True, "path", "name", "folder", "exists"}` (a file already
        there is asked about first), or `{"ok": False, "field": "name" |
        "folder", "error": sentence}` for the line under that row."""
        if tool not in tool_messages.TOOLS:
            return {"ok": False, "field": tool_destinations.NAME_FIELD, "error": tool_destinations.NAME_UNUSABLE}
        try:
            target = tool_destinations.check(tool, folder, name, self._tool_originals(source, inputs))
        except tool_destinations.DestinationError as exc:
            return {"ok": False, "field": exc.field, "error": exc.message}
        return {
            "ok": True,
            "path": target.path,
            "name": os.path.basename(target.path),
            "folder": os.path.dirname(target.path),
            "exists": target.exists,
        }

    def _tool_origin(self, source) -> str | None:
        """The PDF (or Convert's first file) a tool's "beside" means."""
        if source == TOOL_SOURCE_OPEN:
            return self._doc.path if self._doc.is_open else None
        return source if isinstance(source, str) and os.path.isabs(source) else None

    def _tool_originals(self, source, inputs=None) -> list[str]:
        """What a tool's output may never be: its PDF, or every Convert input."""
        if isinstance(inputs, list):
            return [p for p in inputs if isinstance(p, str)]
        origin = self._tool_origin(source)
        return [origin] if origin else []

    def parse_export_range(self, text: str, page_count: int) -> dict:
        """The Export dialog's "Pages" field: `{"pages": [1-based...]}`, or
        `{"error": sentence}` for the line under the field."""
        count = _page_index(page_count)
        if not count:
            return {"error": "This PDF has no pages."}
        try:
            return {"pages": tool_naming.parse_page_ranges(text, count)}
        except tool_naming.RangeError as exc:
            return {"error": exc.message}

    def describe_convert_inputs(self, paths: list[str]) -> list[dict]:
        """Each file Convert to PDF was given: its type, size, page count
        (an e-book's after it is laid out) and thumbnail, or `{"error"}`
        naming why it can't be used."""
        if not isinstance(paths, list):
            return []
        described = []
        for path in paths[:MAX_CONVERT_INPUTS]:
            entry = {"path": path, "name": os.path.basename(path) if isinstance(path, str) else ""}
            if not isinstance(path, str) or not os.path.isfile(path):
                entry["error"] = f"Couldn't find {entry['name'] or 'that file'}."
            elif not tool_formats.is_convertible(path):
                entry["error"] = f"{entry['name']} can't be made into a PDF."
            else:
                try:
                    entry.update(
                        self._ocr.call(tool_engine.convert_describe, (path,), timeout=DESCRIBE_TIMEOUT_S).result()
                    )
                except Exception:
                    entry["error"] = f"{entry['name']} couldn't be read."
            described.append(entry)
        return described

    def start_file_job(self, tool: str, source: str, options: dict) -> dict:
        """Start a tool's job. `source` is `"open"` or a path (Convert: the
        first input; `options["inputs"]` holds them all). Returns `{"ok":
        True, "id"}`, or `{"ok": False, "busy": True}` when one is running,
        or `{"ok": False, "error": sentence}`. Progress, and the outcome,
        arrive as `lector:filejob` events."""
        if self._jobs.is_running():
            return {"ok": False, "busy": True, "error": "Another file job is running."}
        try:
            run = self._build_job(tool, source, options if isinstance(options, dict) else {})
        except _BadToolRequest as exc:
            return {"ok": False, "error": tool_messages.message(exc.reason, tool)}
        job_id = self._jobs.start(tool, run)
        if job_id is None:
            return {"ok": False, "busy": True, "error": "Another file job is running."}
        return {"ok": True, "id": job_id}

    def _build_job(self, tool: str, source: str, options: dict):
        """Check what the page asked for and return the job's function. Never
        trusts the page: every path, choice and page number is checked."""
        if tool == tool_messages.CONVERT:
            return self._build_convert(options)
        if tool not in (tool_messages.COMPRESS, tool_messages.SEARCHABLE, tool_messages.EXPORT):
            raise _BadToolRequest(tool_messages.WORKER_FAILED)
        src = self._tool_job_source(source)
        dest, where = self._checked_dest(tool, options, [src.path], tool_destinations.BESIDE_ORIGINAL)
        page_count = self._source_page_count(source, src)
        if tool == tool_messages.COMPRESS:
            level = options.get("level")
            if level not in tool_choices.LEVELS:
                raise _BadToolRequest(tool_messages.WORKER_FAILED)
            settings.set_file_tool_prefs(compress_level=level)
            return _with_place(lambda ctx: file_tools.compress(ctx, src, dest, level), where)
        if tool == tool_messages.EXPORT:
            fmt, ppi = options.get("format"), options.get("ppi")
            if fmt not in tool_choices.EXPORT_FORMATS or isinstance(ppi, bool) or ppi not in tool_choices.EXPORT_PPI:
                raise _BadToolRequest(tool_messages.WORKER_FAILED)
            pages = _checked_pages(options.get("pages"), page_count)
            settings.set_file_tool_prefs(export_format=fmt, export_ppi=ppi)
            return _with_place(lambda ctx: file_tools.export_images(ctx, src, dest, fmt, ppi, pages), where)
        pages = _checked_pages(options.get("pages"), page_count)
        known = self._known_words if source == TOOL_SOURCE_OPEN else file_tools.cached_words
        return _with_place(lambda ctx: file_tools.make_searchable(ctx, src, dest, pages, known), where)

    def _build_convert(self, options: dict):
        inputs = options.get("inputs")
        if not isinstance(inputs, list) or not 0 < len(inputs) <= MAX_CONVERT_INPUTS:
            raise _BadToolRequest(tool_messages.INPUT_UNREADABLE)
        for path in inputs:
            if not isinstance(path, str) or not os.path.isfile(path) or not tool_formats.is_convertible(path):
                raise _BadToolRequest(tool_messages.INPUT_UNREADABLE)
        page_size = options.get("page_size")
        if page_size not in tool_formats.PAGE_SIZE_CHOICES:
            raise _BadToolRequest(tool_messages.WORKER_FAILED)
        dest, where = self._checked_dest(tool_messages.CONVERT, options, inputs, tool_destinations.BESIDE_FIRST_FILE)
        return _with_place(lambda ctx: file_tools.convert(ctx, list(inputs), dest, page_size), where)

    def _tool_job_source(self, source) -> "file_tools.Source":
        if source == TOOL_SOURCE_OPEN:
            if not self._doc.is_open:
                raise _BadToolRequest(tool_messages.SOURCE_UNREADABLE)
            data, password = self._doc.snapshot()
            return file_tools.Source(self._doc.path, data, password)
        if not isinstance(source, str) or not os.path.isfile(source):
            raise _BadToolRequest(tool_messages.SOURCE_UNREADABLE)
        held = self._tool_source
        password = held[1] if held and safe_write.same_file(held[0], source) else None
        return file_tools.Source(source, None, password)

    def _source_page_count(self, source: str, src: "file_tools.Source") -> int:
        if source == TOOL_SOURCE_OPEN:
            return self._doc.page_count
        info = self._describe_home_source(src.path, src.password)
        if not info.get("ok", True) or "pages" not in info:
            raise _BadToolRequest(tool_messages.SOURCE_UNREADABLE)
        return info["pages"]

    @staticmethod
    def _checked_dest(tool: str, options: dict, sources: list[str], origin_phrase: str) -> tuple[str, str]:
        """The output path for the folder and name the page chose, and how
        the result says where it went. The page sends the folder and the
        bare name, never a path: Python adds the extension and checks the
        name (no `\\ / : * ? " < > |`), that the folder is there and
        writable, and that the output isn't one of the sources (the tools
        never write over an original)."""
        try:
            target = tool_destinations.check(tool, options.get("folder"), options.get("name"), sources)
        except tool_destinations.DestinationError as exc:
            raise _BadToolRequest(exc.reason) from None
        folder = os.path.dirname(target.path)
        origin = os.path.dirname(sources[0]) if sources else ""
        return target.path, tool_destinations.where_phrase(folder, origin, origin_phrase)

    def _known_words(self, index: int, key: str) -> list | None:
        """Make searchable from the reader: what highlighting already read on
        the page (in memory, or cached), else the cache under the job's key."""
        return self._doc.ocr_words(index) or file_tools.cached_words(index, key)

    def cancel_file_job(self) -> dict:
        """Stop the running job at the next page (a long step at once); it
        writes nothing. `{"cancelled": bool}`."""
        return {"cancelled": self._jobs.cancel()}

    def get_file_job(self) -> dict | None:
        """The running job's latest progress, or None: a page that has just
        loaded shows the footer pill or the dialog from it."""
        return self._jobs.current()

    def _on_file_job_event(self, event: dict) -> None:
        """A job's progress or outcome, from its own thread, to the page.
        What a job wrote may then be shown in its folder or opened."""
        result = event.get("result")
        if event.get("state") == file_jobs.DONE and isinstance(result, dict) and result.get("path"):
            with self._tool_outputs_lock:
                self._tool_outputs.add(os.path.normcase(os.path.abspath(result["path"])))
            result.update(name=os.path.basename(result["path"]), folder=os.path.dirname(result["path"]))
        if event.get("state") != file_jobs.WORKING:
            self._tool_source = None
        self._dispatch_page_event("lector:filejob", event)

    def _is_tool_output(self, path: str) -> bool:
        with self._tool_outputs_lock:
            return os.path.normcase(os.path.abspath(path)) in self._tool_outputs

    def show_open_document_in_folder(self) -> dict:
        """The reader's More menu's "Show in folder", for the open PDF."""
        if not self._doc.is_open or not os.path.isfile(self._doc.path or ""):
            return {"error": "That file has been moved or deleted."}
        try:
            home_recent.reveal_in_file_manager(self._doc.path)
        except OSError:
            return {"error": "Couldn't open the folder."}
        return {"error": None}

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
        outcome = file_drop.with_convertible(
            file_drop.pdf_from_drop(event), file_drop.convertible_from_drop(event)
        )
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
        if not self._closing_confirmed and self._jobs.is_running():
            # A file job first: "Stop it and close?" Yes cancels it, waits for
            # it to clean up (it writes nothing), and closes again, which
            # brings the unsaved-highlights question below if there is one.
            try:
                window = webview.windows[0]
            except IndexError:
                window = None
            if window is not None:

                def _on_job_answer(stop) -> None:
                    if not stop:
                        return
                    self._jobs.cancel()
                    if not self._jobs.wait(JOB_STOP_WAIT_S):
                        log.warning("A cancelled file job had not stopped when the app closed")
                    window.destroy()

                window.evaluate_js("confirmStopFileJob()", callback=_on_job_answer)
                return False

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
