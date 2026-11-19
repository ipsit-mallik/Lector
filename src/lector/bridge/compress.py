"""Compress: the estimates its dialog runs in the background, and the job
it starts, by Quality level or size limit (docs/ARCHITECTURE.md § "File
tools")."""
import dataclasses
import os
import tempfile

from lector.bridge.core import Controller
from lector.bridge.file_tool_setup import BadToolRequest
from lector.features.file_tools import choices as tool_choices
from lector.features.file_tools import compress_plan
from lector.features.file_tools import jobs as file_jobs
from lector.features.file_tools import messages as tool_messages
from lector.features.file_tools import tools as file_tools
from lector.features.reading import safe_write
from lector.features.settings import store as settings


def _session_id(value) -> int:
    """Compress's dialog session as the page sent it, or 0 for none."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class CompressController(Controller):
    def __init__(self, core, tool_setup):
        super().__init__(core)
        self._tool_setup = tool_setup

    def build_compress(self, src: "file_tools.Source", dest: str, options: dict):
        """Compress's runs: a Quality level or a size limit (the dialog's
        finished step moved into place, or, for a level, waited for), or the
        size-limit safety net's held result."""
        mode = options.get("mode", compress_plan.QUALITY)
        session = _session_id(options.get("session"))
        opened = self._estimates.session_source(session)
        same_source = opened is not None and safe_write.same_file(opened.path, src.path)
        if mode == compress_plan.QUALITY:
            level = options.get("level")
            if level not in tool_choices.LEVELS:
                raise BadToolRequest(tool_messages.WORKER_FAILED)
            step = compress_plan.LEVEL_STEPS[level]
            estimate = None
            if same_source:
                estimate = lambda ctx: self._estimates.wait_for(session, step, ctx)  # noqa: E731

            def run_level(ctx):
                return file_tools.compress(ctx, src, dest, level, self._scratch, estimate)

            run_level.compress_keep = (session, step) if same_source else None
            return run_level
        value, unit = options.get("limit"), options.get("unit")
        limit = compress_plan.limit_bytes(value, unit)
        if limit is None or not same_source:
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        label = f"{value} {unit}"
        if mode == compress_plan.SAVE_HELD:
            if not self._estimates.has_held(session):
                raise BadToolRequest(tool_messages.WORKER_FAILED)

            def save(ctx):
                # Taken only once the job runs: a request that never starts
                # leaves it held for "Save … file" again.
                held = self._estimates.take_held(session)
                if held is None:
                    raise file_jobs.JobFailed(tool_messages.WORKER_FAILED)
                return file_tools.save_held(ctx, src, dest, held, label, self._scratch)

            return save
        # A limit at or above the file's size has no pill, and one the
        # dialog's checking hasn't settled on a step for can't be chosen.
        if mode != compress_plan.SIZE_LIMIT or limit >= src.size:
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        step, settled, planned = self._estimates.decide(session, limit)
        if step is None or not settled:
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        settings.set_file_tool_prefs(compress_limit=value, compress_unit=unit)
        known = self._estimates.real_sizes(session)

        def estimate(ctx, each):
            return self._estimates.wait_for(session, each, ctx)

        def hold(attempt) -> None:
            self._estimates.hold(session, attempt)

        def run_limit(ctx):
            return file_tools.compress_to_limit(
                ctx, src, dest, step, limit, label, self._scratch, estimate, hold, known, planned
            )

        run_limit.compress_keep = (session, step)
        return run_limit

    def with_session_password(self, src: "file_tools.Source", session) -> "file_tools.Source":
        """A protected PDF from Home: the password held for it goes when a
        job ends, but Compress's dialog may run another on the same file
        (Change limit, Save … file); its session kept the password."""
        opened = self._estimates.session_source(_session_id(session))
        if opened is None or src.password is not None or opened.password is None:
            return src
        if not safe_write.same_file(opened.path, src.path):
            return src
        return dataclasses.replace(src, password=opened.password)

    # ---- Compress's estimates (owned by its dialog; not file jobs) ----------

    def start_compress_estimates(self, source: str, folder: str, session=None, mode=compress_plan.QUALITY) -> dict:
        """Compress the steps `mode`'s tab needs in the background (Quality:
        its three levels; Size limit: the whole ladder), into temporary files
        in `folder` (the destination's; else the original's, else the
        system's temporary folder). Each finished one arrives as a
        `lector:compressestimate` event. `session` keeps what an earlier
        call in the same dialog finished. `{"ok": True, "session"}`."""
        if mode not in compress_plan.MODES:
            return {"ok": False}
        try:
            src = self._tool_setup.job_source(source)
        except BadToolRequest:
            return {"ok": False}
        folders = self._scratch_folders(folder, src.path)
        return {"ok": True, "session": self._estimates.start(src, folders, _session_id(session) or None, mode)}

    def stop_compress_estimates(self, discard: bool = True) -> dict:
        """The dialog closed (`discard`: every temporary file goes), or
        stopped its background work (finished steps are kept)."""
        self._estimates.stop(discard=discard is not False)
        return {"ok": True}

    def get_compress_estimates(self, session) -> dict:
        """What the session knows so far, for a dialog drawn again: each
        ladder step's result, a big file's estimates, and whether there was
        room for the temporary files (`CompressEstimates.snapshot`)."""
        if not isinstance(session, int) or isinstance(session, bool):
            return {}
        return self._estimates.snapshot(session)

    def select_compress_limit(self, session, value=None, unit=None) -> dict:
        """The limit picked in Size limit (a pill or "Other size…"), or none:
        its step is made first and its file kept."""
        if not isinstance(session, int) or isinstance(session, bool):
            return {"ok": False}
        limit = None if value is None else compress_plan.limit_bytes(value, unit)
        if value is not None and limit is None:
            return {"ok": False}
        self._estimates.select(session, limit)
        return {"ok": True}

    def set_compress_mode(self, mode: str) -> dict:
        """Remember Quality or Size limit for next time."""
        if mode not in compress_plan.MODES:
            return {"ok": False}
        settings.set_file_tool_prefs(compress_mode=mode)
        return {"ok": True}

    @staticmethod
    def _scratch_folders(folder, origin: str) -> list[str]:
        folders = [folder] if isinstance(folder, str) and os.path.isabs(folder) and os.path.isdir(folder) else []
        return folders + [os.path.dirname(origin), tempfile.gettempdir()]
