"""What a file tool's dialog shows before its job starts: the PDF or files
it works on, where it saves and under what name, and its previews
(docs/ARCHITECTURE.md § "File tools"). Also the checked source a job is
then given, which Compress and the jobs ask for."""
import os

from lector.bridge import checks
from lector.bridge.core import Controller, log
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import destinations as tool_destinations
from lector.features.file_tools import engine as tool_engine
from lector.features.file_tools import formats as tool_formats
from lector.features.file_tools import messages as tool_messages
from lector.features.file_tools import naming as tool_naming
from lector.features.file_tools import tools as file_tools
from lector.features.reading import open_errors, page_changes, safe_write
from lector.features.settings import store as settings

# A file tool's `source` meaning "the PDF open in the reader, as it is now".
TOOL_SOURCE_OPEN = "open"

# The tools that work on one PDF, described by `file_tool_info`.
ONE_PDF_TOOLS = (tool_messages.COMPRESS, tool_messages.SEARCHABLE, tool_messages.EXPORT, tool_messages.SPLIT)

# Most files one Convert to PDF takes, so a runaway list can't be sent.
MAX_CONVERT_INPUTS = 500

# How long describing a file (in the worker, behind any OCR read) may take.
DESCRIBE_TIMEOUT_S = 60


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


class FileToolSetupController(Controller):
    def __init__(self, core):
        super().__init__(core)
        # A Home PDF's password, as (path, password): held, in memory only,
        # for the job on that file, and replaced by the next file chosen.
        self.tool_source: tuple[str, str | None] | None = None
        # Combine files: the passwords of the protected PDFs in its list, by
        # file (normcase'd path), in memory only, until its dialog closes.
        self.combine_passwords: dict[str, str] = {}

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
        if tool == tool_messages.SPLIT:
            if not info.get("can_change_pages", True):
                return {"ok": False, "error": page_changes.BLOCK_MESSAGES[page_changes.RESTRICTED]}
            info["split_every"] = tool_naming.default_split_every(info["pages"])
        return info

    def describe_home_source(self, path, password: str | None) -> dict:
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
        self.tool_source = (path, password) if info.get("needs_pass") else None
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

    def suggest_tool_name(self, tool: str, source: str, folder: str, inputs=None) -> dict:
        """The Name field's suggestion in `folder`: "Report (compressed)",
        or the first free "… (2)" there. `source` is "open", a path, or
        Convert's or Combine's first file; those two also send their whole
        list (`inputs`), which their name is made from
        (`destinations.list_stem`: "Report (combined)", "Images 14-11-2026")."""
        origin = self._tool_origin(source)
        if tool not in tool_messages.TOOLS or origin is None:
            return {"stem": "", "extension": ""}
        if not isinstance(folder, str) or not os.path.isdir(folder):
            folder = os.path.dirname(origin)
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
        return tool_naming.split_preview(mode, value, count, stem)

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
        held = self.tool_source
        password = held[1] if held and safe_write.same_file(held[0], source) else None
        return file_tools.Source(source, None, password)
