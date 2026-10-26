"""Persisted app-level settings via a local JSON file.

docs/TECH_STACK.md rules out a database for app state. Under PySide6 this was
backed by `QSettings` (registry on Windows, a plist on Mac); pywebview has no
equivalent, so this is now a plain JSON file in the OS's standard per-user app
data location — "a straightforward substitution, not a design change" per
docs/TECH_STACK.md. Behavior (keys, defaults, the 20-item recent-files cap)
is unchanged from the QSettings-backed version.

The file is never left half-written (docs/HARDENING.md H4): `_save` writes a
temporary file beside it, flushes it to disk and moves it into place in one
`os.replace`, and every read-modify-write runs under one lock, because bridge
calls arrive on several threads and two setters interleaving used to save
each other's half-read copy. A file that is there but doesn't parse is kept
aside as `settings.json.corrupt-<timestamp>` before the defaults load, and a
file that is there but can't be read is never written over.
"""
import datetime
import functools
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

from lector.features.reading import safe_write
from lector.shared.theme import DEFAULT_HIGHLIGHT_COLOR, HIGHLIGHT_COLOR_NAMES, THEME_ORDER

log = logging.getLogger(__name__)

ASK = "ask"
COPY = "copy"
OVERWRITE = "overwrite"

# How a PDF opens when it is reopened later (the Settings > "When I reopen a
# PDF" preference). CONTINUE restores the page and layout the reader left the
# document on; START always opens at page 1 in the default view (Settings >
# Reading).
CONTINUE = "continue"
START = "start"

BOOK = "book"
STRIP = "strip"

_SAVE_BEHAVIOR_KEY = "save_behavior"
_THEME_KEY = "theme"
_REOPEN_BEHAVIOR_KEY = "reopen_behavior"
_RECENT_FILES_KEY = "recent_files"
RECENT_FILES_CAP = 20

# Home screen's Recent grid/list toggle (grid is the long-standing default;
# list adds sortable-at-a-glance columns for a longer history now that the
# cap is 20). A persisted preference like theme/reopen-behavior, not a
# per-session UI toggle, since the whole point is it stays how the reader
# left it.
_RECENT_VIEW_KEY = "recent_view"
RECENT_VIEW_GRID = "grid"
RECENT_VIEW_LIST = "list"

# Voice activation (docs/PRD.md: "independently toggleable"). Two separate
# booleans rather than one three-valued mode, because the PRD's requirement
# is precisely that they are not alternatives: both on, both off, or either
# alone all have to be expressible, and a single "mode" key could not say
# "both" without growing a value that means the same thing as two flags.
# The active highlight colour: what the next highlight is drawn in, by mouse
# or by voice. One app-wide choice rather than one per document, so it is
# still the reader's colour when they open the next PDF.
_HIGHLIGHT_COLOR_KEY = "highlight_color"

_PUSH_TO_TALK_KEY = "push_to_talk_enabled"
_WAKE_PHRASE_KEY = "wake_phrase_enabled"

# What an install starts with before onboarding says otherwise: the key,
# which opens the microphone only while it is held. Nothing is silently
# always-on until the reader asks for it.
_DEFAULT_PUSH_TO_TALK = True
_DEFAULT_WAKE_PHRASE = False


