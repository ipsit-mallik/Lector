"""Where a file tool saves: the "Save in" menu's places, the checks a chosen
folder and name must pass before a job runs, and how a message names the
place ("beside the original", "in Documents", "on the Desktop").

docs/PRD.md § "Post-v1: file tools" → "Rules every tool follows". Every tool
dialog opens on "Beside the original"; a folder the reader chooses with
"Choose another folder…" is remembered in the settings file's RECENT list,
but never becomes the default.

No PyMuPDF. The only disk access is looking (`os.path`), listing the known
folders, and the writable check's empty probe file.
"""
import logging
import os
import tempfile
from dataclasses import dataclass

from lector.features.dialogs import browser
from lector.features.file_tools import messages, naming
from lector.features.reading import safe_write
from lector.features.settings import store as settings

log = logging.getLogger(__name__)

PDF_EXTENSION = ".pdf"

# What "Beside the original" is called in a sentence. Convert's output goes
# beside the first file it was given.
BESIDE_ORIGINAL = "beside the original"
BESIDE_FIRST_FILE = "beside the first file"

# ON THIS PC, in menu order: (key, name). Each is looked up through the OS
# (dialogs/browser.py's `known_folder`: SHGetKnownFolderPath on Windows,
# ~/<name> elsewhere) and left out if it isn't there.
KNOWN_PLACES = (("desktop", "Desktop"), ("documents", "Documents"), ("downloads", "Downloads"))

# Characters Windows doesn't allow in a file or folder name. Mac allows most
# of them, but a file named on one should open on the other.
ILLEGAL_NAME_CHARS = frozenset('\\/:*?"<>|')

# The lines under the Name and "Save in" rows.
NAME_EMPTY = "Type a name."
NAME_ILLEGAL = 'A name can\'t contain \\ / : * ? " < > |'
NAME_UNUSABLE = "That name can't be used. Choose another."
NAME_IS_SOURCE = "That's the original file. Choose another name."
NAME_IS_INPUT = "That's one of the files you chose. Choose another name."
NAME_IS_FILE = "A file already has that name. Choose another."
NAME_IS_FOLDER = "A folder already has that name. Choose another."
FOLDER_UNUSABLE = "Can't save in this folder. Choose another."

NAME_FIELD = "name"
FOLDER_FIELD = "folder"


class DestinationError(ValueError):
    """A folder or name a job can't write to: `field` is the row the
    sentence goes under, `reason` what a job refused for it reports
    (messages.message)."""

    def __init__(self, field: str, message: str, reason: str):
        super().__init__(message)
        self.field = field
        self.message = message
        self.reason = reason


@dataclass(frozen=True)
class Destination:
    path: str
    # Something is already there: a file is only replaced after the
    # "Replace?" question; a folder export takes the next free name instead.
    exists: bool


def writes_folder(tool: str) -> bool:
    return tool == messages.EXPORT


def extension(tool: str) -> str:
    """The part of the output's name the reader can't change."""
    return "" if writes_folder(tool) else PDF_EXTENSION


def base_stem(tool: str, source: str) -> str:
    """The output's name before a free one is found: "Report (compressed)",
    "Report (images)", or Convert's first file's own name."""
    stem = os.path.splitext(os.path.basename(source or ""))[0] or "untitled"
    labels = {
        messages.COMPRESS: naming.COMPRESSED,
        messages.SEARCHABLE: naming.SEARCHABLE,
        messages.EXPORT: naming.IMAGES,
    }
    label = labels.get(tool)
    return f"{stem} ({label})" if label else stem


def suggested_stem(tool: str, source: str, folder: str) -> str:
    """`base_stem`, or the first free "… (2)", (3), … in `folder`, so the
    suggestion never names something already there."""
    return naming.free_stem(folder, base_stem(tool, source), extension(tool))


def extract_stem(source: str, numbers: list[int], folder: str) -> str:
    """Extract's Name suggestion: "Report (pages 4–6)", "Report (5 pages)",
    or the first free "… (2)" in `folder`."""
    stem = os.path.splitext(os.path.basename(source or ""))[0] or "untitled"
    return naming.free_stem(folder, f"{stem} ({naming.pages_label(numbers)})", PDF_EXTENSION)


def folder_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def same_folder(a: str, b: str) -> bool:
    return isinstance(a, str) and isinstance(b, str) and bool(a) and bool(b) and folder_key(a) == folder_key(b)


