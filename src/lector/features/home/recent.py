"""Recent-file listing data and thumbnails for the Home screen.

Logic moved as-is from the PySide6 `HomeView` (it only ever touched
PyMuPDF/QPixmap, no Qt widget code) — `src/lector/api.py` is the only thing
that now calls this, handing the frontend a base64 PNG instead of a QPixmap.
"""
import base64
import os
import subprocess
import sys
import threading

import fitz  # PyMuPDF

from lector.features.settings import store as settings

THUMBNAIL_HEIGHT = 116

# Keyed by path, each recomputed only when the file's mtime changes, not on
# every Home screen render. Kept apart because they cost very different
# amounts: opening a PDF for its page count and title is about a sixth of the
# time rendering its first page takes, so the list is built from the first
# alone and each thumbnail follows on its own (`load_thumbnail`).
#   _info_cache:      path -> (page_count, title, renderable, mtime)
#   _thumbnail_cache: path -> (png_base64, mtime)
_info_cache: dict[str, tuple[int, str | None, bool, float | None]] = {}
_thumbnail_cache: dict[str, tuple[str | None, float | None]] = {}

# The page lists and fetches thumbnails over separate bridge calls, which
# pywebview runs on threads of their own, and PyMuPDF documents must not be
# used from two threads at once.
_fitz_lock = threading.Lock()


def clear_caches() -> None:
    """Forgets every page count, title and thumbnail (tests start clean)."""
    _info_cache.clear()
    _thumbnail_cache.clear()


def _mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _metadata_title(doc: fitz.Document) -> str | None:
    """The PDF's own Title metadata, or None when it has none worth showing
    (missing or blank) — the caller falls back to the filename."""
    title = (doc.metadata or {}).get("title") or ""
    # One line only: the list row keeps whitespace as written, so a newline
    # in a badly generated PDF's title would stretch the row.
    return " ".join(title.split()) or None


def _load_document_info(path: str) -> tuple[int, str | None, bool]:
    """The PDF's page count and metadata title, and whether it has a first
    page to make a thumbnail of. Opens and closes its own fitz.Document
    rather than sharing the reading view's — recent cards render on the Home
    screen, before any document is otherwise open."""
    mtime = _mtime(path)
    cached = _info_cache.get(path)
    if cached is not None and cached[3] == mtime:
        return cached[:3]

    with _fitz_lock:
        try:
            doc = fitz.open(path)
        except Exception:
            result = (0, None, False)
        else:
            try:
                count = len(doc)
                result = (count, _metadata_title(doc), count > 0)
            except Exception:
                result = (0, None, False)
            finally:
                doc.close()
    _info_cache[path] = (*result, mtime)
    return result


def _cached_thumbnail(path: str) -> tuple[bool, str | None]:
    """(found, png): whether a thumbnail for the file as it is now has already
    been rendered, and that thumbnail (None if rendering it failed)."""
    cached = _thumbnail_cache.get(path)
    if cached is not None and cached[1] == _mtime(path):
        return True, cached[0]
    return False, None


def load_thumbnail(path: str, height: int = THUMBNAIL_HEIGHT) -> str | None:
    """The PDF's first page as a base64-encoded PNG, `height` pixels tall, or
    None when it has no first page or can't be read. Rendered once per
    version of the file."""
    found, png = _cached_thumbnail(path)
    if found:
        return png
    mtime = _mtime(path)
    if not _load_document_info(path)[2]:
        _thumbnail_cache[path] = (None, mtime)
        return None

    with _fitz_lock:
        try:
            doc = fitz.open(path)
        except Exception:
            png = None
        else:
            try:
                rect = doc[0].rect
                zoom = height / rect.height if rect.height else 1.0
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                png = base64.b64encode(pix.tobytes("png")).decode("ascii")
            except Exception:
                png = None
            finally:
                doc.close()
    _thumbnail_cache[path] = (png, mtime)
    return png


def list_recent() -> list[dict]:
    """Recent files enriched with page-count/title/relative-time data, ready
    for the frontend's card grid and list view."""
    favorite_paths = {e["path"] for e in settings.get_favorites()}
    return [
        _build_entry(e["path"], e["opened_at"], e["path"] in favorite_paths)
        for e in settings.get_recent_files()
    ]


def list_favorites() -> list[dict]:
    """The Favorites view's entries, newest-favorited first, in the same shape
    as `list_recent()`. A favorite outlives its Recent entry, so "Last active"
    comes from the Recent entry when there still is one and is empty (shown as
    a dash) when the file has since fallen off the list."""
    opened_at = {e["path"]: e["opened_at"] for e in settings.get_recent_files()}
    return [
        _build_entry(e["path"], opened_at.get(e["path"], ""), True)
        for e in settings.get_favorites()
    ]


def listed_paths() -> set[str]:
    """Every path on either list the Home screen shows."""
    return {e["path"] for e in settings.get_recent_files()} | {
        e["path"] for e in settings.get_favorites()
    }


def page_state() -> dict[str, str]:
    """What Home needs before its first paint, as the `<html>` attributes the
    page server writes into it (shared/frontend_server.with_page_state): the
    saved grid/list view, and how many files each list holds, so the loading
    placeholder has the right shape and length without a bridge call. Reads
    settings.json only; opens no PDF."""
    return {
        "data-recent-view": settings.get_recent_view(),
        "data-recent-count": str(len(settings.get_recent_files())),
        "data-favorites-count": str(len(settings.get_favorites())),
    }


def _build_entry(path: str, opened_at: str, favorite: bool) -> dict:
    name = os.path.splitext(os.path.basename(path))[0]
    page_count, title, renderable = _load_document_info(path)
    found, thumbnail = _cached_thumbnail(path)
    return {
        "path": path,
        "name": name,
        # List view's Name column: the PDF's own title when it carries
        # one, the filename otherwise. The grid keeps showing `name`.
        "title": title or name,
        # Only one already rendered; the page asks for the rest
        # (`get_thumbnails`) once the rows are on screen.
        "thumbnail": thumbnail,
        "thumbnail_pending": renderable and not found,
        "page_count": page_count,
        # Raw ISO timestamp alongside the display string, so the list
        # view can sort by "Last active" without parsing "52 min ago".
        "opened_at": opened_at,
        "relative_time": settings.format_relative_time(opened_at) if opened_at else "",
        "favorite": favorite,
    }


def reveal_in_file_manager(path: str) -> None:
    """Open the OS file manager on `path`'s folder with the file selected
    (Recent list's "Show in folder"). Fire-and-forget: the file manager is
    its own process and Lector doesn't wait on it. The caller is
    responsible for checking `path` is a real file first."""
    if sys.platform == "win32":
        # A single command-line string rather than an argv list: explorer.exe
        # parses its own command line, and only accepts the path quoted
        # *after* the comma. Windows paths cannot contain a double quote,
        # and no shell is involved, so nothing in `path` can break out.
        # Full path to explorer.exe so a stray copy in the working directory
        # can't be picked up instead.
        explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
        subprocess.Popen(f'"{explorer}" /select,"{os.path.normpath(path)}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])
