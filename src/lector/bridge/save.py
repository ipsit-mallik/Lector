"""Saving the open PDF: over itself or as a copy, and what the Save-As
screen starts with (docs/ARCHITECTURE.md § "Saving: all or nothing")."""
import os

from lector.bridge.core import Controller, log
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import naming as tool_naming
from lector.features.reading import safe_write
from lector.features.settings import store as settings


class SaveController(Controller):
    def get_save_dialog_start(self) -> dict:
        """Where the in-app Save-As screen should start browsing and what
        filename to pre-fill — both derived from `_suggested_copy_path()`,
        the same suggestion the native dialog used to pre-fill."""
        suggested = self._suggested_copy_path()
        return {
            "dir": dialog_browser.default_save_dir(suggested),
            "filename": os.path.basename(suggested),
        }

    def perform_save(self, mode: str, path: str | None = None) -> dict:
        """`path` is required when `mode` is `settings.COPY` — the in-app
        Save-As screen (Milestone 8.7) gathers it via `browse_directory()`/
        `get_save_dialog_start()` before calling this, the same way the
        native dialog used to hand back a chosen path.

        A failure comes back as `error`, a sentence for the reader saying what
        happened and that the file on disk is unchanged — never the library's
        own text, which names paths and MuPDF error codes."""
        try:
            if mode == settings.OVERWRITE:
                self._doc.save_overwrite()
            else:
                if not path:
                    return {"ok": False, "cancelled": True}
                self._doc.save_copy(path)
            return {"ok": True, "state": self._state()}
        except safe_write.SaveError as exc:
            if exc.closes_document:
                # The PDF couldn't be opened again, so no document is left
                # open: Reading has to leave.
                return {"ok": False, "error": exc.message, "closed": True}
            return {"ok": False, "error": exc.message}
        except Exception:
            log.exception("Saving the PDF failed (%s)", mode)
            return {"ok": False, "error": safe_write.MESSAGES[safe_write.UNKNOWN]}

    def describe_save_target(self, path: str) -> dict:
        """What the Save-As screen needs to know about a chosen path before
        saving there: whether a file is already there (the reader is asked
        before it is replaced), and whether that file is the open PDF itself
        (which a copy refuses, so no "replace?" question is asked for it).
        `name` and `folder` are what that question shows."""
        return {
            "exists": os.path.isfile(path),
            "is_open_document": self._doc.is_open_document(path),
            "name": os.path.basename(path),
            "folder": os.path.dirname(path),
        }

    def _suggested_copy_path(self) -> str:
        """`<name> (highlighted).pdf` beside the original, or, when that is
        taken, the first free `<name> (highlighted) (2).pdf`, (3), …, so the
        suggestion never names a file that is already there."""
        return tool_naming.free_file_name(self._doc.path or "untitled.pdf", tool_naming.HIGHLIGHTED)
