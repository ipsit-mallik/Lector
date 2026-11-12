"""Each file tool's job: the steps it runs in the worker (`engine`), in
order, with progress between them. Run by `jobs.FileJobRunner`; built by
`api.py` from what the reader chose in the dialog.

`engine`'s functions are only named here, to be sent to the worker; none of
them is called in this process.
"""
import os
from collections.abc import Callable
from dataclasses import dataclass

from lector.features.file_tools import choices, engine, messages, naming
from lector.features.file_tools.jobs import JobContext, JobFailed, write_file, write_folder
from lector.features.reading import ocr, ocr_cache, safe_write

# Compress writes nothing unless the new file is at least this much smaller.
MIN_SAVING = 0.05

# Make searchable: a page's OCR words in `recognize`'s form, from the open
# document's own reading or the cache, or None to read the page in the job.
KnownWords = Callable[[int, str], list | None]


@dataclass(frozen=True)
class Source:
    """The PDF a tool works on. `data` is the reader's in-memory copy (its
    unsaved highlights included), or None to open the file at `path`.
    `password` unlocks it, in memory only; a protected PDF's OCR text is
    never written to the cache."""

    path: str
    data: bytes | None = None
    password: str | None = None

    @property
    def size(self) -> int:
        """The original's size on disk: what the reader sees as "now"."""
        try:
            return os.path.getsize(self.path)
        except OSError:
            return len(self.data or b"")


class _NotSmaller(BaseException):
    """Compress's verdict on a file that saves under `MIN_SAVING`: raised
    from the check so the temporary file is discarded, not kept."""


def _open(ctx: JobContext, source: Source) -> dict:
    path = None if source.data is not None else source.path
    try:
        return ctx.step(engine.open_job, ctx.job_id, source.data, path, source.password, long=True)
    except engine.SourceError:
        raise JobFailed(messages.SOURCE_UNREADABLE) from None


def _check_pdf(ctx: JobContext, path: str, password: str | None, expect: dict) -> None:
    try:
        ctx.step(engine.verify_output, path, password, expect, long=True)
    except engine.OutputInvalid:
        raise JobFailed(safe_write.VERIFY_FAILED) from None


def compress(ctx: JobContext, source: Source, dest: str, level: str) -> dict:
    """The Compress job. Returns `{path, before, after}`, or `{compact:
    True, before, level}` when the saving is under `MIN_SAVING` and nothing
    was written. Progress is by phase: MuPDF rewrites every image in one
    call, with no page to report or stop at."""
    steps = 5
    ctx.progress("Copying the document", 0, steps, "step")
    expect = _open(ctx, source)
    try:
        before = source.size
        if level != choices.LIGHT:
            ctx.progress("Compressing images", 1, steps, "step")
            ctx.step(engine.compress_images, ctx.job_id, level, long=True)
        ctx.progress("Subsetting fonts", 2, steps, "step")
        ctx.step(engine.subset_fonts, ctx.job_id, long=True)

        def write(tmp: str) -> None:
            ctx.progress("Writing the file", 3, steps, "step")
            ctx.step(engine.save_compressed, ctx.job_id, tmp, long=True)

        def verify(tmp: str) -> None:
            ctx.progress("Checking the file", 4, steps, "step")
            _check_pdf(ctx, tmp, source.password, expect)
            if os.path.getsize(tmp) > before * (1 - MIN_SAVING):
                raise _NotSmaller()

        try:
            write_file(dest, write, verify)
        except _NotSmaller:
            return {"compact": True, "before": before, "level": level}
        return {"path": dest, "before": before, "after": os.path.getsize(dest)}
    finally:
        ctx.release(engine.close_job)


def export_images(ctx: JobContext, source: Source, dest: str, fmt: str, ppi: int, pages: list[int]) -> dict:
    """The Export as images job: `pages` (0-based) as `<stem>-p001.png` …,
    numbered by page, into a new folder. Returns `{path, count}`."""
    ctx.progress("Exporting", 0, len(pages), "image")
    info = _open(ctx, source)
    try:
        stem = os.path.splitext(os.path.basename(source.path))[0]
        ext = engine.EXTENSIONS[fmt]
        names = [naming.image_file_name(stem, i + 1, info["pages"], ext) for i in pages]

        def fill(folder: str) -> None:
            for n, (index, name) in enumerate(zip(pages, names)):
                ctx.progress("Exporting", n + 1, len(pages), "image")
                ctx.step(engine.export_page, ctx.job_id, index, folder, name, fmt, ppi)

        def verify(folder: str) -> None:
            if sorted(os.listdir(folder)) != sorted(names):
                raise JobFailed(safe_write.VERIFY_FAILED)

        final = write_folder(dest, fill, verify)
        return {"path": final, "count": len(pages)}
    finally:
        ctx.release(engine.close_job)


