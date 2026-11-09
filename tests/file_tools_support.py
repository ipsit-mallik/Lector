"""Fixtures and helpers for the file tools' tests (Milestone F1).

Every file is built here in code, small, into a folder the test owns; nothing
binary is committed. `InlineContext` runs a tool's steps in this process, so
the engine can be tested without spawning the worker; `slow_step` and
`quick_step` are module-level so the real worker can be sent them.
"""
import io
import struct
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the throwaway inputs

from lector.features.file_tools.jobs import JobCancelled  # noqa: E402

# A colour photo's pixel size, and the box it is drawn in: 900 px across a
# 150 pt box is 432 ppi, well over every level's threshold, and small enough
# to keep the tests quick.
PHOTO_PX = (900, 600)
PHOTO_BOX = pymupdf.Rect(50, 50, 200, 150)
PHOTO_PPI = PHOTO_PX[0] / (PHOTO_BOX.width / 72)

KNOWN_WORD = "Lighthouse"


def _noisy_photo(width: int, height: int, alpha: bool = False) -> "pymupdf.Pixmap":
    """Stripes of varied colour: compresses about as badly as a photo."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), 1 if alpha else 0)
    pix.clear_with(200)
    for x in range(0, width, 7):
        shade = (x * 37) % 256
        colour = (shade, (shade * 3) % 256, 255 - shade)
        pix.set_rect(pymupdf.IRect(x, 0, x + 4, height), colour + ((255,) if alpha else ()))
    return pix


def _save(doc: "pymupdf.Document", path: Path, **options) -> Path:
    doc.save(path, **options)
    doc.close()
    return path


def photo_pdf(folder: Path, name: str = "photos.pdf", pages: int = 1) -> Path:
    """Pages each with a high-resolution photo, a line of text, and one of
    each annotation a reader makes in Lector (highlight at 60% Multiply,
    underline, strikeout, an ink stroke)."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page()
        page.insert_image(PHOTO_BOX, pixmap=_noisy_photo(*PHOTO_PX))
        page.insert_text((50, 300), f"Page {n + 1} {KNOWN_WORD} caption", fontsize=12)
        line = page.search_for(KNOWN_WORD)[0]
        highlight = page.add_highlight_annot(line)
        highlight.set_colors(stroke=(1, 0.85, 0.2))
        highlight.set_opacity(0.6)
        highlight.set_blendmode("Multiply")
        highlight.update()
        page.add_underline_annot(line).update()
        page.add_strikeout_annot(line).update()
        ink = page.add_ink_annot([[(60, 400), (120, 420), (200, 410)]])
        ink.set_border(width=14)
        ink.set_opacity(0.6)
        ink.update()
    return _save(doc, Path(folder) / name)


def alpha_photo_pdf(folder: Path, name: str = "alpha.pdf") -> Path:
    """One photo with an alpha channel, which MuPDF stores as a soft mask."""
    doc = pymupdf.open()
    doc.new_page().insert_image(PHOTO_BOX, pixmap=_noisy_photo(*PHOTO_PX, alpha=True))
    return _save(doc, Path(folder) / name)


def low_res_photo_pdf(folder: Path, name: str = "lowres.pdf") -> Path:
    """A photo at 72 ppi: under every threshold, so no level resamples it."""
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 300, 200), pixmap=_noisy_photo(300, 200))
    return _save(doc, Path(folder) / name)


def bilevel_scan_pdf(folder: Path, name: str = "bilevel.pdf") -> Path:
    """A 1-bit "fax" scan at 600 ppi: must stay 1-bit and lossless."""
    width = height = 1200
    row = bytes([0b10101010, 0b11110000] * (width // 16))
    pbm = b"P4\n%d %d\n" % (width, height) + row * height
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 144, 144), stream=pbm)
    return _save(doc, Path(folder) / name, deflate=True)


def scanned_text_pdf(folder: Path, name: str = "scan.pdf", rotation: int = 0, with_text_page: bool = True) -> Path:
    """A page that is a picture of large, clean text (no text layer) and,
    unless told otherwise, an ordinary text page after it. With `rotation`,
    the picture is stored turned and the page's /Rotate shows it upright, the
    way a scanner stores a page fed in sideways."""
    source = pymupdf.open()
    page = source.new_page(width=420, height=300)
    page.insert_text((40, 120), f"The {KNOWN_WORD} stands on the rocky shore", fontsize=20)
    page.insert_text((40, 170), "and its light can be seen for miles", fontsize=20)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72).prerotate(-rotation))
    source.close()
    doc = pymupdf.open()
    width, height = (300, 420) if rotation in (90, 270) else (420, 300)
    scan = doc.new_page(width=width, height=height)
    scan.insert_image(scan.rect, pixmap=pix)
    scan.set_rotation(rotation)
    if with_text_page:
        doc.new_page().insert_text((72, 100), "This page already has text.", fontsize=12)
    return _save(doc, Path(folder) / name)


