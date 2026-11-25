"""What a tool's dialog knows first, and work nobody waits for any more.

- A PDF from Home is described at a glance in the main process
  (`quick_look`): the page count and the first page only, the document closed
  again, never queued behind the worker.
- Make searchable's pages with no text come separately
  (`file_tool_analysis`), in the worker: the newest request wins, one for the
  same file shares the work, and Back or Close (`cancel_file_tool_analysis`)
  drops it.
- A worker call asked to stop stops at its next page and the process carries
  on (`ocr.check_worker_stop`); one with no page to stop at is ended after
  `ocr.STOP_GRACE_S`.
- The worker can be started ahead of need (`OcrRunner.warm`).
"""
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402

import file_tools_support as fx  # noqa: E402
import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import engine, jobs, quick_look, tools  # noqa: E402
from lector.features.home import recent  # noqa: E402
from lector.features.reading import ocr, ocr_cache  # noqa: E402
from lector.features.settings import store  # noqa: E402

WAIT_S = 120
QUICK_LOOK_BUDGET_S = 0.05


def _pdf(path: Path, pages: int) -> Path:
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page().insert_text((72, 100), f"page {n + 1}", fontsize=12)
    doc.save(path)
    doc.close()
    return path


class QuickLookTests(unittest.TestCase):
    def test_it_takes_turns_with_homes_thumbnails(self):
        # One lock for Home's PyMuPDF work: the chooser asks for thumbnails
        # and a look at the same time, on two bridge threads.
        self.assertIs(quick_look._lock, recent._fitz_lock)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_the_first_page_is_loaded_and_the_document_is_closed(self):
        source = _pdf(self.folder / "long.pdf", 400)
        quick_look.look(str(source), None)  # MuPDF's first open in this process
        opened, loaded = [], []
        real_open, real_load = pymupdf.open, pymupdf.Document.load_page

        def tracking_open(*args, **kwargs):
            doc = real_open(*args, **kwargs)
            opened.append(doc)
            return doc

        def tracking_load(doc, *args, **kwargs):
            loaded.append(args[0] if args else kwargs.get("page_id", 0))
            return real_load(doc, *args, **kwargs)

        with mock.patch.object(pymupdf, "open", tracking_open), mock.patch.object(
            pymupdf.Document, "load_page", tracking_load
        ):
            started = time.perf_counter()
            info = quick_look.look(str(source), None)
            took = time.perf_counter() - started
        self.assertEqual(info["pages"], 400)
        self.assertEqual(info["page_size"], [595.0, 842.0])
        self.assertLessEqual(set(loaded), {0})
        self.assertTrue(opened and all(doc.is_closed for doc in opened))
        self.assertLess(took, QUICK_LOOK_BUDGET_S)

    def test_a_locked_pdf_asks_for_its_password(self):
        locked = str(pdf_support.encrypted(self.folder))
        self.assertEqual(quick_look.look(locked, None), {"locked": True})
        self.assertEqual(quick_look.look(locked, "nope"), {"locked": True})
        self.assertTrue(quick_look.look(locked, pdf_support.PASSWORD)["needs_pass"])

    def test_not_a_pdf_is_unreadable(self):
        junk = self.folder / "junk.pdf"
        junk.write_bytes(b"not a pdf")
        with self.assertRaises(quick_look.Unreadable):
            quick_look.look(str(junk), None)


