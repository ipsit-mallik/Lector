"""The file tools' PyMuPDF side. Everything here runs in the OCR worker
process (`reading/ocr.py`'s `OcrRunner.call`), never on a bridge thread:
PyMuPDF holds the GIL for a whole call, which would freeze the app.

A job is a run of short calls — open, one per page or file, write, check,
close — so the main process can report progress and stop between pages.
What a job has open stays in `_OPEN`, in the worker, between those calls;
if the worker is replaced (a crash, a timeout, a cancel during a long call)
it is gone with it, and the next call raises `JobMissing`.

Passwords are only ever held in memory here, for as long as a call needs
them. Nothing here writes outside the paths the main process hands in.
"""
import base64
import os
import re

import pymupdf

from lector.features.annotations import highlighter
from lector.features.file_tools import formats
from lector.features.file_tools.choices import BALANCED, EXPORT_FORMATS, EXPORT_PPI, JPEG, LIGHT, PNG, STRONG
from lector.features.reading import ocr

# (above this ppi, down to this ppi, JPEG quality). An image is resampled
# only when its effective resolution on the page is above the first number,
# set 10% over the target so an image already about right is left alone.
IMAGE_SETTINGS = {
    BALANCED: (165, 150, 80),
    STRONG: (106, 96, 65),
}

EXTENSIONS = {PNG: "png", JPEG: "jpg"}
JPEG_QUALITY = 85

# Helvetica's text box, per point of font size, as MuPDF extracts it
# (measured in PyMuPDF 1.28: 1.374 tall, the baseline 0.299 above its
# bottom). An invisible word is sized from these so its box is the box OCR
# found, which is what search hits and selections are drawn from.
HELV_BOX_HEIGHT = 1.374
HELV_DESCENT = 0.299

# Longest side of a Convert list thumbnail, in pixels (32px, doubled for a
# high-density screen).
THUMBNAIL_PX = 64

_OPEN: dict[str, pymupdf.Document] = {}


class SourceError(Exception):
    """The file to work on couldn't be opened, or its password didn't fit."""


class UnsupportedInput(Exception):
    """A file Convert to PDF can't read."""


class OutputInvalid(Exception):
    """What was written didn't check out."""


class JobMissing(Exception):
    """The worker no longer holds the job (it was replaced in between)."""


# ---------------------------------------------------------------------- #
# Opening and describing                                                   #
# ---------------------------------------------------------------------- #


def _open_pdf(data: bytes | None, path: str | None, password: str | None) -> tuple[pymupdf.Document, bool]:
    """The PDF, unlocked, and whether it needed a password. `needs_pass` is
    read once, before `authenticate`: PyMuPDF 1.28 locks a document again
    when it is read afterwards (see `reading/document.py`'s `_unlock`)."""
    try:
        doc = pymupdf.open("pdf", data) if data is not None else pymupdf.open(path, filetype="pdf")
    except Exception:
        raise SourceError() from None
    needs = bool(doc.needs_pass)
    if needs and not (password and doc.authenticate(password)):
        doc.close()
        raise SourceError()
    if len(doc) == 0:
        doc.close()
        raise SourceError()
    return doc, needs


def describe_document(doc: pymupdf.Document, page_index: int = 0, scanned=None) -> dict:
    """What the tool dialogs show about a PDF: its pages, its images (each
    image object counted once, however many pages show it), the pages with
    no text (`ocr.page_needs_ocr`, or the caller's cached version of it),
    whether it is signed, and one page's size as shown, in points."""
    is_scanned = scanned or (lambda i: ocr.page_needs_ocr(doc[i]))
    images = set()
    for page in doc:
        images.update(image[0] for image in page.get_images(full=True))
    page = doc[min(max(page_index, 0), len(doc) - 1)]
    return {
        "pages": len(doc),
        "images": len(images),
        "textless": [i for i in range(len(doc)) if is_scanned(i)],
        "signed": doc.get_sigflags() > 0,
        "page_size": [round(page.rect.width, 2), round(page.rect.height, 2)],
    }


