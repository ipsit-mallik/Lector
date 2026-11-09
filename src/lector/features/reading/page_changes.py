"""The reader's undo entry for applied page changes, without PyMuPDF.

Done in Organise pages applies a plan to the open document as one entry on
`PdfDocument`'s undo stack (docs/ARCHITECTURE.md § "Page changes (Milestone
F2)"). That entry always holds exactly one other state of the document — the
one before the change while it is applied, the one after while it is undone
— and undo and redo swap it with the open one. A state is the document's
bytes plus what the highlight edits on the stack name: the live xref -> ref
map, and the xref every shared ref pointed at.

What lives here is the bookkeeping: the entry, which page the reader is on
after a change, and the caps on how many changes (and bytes) are held.
`PdfDocument` takes and restores the states.
"""
from dataclasses import dataclass

# At most this many page-change entries across both stacks, holding at most
# this many bytes of document between them. Past either, the oldest one and
# everything below it on the undo stack is dropped.
MAX_PAGE_CHANGES = 3
MAX_PAGE_CHANGE_BYTES = 300 * 1024 * 1024

# What the reader's Undo and Redo would take back (`PdfDocument.undo_kind`).
HIGHLIGHT = "highlight"
PAGE_CHANGES = "page_changes"

# Why a PDF's pages can't be changed (`PdfDocument.page_change_block`).
NOT_LOADED = "not_loaded"
RESTRICTED = "restricted"
BLOCK_MESSAGES = {
    NOT_LOADED: "The PDF hasn't finished opening yet.",
    RESTRICTED: "This PDF's owner doesn't allow changing its pages.",
}


@dataclass(frozen=True)
class DocState:
    """One state of the open document: `data` from `tobytes` without
    garbage collection (so xrefs survive) with its encryption kept; `refs`
    the live xref -> ref map; `ref_xrefs` each ref the stacks name, with the
    xref it pointed at."""

    data: bytes
    refs: dict
    ref_xrefs: tuple[tuple[object, int], ...]


@dataclass
class PageChange:
    """One applied page plan on the undo or redo stack.

    `held` is the state that isn't open: swapped on every undo and redo, so
    the entry is mutable. `order` says, for each page after the change,
    which page it was before (None for an inserted page), so the reader
    stays on the page it was reading."""

    held: DocState
    order: tuple[int | None, ...]

    @property
    def size(self) -> int:
        return len(self.held.data)


def page_after(order: tuple[int | None, ...], before: int) -> int:
    """Where page `before` is after the change; if it was deleted, the
    nearest earlier page that wasn't; failing that, the first page."""
    best_position, best_index = 0, -1
    for position, was in enumerate(order):
        if was == before:
            return position
        if was is not None and best_index < was < before:
            best_position, best_index = position, was
    return best_position


def page_before(order: tuple[int | None, ...], after: int) -> int:
    """Which page `after` was before the change; for an inserted page, the
    nearest earlier page that was there before; failing that, the first."""
    for position in range(min(after, len(order) - 1), -1, -1):
        if order[position] is not None:
            return order[position]
    return 0


def _over_caps(entries: list, max_count: int, max_bytes: int) -> bool:
    changes = [entry for entry in entries if isinstance(entry, PageChange)]
    return len(changes) > max_count or sum(change.size for change in changes) > max_bytes


def _first_change(stack: list) -> int | None:
    return next((i for i, entry in enumerate(stack) if isinstance(entry, PageChange)), None)


def trim(
    undo: list,
    redo: list,
    max_count: int | None = None,
    max_bytes: int | None = None,
) -> tuple[list, list, bool]:
    """`undo` and `redo` within the caps (by default `MAX_PAGE_CHANGES` and
    `MAX_PAGE_CHANGE_BYTES`, read at the call), and whether anything was
    dropped.

    The oldest page change goes first, with everything below it on the undo
    stack: an edit below a dropped change names pages as they were before
    it, so it could never be reached again. With none left on the undo
    stack, the farthest one on the redo stack goes, with everything beyond
    it."""
    max_count = MAX_PAGE_CHANGES if max_count is None else max_count
    max_bytes = MAX_PAGE_CHANGE_BYTES if max_bytes is None else max_bytes
    undo, redo, trimmed = list(undo), list(redo), False
    while _over_caps(undo + redo, max_count, max_bytes):
        cut = _first_change(undo)
        if cut is not None:
            undo = undo[cut + 1:]
        else:
            redo = redo[_first_change(redo) + 1:]
        trimmed = True
    return undo, redo, trimmed