def settings_dir() -> Path:
    """The app's per-user data folder (settings.json, the web view's storage,
    the logs, and the single-instance hand-off key)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Lector"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Lector"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "lector"


def _settings_path() -> Path:
    return settings_dir() / "settings.json"


# The shape of settings.json, written into it on every save. A file without
# the key predates H4 and is version 0; see `_migrate`.
SCHEMA_VERSION = 1
_SCHEMA_VERSION_KEY = "schema_version"

# A settings.json that doesn't parse is renamed to `settings.json.corrupt-` +
# a timestamp, and this many of the newest such copies are kept.
_CORRUPT_MARKER = ".corrupt-"
CORRUPT_COPIES_KEPT = 2

# An antivirus scanner or a sync client can hold the file for a moment;
# Windows reports that as a sharing or lock violation (winerror.h), which is
# worth a few short retries. Anything else (access denied) won't clear up by
# waiting, and every getter reads the file, so it is not retried.
_WIN_SHARING_VIOLATION = 32
_WIN_LOCK_VIOLATION = 33
_READ_ATTEMPTS = 5
_READ_RETRY_SECONDS = 0.1

# Held by every read and by every read-modify-write as a whole (`_serialized`).
# Reentrant because setters call getters, which read.
_lock = threading.RLock()

# How many `_serialized` calls the thread holding `_lock` is inside. Only
# touched under `_lock`, so a non-zero value always belongs to the caller.
_write_depth = 0

# Set during a setter when settings.json is there but couldn't be read, or
# is unparseable and couldn't be moved aside: saving would replace the
# reader's real file with defaults plus one change, so `_save` skips instead.
# Cleared as the outermost setter starts and ends, so it never outlives the
# operation that found the problem (a getter on its own sets nothing).
_hold_writes = False

# Problems currently reported in the log. One that persists (another program
# keeping the file open) is logged when it starts and when it clears, not on
# every read or page turn.
_UNREADABLE = "settings.json could not be read; using defaults and not saving changes"
_STUCK = "settings.json could not be parsed or moved aside; using defaults and not saving changes"
_UNWRITABLE = "settings.json could not be written; the change was not saved and the old file is unchanged"
_ongoing: set[str] = set()

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _serialized(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run a load-modify-save setter as one step under `_lock`, so another
    thread's write can't land between its read and its save (and be lost)."""

    @functools.wraps(func)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        global _write_depth, _hold_writes
        with _lock:
            if _write_depth == 0:
                _hold_writes = False
            _write_depth += 1
            try:
                return func(*args, **kwargs)
            finally:
                _write_depth -= 1
                if _write_depth == 0:
                    _hold_writes = False

    return serialized


def _hold() -> None:
    """Keep the running setter, if there is one, from saving."""
    global _hold_writes
    if _write_depth:
        _hold_writes = True


def _note(problem: str, failing: bool) -> None:
    """Log `problem` when it starts and when it clears. Called from inside an
    `except` block when failing, so the exception's type and frames go with it."""
    if failing and problem not in _ongoing:
        _ongoing.add(problem)
        log.warning(problem, exc_info=True)
    elif not failing and problem in _ongoing:
        _ongoing.discard(problem)
        log.info("Resolved: %s", problem)


def _load() -> dict:
    """The stored settings, or `{}` when there are none to use: no file yet,
    one that can't be read (writes are then held), or one that doesn't parse
    (kept aside first)."""
    path = _settings_path()
    with _lock:
        try:
            raw = _read_bytes(path)
        except OSError:
            _hold()
            _note(_UNREADABLE, failing=True)
            return {}
        _note(_UNREADABLE, failing=False)
        if raw is None:
            _note(_STUCK, failing=False)
            return {}
        try:
            # utf-8-sig: a file hand-edited in an editor that adds a BOM is
            # still the reader's settings, not a damaged file.
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict):
            _set_aside(path)
            return {}
        _note(_STUCK, failing=False)
        return _migrate(data)


def _read_bytes(path: Path) -> bytes | None:
    """The file's bytes, or None if there is no file. Raises OSError if it is
    there but can't be read, after retrying briefly while it is held."""
    for attempt in range(_READ_ATTEMPTS):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            held = getattr(exc, "winerror", None) in (_WIN_SHARING_VIOLATION, _WIN_LOCK_VIOLATION)
            if not held or attempt == _READ_ATTEMPTS - 1:
                raise
            time.sleep(_READ_RETRY_SECONDS)
    return None


