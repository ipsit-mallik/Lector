"""Compress's sampled estimates, PyMuPDF side: runs in the worker process
(like `engine.py`), as the dialog's background work, never a file job.

A big file (`limit_plan.needs_sample`) is estimated before its real steps
run: a few of its pages (the ones with the most image bytes, and some spread
evenly through the rest) are copied into a small document held in memory
here, and each ladder step compresses a fresh copy of it, in memory only. The
main process scales what each step made to the whole document
(`limit_plan.scale_sample`). Nothing is written to disk.
"""
import logging
import os

import pymupdf

from lector.features.file_tools import compress_plan, engine, limit_plan

log = logging.getLogger(__name__)
# job id → (the sample's bytes, how many of its first pages are the heavy ones)
_SAMPLES: dict[str, tuple[bytes, int]] = {}


def _image_bytes(doc: pymupdf.Document, xref: int) -> int:
    kind, value = doc.xref_get_key(xref, "Length")
    if kind == "int":
        return int(value)
    try:
        return len(doc.xref_stream_raw(xref) or b"")
    except Exception:
        return 0


def image_bytes_by_page(doc: pymupdf.Document) -> list[int]:
    """Each page's image bytes as stored (soft masks included), every image
    counted once, on the first page that draws it."""
    seen: set[int] = set()
    out = []
    for page in doc:
        total = 0
        for image in page.get_images(full=True):
            for xref in (image[0], image[1]):
                if xref > 0 and xref not in seen:
                    seen.add(xref)
                    total += _image_bytes(doc, xref)
        out.append(total)
    return out


def _has_image_objects(doc: pymupdf.Document) -> bool:
    """Whether the file holds any image object, drawn or not, from its
    object table alone."""
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Subtype")[1] == "/Image":
                return True
        except Exception:
            continue
    return False


def pick_pages(per_page: list[int], heavy: int, spread: int) -> tuple[list[int], list[int]]:
    """The heavy pages (most image bytes first) and the spread ones (evenly
    through the document, never a heavy one), each 0-based."""
    count = len(per_page)
    by_bytes = sorted(range(count), key=lambda i: (-per_page[i], i))
    chosen = [i for i in by_bytes[:heavy] if per_page[i] > 0]
    rest = [i for i in range(count) if i not in chosen]
    spread_pages: list[int] = []
    for n in range(min(spread, len(rest))):
        target = (n + 0.5) * count / spread
        pick = min((i for i in rest if i not in spread_pages), key=lambda i: (abs(i - target), i))
        spread_pages.append(pick)
    return chosen, sorted(spread_pages)


def sample_open(job_id: str, data: bytes | None, path: str | None, password: str | None) -> dict:
    """Look at the PDF and, if it is big enough to estimate
    (`limit_plan.needs_sample`) and has images, keep a sample of its pages
    for `sample_step`. Returns `{pages, source_bytes, sampled}`, and for a
    sample what `limit_plan.scale_sample` needs."""
    sample_close(job_id)
    doc, _needs = engine._open_pdf(data, path, password)
    try:
        source_bytes = len(data) if data is not None else os.path.getsize(path)
        out = {"pages": len(doc), "source_bytes": source_bytes, "sampled": False}
        # Cheapest first: the page count, then whether any image object
        # exists at all (no page is loaded for either), and only then each
        # page's image bytes. A 1,000-page text PDF stops at the second.
        if not limit_plan.needs_sample(source_bytes, len(doc)) or not _has_image_objects(doc):
            return out
        per_page = image_bytes_by_page(doc)
        if not any(per_page):
            return out
        heavy, spread = pick_pages(per_page, limit_plan.SAMPLE_HEAVY, limit_plan.SAMPLE_SPREAD)
        sample = pymupdf.open()
        try:
            for index in heavy + spread:
                sample.insert_pdf(doc, from_page=index, to_page=index)
            sample_bytes = sample.tobytes(garbage=1)
        finally:
            sample.close()
    finally:
        doc.close()
    heavy_in, spread_in = _split(sample_bytes, len(heavy))
    _SAMPLES[job_id] = (sample_bytes, len(heavy))
    out.update(
        sampled=True,
        sample_pages=[i + 1 for i in heavy + spread],
        image_bytes=sum(per_page),
        heavy_full=sum(per_page[i] for i in heavy),
        heavy_in=heavy_in,
        spread_in=spread_in,
        sample_bytes=len(sample_bytes),
    )
    return out


def _split(data: bytes, heavy: int) -> tuple[int, int]:
    """A sample's image bytes: on its first `heavy` pages, and on the rest."""
    doc = pymupdf.open("pdf", data)
    try:
        per_page = image_bytes_by_page(doc)
    finally:
        doc.close()
    return sum(per_page[:heavy]), sum(per_page[heavy:])


def sample_step(job_id: str, recipe: compress_plan.Recipe) -> dict:
    """Compress a copy of the sample with `recipe`, as a real step does
    (images, fonts subset, the lossless tidy), in memory. Returns its size,
    its image bytes on the heavy and the spread pages, and what the image
    pass reports (`engine.compress_document`)."""
    try:
        data, heavy = _SAMPLES[job_id]
    except KeyError:
        raise engine.JobMissing() from None
    doc = pymupdf.open("pdf", data)
    try:
        info = engine.compress_document(doc, recipe)
        try:
            doc.subset_fonts()
        except Exception:
            # As a real step: fonts that can't be subset stay whole.
            log.debug("Couldn't subset the sample's fonts", exc_info=True)
        out = doc.tobytes(garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    heavy_out, spread_out = _split(out, heavy)
    return {"out": len(out), "heavy_out": heavy_out, "spread_out": spread_out, "info": info}


def sample_close(job_id: str) -> None:
    _SAMPLES.pop(job_id, None)
