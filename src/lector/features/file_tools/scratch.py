"""Compress's temporary results: files written beside the destination by the
estimates and the size-limit search, kept until one is moved into place or
the dialog closes.

Each one is listed in a small manifest in the app's settings folder while it
exists, so one left behind by a crash or a forced quit is deleted at the next
start (`clean_leftovers`, from `__main__`). Only files whose name has
Lector's own prefix and suffix are ever deleted from it.

Before one is written, the free space on its drive is checked
(`free_bytes`): under twice the original's size plus `KEEP_RESERVE`, a step's
file is deleted as soon as its size is known (`keeps_files`), and under the
original's size a step is taken not to fit on the drive at all (`can_write`).

No PyMuPDF: the worker writes into the paths made here.
"""
import errno
import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path

from lector.features.reading import safe_write

log = logging.getLogger(__name__)

PREFIX = ".lector-compress-"
SUFFIX = safe_write.TEMP_SUFFIX
MANIFEST_NAME = "compress-scratch.json"

# Free space left beyond the temporary files kept while the dialog is open.
KEEP_RESERVE = 200 * 1000 * 1000
# Windows' "disk full" errors (ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL).
_WIN_DISK_FULL = (39, 112)


def free_bytes(folder: str) -> int | None:
    """Free space on `folder`'s drive, or None when it can't be read."""
    try:
        return shutil.disk_usage(folder).free
    except (OSError, ValueError):
        return None


def keeps_files(free: int | None, source_size: int) -> bool:
    """Whether there is room to keep each step's file until the dialog closes."""
    return free is None or free >= 2 * source_size + KEEP_RESERVE


def can_write(free: int | None, source_size: int) -> bool:
    """Whether one step's file (at most about the original's size) fits."""
    return free is None or free >= source_size


def is_space_error(exc: OSError) -> bool:
    return exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in _WIN_DISK_FULL


def _is_ours(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(PREFIX) and name.endswith(SUFFIX)


def _remove(path: str) -> bool:
    """Delete `path`; whether it is gone."""
    try:
        os.remove(path)
    except FileNotFoundError:
        return True
    except OSError:
        log.warning("Couldn't remove a temporary Compress file")
        return False
    return True


class ScratchFiles:
    """The temporary files that exist right now, and their manifest."""

    def __init__(self, manifest: Path):
        self._manifest = Path(manifest)
        self._lock = threading.Lock()
        self._paths: set[str] = set()

    def new(self, folders: list[str]) -> str:
        """An empty temporary file in the first of `folders` that takes one
        (the destination's folder, then fallbacks). Raises OSError if none
        does."""
        error: OSError | None = None
        for folder in folders:
            if not folder:
                continue
            try:
                handle, path = tempfile.mkstemp(dir=folder, prefix=PREFIX, suffix=SUFFIX)
            except OSError as exc:
                error = exc
                continue
            os.close(handle)
            with self._lock:
                self._paths.add(path)
                self._write_manifest()
            return path
        raise error or OSError("no folder for a temporary file")

    def release(self, path: str) -> None:
        """`path` was moved into place (or is gone): stop tracking it."""
        with self._lock:
            self._paths.discard(path)
            self._write_manifest()

    def discard(self, path: str | None) -> None:
        """Delete a temporary file. One that can't be deleted now (another
        process still has it open) stays listed, so the next start does."""
        if not path:
            return
        if _remove(path):
            self.release(path)

    def discard_all(self) -> None:
        with self._lock:
            self._paths = {path for path in self._paths if not _remove(path)}
            self._write_manifest()

    def tracked(self) -> list[str]:
        with self._lock:
            return sorted(self._paths)

    def _write_manifest(self) -> None:
        try:
            self._manifest.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._manifest.with_suffix(".tmp")
            tmp.write_text(json.dumps({"files": sorted(self._paths)}), encoding="utf-8")
            os.replace(tmp, self._manifest)
        except OSError:
            log.warning("Couldn't record Compress's temporary files")


def clean_leftovers(manifest: Path) -> int:
    """Delete the temporary files an earlier run left behind (listed in its
    manifest), then empty the manifest. Returns how many were deleted."""
    manifest = Path(manifest)
    try:
        listed = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
    except (OSError, ValueError, AttributeError):
        return 0
    removed = 0
    for path in listed if isinstance(listed, list) else []:
        if isinstance(path, str) and _is_ours(path) and os.path.isfile(path):
            _remove(path)
            removed += not os.path.exists(path)
    try:
        manifest.unlink()
    except OSError:
        pass
    return removed
