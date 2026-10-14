"""Reading the text off scanned pages (OCR), one page at a time, on demand.

A scanned page is a picture of text with no text layer, so it has no
character or word boxes and nothing to highlight. This gives such a page
the same `Char`/`Word` lists `highlighter.chars_on_page`/`words_on_page`
return for a born-digital one, so mouse selection, speech-to-match and the
annotation writer all run unchanged on top of it (`PdfDocument` swaps them
in). Detection is per page, never per file: a PDF can mix scanned pages
with ordinary ones.

The engine is the Tesseract build already compiled into PyMuPDF; the only
extra file is its English language data in `assets/tessdata/` (fetched by
`scripts/fetch_tessdata.py`). OCR runs in a separate worker process, not a
thread: PyMuPDF holds the GIL for the whole call (measured: a 1.9 s page
stalled the main thread for 1.3 s), which would freeze every bridge call —
page renders, voice results — for as long as a page takes to read. The
process also keeps a Tesseract crash out of the app.

Nothing here runs on file open. `PdfDocument` starts a job only when a
reader tries to highlight a scanned page, or when voice is on and a scanned
page has stayed on screen (see `reading.js`).
"""
import hashlib
import logging
import math
import multiprocessing
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF
import numpy as np

from lector.features.annotations import highlighter

log = logging.getLogger(__name__)

TESSDATA_DIR = Path(__file__).resolve().parents[4] / "assets" / "tessdata"
OCR_LANGUAGE = "eng"
# Tesseract's own recommendation for body text; lower loses small print.
OCR_DPI = 300
# A poster-sized page at 300 dpi would be hundreds of megapixels; past this
# the page is rendered at whatever resolution fits instead.
MAX_OCR_PIXELS = 40_000_000
# Long enough for any real page (a dense one takes a few seconds); a job
# past it is treated as a failure and the worker process is replaced.
OCR_TIMEOUT_S = 120
# How often a wait on the worker looks up to see whether the app is closing.
_WAIT_SLICE_S = 0.1
# Straightening (see `detect_skew`): measured at this resolution, which is
# plenty for finding lines and fast (under 0.1 s). Tilts below the minimum
# are left alone; past the maximum it is not a tilted scan but a page set
# sideways, which /Rotate already handles.
SKEW_DPI = 150
MIN_SKEW_DEG = 0.3
MAX_SKEW_DEG = 10.0
MIN_SKEW_INK_PIXELS = 500
# When a read is good enough to stop at, and how much more ink a later read
# must account for to replace an earlier one (see `recognize`). Clean pages
# measured 0.99-1.00 on the first read; a speckled office scan 0.57-0.61.
GOOD_INK_COVERAGE = 0.9
COVERAGE_MARGIN = 0.03
# Bumped whenever `recognize` reads pages differently, so results cached by
# an older version are read again rather than kept (see `page_key`).
OCR_PIPELINE_VERSION = 2
# What tells read text from noise (see `is_readable`): at least this many
# counted words — numbers, and words of three letters or more — of which at
# least this share are common English words or numbers. Measured: 12 pages
# of a 1908 scan and a speckled 1998 office scan scored 0.17-0.43 (forms
# lowest), a skewed, blurred phone photo 0.07. One- and two-letter words are
# left out because noise is full of them ("a", "be", "of" came out of the
# phone photo by chance).
MIN_READABLE_WORDS = 3
MIN_COMMON_WORD_SHARE = 0.12
_COMMON_WORDS = frozenset(
    "the and that for with this from are was which not but have has can will"
    " all their his may one each".split()
)
_NUMBER = re.compile(r"^[\d.,/%$()#-]*\d[\d.,/%$()#-]*$")

# A page's OCR status, as the bridge reports it. None (not a status) means
# the page has its own text layer, or nothing on it that could be text.
NEEDED = "needed"  # scanned, not read yet
RUNNING = "running"
DONE = "done"
NO_TEXT = "no_text"  # read, and nothing legible was found
UNAVAILABLE = "unavailable"  # language data missing
ERROR = "error"  # the OCR itself failed

# Statuses a fresh request may (re)start: a failure that might not recur
# is retried when the reader asks again; a finished read never is.
RESTARTABLE = (NEEDED, ERROR, UNAVAILABLE)


def page_needs_ocr(page: fitz.Page) -> bool:
    """True for a page with no extractable text that does show an image —
    a blank page has no text either, but nothing to read."""
    if page.get_text("text").strip():
        return False
    return bool(page.get_images(full=True))


