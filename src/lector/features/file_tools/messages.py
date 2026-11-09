"""What the reader is told when a file tool fails.

Follows Save's rules (`reading/safe_write.py`): say what happened in plain
words and that the original is unchanged, never the library's own text or a
path. Save's own sentences end "your highlights are still open", which is
not what a tool has to say, so the tools have these.
"""
from lector.features.reading import safe_write

COMPRESS = "compress"
SEARCHABLE = "searchable"
EXPORT = "export"
CONVERT = "convert"
TOOLS = (COMPRESS, SEARCHABLE, EXPORT, CONVERT)

# Reasons of the tools' own, beside `safe_write`'s.
SAME_AS_SOURCE = "same_as_source"
SOURCE_UNREADABLE = "source_unreadable"
WORKER_FAILED = "worker_failed"
INPUT_UNREADABLE = "input_unreadable"
OCR_UNAVAILABLE = "ocr_unavailable"
NAME_INVALID = "name_invalid"

_WHAT = {
    COMPRESS: "compress the PDF",
    SEARCHABLE: "make the PDF searchable",
    EXPORT: "export the pages",
    CONVERT: "create the PDF",
}

_DETAIL = {
    safe_write.READ_ONLY: "a file with that name is read-only.",
    safe_write.PERMISSION: "Lector isn't allowed to write to that folder.",
    safe_write.LOCKED: "another program is using a file with that name. Close it there and try again.",
    safe_write.DISK_FULL: "the disk is full.",
    safe_write.FOLDER_MISSING: "that folder no longer exists.",
    safe_write.FILE_MISSING: "the file was moved or deleted while Lector was working on it.",
    safe_write.DRIVE_GONE: "the drive or network location isn't available.",
    safe_write.VERIFY_FAILED: "the new file didn't check out, so it was discarded.",
    SAME_AS_SOURCE: "that's the original file. Choose another name: the tools never change it.",
    SOURCE_UNREADABLE: "the file couldn't be read. It may have been moved, deleted or damaged.",
    WORKER_FAILED: "something went wrong while working on the file.",
    INPUT_UNREADABLE: "one of the files couldn't be read. It may have been moved, deleted or damaged.",
    OCR_UNAVAILABLE: "the text reader's English language data isn't installed.",
    NAME_INVALID: "that name can't be used. Choose another.",
}

_UNCHANGED = {CONVERT: "The original files are unchanged."}
_UNCHANGED_DEFAULT = "The original is unchanged."


def unchanged(tool: str) -> str:
    return _UNCHANGED.get(tool, _UNCHANGED_DEFAULT)


def message(reason: str, tool: str) -> str:
    """The sentence for `reason` (a `safe_write` reason or one above),
    raised while running `tool`. Always ends saying the original is
    unchanged."""
    what = _WHAT.get(tool, "finish")
    detail = _DETAIL.get(reason)
    if detail is None:
        return f"Couldn't {what}. {unchanged(tool)}"
    return f"Couldn't {what}: {detail} {unchanged(tool)}"
