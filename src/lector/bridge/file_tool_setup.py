"""What a file tool's dialog shows before its job starts: the PDF or files
it works on, where it saves and under what name, and its previews
(docs/ARCHITECTURE.md § "File tools"). Also the checked source a job is
then given, which Compress and the jobs ask for."""
import os
import threading

from lector.bridge import checks
from lector.bridge.core import Controller, log
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import destinations as tool_destinations
from lector.features.file_tools import engine as tool_engine
from lector.features.file_tools import formats as tool_formats
from lector.features.file_tools import messages as tool_messages
from lector.features.file_tools import naming as tool_naming
from lector.features.file_tools import quick_look
from lector.features.file_tools import tools as file_tools
from lector.features.file_tools import trash as tool_trash
from lector.features.reading import ocr, open_errors, page_changes
from lector.features.settings import store as settings

# A file tool's `source` meaning "the PDF open in the reader, as it is now".
TOOL_SOURCE_OPEN = "open"

# The tools that work on one PDF, described by `file_tool_info`.
ONE_PDF_TOOLS = (tool_messages.COMPRESS, tool_messages.SEARCHABLE, tool_messages.EXPORT, tool_messages.SPLIT)

# Most files one Convert to PDF takes, so a runaway list can't be sent.
MAX_CONVERT_INPUTS = 500

# How long describing a file (in the worker, behind any OCR read) may take.
DESCRIBE_TIMEOUT_S = 60
# How long Make searchable's look at every page may take, in the worker.
ANALYSIS_TIMEOUT_S = 300


def _password_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class BadToolRequest(Exception):
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


def _split_range(value) -> tuple[int, int]:
    """Split's one range as the page sends it, [first, last], checked; (1, 1)
    for anything else (only a suggestion is made from it)."""
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in value)
        and value[0] <= value[1]
    ):
        return value[0], value[1]
    return 1, 1