def page_key(doc: fitz.Document, page: fitz.Page) -> str:
    """Cache key for `page`'s OCR result: what the page draws (its content
    stream and the raw bytes of its images) plus everything that changes how
    it is read (language, resolution, rotation, page box). Annotations live
    outside the content stream, so highlighting and saving leave it alone."""
    h = hashlib.sha256()
    h.update(
        f"{OCR_PIPELINE_VERSION}|{OCR_LANGUAGE}|{OCR_DPI}|{page.rotation}"
        f"|{tuple(page.mediabox)}|{tuple(page.cropbox)}".encode()
    )
    h.update(page.read_contents())
    for xref in sorted({image[0] for image in page.get_images(full=True)}):
        h.update(_image_bytes(doc, xref))
    return h.hexdigest()


# Image codecs MuPDF never re-encodes when saving, so an image's raw bytes
# under one of these are stable, and much cheaper than decoding (a JPEG scan
# decodes in ~30 ms). Anything else is hashed decoded: `save(deflate=True)`,
# which "save a copy" uses, compresses a stream that was stored plain.
_IMAGE_CODECS = frozenset({"/DCTDecode", "/JPXDecode", "/JBIG2Decode", "/CCITTFaxDecode"})


def _image_bytes(doc: fitz.Document, xref: int) -> bytes:
    kind, value = doc.xref_get_key(xref, "Filter")
    if kind == "name" and value in _IMAGE_CODECS:
        return doc.xref_stream_raw(xref) or b""
    return doc.xref_stream(xref) or b""


def single_page_pdf(doc: fitz.Document, index: int) -> bytes:
    """Page `index` alone, without annotations, as PDF bytes — what the
    worker process is handed. A copy rather than the open document because
    the worker cannot share it, and annotation-free so an existing highlight
    is not read as part of the scan."""
    out = fitz.open()
    out.insert_pdf(doc, from_page=index, to_page=index, annots=False)
    return out.tobytes()


def _ocr_dpi(page: fitz.Page) -> int:
    area_in_sq_inches = (page.rect.width / 72) * (page.rect.height / 72)
    if area_in_sq_inches <= 0:
        return OCR_DPI
    return min(OCR_DPI, int(math.sqrt(MAX_OCR_PIXELS / area_in_sq_inches)))


def despeckle(gray: np.ndarray) -> np.ndarray:
    """`gray` with isolated dark specks turned white: dark pixels with fewer
    than three dark neighbours. Scanner and photocopier noise is single black
    dots, and on a noisy page Tesseract's layout analysis drops whole lines
    of perfectly clear text around them (measured on a speckled office scan:
    45% of a memo's words missing). Letter strokes are never that thin, so
    unlike a median filter this leaves the text itself alone."""
    dark = gray < 128
    padded = np.pad(dark, 1).astype(np.uint8)
    h, w = gray.shape
    neighbours = sum(padded[y:y + h, x:x + w] for y in range(3) for x in range(3)) - dark
    out = gray.copy()
    out[dark & (neighbours < 3)] = 255
    return out


def detect_skew(gray: np.ndarray) -> float:
    """How far the text on `gray` is tilted, in degrees (positive: lines
    run down to the right), or 0.0 when there is too little ink to tell.

    Projection profile: shearing the ink by the right angle stacks each
    line's pixels into as few rows as possible, which is when the row
    histogram's sum of squares peaks. Searched coarse then fine, up to
    `MAX_SKEW_DEG` either way. MuPDF has a detector of its own, but it
    measured 1.1, 2.9 and 3.8 degrees on pages tilted by 2, 4 and 7.
    """
    ys, xs = np.nonzero(despeckle(gray) < 128)
    if len(ys) < MIN_SKEW_INK_PIXELS:
        return 0.0
    ys = ys.astype(np.float64)
    xs = xs.astype(np.float64)

    def sharpness(deg: float) -> float:
        rows = ys - xs * math.tan(math.radians(deg))
        histogram = np.bincount((rows - rows.min()).astype(np.int64)).astype(np.float64)
        return float((histogram ** 2).sum())

    coarse = max(np.arange(-MAX_SKEW_DEG, MAX_SKEW_DEG + 1e-9, 0.5), key=sharpness)
    return float(max(np.arange(coarse - 0.5, coarse + 0.5 + 1e-9, 0.05), key=sharpness))


