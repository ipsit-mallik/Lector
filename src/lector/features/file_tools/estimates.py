"""Compress's background work for its dialog: steps of the ladder
(`compress_plan.LADDER`) compressed for real into temporary files, so the
dialog can show what each choice will make before the reader picks, and
pressing Compress only moves a finished one into place.

One set of results per dialog (a session), shared by its two tabs. Quality
asks for its three levels' steps (`compress_plan.QUALITY_ORDER`). Size limit
runs only the steps that can still change a visible pill's answer
(`limit_plan.next_step`), and stops once every one is settled. A big file
(`limit_plan.needs_sample`) is first estimated from a sample of its pages
(`sampling.py`, in memory), which can settle pills with room to spare; its
real steps then go the selected pill's step first. A step one tab made is
never made again for the other, and once an attempt has shown what the PDF
holds, steps that would make the same file (`compress_plan.same_result`)
share it rather than being run.

Not a file job: no footer pill, and no part of the one-job rule. The dialog
owns them. They run on their own thread, `lector-compress-estimate`, as short
calls into the same worker process the jobs use, one at a time. They stop
when the dialog closes, and give way when a file job starts (`give_way`): a
Compress job keeps only the step it is waiting for.

Every result is a scratch file (`scratch.py`), deleted as soon as it is no
pill's answer and no Quality level's step (`limit_plan.answers`), and when
the dialog closes or the app quits; its size is kept. With too little free
space to keep them, each is deleted once measured; with too little to write
one at all, the dialog says so (`nospace`) until it asks again.

What the page is sent carries the session and a sequence number (`seq`), so
it can drop a result older than one it has already shown.

The size-limit safety net's result (a step that didn't fit after all) is
held here too, for "Save … file".
"""
import dataclasses
import itertools
import logging
import os
import threading
from collections.abc import Callable

from lector.features.file_tools import compress_plan, engine, limit_plan, messages, sampling, scratch, tools
from lector.features.file_tools.jobs import JobCancelled, JobFailed, JobContext, WorkerSteps
from lector.features.file_tools.scratch import ScratchFiles
from lector.features.reading import ocr, safe_write

log = logging.getLogger(__name__)

WORKING = "working"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"

# How often a job waiting for a step looks for a cancel.
_WAIT_SLICE_S = 0.2
# How many times a big file's sample may go missing in the worker (it was
# replaced) and be made again in a session, before sampling stops.
_MAX_LOST_SAMPLES = 2

# What the thread does next: a real step, a look at the PDF (and its sample,
# for a big file), or one step of the sample.
_REAL, _LOOK, _SAMPLE = "real", "look", "sample"
_QUALITY_STEPS = frozenset(compress_plan.LEVEL_STEPS.values())
_NO_SPACE = object()  # a step's file couldn't be written: the drive is full

_sessions = itertools.count(1)


def plan_for(mode: str) -> tuple[int, ...]:
    """The steps a tab may need, in the order Quality makes them (Size limit
    picks its own as results come in)."""
    return compress_plan.CHECK_ORDER if mode == compress_plan.SIZE_LIMIT else compress_plan.QUALITY_ORDER


