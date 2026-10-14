"""On-disk cache of OCR results, one JSON file per scanned page.

Kept outside the PDF: Lector only ever writes highlights into a document,
and an invisible text layer added behind a reader's back would be an edit
they never asked for. Files are named by `ocr.page_key`, a hash of what the
page draws rather than of the file, so the cache survives highlighting,
saving a copy and overwriting — none of which touch a page's content stream
or its images.

Size-capped, least-recently-used first: a hit refreshes the file's mtime,
and each write evicts by oldest mtime until the folder is back under
`MAX_CACHE_BYTES`. A page's entry is around 100-200 KB, so the cap holds a
few hundred pages.
"""
import json
import logging
import os
from pathlib import Path

from lector.features.settings.store import settings_dir

log = logging.getLogger(__name__)

MAX_CACHE_BYTES = 100 * 1024 * 1024

# Bumped whenever the payload's shape changes, so an old file reads as a
# miss rather than as a malformed result.
FORMAT_VERSION = 1


def cache_dir() -> Path:
    return settings_dir() / "ocr"


def _path(key: str, root: Path | None) -> Path:
    return (root or cache_dir()) / f"{key}.json"


def load(key: str, root: Path | None = None) -> dict | None:
    """The cached payload for `key`, or None on a miss. A file that cannot be
    read or parsed is a miss too (and is removed): the cost of a bad entry is
    one more OCR, never a broken page."""
    path = _path(key, root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Discarding unreadable OCR cache entry %s", path.name)
        _remove(path)
        return None
    if not isinstance(payload, dict) or payload.get("v") != FORMAT_VERSION:
        _remove(path)
        return None
    try:
        os.utime(path)  # most recently used
    except OSError:
        pass
    return payload


def store(key: str, payload: dict, root: Path | None = None, max_bytes: int = MAX_CACHE_BYTES) -> None:
    """Write `payload` for `key`, then evict down to `max_bytes`. Failure to
    write is logged and otherwise ignored: the result is still in memory for
    this session, and a cache is never worth failing a highlight over."""
    path = _path(key, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**payload, "v": FORMAT_VERSION}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("Could not write OCR cache entry %s", path.name, exc_info=True)
        return
    evict(path.parent, max_bytes)


def evict(root: Path, max_bytes: int) -> None:
    """Delete least-recently-used entries until the folder fits `max_bytes`."""
    entries = []
    for entry in root.glob("*.json"):
        try:
            stat = entry.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, entry))
    total = sum(size for _, size, _ in entries)
    for _, size, entry in sorted(entries, key=lambda e: e[0]):
        if total <= max_bytes:
            break
        _remove(entry)
        total -= size


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
