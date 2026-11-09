"""Running file jobs in the real worker process (Milestone F1, F1.1).

These spawn the OCR worker (`OcrRunner`), as the app does, so they are
slower than most; each class shares one worker.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import file_tools_support as fx  # noqa: E402
import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import jobs, tools  # noqa: E402
from lector.features.reading import ocr  # noqa: E402
from lector.features.settings import store  # noqa: E402

WAIT_S = 120


class _Recorder:
    def __init__(self):
        self.events: list[dict] = []
        self.finished = threading.Event()

    def __call__(self, event: dict) -> None:
        self.events.append(event)
        if event["state"] != jobs.WORKING:
            self.finished.set()

    @property
    def last(self) -> dict:
        return self.events[-1]


class WorkerCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = ocr.OcrRunner(lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        cls.worker.shutdown()

    def test_a_call_returns_its_result(self):
        self.assertEqual(self.worker.call(fx.quick_step, (21,)).result(WAIT_S), 42)

    def test_a_calls_own_error_comes_back(self):
        with self.assertRaises(ValueError):
            self.worker.call(fx.failing_step).result(WAIT_S)

    def test_an_aborted_call_ends_the_worker_and_the_next_call_still_runs(self):
        abort = threading.Event()
        future = self.worker.call(fx.slow_step, (30,), abort=abort)
        time.sleep(0.5)
        abort.set()
        started = time.monotonic()
        with self.assertRaises(ocr.CallAborted):
            future.result(WAIT_S)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.worker.call(fx.quick_step, (1,)).result(WAIT_S), 2)

    def test_a_hung_call_times_out_as_worker_lost(self):
        with self.assertRaises(ocr.WorkerLost):
            self.worker.call(fx.slow_step, (30,), timeout=0.5).result(WAIT_S)
        self.assertEqual(self.worker.call(fx.quick_step, (2,)).result(WAIT_S), 4)

    def test_ocr_and_calls_share_one_queue(self):
        finished = threading.Event()
        runner = ocr.OcrRunner(lambda job, status, payload: finished.set(), tessdata_dir=Path("missing"))
        try:
            future = runner.call(fx.quick_step, (5,))
            runner.submit(ocr.OcrJob(1, 0, "k", b""))
            self.assertEqual(future.result(WAIT_S), 10)
            self.assertTrue(finished.wait(WAIT_S))
        finally:
            runner.shutdown()


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = ocr.OcrRunner(lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        cls.worker.shutdown()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.recorder = _Recorder()
        self.runner = jobs.FileJobRunner(self.worker, self.recorder)

    def tearDown(self):
        self.runner.cancel()
        self.runner.wait(WAIT_S)
        self._tmp.cleanup()

    def finish(self) -> dict:
        self.assertTrue(self.recorder.finished.wait(WAIT_S), "the job never finished")
        self.runner.wait(WAIT_S)
        return self.recorder.last

    def test_a_job_reports_progress_then_its_result(self):
        source = pdf_support.multi_page(self.folder, pages=3, name="Three.pdf")
        dest = str(self.folder / "Three (images)")
        self.runner.start(
            "export", lambda ctx: tools.export_images(ctx, tools.Source(str(source)), dest, "png", 96, [0, 1, 2])
        )
        final = self.finish()
        self.assertEqual(final["state"], jobs.DONE)
        self.assertEqual(final["result"]["count"], 3)
        steps = [(e["current"], e["total"]) for e in self.recorder.events if e["state"] == jobs.WORKING and e["current"]]
        self.assertEqual(steps, [(1, 3), (2, 3), (3, 3)])

    def test_a_second_job_is_refused_while_one_runs(self):
        self.assertIsNotNone(self.runner.start("export", lambda ctx: ctx.step(fx.slow_step, 1.5)))
        self.assertIsNone(self.runner.start("compress", lambda ctx: {}))
        self.assertTrue(self.runner.is_running())
        self.assertEqual(self.runner.current()["tool"], "export")
        self.finish()
        self.assertIsNone(self.runner.current())

    def test_cancel_stops_at_the_next_page_and_writes_nothing(self):
        source = pdf_support.multi_page(self.folder, pages=30, name="Long.pdf")
        dest = str(self.folder / "Long (images)")
        self.runner.start(
            "export", lambda ctx: tools.export_images(ctx, tools.Source(str(source)), dest, "png", 150, list(range(30)))
        )
        deadline = time.monotonic() + WAIT_S
        while not any(e["current"] >= 2 for e in self.recorder.events) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.runner.cancel())
        final = self.finish()
        self.assertEqual(final["state"], jobs.CANCELLED)
        self.assertEqual(sorted(os.listdir(self.folder)), ["Long.pdf"])

    def test_cancel_in_a_long_step_ends_it_at_once(self):
        self.runner.start("compress", lambda ctx: ctx.step(fx.slow_step, 30, long=True))
        time.sleep(1.0)
        started = time.monotonic()
        self.runner.cancel()
        final = self.finish()
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(final["state"], jobs.CANCELLED)

    def test_a_failure_says_so_in_words_and_never_in_the_librarys(self):
        self.runner.start("compress", lambda ctx: ctx.step(fx.failing_step))
        final = self.finish()
        self.assertEqual(final["state"], jobs.FAILED)
        self.assertIn("The original is unchanged.", final["error"])
        self.assertNotIn("planted", final["error"])

    def test_an_unreadable_source_fails_cleanly(self):
        bad = self.folder / "bad.pdf"
        bad.write_bytes(b"%PDF-1.4 nothing else")
        self.runner.start(
            "compress", lambda ctx: tools.compress(ctx, tools.Source(str(bad)), str(self.folder / "o.pdf"), "light")
        )
        final = self.finish()
        self.assertEqual(final["state"], jobs.FAILED)
        self.assertEqual(sorted(os.listdir(self.folder)), ["bad.pdf"])


class ApiTests(unittest.TestCase):
    """The bridge's side: what the page can ask for, and what it refuses."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self._settings = mock.patch.object(store, "_settings_path", lambda: self.folder / "settings.json")
        self._settings.start()
        self.window = mock.Mock()
        self._windows = mock.patch.object(api_module.webview, "windows", [self.window])
        self._windows.start()
        self.api = api_module.Api()
        self.api._voice = mock.MagicMock()

    def tearDown(self):
        self.api._jobs.cancel()
        self.api._jobs.wait(WAIT_S)
        self.api._ocr.shutdown()
        self.api._doc.close()
        self._windows.stop()
        self._settings.stop()
        self._tmp.cleanup()

    def events(self) -> list[dict]:
        found = []
        for call in self.window.evaluate_js.call_args_list:
            script = call.args[0]
            if "lector:filejob" in script:
                found.append(json.loads(script.split("detail: ", 1)[1].rsplit("}))", 1)[0]))
        return found

    def wait_for_end(self) -> dict:
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            ends = [e for e in self.events() if e["state"] != jobs.WORKING]
            if ends:
                return ends[-1]
            time.sleep(0.1)
        self.fail("no lector:filejob end event")

    def gatekeeper_callback(self):
        calls = [c for c in self.window.evaluate_js.call_args_list if "confirmStopFileJob" in c.args[0]]
        self.assertEqual(len(calls), 1)
        return calls[0].kwargs["callback"]

    def test_the_open_document_is_compressed_with_its_unsaved_highlights_and_left_unsaved(self):
        source = fx.photo_pdf(self.folder, pages=1)
        self.api.open_pdf(str(source))
        page = self.api._doc._doc[0]
        page.add_highlight_annot(page.search_for(fx.KNOWN_WORD)[0])
        self.api._doc._dirty = True
        before = fx.annotations(source)
        info = self.api.file_tool_info("compress", "open")
        self.assertTrue(info["ok"])
        self.assertEqual(
            info["output"], {"folder": str(self.folder), "stem": "photos (compressed)", "extension": ".pdf"}
        )
        self.assertEqual(info["images"], 1)
        self.assertEqual(info["unsaved"], 1)
        options = {"level": "balanced", "folder": info["output"]["folder"], "name": info["output"]["stem"]}
        outcome = self.api.start_file_job("compress", "open", options)
        self.assertTrue(outcome["ok"])
        final = self.wait_for_end()
        self.assertEqual(final["state"], jobs.DONE)
        self.assertEqual(final["result"]["name"], "photos (compressed).pdf")
        self.assertEqual(final["result"]["where"], "beside the original")
        self.assertEqual(len(fx.annotations(final["result"]["path"])[0]), len(before[0]) + 1)
        self.assertTrue(self.api._doc.is_dirty)
        self.assertEqual(fx.annotations(source), before)
        self.assertEqual(store.get_file_tool_prefs()["compress_level"], "balanced")
        with mock.patch.object(api_module.home_recent, "reveal_in_file_manager") as reveal:
            self.assertEqual(self.api.show_in_folder(final["result"]["path"]), {"error": None})
        reveal.assert_called_once()

    def test_a_tool_never_writes_over_its_original(self):
        source = pdf_support.plain_text(self.folder)
        self.api.open_pdf(str(source))
        options = {"level": "light", "folder": str(source.parent), "name": source.stem}
        outcome = self.api.start_file_job("compress", "open", options)
        self.assertFalse(outcome["ok"])
        self.assertIn("original", outcome["error"])

    def test_bad_requests_are_refused(self):
        source = pdf_support.plain_text(self.folder)
        self.api.open_pdf(str(source))
        here = {"folder": str(self.folder), "name": "x"}
        for tool, options in (
            ("compress", {"level": "extreme", **here}),
            ("compress", {"level": "light", "folder": "relative", "name": "x"}),
            ("compress", {"level": "light", "folder": str(self.folder / "missing"), "name": "x"}),
            ("compress", {"level": "light", "folder": str(self.folder), "name": ""}),
            ("compress", {"level": "light", "folder": str(self.folder), "name": "a/b"}),
            ("compress", {"level": "light", "folder": str(self.folder), "name": r"..\x"}),
            ("compress", {"level": "light", "folder": str(self.folder), "name": "x?"}),
            ("compress", {"level": "light", "folder": str(self.folder), "name": ".."}),
            ("compress", {"level": "light", "dest": str(self.folder / "x.pdf")}),
            ("export", {"format": "gif", "ppi": 96, "pages": [1], **here}),
            ("export", {"format": "png", "ppi": 97, "pages": [1], **here}),
            ("export", {"format": "png", "ppi": True, "pages": [1], **here}),
            ("export", {"format": "png", "ppi": 96, "pages": [2], **here}),
            ("export", {"format": "png", "ppi": 96, "pages": [1, 1], **here}),
            ("export", {"format": "png", "ppi": 96, "pages": [1], "folder": str(self.folder), "name": ".."}),
            ("searchable", {"pages": [0], **here}),
            ("convert", {"inputs": [str(source)], "page_size": "a4", **here}),
            ("delete", {}),
        ):
            with self.subTest(tool=tool, options=options):
                self.assertFalse(self.api.start_file_job(tool, "open", options)["ok"])
        self.assertFalse(self.api._jobs.is_running())

    def test_a_second_job_is_refused_through_the_bridge(self):
        source = pdf_support.multi_page(self.folder, pages=40)
        self.api.open_pdf(str(source))
        here = {"folder": str(self.folder)}
        options = {"format": "png", "ppi": 300, "pages": list(range(1, 41)), "name": "imgs", **here}
        self.assertTrue(self.api.start_file_job("export", "open", options)["ok"])
        second = self.api.start_file_job("compress", "open", {"level": "light", "name": "c", **here})
        self.assertEqual(second, {"ok": False, "busy": True, "error": "Another file job is running."})
        self.api.cancel_file_job()
        self.assertEqual(self.wait_for_end()["state"], jobs.CANCELLED)

    def test_show_in_folder_only_for_what_a_job_wrote(self):
        stray = self.folder / "stray (images)"
        stray.mkdir()
        self.assertEqual(self.api.show_in_folder(str(stray)), {"error": "That file is no longer in Recent."})

    def test_a_password_protected_pdf_from_home_asks_and_keeps_it_in_memory_only(self):
        locked = str(pdf_support.encrypted(self.folder))
        self.assertEqual(self.api.file_tool_info("compress", locked)["reason"], "password")
        self.assertEqual(self.api.file_tool_info("compress", locked, "nope")["reason"], "wrong_password")
        info = self.api.file_tool_info("compress", locked, pdf_support.PASSWORD)
        self.assertTrue(info["ok"])
        settings_file = self.folder / "settings.json"
        if settings_file.exists():
            self.assertNotIn(pdf_support.PASSWORD, settings_file.read_text(encoding="utf-8"))

    def test_a_missing_home_file_says_it_couldnt_be_found(self):
        outcome = self.api.file_tool_info("export", str(self.folder / "gone.pdf"))
        self.assertEqual(outcome["reason"], "missing")
        self.assertIn("couldn't be found", outcome["error"])

    def test_the_menu_counts_pages_without_text_without_reading_them(self):
        self.api.open_pdf(str(fx.scanned_text_pdf(self.folder)))
        with mock.patch.object(ocr, "recognize", side_effect=AssertionError("ran OCR")):
            self.assertEqual(self.api.get_file_tool_menu(), {"textless": 1, "pages": 2})

    def test_closing_while_a_job_runs_asks_first(self):
        self.api._jobs.start("export", lambda ctx: ctx.step(fx.slow_step, 3))
        self.assertIs(self.api.handle_window_closing(), False)
        self.gatekeeper_callback()(True)
        self.assertFalse(self.api._jobs.is_running())
        self.window.destroy.assert_called_once()

    def test_keeping_the_job_running_does_not_close(self):
        self.api._jobs.start("export", lambda ctx: ctx.step(fx.slow_step, 2))
        self.assertIs(self.api.handle_window_closing(), False)
        self.gatekeeper_callback()(False)
        self.window.destroy.assert_not_called()
        self.assertTrue(self.api._jobs.is_running())


if __name__ == "__main__":
    unittest.main()
