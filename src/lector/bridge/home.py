"""Home's Recent and Favorites lists, their thumbnails, and "Show in
folder" for a listed file, a tool's output or the open PDF
(docs/ARCHITECTURE.md § "Bridge layout")."""
import os

from lector.bridge.core import Controller
from lector.features.home import recent as home_recent
from lector.features.settings import store as settings


class HomeController(Controller):
    def __init__(self, core, file_jobs):
        super().__init__(core)
        self._file_jobs = file_jobs
        # The last Recent entry removed, as (index, raw entry), kept so the
        # removal toast's Undo can put it back with its reading position.
        # Lives here rather than in the frontend so no raw settings entry
        # has to make a round trip through JS.
        self._last_removed_recent: tuple[int, dict] | None = None

    def get_recent_files(self) -> list[dict]:
        return home_recent.list_recent()

    def get_thumbnails(self, paths: list[str]) -> dict[str, str | None]:
        """The first-page thumbnails (base64 PNG, or None) of rows the list
        handed back with `thumbnail_pending`, by path. A few per call: each
        bridge round trip costs several times what rendering one thumbnail
        does. Only for files on the Recent or Favorites list, so the frontend
        can't use this to read an arbitrary path."""
        listed = home_recent.listed_paths()
        return {
            path: home_recent.load_thumbnail(path) if path in listed else None
            for path in paths
        }

    def remove_recent_file(self, path: str) -> dict:
        """Drops `path` from the Recent list only (Milestone 8.9) — never
        touches the file on disk, per docs/PRD.md's scope note. Returns
        `{"entries": [...], "removed": bool}`: the refreshed list so the
        frontend can re-render without a second round trip, and whether
        `path` was actually listed (False for a stale view), so it only
        offers Undo for a removal that happened."""
        removal = settings.remove_recent_file(path)
        # Kept as-is when nothing was removed, so a toast still on screen
        # from the previous removal keeps a working Undo.
        if removal is not None:
            self._last_removed_recent = removal
        return {"entries": home_recent.list_recent(), "removed": removal is not None}

    def undo_remove_recent_file(self) -> dict:
        """Puts the most recently removed Recent entry back where it was
        (the removal toast's Undo). One level only. Returns
        `{"entries": [...], "status": str}` where status is
        `settings.RESTORED`, `ALREADY_LISTED`, `LIST_FULL`, or None when
        there was nothing to undo."""
        status = None
        if self._last_removed_recent is not None:
            index, entry = self._last_removed_recent
            self._last_removed_recent = None
            status = settings.restore_recent_file(index, entry)
        return {"entries": home_recent.list_recent(), "status": status}

    def get_favorites(self) -> list[dict]:
        """The Favorites view's entries, in the same shape as
        `get_recent_files`."""
        return home_recent.list_favorites()

    def set_favorite(self, path: str, favorite: bool) -> dict:
        """Pins or unpins `path`. Only a file the reader can see — one in
        Recent or already a favorite — can be pinned, so the frontend can't
        use this to put an arbitrary path into settings.json. Returns
        `{"favorite": bool, "error": str | None}`, `favorite` being the state
        after the call."""
        if favorite:
            listed = any(e["path"] == path for e in settings.get_recent_files())
            if not listed and not settings.is_favorite(path):
                return {"favorite": False, "error": "That file is no longer in Recent."}
            settings.add_favorite(path)
        else:
            settings.remove_favorite(path)
        return {"favorite": settings.is_favorite(path), "error": None}

    def show_in_folder(self, path: str) -> dict:
        """Opens the OS file manager with `path` selected (Recent list's
        "Show in folder"), or, for a folder a file tool made (Export as
        images), on that folder. Only for a path in the Recent list or one a
        file tool wrote this session, still on disk — the frontend can't use
        this to launch the file manager on an arbitrary location. Returns
        `{"error": str | None}`."""
        if not isinstance(path, str):
            return {"error": "That file is no longer in Recent."}
        tool_output = self._file_jobs.is_tool_output(path)
        if not tool_output and not any(e["path"] == path for e in settings.get_recent_files()):
            return {"error": "That file is no longer in Recent."}
        is_folder = tool_output and os.path.isdir(path)
        if not is_folder and not os.path.isfile(path):
            return {"error": "That file has been moved or deleted."}
        try:
            if is_folder:
                home_recent.reveal_in_file_manager(path, open_folder=True)
            else:
                home_recent.reveal_in_file_manager(path)
        except OSError:
            return {"error": "Couldn't open the folder."}
        return {"error": None}

    def show_open_document_in_folder(self) -> dict:
        """The reader's More menu's "Show in folder", for the open PDF."""
        if not self._doc.is_open or not os.path.isfile(self._doc.path or ""):
            return {"error": "That file has been moved or deleted."}
        try:
            home_recent.reveal_in_file_manager(self._doc.path)
        except OSError:
            return {"error": "Couldn't open the folder."}
        return {"error": None}