class _BridgeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        for patcher in (
            mock.patch.object(store, "_settings_path", lambda: self.folder / "settings.json"),
            mock.patch.object(ocr_cache, "cache_dir", lambda: self.folder / "ocr-cache"),
            mock.patch.object(api_module.webview, "windows", [mock.Mock()]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.api._voice = mock.MagicMock()

    def tearDown(self):
        self.api._ocr.shutdown()
        self.api._doc.close()
        self._tmp.cleanup()


class FileToolInfoTests(_BridgeTest):
    def test_info_from_home_never_waits_for_the_worker(self):
        source = _pdf(self.folder / "report.pdf", 12)
        with mock.patch.object(self.api._ocr, "call", side_effect=AssertionError("queued behind the worker")):
            compress = self.api.file_tool_info("compress", str(source))
            searchable = self.api.file_tool_info("searchable", str(source))
            split = self.api.file_tool_info("split", str(source))
        self.assertTrue(compress["ok"])
        self.assertEqual((compress["pages"], compress["name"]), (12, "report.pdf"))
        self.assertFalse(compress["analysis_pending"])
        self.assertEqual((searchable["textless"], searchable["analysis_pending"]), (None, True))
        self.assertEqual((split["split_every"], split["preview_named"]), (6, 3))

    def test_a_protected_pdf_from_home_still_asks_for_its_password(self):
        locked = str(pdf_support.encrypted(self.folder))
        self.assertEqual(self.api.file_tool_info("compress", locked)["reason"], "password")
        self.assertTrue(self.api.file_tool_info("compress", locked, pdf_support.PASSWORD)["ok"])

    def test_a_look_at_another_pdf_keeps_the_password_already_given(self):
        locked = str(pdf_support.encrypted(self.folder))
        other = str(_pdf(self.folder / "other.pdf", 2))
        self.assertTrue(self.api.file_tool_info("compress", locked, pdf_support.PASSWORD)["ok"])
        # The list's next row is looked at, then the protected one is
        # Continued with (asked again without its password: it is held).
        self.assertTrue(self.api.file_tool_info("compress", other)["ok"])
        self.assertEqual(self.api._tool_setup.job_source(locked).password, pdf_support.PASSWORD)
        self.assertIsNone(self.api._tool_setup.job_source(other).password)
        self.api._tool_setup.forget_tool_passwords()
        self.assertIsNone(self.api._tool_setup.job_source(locked).password)

    def test_closing_the_dialog_forgets_the_passwords(self):
        locked = str(pdf_support.encrypted(self.folder))
        self.assertTrue(self.api.file_tool_info("compress", locked, pdf_support.PASSWORD)["ok"])
        self.assertEqual(self.api.forget_file_tool_passwords(), {"ok": True})
        self.assertIsNone(self.api._tool_setup.job_source(locked).password)

    def test_warming_the_worker_answers_at_once(self):
        with mock.patch.object(self.api._ocr, "warm") as warm:
            self.assertEqual(self.api.warm_file_tool_worker(), {"ok": True})
        warm.assert_called_once()


class _FakeWorker:
    """Hands back Futures the test settles, and records each call's abort."""

    def __init__(self):
        self.calls = []

    def call(self, fn, args=(), timeout=0, abort=None):
        future = Future()
        self.calls.append({"fn": fn, "args": args, "abort": abort, "future": future})
        return future

    def shutdown(self):
        pass


class AnalysisTests(_BridgeTest):
    def setUp(self):
        super().setUp()
        self.real_worker = self.api._tool_setup._core.ocr
        self.worker = _FakeWorker()
        self.api._tool_setup._core.ocr = self.worker
        self.a = str(_pdf(self.folder / "a.pdf", 3))
        self.b = str(_pdf(self.folder / "b.pdf", 3))

    def tearDown(self):
        self.api._tool_setup._core.ocr = self.real_worker
        super().tearDown()

    def ask(self, path, answers):
        thread = threading.Thread(target=lambda: answers.append(self.api.file_tool_analysis(path)))
        thread.start()
        return thread

    def wait_for_calls(self, count):
        deadline = time.monotonic() + 5
        while len(self.worker.calls) < count and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_it_runs_in_the_worker(self):
        answers = []
        thread = self.ask(self.a, answers)
        self.wait_for_calls(1)
        self.assertIs(self.worker.calls[0]["fn"], engine.textless_pages)
        self.worker.calls[0]["future"].set_result([])
        thread.join(5)
        self.assertEqual(answers, [{"ok": True, "textless": 0, "textless_pages": []}])

    def test_a_request_for_another_file_stops_the_older_one(self):
        first, second = [], []
        t1 = self.ask(self.a, first)
        self.wait_for_calls(1)
        t2 = self.ask(self.b, second)
        self.wait_for_calls(2)
        old, new = self.worker.calls
        self.assertTrue(old["abort"].is_set())
        self.assertFalse(new["abort"].is_set())
        old["future"].set_exception(ocr.CallAborted(replaced=False))
        new["future"].set_result([0, 2])
        t1.join(5)
        t2.join(5)
        self.assertEqual(first, [{"ok": False, "cancelled": True}])
        self.assertEqual(second, [{"ok": True, "textless": 2, "textless_pages": [1, 3]}])

    def test_the_same_file_shares_the_work(self):
        answers = []
        threads = [self.ask(self.a, answers), self.ask(self.a, answers)]
        self.wait_for_calls(1)
        time.sleep(0.1)
        self.assertEqual(len(self.worker.calls), 1)
        self.worker.calls[0]["future"].set_result([1])
        for thread in threads:
            thread.join(5)
        self.assertEqual(answers, [{"ok": True, "textless": 1, "textless_pages": [2]}] * 2)

    def test_a_finished_look_is_asked_again_never_handed_back(self):
        # Asked as the row is selected, before the password is given: it
        # fails. Asked again once unlocked, it runs again rather than
        # answering with that old failure.
        answers = []
        self.ask(self.a, answers).join(0)
        self.wait_for_calls(1)
        self.worker.calls[0]["future"].set_exception(engine.SourceError())
        deadline = time.monotonic() + 5
        while not answers and time.monotonic() < deadline:
            time.sleep(0.01)
        thread = self.ask(self.a, answers)
        self.wait_for_calls(2)
        self.assertEqual(len(self.worker.calls), 2)
        self.worker.calls[1]["future"].set_result([0])
        thread.join(5)
        self.assertEqual(answers[0]["reason"], "not_a_pdf")
        self.assertEqual(answers[1], {"ok": True, "textless": 1, "textless_pages": [1]})

    def test_cancel_drops_it(self):
        answers = []
        thread = self.ask(self.a, answers)
        self.wait_for_calls(1)
        self.api.cancel_file_tool_analysis()
        call = self.worker.calls[0]
        self.assertTrue(call["abort"].is_set())
        call["future"].set_exception(ocr.CallAborted(replaced=False))
        thread.join(5)
        self.assertEqual(answers, [{"ok": False, "cancelled": True}])


class TextlessPagesTests(unittest.TestCase):
    def test_pages_with_no_text_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(fx.scanned_text_pdf(Path(tmp)))
            self.assertEqual(engine.textless_pages(path, None), [0])


class WorkerStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = ocr.OcrRunner(lambda *a: None)
        cls.worker.warm().result(WAIT_S)

    @classmethod
    def tearDownClass(cls):
        cls.worker.shutdown()

    def test_a_call_asked_to_stop_stops_at_its_next_page_and_the_worker_carries_on(self):
        before = ocr._worker_pids(self.worker._pool)
        abort = threading.Event()
        future = self.worker.call(fx.paged_step, (400, 0.05), abort=abort)
        time.sleep(0.4)
        abort.set()
        started = time.monotonic()
        with self.assertRaises(ocr.CallAborted) as caught:
            future.result(WAIT_S)
        # Well inside the grace (a busy machine can take most of it), and
        # the process kept: it stopped at a page rather than being ended.
        self.assertLess(time.monotonic() - started, ocr.STOP_GRACE_S)
        self.assertFalse(caught.exception.replaced)
        self.assertEqual(ocr._worker_pids(self.worker._pool), before)
        self.assertEqual(self.worker.call(fx.paged_step, (2, 0.0)).result(WAIT_S), 2)

    def test_a_call_with_no_page_to_stop_at_is_ended_after_the_grace(self):
        abort = threading.Event()
        future = self.worker.call(fx.slow_step, (30,), abort=abort)
        time.sleep(0.4)
        abort.set()
        started = time.monotonic()
        with self.assertRaises(ocr.CallAborted) as caught:
            future.result(WAIT_S)
        took = time.monotonic() - started
        self.assertTrue(caught.exception.replaced)
        self.assertGreaterEqual(took, ocr.STOP_GRACE_S - 0.2)
        self.assertLess(took, ocr.STOP_GRACE_S + 3)
        self.assertEqual(self.worker.call(fx.quick_step, (4,)).result(WAIT_S), 8)

    def test_a_queued_call_nobody_wants_is_dropped_without_running(self):
        blocker = self.worker.call(fx.paged_step, (6, 0.05))
        abort = threading.Event()
        queued = self.worker.call(fx.quick_step, (1,), abort=abort)
        abort.set()
        with self.assertRaises(ocr.CallAborted) as caught:
            queued.result(WAIT_S)
        self.assertFalse(caught.exception.replaced)
        self.assertEqual(blocker.result(WAIT_S), 6)


class WarmTests(unittest.TestCase):
    def test_warming_starts_the_worker_process(self):
        worker = ocr.OcrRunner(lambda *a: None)
        try:
            self.assertIsNone(worker._pool)
            self.assertTrue(worker.warm().result(WAIT_S))
            self.assertTrue(ocr._worker_pids(worker._pool))
        finally:
            worker.shutdown()



class OpenStoppedAsItFinishedTests(unittest.TestCase):
    """A Cancel while a job's one long open runs, which finishes anyway
    within the grace: the worker holds the document, and the job hasn't yet
    reached the `finally` that lets go of it, so the open lets go itself."""

    class _Ctx:
        job_id = "job1"

        def __init__(self, outcome):
            self.outcome = outcome
            self.released = []

        def step(self, fn, *args, long=False):
            raise self.outcome

        def release(self, close):
            self.released.append(close)

    def test_a_stopped_open_is_released(self):
        source = tools.Source("C:/Docs/Report.pdf", None, None)
        for name, fn, close in (("open", tools._open, engine.close_job),
                                ("split", lambda c, s: tools._opened(c, engine.split_open, engine.split_close, s),
                                 engine.split_close)):
            with self.subTest(open=name):
                ctx = self._Ctx(jobs.JobCancelled())
                with self.assertRaises(jobs.JobCancelled):
                    fn(ctx, source)
                self.assertEqual(ctx.released, [close])

    def test_an_unreadable_pdf_has_nothing_to_release(self):
        ctx = self._Ctx(engine.SourceError())
        with self.assertRaises(jobs.JobFailed):
            tools._open(ctx, tools.Source("C:/Docs/Report.pdf", None, None))
        self.assertEqual(ctx.released, [])


if __name__ == "__main__":
    unittest.main()
