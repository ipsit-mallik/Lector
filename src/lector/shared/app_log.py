"""The app's log file, and what happens to an error nothing else caught.

Without this, a windowed build has no stderr: every warning and traceback was
lost, and an exception on a background thread vanished with the thread
(hardening H3, docs/ARCHITECTURE.md "Errors and logging").

`setup` writes `lector.log` into the folder it is given (`logs/` next to
settings.json), rotated at `MAX_BYTES` with `BACKUP_COUNT` old files kept.
`install_exception_hooks` logs any uncaught exception and tells the reader in
a sentence, never with a traceback.

**The log never holds what the reader reads, says or highlights.** Lector's
own log calls never pass document text, OCR output, highlight text, search
queries or transcripts. Exceptions are the way such text could still get in
(a library quoting its input, an error from the OCR worker), so
`ContentFreeFormatter` writes each exception as its type and the frames it
passed through (file, line and function) and withholds its message, including
an exception passed as a log argument. Records from other libraries are not
written by Lector, so they get less trust still: pywebview, for one, formats
exceptions, tracebacks and values a page returned straight into its message
text. Theirs are cut to what holds no content (see `_library_text`).
"""
import logging
import logging.handlers
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

LOG_FILE = "lector.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"

# Lector's own records are kept from INFO up; everyone else's (pywebview,
# PIL, ...) only from WARNING, which is where their chatter stops.
_APP_LOGGER = "lector"
_WARNINGS_LOGGER = "py.warnings"

WITHHELD = "(message withheld)"

FATAL_TITLE = "Lector"
FATAL_MESSAGE = "Lector ran into a problem and has to close. Details were saved to the log."
# MessageBoxW flags: one OK button, the error icon.
_MB_OK_ICONERROR = 0x00000010

# One frame line of a traceback that arrives already printed as text (from
# pywebview, or the OCR worker's RemoteTraceback): kept, it holds no content.
# Strict on purpose, so a line of a message can't pass for one.
_FRAME_LINE = re.compile(r'^  File "[^"]+\.py", line \d+, in [\w<>.]+$')
# The line naming the exception, after a printed traceback's last frame. Only
# a name that looks like an exception class is believed; anything else could
# be the last line of a message.
_EXCEPTION_NAME = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Traceback|Group))(?::|$)"
)
_TRACEBACK_HEADER = "Traceback (most recent call last):"
# "path.py:12: DeprecationWarning: the message" -> where, and which category.
_WARNING_LINE = re.compile(r"^(.*?:\d+): ([A-Za-z_]\w*):")
# Libraries put the detail after a colon ("Error while processing x: <exc>").
_DETAIL_SEPARATOR = ": "
# Where pywebview starts quoting what a page returned to it.
_RETURNED_VALUE = "Returned value"
_MAX_LIBRARY_LINE = 200


class _RotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Keeps writing when a rollover fails, as it does on Windows while
    another program (an editor, a virus scanner) has the log open: the
    standard handler would drop every record until that program let go."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                self.stream = self._open()


