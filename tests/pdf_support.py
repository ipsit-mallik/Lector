"""Shared PDFs for the tests: built in code with PyMuPDF, never committed.

Each builder writes one kind of PDF a reader will meet into a folder the test
owns (usually a TemporaryDirectory) and returns its path. Building them here,
rather than in each test module, keeps "what an encrypted PDF looks like" or
"what a scanned page looks like" the same everywhere, and keeps binaries out
of `tests/fixtures/` (hardening H6).

Two builders lean on MuPDF (1.28.2 here): `truncated` relies on it repairing a
file cut off before its cross-reference table, and `unicode_text` on its
built-in Droid Sans Fallback font ("cjk"), which covers Latin, Greek, Cyrillic
and CJK. A PyMuPDF upgrade that changes either fails `test_pdf_support.py`
first.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the throwaway PDFs

PASSWORD = "correct horse"

PLAIN_TEXT = "The quick brown fox jumps over the lazy dog."
UNICODE_TEXT = "Café naïve façade — Ωmega Straße 東京 Привет"
SCANNED_TEXT = "These words are only pixels"

# A colour no palette entry is near, as another app might write it.
FOREIGN_RGB = (0.2, 0.8, 0.9)


def _page_with(doc: "pymupdf.Document", text: str) -> "pymupdf.Page":
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=12)
    return page


def _save(doc: "pymupdf.Document", path: Path, **options) -> Path:
    doc.save(path, **options)
    doc.close()
    return path


def page_text(n: int) -> str:
    """The words `multi_page` puts on page `n` (0-based)."""
    return f"Page {n + 1} talks about topic{n + 1} only."


def plain_text(folder: Path, name: str = "plain.pdf", text: str = PLAIN_TEXT) -> Path:
    """One born-digital page with one line of text."""
    doc = pymupdf.open()
    _page_with(doc, text)
    return _save(doc, Path(folder) / name)


def multi_page(folder: Path, pages: int = 5, name: str = "multi.pdf") -> Path:
    """`pages` pages, page n carrying `page_text(n)`."""
    doc = pymupdf.open()
    for n in range(pages):
        _page_with(doc, page_text(n))
    return _save(doc, Path(folder) / name)


def rotated(folder: Path, rotation: int = 90, name: str | None = None, text: str = PLAIN_TEXT) -> Path:
    """One page whose /Rotate is `rotation`: MuPDF reports its text on the
    unrotated page but renders it turned, the way a landscape scan or a
    sideways table arrives."""
    doc = pymupdf.open()
    _page_with(doc, text).set_rotation(rotation)
    return _save(doc, Path(folder) / (name or f"rotated-{rotation}.pdf"))


def image_only(folder: Path, name: str = "scanned.pdf", text: str = SCANNED_TEXT) -> Path:
    """One page that is nothing but a picture of text: no text layer, like a
    scanner's output before OCR."""
    source = pymupdf.open()
    pix = _page_with(source, text).get_pixmap(dpi=150)
    rect = source[0].rect
    source.close()
    doc = pymupdf.open()
    page = doc.new_page(width=rect.width, height=rect.height)
    page.insert_image(page.rect, pixmap=pix)
    return _save(doc, Path(folder) / name)


def encrypted(folder: Path, name: str = "locked.pdf", password: str = PASSWORD) -> Path:
    """`plain_text`'s page behind an AES-256 user password (the owner
    password is `password` reversed)."""
    doc = pymupdf.open()
    _page_with(doc, PLAIN_TEXT)
    return _save(
        doc, Path(folder) / name,
        encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password[::-1],
    )


def unicode_text(folder: Path, name: str = "unicode.pdf", text: str = UNICODE_TEXT) -> Path:
    """One page of accented, Greek, Cyrillic and CJK words in an embedded font."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontbuffer=pymupdf.Font("cjk").buffer)
    page.insert_text((72, 100), text, fontname="F0", fontsize=12)
    return _save(doc, Path(folder) / name)


def already_highlighted(folder: Path, name: str = "highlighted.pdf") -> Path:
    """`plain_text`'s page with two highlights already in the file: one over
    "quick brown" in a colour another app chose (`FOREIGN_RGB`, with a note
    and an author), and one over "lazy dog." with no colour of its own."""
    doc = pymupdf.open()
    page = _page_with(doc, PLAIN_TEXT)
    words = {w[4]: pymupdf.Rect(w[:4]) for w in page.get_text("words")}
    foreign = page.add_highlight_annot(words["quick"] | words["brown"])
    foreign.set_colors(stroke=FOREIGN_RGB)
    foreign.set_info(content="a note", title="someone")
    foreign.update()
    uncoloured = page.add_highlight_annot(words["lazy"] | words["dog."]).xref
    del foreign, page  # an Annot must not outlive its Page (see highlighter.py)
    # PyMuPDF gives every new highlight a yellow /C; another app may not.
    doc.xref_set_key(uncoloured, "C", "null")
    return _save(doc, Path(folder) / name)


def zero_byte(folder: Path, name: str = "zero.pdf") -> Path:
    """A file named .pdf with nothing in it."""
    path = Path(folder) / name
    path.write_bytes(b"")
    return path


def truncated(folder: Path, name: str = "truncated.pdf") -> Path:
    """`plain_text`'s bytes cut off just before the cross-reference table,
    as a download that stopped early leaves it. MuPDF repairs it on open."""
    path = plain_text(folder, name)
    data = path.read_bytes()
    path.write_bytes(data[:data.rfind(b"\nxref")])
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def highlights_on_disk(path: Path, password: str | None = None) -> list[list[tuple]]:
    """Each page's highlights as the file holds them, `(stroke, text)` in page
    order, read by a fresh PyMuPDF document rather than through Lector."""
    doc = pymupdf.open(path)
    try:
        if password is not None:
            doc.authenticate(password)
        pages = []
        for page in doc:
            found = []
            for annot in page.annots(types=[pymupdf.PDF_ANNOT_HIGHLIGHT]) or []:
                points = annot.vertices or []
                rects = [pymupdf.Quad(points[i:i + 4]).rect for i in range(0, len(points) - 3, 4)]
                text = " ".join(" ".join(page.get_textbox(r).split()) for r in rects)
                stroke = annot.colors.get("stroke")
                found.append((tuple(round(c, 3) for c in stroke) if stroke else None, text))
            pages.append(found)
        return pages
    finally:
        doc.close()