def _set_aside(path: Path) -> None:
    """Rename an unparseable settings.json out of the way, so the next save
    doesn't destroy what the reader might still recover from it, and drop
    all but the newest `CORRUPT_COPIES_KEPT` such copies. If the rename
    fails, writes are held rather than going over it."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = path.with_name(f"{path.name}{_CORRUPT_MARKER}{stamp}")
    # A second damaged file within the same second must not replace the first copy.
    suffix = 2
    while kept.exists():
        kept = path.with_name(f"{path.name}{_CORRUPT_MARKER}{stamp}-{suffix}")
        suffix += 1
    try:
        safe_write.replace(str(path), str(kept))
    except OSError:
        _hold()
        _note(_STUCK, failing=True)
        return
    _note(_STUCK, failing=False)
    log.warning("settings.json could not be parsed; kept it as %s and loaded defaults", kept.name)
    copies = sorted(path.parent.glob(f"{path.name}{_CORRUPT_MARKER}*"), key=lambda p: p.name, reverse=True)
    for old in copies[CORRUPT_COPIES_KEPT:]:
        try:
            old.unlink()
        except OSError:
            log.warning("Could not remove an old damaged settings copy %s", old.name)


def _migrate(data: dict) -> dict:
    """Bring a file written by an older Lector up to `SCHEMA_VERSION`.

    Nothing to do yet: version 0 (no key, before H4) and version 1 have the
    same keys. When a key is renamed or changes meaning, the step goes here,
    keyed on `data.get(_SCHEMA_VERSION_KEY, 0)`, so the rest of this module
    only ever reads the current shape. A file from a newer Lector is read as
    it is; keys this version doesn't know are carried through untouched."""
    return data


def _save(data: dict) -> None:
    """Write `data` as settings.json in one step: a complete temporary file
    beside it, flushed to disk, then `os.replace`d over it, so the file on
    disk is always the old one or the new one, whole.

    A write that fails (disk full, another program holding the file past
    `safe_write.replace`'s retries) is logged and the change is not saved;
    it is not raised, so a setting can never be why a PDF fails to open or a
    page fails to turn. Skipped outright while writes are held (`_hold`),
    which `_load` has already logged."""
    path = _settings_path()
    with _lock:
        if _hold_writes:
            return
        stored = data.get(_SCHEMA_VERSION_KEY)
        newer = isinstance(stored, int) and not isinstance(stored, bool) and stored > SCHEMA_VERSION
        payload = {**data, _SCHEMA_VERSION_KEY: stored if newer else SCHEMA_VERSION}
        tmp = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            safe_write.replace(str(tmp), str(path))
        except OSError:
            _note(_UNWRITABLE, failing=True)
            return
        else:
            _note(_UNWRITABLE, failing=False)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove the temporary settings file")


def get_save_behavior() -> str:
    value = _load().get(_SAVE_BEHAVIOR_KEY, ASK)
    return value if value in (ASK, COPY, OVERWRITE) else ASK


@_serialized
def set_save_behavior(mode: str) -> None:
    if mode not in (ASK, COPY, OVERWRITE):
        raise ValueError(f"invalid save behavior: {mode!r}")
    data = _load()
    data[_SAVE_BEHAVIOR_KEY] = mode
    _save(data)


def get_theme() -> str:
    value = _load().get(_THEME_KEY, "light")
    return value if value in THEME_ORDER else "light"


@_serialized
def set_theme(name: str) -> None:
    if name not in THEME_ORDER:
        raise ValueError(f"invalid theme: {name!r}")
    data = _load()
    data[_THEME_KEY] = name
    _save(data)


def get_recent_view() -> str:
    value = _load().get(_RECENT_VIEW_KEY, RECENT_VIEW_GRID)
    return value if value in (RECENT_VIEW_GRID, RECENT_VIEW_LIST) else RECENT_VIEW_GRID


@_serialized
def set_recent_view(mode: str) -> None:
    if mode not in (RECENT_VIEW_GRID, RECENT_VIEW_LIST):
        raise ValueError(f"invalid recent view: {mode!r}")
    data = _load()
    data[_RECENT_VIEW_KEY] = mode
    _save(data)


def get_reopen_behavior() -> str:
    value = _load().get(_REOPEN_BEHAVIOR_KEY, CONTINUE)
    return value if value in (CONTINUE, START) else CONTINUE


@_serialized
def set_reopen_behavior(mode: str) -> None:
    if mode not in (CONTINUE, START):
        raise ValueError(f"invalid reopen behavior: {mode!r}")
    data = _load()
    data[_REOPEN_BEHAVIOR_KEY] = mode
    _save(data)


