"""Writing a PDF to disk so that a failed or interrupted save never damages
a file that is already there.

Everything is written to a temporary file in the destination's own folder,
flushed to disk, checked, and only then moved over the destination in one
`os.replace` — so the file at that path is always either the old one, intact,
or the new one, complete. What went wrong is reported as a `SaveError`
carrying a sentence written for the reader, never the library's own text.

No PyMuPDF here (docs/HARDENING.md H7 keeps it to five modules): the caller
passes in how to write the file and how to check it.
"""
import errno
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable
from typing import TypeVar

TEMP_PREFIX = ".lector-save-"
TEMP_SUFFIX = ".tmp"

READ_ONLY = "read_only"
PERMISSION = "permission"
LOCKED = "locked"
DISK_FULL = "disk_full"
FOLDER_MISSING = "folder_missing"
FILE_MISSING = "file_missing"
DRIVE_GONE = "drive_gone"
VERIFY_FAILED = "verify_failed"
SIGNED = "signed"
IS_OPEN_DOCUMENT = "is_open_document"
HIGHLIGHTS_LOST = "highlights_lost"
UNSAVED_NOT_REOPENED = "unsaved_not_reopened"
SAVED_NOT_REOPENED = "saved_not_reopened"
UNKNOWN = "unknown"

# After these the PDF could not be opened again, so no document is left open
# and the reader has to go back to Home.
CLOSES_DOCUMENT = frozenset({UNSAVED_NOT_REOPENED, SAVED_NOT_REOPENED})

# Every failure leaves the file on disk as it was and the highlights in the
# reader, unsaved, so each message says so — except the last three below,
# which are exactly the cases where that is not true.
_SAFE = "Your original PDF is unchanged and your highlights are still open."

MESSAGES = {
    READ_ONLY: f"Couldn't save: the file is read-only. {_SAFE}",
    PERMISSION: f"Couldn't save: Lector isn't allowed to write to that folder. {_SAFE}",
    LOCKED: f"Couldn't save: another program is using the file. Close it there and try again. {_SAFE}",
    DISK_FULL: f"Couldn't save: the disk is full. {_SAFE}",
    FOLDER_MISSING: f"Couldn't save: that folder no longer exists. {_SAFE}",
    FILE_MISSING: "Couldn't save: the PDF was moved or deleted while it was open. "
    "Your highlights are still open; use Save a copy to keep them.",
    DRIVE_GONE: f"Couldn't save: the drive or network location isn't available. {_SAFE}",
    VERIFY_FAILED: f"Couldn't save: the saved file didn't check out, so it was discarded. {_SAFE}",
    SIGNED: "This PDF is digitally signed, and overwriting it would break the signature. "
    f"Save a copy instead. {_SAFE}",
    IS_OPEN_DOCUMENT: "That's the PDF you're reading. To save into it, choose Overwrite the original. "
    f"{_SAFE}",
    UNKNOWN: f"Couldn't save the PDF. {_SAFE}",
    HIGHLIGHTS_LOST: "Couldn't save, and Lector had to reload the PDF as it is on disk, "
    "so the highlights since your last save are gone. Your original PDF is unchanged.",
    UNSAVED_NOT_REOPENED: "Couldn't save, and Lector couldn't open the PDF again, so the highlights "
    "since your last save are gone. Your original PDF is unchanged. Open it from Home to keep reading.",
    SAVED_NOT_REOPENED: "Your highlights were saved, but Lector couldn't open the PDF again "
    "afterwards. Open it from Home to keep reading.",
}

# Windows error codes, from winerror.h.
_WIN_ACCESS_DENIED = 5
_WIN_SHARING_VIOLATION = 32
_WIN_LOCK_VIOLATION = 33
_WIN_DISK_FULL = (39, 112)  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
# Not ready, bad net path, net name deleted, bad net name, network unreachable.
_WIN_DRIVE_GONE = (21, 53, 64, 67, 1231)

# An antivirus scanner or the search indexer can hold a just-written file for
# a moment, which makes the replace fail with a sharing error that a short
# wait clears.
_REPLACE_ATTEMPTS = 5
_REPLACE_RETRY_SECONDS = 0.1

# Below this much free space, an opaque failure from MuPDF's writer (which
# raises RuntimeError, not OSError) is taken to be a full disk.
_LOW_SPACE_BYTES = 1024 * 1024

T = TypeVar("T")


