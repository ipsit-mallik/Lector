"""Persisted app-level settings via a local JSON file.

docs/TECH_STACK.md rules out a database for app state. Under PySide6 this was
backed by `QSettings` (registry on Windows, a plist on Mac); pywebview has no
equivalent, so this is now a plain JSON file in the OS's standard per-user app
data location — "a straightforward substitution, not a design change" per
docs/TECH_STACK.md. Behavior (keys, defaults, the 10-item recent-files cap)
is unchanged from the QSettings-backed version.
"""
import datetime
import json
import os
import sys
from pathlib import Path

from lector.shared.theme import THEME_ORDER

ASK = "ask"
COPY = "copy"
OVERWRITE = "overwrite"

# How a PDF opens when it is reopened later (the Settings > "When I reopen a
# PDF" preference). CONTINUE restores the page and layout the reader left the
# document on; START always opens at page 1 in book layout.
CONTINUE = "continue"
START = "start"

BOOK = "book"
STRIP = "strip"

_SAVE_BEHAVIOR_KEY = "save_behavior"
_THEME_KEY = "theme"
_REOPEN_BEHAVIOR_KEY = "reopen_behavior"
_RECENT_FILES_KEY = "recent_files"
RECENT_FILES_CAP = 10

# Voice activation (docs/PRD.md: "independently toggleable"). Two separate
# booleans rather than one three-valued mode, because the PRD's requirement
# is precisely that they are not alternatives: both on, both off, or either
# alone all have to be expressible, and a single "mode" key could not say
# "both" without growing a value that means the same thing as two flags.
_PUSH_TO_TALK_KEY = "push_to_talk_enabled"
_WAKE_PHRASE_KEY = "wake_phrase_enabled"

# What an install starts with before onboarding says otherwise: the key,
# which opens the microphone only while it is held. Nothing is silently
# always-on until the reader asks for it.
_DEFAULT_PUSH_TO_TALK = True
_DEFAULT_WAKE_PHRASE = False


def _settings_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Lector"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Lector"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "lector"


def _settings_path() -> Path:
    return _settings_dir() / "settings.json"


def _load() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_save_behavior() -> str:
    value = _load().get(_SAVE_BEHAVIOR_KEY, ASK)
    return value if value in (ASK, COPY, OVERWRITE) else ASK


def set_save_behavior(mode: str) -> None:
    if mode not in (ASK, COPY, OVERWRITE):
        raise ValueError(f"invalid save behavior: {mode!r}")
    data = _load()
    data[_SAVE_BEHAVIOR_KEY] = mode
    _save(data)


def get_theme() -> str:
    value = _load().get(_THEME_KEY, "light")
    return value if value in THEME_ORDER else "light"


def set_theme(name: str) -> None:
    if name not in THEME_ORDER:
        raise ValueError(f"invalid theme: {name!r}")
    data = _load()
    data[_THEME_KEY] = name
    _save(data)


def get_reopen_behavior() -> str:
    value = _load().get(_REOPEN_BEHAVIOR_KEY, CONTINUE)
    return value if value in (CONTINUE, START) else CONTINUE


def set_reopen_behavior(mode: str) -> None:
    if mode not in (CONTINUE, START):
        raise ValueError(f"invalid reopen behavior: {mode!r}")
    data = _load()
    data[_REOPEN_BEHAVIOR_KEY] = mode
    _save(data)


def get_voice_activation() -> dict:
    """Which activation modes are enabled, as `{"push_to_talk", "wake_phrase"}`.

    Returned together because they are read together everywhere — the UI's
    hint text, the engine's idle listening, and the key handler all depend on
    the pair, and two bridge round-trips to answer one question is worse than
    one.
    """
    data = _load()
    return {
        "push_to_talk": _as_bool(data.get(_PUSH_TO_TALK_KEY), _DEFAULT_PUSH_TO_TALK),
        "wake_phrase": _as_bool(data.get(_WAKE_PHRASE_KEY), _DEFAULT_WAKE_PHRASE),
    }


def set_voice_activation(push_to_talk: bool, wake_phrase: bool) -> None:
    """Store both flags. Either, both, or neither may be on — docs/PRD.md
    makes them independent, and "neither" is a supported choice, not an error
    state: it means voice off, with the app still fully usable by mouse and
    keyboard."""
    data = _load()
    data[_PUSH_TO_TALK_KEY] = bool(push_to_talk)
    data[_WAKE_PHRASE_KEY] = bool(wake_phrase)
    _save(data)


