"""The log file never holds what the reader reads, says or highlights
(hardening H3).

Runs real paths with the real file handler from `app_log.setup`, using a PDF
whose text includes words found nowhere else. Every failure here is set up
to carry those words in its exception message, as a library quoting its
input would. The log must record each failure and never the words.

`unittest`, matching the rest of the suite. PDFs are built in code.
"""
import logging
import multiprocessing
import sys
import tempfile
import threading
import unittest
from multiprocessing.pool import RemoteTraceback
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the throwaway PDF

from lector import api as api_module  # noqa: E402
from lector.features.reading import ocr  # noqa: E402
from lector.features.settings import store as settings  # noqa: E402
from lector.shared import app_log  # noqa: E402

# Words that appear in the document, the query, the transcript and the OCR
# output, and so must appear nowhere in the log.
DOCUMENT_WORDS = ("quokkazephyr", "plinthbramble")
SPOKEN_WORDS = "highlight quokkazephyr"
OCR_WORDS = "marzipanfjord"
ALL_WORDS = (*DOCUMENT_WORDS, OCR_WORDS)


class LogPrivacyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-privacy-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch.object(settings, "_settings_path", lambda: self.root / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)

        app_logger = logging.getLogger("lector")
        self.addCleanup(app_logger.setLevel, app_logger.level)
        self.addCleanup(logging.captureWarnings, False)
        self.log_dir = self.root / "logs"
        self.handler = app_log.setup(self.log_dir)
        self.assertIsNotNone(self.handler)
        self.addCleanup(self.handler.close)
        self.addCleanup(logging.getLogger().removeHandler, self.handler)

        self.pdf = self.root / "private notes.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), f"The {DOCUMENT_WORDS[0]} met a {DOCUMENT_WORDS[1]} today.")
        doc.save(self.pdf)
        doc.close()

        self.api = api_module.Api(self.log_dir)
        self.addCleanup(self.api.shutdown_ocr)
        self.addCleanup(self.api.shutdown_voice)
        # Windows won't delete a PDF that is still open.
        self.addCleanup(self.api._doc.close)

    def written(self) -> str:
        self.handler.flush()
        return (self.log_dir / app_log.LOG_FILE).read_text(encoding="utf-8")

    def assert_no_content(self, text: str) -> None:
        for word in ALL_WORDS:
            self.assertNotIn(word, text)
        self.assertNotIn("private notes", text)

    def test_reading_highlighting_and_a_failed_save(self):
        self.api.open_pdf(str(self.pdf))
        self.api.highlight_chars(0, 4, 16)
        self.api.find_in_document(DOCUMENT_WORDS[0])
        failure = RuntimeError(f"cannot write '{self.pdf}': {DOCUMENT_WORDS[0]}")
        with mock.patch.object(self.api._doc, "save_copy", side_effect=failure):
            outcome = self.api.perform_save(settings.COPY, str(self.root / "copy.pdf"))

        self.assertFalse(outcome["ok"])
        log_text = self.written()
        self.assertIn("Saving the PDF failed", log_text)
        self.assertIn("RuntimeError", log_text)
        self.assert_no_content(log_text)

    def test_a_voice_result_that_fails_to_resolve(self):
        window = mock.Mock()
        with mock.patch.object(api_module.webview, "windows", [window]), \
                mock.patch.object(api_module.router, "resolve", side_effect=KeyError(SPOKEN_WORDS)):
            self.api._voice._emit(SPOKEN_WORDS, final=True)

        log_text = self.written()
        self.assertIn("A voice result subscriber failed", log_text)
        self.assertIn("KeyError", log_text)
        self.assert_no_content(log_text)

    def test_an_ocr_failure_from_the_worker(self):
        tessdata = self.root / "tessdata"
        tessdata.mkdir()
        (tessdata / f"{ocr.OCR_LANGUAGE}.traineddata").write_bytes(b"")
        error = ValueError(f"recognised text: {OCR_WORDS}")
        error.__cause__ = RemoteTraceback(
            f'\n"""\nTraceback (most recent call last):\n'
            f'  File "ocr.py", line 280, in recognize\n'
            f"ValueError: {OCR_WORDS}\n\"\"\""
        )
        pending = mock.Mock(**{"ready.return_value": True, "get.side_effect": error})
        pool = mock.Mock(**{"apply_async.return_value": pending})
        runner = ocr.OcrRunner(mock.Mock(), tessdata_dir=tessdata)
        job = ocr.OcrJob(generation=1, page_index=0, key="k", page_pdf=b"")

        with mock.patch.object(runner, "_ensure_pool", return_value=pool):
            status, _payload = runner._read(job)

        self.assertEqual(status, ocr.ERROR)
        log_text = self.written()
        self.assertIn("OCR failed on page 0", log_text)
        self.assertIn('ocr.py", line 280, in recognize', log_text)
        self.assert_no_content(log_text)

    def test_a_bridge_call_that_raised_inside_pywebview(self):
        # pywebview logs `traceback.format_exc()` for any Api method that
        # raises, e.g. a voice context named after what was said.
        logging.getLogger("pywebview").error(
            "Traceback (most recent call last):\n"
            '  File "webview\\util.py", line 254, in _call\n'
            "    result = func(*func_params)\n"
            f"ValueError: unknown voice context: '{SPOKEN_WORDS}'\n"
        )

        log_text = self.written()
        self.assertIn("pywebview", log_text)
        self.assert_no_content(log_text)

    def test_pywebview_logging_an_exception_object_or_text_with_one_in_it(self):
        # How the installed pywebview logs: `logger.exception(e)`, f-strings
        # with the exception in them, and %-args.
        webview_log = logging.getLogger("pywebview")
        try:
            raise ValueError(f"page said {DOCUMENT_WORDS[0]}")
        except ValueError as exc:
            webview_log.exception(exc)
            webview_log.error(f"Error while processing api.find: {exc}")
            webview_log.error("Failed: %s", exc)

        log_text = self.written()
        self.assertIn("Error while processing api.find", log_text)
        self.assert_no_content(log_text)

    def test_a_multi_line_message_cannot_pass_for_the_exception_type(self):
        logging.getLogger("pywebview").error(
            "Traceback (most recent call last):\n"
            '  File "webview\\util.py", line 254, in _call\n'
            "    result = func(*func_params)\n"
            f"ValueError: first line\n{DOCUMENT_WORDS[1]}\n"
            f'  File "fake.py", line 1, in {DOCUMENT_WORDS[0]}x'
        )

        log_text = self.written()
        self.assertIn("ValueError", log_text)
        self.assert_no_content(log_text)

    def test_warnings_and_other_libraries_keep_no_detail(self):
        import warnings

        warnings.warn(f"odd value {DOCUMENT_WORDS[0]}", UserWarning)
        logging.getLogger("somelib").warning("Could not read: %s", DOCUMENT_WORDS[1])

        log_text = self.written()
        self.assertIn("UserWarning", log_text)
        self.assertIn("Could not read", log_text)
        self.assert_no_content(log_text)

    def test_an_error_python_could_only_ignore(self):
        restore = app_log.install_exception_hooks(mock.Mock(), mock.Mock())
        self.addCleanup(restore)

        class Holder:
            def __del__(self):
                raise RuntimeError(DOCUMENT_WORDS[0])

        holder = Holder()
        del holder

        log_text = self.written()
        self.assertIn("An error was ignored", log_text)
        self.assert_no_content(log_text)

    def test_a_background_thread_that_dies(self):
        restore = app_log.install_exception_hooks(mock.Mock(), mock.Mock())
        self.addCleanup(restore)

        def read_page():
            raise RuntimeError(f"page said {DOCUMENT_WORDS[1]}")

        thread = threading.Thread(target=read_page, name="lector-test")
        thread.start()
        thread.join()

        log_text = self.written()
        self.assertIn("A background task (lector-test)", log_text)
        self.assert_no_content(log_text)


class OcrCrashLogTests(unittest.TestCase):
    """A worker that crashes outright leaves its stack in ocr-worker.log."""

    def test_the_worker_writes_its_crash_log_where_it_is_told(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-ocr-crash-")
        self.addCleanup(tmp.cleanup)
        crash_log = Path(tmp.name) / "logs" / "ocr-worker.log"
        runner = ocr.OcrRunner(mock.Mock(), crash_log=crash_log)
        self.addCleanup(runner.shutdown)

        pool = runner._ensure_pool()
        # The initializer has run in the worker once it answers a job. A
        # timeout, so a worker that never starts fails the test, not the run.
        self.assertEqual(pool.apply_async(sum, ([1, 2],)).get(timeout=60), 3)

        self.assertTrue(crash_log.exists())

    def test_no_crash_log_without_a_folder(self):
        runner = ocr.OcrRunner(mock.Mock())
        with mock.patch.object(multiprocessing, "get_context") as get_context:
            runner._ensure_pool()

        self.assertNotIn("initializer", get_context.return_value.Pool.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
