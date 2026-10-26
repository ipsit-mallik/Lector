"""Why a PDF didn't open, in words for the reader (hardening H5).

`PdfDocument.open` raises `OpenError` with one of these reasons, and the
frontend shows `MESSAGES[reason]`. The library's own text never reaches the
reader: it names paths and MuPDF error codes. No message names the file
either, since the page that shows it already knows which one it was.

`PASSWORD` and `WRONG_PASSWORD` are not shown as toasts: the first opens the
password dialog, the second is the error line under its field.
"""

MISSING = "missing"
EMPTY = "empty"
NOT_A_PDF = "not_a_pdf"
NO_PAGES = "no_pages"
UNREADABLE = "unreadable"
PASSWORD = "password"
WRONG_PASSWORD = "wrong_password"
UNKNOWN = "unknown"

MESSAGES = {
    MISSING: "The PDF couldn't be found. It may have been moved or deleted.",
    EMPTY: "The PDF is empty, so there's nothing to open.",
    NOT_A_PDF: "Couldn't open the file. It's damaged or isn't a PDF.",
    NO_PAGES: "The PDF has no pages to show.",
    UNREADABLE: "Couldn't read the PDF. Another program may be using it, "
    "or Lector isn't allowed to open it.",
    PASSWORD: "The PDF is protected by a password.",
    WRONG_PASSWORD: "The password isn't right. Try again.",
    UNKNOWN: "Couldn't open the PDF. It may be damaged or no longer there.",
}


class OpenError(Exception):
    """A PDF that could not be opened, and why (one of the reasons above)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

    @property
    def message(self) -> str:
        return MESSAGES[self.reason]
