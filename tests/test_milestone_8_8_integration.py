"""Integration tests for Milestone 8.8, exercised through the real `Api`.

Where `test_voice_router.py` checks `router.resolve`'s `DICTATION` branch in
isolation and `test_voice_engine.py` checks `VoiceEngine.set_vocabulary(None)`
in isolation, this checks the whole dependency group wired together the way
`command-reference.js` actually drives it: `set_voice_context("dictation")`
must put the recognizer into open-vocabulary mode (no grammar), a dictated
sentence must come back through `_on_voice_result` as plain text with no
command attached, "done"/"cancel" must still resolve to their intents while
dictating, and leaving dictation must restore the reading grammar.

Follows the same pattern as `test_milestone_8_4_integration.py` and
`test_milestone_8_7_integration.py`: `Api()` is real, and only the two seams
that need an actual window or an actual settings file on disk are mocked.
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


class DictationVoiceContextIntegrationTests(unittest.TestCase):
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

    def test_entering_dictation_puts_the_recognizer_into_open_vocabulary_mode(self):
        self.api.set_voice_context(router.DICTATION)

        self.assertIsNone(self.api._voice._vocabulary)

    def test_start_search_resolves_while_reading(self):
        detail = self._speak("start search")

        self.assertEqual(detail["command"]["intent"], router.START_DICTATION)

    def test_dictated_text_reaches_the_caller_as_plain_text_with_no_command(self):
        self.api.set_voice_context(router.DICTATION)
        self.window.evaluate_js.reset_mock()

        detail = self._speak("the quick brown fox")

        self.assertIsNone(detail["command"])
        self.assertEqual(detail["text"], "the quick brown fox")

    def test_done_resolves_to_stop_dictation_while_dictating(self):
        self.api.set_voice_context(router.DICTATION)
        self.window.evaluate_js.reset_mock()

        detail = self._speak("done")

        self.assertEqual(detail["command"]["intent"], router.STOP_DICTATION)

    def test_cancel_resolves_while_dictating(self):
        self.api.set_voice_context(router.DICTATION)
        self.window.evaluate_js.reset_mock()

        detail = self._speak("cancel")

        self.assertEqual(detail["command"]["intent"], router.CANCEL)

    def test_a_global_command_word_is_not_hijacked_while_dictating(self):
        self.api.set_voice_context(router.DICTATION)
        self.window.evaluate_js.reset_mock()

        detail = self._speak("please undo what I just said")

        self.assertIsNone(detail["command"])
        self.assertEqual(detail["text"], "please undo what I just said")

    def test_leaving_dictation_restores_the_reading_grammar(self):
        self.api.set_voice_context(router.DICTATION)
        self.api.set_voice_context(router.READING)
        self.window.evaluate_js.reset_mock()

        detail = self._speak("next page")

        self.assertEqual(detail["command"]["intent"], "NEXT_PAGE")


if __name__ == "__main__":
    unittest.main()
