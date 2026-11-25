"""A PDF at a glance, for a tool's dialog from Home (docs/ARCHITECTURE.md §
"File tools" → "What a dialog knows first"): its page count, its first
page's size, whether it needs a password, whether it is signed, and whether
its pages may be changed. Nothing here reads every page, so it runs in the
main process, on the bridge thread that asks, in a few milliseconds (a
109 MB, 320-page scan: about 10 ms), instead of queueing behind whatever the
worker is doing. What does read every page (Make searchable's pages with no
text) is `engine.textless_pages`, in the worker.

The document is opened and closed within the call: nothing is left open in
the main process. Passwords are only ever held for the call.
"""
import os

import pymupdf

from lector.shared.mupdf_lock import HOME_PYMUPDF_LOCK

# PyMuPDF must not run on two threads at once: two looks (a selection and a
# Continue), and a look beside Home's thumbnails, take turns.
_lock = HOME_PYMUPDF_LOCK


class Unreadable(Exception):
    """The file isn't a PDF MuPDF can open, or it has no pages."""


def look(path: str, password: str | None) -> dict:
    """`{pages, page_size, signed, needs_pass, can_change_pages, size}` for
    the PDF at `path`, or `{"locked": True}` when it needs a password and
    `password` is missing or wrong. Raises Unreadable."""
    with _lock:
        try:
            doc = pymupdf.open(path, filetype="pdf")
        except Exception:
            raise Unreadable() from None
        try:
            # `needs_pass` is read once, before `authenticate`: PyMuPDF 1.28
            # locks a document again when it is read afterwards.
            needs = bool(doc.needs_pass)
            if needs and not (password and doc.authenticate(password)):
                return {"locked": True}
            count = len(doc)
            if count == 0:
                raise Unreadable()
            rect = doc[0].rect
            info = {
                "pages": count,
                "page_size": [round(rect.width, 2), round(rect.height, 2)],
                "signed": doc.get_sigflags() > 0,
                # Split's check, as the reader's `page_change_block`: MuPDF
                # doesn't enforce a PDF's permissions, so Lector does.
                "can_change_pages": bool(doc.permissions & (pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE)),
                "needs_pass": needs,
            }
        finally:
            doc.close()
    info["size"] = os.path.getsize(path)
    return info