class CompressEstimates:
    """One dialog's ladder results at a time (a session), and its held result."""

    def __init__(self, worker: ocr.OcrRunner, scratch_files: ScratchFiles, on_event: Callable[[dict], None]):
        self._worker = worker
        self._scratch = scratch_files
        self._on_event = on_event
        self._lock = threading.Condition()
        self._seq = itertools.count(1)
        self._session = 0
        self._source: tools.Source | None = None
        self._folders: list[str] = []
        self._results: dict[int, tools.Attempt] = {}
        self._failed: set[int] = set()
        # What the thread is asked for: a tab's steps (`_mode`), or only the
        # step a Compress job is waiting for (`_keep`).
        self._mode: str | None = None
        self._keep: int | None = None
        # Steps a job has taken since the dialog last asked: a job's late
        # `give_way` for one doesn't make it again.
        self._taken: set[int] = set()
        # Size limit: the limit the reader chose (bytes), the look at the PDF
        # (None until made), and the sample's estimates (step → {size, kind},
        # or None for one the sample couldn't make).
        self._selected: int | None = None
        self._look: dict | None = None
        self._lost_samples = 0
        self._estimates: dict[int, dict | None] = {}
        self._estimates_seq = 0
        # The worker's job id holding this dialog's sample, while it does
        # (kept across a change of tab, until every step is estimated), and
        # samples no longer needed that the worker is still to drop.
        self._sample_job: str | None = None
        self._closing: list[str] = []
        self._no_space = False
        self._no_space_seq = 0
        self._row_seq: dict[int, int] = {}
        self._running: int | None = None
        self._abort = threading.Event()
        # Set when work arrives while the thread is stopping what it was
        # told to: it carries on instead of ending.
        self._restart = False
        self._thread: threading.Thread | None = None
        self._held: tools.Attempt | None = None
        # The session the step running now belongs to.
        self._running_session = 0

    # ---- The dialog's side ----------------------------------------------

    def start(self, source: tools.Source, folders: list[str], session: int | None = None, mode: str = "") -> int:
        """Make what `mode`'s tab needs that isn't made yet. With the session
        the dialog already has (it changed tab, changed folder, or a job it
        started was cancelled), what is finished is kept, and a "not enough
        space" is tried again; otherwise a new session starts and the last
        one's files are deleted. A step running for the other tab that this
        one doesn't need stops (it is made again when that tab asks).
        Returns the session."""
        mode = compress_plan.SIZE_LIMIT if mode == compress_plan.SIZE_LIMIT else compress_plan.QUALITY
        events = []
        with self._lock:
            if session != self._session or self._source is None or self._source.path != source.path:
                self._clear_locked()
                self._session = next(_sessions)
                self._source = source
            self._folders = [f for f in folders if f]
            if self._no_space:
                self._no_space = False
                events.append(self._space_event_locked())
            self._mode = mode
            self._keep = None
            self._taken = set()
            self._failed -= set(plan_for(mode))
            if self._running is not None and not self._wanted_locked(self._running):
                self._abort.set()
            self._ensure_thread_locked()
            session = self._session
        self._send(events)
        self._close_samples()
        return session

    def select(self, session: int, limit: int | None) -> None:
        """The limit the reader chose in Size limit (a pill, or "Other
        size…"), or None: its step goes first, it counts as a pill that
        needs an answer, and its answer's file is kept."""
        with self._lock:
            if not session or session != self._session:
                return
            self._selected = limit if _is_bytes(limit) else None
            self._prune_locked()
            self._ensure_thread_locked()

    def stop(self, discard: bool) -> None:
        """Stop the step running and drop the ones waiting. `discard` (the
        dialog closed) also deletes every finished one and the held result;
        otherwise finished ones are kept."""
        with self._lock:
            self._mode = None
            self._keep = None
            self._restart = False
            self._abort.set()
            if discard:
                self._clear_locked()
                self._session = 0
                self._source = None
        self._close_samples()

    def give_way(self, session: int | None = None, keep: int | None = None) -> None:
        """A file job is starting: the steps stop, except `keep` in
        `session`, the step a Compress job is about to use, which carries on
        (or is started next, if it hadn't started)."""
        with self._lock:
            self._give_way_locked(session, keep)

    def _give_way_locked(self, session, keep) -> None:
        keeping = bool(session) and session == self._session and _is_step(keep)
        self._mode = None
        if keeping and keep in self._taken:
            # The job got here first: it took `keep` and may already be
            # waiting for a step further down (Size limit). What it asked
            # for stands; this late call only stops the dialog's own work.
            pass
        else:
            self._keep = keep if keeping and keep not in self._results else None
            if keeping:
                self._failed.discard(keep)
        kept = self._keep is not None and self._running_for_session(self._keep)
        if self._running is not None and not kept:
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
        """Everything the dialog shows, for one drawn again: `steps` (each
        step's `{state, size, helps, kind, seq}`; a step not started yet is
        absent), `estimates` (a big file's, or None) with `estimates_seq`,
        and `nospace` with `nospace_seq`."""
        with self._lock:
            if not session or session != self._session or self._source is None:
                return {}
            before = self._source.size
            steps = {step: _row(DONE, a, before) for step, a in self._results.items()}
            steps.update({step: _row(FAILED) for step in self._failed})
            if self._running is not None and self._running_session == self._session:
                steps[self._running] = _row(WORKING)
            for step, row in steps.items():
                row["seq"] = self._row_seq.get(step, 0)
            return {
                "steps": steps,
                "estimates": self._estimates_payload_locked(),
                "estimates_seq": self._estimates_seq,
                "nospace": self._no_space,
                "nospace_seq": self._no_space_seq,
            }

    def decide(self, session: int, limit: int) -> tuple[int | None, bool, dict | None]:
        """`limit_plan.decide` on this session's results and estimates:
        (step, settled, the step's estimate `{size, kind}` when it was
        decided on one)."""
        with self._lock:
            if not session or session != self._session:
                return None, False, None
            real, estimated = self._real_sizes_locked(), self._estimate_sizes_locked()
            step, settled = limit_plan.decide(real, estimated, limit)
            estimate = self._estimates.get(step) if step is not None and step not in real else None
            return step, settled, estimate

    def real_sizes(self, session: int) -> dict[int, int | None]:
        with self._lock:
            return self._real_sizes_locked() if session and session == self._session else {}

    # ---- The job's side --------------------------------------------------

    def wait_for(self, session: int, step: int, ctx: JobContext) -> tools.Attempt | None:
        """The finished result for `step`, taken (from now on the job owns
        its file), waiting while it is still being made, and making it next
        if it hasn't been. None when there is no file to take (none could be
        made here, or it was deleted once measured): the job makes it then.
        Raises JobCancelled if the job is cancelled while waiting; the step
        carries on for the dialog."""
        with self._lock:
            if session and session == self._session and step not in self._results:
                self._give_way_locked(session, step)
            while True:
                if not session or session != self._session:
                    return None
                if step in self._results:
                    if not self._results[step].path:
                        self._release_keep_locked(step)
                        return None
                    attempt, taken = self._take_locked(step)
                    break
                coming = self._keep == step and not self._no_space and step not in self._failed
                if not self._running_for_session(step) and not coming:
                    return None
                self._lock.wait(_WAIT_SLICE_S)
                if ctx.cancelled:
                    raise JobCancelled()
            self._release_keep_locked(step)
            # It, and the steps that shared its file, have none now: the
            # dialog shows them as not made (a cancelled job's dialog makes
            # them again).
            events = [self._row_event_locked(other, STOPPED) for other in (step, *taken)]
        self._send(events)
        return attempt

    def _release_keep_locked(self, step: int) -> None:
        if self._keep == step:
            self._keep = None

    def _take_locked(self, step: int) -> tuple[tools.Attempt, list[int]]:
        attempt = self._results.pop(step)
        taken = [other for other, a in self._results.items() if a.path == attempt.path]
        for other in taken:
            del self._results[other]
        self._taken.update((step, *taken))
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

    # ---- What to do next -------------------------------------------------

    def _real_sizes_locked(self) -> dict[int, int | None]:
        sizes: dict[int, int | None] = {step: a.size for step, a in self._results.items()}
        sizes.update({step: None for step in self._failed})
        return sizes

    def _estimate_sizes_locked(self) -> dict[int, int | None] | None:
        if not self._estimates:
            return None
        return {step: (row or {}).get("size") for step, row in self._estimates.items()}

    def _limits_locked(self) -> list[int]:
        return limit_plan.limits_of_interest(self._source.size, self._selected) if self._source else []

    def _sampling_locked(self) -> bool:
        """Whether a big file's sample is still to be made (or looked for).
        Before the look only the size is known here, so a file big by its
        page count alone (`limit_plan.needs_sample`) is found by the look
        itself; until then a real step already running is left to finish."""
        if self._look is None:
            return self._source is not None and self._source.size > limit_plan.SAMPLE_MIN_BYTES
        return bool(self._look.get("sampled")) and len(self._estimates) < len(compress_plan.LADDER)

    def _wanted_locked(self, step: int) -> bool:
        """Whether the running `step` is still wanted by what was asked."""
        if self._keep is not None:
            return step == self._keep
        if self._mode == compress_plan.QUALITY:
            return step in compress_plan.QUALITY_ORDER
        return self._mode == compress_plan.SIZE_LIMIT and not self._sampling_locked()

    def _pick_locked(self):
        """The next thing to do, `(kind, step)`, or None. No side effects."""
        if self._source is None or self._no_space:
            return None
        if self._keep is not None:
            missing = self._keep not in self._results and self._keep not in self._failed
            return (_REAL, self._keep) if missing else None
        if self._mode == compress_plan.QUALITY:
            step = next((s for s in compress_plan.QUALITY_ORDER if s not in self._results and s not in self._failed), None)
            return None if step is None else (_REAL, step)
        if self._mode != compress_plan.SIZE_LIMIT:
            return None
        if self._look is None or (self._sampling_locked() and not self._sample_job):
            return _LOOK, None
        if self._sampling_locked():
            return _SAMPLE, next(s for s in compress_plan.CHECK_ORDER if s not in self._estimates)
        step = limit_plan.next_step(
            self._real_sizes_locked(), self._estimate_sizes_locked(), self._limits_locked(), self._selected
        )
        return None if step is None else (_REAL, step)

    def _running_for_session(self, step: int) -> bool:
        """Whether `step` is being made now for this session and will be
        kept: a run that was told to stop, or belongs to a session that has
        gone, is thrown away when it ends, so it has to be made again."""
        return self._running == step and self._running_session == self._session and not self._abort.is_set()

    def _prune_locked(self) -> None:
        """Delete every finished step's file that is no limit's current
        answer and no Quality level's step; its size stays known."""
        if self._source is None:
            return
        keep = limit_plan.answers(self._real_sizes_locked(), self._estimate_sizes_locked(), self._limits_locked())
        keep |= _QUALITY_STEPS
        if self._keep is not None:
            keep.add(self._keep)  # a job is waiting to take it
        kept = {self._results[step].path for step in keep if step in self._results}
        for step, attempt in list(self._results.items()):
            if attempt.path and attempt.path not in kept:
                self._scratch.discard(attempt.path)
                self._results[step] = dataclasses.replace(attempt, path="")

    # ---- Running them ----------------------------------------------------

    def _ensure_thread_locked(self) -> None:
        """Run what is asked: on the thread already there (once it has
        stopped what it was told to stop), or on a new one."""
        if self._pick_locked() is None:
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
        self._mode = None
        self._keep = None
        self._taken = set()
        for path in {attempt.path for attempt in self._results.values()}:
            self._scratch.discard(path)
        self._results = {}
        self._failed = set()
        self._selected = None
        self._look = None
        self._lost_samples = 0
        if self._sample_job is not None:
            self._closing.append(self._sample_job)
            self._sample_job = None
        self._estimates = {}
        self._estimates_seq = 0
        self._no_space = False
        self._no_space_seq = 0
        self._row_seq = {}
        if self._held is not None:
            self._scratch.discard(self._held.path)
            self._held = None
        self._lock.notify_all()

    def _next(self):
        """The next thing to do, or None when the thread should end (it then
        says so, under the lock, so a later start makes a new one)."""
        with self._lock:
            self._running = None
            if self._abort.is_set() and self._restart:
                self._abort = threading.Event()
                self._restart = False
            picked = None if self._abort.is_set() else self._pick_locked()
            if picked is None:
                self._thread = None
                self._lock.notify_all()
                return None
            kind, step = picked
            working = None
            if kind == _REAL:
                self._running = step
                self._running_session = self._session
                working = self._row_event_locked(step, WORKING)
            for_job = kind == _REAL and step == self._keep
            self._lock.notify_all()
            return (
                self._session, kind, step, self._source, list(self._folders), self._abort, working, self._look, for_job
            )

    def _serve(self) -> None:
        try:
            while True:
                picked = self._next()
                if picked is None:
                    return
                session, kind, step, source, folders, abort, working, look, for_job = picked
                self._close_samples()
                if kind == _LOOK:
                    self._look_at(session, source, abort)
                elif kind == _SAMPLE:
                    self._sample(session, step, look, abort)
                else:
                    self._send([working])
                    attempt = self._make(session, step, source, folders, abort, for_job)
                    self._finish(session, step, attempt, abort)
        except Exception:
            log.exception("Compress's estimates stopped")
            # A job waiting for a step makes it itself rather than waiting
            # for a thread that has gone.
            with self._lock:
                self._keep = None
        finally:
            self._close_samples()
            # However it ended, a later start makes a new thread.
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._running = None
                self._lock.notify_all()

    # ---- A big file's sample ---------------------------------------------

    def _look_at(self, session: int, source: tools.Source, abort: threading.Event) -> None:
        """How many pages, whether there are images, and, for a big file, a
        sample of its pages kept in the worker for `_sample`."""
        job = f"sample-{session}"
        ctx = WorkerSteps(self._worker, abort, job, messages.COMPRESS)
        path = None if source.data is not None else source.path
        with self._lock:
            lost, before = self._lost_samples, dict(self._look or {})
        if lost > _MAX_LOST_SAMPLES:
            # The sample keeps going missing (the worker keeps being
            # replaced): carry on with real steps and what was estimated.
            look = {**before, "sampled": False}
        else:
            try:
                # Long: a dialog that closes or moves on stops it at its next
                # page rather than waiting for the whole look.
                look = ctx.step(sampling.sample_open, job, source.data, path, source.password, long=True)
            except JobCancelled:
                return
            except Exception:
                log.warning("Couldn't sample a PDF for Compress's estimates", exc_info=True)
                look = {"sampled": False}
        with self._lock:
            stale = session != self._session
            if not stale:
                self._look = look
                if look.get("sampled"):
                    self._sample_job = job
        if stale and look.get("sampled"):
            ctx.release(sampling.sample_close)

    def _sample(self, session: int, step: int, look: dict, abort: threading.Event) -> None:
        """One step of the sample, scaled to the whole document."""
        job = f"sample-{session}"
        ctx = WorkerSteps(self._worker, abort, job, messages.COMPRESS)
        recipe = compress_plan.LADDER[step]
        try:
            measured = ctx.step(sampling.sample_step, job, recipe, long=True)
            estimate = {"size": limit_plan.scale_sample(look, measured), "kind": compress_plan.kind(recipe, measured["info"])}
        except JobCancelled:
            return
        except engine.JobMissing:
            # The worker was replaced since the sample was made: make it again.
            self._forget_sample(job)
            return
        except (JobFailed, Exception):
            log.warning("A Compress sample step failed (step %d)", step, exc_info=True)
            estimate = None
        with self._lock:
            if session != self._session:
                return
            self._estimates[step] = estimate
            self._estimates_seq = next(self._seq)
            event = {"session": session, "seq": self._estimates_seq, "estimates": self._estimates_payload_locked()}
        self._send([event])

    def _forget_sample(self, job: str) -> None:
        """The worker lost the sample (it was replaced): it is made again,
        up to `_MAX_LOST_SAMPLES` times."""
        with self._lock:
            if self._sample_job == job:
                self._sample_job = None
                self._lost_samples += 1

    def _close_samples(self) -> None:
        """Let the worker drop the samples no longer needed: a session's that
        has gone, and the current one's once every step is estimated. A
        change of tab keeps it, so coming back doesn't make it again. Queued
        behind the worker's current call, not waited for."""
        with self._lock:
            if self._sample_job is not None and not self._sampling_locked():
                self._closing.append(self._sample_job)
                self._sample_job = None
            jobs, self._closing = self._closing, []
        for job in jobs:
            self._worker.call(sampling.sample_close, (job,)).add_done_callback(_log_close_failure)

    def _estimates_payload_locked(self) -> dict | None:
        return dict(self._estimates) if self._estimates else None

    # ---- A real step -------------------------------------------------------

    def _make(self, session, step, source, folders, abort, for_job=False):
        """The step compressed into a temporary file: an Attempt (with no
        file when there was too little room to keep it), None when it failed
        or was stopped, or `_NO_SPACE` when it couldn't be written. A step a
        Compress job is waiting for (`for_job`) keeps its file whatever the
        free space: the job is about to take it."""
        ctx = WorkerSteps(self._worker, abort, f"estimate-{session}-{step}", messages.COMPRESS)
        before = source.size
        try:
            tmp = self._scratch.new(folders)
        except OSError as exc:
            log.warning("No folder took a Compress estimate's temporary file")
            return _NO_SPACE if scratch.is_space_error(exc) else None
        # The drive the file is really on: `new` may have passed over a
        # folder that refused it.
        folder = os.path.dirname(tmp)
        free = scratch.free_bytes(folder)
        if not scratch.can_write(free, before):
            log.warning("Compress: not enough free space for a step's temporary file (step %d)", step)
            self._scratch.discard(tmp)
            return _NO_SPACE
        try:
            attempt = tools.compress_attempt(ctx, source, tmp, compress_plan.LADDER[step])
        except JobCancelled:
            self._scratch.discard(tmp)
            return None
        except (JobFailed, Exception) as exc:
            log.warning("A Compress estimate failed (step %d)", step, exc_info=True)
            # Classified before the partial file is deleted, so a drive that
            # filled up while it was written still reads as full (the same
            # test every save uses).
            full = safe_write.classify(exc, tmp) == safe_write.DISK_FULL
            self._scratch.discard(tmp)
            return _NO_SPACE if full else None
        if not for_job and not scratch.keeps_files(free, before):
            self._scratch.discard(attempt.path)
            attempt = dataclasses.replace(attempt, path="")
        return attempt

    def _finish(self, session: int, step: int, attempt, abort: threading.Event) -> None:
        events = []
        with self._lock:
            current = session == self._session
            self._running = None
            if attempt is _NO_SPACE:
                if current:
                    self._no_space = True
                    events += [self._row_event_locked(step, STOPPED), self._space_event_locked()]
                self._lock.notify_all()
            else:
                events = self._record_locked(current, step, attempt, abort)
        self._send(events)

    def _record_locked(self, current: bool, step: int, attempt, abort: threading.Event) -> list[dict]:
        stopped = abort.is_set() and attempt is None
        shared: list[int] = []
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
        self._lock.notify_all()
        if not current:
            return []
        if attempt is None:
            return [self._row_event_locked(step, STOPPED if stopped else FAILED)]
        before = self._source.size if self._source else 0
        events = [self._row_event_locked(each, DONE, attempt, before) for each in (step, *shared)]
        self._prune_locked()
        return events

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
        return shared

    # ---- What the page is sent --------------------------------------------

    def _row_event_locked(self, step: int, state: str, attempt=None, before: int = 0) -> dict:
        seq = next(self._seq)
        self._row_seq[step] = seq
        return {"session": self._session, "step": step, "seq": seq, **_row(state, attempt, before)}

    def _space_event_locked(self) -> dict:
        self._no_space_seq = next(self._seq)
        return {"session": self._session, "seq": self._no_space_seq, "nospace": self._no_space}

    def _send(self, events: list[dict]) -> None:
        for event in events:
            try:
                self._on_event(event)
            except Exception:
                log.exception("Could not report a Compress estimate")


def _is_step(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(compress_plan.LADDER)


def _log_close_failure(future) -> None:
    if future.exception() is not None:
        log.debug("Couldn't release Compress's sample in the worker", exc_info=future.exception())


def _is_bytes(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _row(state: str, attempt: tools.Attempt | None = None, before: int = 0) -> dict:
    row = {"state": state}
    if attempt is not None:
        row.update(size=attempt.size, helps=compress_plan.helps(before, attempt.size), kind=attempt.kind)
    return row
