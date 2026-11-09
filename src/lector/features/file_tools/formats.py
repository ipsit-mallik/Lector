"""Which files Convert to PDF takes. Pure constants, shared by the engine,
the Open dialog's "convert" filter (`dialogs/browser.py`) and drag-and-drop
(`home/file_drop.py`), so the three can't disagree.

WebP is not here: the MuPDF in the pinned PyMuPDF (1.28.2) can't decode it
("unknown image file format"), checked with two valid sample files.
"""

IMAGE_EXTENSIONS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".bmp": "BMP",
    ".gif": "GIF",
}

EBOOK_EXTENSIONS = {
    ".epub": "EPUB",
    ".xps": "XPS",
    ".oxps": "XPS",
    ".cbz": "CBZ",
    ".fb2": "FB2",
}

# MuPDF's own name for each type, for `pymupdf.open(filetype=...)`, which
# otherwise guesses from the content.
FILETYPES = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".bmp": "bmp",
    ".gif": "gif",
    ".epub": "epub",
    ".xps": "xps",
    ".oxps": "xps",
    ".cbz": "cbz",
    ".fb2": "fb2",
}

CONVERT_EXTENSIONS = frozenset(IMAGE_EXTENSIONS) | frozenset(EBOOK_EXTENSIONS)

# A4 and US Letter, in PDF points. E-books that flow (EPUB, FB2) are laid
# out on A4.
A4 = (595.0, 842.0)
LETTER = (612.0, 792.0)
SAME_AS_IMAGE = "image"
PAGE_SIZES = {"a4": A4, "letter": LETTER}
PAGE_SIZE_CHOICES = (SAME_AS_IMAGE, "a4", "letter")

# An image's own size: one image pixel per CSS pixel, 96 to the inch.
NATIVE_PPI = 96


def extension(path: str) -> str:
    """`path`'s extension, lower-cased, with the dot; "" if it has none."""
    dot = path.rfind(".")
    return path[dot:].lower() if dot > max(path.rfind("/"), path.rfind("\\")) else ""


def is_convertible(path: str) -> bool:
    return extension(path) in CONVERT_EXTENSIONS


def is_ebook(path: str) -> bool:
    return extension(path) in EBOOK_EXTENSIONS


def type_label(path: str) -> str:
    ext = extension(path)
    return IMAGE_EXTENSIONS.get(ext) or EBOOK_EXTENSIONS.get(ext) or ""
