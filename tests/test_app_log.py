"""Tests for the log file and the uncaught-exception hooks (hardening H3).

`unittest`, matching the rest of the suite. Every test that calls
`app_log.setup` takes its handler off the root logger again, so nothing here
leaks a log file into other tests.
"""
import logging
import sys
import tempfile
import threading
import unittest
from multiprocessing.pool import RemoteTraceback
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.shared import app_log  # noqa: E402

SECRET = "quokkazephyr"


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:
        return caught


class LogFileTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-logs-")
        self.addCleanup(tmp.cleanup)
        self.log_dir = Path(tmp.name) / "logs"
        app_logger = logging.getLogger("lector")
        level = app_logger.level
        self.addCleanup(app_logger.setLevel, level)
        self.addCleanup(logging.captureWarnings, False)

    def start(self) -> logging.Handler:
        handler = app_log.setup(self.log_dir)
        self.assertIsNotNone(handler)
        self.addCleanup(handler.close)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        return handler

    def written(self) -> str:
        for handler in logging.getLogger().handlers:
            handler.flush()
        path = self.log_dir / app_log.LOG_FILE
        return path.read_text(encoding="utf-8") if path.exists() else ""


class SetupTests(LogFileTestCase):
    def test_lector_records_from_info_up_go_to_the_file(self):
        self.start()

        logging.getLogger("lector.features.example").info("opened page %d", 3)

        self.assertIn("INFO lector.features.example", self.written())
        self.assertIn("opened page 3", self.written())

    def test_other_libraries_only_from_warning_up(self):
        self.start()

        logging.getLogger("somelib").info("chatter")
        logging.getLogger("somelib").warning("a real problem")

        self.assertNotIn("chatter", self.written())
        self.assertIn("a real problem", self.written())

    def test_the_file_rotates_and_keeps_a_few_old_ones(self):
        handler = self.start()
        self.assertEqual(handler.maxBytes, app_log.MAX_BYTES)
        self.assertEqual(handler.backupCount, app_log.BACKUP_COUNT)
        handler.maxBytes = 300  # so the test doesn't write megabytes

        for i in range(200):
            logging.getLogger("lector.test").warning("line %d of filler text", i)

        names = sorted(p.name for p in self.log_dir.iterdir())
        self.assertIn("lector.log.1", names)
        self.assertIn(f"lector.log.{app_log.BACKUP_COUNT}", names)
        self.assertNotIn(f"lector.log.{app_log.BACKUP_COUNT + 1}", names)

    def test_a_rollover_that_fails_keeps_writing(self):
        # Windows refuses the rename while another program has the log open.
        handler = self.start()
        handler.maxBytes = 300

        with mock.patch.object(
            logging.handlers.RotatingFileHandler, "doRollover", side_effect=PermissionError
        ):
            for i in range(50):
                logging.getLogger("lector.test").warning("line %d of filler text", i)

        self.assertIn("line 49 of filler text", self.written())

    def test_a_folder_that_cannot_be_made_is_not_a_crash(self):
        blocker = self.log_dir.parent / "not-a-folder"
        blocker.write_text("x")

        self.assertIsNone(app_log.setup(blocker / "logs"))


