"""Integration tests for Milestone 8.4, exercised through the real `Api`.

Where `test_voice_router.py` checks `router.resolve` and `router.vocabulary_for`
in isolation, this checks that `Api` actually wires them in: that
`set_voice_context` changes what the recognizer's vocabulary would be *and*
what `_on_voice_result` will resolve, and that a page which never calls
`set_voice_context` at all — every pre-8.4 caller, including
`test_milestone_8_1_to_8_3_integration.py` — keeps behaving exactly as it did
before this milestone.

Follows the same pattern as that file: `Api()` is real, and only the two
seams that need an actual window or an actual settings file on disk are
mocked.
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


class VoiceContextIntegrationTests(unittest.TestCase):
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

    def test_a_fresh_api_defaults_to_the_reading_context(self):
        self.assertEqual(self.api._voice_context, router.DEFAULT_CONTEXT)

    def test_set_voice_context_reports_back_what_it_applied(self):
        result = self.api.set_voice_context(router.HOME)
        self.assertEqual(result, {"context": router.HOME})
        self.assertEqual(self.api._voice_context, router.HOME)

    def test_set_voice_context_rejects_an_unknown_context(self):
        with self.assertRaises(ValueError):
            self.api.set_voice_context("not_a_real_context")

    def test_a_global_command_resolves_after_switching_to_home(self):
        self.api.set_voice_context(router.HOME)

        self.api._on_voice_result(
            {"text": "go home", "final": True, "wake": False, "alternatives": []}
        )

        detail = self._dispatched_detail()
        self.assertEqual(detail["command"]["intent"], router.GO_HOME)
        self.assertIsNone(detail["clarify"])

    def test_a_reading_only_phrase_does_not_resolve_once_context_is_home(self):
        self.api.set_voice_context(router.HOME)

        self.api._on_voice_result(
            {"text": "next page", "final": True, "wake": False, "alternatives": []}
        )

        detail = self._dispatched_detail()
        self.assertIsNone(detail["command"])
        self.assertIsNone(detail["clarify"])

    def test_switching_back_to_reading_restores_the_reading_grammar(self):
        self.api.set_voice_context(router.HOME)
        self.api.set_voice_context(router.READING)

        self.api._on_voice_result(
            {"text": "next page", "final": True, "wake": False, "alternatives": []}
        )

        detail = self._dispatched_detail()
        self.assertEqual(detail["command"]["intent"], "NEXT_PAGE")

    def test_a_page_that_never_sets_a_context_still_resolves_reading_commands(self):
        # No `set_voice_context` call at all — the pre-8.4 calling
        # convention every existing page and test used, and the one
        # `test_milestone_8_1_to_8_3_integration.py` still relies on.
        self.api._on_voice_result(
            {"text": "next page", "final": True, "wake": False, "alternatives": []}
        )

        detail = self._dispatched_detail()
        self.assertEqual(detail["command"]["intent"], "NEXT_PAGE")


if __name__ == "__main__":
    unittest.main()
