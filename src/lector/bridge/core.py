"""What the bridge's controllers share (docs/ARCHITECTURE.md § "Bridge layout").

One `BridgeCore` per `Api`: the open document, the services that outlive any
one bridge call, and the few things more than one controller does with them.
Controllers read all of it at call time and never keep a copy, so whatever
replaces a piece (opening a PDF, a test's stand-in) is what every one of
them sees next.
"""
import json
import logging
import threading
from pathlib import Path

import webview

from lector.bridge.vocabulary import VoiceScope
from lector.features.file_tools import estimates as compress_estimates
from lector.features.file_tools import jobs as file_jobs
from lector.features.file_tools import scratch as compress_scratch
from lector.features.reading import ocr, page_changes
from lector.features.reading.document import PdfDocument
from lector.features.settings import store as settings
from lector.features.voice.engine import VoiceEngine
from lector.shared import window_title

# Every controller logs here, as all of this did when it was api.py.
log = logging.getLogger("lector.api")


class BridgeCore:
    def __init__(self, log_dir: Path | None, on_voice_result, on_ocr_finished, on_file_job_event):
        """`log_dir` is the app's log folder (`shared/app_log.py`); the OCR
        worker writes its crash log there. None, as in tests, writes none.
        The three callbacks are where the voice engine, the OCR worker and
        the file-job runner report, each from its own thread."""
        self.doc = PdfDocument()
        self.title = ""
        self.path = ""
        # Which reading layout the frontend is currently showing. The frontend
        # owns the DOM for it, but the value lives here too so it can be
        # persisted with the reading position and handed back on reopen.
        self.layout_mode = settings.BOOK
        # Last (page_index, layout_mode) actually written to settings.json, so
        # that page turns which don't change anything — and the flurry of
        # goto_page calls strip layout makes while scrolling — don't each cost
        # a file write.
        self.persisted_position = None
        # Push-to-talk speech recognition (Milestone 5). Constructed eagerly
        # but inert — the Vosk model is only loaded on the first call that
        # needs it, so a missing model costs nothing until voice is used.
        self.voice = VoiceEngine()
        self.voice.subscribe(on_voice_result)
        self.vocabulary = VoiceScope(self)
        # Reads scanned pages in a worker process, one at a time, on demand.
        self.ocr = ocr.OcrRunner(
            on_ocr_finished,
            crash_log=None if log_dir is None else log_dir / "ocr-worker.log",
        )
        # File tools: one job at a time, run as steps in the same worker
        # process (features/file_tools/).
        self.jobs = file_jobs.FileJobRunner(self.ocr, on_file_job_event)
        # Compress's temporary results (listed in a manifest beside the
        # settings, so a crash's leftovers go at the next start) and its
        # estimates, which the dialog owns: not file jobs.
        self.scratch = compress_scratch.ScratchFiles(settings.data_path(compress_scratch.MANIFEST_NAME))
        self.estimates = compress_estimates.CompressEstimates(
            self.ocr, self.scratch, lambda event: self.dispatch_page_event("lector:compressestimate", event)
        )
        # Organise pages' draft while it is open (Milestone F2): every plan
        # since it opened, for its own Undo/Redo. The open document is
        # untouched until Done applies the current plan.
        self.organise = None
        # Whether the page on screen is the reader (set on every page load,
        # `WindowController.on_page_loaded`): only there does the window's
        # title name the document. After "Don't save" on the way to the
        # Library the PDF is still open here, dirty, but Home says "Lector".
        self.reader_showing = False
        # Opened only to organise its pages (Home's Tools page, or a Recent
        # or Favorites menu): not in Recent and no reading position written
        # until Organise's Done makes it a real open (`adopt_organise_open`);
        # Cancel closes it again (`close_document`), leaving nothing behind.
        self.opened_for_organise = False
        # The title last given to the window, so it is set only on a change.
        self._window_title = window_title.APP_TITLE
        self._title_lock = threading.Lock()

    def state(self) -> dict:
        # Every change the unsaved flag follows (a highlight, an undo, a
        # save, Organise's Done) answers with this, so the title follows too.
        self.sync_window_title()
        if not self.doc.is_open:
            return {"is_open": False}
        highlight_counts = self.doc.highlight_counts()
        return {
            "is_open": True,
            "title": self.title,
            "page_index": self.doc.page_index,
            "page_count": self.doc.page_count,
            "zoom": self.doc.zoom,
            "zoom_pct": round(self.doc.zoom * 100),
            "zoom_min_pct": round(self.doc.MIN_ZOOM * 100),
            "zoom_max_pct": round(self.doc.MAX_ZOOM * 100),
            "is_dirty": self.doc.is_dirty,
            "can_undo": self.doc.can_undo,
            "can_redo": self.doc.can_redo,
            # "highlight" or "page_changes": Undo's label ("Undo page changes").
            "undo_kind": self.doc.undo_kind,
            "redo_kind": self.doc.redo_kind,
            # Why Organise pages is unavailable (its menu item's tooltip), or None.
            "organise_blocked": self.organise_block_message(),
            "highlight_count_page": highlight_counts.get(self.doc.page_index, 0),
            "highlight_count_total": sum(highlight_counts.values()),
            # The progress bar's markers: every page with a highlight on it,
            # saved or not, so undo and redo move them as they happen.
            "highlighted_pages": sorted(highlight_counts),
            "highlight_color": settings.get_highlight_color(),
            "layout_mode": self.layout_mode,
        }

    def sync_window_title(self) -> None:
        """Give the window the title for what is on screen
        (`window_title.compose`: "*Report – Lector" while unsaved, "Lector"
        off the reader). Only on a change; quietly nothing with no window
        (tests, teardown), and a failure is logged, never raised: the title
        is cosmetic and must not fail the call that changed the document."""
        on_screen = self.title if self.reader_showing and self.doc.is_open else None
        wanted = window_title.compose(on_screen, self.doc.is_open and self.doc.is_dirty)
        with self._title_lock:
            if wanted == self._window_title:
                return
            try:
                window = webview.windows[0]
            except IndexError:
                return
            # pywebview's set_title waits up to 20s for the window to be
            # shown; before that (or for a window that never is) the title
            # is left for the `shown` handler to set (`__main__.py`).
            if not window.events.shown.is_set():
                return
            try:
                window.set_title(wanted)
            except Exception:
                log.debug("Could not set the window title", exc_info=True)
                return
            self._window_title = wanted

    def adopt_organise_open(self) -> None:
        """A PDF opened only for Organise pages becomes an ordinary open one
        (Done, or a PDF whose pages can't be changed, which the reader shows):
        into Recent, with its reading position. Nothing for any other."""
        if not self.opened_for_organise:
            return
        self.opened_for_organise = False
        if self.path and self.doc.is_open:
            settings.add_recent_file(self.path)
            self.persisted_position = None
            self.remember_position()

    def close_document(self) -> None:
        """Let go of the open PDF (its file handle, Insert's files, any
        draft): Home leaves a PDF open in Python, and a tool or a cancelled
        Organise must not keep it locked."""
        self.organise = None
        self.doc.drop_page_sources()
        if self.doc.is_open:
            self.doc.close()
        self.title = ""
        self.path = ""
        self.persisted_position = None
        self.opened_for_organise = False

    def remember_position(self) -> None:
        """Persist the current page/layout/zoom, but only when it actually moved.
        Never for a PDF opened only to organise it (`opened_for_organise`)."""
        if not self.path or not self.doc.is_open or self.opened_for_organise:
            return
        current = (self.doc.page_index, self.layout_mode, self.doc.zoom)
        if current == self.persisted_position:
            return
        settings.set_document_position(self.path, current[0], current[1], current[2])
        self.persisted_position = current

    def organise_block_message(self) -> str | None:
        reason = self.doc.page_change_block()
        return None if reason is None else page_changes.BLOCK_MESSAGES[reason]

    def after_pages_changed(self) -> None:
        """The page list just changed under everything that names pages by
        index: the voice viewport is the old document's (the page sends a
        new one once it has laid out again), and the reading position moved.
        The change itself has already happened, so a failure here is logged
        and never reported as the change failing."""
        self.vocabulary.viewport = []
        self.vocabulary.ocr_pages = []
        try:
            self.vocabulary.refresh()
            self.remember_position()
        except Exception:
            log.exception("Couldn't refresh voice or the reading position after a page change")

    def dispatch_page_event(self, name: str, detail: dict) -> None:
        """Push a `CustomEvent` to the current page, the same direction
        voice results use. Quietly does nothing with no window, which is
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


class Controller:
    """A bridge controller: the core's shared pieces under the names the
    bridge code has always used for them, each read from the core when used."""

    def __init__(self, core: BridgeCore):
        self._core = core

    @property
    def _doc(self) -> PdfDocument:
        return self._core.doc

    @property
    def _ocr(self):
        return self._core.ocr

    @property
    def _voice(self) -> VoiceEngine:
        return self._core.voice

    @property
    def _jobs(self):
        return self._core.jobs

    @property
    def _scratch(self):
        return self._core.scratch

    @property
    def _estimates(self):
        return self._core.estimates

    @property
    def _vocabulary(self) -> VoiceScope:
        return self._core.vocabulary

    @property
    def _organise(self):
        return self._core.organise

    @_organise.setter
    def _organise(self, history) -> None:
        self._core.organise = history

    def _state(self) -> dict:
        return self._core.state()

    def _remember_position(self) -> None:
        self._core.remember_position()

    def _after_pages_changed(self) -> None:
        self._core.after_pages_changed()

    def _dispatch_page_event(self, name: str, detail: dict) -> None:
        self._core.dispatch_page_event(name, detail)
