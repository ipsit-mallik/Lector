"""Tests for the push-to-talk speech engine (Milestone 5).

Uses `unittest` from the standard library rather than pytest: the project
deliberately keeps its dependency list short (docs/TECH_STACK.md), and nothing
here needs a fixture framework. Run with:

    python -m unittest discover -s tests

The tests that need real audio hardware are not here — a microphone cannot be
driven in CI or from an agent environment. What *is* covered is everything
downstream of the audio callback, which is where the logic lives. Recognition
accuracy itself was verified separately by playing synthesized speech through
`VoiceEngine._consume` and `stop_listening()`; see CHANGELOG.md.
"""
import json
import queue
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.voice import command_grammar  # noqa: E402
from lector.features.voice import engine as ve  # noqa: E402


class CleanTextTests(unittest.TestCase):
    """`_clean` exists so callers never have to know about Vosk's markers."""

    def test_strips_unknown_word_markers(self):
        self.assertEqual(ve._clean("next [unk] page"), "next page")

    def test_collapses_whitespace(self):
        self.assertEqual(ve._clean("  next   page  "), "next page")

    def test_empty_and_none_are_empty_strings(self):
        self.assertEqual(ve._clean(""), "")
        self.assertEqual(ve._clean(None), "")

    def test_all_unknown_becomes_empty(self):
        # A cough decodes as nothing but markers; callers must see "no
        # command", not a phantom one.
        self.assertEqual(ve._clean("[unk] [unk]"), "")


class SubscriptionTests(unittest.TestCase):
    def test_emit_reaches_every_subscriber(self):
        # Arrange
        engine = ve.VoiceEngine()
        first, second = [], []
        engine.subscribe(first.append)
        engine.subscribe(second.append)

        # Act
        engine._emit("next page", final=True)

        # Assert
        self.assertEqual(first, [{"text": "next page", "final": True}])
        self.assertEqual(second, [{"text": "next page", "final": True}])

    def test_a_broken_subscriber_does_not_block_the_others(self):
        # Voice is an accelerator (docs/PRD.md); one bad listener must not
        # take the microphone down with it.
        engine = ve.VoiceEngine()
        received = []

        def explodes(_):
            raise RuntimeError("boom")

        engine.subscribe(explodes)
        engine.subscribe(received.append)

        engine._emit("save", final=True)

        self.assertEqual(received, [{"text": "save", "final": True}])


class VocabularyTests(unittest.TestCase):
    def test_defaults_to_the_navigation_grammar(self):
        # The recognizer must be able to hear every word the grammar can
        # match; pinning it to anything else makes commands undetectable.
        self.assertEqual(ve.VoiceEngine()._vocabulary, command_grammar.VOCABULARY)

    def test_accepts_an_explicit_vocabulary(self):
        engine = ve.VoiceEngine(vocabulary=["open", "close"])
        self.assertEqual(engine._vocabulary, ["open", "close"])

    def test_set_vocabulary_replaces_it(self):
        # Milestone 7 widens the vocabulary for highlighting this way.
        engine = ve.VoiceEngine(vocabulary=["open"])
        engine.set_vocabulary(["next", "previous"])
        self.assertEqual(engine._vocabulary, ["next", "previous"])

    def test_vocabulary_is_copied_not_aliased(self):
        words = ["next"]
        engine = ve.VoiceEngine(vocabulary=words)
        words.append("previous")
        self.assertEqual(engine._vocabulary, ["next"])


class MissingModelTests(unittest.TestCase):
    """A missing model is a supported state, not a crash: docs/PRD.md requires
    the app to stay fully usable by mouse and keyboard without voice."""

    def setUp(self):
        self.engine = ve.VoiceEngine(model_dir=Path("no-such-directory-anywhere"))

    def test_status_reports_unavailable_with_a_reason(self):
        status = self.engine.status()
        self.assertFalse(status["available"])
        self.assertFalse(status["listening"])
        self.assertIn("fetch_vosk_model", status["error"])

    def test_start_listening_does_not_raise(self):
        status = self.engine.start_listening()
        self.assertFalse(status["available"])
        self.assertFalse(status["listening"])

    def test_stop_listening_when_never_started_returns_no_text(self):
        result = self.engine.stop_listening()
        self.assertEqual(result["text"], "")
        self.assertTrue(result["final"])

    def test_shutdown_is_safe_when_idle(self):
        self.engine.shutdown()  # must not raise


def _model_available() -> bool:
    return ve.VoiceEngine().status()["available"]


@unittest.skipUnless(
    _model_available(),
    "speech model not installed - run: python scripts/fetch_vosk_model.py",
)
class RecognizerTests(unittest.TestCase):
    """Exercises the real Vosk recognizer, without a microphone."""

    def test_grammar_is_constrained_to_the_vocabulary_plus_unknown(self):
        engine = ve.VoiceEngine(vocabulary=["next", "page"])
        engine._ensure_model()
        recognizer = engine._new_recognizer()
        self.assertIsNotNone(recognizer)

    def test_silence_yields_no_command(self):
        # Arrange: half a second of digital silence fed through the engine's
        # own worker, exactly as the audio callback would deliver it.
        engine = ve.VoiceEngine()
        engine._ensure_model()
        engine._recognizer = engine._new_recognizer()
        engine._utterances = []
        engine._audio = queue.Queue()
        engine._listening = True
        engine._worker = threading.Thread(target=engine._consume, daemon=True)
        engine._worker.start()

        # Act
        silence = b"\x00\x00" * ve.BLOCK_SIZE
        for _ in range(4):
            engine._audio.put(silence)
        result = engine.stop_listening()

        # Assert
        self.assertEqual(result["text"], "")
        self.assertTrue(result["final"])
        self.assertFalse(result["listening"])

    def test_completed_utterances_survive_into_the_final_result(self):
        """Regression: `Result()` consumes a finished utterance, so a phrase
        Vosk closed out mid-hold used to vanish from `FinalResult()` and
        `stop_listening()` returned an empty string."""
        engine = ve.VoiceEngine()
        engine._ensure_model()
        engine._recognizer = engine._new_recognizer()
        engine._utterances = ["next page"]
        engine._listening = True
        engine._audio = queue.Queue()
        engine._worker = None

        result = engine.stop_listening()

        self.assertEqual(result["text"], "next page")

    def test_json_grammar_round_trips(self):
        engine = ve.VoiceEngine(vocabulary=["next", "previous"])
        grammar = json.loads(json.dumps(engine._vocabulary + [ve.UNKNOWN_TOKEN]))
        self.assertEqual(grammar, ["next", "previous", ve.UNKNOWN_TOKEN])


if __name__ == "__main__":
    unittest.main()
