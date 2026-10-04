"""Recent-file thumbnails and listing data for the Home screen.

Logic moved as-is from the PySide6 `HomeView` (it only ever touched
PyMuPDF/QPixmap, no Qt widget code) — `src/lector/api.py` is the only thing
that now calls this, handing the frontend a base64 PNG instead of a QPixmap.
"""
import base64
import os
import subprocess
import sys

import fitz  # PyMuPDF

from lector.features.settings import store as settings

# Keyed by path -> (png_base64, page_count, title, mtime). Recomputed only
# when the underlying file's mtime changes, not on every Home screen render.
_thumbnail_cache: dict[str, tuple[str | None, int, str | None, float | None]] = {}


def _metadata_title(doc: fitz.Document) -> str | None:
    """The PDF's own Title metadata, or None when it has none worth showing
    (missing or blank) — the caller falls back to the filename."""
    title = (doc.metadata or {}).get("title") or ""
    # One line only: the list row keeps whitespace as written, so a newline
    # in a badly generated PDF's title would stretch the row.
    return " ".join(title.split()) or None


def _load_document_info(path: str, height: int) -> tuple[str | None, int, str | None]:
    """Render the PDF's first page as a base64-encoded PNG thumbnail,
    alongside its page count and metadata title. Opens and closes its own
    fitz.Document rather than sharing the reading view's — recent cards
    render on the Home screen, before any document is otherwise open."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    cached = _thumbnail_cache.get(path)
    if cached is not None and cached[3] == mtime:
        return cached[0], cached[1], cached[2]

    try:
        doc = fitz.open(path)
    except Exception:
        _thumbnail_cache[path] = (None, 0, None, mtime)
        return None, 0, None
    try:
        count = len(doc)
        title = _metadata_title(doc)
        if count == 0:
            result = (None, 0, title)
        else:
            rect = doc[0].rect
            zoom = height / rect.height if rect.height else 1.0
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            png_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
            result = (png_b64, count, title)
    except Exception:
        result = (None, len(doc), None)
    finally:
        doc.close()
    _thumbnail_cache[path] = (*result, mtime)
    return result


def list_recent(thumbnail_height: int = 116) -> list[dict]:
    """Recent files enriched with thumbnail/page-count/relative-time data,
    ready for the frontend's card grid and list view."""
    entries = []
    for entry in settings.get_recent_files():
        path = entry["path"]
        name = os.path.splitext(os.path.basename(path))[0]
        thumbnail, page_count, title = _load_document_info(path, thumbnail_height)
        entries.append({
            "path": path,
            "name": name,
            # List view's Name column: the PDF's own title when it carries
            # one, the filename otherwise. The grid keeps showing `name`.
            "title": title or name,
            "thumbnail": thumbnail,
            "page_count": page_count,
            # Raw ISO timestamp alongside the display string, so the list
            # view can sort by "Last active" without parsing "52 min ago".
            "opened_at": entry["opened_at"],
            "relative_time": settings.format_relative_time(entry["opened_at"]),
        })
    return entries


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