def describe_path(path: str, password: str | None) -> dict:
    """`describe_document` for a file on disk, plus whether it needs a
    password and its size; or `{"locked": True}` when it needs a password
    and `password` is missing or wrong. Raises SourceError when it can't be
    opened at all."""
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception:
        raise SourceError() from None
    try:
        needs = bool(doc.needs_pass)
        if needs and not (password and doc.authenticate(password)):
            return {"locked": True}
        if len(doc) == 0:
            raise SourceError()
        info = describe_document(doc)
        # Split's check, as the reader's `page_change_block`: MuPDF doesn't
        # enforce a PDF's permissions, so Lector does.
        info["can_change_pages"] = bool(doc.permissions & (pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE))
    finally:
        doc.close()
    info["needs_pass"] = needs
    info["size"] = os.path.getsize(path)
    return info


def annotation_summary(doc: pymupdf.Document) -> list[list[str]]:
    """Every page's annotation types, sorted: what a written file must still
    have."""
    return [sorted(annot.type[1] for annot in page.annots()) for page in doc]


def open_job(job_id: str, data: bytes | None, path: str | None, password: str | None) -> dict:
    """Open the job's PDF (the reader's in-memory copy, or the file at
    `path`) and keep it for the job's later calls. Returns what the check
    after writing compares against."""
    close_job(job_id)
    doc, needs = _open_pdf(data, path, password)
    _OPEN[job_id] = doc
    return {"pages": len(doc), "needs_pass": needs, "annots": annotation_summary(doc)}


def close_job(job_id: str) -> None:
    doc = _OPEN.pop(job_id, None)
    if doc is not None:
        doc.close()


def _doc(job_id: str) -> pymupdf.Document:
    try:
        return _OPEN[job_id]
    except KeyError:
        raise JobMissing() from None


# ---------------------------------------------------------------------- #
# Page plans (Organise pages, Milestone F2)                                #
# ---------------------------------------------------------------------- #


def apply_page_plan(
    doc: pymupdf.Document,
    pages: list[tuple[str | None, int, int]],
    sources: dict[str, pymupdf.Document],
) -> None:
    """Make `doc`'s pages `pages`, in order: each `(source, index, turn)`
    is page `index` of `doc` itself (source None) or of `sources[source]`,
    turned `turn` degrees further clockwise.

    The one place a plan becomes pages, run on the reader's open document
    (from `PdfDocument.apply_page_plan`, on a bridge thread: every call here
    is C work on pages already loaded) and on a worker copy by Extract. Each
    inserted source is appended whole with `insert_pdf` (its annotations and
    links kept), then `select()` keeps and orders what the plan names.
    `select()`, not `delete_pages()`: MuPDF's rearrange keeps every
    surviving object's xref, drops bookmarks and links that point at a page
    left out and retargets the rest, where `delete_pages()` leaves a
    bookmark pointing at page -1 (tests/test_page_changes.py)."""
    offsets: dict[str, int] = {}
    for source, _index, _turn in pages:
        if source is not None and source not in offsets:
            offsets[source] = len(doc)
            doc.insert_pdf(sources[source])
    order = [index if source is None else offsets[source] + index for source, index, _turn in pages]
    doc.select(order)
    for position, (_source, _index, turn) in enumerate(pages):
        if turn % 360:
            page = doc[position]
            page.set_rotation((page.rotation + turn) % 360)


def extract_pages(
    job_id: str, pages: list[tuple[str | None, int, int]], source_data: dict[str, bytes]
) -> dict:
    """Make the job's document (the reader's, as it is in memory) just
    `pages`, as `apply_page_plan` takes them: Organise's Extract, the
    selected pages of the draft with their turns, inserted pages included
    (`source_data`: each inserted file's bytes, unlocked). Returns what the
    check after writing compares against. The pages left out are written
    nowhere: the save after this collects their objects (`garbage`)."""
    doc = _doc(job_id)
    sources: dict[str, pymupdf.Document] = {}
    try:
        for source, data in source_data.items():
            sources[source] = pymupdf.open("pdf", data)
        apply_page_plan(doc, pages, sources)
    finally:
        for opened in sources.values():
            opened.close()
    return {"pages": len(doc), "annots": annotation_summary(doc)}


# ---------------------------------------------------------------------- #
# Writing and checking                                                     #
# ---------------------------------------------------------------------- #


def save_document(job_id: str, path: str, garbage: int = 0) -> None:
    """Write the job's document to `path`, keeping its encryption (as Save
    does: a password-protected PDF stays protected)."""
    _doc(job_id).save(path, garbage=garbage, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)


