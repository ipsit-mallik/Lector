"""Integration tests for Milestone 8.7, exercised through the real `Api`.

Where `test_voice_router.py` checks `router.resolve` in isolation and
`test_dialog_browser.py` / `test_api.py`'s `BrowseAndSaveDialogTests` check
`browser.py` and `Api`'s bridge methods each on their own, this checks the
whole dependency group wired together the way the frontend's `file-browser.js`
actually drives it: `set_voice_context(OPEN_DIALOG/SAVE_DIALOG)` changes what
`_on_voice_result` resolves, a real temporary directory is listed through
`Api.browse_directory` with no mocking of `dialog_browser`, and closing the
dialog restores the grammar the screen underneath it was using.

Follows the same pattern as `test_milestone_8_4_integration.py`: `Api()` is
real, and only the two seams that need an actual window or an actual settings
file on disk are mocked.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import router  # noqa: E402


class DialogVoiceContextIntegrationTests(unittest.TestCase):
    def setUp(self):
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

    def _dispatched_detail(self) -> dict:
        self.window.evaluate_js.assert_called_once()
        script = self.window.evaluate_js.call_args[0][0]
        start = script.index("detail: ") + len("detail: ")
        payload, _ = json.JSONDecoder().raw_decode(script[start:])
        return payload

    def _speak(self, text: str) -> dict:
        self.api._on_voice_result(
            {"text": text, "final": True, "wake": False, "alternatives": []}
        )
        return self._dispatched_detail()

    def test_a_spoken_number_picks_a_row_while_the_open_dialog_is_active(self):
        self.api.set_voice_context(router.OPEN_DIALOG)

        detail = self._speak("three")

        self.assertEqual(detail["command"]["intent"], router.DIALOG_PICK)
        self.assertEqual(detail["command"]["index"], 3)

    def test_go_up_resolves_while_the_open_dialog_is_active(self):
        self.api.set_voice_context(router.OPEN_DIALOG)

        detail = self._speak("go up")

        self.assertEqual(detail["command"]["intent"], router.DIALOG_UP)

    def test_save_here_does_not_resolve_in_the_open_dialog(self):
        # Confirming files never accepted this phrase; only Save-As does.
        self.api.set_voice_context(router.OPEN_DIALOG)

        detail = self._speak("save here")

        self.assertIsNone(detail["command"])

    def test_save_here_resolves_while_the_save_dialog_is_active(self):
        self.api.set_voice_context(router.SAVE_DIALOG)

        detail = self._speak("save here")

        self.assertEqual(detail["command"]["intent"], router.DIALOG_CONFIRM)

    def test_cancel_resolves_while_the_save_dialog_is_active(self):
        self.api.set_voice_context(router.SAVE_DIALOG)

        detail = self._speak("cancel")

        self.assertEqual(detail["command"]["intent"], router.CANCEL)

    def test_closing_the_dialog_restores_the_screen_it_was_opened_over(self):
        self.api.set_voice_context(router.HOME)
        self.api.set_voice_context(router.OPEN_DIALOG)

        # The dialog closes (Cancel/pick/confirm all end with this, per
        # `file-browser.js`'s `closeWith`): the resting screen's own grammar
        # comes back, so a dialog-only phrase stops resolving.
        self.api.set_voice_context(router.HOME)
        self.window.evaluate_js.reset_mock()

        detail = self._speak("go up")

        self.assertIsNone(detail["command"])


class BrowseDirectoryThroughApiIntegrationTests(unittest.TestCase):
    """`Api.browse_directory`/`get_save_dialog_start` against a real
    temporary directory tree — `dialog_browser` itself is not mocked here,
    unlike `test_api.py`'s bridge-only unit tests, to prove the two modules
    are wired together correctly end to end."""

    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.settings_dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.settings_dir.cleanup)

        self.tree_dir = tempfile.TemporaryDirectory(prefix="lector-tree-")
        self.addCleanup(self.tree_dir.cleanup)
        self.root = Path(self.tree_dir.name)
        (self.root / "Reports").mkdir()
        (self.root / "notes.txt").touch()
        (self.root / "report.pdf").touch()

        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)

    def test_browse_directory_lists_only_folders_and_pdfs_from_a_real_path(self):
        result = self.api.browse_directory(str(self.root))

        names = [entry["name"] for entry in result["entries"]]
        self.assertEqual(names, ["Reports", "report.pdf"])

    def test_get_save_dialog_start_points_at_the_open_documents_own_folder(self):
        self.api._doc = mock.Mock(path=str(self.root / "report.pdf"), is_open=False)

        result = self.api.get_save_dialog_start()

        self.assertEqual(result["dir"], str(self.root))
        self.assertEqual(result["filename"], "report (highlighted).pdf")


if __name__ == "__main__":
    unittest.main()
