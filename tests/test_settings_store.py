"""Tests for the persisted voice-activation flags (Milestone 8).

Every test here writes to a temporary settings file rather than the real one
in %APPDATA%: a test run must never be able to change how the developer's own
copy of Lector starts listening.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.settings import store  # noqa: E402


class VoiceActivationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        self.path = Path(self.dir.name) / "settings.json"
        patcher = mock.patch.object(store, "_settings_path", lambda: self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_defaults_to_push_to_talk_only(self):
        # A fresh install must not be listening until the reader asks it to.
        self.assertEqual(
            store.get_voice_activation(),
            {"push_to_talk": True, "wake_phrase": False},
        )

    def test_both_modes_can_be_on_at_once(self):
        # docs/PRD.md: the two are independent, not alternatives.
        store.set_voice_activation(push_to_talk=True, wake_phrase=True)

        self.assertEqual(
            store.get_voice_activation(),
            {"push_to_talk": True, "wake_phrase": True},
        )

    def test_both_modes_can_be_off(self):
        # "Voice off entirely" is a supported choice, not an error state: the
        # app stays fully usable by mouse and keyboard.
        store.set_voice_activation(push_to_talk=False, wake_phrase=False)

        self.assertEqual(
            store.get_voice_activation(),
            {"push_to_talk": False, "wake_phrase": False},
        )

    def test_the_wake_phrase_can_be_the_only_mode(self):
        store.set_voice_activation(push_to_talk=False, wake_phrase=True)

        self.assertEqual(
            store.get_voice_activation(),
            {"push_to_talk": False, "wake_phrase": True},
        )

    def test_the_choice_survives_a_restart(self):
        store.set_voice_activation(push_to_talk=False, wake_phrase=True)

        stored = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertIs(stored["push_to_talk_enabled"], False)
        self.assertIs(stored["wake_phrase_enabled"], True)

    def test_other_settings_are_left_alone(self):
        store.set_theme("sepia")

        store.set_voice_activation(push_to_talk=True, wake_phrase=True)

        self.assertEqual(store.get_theme(), "sepia")

    def test_a_hand_edited_value_falls_back_to_the_default(self):
        # settings.json is a plain file a reader can open, so it cannot be
        # assumed well-formed. A nonsense value must not switch the
        # microphone on.
        self._write({"wake_phrase_enabled": "yes please"})

        self.assertFalse(store.get_voice_activation()["wake_phrase"])

    def test_a_missing_file_is_not_an_error(self):
        self.assertFalse(self.path.exists())

        self.assertTrue(store.get_voice_activation()["push_to_talk"])


if __name__ == "__main__":
    unittest.main()