def verify_output(path: str, password: str | None, expect: dict) -> None:
    """Raise OutputInvalid unless the PDF at `path` opens, needs a password
    exactly when `expect["needs_pass"]` says, and has `expect["pages"]`
    pages and, when given, `expect["annots"]` (per page, sorted types)."""
    try:
        doc, needs = _open_pdf(None, path, password)
    except SourceError:
        raise OutputInvalid() from None
    try:
        ok = needs == bool(expect.get("needs_pass")) and len(doc) == expect["pages"]
        if ok and expect.get("annots") is not None:
            ok = annotation_summary(doc) == expect["annots"]
    finally:
        doc.close()
    if not ok:
        raise OutputInvalid()


# ---------------------------------------------------------------------- #
# Compress                                                                 #
# ---------------------------------------------------------------------- #


def _rewrite_options(threshold: int, target: int, quality: int):
    """MuPDF's image rewriter options: colour and grey images, lossless or
    not, above `threshold` ppi resampled (bicubic) to `target` and stored as
    JPEG at `quality`, but only when that is smaller. Bicubic, because
    MuPDF's "average" only halves (a 576 ppi image stopped at 288). 1-bit
    images (scanned text) are never touched: JPEG would blur them."""
    mupdf = pymupdf.mupdf
    options = mupdf.PdfImageRewriterOptions()
    for kind in ("color", "gray"):
        for lossiness in ("lossless", "lossy"):
            prefix = f"{kind}_{lossiness}_image_"
            setattr(options, prefix + "recompress_method", mupdf.FZ_RECOMPRESS_JPEG)
            setattr(options, prefix + "recompress_quality", str(quality))
            setattr(options, prefix + "subsample_method", mupdf.FZ_SUBSAMPLE_BICUBIC)
            setattr(options, prefix + "subsample_threshold", threshold)
            setattr(options, prefix + "subsample_to", target)
    options.bitonal_image_recompress_method = mupdf.FZ_RECOMPRESS_NEVER
    options.recompress_when = mupdf.FZ_RECOMPRESS_WHEN_SMALLER
    return options


def compress_images(job_id: str, level: str) -> None:
    """Resample and recompress the job's images for `level` (Light leaves
    them alone). Never enlarges an image; an image with transparency keeps
    its soft mask; annotations are not images and are untouched."""
    if level not in IMAGE_SETTINGS:
        return
    _doc(job_id).rewrite_images(options=_rewrite_options(*IMAGE_SETTINGS[level]))


def subset_fonts(job_id: str) -> bool:
    """Keep only the glyphs each embedded font uses. Some fonts can't be
    subset; then the font stays whole, which costs size, not correctness."""
    try:
        _doc(job_id).subset_fonts()
    except Exception:
        return False
    return True


def save_compressed(job_id: str, path: str) -> None:
    """The lossless part of every level: unused and duplicate objects
    removed, streams compressed, content streams tidied."""
    _doc(job_id).save(
        path, garbage=4, deflate=True, clean=True, encryption=pymupdf.PDF_ENCRYPT_KEEP
    )


# ---------------------------------------------------------------------- #
# Export as images                                                         #
# ---------------------------------------------------------------------- #


def export_page(job_id: str, index: int, folder: str, name: str, fmt: str, ppi: int) -> list[int]:
    """Render page `index` (turned as it is shown, highlights drawn, no
    alpha channel) at `ppi` into `folder/name`. Returns its pixel size."""
    if os.path.basename(name) != name or fmt not in EXPORT_FORMATS or ppi not in EXPORT_PPI:
        raise ValueError("bad export request")
    pix = _doc(job_id)[index].get_pixmap(dpi=ppi, annots=True, alpha=False)
    target = os.path.join(folder, name)
    if fmt == PNG:
        pix.save(target, output="png")
    else:
        with open(target, "wb") as f:
            f.write(pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY))
    return [pix.width, pix.height]


# ---------------------------------------------------------------------- #
# Make searchable                                                          #
# ---------------------------------------------------------------------- #


def ocr_keys(job_id: str, indices: list[int]) -> dict[int, str]:
    """The OCR cache key of each page (`ocr.page_key`), the same key
    highlighting reads and writes, so a page read either way is read once."""
    doc = _doc(job_id)
    return {i: ocr.page_key(doc, doc[i]) for i in indices}


