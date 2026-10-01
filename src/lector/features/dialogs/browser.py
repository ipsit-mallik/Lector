"""Directory listing backing the in-app Open/Save-As dialogs (Milestone 8.7).

`docs/ARCHITECTURE.md`'s "Voice context router" section explains why the
native `create_file_dialog()` pywebview used to call had to go: the router
can only see and drive the app's own UI, not OS chrome, so browsing for a
file now has to happen as regular DOM rows the router can number and pick
(the same Milestone 8.6 numbered-overlay picker mechanic), which means the
directory contents have to be listed in Python and handed to the frontend
rather than delegated to the OS.
"""
import ctypes
import platform
import stat
import string
import uuid
from pathlib import Path

from lector.features.settings import store as settings

PDF_EXTENSION = ".pdf"

# Home's children surfaced as Quick Access chips, in display order — each is
# only offered if it actually exists on this machine.
QUICK_ACCESS_FOLDERS = ("Desktop", "Documents", "Downloads")

# Windows Known Folder IDs for the folders above (Microsoft's FOLDERID_Desktop,
# FOLDERID_Documents, FOLDERID_Downloads).
_WINDOWS_KNOWN_FOLDER_IDS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
}


def list_directory(path: str) -> dict:
    """Folders and PDFs directly inside `path` — folders first, then files,
    each alphabetically (case-insensitive) — the order the Open/Save-As rows
    are drawn in and numbered for voice picking.

    Non-PDF files are omitted, matching the native dialog's own
    `file_types=("PDF Files (*.pdf)",)` filter. A directory that cannot be
    listed (permissions, deleted since) reports an `error` string instead of
    raising, so the dialog stays open with an empty list and a message rather
    than crashing the bridge call.
    """
    resolved = Path(path).resolve()
    parent = _parent_of(resolved)
    try:
        raw_entries = list(resolved.iterdir())
    except OSError as exc:
        return {
            "path": str(resolved),
            "parent": parent,
            "entries": [],
            "error": str(exc),
        }

    folders = sorted(
        (e for e in raw_entries if e.is_dir() and not _is_hidden(e)),
        key=lambda e: e.name.lower(),
    )
    files = sorted(
        (e for e in raw_entries if e.is_file() and e.suffix.lower() == PDF_EXTENSION),
        key=lambda e: e.name.lower(),
    )
    entries = [{"name": e.name, "path": str(e), "is_dir": True} for e in folders]
    entries += [{"name": e.name, "path": str(e), "is_dir": False} for e in files]
    return {
        "path": str(resolved),
        "parent": parent,
        "entries": entries,
        "error": None,
    }


def _is_hidden(path: Path) -> bool:
    """Dot-folders everywhere, plus Windows' hidden-flagged folders
    (`$RECYCLE.BIN`, `System Volume Information`) — none of them are places a
    reader keeps PDFs, and they are not dot-prefixed so the name check alone
    misses them. Only the hidden flag counts: the system flag by itself is
    also set on customised user folders (desktop.ini), which must stay
    browsable, and the noisy system folders above are hidden too.
    `st_file_attributes` only exists on Windows, so it reads as 0 elsewhere."""
    if path.name.startswith("."):
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_HIDDEN)


def list_quick_access() -> dict:
    """Home plus its Desktop/Documents/Downloads children, for the dialogs'
    Quick Access chips. A missing child is omitted rather than shown broken.
    Same entry shape as `list_directory()`'s rows, so the frontend can treat
    them as ordinary folders; there is no `parent`/`error` since this is a
    fixed short list, not a directory listing."""
    home = Path.home()
    entries = [{"name": "Home", "path": str(home), "is_dir": True}]
    for folder in QUICK_ACCESS_FOLDERS:
        candidate = _known_folder(folder)
        if candidate.is_dir():
            entries.append({"name": folder, "path": str(candidate), "is_dir": True})
    return {"entries": entries}


def _known_folder(name: str) -> Path:
    """Where the user's Desktop/Documents/Downloads really are. Windows asks
    the shell (`SHGetKnownFolderPath`), because OneDrive folder redirection
    moves them out of the home folder and `home / name` would then point at an
    empty or stale folder. Everywhere else — and if the shell lookup fails —
    it is `home / name`."""
    fallback = Path.home() / name
    guid_text = _WINDOWS_KNOWN_FOLDER_IDS.get(name)
    if platform.system() != "Windows" or guid_text is None:
        return fallback

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    guid = _Guid.from_buffer_copy(uuid.UUID(guid_text).bytes_le)
    path_ptr = ctypes.c_void_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result != 0 or not path_ptr.value:
            return fallback
        return Path(ctypes.wstring_at(path_ptr.value))
    except (AttributeError, OSError):
        return fallback
    finally:
        if path_ptr.value:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)


def list_drives() -> dict:
    """Available drives/volumes — the top-level nodes of the dialogs'
    directory tree; see docs/DESIGN_SYSTEM.md's open_dialog spec.

    Windows: drive letters A-Z that actually exist, checked by testing each
    root's existence directly (no `psutil` dependency, and `os.listdrives()`
    isn't available on this project's Python floor of 3.11 — it's 3.12+).
    Mac (and other POSIX platforms): there's no drive-letter concept, so this
    surfaces `/` plus whatever is mounted under `/Volumes`.
    """
    if platform.system() == "Windows":
        drives = [
            f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()
        ]
    else:
        drives = ["/"]
        volumes = Path("/Volumes")
        if volumes.is_dir():
            # /Volumes/<boot volume> is a symlink back to "/", already listed
            # above — skipping anything that resolves to it avoids a second,
            # identically-labelled node for the same drive.
            filesystem_root = Path("/").resolve()
            drives += sorted(
                str(v)
                for v in volumes.iterdir()
                if v.is_dir() and not v.name.startswith(".") and v.resolve() != filesystem_root
            )
    entries = [{"name": d, "path": d, "is_dir": True} for d in drives]
    return {"path": "Drives", "parent": None, "entries": entries, "error": None}


def _parent_of(path: Path) -> str | None:
    parent = path.parent
    return str(parent) if parent != path else None


def default_open_dir() -> str:
    """Where the Open dialog starts: the most recent file's folder, falling
    back to the user's home directory when there is no recent file yet or
    its folder is gone — the same fallback a fresh install's native file
    picker would land on."""
    recent = settings.get_recent_files()
    if recent:
        candidate = Path(recent[0]["path"]).parent
        if candidate.is_dir():
            return str(candidate)
    return str(Path.home())


def default_save_dir(suggested_path: str) -> str:
    """Where the Save-As dialog starts: the folder `suggested_path` (from
    `Api._suggested_copy_path()`) already names, falling back to home if
    that folder no longer exists."""
    candidate = Path(suggested_path).parent
    return str(candidate) if candidate.is_dir() else str(Path.home())
