"""Compress's background work for its dialog: steps of the ladder
(`compress_plan.LADDER`) compressed for real into temporary files, so the
dialog can show what each choice will make before the reader picks, and
pressing Compress only moves a finished one into place.

One set of results per dialog (a session), shared by its two tabs: Quality
asks for its three levels' steps (`compress_plan.QUALITY_ORDER`), Size limit
for every step (`compress_plan.CHECK_ORDER`); a step one tab made is never
made again for the other. Once an attempt has shown what the PDF holds, steps
that would make the same file (`compress_plan.same_result`) share it rather
than being run.

Not a file job: no footer pill, and no part of the one-job rule. The dialog
owns them. They run on their own thread, `lector-compress-estimate`, as short
calls into the same worker process the jobs use, one step at a time. They
stop when the dialog closes, and give way when a file job starts
(`give_way`): a Compress job keeps only the step it is waiting for. Every
result is a scratch file (`scratch.py`), deleted when the dialog closes or
the app quits.

The size-limit safety net's result (a step that didn't fit after all) is
held here too, for "Save … file".
"""
import itertools
import logging
import os
import threading
from collections.abc import Callable

from lector.features.file_tools import compress_plan, messages, tools
from lector.features.file_tools.jobs import JobCancelled, JobContext, JobFailed, WorkerSteps
from lector.features.file_tools.scratch import ScratchFiles
from lector.features.reading import ocr

log = logging.getLogger(__name__)

WORKING = "working"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"

# How often a job waiting for a step looks for a cancel.
_WAIT_SLICE_S = 0.2

_sessions = itertools.count(1)


def plan_for(mode: str) -> tuple[int, ...]:
    """The steps a tab needs, in the order they are made."""
    return compress_plan.CHECK_ORDER if mode == compress_plan.SIZE_LIMIT else compress_plan.QUALITY_ORDER