def add_text_layer(job_id: str, index: int, words: list | None, tessdata: str) -> dict:
    """Give page `index` an invisible text layer. `words` are OCR word boxes
    in `recognize`'s form; None reads the page now. Returns the payload when
    the page was read here (for the cache) and whether any text was added —
    none when what OCR found isn't readable text (`ocr.is_readable`)."""
    doc = _doc(job_id)
    payload = None
    if words is None:
        payload = ocr.recognize(ocr.single_page_pdf(doc, index), tessdata)
        words = payload["words"]
    parsed = [
        highlighter.Word(pymupdf.Rect(x0, y0, x1, y1), str(text), int(block), int(line), int(n))
        for x0, y0, x1, y1, text, block, line, n in words
    ]
    if not ocr.is_readable(parsed):
        return {"payload": payload, "added": False}
    page = doc[index]
    shape = page.new_shape()
    written = sum(_invisible_word(page, shape, word.rect, word.text) for word in parsed)
    if written:
        shape.commit()
    return {"payload": payload, "added": bool(written)}


def _invisible_word(page: pymupdf.Page, shape, rect: pymupdf.Rect, text: str) -> bool:
    """One word as render mode 3 text (drawn invisibly, but found, selected
    and copied like any text) filling `rect`, OCR's box for it on the
    unrotated page. OCR reads the page as it is shown, so on a page turned by
    /Rotate the word runs across the screen, not across the unrotated page:
    it is sized and placed from its box as shown, written turned by the
    page's rotation, and stretched along its own direction to fill the box.
    The page's own content, its image included, is left as it is."""
    text = text.strip()
    shown = (rect * page.rotation_matrix).normalize()
    if not text or shown.width <= 0 or shown.height <= 0:
        return False
    size = shown.height / HELV_BOX_HEIGHT
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    if width <= 0:
        return False
    base = pymupdf.Point(shown.x0, shown.y1 - HELV_DESCENT * size) * page.derotation_matrix
    turn = pymupdf.Matrix(page.rotation)
    stretch = ~turn * pymupdf.Matrix(shown.width / width, 1) * turn
    shape.insert_text(
        base,
        text,
        fontsize=size,
        fontname="helv",
        render_mode=3,
        rotate=page.rotation,
        morph=(base, stretch),
    )
    return True


# ---------------------------------------------------------------------- #
# Convert to PDF                                                           #
# ---------------------------------------------------------------------- #


def _open_input(path: str) -> pymupdf.Document:
    """An image or e-book, opened as the type its extension says. A flowing
    e-book (EPUB, FB2) is laid out on A4 pages."""
    ext = formats.extension(path)
    if ext not in formats.CONVERT_EXTENSIONS:
        raise UnsupportedInput()
    try:
        doc = pymupdf.open(path, filetype=formats.FILETYPES[ext])
        if doc.is_reflowable:
            doc.layout(width=formats.A4[0], height=formats.A4[1])
    except Exception:
        raise UnsupportedInput() from None
    if len(doc) == 0:
        doc.close()
        raise UnsupportedInput()
    return doc


def _input_pages(path: str, doc: pymupdf.Document) -> int:
    # A GIF is its first frame only.
    return 1 if formats.extension(path) == ".gif" else len(doc)


def convert_describe(path: str) -> dict:
    """What the Convert list shows for one file: its type, size, how many
    pages it makes (an e-book after layout, a TIFF's frames) and a small
    thumbnail of its first page. Raises UnsupportedInput."""
    doc = _open_input(path)
    try:
        page = doc[0]
        scale = THUMBNAIL_PX / max(page.rect.width, page.rect.height, 1)
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        except Exception:
            raise UnsupportedInput() from None
        return {
            "pages": _input_pages(path, doc),
            "ebook": formats.is_ebook(path),
            "type": formats.type_label(path),
            "size": os.path.getsize(path),
            "thumbnail": base64.b64encode(pix.tobytes("png")).decode("ascii"),
        }
    finally:
        doc.close()


def convert_open(job_id: str) -> None:
    close_job(job_id)
    _OPEN[job_id] = pymupdf.open()


