"""Running a file tool's job from the main process, one at a time.

A job is a function (`tools.py`) run on its own thread, `lector-file-job`,
that does its work as short calls into the worker process (`JobContext.step`)
and says how far it has got (`JobContext.progress`). Between steps it checks
whether the reader cancelled; a step that is one long MuPDF call (Compress's
image pass, writing a big file) is stopped by ending the worker process.

Progress goes to `on_event` as plain dicts, which `api.py` pushes to the page
as `lector:filejob`. Output only reaches its final name through
`write_file`/`write_folder`, which write beside it, check, and move it into
place in one step, so a cancelled or failed job leaves nothing behind.

No PyMuPDF here: the worker does all of that.
"""
import itertools
import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Callable

from lector.features.file_tools import messages, naming
from lector.features.reading import ocr, safe_write

log = logging.getLogger(__name__)

# How long one step may run before the worker is taken to have hung. A page
# step is short; a whole-document one (open, the image pass, write) on a big
# PDF can take minutes.
STEP_TIMEOUT_S = 180
LONG_STEP_TIMEOUT_S = 1800

EXPORT_TEMP_PREFIX = ".lector-export-"

WORKING = "working"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

_ids = itertools.count(1)


class JobCancelled(BaseException):
    """The reader cancelled. A BaseException, like asyncio's CancelledError,
    so no `except Exception` on the way out (`safe_write.write_atomically`
    has one) mistakes it for a failure; `finally` blocks still clean up."""


class JobFailed(Exception):
    """The job can't go on, for one of `messages`' reasons."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Carried(BaseException):
    """A JobFailed passed through `write_atomically` untouched (it would
    otherwise be classified as a write error)."""

    def __init__(self, failure: JobFailed):
        super().__init__()
        self.failure = failure


class JobContext:
    """What a running job's function is handed."""

    def __init__(self, runner: "FileJobRunner", job_id: str, tool: str):
        self._runner = runner
        self.job_id = job_id
        self.tool = tool
        # Set when a step's worker was ended or lost: what the job had open
        # there went with it, so there is nothing left to close.
        self.worker_gone = False

    @property
    def cancelled(self) -> bool:
        return self._runner._cancel.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def progress(self, phase: str, current: int = 0, total: int = 0, unit: str = "") -> None:
        self._runner._emit(
            {
                "id": self.job_id,
                "tool": self.tool,
                "state": WORKING,
                "phase": phase,
                "current": current,
                "total": total,
                "unit": unit,
            }
        )

    def step(self, fn: Callable, *args, long: bool = False):
        """`fn(*args)` in the worker, after checking for a cancel. A `long`
        step is stopped mid-way by a cancel (its worker is ended); any other
        runs to its end first. Raises JobCancelled, JobFailed, or `fn`'s own
        exception."""
        self.check()
        future = self._runner._worker.call(
            fn,
            args,
            timeout=LONG_STEP_TIMEOUT_S if long else STEP_TIMEOUT_S,
            abort=self._runner._cancel if long else None,
        )
        try:
            return future.result()
        except ocr.CallAborted:
            self.worker_gone = True
            raise JobCancelled() from None
        except ocr.WorkerLost:
            self.worker_gone = True
            raise JobFailed(messages.WORKER_FAILED) from None

    def release(self, close: Callable) -> None:
        """Let the worker drop what this job has open there, unless the
        worker is gone (then it went with it)."""
        if self.worker_gone:
            return
        try:
            self._runner._worker.call(close, (self.job_id,), timeout=STEP_TIMEOUT_S).result()
        except Exception:
            log.warning("Could not release a file job in the worker")