class FileToolSetupController(Controller):
    def __init__(self, core):
        super().__init__(core)
        # Home PDFs' passwords, by file (normcase'd path): held, in memory
        # only, for the jobs on those files, and let go when a job ends. By
        # file, since the dialog looks at each PDF chosen in its list: a look
        # at another file mustn't drop the one already unlocked.
        self.tool_passwords: dict[str, str] = {}
        # Combine files: the passwords of the protected PDFs in its list, by
        # file (normcase'd path), in memory only, until its dialog closes.
        self.combine_passwords: dict[str, str] = {}
        # Make searchable's look at every page (`file_tool_analysis`), the
        # newest asked for: {path, abort, future}. One for another file stops
        # it; one for the same file waits on the same work.
        self._analysis: dict | None = None
        self._analysis_lock = threading.Lock()

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
        file's job; a missing one as `reason` "missing". Split's also has
        `split_every`, its "Every N pages" to start with."""
        if tool not in ONE_PDF_TOOLS:
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
            outcome = self.describe_home_source(source, password)
            if not outcome.get("ok", True):
                return outcome
            path, info = source, outcome
            info.update(unsaved=0, dirty=False, current_page=None, current_textless=False)
        if "textless" in info:
            info["textless_pages"] = [i + 1 for i in info["textless"]]
            info["textless"] = len(info["textless"])
        else:
            # From Home only the quick look is made here; Make searchable
            # asks for its pages with no text with `file_tool_analysis`.
            info.update(textless=None, textless_pages=None, analysis_pending=tool == tool_messages.SEARCHABLE)
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
        if tool == tool_messages.SPLIT:
            if not info.get("can_change_pages", True):
                return {"ok": False, "error": page_changes.BLOCK_MESSAGES[page_changes.RESTRICTED]}
            info["split_every"] = tool_naming.default_split_every(info["pages"])
            # How many files the preview names before "and N more", so the
            # dialog can keep room for the preview before it arrives.
            info["preview_named"] = tool_naming.SPLIT_PREVIEW_NAMED
            # "Move the original to the Recycle Bin afterwards" ("…Trash" on a Mac).
            info["trash_name"] = tool_trash.TRASH_NAME
        return info

    def describe_home_source(self, path, password: str | None) -> dict:
        """A PDF from Home at a glance (`quick_look`, in this process, a few
        milliseconds whatever its size): never queued behind the worker."""
        if not isinstance(path, str) or not os.path.isfile(path):
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.MISSING], "reason": "missing"}
        try:
            info = quick_look.look(path, password)
        except quick_look.Unreadable:
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.NOT_A_PDF], "reason": "not_a_pdf"}
        except Exception:
            log.exception("Couldn't describe a PDF for a file tool")
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.UNKNOWN], "reason": "unknown"}
        if info.get("locked"):
            reason = open_errors.PASSWORD if password is None else open_errors.WRONG_PASSWORD
            return {"ok": False, "reason": reason, "error": open_errors.MESSAGES[reason], "name": os.path.basename(path)}
        # Held for this file's job only.
        if info.get("needs_pass") and password is not None:
            self.tool_passwords[_password_key(path)] = password
        return info

    def held_password(self, path: str) -> str | None:
        """The password a Home PDF was unlocked with, or None."""
        return self.tool_passwords.get(_password_key(path)) if isinstance(path, str) else None

    def forget_tool_passwords(self) -> None:
        """A job has ended, or the tool dialog closed: the passwords held for
        it go."""
        self.tool_passwords = {}

    def forget_file_tool_passwords(self) -> dict:
        """The tool dialog closed without a job holding them: every password
        a Home PDF was unlocked with in it goes."""
        self.forget_tool_passwords()
        return {"ok": True}

    # ---- Make searchable's look at every page, and the worker --------------

    def file_tool_analysis(self, source: str) -> dict:
        """Make searchable's pages with no text, for a PDF from Home (what
        `file_tool_info` leaves out there): `{"ok": True, "textless",
        "textless_pages"}` (1-based), `{"ok": False, "cancelled": True}` when
        a newer request or `cancel_file_tool_analysis` stopped it, or
        `{"ok": False, "error"}`. Runs in the worker. The newest request
        wins: one for another file stops the older at its next page and
        never waits behind it; one for the same file shares its work."""
        if not isinstance(source, str) or not os.path.isfile(source):
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.MISSING], "reason": "missing"}
        key = os.path.normcase(os.path.abspath(source))
        with self._analysis_lock:
            current = self._analysis
            # Only work still going is shared: a finished look (above all a
            # failed one, asked before the password was given) is asked again.
            sharing = (
                current is not None
                and current["path"] == key
                and not current["abort"].is_set()
                and not current["future"].done()
            )
            if sharing:
                future = current["future"]
            else:
                if current is not None:
                    current["abort"].set()
                password = self.held_password(source)
                abort = threading.Event()
                future = self._ocr.call(
                    tool_engine.textless_pages, (source, password), timeout=ANALYSIS_TIMEOUT_S, abort=abort
                )
                self._analysis = {"path": key, "abort": abort, "future": future}
        try:
            pages = future.result()
        except ocr.CallAborted:
            return {"ok": False, "cancelled": True}
        except tool_engine.SourceError:
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.NOT_A_PDF], "reason": "not_a_pdf"}
        except Exception:
            log.exception("Couldn't look for pages with no text")
            return {"ok": False, "error": open_errors.MESSAGES[open_errors.UNKNOWN], "reason": "unknown"}
        return {"ok": True, "textless": len(pages), "textless_pages": [i + 1 for i in pages]}

    def cancel_file_tool_analysis(self) -> dict:
        """Nobody wants the look any more (Back, another file, the dialog
        closed): queued, it is dropped; running, it stops at its next page."""
        with self._analysis_lock:
            current, self._analysis = self._analysis, None
        if current is not None:
            current["abort"].set()
        return {"ok": True}

    def warm_file_tool_worker(self) -> dict:
        """Start the worker process now (Home, once idle after launch, or on
        opening the Tools page), so the first tool used doesn't wait for it.
        Does nothing when it is already running."""
        self._ocr.warm()
        return {"ok": True}

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

    def suggest_tool_name(self, tool: str, source: str, folder: str, inputs=None) -> dict:
        """The Name field's suggestion in `folder`: "Report (compressed)",
        or the first free "… (2)" there. `source` is "open", a path, or
        Convert's or Combine's first file; those two also send their whole
        list (`inputs`), which their name is made from
        (`destinations.list_stem`: "Report (combined)", "Images 14-11-2026").
        Split with one range is "split_one", its `inputs` that range as
        [first, last]: "Report (pages 1–6)"."""
        origin = self._tool_origin(source)
        if tool not in tool_messages.TOOLS or origin is None:
            return {"stem": "", "extension": ""}
        if not isinstance(folder, str) or not os.path.isdir(folder):
            folder = os.path.dirname(origin)
        if tool == tool_messages.SPLIT_ONE:
            # Split with one range: `inputs` is that range, [first, last].
            first, last = _split_range(inputs)
            stem = tool_destinations.split_one_stem(origin, first, last, folder)
            return {"stem": stem, "extension": tool_destinations.extension(tool)}
        paths = self._tool_originals(source, inputs) if isinstance(inputs, list) else []
        if tool in (tool_messages.COMBINE, tool_messages.CONVERT) and paths:
            stem = tool_destinations.suggested_list_stem(tool, paths, folder)
        else:
            stem = tool_destinations.suggested_stem(tool, origin, folder)
        return {"stem": stem, "extension": tool_destinations.extension(tool)}

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
        count = checks.page_index(page_count)
        if not count:
            return {"error": "This PDF has no pages."}
        try:
            return {"pages": tool_naming.parse_page_ranges(text, count)}
        except tool_naming.RangeError as exc:
            return {"error": exc.message}

    def split_preview(self, source: str, mode: str, value: str, page_count: int) -> dict:
        """Split's live preview for what the dialog has chosen: `{count,
        parts, named, more, gaps}`, or `{"error"}` for the line under the
        field. Worked out by the parser the job uses (naming.py)."""
        count = checks.page_index(page_count)
        origin = self._tool_origin(source)
        if not count or origin is None:
            return {"error": tool_naming.ONE_PAGE, "count": 0}
        stem = os.path.splitext(os.path.basename(origin))[0] or "untitled"
        preview = tool_naming.split_preview(mode, value, count, stem)
        single = preview.get("single")
        if single:
            # The name it is saved under beside the original: "… (2)" when
            # that one is taken, as the Name field and the job say.
            free = tool_destinations.split_one_stem(origin, single["first"], single["last"], os.path.dirname(origin))
            single["stem"] = free
            preview["named"] = [{**preview["named"][0], "name": f"{free}{tool_destinations.PDF_EXTENSION}"}]
        return preview

    def describe_combine_inputs(self, paths: list[str]) -> list[dict]:
        """Each file Combine files was given: a PDF's page count, size and
        thumbnail (`reason` "password", with `error`, for one that needs a
        password: the page asks for it with `unlock_combine_input`), or an
        image's or e-book's as Convert describes it; `{"error"}` naming why
        a file can't be used."""
        if not isinstance(paths, list):
            return []
        return [self._describe_combine_input(path, None) for path in paths[:MAX_CONVERT_INPUTS]]

    def unlock_combine_input(self, path: str, password: str) -> dict:
        """A protected PDF in Combine's list, with the password the reader
        typed: described, and the password held (in memory only) for the
        job, or `reason` "wrong_password"."""
        if not isinstance(password, str):
            return {"path": path, "error": open_errors.MESSAGES[open_errors.UNKNOWN]}
        return self._describe_combine_input(path, password)

    def forget_combine_passwords(self) -> dict:
        """Combine's dialog has closed: let go of every password it held."""
        self.combine_passwords = {}
        return {"ok": True}

    def _describe_combine_input(self, path, password: str | None) -> dict:
        entry = {"path": path, "name": os.path.basename(path) if isinstance(path, str) else ""}
        if not isinstance(path, str) or not os.path.isfile(path):
            entry["error"] = f"Couldn't find {entry['name'] or 'that file'}."
            return entry
        is_pdf = tool_formats.extension(path) == dialog_browser.PDF_EXTENSION
        if not is_pdf and not tool_formats.is_convertible(path):
            entry["error"] = f"{entry['name']} can't be combined. Choose PDFs, images or e-books."
            return entry
        try:
            info = self._ocr.call(tool_engine.combine_describe, (path, password), timeout=DESCRIBE_TIMEOUT_S).result()
        except Exception:
            entry["error"] = f"{entry['name']} couldn't be read."
            return entry
        if info.get("locked"):
            reason = open_errors.PASSWORD if password is None else open_errors.WRONG_PASSWORD
            entry.update(reason=reason, error=open_errors.MESSAGES[reason])
            return entry
        if info.get("needs_pass"):
            self.combine_passwords[os.path.normcase(os.path.abspath(path))] = password
        entry.update(info)
        return entry

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

    def job_source(self, source) -> "file_tools.Source":
        if source == TOOL_SOURCE_OPEN:
            if not self._doc.is_open:
                raise BadToolRequest(tool_messages.SOURCE_UNREADABLE)
            data, password = self._doc.snapshot()
            return file_tools.Source(self._doc.path, data, password)
        if not isinstance(source, str) or not os.path.isfile(source):
            raise BadToolRequest(tool_messages.SOURCE_UNREADABLE)
        return file_tools.Source(source, None, self.held_password(source))