def convert_add(job_id: str, path: str, page_size: str) -> int:
    """Append `path`'s pages to the job's new PDF; returns how many. An
    image on A4 or Letter is fitted inside the page, centred, in proportion,
    and never drawn bigger than its own size (`formats.NATIVE_PPI`). An
    e-book keeps its own (A4) pages."""
    out = _doc(job_id)
    src = _open_input(path)
    try:
        count = _input_pages(path, src)
        # An image is only decoded here, so a damaged one fails here.
        pdf = pymupdf.open("pdf", src.convert_to_pdf(0, count - 1))
    except Exception:
        raise UnsupportedInput() from None
    finally:
        src.close()
    try:
        if page_size not in formats.PAGE_SIZES or formats.is_ebook(path):
            out.insert_pdf(pdf)
            return count
        width, height = formats.PAGE_SIZES[page_size]
        for i in range(len(pdf)):
            native_w, native_h = _native_size(pdf[i])
            scale = min(width / native_w, height / native_h, 1.0)
            w, h = native_w * scale, native_h * scale
            page = out.new_page(width=width, height=height)
            page.show_pdf_page(
                pymupdf.Rect((width - w) / 2, (height - h) / 2, (width + w) / 2, (height + h) / 2),
                pdf,
                i,
            )
        return count
    finally:
        pdf.close()


# ---------------------------------------------------------------------- #
# Combine files (Milestone F2)                                              #
# ---------------------------------------------------------------------- #

# Each Combine job's bookmarks so far, as `set_toc` takes them, written when
# the last file is in.
_TOC: dict[str, list[list]] = {}
# The form field names each Combine job has taken in so far.
_FIELDS: dict[str, set[str]] = {}


def _first_page_thumbnail(doc: pymupdf.Document) -> str:
    page = doc[0]
    scale = THUMBNAIL_PX / max(page.rect.width, page.rect.height, 1)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def combine_describe(path: str, password: str | None) -> dict:
    """What Combine's list shows for one file. A PDF: its pages, size, a
    thumbnail, whether it is signed or needed a password; `{"locked":
    True}` when it needs one and `password` is missing or wrong. An image
    or e-book: `convert_describe`'s answer. Raises SourceError or
    UnsupportedInput when it can't be read."""
    if formats.extension(path) != ".pdf":
        return convert_describe(path)
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception:
        raise SourceError() from None
    try:
        needs = bool(doc.needs_pass)
        if needs and not (password and doc.authenticate(password)):
            return {"locked": True}
        if len(doc) == 0:
            raise SourceError()
        try:
            thumbnail = _first_page_thumbnail(doc)
        except Exception:
            raise SourceError() from None
        return {
            "pages": len(doc),
            "type": "PDF",
            "pdf": True,
            "size": os.path.getsize(path),
            "thumbnail": thumbnail,
            "needs_pass": needs,
            "signed": doc.get_sigflags() > 0,
        }
    finally:
        doc.close()


def combine_open(job_id: str) -> None:
    convert_open(job_id)
    _TOC[job_id] = []
    _FIELDS[job_id] = set()


def combine_close(job_id: str) -> None:
    close_job(job_id)
    _TOC.pop(job_id, None)
    _FIELDS.pop(job_id, None)


_XREF_REF = re.compile(r"(\d+) 0 R")


def _field_names(doc: pymupdf.Document) -> set[str]:
    """The names of the form's top-level fields (what `insert_pdf` renames
    when two files share one), read from the AcroForm dictionary as plain
    strings: no field, widget or action is loaded or followed (the
    untrusted-content guard rail keeps MuPDF's form APIs out of Lector)."""
    if not doc.is_pdf:
        return set()
    kind, fields = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
    if kind != "array":
        return set()
    names = set()
    for xref in _XREF_REF.findall(fields):
        kind, name = doc.xref_get_key(int(xref), "T")
        if kind == "string" and name:
            names.add(name)
    return names