def signed_flag_pdf(folder: Path, name: str = "signed.pdf") -> Path:
    """A PDF whose form says it is signed (/SigFlags 3), which is what MuPDF's
    `get_sigflags` reads. No real signature: only the flag is tested."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Signed contract", fontsize=12)
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", "<< /SigFlags 3 /Fields [] >>")
    return _save(doc, Path(folder) / name)


def png_file(folder: Path, name: str, size: tuple[int, int], shade: int = 120) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, *size), 0)
    pix.clear_with(shade)
    path = Path(folder) / name
    pix.save(path, output="png")
    return path


def jpeg_file(folder: Path, name: str, size: tuple[int, int], shade: int = 60) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, *size), 0)
    pix.clear_with(shade)
    path = Path(folder) / name
    path.write_bytes(pix.tobytes("jpeg"))
    return path


def tiff_file(folder: Path, name: str = "frames.tif", frames: int = 3, size: tuple[int, int] = (16, 12)) -> Path:
    """An uncompressed greyscale TIFF with `frames` pages, written by hand
    (PyMuPDF reads TIFF but can't write it)."""
    width, height = size
    out = io.BytesIO()
    out.write(b"II*\x00" + struct.pack("<I", 0))
    previous_link = 4
    for frame in range(frames):
        data_offset = out.tell()
        out.write(bytes([(frame * 80) % 256]) * (width * height))
        if out.tell() % 2:
            out.write(b"\x00")
        ifd_offset = out.tell()
        entries = [
            (256, 3, width),  # ImageWidth
            (257, 3, height),  # ImageLength
            (258, 3, 8),  # BitsPerSample
            (259, 3, 1),  # Compression: none
            (262, 3, 1),  # Photometric: black is zero
            (273, 4, data_offset),  # StripOffsets
            (277, 3, 1),  # SamplesPerPixel
            (278, 3, height),  # RowsPerStrip
            (279, 4, width * height),  # StripByteCounts
        ]
        out.write(struct.pack("<H", len(entries)))
        for tag, kind, value in entries:
            packed = struct.pack("<HH", value, 0) if kind == 3 else struct.pack("<I", value)
            out.write(struct.pack("<HHI", tag, kind, 1) + packed)
        link = out.tell()
        out.write(struct.pack("<I", 0))
        end = out.tell()
        out.seek(previous_link)
        out.write(struct.pack("<I", ifd_offset))
        out.seek(end)
        previous_link = link
    path = Path(folder) / name
    path.write_bytes(out.getvalue())
    return path


def epub_file(folder: Path, name: str = "book.epub", paragraphs: int = 300) -> Path:
    """A minimal EPUB 2 with one chapter long enough for several A4 pages."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        epub.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>Field notes</dc:title><dc:identifier id="id">x</dc:identifier>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        )
        body = "".join(f"<p>Paragraph {n} of the field notes, with a few words.</p>" for n in range(paragraphs))
        epub.writestr(
            "OEBPS/c1.xhtml",
            f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>',
        )
    path = Path(folder) / name
    path.write_bytes(buffer.getvalue())
    return path


def image_sizes(path: Path) -> list[tuple[int, int, int, int]]:
    """Every image object in a PDF as (xref, width, height, bits per
    component)."""
    doc = pymupdf.open(path)
    try:
        seen = {}
        for page in doc:
            for image in page.get_images(full=True):
                seen[image[0]] = (image[0], image[2], image[3], image[4])
        return sorted(seen.values())
    finally:
        doc.close()


def annotations(path: Path, password: str | None = None) -> list[list[tuple]]:
    """Per page: each annotation's type, rect, colour, opacity and blend
    mode, rounded, in page order."""
    doc = pymupdf.open(path)
    if password:
        doc.authenticate(password)
    try:
        out = []
        for page in doc:
            row = []
            for annot in page.annots():
                rect = tuple(round(v, 1) for v in annot.rect)
                stroke = tuple(round(c, 3) for c in (annot.colors.get("stroke") or ()))
                row.append((annot.type[1], rect, stroke, round(annot.opacity, 2), annot.blendmode))
            out.append(row)
        return out
    finally:
        doc.close()


class InlineContext:
    """A `jobs.JobContext` that runs each step here and now. `cancel_at`
    cancels once that many steps have run; `reports` keeps every progress."""

    def __init__(self, job_id: str = "inline", tool: str = "test", cancel_at: int | None = None):
        self.job_id = job_id
        self.tool = tool
        self.worker_gone = False
        self.steps = 0
        self.cancel_at = cancel_at
        self.reports: list[tuple] = []

    @property
    def cancelled(self) -> bool:
        return self.cancel_at is not None and self.steps >= self.cancel_at

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def progress(self, phase: str, current: int = 0, total: int = 0, unit: str = "") -> None:
        self.reports.append((phase, current, total, unit))

    def step(self, fn, *args, long: bool = False):
        self.check()
        self.steps += 1
        return fn(*args)

    def release(self, close) -> None:
        close(self.job_id)


def slow_step(seconds: float) -> str:
    """Sent to the real worker: takes a while, then answers."""
    time.sleep(seconds)
    return "slept"


def quick_step(value: int) -> int:
    return value * 2


def failing_step() -> None:
    raise ValueError("planted failure text that must never reach the reader")
