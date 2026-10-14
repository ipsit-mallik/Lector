"""Scanned pages: OCR on demand, its cache, and its wiring into highlighting.

Three layers, cheapest first:

* **Pure logic** — scanned-page detection, the cache key, the readable-text
  test, the disk cache and `PdfDocument`'s status machine, driven with
  synthetic OCR payloads (a born-digital page's own boxes), so they need no
  Tesseract and run in milliseconds.
* **Real OCR** — `OcrRunner` reading a rendered page in its worker process,
  checking the boxes land where the text really is, rotated pages included.
  Skipped when `assets/tessdata/eng.traineddata` has not been fetched, the
  same way the voice tests skip without the Vosk model.
* **Bridge** — a real `Api` with the runner and window mocked, for what the
  frontend is told.
"""
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import fitz
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading import ocr, ocr_cache  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import command_grammar  # noqa: E402

READING_JS = (ROOT / "frontend" / "pages" / "reading.js").read_text(encoding="utf-8")
VOICE_JS = (ROOT / "frontend" / "js" / "voice.js").read_text(encoding="utf-8")
DESIGN_SYSTEM = (ROOT / "docs" / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")

HAS_TESSDATA = (ocr.TESSDATA_DIR / f"{ocr.OCR_LANGUAGE}.traineddata").is_file()

PROSE = (
    "It is recommended that a book be used which can be ruled the same as the "
    "form below, so that a record of all the tests may be preserved for "
    "reference. This is preferable to a loose sheet, which may be lost."
)


def _text_page(doc: fitz.Document, text: str = PROSE) -> fitz.Page:
    page = doc.new_page(width=420, height=300)
    page.insert_textbox(fitz.Rect(30, 30, 390, 270), text, fontsize=14)
    return page


def _scanned_copy(text_page: fitz.Page, out: fitz.Document, dpi: int = 200) -> fitz.Page:
    """A picture of `text_page` on a page of the same size, with no text
    layer — what a scanner produces."""
    pix = text_page.get_pixmap(dpi=dpi)
    page = out.new_page(width=text_page.rect.width, height=text_page.rect.height)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    return page


def _payload_for(page: fitz.Page) -> dict:
    """The payload `ocr.recognize` would return for a page whose text it
    read perfectly: that page's own boxes."""
    return {
        "chars": [
            [c.rect.x0, c.rect.y0, c.rect.x1, c.rect.y1, c.text, c.block_no, c.line_no, c.char_no]
            for c in highlighter.chars_on_page(page)
        ],
        "words": [
            [w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1, w.text, w.block_no, w.line_no, w.word_no]
            for w in highlighter.words_on_page(page)
        ],
    }


def _words(*texts: str) -> list[highlighter.Word]:
    return [highlighter.Word(fitz.Rect(0, 0, 1, 1), t, 0, 0, i) for i, t in enumerate(texts)]


class TempDirTestCase(unittest.TestCase):
    """Gives each test a scratch folder, and points the OCR cache into it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lector-ocr-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cache = self.dir / "ocr-cache"
        patcher = mock.patch.object(ocr_cache, "cache_dir", lambda: self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mixed_pdf(self) -> Path:
        """Page 0 born-digital, page 1 scanned, page 2 blank."""
        source = fitz.open()
        text_page = _text_page(source)
        doc = fitz.open()
        _text_page(doc)
        _scanned_copy(text_page, doc)
        doc.new_page(width=420, height=300)
        path = self.dir / "mixed.pdf"
        doc.save(path)
        return path


class DetectionTests(TempDirTestCase):
    def test_detection_is_per_page(self):
        doc = fitz.open(self.mixed_pdf())
        self.assertEqual([ocr.page_needs_ocr(p) for p in doc], [False, True, False])

    def test_a_blank_page_is_not_scanned(self):
        doc = fitz.open()
        self.assertFalse(ocr.page_needs_ocr(doc.new_page()))


class PageKeyTests(TempDirTestCase):
    def test_key_survives_highlighting_and_both_kinds_of_save(self):
        path = self.mixed_pdf()
        doc = fitz.open(path)
        self.addCleanup(doc.close)
        before = ocr.page_key(doc, doc[1])
        page = doc[1]
        page.add_highlight_annot(fitz.Rect(40, 40, 200, 60))
        self.assertEqual(ocr.page_key(doc, doc[1]), before)
        copy = self.dir / "copy.pdf"
        doc.save(copy, deflate=True)
        reopened = fitz.open(copy)
        self.addCleanup(reopened.close)
        self.assertEqual(ocr.page_key(reopened, reopened[1]), before)
        doc.saveIncr()
        overwritten = fitz.open(path)
        self.addCleanup(overwritten.close)
        self.assertEqual(ocr.page_key(overwritten, overwritten[1]), before)

    def test_key_differs_between_pages_and_with_rotation(self):
        source = fitz.open()
        doc = fitz.open()
        _scanned_copy(_text_page(source, "First page words."), doc)
        _scanned_copy(_text_page(source, "Second page, other words."), doc)
        first = ocr.page_key(doc, doc[0])
        self.assertNotEqual(first, ocr.page_key(doc, doc[1]))
        doc[0].set_rotation(90)
        self.assertNotEqual(first, ocr.page_key(doc, doc[0]))

    def test_single_page_pdf_carries_no_annotations(self):
        doc = fitz.open(self.mixed_pdf())
        page = doc[1]
        page.add_highlight_annot(fitz.Rect(40, 40, 200, 60))
        single = fitz.open("pdf", ocr.single_page_pdf(doc, 1))
        self.assertEqual(len(single), 1)
        self.assertEqual(list(single[0].annots()), [])


class ReadableTests(unittest.TestCase):
    def test_prose_is_readable(self):
        self.assertTrue(ocr.is_readable(_words(*PROSE.split())))

    def test_a_table_of_numbers_is_readable(self):
        self.assertTrue(ocr.is_readable(_words("Jar", "No.", "37", "38", "39", "40", "41", "42")))

    def test_noise_from_a_photo_is_not(self):
        # What Tesseract returned for a skewed, blurred phone photo of a page,
        # on two different reads.
        for noise in (
            "ion | ia bo - ee le wie! least aml v ae a b= = pute ai . ead a si rr * idl av i =4",
            # Short real words turn up in noise by chance; they are not counted.
            "sgn ts r r aa be - ; aee | a og be W i eel th oad ee a , marr eee lat i 74 ieee oF"
            " * r ie a= ft as Fou amas t ake) Feat eee eS| Li = ; on o=yr ae nb. = pent wll fea",
        ):
            with self.subTest(noise=noise[:20]):
                self.assertFalse(ocr.is_readable(_words(*noise.split())))

    def test_a_couple_of_words_is_not_enough(self):
        self.assertFalse(ocr.is_readable(_words("the", "of")))


def _gray(page: fitz.Page, dpi: int = 150) -> "np.ndarray":
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


def _speckled(gray: "np.ndarray", per_megapixel: int = 3000, seed: int = 7) -> "np.ndarray":
    """`gray` with single black dots scattered over it, the way the noisy
    office scan this was tuned on is (about 3000 per megapixel)."""
    rng = np.random.default_rng(seed)
    out = gray.copy()
    count = int(per_megapixel * gray.size / 1e6)
    out[rng.integers(0, gray.shape[0], count), rng.integers(0, gray.shape[1], count)] = 0
    return out


def _tilted_scan(text_page: fitz.Page, degrees: float, noisy: bool = False) -> tuple[fitz.Document, fitz.Matrix]:
    """An image-only page showing `text_page` turned by `degrees`
    (positive: lines run down to the right), as a crooked scan would, and
    the matrix taking a point on `text_page` to where it shows on the scan."""
    render = fitz.Matrix(200 / 72, 200 / 72) * fitz.Matrix(degrees)
    pix = text_page.get_pixmap(matrix=render, colorspace=fitz.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    if noisy:
        gray = _speckled(gray)
    image = fitz.Pixmap(fitz.csGRAY, gray.shape[1], gray.shape[0], gray.tobytes(), False)
    doc = fitz.open()
    page = doc.new_page(width=pix.width * 72 / 200, height=pix.height * 72 / 200)
    page.insert_image(page.rect, stream=image.tobytes("png"))
    to_scan = render * fitz.Matrix(1, 0, 0, 1, -pix.x, -pix.y) * fitz.Matrix(72 / 200, 72 / 200)
    return doc, to_scan


class DespeckleTests(unittest.TestCase):
    def test_isolated_specks_go_and_text_stays(self):
        doc = fitz.open()
        clean = _gray(_text_page(doc))
        noisy = _speckled(clean)
        self.assertGreater(int((noisy < 128).sum()), int((clean < 128).sum()))
        cleaned = ocr.despeckle(noisy)
        # The specks that landed on white paper are gone, all but the few
        # that happened to land touching the text or each other (1.5%)...
        specks = ((noisy < 128) & (clean >= 128)).sum()
        left = ((cleaned < 128) & (clean >= 128)).sum()
        self.assertLess(left, 0.03 * specks)
        # ...and the text is (nearly) untouched.
        kept = ((cleaned < 128) & (clean < 128)).sum() / max(1, (clean < 128).sum())
        self.assertGreater(kept, 0.98)


class SkewTests(unittest.TestCase):
    def test_measures_a_known_tilt(self):
        source = fitz.open()
        text_page = _text_page(source)
        for degrees in (0.0, 2.0, -4.0, 7.0):
            with self.subTest(degrees=degrees):
                scan, _ = _tilted_scan(text_page, degrees)
                self.assertAlmostEqual(ocr.detect_skew(_gray(scan[0])), degrees, delta=0.25)

    def test_a_blank_page_has_no_tilt(self):
        self.assertEqual(ocr.detect_skew(np.full((400, 300), 255, np.uint8)), 0.0)


class PayloadTests(unittest.TestCase):
    def test_round_trip(self):
        doc = fitz.open()
        page = _text_page(doc)
        chars, words = ocr.text_from_payload(_payload_for(page))
        self.assertEqual([w.text for w in words], [w.text for w in highlighter.words_on_page(page)])
        self.assertEqual(len(chars), len(highlighter.chars_on_page(page)))

    def test_malformed_payload_raises(self):
        with self.assertRaises((KeyError, ValueError, TypeError)):
            ocr.text_from_payload({"chars": [[1, 2, 3]], "words": []})


class CacheTests(TempDirTestCase):
    def test_store_then_load(self):
        ocr_cache.store("k1", {"chars": [], "words": []})
        self.assertEqual(ocr_cache.load("k1")["words"], [])
        self.assertIsNone(ocr_cache.load("missing"))

    def test_corrupt_entry_is_a_miss_and_is_removed(self):
        self.cache.mkdir(parents=True)
        (self.cache / "bad.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(ocr_cache.load("bad"))
        self.assertFalse((self.cache / "bad.json").exists())

    def test_old_format_is_a_miss(self):
        self.cache.mkdir(parents=True)
        (self.cache / "old.json").write_text(json.dumps({"v": 0, "chars": [], "words": []}), encoding="utf-8")
        self.assertIsNone(ocr_cache.load("old"))

    def test_eviction_drops_least_recently_used_first(self):
        payload = {"chars": [], "words": [["x" * 400]]}
        for i, key in enumerate(("oldest", "middle", "newest")):
            ocr_cache.store(key, payload)
            stamp = time.time() - 100 + i * 10
            os.utime(self.cache / f"{key}.json", (stamp, stamp))
        ocr_cache.load("oldest")  # a hit makes it the most recently used
        size = (self.cache / "oldest.json").stat().st_size
        ocr_cache.evict(self.cache, max_bytes=size * 2)
        self.assertEqual(sorted(p.stem for p in self.cache.glob("*.json")), ["newest", "oldest"])

    def test_store_keeps_the_folder_under_the_cap(self):
        payload = {"chars": [], "words": [["x" * 1000]]}
        for i in range(5):
            ocr_cache.store(f"k{i}", payload, max_bytes=2500)
        total = sum(p.stat().st_size for p in self.cache.glob("*.json"))
        self.assertLessEqual(total, 2500)
        self.assertTrue((self.cache / "k4.json").exists())


class DocumentOcrTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.mixed_pdf()
        self.doc = PdfDocument()
        self.doc.open(str(self.path))
        self.addCleanup(self.doc.close)
        source = fitz.open()
        self.payload = _payload_for(_text_page(source))

    def _read(self, payload=None, status=ocr.DONE):
        job = self.doc.begin_ocr(1)
        self.assertIsNotNone(job)
        self.assertTrue(self.doc.finish_ocr(job, status, payload))
        return job

    def test_statuses_before_any_reading(self):
        self.assertEqual([self.doc.ocr_status(i) for i in range(3)], [None, ocr.NEEDED, None])
        self.assertEqual(self.doc.chars_on_page(1), [])

    def test_begin_is_once_per_page(self):
        self.assertIsNotNone(self.doc.begin_ocr(1))
        self.assertEqual(self.doc.ocr_status(1), ocr.RUNNING)
        self.assertIsNone(self.doc.begin_ocr(1))
        self.assertIsNone(self.doc.begin_ocr(0), "a page with its own text is never read")

    def test_finished_page_feeds_the_highlight_pipeline(self):
        self._read(self.payload)
        self.assertEqual(self.doc.ocr_status(1), ocr.DONE)
        self.assertGreater(len(self.doc.words_on_page(1)), 10)
        self.assertIsNotNone(self.doc.highlight_char_indices(1, 0, 10))
        highlight = self.doc.highlights_on_page(1)[0]
        self.assertTrue(self.doc.text_in_rects(1, highlight.rects).startswith("It is"))
        self.assertIsNone(self.doc.begin_ocr(1), "a finished page is not read again")

    def test_noise_is_no_text_and_is_not_retried(self):
        self._read({"chars": [], "words": [[0, 0, 1, 1, "ion", 0, 0, 0]]})
        self.assertEqual(self.doc.ocr_status(1), ocr.NO_TEXT)
        self.assertEqual(self.doc.words_on_page(1), [])
        self.assertIsNone(self.doc.begin_ocr(1))

    def test_failures_can_be_retried(self):
        for failure in (ocr.ERROR, ocr.UNAVAILABLE):
            with self.subTest(failure=failure):
                self._read(None, failure)
                self.assertEqual(self.doc.ocr_status(1), failure)
        self.assertIsNotNone(self.doc.begin_ocr(1))

    def test_a_result_for_a_closed_document_is_dropped(self):
        job = self.doc.begin_ocr(1)
        self.doc.open(str(self.path))
        self.assertFalse(self.doc.finish_ocr(job, ocr.DONE, self.payload))
        self.assertEqual(self.doc.ocr_status(1), ocr.NEEDED)

    def test_a_saved_copy_reopens_with_its_text_from_the_cache(self):
        self._read(self.payload)
        self.doc.highlight_char_indices(1, 0, 10)
        copy = self.dir / "copy.pdf"
        self.doc.save_copy(str(copy))
        self.doc.open(str(copy))
        self.assertEqual(self.doc.ocr_status(1), ocr.DONE)
        self.assertGreater(len(self.doc.chars_on_page(1)), 10)
        self.assertEqual(len(self.doc.highlights_on_page(1)), 1)

    def test_cached_text_is_there_whichever_call_comes_first(self):
        # Found in the reading view: after reopening a saved copy, the page's
        # highlights were listed before anything had asked for its OCR status,
        # so the highlight's name came out without its text.
        self._read(self.payload)
        self.doc.highlight_char_indices(1, 0, 10)
        copy = self.dir / "copy.pdf"
        self.doc.save_copy(str(copy))
        for first_call in ("text_in_rects", "words_on_page", "chars_on_page"):
            with self.subTest(first_call=first_call):
                self.doc.open(str(copy))
                if first_call == "text_in_rects":
                    rects = self.doc.highlights_on_page(1)[0].rects
                    self.assertTrue(self.doc.text_in_rects(1, rects).startswith("It is"))
                else:
                    self.assertGreater(len(getattr(self.doc, first_call)(1)), 10)

    def test_an_unreadable_cache_entry_means_reading_again(self):
        key = self._read(self.payload).key
        (self.cache / f"{key}.json").write_text(json.dumps({"v": 1, "chars": "x"}), encoding="utf-8")
        self.doc.open(str(self.path))
        self.assertEqual(self.doc.ocr_status(1), ocr.NEEDED)


class FakePool:
    """Stands in for the worker process. `error` is what the job raises when
    it finishes; `finishes=False` is a job that never does."""

    def __init__(self, error: BaseException | None = None, finishes: bool = True):
        self.error = error
        self.finishes = finishes
        self.terminated = False

    def apply_async(self, *args):
        pool = self

        class Pending:
            def ready(self):
                return pool.finishes

            def wait(self, timeout):
                time.sleep(timeout)

            def get(self, timeout=None):
                if not pool.finishes:  # as multiprocessing's does
                    time.sleep(timeout or 0)
                    raise multiprocessing.TimeoutError
                raise pool.error

        return Pending()

    def terminate(self):
        self.terminated = True


class RunnerOutcomeTests(TempDirTestCase):
    """How `OcrRunner` turns each way of failing into a status."""

    def _read_outcome(self, runner: ocr.OcrRunner) -> tuple[str, dict | None]:
        return runner._read(ocr.OcrJob(1, 0, "k", b""))

    def _runner(self, error: BaseException | None = None):
        tessdata = self.dir / "tessdata"
        tessdata.mkdir(exist_ok=True)
        (tessdata / "eng.traineddata").write_bytes(b"")
        runner = ocr.OcrRunner(lambda *a: None, tessdata_dir=tessdata)
        self.addCleanup(runner.shutdown)
        if error is not None:
            runner._pool = FakePool(error)
        return runner

    def test_missing_language_data_is_unavailable_without_starting_a_worker(self):
        runner = ocr.OcrRunner(lambda *a: None, tessdata_dir=self.dir / "nowhere")
        self.addCleanup(runner.shutdown)
        self.assertEqual(self._read_outcome(runner), (ocr.UNAVAILABLE, None))
        self.assertIsNone(runner._pool)

    def test_unloadable_language_data_is_unavailable(self):
        error = RuntimeError("code=3: Tesseract language initialisation failed")
        self.assertEqual(self._read_outcome(self._runner(error))[0], ocr.UNAVAILABLE)

    def test_any_other_failure_is_an_error(self):
        self.assertEqual(self._read_outcome(self._runner(RuntimeError("boom")))[0], ocr.ERROR)

    def test_a_timeout_is_an_error_and_replaces_the_worker(self):
        runner = self._runner()
        pool = runner._pool = FakePool(finishes=False)
        with mock.patch.object(ocr, "OCR_TIMEOUT_S", 0.2):
            self.assertEqual(self._read_outcome(runner)[0], ocr.ERROR)
        self.assertTrue(pool.terminated)
        self.assertIsNone(runner._pool)

    def test_closing_the_app_mid_read_lets_go_at_once(self):
        # Measured before the fix: a read under way at close held the process
        # open for the whole OCR timeout (two minutes) after the window went.
        runner = self._runner()
        runner._pool = FakePool(finishes=False)
        threading.Timer(0.2, runner.shutdown).start()
        started = time.monotonic()
        with mock.patch.object(ocr, "OCR_TIMEOUT_S", 5):
            self._read_outcome(runner)
        self.assertLess(time.monotonic() - started, 2)

    def test_the_queue_thread_does_not_hold_the_process_open(self):
        runner = self._runner()
        runner._pool = FakePool(finishes=False)
        runner.submit(ocr.OcrJob(1, 0, "k", b""))
        self.assertTrue(runner._thread.daemon)


@unittest.skipUnless(HAS_TESSDATA, "needs assets/tessdata (python scripts/fetch_tessdata.py)")
class RealOcrTests(unittest.TestCase):
    """Tesseract for real, in the worker process. One runner for the class,
    since starting the worker is most of the first job's time."""

    @classmethod
    def setUpClass(cls):
        cls.results: dict[int, tuple[str, dict | None]] = {}
        cls.finished = threading.Event()

        def on_finished(job, status, payload):
            cls.results[job.page_index] = (status, payload)
            cls.finished.set()

        cls.runner = ocr.OcrRunner(on_finished)

    @classmethod
    def tearDownClass(cls):
        cls.runner.shutdown()

    def _read(self, doc: fitz.Document, index: int) -> tuple[str, dict | None]:
        self.finished.clear()
        page = doc[index]
        self.runner.submit(ocr.OcrJob(1, index, ocr.page_key(doc, page), ocr.single_page_pdf(doc, index)))
        self.assertTrue(self.finished.wait(ocr.OCR_TIMEOUT_S))
        return self.results[index]

    def _assert_boxes_match(self, text_page: fitz.Page, payload: dict, to_page=fitz.Identity):
        """Each found word's centre lies on that word in `text_page`, mapped
        into the scanned page's text coordinates by `to_page`."""
        _, words = ocr.text_from_payload(payload)
        self.assertTrue(ocr.is_readable(words))
        truth = {w.text: w.rect for w in highlighter.words_on_page(text_page)}
        for word in ("recommended", "preserved", "preferable"):
            found = [w for w in words if w.text.strip(".,") == word]
            self.assertTrue(found, f"OCR did not find {word!r}")
            centre = (found[0].rect.tl + found[0].rect.br) / 2
            expected = (truth[word] * to_page) + (-3, -3, 3, 3)
            self.assertTrue(expected.contains(centre), f"{word!r} box is off the word")

    def test_reads_a_scanned_page_onto_the_words_it_shows(self):
        source = fitz.open()
        text_page = _text_page(source)
        doc = fitz.open()
        _scanned_copy(text_page, doc)
        status, payload = self._read(doc, 0)
        self.assertEqual(status, ocr.DONE)
        self._assert_boxes_match(text_page, payload)

    def test_boxes_on_a_rotated_page_match_its_text_coordinates(self):
        # How scanners produce a rotated page: the image stored sideways and
        # /Rotate turning it upright, so it displays exactly as `text_page`.
        source = fitz.open()
        text_page = _text_page(source)
        doc = fitz.open()
        page = doc.new_page(width=text_page.rect.height, height=text_page.rect.width)
        page.insert_image(page.rect, stream=text_page.get_pixmap(dpi=200).tobytes("png"), rotate=90)
        page.set_rotation(90)
        status, payload = self._read(doc, 0)
        self.assertEqual(status, ocr.DONE)
        self._assert_boxes_match(text_page, payload, to_page=doc[0].derotation_matrix)

    def test_a_crooked_scan_is_read_and_its_boxes_land_on_the_words(self):
        source = fitz.open()
        text_page = _text_page(source)
        for degrees in (4.0, -6.0):
            with self.subTest(degrees=degrees):
                scan, to_scan = _tilted_scan(text_page, degrees)
                status, payload = self._read(scan, 0)
                self.assertEqual(status, ocr.DONE)
                self._assert_boxes_match(text_page, payload, to_page=to_scan)

    def test_a_crooked_speckled_scan_is_read(self):
        # Both troubles of the office scan this was tuned on, together: single
        # black specks all over, and a tilt.
        source = fitz.open()
        text_page = _text_page(source)
        scan, to_scan = _tilted_scan(text_page, 3.0, noisy=True)
        status, payload = self._read(scan, 0)
        self.assertEqual(status, ocr.DONE)
        self._assert_boxes_match(text_page, payload, to_page=to_scan)


class BareHighlightTests(unittest.TestCase):
    def test_a_trigger_alone_is_a_bare_highlight(self):
        for text in ("highlight", "Highlight", "highlight sentence"):
            self.assertTrue(command_grammar.is_bare_highlight(text), text)

    def test_anything_else_is_not(self):
        for text in ("", "highlight the", "next page", "hi light the fox"):
            self.assertFalse(command_grammar.is_bare_highlight(text), text)


class BridgeTests(TempDirTestCase):
    """What the frontend is told, with a real `Api` and the OCR runner,
    microphone and window mocked."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api._voice = mock.MagicMock()
        self.api._ocr = mock.Mock()
        self.window = mock.Mock()
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api.open_pdf(str(self.mixed_pdf()))
        self.addCleanup(self.api._doc.close)  # Windows will not delete an open file
        self.payload = _payload_for(_text_page(fitz.open()))

    def _finish_submitted(self, status=ocr.DONE):
        job = self.api._ocr.submit.call_args[0][0]
        self.api._on_ocr_finished(job, status, self.payload if status == ocr.DONE else None)
        script = self.window.evaluate_js.call_args[0][0]
        marker = "{detail: "
        return json.loads(script[script.index(marker) + len(marker):].removesuffix("}))"))

    def test_page_chars_report_the_status(self):
        self.assertEqual([self.api.get_page_chars(i)["ocr"] for i in range(3)], [None, ocr.NEEDED, None])

    def test_ocr_page_submits_once_and_announces_the_outcome(self):
        self.assertEqual(self.api.ocr_page(1)["status"], ocr.RUNNING)
        self.assertEqual(self.api.ocr_page(1)["status"], ocr.RUNNING)
        self.assertEqual(self.api._ocr.submit.call_count, 1)
        self.assertEqual(self.api.ocr_page(0), {"page_index": 0, "status": None})
        detail = self._finish_submitted()
        self.assertEqual(detail, {"page_index": 1, "status": ocr.DONE})
        self.assertIn("lector:ocr", self.window.evaluate_js.call_args[0][0])
        self.assertGreater(len(self.api.get_page_chars(1)["chars"]), 10)

    def test_finished_page_widens_the_voice_vocabulary(self):
        self.api.update_voice_viewport([{"page_index": 1, "y0": 0, "y1": 300}])
        self.api.ocr_page(1)
        self._finish_submitted()
        vocabulary = self.api._voice.set_vocabulary.call_args[0][0]
        self.assertIn("recommended", vocabulary)

    def test_voice_highlight_before_the_text_is_read_starts_reading_it(self):
        self.api.start_listening([{"page_index": 1, "y0": 0, "y1": 300}])
        result = self.api.highlight_by_voice("recommended")["voice_highlight"]
        self.assertFalse(result["matched"])
        self.assertEqual(result["ocr"], {"page_index": 1, "status": ocr.RUNNING})
        self.api._ocr.submit.assert_called_once()

    def test_voice_highlight_after_the_text_was_read_meanwhile_says_say_it_again(self):
        self.api.start_listening([{"page_index": 1, "y0": 0, "y1": 300}])
        self.api.ocr_page(1)
        job = self.api._ocr.submit.call_args[0][0]
        self.api._doc.finish_ocr(job, ocr.DONE, self.payload)  # vocabulary not rebuilt
        result = self.api.highlight_by_voice("nothing like this")["voice_highlight"]
        self.assertEqual(result["ocr"], {"page_index": 1, "status": ocr.DONE})

    def test_voice_highlight_on_a_born_digital_page_has_no_ocr_story(self):
        self.api.start_listening([{"page_index": 0, "y0": 0, "y1": 300}])
        result = self.api.highlight_by_voice("nothing like this")["voice_highlight"]
        self.assertIsNone(result["ocr"])

    def test_opening_a_shorter_pdf_forgets_the_old_viewport(self):
        # Found while verifying OCR: the viewport outlived its document, so the
        # next vocabulary rebuild asked a one-page PDF for page 3 and the
        # reading page's set_voice_context call failed on load.
        self.api.start_listening([{"page_index": 2, "y0": 0, "y1": 300}])
        single = fitz.open()
        _text_page(single)
        path = self.dir / "single.pdf"
        single.save(path)
        self.api.open_pdf(str(path))
        self.assertEqual(self.api.set_voice_context("reading"), {"context": "reading"})

    def test_a_bare_highlight_heard_over_an_unread_page_carries_the_ocr_status(self):
        self.api.start_listening([{"page_index": 1, "y0": 0, "y1": 300}])
        self.api._on_voice_result({"final": True, "text": "highlight", "alternatives": []})
        script = self.window.evaluate_js.call_args[0][0]
        detail = json.loads(script[script.index("{detail: ") + 9:].removesuffix("}))"))
        self.assertIsNone(detail["command"])
        self.assertEqual(detail["ocr"], {"page_index": 1, "status": ocr.RUNNING})


class FrontendWiringTests(unittest.TestCase):
    """The page acts on what the bridge sends, and says what the docs say."""

    def test_reading_view_listens_for_finished_pages(self):
        self.assertIn('addEventListener("lector:ocr"', READING_JS)
        self.assertIn('callApi("ocr_page"', READING_JS)

    def test_voice_passes_the_ocr_status_through(self):
        self.assertIn("ocr: ocr || null", VOICE_JS)

    def test_every_message_is_the_documented_copy(self):
        for message in (
            "Reading text on this page…",
            "Text read — say it again",
            "Text read — drag to highlight",
            "No text found on this page",
            "Text recognition isn't installed",
            "Couldn't read the text on this page",
            "Couldn't read the text there",
        ):
            with self.subTest(message=message):
                self.assertIn(message, READING_JS)
                self.assertIn(message, DESIGN_SYSTEM)


if __name__ == "__main__":
    unittest.main()
