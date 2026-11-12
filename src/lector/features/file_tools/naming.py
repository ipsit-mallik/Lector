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
SPLIT = "split"
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


# ---------------------------------------------------------------------- #
# Split (Milestone F2)                                                     #
# ---------------------------------------------------------------------- #

# "Every [10] pages" starts at this, or at half the PDF when that is less.
DEFAULT_SPLIT_EVERY = 10

# Split's two ways of choosing the files.
SPLIT_EVERY = "every"
SPLIT_RANGES = "ranges"

# How many files the preview names before "and 9 more".
SPLIT_PREVIEW_NAMED = 3

# How many gaps the gap sentence lists before "and others".
SPLIT_GAPS_NAMED = 3

ONE_PAGE = "A PDF of one page can't be split."
RANGES_EMPTY = "Enter page ranges, like 1–10, 11–40."


def split_part_name(stem: str, number: int) -> str:
    """The `number`th file Split writes (1-based): "Report (part 1).pdf"."""
    return f"{stem} (part {number}).pdf"


def part_pages_label(first: int, last: int) -> str:
    """What one file holds, as the preview says it: "page 5", "pages 1–10"."""
    return f"page {first}" if first == last else f"pages {first}–{last}"


def default_split_every(page_count: int) -> int:
    """"Every N pages"' first N: 10, or half the pages (rounded up) when
    that is smaller, so a short PDF still splits in two."""
    return max(1, min(DEFAULT_SPLIT_EVERY, (page_count + 1) // 2))


def split_every(text, page_count: int) -> list[tuple[int, int]]:
    """"Every N pages": (first, last) 1-based for each file, the last one
    shorter when the pages don't divide evenly. N runs from 1 to one less
    than the page count (all the pages would be one file)."""
    if page_count < 2:
        raise RangeError(ONE_PAGE)
    raw = str(text).strip() if isinstance(text, (str, int)) and not isinstance(text, bool) else ""
    if not raw.isdigit() or not 1 <= int(raw) <= page_count - 1:
        raise RangeError(f"Enter a number of pages from 1 to {page_count - 1}.")
    n = int(raw)
    return [(first, min(first + n - 1, page_count)) for first in range(1, page_count + 1, n)]


def parse_split_ranges(text: str, page_count: int) -> list[tuple[int, int]]:
    """"Page ranges": each comma-separated part as its own file, in the
    order typed, as (first, last) 1-based. Unlike `parse_page_ranges`
    (one set of pages), parts may overlap, and pages no part names are
    simply not written (`uncovered_pages` says which). A hyphen, en dash or
    em dash joins a range; spaces anywhere. Raises RangeError for a part
    that isn't a page or range on the PDF, or one that runs backwards."""
    if page_count < 1:
        raise RangeError(ONE_PAGE)
    if not isinstance(text, str) or not text.strip():
        raise RangeError(RANGES_EMPTY)
    if len(text) > MAX_RANGE_CHARS:
        raise RangeError("That's too long for a list of pages.")
    parts = []
    for raw in text.split(","):
        shown = raw.strip()
        if not shown:
            continue
        match = _RANGE_PART.match(re.sub(r"\s+", "", shown).replace("–", "-").replace("—", "-"))
        outside = f"“{shown}” isn’t a page or range within 1–{page_count}."
        if not match:
            raise RangeError(outside)
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if 1 <= last < first <= page_count:
            raise RangeError(f"“{shown}” runs backwards. Try {last}–{first}.")
        if first < 1 or last > page_count or first > last:
            raise RangeError(outside)
        parts.append((first, last))
    if not parts:
        raise RangeError(RANGES_EMPTY)
    return parts


def uncovered_pages(parts: list[tuple[int, int]], page_count: int) -> list[tuple[int, int]]:
    """The runs of pages (first, last) that no part holds, in page order."""
    covered = [False] * (page_count + 2)
    for first, last in parts:
        for page in range(first, last + 1):
            covered[page] = True
    gaps, start = [], None
    for page in range(1, page_count + 2):
        missing = page <= page_count and not covered[page]
        if missing and start is None:
            start = page
        elif not missing and start is not None:
            gaps.append((start, page - 1))
            start = None
    return gaps


def gaps_sentence(gaps: list[tuple[int, int]]) -> str | None:
    """"Pages 121–130 aren't in any file.", or None when every page is."""
    if not gaps:
        return None
    if len(gaps) == 1 and gaps[0][0] == gaps[0][1]:
        return f"Page {gaps[0][0]} isn’t in any file."
    labels = [str(a) if a == b else f"{a}–{b}" for a, b in gaps]
    if len(labels) > SPLIT_GAPS_NAMED:
        listed = f"{', '.join(labels[:SPLIT_GAPS_NAMED])} and others"
    elif len(labels) == 1:
        listed = labels[0]
    else:
        listed = f"{', '.join(labels[:-1])} and {labels[-1]}"
    return f"Pages {listed} aren’t in any file."


def split_parts(mode, value, page_count: int) -> list[tuple[int, int]]:
    """The files Split writes for what the dialog chose, or RangeError."""
    if mode == SPLIT_EVERY:
        return split_every(value, page_count)
    if mode == SPLIT_RANGES:
        return parse_split_ranges(value, page_count)
    raise RangeError(RANGES_EMPTY)


def split_preview(mode, value, page_count: int, stem: str) -> dict:
    """The dialog's live preview: how many files, the first few by name
    and pages, how many more, and the gap sentence; or `{"error"}` for the
    line under the field."""
    try:
        parts = split_parts(mode, value, page_count)
    except RangeError as exc:
        return {"error": exc.message, "count": 0}
    return {
        "count": len(parts),
        "parts": [[first, last] for first, last in parts],
        "named": [
            {"name": split_part_name(stem, n + 1), "pages": part_pages_label(first, last)}
            for n, (first, last) in enumerate(parts[:SPLIT_PREVIEW_NAMED])
        ],
        "more": max(0, len(parts) - SPLIT_PREVIEW_NAMED),
        "gaps": gaps_sentence(uncovered_pages(parts, page_count)),
    }
