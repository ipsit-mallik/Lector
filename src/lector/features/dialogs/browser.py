"""Directory listing backing the in-app Open/Save-As dialogs (Milestone 8.7).

`docs/ARCHITECTURE.md`'s "Voice context router" section explains why the
native `create_file_dialog()` pywebview used to call had to go: the router
can only see and drive the app's own UI, not OS chrome, so browsing for a
file now has to happen as regular DOM rows the router can number and pick
(the same Milestone 8.6 numbered-overlay picker mechanic), which means the
directory contents have to be listed in Python and handed to the frontend
rather than delegated to the OS.
"""
from pathlib import Path

from lector.features.settings import store as settings

PDF_EXTENSION = ".pdf"


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
    try:
        raw_entries = list(resolved.iterdir())
    except OSError as exc:
        return {
            "path": str(resolved),
            "parent": _parent_of(resolved),
            "entries": [],
            "error": str(exc),
        }

    folders = sorted(
        (e for e in raw_entries if e.is_dir() and not e.name.startswith(".")),
        key=lambda e: e.name.lower(),
    )
    files = sorted(
        (e for e in raw_entries if e.is_file() and e.suffix.lower() == PDF_EXTENSION),
        key=lambda e: e.name.lower(),
    )
    entries = [{"name": e.name, "path": str(e), "is_dir": True} for e in folders]
    entries += [{"name": e.name, "path": str(e), "is_dir": False} for e in files]
    return {"path": str(resolved), "parent": _parent_of(resolved), "entries": entries, "error": None}


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