def setup(log_dir: Path) -> logging.Handler | None:
    """Start writing `log_dir`/`lector.log`. Returns the handler, or None if
    the folder or file can't be made: a log is never a reason not to start."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = _RotatingFileHandler(
            log_dir / LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
    except OSError:
        log.warning("Could not open the log folder", exc_info=True)
        return None
    handler.setFormatter(ContentFreeFormatter(FORMAT))
    handler.addFilter(_keep_record)
    logging.getLogger().addHandler(handler)
    logging.getLogger(_APP_LOGGER).setLevel(logging.INFO)
    logging.captureWarnings(True)
    return handler


def _is_lectors(record: logging.LogRecord) -> bool:
    return record.name == _APP_LOGGER or record.name.startswith(_APP_LOGGER + ".")


def _keep_record(record: logging.LogRecord) -> bool:
    if _is_lectors(record):
        return record.levelno >= logging.INFO
    return record.levelno >= logging.WARNING


class ContentFreeFormatter(logging.Formatter):
    """A formatter that writes exceptions without their messages, and other
    libraries' records cut to what holds no content (see the module
    docstring). Formats a copy, so a record other handlers also see is left
    as it was.

    For Lector's own records this strips exception *objects*, as the message
    or an argument; a caller that turned one into a string itself is
    responsible for what is in it.
    """

    def format(self, record: logging.LogRecord) -> str:
        clean = logging.makeLogRecord(record.__dict__)
        # Another handler may already have cached the full traceback here.
        clean.exc_text = None
        clean.msg = _type_only(clean.msg)
        if clean.args:
            clean.args = _without_exception_text(clean.args)
        if not _is_lectors(clean):
            clean.msg, clean.args = _library_text(clean.name, clean.getMessage()), None
        return super().format(clean)

    def formatException(self, ei) -> str:
        exc_type, exc, tb = ei
        if exc is None:
            return exc_type.__name__ if exc_type else ""
        return "\n".join(_exception_lines(exc, tb, set()))


def _without_exception_text(args):
    """`args` with any exception in it replaced by its type name."""
    if isinstance(args, dict):
        return {key: _type_only(value) for key, value in args.items()}
    if isinstance(args, tuple):
        return tuple(_type_only(value) for value in args)
    return _type_only(args)


def _type_only(value):
    if isinstance(value, BaseException):
        return f"{_type_name(value)} {WITHHELD}"
    return value


def _type_name(exc: BaseException) -> str:
    cls = type(exc)
    if cls.__module__ in ("builtins", "__main__"):
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def _exception_lines(exc: BaseException, tb, seen: set[int]) -> list[str]:
    """Like `traceback.format_exception`, cause first, but each exception is
    its type and frames only. `tb` is the traceback to print for `exc`."""
    if id(exc) in seen:
        return []
    seen.add(id(exc))
    lines: list[str] = []
    if exc.__cause__ is not None:
        lines += _exception_lines(exc.__cause__, exc.__cause__.__traceback__, seen)
        lines.append("The above exception was the direct cause of:")
    elif exc.__context__ is not None and not exc.__suppress_context__:
        lines += _exception_lines(exc.__context__, exc.__context__.__traceback__, seen)
        lines.append("During handling of the above exception, another exception occurred:")
    lines.append(_TRACEBACK_HEADER)
    for frame, lineno in traceback.walk_tb(tb):
        code = frame.f_code
        lines.append(f'  File "{code.co_filename}", line {lineno}, in {code.co_name}')
    if type(exc).__name__ == "RemoteTraceback":
        # The OCR worker's traceback, already text: its frames and type.
        remote = str(exc).splitlines()
        if _TRACEBACK_HEADER in remote:
            lines += ["Worker traceback:", *_first_traceback_block(remote)]
    lines.append(f"{_type_name(exc)} {WITHHELD}")
    if isinstance(exc, BaseExceptionGroup):
        for number, inner in enumerate(exc.exceptions, 1):
            lines.append(f"Sub-exception {number}:")
            lines += _exception_lines(inner, inner.__traceback__, seen)
    return lines


def _library_text(name: str, text: str) -> str:
    """Another library's message, cut to what holds no content:
    - a printed traceback keeps its frames and its exception's type;
    - a warning keeps where it came from and its category;
    - anything else its first line, up to any detail after a colon and any
      value a page returned."""
    lines = text.splitlines()
    if _TRACEBACK_HEADER in lines:
        return "\n".join([_TRACEBACK_HEADER, *_first_traceback_block(lines)])
    first = lines[0] if lines else ""
    if name == _WARNINGS_LOGGER:
        match = _WARNING_LINE.match(first)
        return f"{match.group(1)}: {match.group(2)} {WITHHELD}" if match else f"Warning {WITHHELD}"
    if _RETURNED_VALUE in first:
        first = first[: first.index(_RETURNED_VALUE)] + f"{_RETURNED_VALUE} {WITHHELD}"
    elif _DETAIL_SEPARATOR in first:
        first = first[: first.index(_DETAIL_SEPARATOR)] + f": {WITHHELD}"
    return first[:_MAX_LIBRARY_LINE]


def _first_traceback_block(lines: list[str]) -> list[str]:
    """The frames and exception type of the first traceback printed in
    `lines`. Everything after its exception line may be that exception's
    message, however much it looks like more traceback, so it is dropped;
    a chained traceback loses its later parts, never its privacy."""
    start = lines.index(_TRACEBACK_HEADER) + 1
    kept: list[str] = []
    for line in lines[start:]:
        if _FRAME_LINE.match(line):
            kept.append(line)
        elif line.startswith("    "):
            continue  # the source line under a frame
        else:
            match = _EXCEPTION_NAME.match(line)
            kept.append(f"{match.group(1) if match else 'Exception'} {WITHHELD}")
            return kept
    kept.append(f"Exception {WITHHELD}")
    return kept


def install_exception_hooks(
    on_background_error: Callable[[], None], on_fatal: Callable[[], None]
) -> Callable[[], None]:
    """Log every uncaught exception, then tell the reader.

    A background thread's error calls `on_background_error` (the page shows a
    toast); the main thread's, which ends the app, calls `on_fatal`. An
    exception Python could only ignore (raised in `__del__`, or in a C
    library's callback, such as the microphone's) is logged, nothing more.
    Ctrl+C and `SystemExit` are left to Python. Returns a function that puts
    the previous hooks back (tests use it).
    """
    previous_sys_hook = sys.excepthook
    previous_thread_hook = threading.excepthook
    previous_unraisable_hook = sys.unraisablehook

    def sys_hook(exc_type, exc, tb) -> None:
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            log.critical("Lector stopped on an unexpected error", exc_info=(exc_type, exc, tb))
            _call_quietly(on_fatal)
        previous_sys_hook(exc_type, exc, tb)

    def thread_hook(args) -> None:
        if args.exc_type is SystemExit:
            previous_thread_hook(args)
            return
        name = args.thread.name if args.thread is not None else "unknown"
        log.error(
            "A background task (%s) stopped on an unexpected error",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        _call_quietly(on_background_error)

    def unraisable_hook(unraisable) -> None:
        # The object's type only: its repr could be anything.
        where = type(unraisable.object).__name__ if unraisable.object is not None else "unknown"
        log.error(
            "An error was ignored (in a %s)",
            where,
            exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
        )

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook

    def restore() -> None:
        sys.excepthook = previous_sys_hook
        threading.excepthook = previous_thread_hook
        sys.unraisablehook = previous_unraisable_hook

    return restore


def _call_quietly(callback: Callable[[], None]) -> None:
    """Run a hook's callback; one that fails is logged, never raised, so an
    exception hook can't end up handling its own error."""
    try:
        callback()
    except Exception:
        log.warning("Could not tell the reader about an error", exc_info=True)


def show_fatal_message() -> None:
    """A native message box on Windows, so a crash doesn't just make the
    window vanish. Elsewhere there is no box without a new dependency, so the
    log is all there is."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, FATAL_MESSAGE, FATAL_TITLE, _MB_OK_ICONERROR)
    except Exception:
        log.warning("Could not show the crash message", exc_info=True)
