"""Integration tests for Milestone 8.1-8.3, exercised together through the
real `Api` object.

`docs/TASKS.md` marks 8.1 (wire app-close to the unsaved-changes check), 8.2
(VAD/gain/noise audio front-end), and 8.3 (confidence-based clarification)
as independent of each other and of the router 8.4 introduces. "Independent"
is a claim about *dependency order*, not about whether they ever run in the
same process — all three sit on the same `Api` instance and the same
`VoiceEngine`, so what these tests check is that wiring one did not disturb
another: a dirty document's close-prompt still fires with the voice engine
attached (8.1), and a near-miss recognition result still reaches the
frontend as a `clarify` payload through the exact bridge method 8.1 also
uses to reach `webview.windows[0]` (8.2's audio pipeline feeding 8.3's
resolution).

Follows `test_api.py`'s pattern: `Api()` is real, and only the two seams that
require an actual window (`webview.windows`) or an actual settings file on
disk are mocked.
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


class VoicePipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Same isolation `test_api.py` uses: a real settings.json a test run
        # must not read from or write into the developer's own.
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
        """The `detail` object from the one `lector:voice` CustomEvent the
        mocked window's `evaluate_js` was called with."""
        self.window.evaluate_js.assert_called_once()
        script = self.window.evaluate_js.call_args[0][0]
        # `_on_voice_result` builds this call as an f-string wrapping the
        # JSON payload after `detail: `; `raw_decode` stops at the payload's
        # own closing brace rather than trying to parse the trailing
        # `}))` that closes the surrounding JS call as more JSON.
        start = script.index("detail: ") + len("detail: ")
        payload, _ = json.JSONDecoder().raw_decode(script[start:])
        return payload

    def test_a_near_miss_final_result_reaches_the_bridge_as_a_clarify_payload(self):
        # As `engine._emit` would produce it for a push-to-talk hold (8.2's
        # audio front-end) that closed on a misheard "next page".
        self.api._on_voice_result(
            {"text": "nest pah", "final": True, "wake": False, "alternatives": []}
        )

        detail = self._dispatched_detail()
        self.assertIsNone(detail["command"])
        self.assertEqual(detail["clarify"]["intent"], "NEXT_PAGE")
        self.assertEqual(detail["clarify"]["matched"], "next page")

    def test_a_confident_nbest_alternative_reaches_the_bridge_as_a_command(self):
        # Vosk's top guess missed, but an n-best alternative (8.2's
        # SetMaxAlternatives capture) is an exact phrasing.
        self.api._on_voice_result(
            {
                "text": "nest pah",
                "final": True,
                "wake": False,
                "alternatives": ["next page"],
            }
        )

        detail = self._dispatched_detail()
        self.assertEqual(detail["command"]["intent"], "NEXT_PAGE")
        self.assertIsNone(detail["clarify"])

    def test_a_partial_result_is_dispatched_without_resolving_a_command(self):
        # Still mid-utterance; resolving early would risk clarifying (or
        # acting on) words the reader has not finished saying.
        self.api._on_voice_result(
            {"text": "next", "final": False, "wake": False, "alternatives": []}
        )

        detail = self._dispatched_detail()
        self.assertNotIn("command", detail)
        self.assertNotIn("clarify", detail)

    def test_window_closing_still_prompts_with_the_voice_engine_attached(self):
        # 8.1's close-prompt path must be unaffected by the voice engine
        # (8.2/8.3) sharing the same Api instance and the same window mock.
        self.api._doc = mock.Mock(is_open=True, is_dirty=True)

        result = self.api.handle_window_closing()

        self.assertFalse(result)
        self.window.evaluate_js.assert_called_once()
        self.assertIn("promptSaveIfDirty", self.window.evaluate_js.call_args[0][0])

    def test_shutdown_voice_is_safe_after_a_clarification_was_dispatched(self):
        self.api._on_voice_result(
            {"text": "nest pah", "final": True, "wake": False, "alternatives": []}
        )

        self.api.shutdown_voice()  # must not raise


if __name__ == "__main__":
    unittest.main()