class ContentFreeFormatterTests(unittest.TestCase):
    def setUp(self):
        self.formatter = app_log.ContentFreeFormatter(app_log.FORMAT)

    def record(self, name="lector.test", msg="it failed", args=None, exc=None):
        exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
        return logging.LogRecord(name, logging.ERROR, __file__, 1, msg, args, exc_info)

    def test_an_exception_keeps_its_type_and_frames_but_not_its_message(self):
        exc = _raised(ValueError(f"page text {SECRET}"))

        text = self.formatter.format(self.record(exc=exc))

        self.assertNotIn(SECRET, text)
        self.assertIn("ValueError", text)
        self.assertIn(app_log.WITHHELD, text)
        self.assertIn("in _raised", text)

    def test_a_chained_cause_is_withheld_too(self):
        try:
            try:
                raise OSError(f"C:\\Users\\me\\{SECRET}.pdf")
            except OSError as inner:
                raise RuntimeError(SECRET) from inner
        except RuntimeError as outer:
            exc = outer

        text = self.formatter.format(self.record(exc=exc))

        self.assertNotIn(SECRET, text)
        self.assertIn("OSError", text)
        self.assertIn("direct cause", text)

    def test_the_ocr_workers_traceback_keeps_its_frames_only(self):
        remote = RemoteTraceback(
            '\n"""\nTraceback (most recent call last):\n'
            '  File "C:\\lector\\ocr.py", line 270, in recognize\n'
            f"    text = '{SECRET}'\nValueError: {SECRET}\n\"\"\""
        )
        exc = ValueError(SECRET)
        exc.__cause__ = remote

        text = self.formatter.format(self.record(exc=exc))

        self.assertNotIn(SECRET, text)
        self.assertIn('ocr.py", line 270, in recognize', text)

    def test_an_exception_passed_as_an_argument_is_withheld(self):
        text = self.formatter.format(
            self.record(msg="Could not do it: %s", args=(OSError(SECRET),))
        )

        self.assertNotIn(SECRET, text)
        self.assertIn("OSError", text)

    def test_the_record_other_handlers_see_is_left_alone(self):
        record = self.record(msg="%s", args=(OSError(SECRET),))

        self.formatter.format(record)

        self.assertIn(SECRET, record.getMessage())

    def test_a_pywebview_bridge_traceback_is_cut_to_frames_and_type(self):
        message = (
            "Traceback (most recent call last):\n"
            '  File "webview\\util.py", line 254, in _call\n'
            "    result = func(*func_params)\n"
            f"ValueError: unknown voice context: '{SECRET}'\n"
        )

        text = self.formatter.format(self.record(name="pywebview", msg=message))

        self.assertNotIn(SECRET, text)
        self.assertNotIn("func_params", text)
        self.assertIn('util.py", line 254, in _call', text)
        self.assertIn("ValueError", text)

    def test_an_exception_group_lists_each_inner_exception_without_messages(self):
        exc = _raised(ExceptionGroup("several", [ValueError(SECRET), OSError(SECRET)]))

        text = self.formatter.format(self.record(exc=exc))

        self.assertNotIn(SECRET, text)
        self.assertIn("Sub-exception 1", text)
        self.assertIn("OSError", text)

    def test_the_traceback_given_is_the_one_printed(self):
        first = _raised(ValueError(SECRET))
        record = self.record(exc=first)
        record.exc_info = (ValueError, first, None)

        text = self.formatter.format(record)

        self.assertNotIn("in _raised", text)

    def test_a_value_a_page_returned_to_pywebview_is_withheld(self):
        message = f"Async function executed and callback is not callable. Returned value {SECRET}"

        text = self.formatter.format(self.record(name="pywebview", msg=message))

        self.assertNotIn(SECRET, text)
        self.assertIn("callback is not callable", text)


class ExceptionHookTests(unittest.TestCase):
    def setUp(self):
        self.previous_sys = mock.Mock()
        self.previous_thread = mock.Mock()
        for target, name, value in (
            (sys, "excepthook", self.previous_sys),
            (threading, "excepthook", self.previous_thread),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.on_background = mock.Mock()
        self.on_fatal = mock.Mock()
        self.restore = app_log.install_exception_hooks(self.on_background, self.on_fatal)

    def test_a_background_thread_error_is_logged_and_the_page_told(self):
        def boom():
            raise RuntimeError(SECRET)

        with self.assertLogs("lector.shared.app_log", level="ERROR") as logs:
            thread = threading.Thread(target=boom, name="lector-test-thread")
            thread.start()
            thread.join()

        self.assertIn("lector-test-thread", logs.output[0])
        self.on_background.assert_called_once_with()
        self.on_fatal.assert_not_called()

    def test_a_thread_ending_with_system_exit_is_not_reported(self):
        thread = threading.Thread(target=sys.exit)
        thread.start()
        thread.join()

        self.on_background.assert_not_called()

    def test_a_main_thread_error_is_logged_then_shown_then_passed_on(self):
        exc = _raised(RuntimeError(SECRET))

        with self.assertLogs("lector.shared.app_log", level="CRITICAL"):
            sys.excepthook(type(exc), exc, exc.__traceback__)

        self.on_fatal.assert_called_once_with()
        self.previous_sys.assert_called_once()

    def test_ctrl_c_is_left_to_python(self):
        exc = _raised(KeyboardInterrupt())

        sys.excepthook(type(exc), exc, exc.__traceback__)

        self.on_fatal.assert_not_called()
        self.previous_sys.assert_called_once()

    def test_a_failing_callback_is_logged_not_raised(self):
        self.on_background.side_effect = RuntimeError("the page is gone")

        def boom():
            raise RuntimeError(SECRET)

        with self.assertLogs("lector.shared.app_log", level="WARNING") as logs:
            thread = threading.Thread(target=boom)
            thread.start()
            thread.join()

        self.assertTrue(any("Could not tell the reader" in line for line in logs.output))

    def test_restore_puts_the_previous_hooks_back(self):
        self.restore()

        self.assertIs(sys.excepthook, self.previous_sys)
        self.assertIs(threading.excepthook, self.previous_thread)


class FatalMessageTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "the message box is Windows-only")
    def test_windows_shows_a_message_box_with_the_plain_sentence(self):
        with mock.patch("ctypes.windll.user32.MessageBoxW") as box:
            app_log.show_fatal_message()

        args = box.call_args[0]
        self.assertEqual(args[1], app_log.FATAL_MESSAGE)
        self.assertEqual(args[2], app_log.FATAL_TITLE)

    def test_elsewhere_it_does_nothing(self):
        with mock.patch.object(app_log.sys, "platform", "darwin"):
            app_log.show_fatal_message()  # must not raise


if __name__ == "__main__":
    unittest.main()