def make_searchable(
    ctx: JobContext,
    source: Source,
    dest: str,
    pages: list[int],
    known_words: KnownWords,
    tessdata: str = str(ocr.TESSDATA_DIR),
) -> dict:
    """The Make searchable job: an invisible text layer on each of `pages`
    (0-based, pages with no text). Words come from `known_words` (what
    highlighting already read, or the cache) or are read now, and what is
    read now goes into the cache, unless the PDF is password-protected.
    Returns `{path, added, no_text}` (`no_text`: 1-based pages where nothing
    readable was found), or `{written: False, no_text}` when no page got any
    text, and then nothing is written."""
    if not os.path.isfile(os.path.join(tessdata, f"{ocr.OCR_LANGUAGE}.traineddata")):
        raise JobFailed(messages.OCR_UNAVAILABLE)
    ctx.progress("Reading text", 0, len(pages), "page")
    expect = _open(ctx, source)
    try:
        keys = ctx.step(engine.ocr_keys, ctx.job_id, list(pages))
        added, no_text = 0, []
        for n, index in enumerate(pages):
            ctx.progress("Reading text", n + 1, len(pages), "page")
            words = known_words(index, keys[index])
            outcome = ctx.step(engine.add_text_layer, ctx.job_id, index, words, tessdata)
            if outcome["payload"] is not None and source.password is None:
                ocr_cache.store(keys[index], outcome["payload"])
            if outcome["added"]:
                added += 1
            else:
                no_text.append(index + 1)
        if not added:
            return {"written": False, "no_text": no_text}

        def write(tmp: str) -> None:
            ctx.progress("Writing the file", len(pages), len(pages), "page")
            ctx.step(engine.save_document, ctx.job_id, tmp, long=True)

        write_file(dest, write, lambda tmp: _check_pdf(ctx, tmp, source.password, expect))
        return {"path": dest, "added": added, "no_text": no_text}
    finally:
        ctx.release(engine.close_job)


def extract(
    ctx: JobContext,
    source: Source,
    pages: list[tuple[str | None, int, int]],
    source_data: dict[str, bytes],
    dest: str,
) -> dict:
    """Organise's Extract: the selected pages of the draft (turned as the
    grid shows them, inserted pages and unsaved highlights included) as a
    new PDF, protected as the original is. Only those pages' objects are
    written. Returns `{path, pages}`."""
    steps = 4
    ctx.progress("Copying the document", 0, steps, "step")
    opened = _open(ctx, source)
    try:
        ctx.progress("Taking the pages", 1, steps, "step")
        expect = ctx.step(engine.extract_pages, ctx.job_id, pages, source_data, long=True)
        expect["needs_pass"] = opened["needs_pass"]

        def write(tmp: str) -> None:
            ctx.progress("Writing the file", 2, steps, "step")
            ctx.step(engine.save_document, ctx.job_id, tmp, 3, long=True)

        def verify(tmp: str) -> None:
            ctx.progress("Checking the file", 3, steps, "step")
            _check_pdf(ctx, tmp, source.password, expect)

        write_file(dest, write, verify)
        return {"path": dest, "pages": len(pages)}
    finally:
        ctx.release(engine.close_job)


def combine(ctx: JobContext, inputs: list[tuple[str, str | None]], dest: str) -> dict:
    """The Combine files job: every input (path, password) into one new PDF,
    in list order, each one's bookmarks under one named after it. The result
    is never password-protected. Returns `{path, pages, files, clashes}`:
    `clashes` names the files whose form fields share a name with an
    earlier file's."""
    ctx.progress("Adding", 0, len(inputs), "file")
    ctx.step(engine.combine_open, ctx.job_id)
    try:
        total = 0
        clashes = []
        for n, (path, password) in enumerate(inputs):
            ctx.progress("Adding", n + 1, len(inputs), "file")
            title = os.path.splitext(os.path.basename(path))[0]
            try:
                added = ctx.step(engine.combine_add, ctx.job_id, path, password, title, long=True)
            except engine.UnsupportedInput:
                raise JobFailed(messages.INPUT_UNREADABLE) from None
            total += added["pages"]
            if added["clash"]:
                clashes.append(os.path.basename(path))
        expect = ctx.step(engine.combine_finish, ctx.job_id)

        def write(tmp: str) -> None:
            ctx.progress("Writing the file", len(inputs), len(inputs), "file")
            ctx.step(engine.save_document, ctx.job_id, tmp, 3, long=True)

        write_file(dest, write, lambda tmp: _check_pdf(ctx, tmp, None, expect))
        return {"path": dest, "pages": total, "files": len(inputs), "clashes": clashes}
    finally:
        ctx.release(engine.combine_close)


def cached_words(index: int, key: str) -> list | None:
    """`KnownWords` from the OCR cache alone (Home has no open document)."""
    payload = ocr_cache.load(key)
    if not isinstance(payload, dict) or not isinstance(payload.get("words"), list):
        return None
    return payload["words"]


def convert(ctx: JobContext, inputs: list[str], dest: str, page_size: str) -> dict:
    """The Convert to PDF job: every input's pages into one new PDF, in
    list order. Returns `{path, pages}`."""
    ctx.progress("Adding", 0, len(inputs), "file")
    ctx.step(engine.convert_open, ctx.job_id)
    try:
        total = 0
        for n, path in enumerate(inputs):
            ctx.progress("Adding", n + 1, len(inputs), "file")
            try:
                total += ctx.step(engine.convert_add, ctx.job_id, path, page_size, long=True)
            except engine.UnsupportedInput:
                raise JobFailed(messages.INPUT_UNREADABLE) from None

        def write(tmp: str) -> None:
            ctx.progress("Writing the file", len(inputs), len(inputs), "file")
            ctx.step(engine.save_document, ctx.job_id, tmp, 3, long=True)

        expect = {"pages": total, "needs_pass": False}
        write_file(dest, write, lambda tmp: _check_pdf(ctx, tmp, None, expect))
        return {"path": dest, "pages": total}
    finally:
        ctx.release(engine.close_job)