class CompressEstimates:
    """One dialog's ladder results at a time (a session), and its held result."""

    def __init__(self, worker: ocr.OcrRunner, scratch: ScratchFiles, on_event: Callable[[dict], None]):
        self._worker = worker
        self._scratch = scratch
        self._on_event = on_event
        self._lock = threading.Condition()
        self._session = 0
        self._source: tools.Source | None = None
        self._folders: list[str] = []
        self._results: dict[int, tools.Attempt] = {}
        self._failed: set[int] = set()
        self._plan: list[int] = []
        self._running: int | None = None
        self._abort = threading.Event()
        # Set when a plan arrives while the thread is stopping what it was
        # told to: it carries on with the plan instead of ending.
        self._restart = False
        self._thread: threading.Thread | None = None
        self._held: tools.Attempt | None = None
        # The session the step running now belongs to.
        self._running_session = 0

    # ---- The dialog's side ----------------------------------------------

    def start(self, source: tools.Source, folders: list[str], session: int | None = None, mode: str = "") -> int:
        """Make every step `mode`'s tab needs that isn't made yet. With the
        session the dialog already has (it changed tab, or a job it started
        was cancelled), what is finished is kept; otherwise a new session
        starts and the last one's files are deleted. A step running for the
        other tab that this one doesn't need stops (it is made again when
        that tab asks). Returns the session."""
        order = plan_for(mode)
        with self._lock:
            if session != self._session or self._source is None or self._source.path != source.path:
                self._clear_locked()
                self._session = next(_sessions)
                self._source = source
            self._folders = [f for f in folders if f]
            if self._running is not None and self._running not in order:
                self._abort.set()
            self._plan = [step for step in order if step not in self._results and not self._running_for_session(step)]
            self._failed -= set(self._plan)
            self._ensure_thread_locked()
            return self._session

    def _running_for_session(self, step: int) -> bool:
        """Whether `step` is being made now for this session and will be
        kept: a run that was told to stop, or belongs to a session that has
        gone, is thrown away when it ends, so it has to be planned again."""
        return self._running == step and self._running_session == self._session and not self._abort.is_set()

    def stop(self, discard: bool) -> None:
        """Stop the step running and drop the ones waiting. `discard` (the
        dialog closed) also deletes every finished one and the held result;
        otherwise finished ones are kept."""
        with self._lock:
            self._plan = []
            self._restart = False
            self._abort.set()
            if discard:
                self._clear_locked()
                self._session = 0
                self._source = None

    def give_way(self, session: int | None = None, keep: int | None = None) -> None:
        """A file job is starting: the steps stop, except `keep` in
        `session`, the step a Compress job is about to use, which carries on
        (or is started next, if it hadn't started)."""
        with self._lock:
            keeping = bool(session) and session == self._session and _is_step(keep)
            if keeping and keep not in self._results:
                self._plan = [] if self._running_for_session(keep) else [keep]
                self._failed.discard(keep)
            else:
                self._plan = []
            if self._running is not None and not (keeping and self._running_for_session(keep)):
                self._abort.set()
            self._ensure_thread_locked()

    def wait(self, timeout: float) -> bool:
        """Wait for the thread to end (after `stop`); True if it has."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        with self._lock:
            return self._thread is None

    def snapshot(self, session: int) -> dict:
        """Each step's state for the dialog: `{step: {state, size, helps,
        kind}}` (a step not started yet is absent)."""
        with self._lock:
            if not session or session != self._session or self._source is None:
                return {}
            before = self._source.size
            out = {step: _row(DONE, a, before) for step, a in self._results.items()}
            out.update({step: _row(FAILED) for step in self._failed})
            if self._running is not None and self._running_session == self._session:
                out[self._running] = _row(WORKING)
            return out

    def choose(self, session: int, limit: int) -> tuple[int | None, bool]:
        """`compress_plan.choose` on this session's results so far."""
        with self._lock:
            if not session or session != self._session:
                return None, False
            sizes: dict[int, int | None] = {step: a.size for step, a in self._results.items()}
            sizes.update({step: None for step in self._failed})
            return compress_plan.choose(sizes, limit)

    # ---- The job's side --------------------------------------------------

    def wait_for(self, session: int, step: int, ctx: JobContext) -> tools.Attempt | None:
        """The finished result for `step`, taken (from now on the job owns
        its file), waiting while it is still being made. None when there is
        none to wait for. Raises JobCancelled if the job is cancelled while
        waiting; the step carries on for the dialog."""
        with self._lock:
            while True:
                if not session or session != self._session:
                    return None
                if step in self._results:
                    attempt, taken = self._take_locked(step)
                    break
                if not self._running_for_session(step) and step not in self._plan:
                    return None
                self._lock.wait(_WAIT_SLICE_S)
                if ctx.cancelled:
                    raise JobCancelled()
        # It, and the steps that shared its file, have none now: the dialog
        # shows them as not made (a cancelled job's dialog makes them again).
        for other in (step, *taken):
            self._emit(session, other, STOPPED)
        return attempt

    def _take_locked(self, step: int) -> tuple[tools.Attempt, list[int]]:
        attempt = self._results.pop(step)
        taken = [other for other, a in self._results.items() if a.path == attempt.path]
        for other in taken:
            del self._results[other]
        return attempt, taken

    def hold(self, session: int, attempt: tools.Attempt) -> None:
        """The size-limit safety net's result, kept for "Save … file"."""
        with self._lock:
            if not session or session != self._session:
                self._scratch.discard(attempt.path)
                return
            if self._held is not None:
                self._scratch.discard(self._held.path)
            self._held = attempt

    def has_held(self, session: int) -> bool:
        with self._lock:
            held = self._held if session and session == self._session else None
            return held is not None and os.path.isfile(held.path)

    def take_held(self, session: int) -> tools.Attempt | None:
        with self._lock:
            if not session or session != self._session:
                return None
            held, self._held = self._held, None
            return held if held is not None and os.path.isfile(held.path) else None

    def session_source(self, session: int) -> tools.Source | None:
        """The PDF the session's dialog is for, as it was opened (with the
        password a protected one was unlocked with, held in memory only)."""
        with self._lock:
            return self._source if session and session == self._session else None

    # ---- Running them ----------------------------------------------------

    def _ensure_thread_locked(self) -> None:
        """Run the plan: on the thread already there (once it has stopped
        what it was told to stop), or on a new one."""
        if not self._plan:
            return
        if self._thread is not None:
            self._restart = self._abort.is_set()
            return
        self._abort = threading.Event()
        self._restart = False
        self._thread = threading.Thread(target=self._serve, name="lector-compress-estimate", daemon=True)
        self._thread.start()

    def _clear_locked(self) -> None:
        self._abort.set()
        self._plan = []
        for path in {attempt.path for attempt in self._results.values()}:
            self._scratch.discard(path)
        self._results = {}
        self._failed = set()
        if self._held is not None:
            self._scratch.discard(self._held.path)
            self._held = None
        self._lock.notify_all()

    def _next(self):
        """The next step to make, or None when the thread should end (it
        then says so, under the lock, so a later start makes a new one)."""
        with self._lock:
            self._running = None
            if self._abort.is_set() and self._plan and self._restart:
                self._abort = threading.Event()
                self._restart = False
            # A step finished meanwhile (a run told to stop that ended anyway,
            # or one that shares another's file) isn't made again.
            self._plan = [step for step in self._plan if step not in self._results]
            if self._abort.is_set() or not self._plan or self._source is None:
                self._thread = None
                self._lock.notify_all()
                return None
            step = self._plan.pop(0)
            self._running = step
            self._running_session = self._session
            self._lock.notify_all()
            return self._session, step, self._source, list(self._folders), self._abort

    def _serve(self) -> None:
        try:
            while True:
                picked = self._next()
                if picked is None:
                    return
                session, step, source, folders, abort = picked
                self._emit(session, step, WORKING)
                attempt = self._make(session, step, source, folders, abort)
                self._finish(session, step, attempt, abort)
        except Exception:
            log.exception("Compress's estimates stopped")
        finally:
            # However it ended, a later start makes a new thread.
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._running = None
                self._lock.notify_all()

    def _make(self, session, step, source, folders, abort) -> tools.Attempt | None:
        ctx = WorkerSteps(self._worker, abort, f"estimate-{session}-{step}", messages.COMPRESS)
        try:
            tmp = self._scratch.new(folders)
        except OSError:
            log.warning("No folder took a Compress estimate's temporary file")
            return None
        try:
            return tools.compress_attempt(ctx, source, tmp, compress_plan.LADDER[step])
        except JobCancelled:
            self._scratch.discard(tmp)
            return None
        except (JobFailed, Exception):
            log.warning("A Compress estimate failed (step %d)", step, exc_info=True)
            self._scratch.discard(tmp)
            return None

    def _finish(self, session: int, step: int, attempt, abort: threading.Event) -> None:
        shared: list[int] = []
        with self._lock:
            current = session == self._session
            stopped = abort.is_set() and attempt is None
            self._running = None
            if current and attempt is not None and step not in self._results:
                self._results[step] = attempt
                shared = self._share_locked(step, attempt)
            elif attempt is not None:
                # A session that has gone, or a step another's file already
                # stands for.
                self._scratch.discard(attempt.path)
                attempt = self._results[step] if current else None
            elif current and not stopped:
                self._failed.add(step)
            before = self._source.size if current and self._source else 0
            self._lock.notify_all()
        if not current:
            return
        if attempt is None:
            self._emit(session, step, STOPPED if stopped else FAILED)
            return
        for each in [step, *shared]:
            self._emit(session, each, DONE, attempt, before)

    def _share_locked(self, step: int, attempt: tools.Attempt) -> list[int]:
        """Record `attempt` for the steps that would make the same file, so
        they aren't run. Returns them."""
        shared = [
            other
            for other in compress_plan.same_result(step, attempt.facts)
            if other not in self._results and not self._running_for_session(other)
        ]
        for other in shared:
            self._results[other] = attempt
            self._failed.discard(other)
        self._plan = [other for other in self._plan if other not in self._results]
        return shared

    def _emit(self, session: int, step: int, state: str, attempt=None, before: int = 0) -> None:
        try:
            self._on_event({"session": session, "step": step, **_row(state, attempt, before)})
        except Exception:
            log.exception("Could not report a Compress estimate")


def _is_step(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(compress_plan.LADDER)


def _row(state: str, attempt: tools.Attempt | None = None, before: int = 0) -> dict:
    row = {"state": state}
    if attempt is not None:
        row.update(size=attempt.size, helps=compress_plan.helps(before, attempt.size), kind=attempt.kind)
    return row