def get_highlight_color() -> str:
    value = _load().get(_HIGHLIGHT_COLOR_KEY, DEFAULT_HIGHLIGHT_COLOR)
    return value if value in HIGHLIGHT_COLOR_NAMES else DEFAULT_HIGHLIGHT_COLOR


@_serialized
def set_highlight_color(name: str) -> None:
    if name not in HIGHLIGHT_COLOR_NAMES:
        raise ValueError(f"invalid highlight color: {name!r}")
    data = _load()
    data[_HIGHLIGHT_COLOR_KEY] = name
    _save(data)


# Settings > Reading. The view a PDF opens in the first time it is opened (one
# with a saved position comes back in its own); whether the reader shows the
# progress bar on its left edge; and whether the outline panel was left open,
# so it comes back the way the reader left it.
_DEFAULT_VIEW_KEY = "default_view"
_PROGRESS_BAR_KEY = "progress_bar"
_OUTLINE_OPEN_KEY = "outline_panel_open"


def get_default_view() -> str:
    value = _load().get(_DEFAULT_VIEW_KEY, BOOK)
    return value if value in (BOOK, STRIP) else BOOK


@_serialized
def set_default_view(mode: str) -> None:
    if mode not in (BOOK, STRIP):
        raise ValueError(f"invalid default view: {mode!r}")
    data = _load()
    data[_DEFAULT_VIEW_KEY] = mode
    _save(data)


def get_progress_bar() -> bool:
    return _as_bool(_load().get(_PROGRESS_BAR_KEY), True)


@_serialized
def set_progress_bar(on: bool) -> None:
    data = _load()
    data[_PROGRESS_BAR_KEY] = bool(on)
    _save(data)


def get_outline_open() -> bool:
    return _as_bool(_load().get(_OUTLINE_OPEN_KEY), False)


@_serialized
def set_outline_open(is_open: bool) -> None:
    data = _load()
    data[_OUTLINE_OPEN_KEY] = bool(is_open)
    _save(data)


# The outline panel's width, dragged at its right edge, in CSS pixels.
_OUTLINE_WIDTH_KEY = "outline_panel_width"
OUTLINE_WIDTH_DEFAULT = 200
OUTLINE_WIDTH_MIN = 168
OUTLINE_WIDTH_MAX = 400


def _clamp_outline_width(value) -> int:
    return min(OUTLINE_WIDTH_MAX, max(OUTLINE_WIDTH_MIN, round(value)))


