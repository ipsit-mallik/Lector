"""File tool jobs: starting one (every path, choice and page number from
the page checked first), its progress and outcome as `lector:filejob`
events, cancelling it, and what jobs wrote this session
(docs/ARCHITECTURE.md § "File tools")."""
import os
import threading

from lector.bridge import checks
from lector.bridge.core import Controller, log
from lector.bridge.file_tool_setup import MAX_CONVERT_INPUTS, ONE_PDF_TOOLS, TOOL_SOURCE_OPEN, BadToolRequest
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import choices as tool_choices
from lector.features.file_tools import destinations as tool_destinations
from lector.features.file_tools import formats as tool_formats
from lector.features.file_tools import jobs as file_jobs
from lector.features.file_tools import messages as tool_messages
from lector.features.file_tools import naming as tool_naming
from lector.features.file_tools import tools as file_tools
from lector.features.file_tools import trash as tool_trash
from lector.features.reading import page_changes, page_plan
from lector.features.settings import store as settings


def _checked_pages(value, page_count: int) -> list[int]:
    """1-based page numbers from the page, as 0-based indices: refused
    unless every one is a whole number on the PDF, none repeated."""
    if not isinstance(value, list) or not value or len(value) > page_count:
        raise BadToolRequest(tool_messages.WORKER_FAILED)
    indices = []
    for number in value:
        index = checks.page_index(number)
        if index is None or not 1 <= index <= page_count:
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        indices.append(index - 1)
    if len(set(indices)) != len(indices):
        raise BadToolRequest(tool_messages.WORKER_FAILED)
    return sorted(indices)


def _with_place(run, where: str):
    """A job whose result also says where its output went ("in Documents"),
    for the dialog's result line and the finished toast."""

    def run_and_say_where(ctx):
        result = run(ctx)
        return {**result, "where": where} if isinstance(result, dict) else result

    return run_and_say_where


