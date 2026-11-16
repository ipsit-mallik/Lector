"""Each file tool's job: the steps it runs in the worker (`engine`), in
order, with progress between them. Run by `jobs.FileJobRunner`; built by
`api.py` from what the reader chose in the dialog.

`engine`'s functions are only named here, to be sent to the worker; none of
them is called in this process.
"""
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from lector.features.file_tools import compress_plan, engine, messages, naming
from lector.features.file_tools.jobs import JobContext, JobFailed, write_file, write_folder
from lector.features.file_tools.scratch import ScratchFiles
from lector.features.reading import ocr, ocr_cache, safe_write

log = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class Attempt:
    """One compression, written to a temporary file (`scratch.py`): its
    size, what the check after moving it into place compares against, what
    the Done state's note needs, what the step did in the Size limit tab's
    words (`kind`, `compress_plan.KINDS`), and what the PDF holds (`facts`:
    `images`, `scans`; see `compress_plan.same_result`)."""

    path: str
    size: int
    expect: dict
    lowered: bool
    black_and_white: bool
    kind: str = compress_plan.SHARP
    facts: dict = field(default_factory=dict)


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


# Compress's phases (progress is by phase: MuPDF rewrites every image in
# one call, with no page to report or stop at).
_COMPRESS_STEPS = 5
_WRITING, _CHECKING = 3, 4

# Gets an estimate's finished attempt for the job (waiting for one still
# running), or None when there is none to use.
EstimateGetter = Callable[[JobContext], Attempt | None]


def compress_attempt(
    ctx: JobContext, source: Source, tmp: str, recipe: compress_plan.Recipe, phases: bool = False
) -> Attempt:
    """Compress `source` with `recipe` into `tmp` (a scratch file): the
    images, then fonts subset, then the lossless tidy as it is written.
    `phases` reports each as the job's progress (a job run without a
    finished estimate); the estimates report none."""

    def phase(text: str, n: int) -> None:
        if phases:
            ctx.progress(text, n, _COMPRESS_STEPS, "step")

    phase("Copying the document", 0)
    expect = _open(ctx, source)
    try:
        phase("Compressing images", 1)
        info = ctx.step(engine.compress_images, ctx.job_id, recipe, long=True)
        phase("Subsetting fonts", 2)
        ctx.step(engine.subset_fonts, ctx.job_id, long=True)
        phase("Writing the file", _WRITING)
        ctx.step(engine.save_compressed, ctx.job_id, tmp, long=True)
    finally:
        ctx.release(engine.close_job)
    return Attempt(
        tmp,
        os.path.getsize(tmp),
        expect,
        info["lowered"],
        info["black_and_white"],
        compress_plan.kind(recipe, info),
        {"images": info["images"], "scans": info["scans"]},
    )


def _attempt_into_scratch(ctx: JobContext, source: Source, recipe, scratch: ScratchFiles, folder: str, phases: bool):
    try:
        tmp = scratch.new([folder])
    except OSError as exc:
        raise JobFailed(safe_write.classify(exc, os.path.join(folder, "x.pdf"))) from None
    try:
        return compress_attempt(ctx, source, tmp, recipe, phases)
    except BaseException:
        scratch.discard(tmp)
        raise