def _render(page: fitz.Page, dpi: int, angle: float) -> tuple[np.ndarray, fitz.Matrix]:
    """A greyscale render of `page` as displayed, turned by `-angle` so its
    text is level, and the matrix taking points on the OCR page made from it
    back to the page's own text coordinates.

    Rendering turned, rather than turning a rendered bitmap, keeps the full
    quality of the source and makes the way back exact: OCR points scale to
    pixels, pixels shift by the render's origin (a turned page's bounding
    box does not start at 0,0), the inverse render matrix undoes the turn
    and the zoom, and the page's derotation goes from "as displayed" to the
    unrotated coordinates text extraction uses."""
    to_pixels = fitz.Matrix(dpi / 72, dpi / 72)
    render_matrix = to_pixels * fitz.Matrix(-angle)
    pix = page.get_pixmap(matrix=render_matrix, colorspace=fitz.csGRAY, alpha=False, annots=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    back = to_pixels * fitz.Matrix(1, 0, 0, 1, pix.x, pix.y) * ~render_matrix * page.derotation_matrix
    return gray, back


def _ocr_bitmap(gray: np.ndarray, dpi: int, tessdata: str) -> fitz.Page:
    """OCR one greyscale bitmap; the page of the PDF MuPDF writes for it,
    whose invisible text layer is what was read. Handed over as RGB: a
    greyscale pixmap is accepted, then reads as no text at all."""
    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    pix = fitz.Pixmap(fitz.csRGB, gray.shape[1], gray.shape[0], rgb.tobytes(), False)
    pix.set_dpi(dpi, dpi)
    return fitz.open("pdf", pix.pdfocr_tobytes(language=OCR_LANGUAGE, tessdata=tessdata))[0]


def ink_coverage(gray: np.ndarray, ocr_page: fitz.Page, dpi: int) -> float:
    """The share of the page's ink that lies inside a recognized word.

    What tells a good read from one that silently dropped lines: Tesseract
    gives no confidence through MuPDF, but text it skipped is still ink on
    the page. Specks are left out of the ink, so noise does not count
    against a read. Measured, choosing the read with the most coverage
    picked the best (or within 0.02 of the best) of three reads on 13 of 14
    test pages."""
    ink = despeckle(gray) < 128
    total = int(ink.sum())
    if total == 0:
        return 0.0
    covered = np.zeros_like(ink)
    scale = dpi / 72
    pad = 2
    for x0, y0, x1, y1, *_ in ocr_page.get_text("words"):
        covered[
            max(0, int(y0 * scale) - pad): int(y1 * scale) + pad + 1,
            max(0, int(x0 * scale) - pad): int(x1 * scale) + pad + 1,
        ] = True
    return float((ink & covered).sum() / total)


def recognize(page_pdf: bytes, tessdata: str) -> dict:
    """Run in the worker process: OCR the one page in `page_pdf` and return
    its character and word boxes in that page's own text coordinates.

    First the page is straightened (`detect_skew`), since a tilt of a few
    degrees costs Tesseract most of a dense page. Then up to three reads,
    stopping as soon as one accounts for `GOOD_INK_COVERAGE` of the ink: as
    rendered, which is all a clean scan ever needs; despeckled; and
    despeckled at two-thirds the resolution, which on a noisy office scan
    kept lines the full resolution lost. A later read replaces the kept one
    only if it covers clearly more ink (`COVERAGE_MARGIN`).

    MuPDF's OCR writes a new PDF whose page is the image plus an invisible
    text layer, so reading that layer back with the same extraction the rest
    of the app uses gives boxes in the same shape; `_render`'s matrix takes
    them back onto the original page.
    """
    page = fitz.open("pdf", page_pdf)[0]
    dpi = _ocr_dpi(page)
    angle = detect_skew(_render(page, SKEW_DPI, 0.0)[0])
    if abs(angle) < MIN_SKEW_DEG:
        angle = 0.0
    best: tuple[float, fitz.Page, fitz.Matrix] | None = None
    for pass_dpi, clean in ((dpi, False), (dpi, True), (dpi * 2 // 3, True)):
        gray, back = _render(page, pass_dpi, angle)
        if clean:
            gray = despeckle(gray)
        ocr_page = _ocr_bitmap(gray, pass_dpi, tessdata)
        coverage = ink_coverage(gray, ocr_page, pass_dpi)
        if best is None or coverage > best[0] + COVERAGE_MARGIN:
            best = (coverage, ocr_page, back)
        if best[0] >= GOOD_INK_COVERAGE:
            break
    _, ocr_page, to_page = best

    def box(rect: fitz.Rect) -> list[float]:
        r = rect * to_page
        return [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]

    return {
        "chars": [
            [*box(c.rect), c.text, c.block_no, c.line_no, c.char_no]
            for c in highlighter.chars_on_page(ocr_page)
        ],
        "words": [
            [*box(w.rect), w.text, w.block_no, w.line_no, w.word_no]
            for w in highlighter.words_on_page(ocr_page)
        ],
    }


def text_from_payload(payload: dict) -> tuple[list[highlighter.Char], list[highlighter.Word]]:
    """`recognize`'s (or the cache's) plain lists back as Char/Word objects.
    Raises ValueError/TypeError on a malformed payload."""
    chars = [
        highlighter.Char(fitz.Rect(x0, y0, x1, y1), str(text), int(block), int(line), int(n))
        for x0, y0, x1, y1, text, block, line, n in payload["chars"]
    ]
    words = [
        highlighter.Word(fitz.Rect(x0, y0, x1, y1), str(text), int(block), int(line), int(n))
        for x0, y0, x1, y1, text, block, line, n in payload["words"]
    ]
    return chars, words


def is_readable(words: list[highlighter.Word]) -> bool:
    """Whether an OCR result is text worth matching against.

    Tesseract given a blurred photo returns dozens of word-shaped fragments
    ("ion", "pute", "Sate") rather than nothing, and treating those as the
    page's text would turn "nothing could be read here" into a puzzling
    "couldn't find that phrase". MuPDF's OCR does not report Tesseract's
    confidence, so this asks the text itself: English prose is dense with
    function words and a table with numbers, and noise is neither.
    """
    tokens = [w.text.strip(".,;:!?()\"'").lower() for w in words]
    tokens = [t for t in tokens if _NUMBER.match(t) or (len(t) >= 3 and t.isalpha())]
    if len(tokens) < MIN_READABLE_WORDS:
        return False
    common = sum(1 for t in tokens if t in _COMMON_WORDS or _NUMBER.match(t))
    return common / len(tokens) >= MIN_COMMON_WORD_SHARE


@dataclass(frozen=True)
class OcrJob:
    """One page to read. `generation` is the open document's, so a result
    that arrives after another PDF has been opened can be recognised and
    dropped."""

    generation: int
    page_index: int
    key: str
    page_pdf: bytes


# Called with the job, the outcome status (DONE, UNAVAILABLE or ERROR) and,
# for DONE, `recognize`'s payload.
FinishedCallback = Callable[[OcrJob, str, dict | None], None]


class OcrRunner:
    """Runs OCR jobs one at a time in a single worker process.

    One thread queues the jobs and waits on each; the wait releases the GIL,
    so only the worker process is busy. The process is started on the first
    job, not at launch, and replaced after a timeout or a crash.

    The thread is a daemon and waits in short slices, so closing the app in
    the middle of a read is immediate: a pool-executor thread waiting out
    the timeout kept the process alive for two minutes after the window
    closed (measured), because Python joins those threads at exit.
    """

    def __init__(self, on_finished: FinishedCallback, tessdata_dir: Path = TESSDATA_DIR):
        self._on_finished = on_finished
        self._tessdata_dir = tessdata_dir
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = None
        self._pool_lock = threading.Lock()

    def submit(self, job: OcrJob) -> None:
        if self._closing.is_set():
            return
        with self._pool_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._serve, name="lector-ocr", daemon=True)
                self._thread.start()
        self._jobs.put(job)

    def _serve(self) -> None:
        while not self._closing.is_set():
            job = self._jobs.get()
            if job is None:  # shutdown's wake-up
                return
            self._run(job)

    def _run(self, job: OcrJob) -> None:
        status, payload = self._read(job)
        if self._closing.is_set():
            return  # nobody left to tell
        try:
            self._on_finished(job, status, payload)
        except Exception:
            log.exception("OCR result handler failed for page %d", job.page_index)

    def _read(self, job: OcrJob) -> tuple[str, dict | None]:
        if not (self._tessdata_dir / f"{OCR_LANGUAGE}.traineddata").is_file():
            log.warning("OCR language data missing from %s", self._tessdata_dir)
            return UNAVAILABLE, None
        try:
            pending = self._ensure_pool().apply_async(
                recognize, (job.page_pdf, str(self._tessdata_dir))
            )
            deadline = time.monotonic() + OCR_TIMEOUT_S
            while not pending.ready():
                if self._closing.is_set():
                    return ERROR, None
                if time.monotonic() > deadline:
                    raise multiprocessing.TimeoutError
                pending.wait(_WAIT_SLICE_S)
            return DONE, pending.get(0)
        except multiprocessing.TimeoutError:
            log.error("OCR timed out on page %d; replacing the worker", job.page_index)
            self._discard_pool()
            return ERROR, None
        except Exception as exc:
            log.exception("OCR failed on page %d", job.page_index)
            # MuPDF's wording when Tesseract cannot load its language data
            # (present but unreadable, say) — that is "not installed" to a
            # reader, not "this page is unreadable".
            if "language initialisation" in str(exc):
                return UNAVAILABLE, None
            return ERROR, None

    def _ensure_pool(self):
        with self._pool_lock:
            if self._pool is None:
                # Spawn, never fork: the parent holds a webview, audio streams
                # and threads that a forked child must not inherit.
                self._pool = multiprocessing.get_context("spawn").Pool(processes=1)
            return self._pool

    def _discard_pool(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.terminate()

    def shutdown(self) -> None:
        """Stop the worker process. Queued jobs are dropped; the app is
        closing, so nobody is waiting on them."""
        self._closing.set()
        self._jobs.put(None)
        self._discard_pool()
