"""Tests for `Api.handle_window_closing` (Milestone 8.1).

`unittest` from the standard library, matching the rest of the suite. Run with:

    python -m unittest discover -s tests

`Api()` is exercised for real rather than through a mock, since the point of
these tests is the interaction between it, a dirty `PdfDocument`, and
pywebview's `window.events.closing` contract (a `False` return cancels the
close). `webview.windows[0]` and the frontend's `promptSaveIfDirty` are the
two things that only exist once a real window is up, so those are the only
seams mocked.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402


class WindowClosingTests(unittest.TestCase):
    def setUp(self):
        # A fresh, temporary settings.json: `Api()` reads the wake-phrase
        # flag on construction, and a test run must never pick up — or
        # perturb — the developer's own settings.
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)

        self.window = mock.Mock()
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_dirty(self):
        self.api._doc = mock.Mock(is_open=True, is_dirty=True)

    def _make_clean(self):
        self.api._doc = mock.Mock(is_open=True, is_dirty=False)

    def test_no_document_open_closes_immediately(self):
        result = self.api.handle_window_closing()

        self.assertIsNone(result)
        self.window.evaluate_js.assert_not_called()

    def test_open_but_clean_document_closes_immediately(self):
        self._make_clean()

        result = self.api.handle_window_closing()

        self.assertIsNone(result)
        self.window.evaluate_js.assert_not_called()

    def test_dirty_document_cancels_the_first_attempt(self):
        self._make_dirty()

        result = self.api.handle_window_closing()

        # pywebview reads a literal False as "cancel this close".
        self.assertFalse(result)
        self.window.evaluate_js.assert_called_once()
        script, kwargs = self.window.evaluate_js.call_args
        self.assertIn("promptSaveIfDirty", script[0])
        self.assertTrue(callable(kwargs["callback"]))
        # Deferred, not lost: the prompt is in flight, not abandoned.
        self.window.destroy.assert_not_called()

    def test_confirmed_save_or_discard_reissues_the_close(self):
        self._make_dirty()
        self.api.handle_window_closing()
        callback = self.window.evaluate_js.call_args.kwargs["callback"]

        callback(True)

        self.window.destroy.assert_called_once()

    def test_cancelling_the_prompt_leaves_the_window_open(self):
        self._make_dirty()
        self.api.handle_window_closing()
        callback = self.window.evaluate_js.call_args.kwargs["callback"]

        callback(False)

        self.window.destroy.assert_not_called()

    def test_the_confirmed_reissue_does_not_prompt_again(self):
        # The `window.destroy()` above fires `closing` a second time —
        # without `_closing_confirmed`, this would show the prompt twice.
        self._make_dirty()
        self.api.handle_window_closing()
        callback = self.window.evaluate_js.call_args.kwargs["callback"]
        callback(True)
        self.window.evaluate_js.reset_mock()

        result = self.api.handle_window_closing()

        self.assertIsNone(result)
        self.window.evaluate_js.assert_not_called()

    def test_shuts_down_voice_when_closing_immediately(self):
        with mock.patch.object(self.api, "shutdown_voice") as shutdown:
            self.api.handle_window_closing()

        shutdown.assert_called_once()

    def test_does_not_shut_down_voice_while_the_prompt_is_pending(self):
        self._make_dirty()

        with mock.patch.object(self.api, "shutdown_voice") as shutdown:
            self.api.handle_window_closing()

        shutdown.assert_not_called()

    def test_shuts_down_voice_once_the_close_is_confirmed(self):
        self._make_dirty()
        self.api.handle_window_closing()
        callback = self.window.evaluate_js.call_args.kwargs["callback"]

        with mock.patch.object(self.api, "shutdown_voice") as shutdown:
            callback(True)

        shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