def folder_label(folder: str) -> str:
    """A folder's own name ("Reports"); a drive's root is its path ("D:\\")."""
    return os.path.basename(os.path.normpath(folder)) or folder


def known_places() -> list[dict]:
    places = []
    for key, name in KNOWN_PLACES:
        path = browser.known_folder(name)
        if path.is_dir():
            places.append({"key": key, "name": name, "path": str(path)})
    return places


def menu(origin_folder: str) -> dict:
    """The "Save in" menu: the origin folder, RECENT (folders that are gone
    are forgotten first; one the menu already lists as the origin or on ON
    THIS PC isn't listed twice), and ON THIS PC."""
    known = known_places()
    recent = [
        {"path": folder, "name": folder_label(folder)}
        for folder in settings.prune_recent_output_folders()
        if not same_folder(folder, origin_folder) and not any(same_folder(folder, p["path"]) for p in known)
    ]
    return {
        "origin": {"path": origin_folder, "name": folder_label(origin_folder)},
        "recent": recent,
        "known": known,
    }


def remember(folder: str) -> list[str]:
    """A folder the reader chose: to the top of RECENT. Desktop, Documents
    and Downloads are always in the menu, so they take no RECENT slot."""
    if any(same_folder(folder, p["path"]) for p in known_places()):
        return settings.get_recent_output_folders()
    return settings.add_recent_output_folder(os.path.normpath(folder))


def where_phrase(folder: str, origin_folder: str, origin_phrase: str = BESIDE_ORIGINAL) -> str:
    """How the result line and the toast say where the output went:
    "beside the original", "on the Desktop", "in Documents", "in Reports"."""
    if same_folder(folder, origin_folder):
        return origin_phrase
    for place in known_places():
        if same_folder(folder, place["path"]):
            return "on the Desktop" if place["key"] == "desktop" else f"in {place['name']}"
    return f"in {folder_label(folder)}"


def is_writable_folder(folder) -> bool:
    """Whether a tool could write in `folder`: it exists, and an empty file
    can be made there and removed. Asked of the folder itself, since on
    Windows `os.access` only reads the read-only flag."""
    if not isinstance(folder, str) or not os.path.isabs(folder) or not os.path.isdir(folder):
        return False
    try:
        handle, probe = tempfile.mkstemp(prefix=".lector-check-", suffix=".tmp", dir=folder)
    except OSError:
        return False
    os.close(handle)
    try:
        os.remove(probe)
    except OSError:
        log.warning("Couldn't remove the writable check's empty file")
    return True


def name_error(stem) -> str | None:
    """The sentence for a name that can't be used, or None. Only the name:
    the extension is added after."""
    if not isinstance(stem, str) or not stem.strip():
        return NAME_EMPTY
    if any(c in ILLEGAL_NAME_CHARS or ord(c) < 32 for c in stem):
        return NAME_ILLEGAL
    if not stem.strip().strip("."):
        return NAME_UNUSABLE
    return None


def check(tool: str, folder, stem, sources: list[str]) -> Destination:
    """Where `tool`'s output goes for this folder and name, or
    DestinationError saying which row is wrong. Never one of `sources`:
    the tools never write over an original."""
    if not isinstance(folder, str) or not os.path.isabs(folder) or not os.path.isdir(folder):
        raise DestinationError(FOLDER_FIELD, FOLDER_UNUSABLE, safe_write.FOLDER_MISSING)
    if not is_writable_folder(folder):
        raise DestinationError(FOLDER_FIELD, FOLDER_UNUSABLE, safe_write.PERMISSION)
    problem = name_error(stem)
    if problem:
        raise DestinationError(NAME_FIELD, problem, messages.NAME_INVALID)
    path = os.path.join(folder, stem.strip() + extension(tool))
    if any(isinstance(s, str) and safe_write.same_file(path, s) for s in sources):
        sentence = NAME_IS_INPUT if tool == messages.CONVERT else NAME_IS_SOURCE
        raise DestinationError(NAME_FIELD, sentence, messages.SAME_AS_SOURCE)
    if writes_folder(tool) and os.path.lexists(path) and not os.path.isdir(path):
        raise DestinationError(NAME_FIELD, NAME_IS_FILE, messages.NAME_INVALID)
    if not writes_folder(tool) and os.path.isdir(path):
        raise DestinationError(NAME_FIELD, NAME_IS_FOLDER, messages.NAME_INVALID)
    return Destination(path, os.path.lexists(path))
