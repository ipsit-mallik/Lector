"""The PDF's own outline (bookmarks), shaped for the reader's outline panel
and progress bar.

Pure functions over PyMuPDF's `get_toc()` rows (`[level, title, page]`, page
1-based, -1 or 0 when the bookmark points nowhere this document can resolve),
so they can be tested without opening a file.

* `outline_tree` nests the flat rows by level, for the panel's collapsible
  tree. An entry whose page cannot be resolved is kept (its children may still
  point somewhere) with `page_index` None, which the panel shows as not
  clickable.
* `chapter_segments` turns the top-level entries into the progress bar's
  segments: one per chapter, covering every page exactly once, in page order.
"""
from typing import TypedDict


class OutlineEntry(TypedDict):
    title: str
    page_index: int | None
    children: list["OutlineEntry"]


class Segment(TypedDict):
    title: str | None
    start: int
    end: int


def _page_index(page: object, page_count: int) -> int | None:
    """A toc row's 1-based page as a 0-based index, or None if it points
    outside the document (MuPDF gives -1 for a destination it can't resolve)."""
    if not isinstance(page, int) or not 1 <= page <= page_count:
        return None
    return page - 1


def _title(raw: object) -> str:
    # Bookmark titles are free text from whoever made the PDF: newlines and
    # runs of spaces are common, and an empty one still needs something to
    # click on.
    title = " ".join(str(raw or "").split())
    return title or "Untitled"


def outline_tree(toc: list, page_count: int) -> list[OutlineEntry]:
    """Nest `toc` rows into a tree by their level.

    A row whose level jumps more than one deeper than the row before it (a
    malformed outline) is attached to the nearest open parent rather than
    dropped, so nothing the PDF lists goes missing.
    """
    root: list[OutlineEntry] = []
    # (level, children list) of the entries a new row could hang under.
    stack: list[tuple[int, list[OutlineEntry]]] = [(0, root)]
    for row in toc:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        level = row[0] if isinstance(row[0], int) and row[0] > 0 else 1
        entry: OutlineEntry = {
            "title": _title(row[1]),
            "page_index": _page_index(row[2], page_count),
            "children": [],
        }
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        stack[-1][1].append(entry)
        stack.append((level, entry["children"]))
    return root


def chapter_segments(tree: list[OutlineEntry], page_count: int) -> list[Segment]:
    """One segment per top-level chapter, `start`/`end` 0-based and
    inclusive, together covering pages 0 to `page_count - 1` with no gaps.

    Chapters are taken in page order (an outline is not always), and two that
    start on the same page are one segment, named after the first. Pages before
    the first chapter (a cover, a contents page) are a leading segment with no
    title. Empty when there are no usable chapters, which the bar shows as one
    continuous fill instead.
    """
    starts: list[tuple[int, str | None]] = []
    for entry in tree:
        page = entry["page_index"]
        if page is None or any(start == page for start, _ in starts):
            continue
        starts.append((page, entry["title"]))
    if not starts or page_count <= 0:
        return []
    starts.sort(key=lambda item: item[0])
    if starts[0][0] > 0:
        starts.insert(0, (0, None))
    return [
        {
            "title": title,
            "start": start,
            "end": (starts[i + 1][0] - 1) if i + 1 < len(starts) else page_count - 1,
        }
        for i, (start, title) in enumerate(starts)
    ]