def _as_bool(value, default: bool) -> bool:
    """A stored flag, falling back to `default` when it is missing or was
    hand-edited into something that is not a boolean — settings.json is a
    plain file a reader can open, so it cannot be assumed well-formed."""
    return value if isinstance(value, bool) else default


def get_recent_files() -> list[dict]:
    """Most-recent-first list of {"path": str, "opened_at": ISO 8601 str}."""
    raw = _load().get(_RECENT_FILES_KEY, [])
    if not isinstance(raw, list):
        return []
    return [
        entry for entry in raw
        if isinstance(entry, dict) and "path" in entry and "opened_at" in entry
    ]


def add_recent_file(path: str) -> None:
    """Move `path` to the front, deduping, capped at RECENT_FILES_CAP entries.

    Any reading position already recorded for `path` is carried over onto the
    refreshed entry — reopening a file must not be what erases the position
    the reader is about to be restored to.
    """
    existing = next((e for e in get_recent_files() if e["path"] == path), None)
    entries = [e for e in get_recent_files() if e["path"] != path]
    entry = {"path": path, "opened_at": datetime.datetime.now().isoformat()}
    if existing is not None:
        for key in (_POSITION_PAGE_KEY, _POSITION_LAYOUT_KEY, _POSITION_ZOOM_KEY):
            if key in existing:
                entry[key] = existing[key]
    entries.insert(0, entry)
    data = _load()
    data[_RECENT_FILES_KEY] = entries[:RECENT_FILES_CAP]
    _save(data)


# Reading position rides along on the recent-files entry rather than living in
# its own map: it is only ever needed for a file the reader can actually get
# back to from the Home screen, so tying its lifetime to the 10-item recent
# list means stale positions for long-forgotten files are pruned for free
# instead of accumulating in settings.json forever.
_POSITION_PAGE_KEY = "page_index"
_POSITION_LAYOUT_KEY = "layout_mode"
_POSITION_ZOOM_KEY = "zoom"

# Fallback when a stored position predates zoom persistence, or the stored
# value is corrupt — matches PdfDocument.DEFAULT_ZOOM without importing the
# reading feature into settings just for one constant.
_DEFAULT_ZOOM = 1.0


def get_document_position(path: str) -> dict | None:
    """The page/layout/zoom `path` was last left on, or None if never recorded."""
    entry = next((e for e in get_recent_files() if e["path"] == path), None)
    if entry is None or _POSITION_PAGE_KEY not in entry:
        return None
    page_index = entry[_POSITION_PAGE_KEY]
    if not isinstance(page_index, int) or page_index < 0:
        return None
    layout = entry.get(_POSITION_LAYOUT_KEY, BOOK)
    zoom = entry.get(_POSITION_ZOOM_KEY, _DEFAULT_ZOOM)
    if not isinstance(zoom, (int, float)) or zoom <= 0:
        zoom = _DEFAULT_ZOOM
    return {
        "page_index": page_index,
        "layout_mode": layout if layout in (BOOK, STRIP) else BOOK,
        "zoom": float(zoom),
    }


def set_document_position(path: str, page_index: int, layout_mode: str, zoom: float) -> None:
    """Record where `path` is being read, leaving recent-list order alone.

    Deliberately does not promote `path` to the front of the recent list:
    this is called as the reader turns pages, and reordering Home's card grid
    underneath them mid-read would be surprising.
    """
    if layout_mode not in (BOOK, STRIP):
        raise ValueError(f"invalid layout mode: {layout_mode!r}")
    data = _load()
    entries = data.get(_RECENT_FILES_KEY)
    if not isinstance(entries, list):
        return
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == path:
            entry[_POSITION_PAGE_KEY] = int(page_index)
            entry[_POSITION_LAYOUT_KEY] = layout_mode
            entry[_POSITION_ZOOM_KEY] = float(zoom)
            _save(data)
            return


def format_relative_time(iso_str: str) -> str:
    try:
        then = datetime.datetime.fromisoformat(iso_str)
    except ValueError:
        return ""
    seconds = (datetime.datetime.now() - then).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago" if minutes != 1 else "1 min ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} hr ago" if hours != 1 else "1 hr ago"
    days = int(hours // 24)
    if days < 7:
        return f"{days} days ago" if days != 1 else "1 day ago"
    return then.strftime("%b %d, %Y")
