"""Find in document: PyMuPDF's own text search, run in time-boxed slices.

A whole-document scan of a long PDF takes seconds, and a bridge call that long
would hold up every other call queued behind it (page images included). So a
search is a series of short calls instead: each covers as many pages as fit in
`SLICE_BUDGET_S` and says which page the next one starts at, and the frontend
shows what each slice found as it arrives. Superseding a search is then just a
matter of not asking for its next slice.

`Page.search_for` returns one rect per line a hit touches, with nothing to say
which rects belong to the same hit, so a phrase that wraps onto the next line
would count twice. `group_fragments` puts those pieces back together by
checking that their text joins up into the query.
"""
import re
import time
from typing import Callable

import fitz  # PyMuPDF

# Long enough to get through dozens of ordinary pages per call, short enough
# that a page render waiting behind the slice is never visibly late.
SLICE_BUDGET_S = 0.05

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    # MuPDF's search ignores case and treats any run of whitespace (a line
    # break included) as one space; comparing fragments has to do the same.
    return _WHITESPACE.sub(" ", text).strip().lower()


def _joins(acc: str, text: str) -> list[str]:
    """The ways a hit's next line can continue it: after a space, straight
    on, or with the end-of-line hyphen MuPDF matched through dropped."""
    joined = [f"{acc} {text}", acc + text]
    if acc.endswith("-"):
        joined.append(acc[:-1] + text)
    return [_normalize(j) for j in joined]


def group_fragments(fragments: list[tuple[fitz.Rect, str]], needle: str) -> list[list[fitz.Rect]]:
    """Group `search_for`'s per-line rects into one list of rects per hit.

    `fragments` pairs each rect with the text inside it, in the order
    `search_for` returned them. A fragment that is the whole query is a hit
    on its own; one that is only the start of it is joined with the
    fragments after it for as long as they keep spelling out the query. A
    run that never completes is still kept, as one match: drawing it is
    better than losing a hit the search did find.
    """
    target = _normalize(needle)
    hits: list[list[fitz.Rect]] = []
    i = 0
    while i < len(fragments):
        rect, text = fragments[i]
        group, acc = [rect], _normalize(text)
        i += 1
        while acc != target and i < len(fragments):
            nxt_rect, nxt_text = fragments[i]
            joined = next((j for j in _joins(acc, nxt_text) if target.startswith(j)), None)
            if joined is None:
                break
            group.append(nxt_rect)
            acc = joined
            i += 1
        hits.append(group)
    return hits


def _search_page(page: fitz.Page, needle: str) -> tuple[bool, list[list[fitz.Rect]]]:
    """Whether the page has any text at all, and its hits."""
    textpage = page.get_textpage()
    if not textpage.extractText().strip():
        return False, []
    rects = page.search_for(needle, textpage=textpage)
    fragments = [(r, textpage.extractTextbox(r)) for r in rects]
    return True, group_fragments(fragments, needle)


def search_slice(
    doc: fitz.Document,
    needle: str,
    start: int,
    end: int | None,
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Search pages `start` up to (not including) `end` until the budget runs out.

    `end` is None for "to the last page". The budget is checked after each
    page, so a slice always gets through at least one; it defaults to
    `SLICE_BUDGET_S`, read at call time. `next_page` is where
    the following slice should start, or None once `end` is reached.
    `text_pages` counts the pages that had any text, so the caller can tell
    "no matches" apart from "nothing here can be searched" (a scanned PDF).
    Rects are in PDF points, in the same space as `get_page_chars`.
    """
    stop = doc.page_count if end is None else min(end, doc.page_count)
    pages: list[dict] = []
    text_pages = 0
    budget = SLICE_BUDGET_S if budget_s is None else budget_s
    began = clock()
    index = start
    while index < stop:
        page = doc[index]
        has_text, hits = _search_page(page, needle)
        text_pages += has_text
        if hits:
            pages.append({
                "page_index": index,
                "width": page.rect.width,
                "height": page.rect.height,
                "hits": [[[r.x0, r.y0, r.x1, r.y1] for r in hit] for hit in hits],
            })
        index += 1
        if clock() - began >= budget:
            break
    return {"pages": pages, "next_page": index if index < stop else None, "text_pages": text_pages}