def get_outline_width() -> int:
    """The stored width, kept within the panel's limits; the default when
    none is stored or the stored value isn't a number."""
    value = _load().get(_OUTLINE_WIDTH_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return OUTLINE_WIDTH_DEFAULT
    return _clamp_outline_width(value)


@_serialized
def set_outline_width(width: float) -> int:
    """Stores `width` within the panel's limits and returns what was stored."""
    stored = _clamp_outline_width(width)
    data = _load()
    data[_OUTLINE_WIDTH_KEY] = stored
    _save(data)
    return stored


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


@_serialized
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


@_serialized
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


@_serialized
def remove_recent_file(path: str) -> tuple[int, dict] | None:
    """Drop `path` from the recent-files list only (Milestone 8.9).

    List-entry removal only — the underlying PDF on disk is never touched,
    which is what makes this reversible (reopening the file re-adds it) and
    safe to gate behind nothing more than a confirmation dialog or an Undo.
    Returns the removed entry and the index it sat at, so
    `restore_recent_file` can put it back exactly where it was, reading
    position included. None (a no-op) if `path` isn't present, the same
    tolerance `add_recent_file`'s own dedup already assumes of this list.
    """
    data = _load()
    entries = data.get(_RECENT_FILES_KEY)
    if not isinstance(entries, list):
        return None
    index = next(
        (i for i, e in enumerate(entries) if isinstance(e, dict) and e.get("path") == path),
        None,
    )
    if index is None:
        return None
    removed = entries[index]
    data[_RECENT_FILES_KEY] = entries[:index] + entries[index + 1:]
    _save(data)
    return index, removed


# Favorites: files the reader pinned, kept in their own list rather than as a
# flag on the recent-files entry. Recent is a 20-item rolling history that
# forgets files (and their reading positions) on purpose; a favorite is the
# opposite — it must survive falling off that list, or "favorite" would mean
# "recent for a bit longer". No cap: the reader decides how many to keep.
_FAVORITES_KEY = "favorites"


def get_favorites() -> list[dict]:
    """Newest-favorited-first list of {"path": str, "added_at": ISO 8601 str}.
    Malformed items (settings.json is a plain file a reader can edit) are
    skipped rather than raised on."""
    raw = _load().get(_FAVORITES_KEY, [])
    if not isinstance(raw, list):
        return []
    return [
        entry for entry in raw
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and "added_at" in entry
    ]


def is_favorite(path: str) -> bool:
    return any(e["path"] == path for e in get_favorites())


@_serialized
def add_favorite(path: str) -> bool:
    """Pin `path` at the front. False (and no write) if it already was."""
    if is_favorite(path):
        return False
    data = _load()
    entry = {"path": path, "added_at": datetime.datetime.now().isoformat()}
    data[_FAVORITES_KEY] = [entry, *get_favorites()]
    _save(data)
    return True


@_serialized
def remove_favorite(path: str) -> bool:
    """Unpin `path`. False (and no write) if it wasn't a favorite."""
    favorites = get_favorites()
    kept = [e for e in favorites if e["path"] != path]
    if len(kept) == len(favorites):
        return False
    data = _load()
    data[_FAVORITES_KEY] = kept
    _save(data)
    return True


RESTORED = "restored"
ALREADY_LISTED = "already_listed"
LIST_FULL = "full"


@_serialized
def restore_recent_file(index: int, entry: dict) -> str:
    """Put an entry `remove_recent_file` returned back at `index` (the
    Recent list's Undo). Returns RESTORED, or why nothing changed:
    ALREADY_LISTED when the file was reopened in the meantime (that fresher
    entry wins), LIST_FULL when other files have since filled the list to
    RECENT_FILES_CAP — restoring would silently push the oldest one out.

    Works on the stored list itself, the same one `remove_recent_file`
    measured `index` on, so a hand-edited settings.json with malformed items
    in it neither shifts the position nor loses those items."""
    data = _load()
    stored = data.get(_RECENT_FILES_KEY)
    stored = list(stored) if isinstance(stored, list) else []
    if any(isinstance(e, dict) and e.get("path") == entry["path"] for e in stored):
        return ALREADY_LISTED
    if len(get_recent_files()) >= RECENT_FILES_CAP:
        return LIST_FULL
    stored.insert(min(max(index, 0), len(stored)), entry)
    data[_RECENT_FILES_KEY] = stored
    _save(data)
    return RESTORED


# Reading position rides along on the recent-files entry rather than living in
# its own map: it is only ever needed for a file the reader can actually get
# back to from the Home screen, so tying its lifetime to the 20-item recent
# list means stale positions for long-forgotten files are pruned for free
# instead of accumulating in settings.json forever.
_POSITION_PAGE_KEY = "page_index"
_POSITION_LAYOUT_KEY = "layout_mode"
_POSITION_ZOOM_KEY = "zoom"

# Fallback when a stored position predates zoom persistence, or the stored
# value is corrupt — matches PdfDocument.DEFAULT_ZOOM without importing the
# reading feature into settings just for one constant.
_DEFAULT_ZOOM = 1.0


def position_from_entry(entry: dict) -> dict | None:
    """The page/layout/zoom recorded on one recent-files `entry`, or None if it
    has none. Split out of `get_document_position` so a caller already holding
    the entries (Home's list) need not re-read settings.json once per file."""
    if _POSITION_PAGE_KEY not in entry:
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


def get_document_position(path: str) -> dict | None:
    """The page/layout/zoom `path` was last left on, or None if never recorded."""
    entry = next((e for e in get_recent_files() if e["path"] == path), None)
    return None if entry is None else position_from_entry(entry)


@_serialized
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