class FileJobRunner:
    """Runs one file job at a time and reports on it through `on_event`."""

    def __init__(self, worker: ocr.OcrRunner, on_event: Callable[[dict], None]):
        self._worker = worker
        self._on_event = on_event
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: dict | None = None

    def start(self, tool: str, run: Callable[[JobContext], dict]) -> str | None:
        """Start `run` as a job; its id, or None when one is running."""
        with self._lock:
            if self._thread is not None:
                return None
            job_id = f"job-{next(_ids)}"
            self._cancel.clear()
            ctx = JobContext(self, job_id, tool)
            self._latest = _event(job_id, tool, WORKING)
            self._thread = threading.Thread(
                target=self._main, args=(ctx, run), name="lector-file-job", daemon=True
            )
            self._thread.start()
        return job_id

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None

    def current(self) -> dict | None:
        """The running job's latest state, for a page that has just loaded."""
        with self._lock:
            return dict(self._latest) if self._thread is not None and self._latest else None

    def cancel(self) -> bool:
        """Ask the running job to stop. False if none is running."""
        with self._lock:
            if self._thread is None:
                return False
            self._cancel.set()
            return True

    def wait(self, timeout: float) -> bool:
        """Wait for the running job to finish; True if none is left."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.is_running()

    def _main(self, ctx: JobContext, run: Callable[[JobContext], dict]) -> None:
        final = _event(ctx.job_id, ctx.tool, DONE)
        try:
            final["result"] = run(ctx)
        except JobCancelled:
            final["state"] = CANCELLED
        except JobFailed as exc:
            final.update(state=FAILED, error=messages.message(exc.reason, ctx.tool))
        except Exception:
            # Type and frames only (app_log's formatter): never the text.
            log.exception("A file job failed (%s)", ctx.tool)
            final.update(state=FAILED, error=messages.message(messages.WORKER_FAILED, ctx.tool))
        with self._lock:
            self._thread = None
            self._latest = None
        self._emit(final)

    def _emit(self, event: dict) -> None:
        if event.get("state") == WORKING:
            with self._lock:
                if self._thread is not None:
                    self._latest = dict(event)
        try:
            self._on_event(event)
        except Exception:
            log.exception("Could not report a file job's progress")


def _event(job_id: str, tool: str, state: str) -> dict:
    return {"id": job_id, "tool": tool, "state": state, "phase": "", "current": 0, "total": 0, "unit": ""}


# ---------------------------------------------------------------------- #
# All-or-nothing output                                                    #
# ---------------------------------------------------------------------- #


def write_file(dest: str, write: Callable[[str], None], verify: Callable[[str], None]) -> None:
    """Write `dest` through Save's own path (`safe_write.write_atomically`):
    a temporary file beside it, checked, then moved over it in one step.
    Raises JobFailed, JobCancelled, or whatever BaseException `verify` uses
    to say "don't keep this" (the temporary file is gone either way)."""

    def carry(fn: Callable[[str], None]) -> Callable[[str], None]:
        def wrapped(tmp: str) -> None:
            try:
                fn(tmp)
            except JobFailed as exc:
                raise _Carried(exc) from None

        return wrapped

    try:
        safe_write.write_atomically(dest, carry(write), carry(verify))
    except _Carried as carried:
        raise carried.failure from None
    except safe_write.SaveError as exc:
        raise JobFailed(exc.reason) from None


def write_folder(dest: str, fill: Callable[[str], None], verify: Callable[[str], None]) -> str:
    """Make a new folder at `dest` (or the next free name, if `dest` is
    taken by the time it is done) holding what `fill` writes. Everything is
    written into a temporary folder beside it, checked, and renamed into
    place in one step; an existing folder is never written into. Returns
    the folder's final path."""
    parent = os.path.dirname(os.path.abspath(dest))
    try:
        tmp = tempfile.mkdtemp(dir=parent, prefix=EXPORT_TEMP_PREFIX, suffix=safe_write.TEMP_SUFFIX)
    except OSError as exc:
        raise JobFailed(safe_write.classify(exc, dest)) from None
    try:
        fill(tmp)
        verify(tmp)
        final = _rename_to_free_name(tmp, dest)
        tmp = None
        return final
    except JobFailed:
        raise
    except Exception as exc:
        raise JobFailed(safe_write.classify(exc, dest)) from None
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# A folder can appear at the chosen name between suggesting it and finishing;
# the next free name is taken then, a few times at most.
_RENAME_ATTEMPTS = 20


def _rename_to_free_name(tmp: str, dest: str) -> str:
    for _ in range(_RENAME_ATTEMPTS):
        target = naming.free_folder_name(dest)
        try:
            os.rename(tmp, target)
            return target
        except OSError:
            # Windows says "exists" and POSIX "not empty" for a folder that
            # appeared there; anything else is a real failure.
            if os.path.exists(target):
                continue
            raise
    raise JobFailed(safe_write.UNKNOWN)
