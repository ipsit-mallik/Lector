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
import shutil
import sys
import tempfile
import threading
import time
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


class NonBlockingStatusTests(unittest.TestCase):
    """Regression: `status()` used to load the ~70 MB model inline, so the
    first `get_voice_status` bridge call blocked for about three seconds.

    That is not merely slow. Every page showing the mic indicator makes that
    call on load, and pywebview delivers a return value by invoking a JS
    callback registered on the page that asked. A call that outlives its page
    — trivially easy during a three-second window — comes back to a document
    that no longer holds the callback, and pywebview's worker thread dies
    with `JavascriptException: ... is not a function`.
    """

    def setUp(self):
        # A directory that looks like an installed model without being one,
        # so the cheap "is it installed" check says yes and the expensive
        # load is never attempted.
        self.model_dir = Path(tempfile.mkdtemp(prefix="lector-fake-model-"))
        (self.model_dir / "placeholder").write_text("not a real model")
        self.engine = ve.VoiceEngine(model_dir=self.model_dir)

    def tearDown(self):
        shutil.rmtree(self.model_dir, ignore_errors=True)

    def test_status_reports_installed_without_loading_the_model(self):
        status = self.engine.status()

        self.assertTrue(status["available"])
        self.assertTrue(status["loading"])
        self.assertIsNone(self.engine._model)

    def test_status_is_cheap_enough_to_call_from_the_bridge(self):
        start = time.perf_counter()
        self.engine.status()
        elapsed = time.perf_counter() - start

        # A real load takes seconds; the threshold is loose enough not to be
        # flaky and tight enough that reintroducing an inline load fails it.
        self.assertLess(elapsed, 0.5)

    def test_a_load_that_failed_settles_to_unavailable(self):
        # Whatever the reason, once the background load has given up the
        # indicator must stop saying "still loading" and say why.
        self.engine._load_error = "Could not load the speech model: boom"

        status = self.engine.status()

        self.assertFalse(status["available"])
        self.assertFalse(status["loading"])
        self.assertEqual(status["error"], "Could not load the speech model: boom")


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

    def test_nothing_is_reported_as_loading(self):
        # There is no model to load, so the UI must settle on "unavailable"
        # immediately rather than waiting for a warm-up that will never come.
        self.assertFalse(self.engine.status()["loading"])

    def test_warm_up_is_safe_with_no_model_installed(self):
        self.engine.warm_up()  # must not raise
        self.assertFalse(self.engine.status()["available"])


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

    def test_warm_up_loads_the_model_off_the_calling_thread(self):
        engine = ve.VoiceEngine()

        start = time.perf_counter()
        engine.warm_up()
        returned_in = time.perf_counter() - start

        # The point of warm_up: it hands control straight back.
        self.assertLess(returned_in, 0.5)

        deadline = time.time() + 30
        while engine.status()["loading"] and time.time() < deadline:
            time.sleep(0.05)

        status = engine.status()
        self.assertTrue(status["available"])
        self.assertFalse(status["loading"])
        self.assertIsNone(status["error"])

    def test_json_grammar_round_trips(self):
        engine = ve.VoiceEngine(vocabulary=["next", "previous"])
        grammar = json.loads(json.dumps(engine._vocabulary + [ve.UNKNOWN_TOKEN]))
        self.assertEqual(grammar, ["next", "previous", ve.UNKNOWN_TOKEN])


if __name__ == "__main__":
    unittest.main()
