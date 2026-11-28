"""Closing the window with unsaved changes must not freeze it.

The closing handler runs on the window's own thread, and pywebview's
`evaluate_js` waits there for the script's result even with a callback;
the result is delivered on that same thread, so asking from it never
returns (found while checking the unsaved-changes indicator in the running
app: X, Alt+F4 and "close app" all froze the window). The question is now
asked from another thread and the handler returns at once, cancelling the
close (`bridge/window.py`'s `ask_off_the_window_thread`).
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402

# Far longer than the handler may take; the script "runs" until released.
BLOCK_S = 5


class CloseQuestionThreadTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(store, "_settings_path", lambda: Path(self.dir.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.release = threading.Event()
        self.asked = threading.Event()
        self.asked_on = []

        def evaluate_js(script, callback=None):
            # Like pywebview's: only returns once the page has answered.
            self.asked_on.append((script, threading.current_thread()))
            self.asked.set()
            self.release.wait(BLOCK_S)
            callback(False)

        self.window = mock.Mock(**{"evaluate_js.side_effect": evaluate_js})
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.release.set)

    def close_with(self, doc, *, job_running=False):
        self.api._doc = doc
        with mock.patch.object(self.api._jobs, "is_running", return_value=job_running):
            started = time.monotonic()
            result = self.api.handle_window_closing()
            took = time.monotonic() - started
        return result, took

    def test_unsaved_changes_ask_from_another_thread_and_the_close_waits(self):
        result, took = self.close_with(mock.Mock(is_open=True, is_dirty=True))
        self.assertIs(result, False)
        self.assertLess(took, 1)  # never waits for the page
        self.assertTrue(self.asked.wait(2))
        script, thread = self.asked_on[0]
        self.assertEqual(script, "promptSaveIfDirty(true)")
        self.assertIsNot(thread, threading.current_thread())

    def test_a_running_job_asks_from_another_thread_too(self):
        result, took = self.close_with(mock.Mock(is_open=True, is_dirty=False), job_running=True)
        self.assertIs(result, False)
        self.assertLess(took, 1)
        self.assertTrue(self.asked.wait(2))
        script, thread = self.asked_on[0]
        self.assertEqual(script, "confirmStopFileJob()")
        self.assertIsNot(thread, threading.current_thread())

    def test_nothing_unsaved_closes_without_asking(self):
        result, _ = self.close_with(mock.Mock(is_open=True, is_dirty=False))
        self.assertIsNone(result)
        self.window.evaluate_js.assert_not_called()


if __name__ == "__main__":
    unittest.main()