def _move(src: str, dest: str) -> None:
    """Move a scratch file onto the all-or-nothing write's temporary file:
    a rename on the same drive, a copy when the folder chosen is elsewhere."""
    try:
        os.replace(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
        os.remove(src)


def place(ctx: JobContext, attempt: Attempt, dest: str, password: str | None, scratch: ScratchFiles) -> None:
    """Move a finished attempt to `dest` through the write every tool uses
    (`write_file`: a temporary file beside it, checked, then moved over it in
    one step). No compression here. The scratch file is gone afterwards,
    whatever happened."""
    try:

        def write(tmp: str) -> None:
            _move(attempt.path, tmp)

        def verify(tmp: str) -> None:
            ctx.progress("Checking the file", _CHECKING, _COMPRESS_STEPS, "step")
            _check_pdf(ctx, tmp, password, attempt.expect)

        write_file(dest, write, verify)
    finally:
        scratch.discard(attempt.path)


def _still_there(attempt: Attempt | None, scratch: ScratchFiles) -> Attempt | None:
    """`attempt`, or None when its temporary file has gone (deleted from
    outside), so the job makes it again rather than failing to move it."""
    if attempt is not None and not os.path.isfile(attempt.path):
        scratch.discard(attempt.path)
        return None
    return attempt


def _done(dest: str, before: int, attempt: Attempt, mode: str, **extra) -> dict:
    return {
        "path": dest,
        "before": before,
        "after": os.path.getsize(dest),
        "mode": mode,
        "lowered": attempt.lowered,
        "black_and_white": attempt.black_and_white,
        **extra,
    }


def compress(
    ctx: JobContext,
    source: Source,
    dest: str,
    level: str,
    scratch: ScratchFiles,
    estimate: EstimateGetter | None = None,
) -> dict:
    """The Compress job in Quality mode. The level's estimate, when the
    dialog has one finished (or still running: the job waits for it), is
    moved into place as it is; otherwise the level is compressed now. A level
    that doesn't save `compress_plan.MIN_SAVING` writes nothing (the dialog
    doesn't offer one). Returns `{path, before, after, mode, lowered,
    black_and_white}`."""
    before = source.size
    attempt = None
    if estimate is not None:
        ctx.progress("Compressing images", 1, _COMPRESS_STEPS, "step")
        attempt = _still_there(estimate(ctx), scratch)
    if attempt is None:
        recipe = compress_plan.LEVEL_RECIPES[level]
        attempt = _attempt_into_scratch(ctx, source, recipe, scratch, os.path.dirname(dest), phases=True)
    if not compress_plan.helps(before, attempt.size):
        scratch.discard(attempt.path)
        raise JobFailed(messages.NOT_SMALLER)
    place(ctx, attempt, dest, source.password, scratch)
    return _done(dest, before, attempt, compress_plan.QUALITY)


def compress_to_limit(
    ctx: JobContext,
    source: Source,
    dest: str,
    step: int,
    limit: int,
    label: str,
    scratch: ScratchFiles,
    estimate: EstimateGetter,
    hold: Callable[[Attempt], None],
) -> dict:
    """The Compress job in Size limit mode: ladder step `step`, the best one
    under `limit` bytes (`compress_plan.choose`, from the dialog's checking),
    moved into place as it is. No compression, unless its temporary file has
    gone (deleted from outside); then the step is made again.

    Returns `{path, before, after, mode, limit, label, kind, lowered,
    black_and_white}`. The safety net, never reached in normal use: a step
    that turns out not to fit writes nothing, is logged, is handed to `hold`
    (the dialog's "Save … file" moves it into place with `save_held`), and
    the result is `{unreachable, before, smallest, limit, label}`."""
    before = source.size
    ctx.progress("Compressing images", 1, _COMPRESS_STEPS, "step")
    attempt = _still_there(estimate(ctx), scratch)
    if attempt is None:
        log.warning("Compress: size-limit step %d had no finished file; making it again", step)
        attempt = _attempt_into_scratch(ctx, source, compress_plan.LADDER[step], scratch, os.path.dirname(dest), True)
    common = {"limit": limit, "label": label}
    if not compress_plan.fits(attempt.size, limit):
        log.warning("Compress: size-limit step %d is %d bytes, over %d after all", step, attempt.size, limit)
        hold(attempt)
        return {"unreachable": True, "before": before, "smallest": attempt.size, **common}
    place(ctx, attempt, dest, source.password, scratch)
    return _done(dest, before, attempt, compress_plan.SIZE_LIMIT, kind=attempt.kind, **common)


def save_held(ctx: JobContext, source: Source, dest: str, held: Attempt, label: str, scratch: ScratchFiles) -> dict:
    """"Save 164 KB file": the safety net's result, which didn't get under
    the limit, moved into place as it is."""
    before = source.size
    place(ctx, held, dest, source.password, scratch)
    return _done(dest, before, held, compress_plan.SIZE_LIMIT, label=label, over_limit=True)


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


def split(ctx: JobContext, source: Source, parts: list[tuple[int, int]], dest: str) -> dict:
    """The Split job: each of `parts` ((first, last), 1-based) as its own
    PDF, "<name> (part 1).pdf" …, in a new folder, protected as the
    original is. One step per file. Returns `{path, files}`."""
    stem = os.path.splitext(os.path.basename(source.path))[0] or "untitled"
    names = [naming.split_part_name(stem, n + 1) for n in range(len(parts))]
    ctx.progress("Splitting", 0, len(parts), "file")
    path = None if source.data is not None else source.path
    try:
        ctx.step(engine.split_open, ctx.job_id, source.data, path, source.password, long=True)
    except engine.SourceError:
        raise JobFailed(messages.SOURCE_UNREADABLE) from None
    try:
        expected: list[dict] = []

        def fill(folder: str) -> None:
            for n, ((first, last), name) in enumerate(zip(parts, names)):
                ctx.progress("Splitting", n + 1, len(parts), "file")
                part = os.path.join(folder, name)
                expected.append(ctx.step(engine.split_part, ctx.job_id, first - 1, last - 1, part, long=True))

        def verify(folder: str) -> None:
            if sorted(os.listdir(folder)) != sorted(names):
                raise JobFailed(safe_write.VERIFY_FAILED)
            for name, expect in zip(names, expected):
                _check_pdf(ctx, os.path.join(folder, name), source.password, expect)

        final = write_folder(dest, fill, verify)
        return {"path": final, "files": len(parts)}
    finally:
        ctx.release(engine.split_close)


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