def _file_stamp(path: str) -> tuple[int, int] | None:
    """(size, modified time) of `path`, or None when it can't be read: what
    says another program changed a file during a job."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


class FileToolJobsController(Controller):
    def __init__(self, core, tool_setup, compress, organise_controller):
        super().__init__(core)
        self._tool_setup = tool_setup
        self._compress = compress
        self._organise_controller = organise_controller
        # What jobs wrote this session, so each may be shown in its folder.
        self._tool_outputs: set[str] = set()
        self._tool_outputs_lock = threading.Lock()

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
        except BadToolRequest as exc:
            return {"ok": False, "error": tool_messages.message(exc.reason, tool)}
        job_id = self._jobs.start(tool, run)
        if job_id is None:
            return {"ok": False, "busy": True, "error": "Another file job is running."}
        # Compress's background work gives way to any real job once it has
        # started; a Compress job keeps the step it uses (a job already
        # waiting for it keeps waiting: the step is still planned or running).
        keep = getattr(run, "compress_keep", None) or ()
        self._estimates.give_way(*keep)
        return {"ok": True, "id": job_id}

    def _build_job(self, tool: str, source: str, options: dict):
        """Check what the page asked for and return the job's function. Never
        trusts the page: every path, choice and page number is checked."""
        if tool == tool_messages.CONVERT:
            return self._build_convert(options)
        if tool == tool_messages.EXTRACT:
            return self._build_extract(source, options)
        if tool == tool_messages.COMBINE:
            return self._build_combine(options)
        if tool not in ONE_PDF_TOOLS:
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        src = self._tool_setup.job_source(source)
        if tool == tool_messages.COMPRESS:
            src = self._compress.with_session_password(src, options.get("session"))
        if tool == tool_messages.SPLIT:
            return self._build_split(source, src, options)
        dest, where = self._checked_dest(tool, options, [src.path], tool_destinations.BESIDE_ORIGINAL)
        page_count = self._source_page_count(source, src)
        if tool == tool_messages.COMPRESS:
            run = self._compress.build_compress(src, dest, options)
            placed = _with_place(run, where)
            placed.compress_keep = getattr(run, "compress_keep", None)
            return placed
        if tool == tool_messages.EXPORT:
            fmt, ppi = options.get("format"), options.get("ppi")
            if fmt not in tool_choices.EXPORT_FORMATS or isinstance(ppi, bool) or ppi not in tool_choices.EXPORT_PPI:
                raise BadToolRequest(tool_messages.WORKER_FAILED)
            pages = _checked_pages(options.get("pages"), page_count)
            settings.set_file_tool_prefs(export_format=fmt, export_ppi=ppi)
            return _with_place(lambda ctx: file_tools.export_images(ctx, src, dest, fmt, ppi, pages), where)
        pages = _checked_pages(options.get("pages"), page_count)
        known = self._known_words if source == TOOL_SOURCE_OPEN else file_tools.cached_words
        return _with_place(lambda ctx: file_tools.make_searchable(ctx, src, dest, pages, known), where)

    def _build_split(self, source: str, src: "file_tools.Source", options: dict):
        """Split: one file per part, into a new folder; one part (a single
        range) is one new PDF instead. With `options["trash"]` the original
        then goes to the Recycle Bin, only from Home and only once every file
        is written and checked (`_then_trash`)."""
        page_count = self._source_page_count(source, src)
        if self._split_block(source, src):
            raise BadToolRequest(tool_messages.SOURCE_UNREADABLE)
        try:
            parts = tool_naming.split_parts(options.get("mode"), options.get("value"), page_count)
        except tool_naming.RangeError:
            raise BadToolRequest(tool_messages.WORKER_FAILED) from None
        trash = options.get("trash", False)
        if not parts or not isinstance(trash, bool) or (trash and source == TOOL_SOURCE_OPEN):
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        kind = tool_messages.SPLIT_ONE if len(parts) == 1 else tool_messages.SPLIT
        dest, where = self._checked_dest(kind, options, [src.path], tool_destinations.BESIDE_ORIGINAL)
        run = _with_place(lambda ctx: file_tools.split(ctx, src, parts, dest), where)
        return self._then_trash(run, src.path) if trash else run

    def _then_trash(self, run, original: str):
        """`run`, then the original moved to the Recycle Bin. `run` only
        returns once every output is written and has passed its check (a
        failure raises, so nothing is moved) and the worker has let go of the
        original. If the reader still holds it open it is closed first; its
        Recent and Favorites entries go once it has moved. A move that fails
        keeps the new files and says why in the result, and so does Stop
        pressed after the last file was saved, or an original that another
        program changed meanwhile (the new files came from the old one)."""

        def run_then_trash(ctx):
            before = _file_stamp(original)
            result = run(ctx)
            if ctx.cancelled:
                reason = tool_trash.STOPPED
            elif before is None or _file_stamp(original) != before:
                reason = tool_trash.CHANGED
            else:
                self._release_document(original)
                reason = tool_trash.move_to_trash(original)
            if reason is None:
                try:
                    self._forget_file(original)
                except Exception:
                    # The move has happened: a Recent entry left behind is
                    # not a failed split (it offers Remove from Recent).
                    log.exception("Couldn't forget a file moved to the Recycle Bin")
                result["original"] = {"moved": True, "trash_name": tool_trash.TRASH_NAME}
            else:
                log.info("Split kept the original: it couldn't be moved (%s)", reason)
                result["original"] = {
                    "moved": False,
                    "reason": reason,
                    "error": tool_trash.reason_sentence(reason),
                    "trash_name": tool_trash.TRASH_NAME,
                }
            return result

        return run_then_trash

    def _release_document(self, path: str) -> None:
        """Let go of `path` if it is the document the reader has open (Home
        leaves it open in Python; leaving the reader already asked about
        unsaved changes), so Windows lets it move."""
        if not (self._doc.is_open and self._doc.is_open_document(path)):
            return
        self._doc.close()
        self._core.title = ""
        self._core.path = ""
        self._core.persisted_position = None

    @staticmethod
    def _forget_file(path: str) -> None:
        """The original has gone to the Recycle Bin: its Recent and Favorites
        entries go too (by normalised path, as the page may spell it apart)."""
        key = os.path.normcase(os.path.abspath(path))
        for entry in settings.get_recent_files():
            if os.path.normcase(os.path.abspath(entry["path"])) == key:
                settings.remove_recent_file(entry["path"])
        for entry in settings.get_favorites():
            if os.path.normcase(os.path.abspath(entry["path"])) == key:
                settings.remove_favorite(entry["path"])

    def _build_combine(self, options: dict):
        """Combine files: at least two inputs, each a PDF, image or e-book on
        disk; a protected PDF's password is the one its unlock held."""
        inputs = options.get("inputs")
        if not isinstance(inputs, list) or not 2 <= len(inputs) <= MAX_CONVERT_INPUTS:
            raise BadToolRequest(tool_messages.INPUT_UNREADABLE)
        for path in inputs:
            if not isinstance(path, str) or not os.path.isfile(path):
                raise BadToolRequest(tool_messages.INPUT_UNREADABLE)
            if tool_formats.extension(path) != dialog_browser.PDF_EXTENSION and not tool_formats.is_convertible(path):
                raise BadToolRequest(tool_messages.INPUT_UNREADABLE)
        held = dict(self._tool_setup.combine_passwords)
        with_passwords = [(path, held.get(os.path.normcase(os.path.abspath(path)))) for path in inputs]
        dest, where = self._checked_dest(tool_messages.COMBINE, options, inputs, tool_destinations.BESIDE_FIRST_FILE)
        return _with_place(lambda ctx: file_tools.combine(ctx, with_passwords, dest), where)

    def _build_convert(self, options: dict):
        inputs = options.get("inputs")
        if not isinstance(inputs, list) or not 0 < len(inputs) <= MAX_CONVERT_INPUTS:
            raise BadToolRequest(tool_messages.INPUT_UNREADABLE)
        for path in inputs:
            if not isinstance(path, str) or not os.path.isfile(path) or not tool_formats.is_convertible(path):
                raise BadToolRequest(tool_messages.INPUT_UNREADABLE)
        page_size = options.get("page_size")
        if page_size not in tool_formats.PAGE_SIZE_CHOICES:
            raise BadToolRequest(tool_messages.WORKER_FAILED)
        dest, where = self._checked_dest(tool_messages.CONVERT, options, inputs, tool_destinations.BESIDE_FIRST_FILE)
        return _with_place(lambda ctx: file_tools.convert(ctx, list(inputs), dest, page_size), where)

    def _build_extract(self, source, options: dict):
        """Organise's Extract: the draft's pages at `options["positions"]`,
        as the grid shows them now. The open document's bytes (unsaved
        highlights included) and each inserted file's go to the job; the
        draft itself isn't touched."""
        numbers = self._organise_controller.extract_numbers(options.get("positions"))
        if source != TOOL_SOURCE_OPEN or numbers is None:
            raise BadToolRequest(tool_messages.SOURCE_UNREADABLE)
        plan = self._organise.current
        chosen = [plan[n - 1] for n in numbers]
        pages = [(None if p.source == page_plan.DOC else p.source, p.index, p.turn) for p in chosen]
        source_data = {}
        for inserted in {p.source for p in chosen if p.source != page_plan.DOC}:
            data = self._doc.page_source_bytes(inserted)
            if data is None:
                raise BadToolRequest(tool_messages.SOURCE_UNREADABLE)
            source_data[inserted] = data
        dest, where = self._checked_dest(
            tool_messages.EXTRACT, options, [self._doc.path], tool_destinations.BESIDE_ORIGINAL
        )
        data, password = self._doc.snapshot()
        src = file_tools.Source(self._doc.path, data, password)
        return _with_place(lambda ctx: file_tools.extract(ctx, src, pages, source_data, dest), where)

    def _split_block(self, source: str, src: "file_tools.Source") -> str | None:
        """Why Split may not take this PDF apart (its owner forbids both
        modifying and assembling it), or None."""
        if source == TOOL_SOURCE_OPEN:
            return self._doc.page_change_block()
        info = self._tool_setup.describe_home_source(src.path, src.password)
        return None if info.get("can_change_pages", True) else page_changes.RESTRICTED

    def _source_page_count(self, source: str, src: "file_tools.Source") -> int:
        if source == TOOL_SOURCE_OPEN:
            return self._doc.page_count
        info = self._tool_setup.describe_home_source(src.path, src.password)
        if not info.get("ok", True) or "pages" not in info:
            raise BadToolRequest(tool_messages.SOURCE_UNREADABLE)
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
            raise BadToolRequest(exc.reason) from None
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

    def on_file_job_event(self, event: dict) -> None:
        """A job's progress or outcome, from its own thread, to the page.
        What a job wrote may then be shown in its folder or opened."""
        result = event.get("result")
        if event.get("state") == file_jobs.DONE and isinstance(result, dict) and result.get("path"):
            with self._tool_outputs_lock:
                self._tool_outputs.add(os.path.normcase(os.path.abspath(result["path"])))
            folder = os.path.dirname(result["path"])
            result.update(
                name=os.path.basename(result["path"]),
                folder=folder,
                # "Saved to Downloads as …" (Compress's Done state).
                folder_label=tool_destinations.folder_label(folder),
            )
        if event.get("state") != file_jobs.WORKING:
            self._tool_setup.forget_tool_passwords()
        self._dispatch_page_event("lector:filejob", event)

    def is_tool_output(self, path: str) -> bool:
        with self._tool_outputs_lock:
            return os.path.normcase(os.path.abspath(path)) in self._tool_outputs