class SaveError(Exception):
    """A save that did not happen, and why, in words the reader can act on."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

    @property
    def message(self) -> str:
        return MESSAGES[self.reason]

    @property
    def closes_document(self) -> bool:
        return self.reason in CLOSES_DOCUMENT


def write_atomically(
    dest: str,
    write: Callable[[str], None],
    verify: Callable[[str], None],
    swap: Callable[[str, str], None] | None = None,
) -> None:
    """Write `dest` through a temporary file beside it.

    `write(tmp)` writes the file, `verify(tmp)` raises (a SaveError, ideally)
    if what was written is not right, and `swap(tmp, dest)` puts it in place —
    `replace` unless the caller has to do something around the move, as the
    overwrite does to let go of the open original. Raises SaveError; the
    temporary file is gone afterwards either way.
    """
    folder = os.path.dirname(os.path.abspath(dest))
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX)
    except OSError as exc:
        raise SaveError(classify(exc, dest)) from exc
    os.close(fd)
    try:
        write(tmp)
        _fsync_file(tmp)
        verify(tmp)
        if os.path.exists(dest):
            # Keep the destination's permissions: a rewritten file would
            # otherwise get the temporary file's (0600 on a Mac).
            shutil.copymode(dest, tmp)
        (swap or replace)(tmp, dest)
        _fsync_folder(folder)
    except SaveError:
        raise
    except Exception as exc:
        raise SaveError(classify(exc, dest)) from exc
    finally:
        _discard(tmp)


def replace(src: str, dest: str) -> None:
    """`os.replace`, retried briefly while another process holds `dest`."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dest)
            return
        except PermissionError as exc:
            transient = getattr(exc, "winerror", None) in (
                _WIN_ACCESS_DENIED,
                _WIN_SHARING_VIOLATION,
                _WIN_LOCK_VIOLATION,
            )
            if not transient or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECONDS)


def retry_briefly(action: Callable[[], T]) -> T:
    """`action()`, tried again a few times while it raises — for a file just
    moved into place, which a scanner may still be holding. Raises the last
    error if every try fails."""
    for _ in range(_REPLACE_ATTEMPTS - 1):
        try:
            return action()
        except Exception:
            time.sleep(_REPLACE_RETRY_SECONDS)
    return action()


def check_writable(path: str) -> None:
    """Raise SaveError if `path` cannot be written in place right now.

    Run before an overwrite touches anything, so that a read-only or locked
    file is reported while the open document is still as it was. On a Mac it
    is also the only thing that stops one: `os.replace` would happily put a
    new file where a read-only one was.
    """
    if os.path.exists(path) and not os.access(path, os.W_OK):
        raise SaveError(READ_ONLY)
    try:
        with open(path, "r+b"):
            pass
    except OSError as exc:
        raise SaveError(classify(exc, path)) from exc


def same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file, however each is spelled."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def classify(exc: BaseException, dest: str) -> str:
    """Which of the reasons above `exc`, raised while saving to `dest`, is."""
    folder = os.path.dirname(os.path.abspath(dest))
    if not os.path.isdir(folder):
        return DRIVE_GONE if _volume_gone(folder) else FOLDER_MISSING
    if not isinstance(exc, OSError):
        return DISK_FULL if _low_on_space(folder) else UNKNOWN
    winerror = getattr(exc, "winerror", None)
    if exc.errno == errno.ENOSPC or winerror in _WIN_DISK_FULL:
        return DISK_FULL
    if winerror in _WIN_DRIVE_GONE or exc.errno in (errno.ENODEV, errno.ENXIO, errno.EIO):
        return DRIVE_GONE
    if winerror in (_WIN_SHARING_VIOLATION, _WIN_LOCK_VIOLATION):
        return LOCKED
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
        return READ_ONLY if os.path.exists(dest) and not os.access(dest, os.W_OK) else PERMISSION
    if exc.errno == errno.ENOENT:
        return FILE_MISSING
    return UNKNOWN


def _volume_gone(folder: str) -> bool:
    """A missing drive letter or network share (Windows), or an unmounted
    volume (macOS's /Volumes/<name>), rather than just a missing folder."""
    drive = os.path.splitdrive(folder)[0]
    if drive:
        return not os.path.isdir(drive + os.sep)
    parts = folder.split(os.sep)
    if len(parts) > 2 and parts[1] == "Volumes":
        return not os.path.isdir(os.sep.join(parts[:3]))
    return False


def _low_on_space(folder: str) -> bool:
    try:
        return shutil.disk_usage(folder).free < _LOW_SPACE_BYTES
    except OSError:
        return False


def _fsync_file(path: str) -> None:
    with open(path, "rb+") as f:
        os.fsync(f.fileno())


def _fsync_folder(folder: str) -> None:
    """Make the rename itself durable. Windows cannot open a folder for this
    (NTFS journals the rename instead); some other filesystems refuse it, and
    by then the save has already happened, so a refusal is not a failure."""
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        # A folder can be writable but not readable (macOS's Drop Box).
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _discard(tmp: str) -> None:
    """Remove the temporary file if it is still there. `copymode` may have
    made it read-only, which on Windows has to be undone before deleting."""
    try:
        os.remove(tmp)
    except FileNotFoundError:
        return
    except PermissionError:
        os.chmod(tmp, stat.S_IWRITE | stat.S_IREAD)
        os.remove(tmp)
