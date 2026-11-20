"""The app window: files dropped on it or handed over at launch, failures
off-screen, closing it (the unsaved-work gatekeeper), and shutting down the
workers (docs/ARCHITECTURE.md § "App-close gatekeeper")."""
import webview
from webview.dom import DOMEventHandler

from lector.bridge.core import Controller, log
from lector.features.home import file_drop

# How long closing waits for a cancelled job to clean up before going on.
JOB_STOP_WAIT_S = 10
# How long quitting waits for a stopped Compress estimate to let go of its file.
ESTIMATES_STOP_WAIT_S = 5


class WindowController(Controller):
    def __init__(self, core, shutdown_voice, drop_handler):
        """`shutdown_voice` and `drop_handler` are the bridge object's own
        `shutdown_voice` and `_on_file_drop`, so the window's drop binding
        and what a close shuts down are `Api`'s methods, as before."""
        super().__init__(core)
        self._shutdown_voice = shutdown_voice
        self._drop_handler = drop_handler
        # Set once `handle_window_closing` has let a close through, so the
        # `window.destroy()` it issues doesn't re-run the dirty check.
        self.closing_confirmed = False

    def shutdown_ocr(self) -> None:
        """Stop the OCR worker process, and delete Compress's temporary
        files. Called once the window has closed."""
        # In this order: an estimate still being written by the worker would
        # otherwise be held open (Windows refuses the delete) or written again
        # after it. The worker is ended first, then whatever is left goes.
        self._estimates.stop(discard=True)
        self._ocr.shutdown()
        self._estimates.wait(ESTIMATES_STOP_WAIT_S)
        self._scratch.discard_all()

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
            DOMEventHandler(self._drop_handler, prevent_default=True, stop_propagation=True),
        )

    def on_file_drop(self, event: dict) -> None:
        """Tell the page which PDF was dropped, or why it can't be opened.

        Runs on a pywebview worker thread. Does not open the file: the page
        decides what opening means on its screen (Reading asks about unsaved
        highlights first), so this only reports.
        """
        outcome = file_drop.with_combinable(
            file_drop.with_convertible(file_drop.pdf_from_drop(event), file_drop.convertible_from_drop(event)),
            file_drop.combinable_from_drop(event),
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
        if not self.closing_confirmed and self._jobs.is_running():
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

        # An Organise draft with changes in it is unsaved too: the page
        # applies it first, then asks the usual question (reading-save.js).
        draft_pending = self._organise is not None and self._organise.steps > 0
        unsaved = self._doc.is_open and (self._doc.is_dirty or draft_pending)
        if self.closing_confirmed or not unsaved:
            self._shutdown_voice()
            return None

        try:
            window = webview.windows[0]
        except IndexError:
            self._shutdown_voice()
            return None

        def _on_resolved(should_close) -> None:
            if not should_close:
                return
            self.closing_confirmed = True
            self._shutdown_voice()
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
