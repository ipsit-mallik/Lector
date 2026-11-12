"""Names for what the file tools write, and the page-range field's parser.

Pure: no PyMuPDF, nothing written. The only disk access is `os.path.exists`,
to skip names that are taken.
"""
import os
import re

COMPRESSED = "compressed"
SEARCHABLE = "searchable"
IMAGES = "images"
COMBINED = "combined"
# Save a copy's suggestion, here so Save and the tools share one rule.
HIGHLIGHTED = "highlighted"

# Image files are numbered with at least this many digits ("-p001").
MIN_PAGE_DIGITS = 3

# A range field longer than this is not a list of pages anyone typed.
MAX_RANGE_CHARS = 500

_RANGE_PART = re.compile(r"^(\d+)(?:-(\d+))?$")


def pages_label(numbers: list[int]) -> str:
    """Some pages, as Extract's name and the delete question say them (1-based
    numbers): "page 4", "pages 4–6" for a run, "5 pages" for a scattered
    selection. Organise's `describePageNumbers` says the same."""
    ordered = sorted(set(numbers))
    if len(ordered) == 1:
        return f"page {ordered[0]}"
    if ordered[-1] - ordered[0] + 1 == len(ordered):
        return f"pages {ordered[0]}–{ordered[-1]}"
    return f"{len(ordered)} pages"


def free_file_name(source: str, label: str) -> str:
    """`<name> (label).pdf` beside `source`, or the first free
    `<name> (label) (2).pdf`, (3), …, so the suggestion never names a file
    that is already there."""
    base, ext = os.path.splitext(source or "untitled.pdf")
    return _first_free(f"{base} ({label})", ext or ".pdf")


def free_folder_name(folder: str) -> str:
    """`folder`, or the first free `folder (2)`, (3), … beside it."""
    return _first_free(folder, "")


def free_stem(folder: str, stem: str, ext: str) -> str:
    """`stem`, or the first free `stem (2)`, (3), … in `folder` (the name
    without `ext`, for the tools' Name field, which shows the extension
    apart)."""
    candidate = stem
    number = 2
    while os.path.lexists(os.path.join(folder, f"{candidate}{ext}")):
        candidate = f"{stem} ({number})"
        number += 1
    return candidate


def _first_free(base: str, ext: str) -> str:
    candidate = f"{base}{ext}"
    number = 2
    while os.path.exists(candidate):
        candidate = f"{base} ({number}){ext}"
        number += 1
    return candidate


def page_digits(page_count: int) -> int:
    """How many digits image numbers get: the page count's width, at least
    three, so files sort in page order in any file manager."""
    return max(MIN_PAGE_DIGITS, len(str(max(1, page_count))))


def image_file_name(stem: str, page_number: int, page_count: int, extension: str) -> str:
    """`<stem>-p001.png`: numbered by the PDF's own page number (1-based),
    not by the order of export."""
    return f"{stem}-p{page_number:0{page_digits(page_count)}d}.{extension}"


class RangeError(ValueError):
    """A page range that can't be used, with a sentence for the field."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse_page_ranges(text: str, page_count: int) -> list[int]:
    """The 1-based page numbers `text` names, in page order, each once.

    Accepts "1–5, 8, 11–13": commas between parts, a hyphen or en dash (or
    em dash) inside one, spaces anywhere. Raises RangeError for anything
    else, a page past the end, or a range that runs backwards.
    """
    if not isinstance(text, str) or not text.strip():
        raise RangeError("Type the pages to export, such as 1–5, 8.")
    if len(text) > MAX_RANGE_CHARS:
        raise RangeError("That's too long for a list of pages.")
    cleaned = re.sub(r"\s+", "", text).replace("–", "-").replace("—", "-")
    pages: set[int] = set()
    for part in cleaned.split(","):
        if not part:
            continue
        match = _RANGE_PART.match(part)
        if not match:
            raise RangeError("Use page numbers and ranges, such as 1–5, 8.")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1:
            raise RangeError("Pages start at 1.")
        if first > last:
            raise RangeError(f"{first}–{last} runs backwards. Try {last}–{first}.")
        if last > page_count:
            raise RangeError(f"This PDF has {page_count} {'page' if page_count == 1 else 'pages'}.")
        pages.update(range(first, last + 1))
    if not pages:
        raise RangeError("Type the pages to export, such as 1–5, 8.")
    return sorted(pages)
