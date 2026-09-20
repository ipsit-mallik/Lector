"""Recent-file thumbnails and listing data for the Home screen.

Logic moved as-is from the PySide6 `HomeView` (it only ever touched
PyMuPDF/QPixmap, no Qt widget code) — `src/lector/api.py` is the only thing
that now calls this, handing the frontend a base64 PNG instead of a QPixmap.
"""
import base64
import os

import fitz  # PyMuPDF

from lector.features.settings import store as settings

# Keyed by path -> (png_base64, page_count, mtime). Recomputed only when the
# underlying file's mtime changes, not on every Home screen render.
_thumbnail_cache: dict[str, tuple[str | None, int, float | None]] = {}


def _load_thumbnail_and_count(path: str, height: int) -> tuple[str | None, int]:
    """Render the PDF's first page as a base64-encoded PNG thumbnail,
    alongside its page count. Opens and closes its own fitz.Document rather
    than sharing the reading view's — recent cards render on the Home
    screen, before any document is otherwise open."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    cached = _thumbnail_cache.get(path)
    if cached is not None and cached[2] == mtime:
        return cached[0], cached[1]

    try:
        doc = fitz.open(path)
    except Exception:
        _thumbnail_cache[path] = (None, 0, mtime)
        return None, 0
    try:
        count = len(doc)
        if count == 0:
            result = (None, 0)
        else:
            rect = doc[0].rect
            zoom = height / rect.height if rect.height else 1.0
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            png_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
            result = (png_b64, count)
    except Exception:
        result = (None, len(doc))
    finally:
        doc.close()
    _thumbnail_cache[path] = (*result, mtime)
    return result


def list_recent(thumbnail_height: int = 116) -> list[dict]:
    """Recent files enriched with thumbnail/page-count/relative-time data,
    ready for the frontend's card grid."""
    entries = []
    for entry in settings.get_recent_files():
        path = entry["path"]
        name = os.path.splitext(os.path.basename(path))[0]
        thumbnail, page_count = _load_thumbnail_and_count(path, thumbnail_height)
        entries.append({
            "path": path,
            "name": name,
            "thumbnail": thumbnail,
            "page_count": page_count,
            "relative_time": settings.format_relative_time(entry["opened_at"]),
        })
    return entries
