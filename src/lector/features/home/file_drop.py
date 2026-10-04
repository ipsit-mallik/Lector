"""Window-wide drag-and-drop of a PDF onto Lector (Milestone 8.12).

pywebview hands Python a `drop` DOM event whose files carry the real on-disk
location as `pywebviewFullPath` — the page's own JavaScript only ever sees a
file's name, never its path, which is why this is resolved here and not in
`frontend/`. This module answers one question about that event: which PDF, if
any, should Lector open?

It decides nothing about *opening*. The page that receives the result does
that, because what opening means depends on the screen — Home opens it
straight away, while Reading must first ask about unsaved highlights.
"""
import os

PDF_SUFFIX = ".pdf"

NOT_A_PDF = "Lector can only open PDF files."
UNREADABLE = "Couldn't find where that file is. Try Open PDF instead."
# A name ending .pdf that is not a file on disk (moved or deleted since the
# drag began, or a folder). Not "not a PDF": the reader dropped what looks like
# one, and telling them otherwise would be wrong.
MISSING = "That PDF couldn't be found. It may have been moved or deleted."


def pdf_from_drop(event: dict) -> dict | None:
    """`{"path": ...}` for the PDF to open, `{"error": ...}` for a drop that
    carried files but no usable PDF, or `None` when it carried no files at all.

    `None` is deliberately not an error: dragging selected text around a page
    also fires `drop`, and answering that with a message would be noise.
    When several files are dropped the first PDF wins — Lector shows one
    document at a time, and refusing a mixed selection would be unkind.
    """
    files = ((event or {}).get("dataTransfer") or {}).get("files") or []
    if not files:
        return None

    paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
    if not paths:
        return {"error": UNREADABLE}

    pdf_names = [p for p in paths if p.lower().endswith(PDF_SUFFIX)]
    for path in pdf_names:
        if os.path.isfile(path):
            return {"path": path}
    return {"error": MISSING if pdf_names else NOT_A_PDF}