def combine_add(job_id: str, path: str, password: str | None, title: str) -> dict:
    """Append `path` to the job's new PDF: a PDF whole (its annotations,
    links and form fields kept; unlocked with `password`, the result not
    protected), an image or e-book as Convert's "Same as image" makes it.
    Its bookmarks go under one top-level bookmark, `title`, at its first
    page (just that one when it has none). Returns `{pages, clash}`:
    `clash` is whether a form field of its shares a name with one already
    in, which MuPDF then renames ("name [13]"), so a script or a form's
    data that names the field may no longer find it. Raises
    UnsupportedInput."""
    out = _doc(job_id)
    start = len(out)
    if formats.extension(path) != ".pdf":
        count = convert_add(job_id, path, formats.SAME_AS_IMAGE)
        _TOC[job_id].append([1, title, start + 1])
        return {"pages": count, "clash": False}
    try:
        src = pymupdf.open(path, filetype="pdf")
    except Exception:
        raise UnsupportedInput() from None
    try:
        if src.needs_pass and not (password and src.authenticate(password)):
            raise UnsupportedInput()
        if len(src) == 0:
            raise UnsupportedInput()
        names = _field_names(src)
        clash = bool(names & _FIELDS[job_id])
        _FIELDS[job_id] |= names
        toc = src.get_toc(simple=True)
        out.insert_pdf(src)
        count = len(src)
    except UnsupportedInput:
        raise
    except Exception:
        raise UnsupportedInput() from None
    finally:
        src.close()
    entries = [[1, title, start + 1]]
    for level, text, page in toc:
        # A bookmark with no page in its own file has nowhere to go here.
        if 1 <= page <= count:
            entries.append([level + 1, text, start + page])
    _TOC[job_id].extend(entries)
    return {"pages": count, "clash": clash}


def combine_finish(job_id: str) -> dict:
    """Give the job's new PDF its bookmarks. Returns what the check after
    writing compares against."""
    out = _doc(job_id)
    out.set_toc(_nested(_TOC.get(job_id, [])))
    return {"pages": len(out), "needs_pass": False, "annots": annotation_summary(out)}


def _nested(toc: list[list]) -> list[list]:
    """`toc` with no bookmark more than one level under the one before it
    (`set_toc` refuses that, and a skipped bookmark can leave one)."""
    fixed, previous = [], 0
    for level, text, page in toc:
        level = min(level, previous + 1)
        fixed.append([level, text, page])
        previous = level
    return fixed


# ---------------------------------------------------------------------- #
# Split (Milestone F2)                                                     #
# ---------------------------------------------------------------------- #

# Each Split job's source, to open afresh for every file it writes:
# (bytes or None, path or None, password or None).
_SPLIT: dict[str, tuple[bytes | None, str | None, str | None]] = {}


def split_open(job_id: str, data: bytes | None, path: str | None, password: str | None) -> dict:
    """Check the job's PDF opens (unlocked) and hold where to find it.
    Returns `{pages, needs_pass}`. Raises SourceError."""
    split_close(job_id)
    doc, needs = _open_pdf(data, path, password)
    try:
        pages = len(doc)
    finally:
        doc.close()
    _SPLIT[job_id] = (data, path, password)
    return {"pages": pages, "needs_pass": needs}


def split_close(job_id: str) -> None:
    _SPLIT.pop(job_id, None)


def split_part(job_id: str, first: int, last: int, dest: str) -> dict:
    """Write pages `first`…`last` (0-based, inclusive) of the job's PDF to
    `dest`, from a fresh copy each time: `select()` keeps the part's pages
    with their annotations and links, drops the bookmarks pointing outside
    it and retargets the rest (tests/test_page_changes.py). Kept protected
    as the original is, and only the part's objects written (`garbage`).
    Returns what the check after writing compares against."""
    try:
        data, path, password = _SPLIT[job_id]
    except KeyError:
        raise JobMissing() from None
    doc, needs = _open_pdf(data, path, password)
    try:
        doc.select(list(range(first, last + 1)))
        doc.save(dest, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        return {"pages": len(doc), "needs_pass": needs, "annots": annotation_summary(doc)}
    finally:
        doc.close()


def convert_to_pdf_bytes(path: str) -> bytes:
    """One file Convert to PDF takes (an image, an e-book) as a PDF's bytes,
    its pages at their own size (Convert's "Same as image"): what Organise's
    Insert… puts in. Raises UnsupportedInput."""
    job_id = f"insert:{path}"
    convert_open(job_id)
    try:
        convert_add(job_id, path, formats.SAME_AS_IMAGE)
        return _doc(job_id).tobytes(garbage=3, deflate=True)
    finally:
        close_job(job_id)


def _native_size(page: pymupdf.Page) -> tuple[float, float]:
    """An image page's picture at one pixel per CSS pixel, in points; the
    page's own size if it shows no image."""
    images = page.get_images(full=True)
    if images and images[0][2] > 0 and images[0][3] > 0:
        to_points = 72 / formats.NATIVE_PPI
        return images[0][2] * to_points, images[0][3] * to_points
    return max(page.rect.width, 1.0), max(page.rect.height, 1.0)
