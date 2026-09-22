"""Tests for wake-phrase activation (Milestone 8).

`unittest` from the standard library, matching the rest of the suite. Run with:

    python -m unittest discover -s tests

The detection rule is tested exhaustively here because it is the piece that
decides whether an always-on microphone is tolerable: a wake that fires while
the reader is reading aloud is the failure mode that would make the feature
worth turning off. The mode machine around it is tested with the engine's own
worker, fed synthesized audio — no microphone required.
"""
import queue
import shutil
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

# voice_support puts `src` on the path; import it before the package.
import voice_support
from voice_support import engine as _engine

from lector.features.voice import engine as ve  # noqa: E402
from lector.features.voice import wake  # noqa: E402


class WakePhraseDetectionTests(unittest.TestCase):
    def test_the_phrase_itself_wakes(self):
        self.assertTrue(wake.contains_wake_phrase("hey lector"))

    def test_the_phrase_mid_utterance_wakes(self):
        # Vosk hands back a whole decoded utterance, which may have swept up
        # a word or two before the phrase.
        self.assertTrue(wake.contains_wake_phrase("okay hey lector"))

    def test_either_word_alone_does_not_wake(self):
        # Both of these are real decodings of ordinary speech against this
        # grammar: "the lecture was long" comes back as a bare "lector", and
        # "hey there how are you" as "hey [unk] [unk]".
        self.assertFalse(wake.contains_wake_phrase("lector"))
        self.assertFalse(wake.contains_wake_phrase("hey"))

    def test_the_words_out_of_order_do_not_wake(self):
        self.assertFalse(wake.contains_wake_phrase("lector hey"))

    def test_the_words_separated_do_not_wake(self):
        self.assertFalse(wake.contains_wake_phrase("hey there lector"))

    def test_empty_input_does_not_wake(self):
        self.assertFalse(wake.contains_wake_phrase(""))
        self.assertFalse(wake.contains_wake_phrase(None))

    def test_detection_is_case_insensitive(self):
        self.assertTrue(wake.contains_wake_phrase("Hey Lector"))


class StripWakePhraseTests(unittest.TestCase):
    def test_returns_what_followed_the_phrase(self):
        self.assertEqual(wake.strip_wake_phrase("hey lector next page"), "next page")

    def test_returns_empty_when_nothing_followed(self):
        self.assertEqual(wake.strip_wake_phrase("hey lector"), "")

    def test_returns_empty_when_the_phrase_is_absent(self):
        self.assertEqual(wake.strip_wake_phrase("next page"), "")

    def test_uses_the_last_occurrence(self):
        # Saying it twice means the command follows the second one.
        self.assertEqual(wake.strip_wake_phrase("hey lector hey lector save"), "save")


class ActivationIndependenceTests(unittest.TestCase):
    """Push-to-talk and the wake phrase are independent (docs/PRD.md), and
    with no model installed both must fail softly rather than raise."""

    def setUp(self):
        self.engine = ve.VoiceEngine(model_dir=Path("no-such-directory-anywhere"))

    def test_start_wake_listening_does_not_raise_without_a_model(self):
        status = self.engine.start_wake_listening()
        self.assertFalse(status["available"])
        self.assertFalse(status["wake_listening"])

    def test_the_request_is_remembered_even_when_it_cannot_be_honoured(self):
        # The reader turned it on; the model is what is missing. Forgetting
        # the request would silently disable hands-free mode for good once a
        # model was installed.
        self.engine.start_wake_listening()
        self.assertTrue(self.engine.status()["wake_requested"])

    def test_stopping_wake_listening_clears_the_request(self):
        self.engine.start_wake_listening()
        self.engine.stop_wake_listening()
        self.assertFalse(self.engine.status()["wake_requested"])

    def test_shutdown_is_safe_while_wake_listening_was_requested(self):
        self.engine.start_wake_listening()
        self.engine.shutdown()  # must not raise
        self.assertFalse(self.engine.status()["wake_requested"])


class WakeStatusTests(unittest.TestCase):
    """Idle listening and command listening are different states, and the
    indicator has to be able to tell them apart."""

    def setUp(self):
        self.model_dir = Path(tempfile.mkdtemp(prefix="lector-fake-model-"))
        (self.model_dir / "placeholder").write_text("not a real model")
        self.engine = ve.VoiceEngine(model_dir=self.model_dir)

    def tearDown(self):
        shutil.rmtree(self.model_dir, ignore_errors=True)

    def test_idle_wake_listening_is_not_reported_as_listening(self):
        # An open microphone waiting for "Hey Lector" must not light the
        # indicator as though a command were being captured.
        self.engine._mode = ve.MODE_WAKE
        status = self.engine.status()
        self.assertTrue(status["wake_listening"])
        self.assertFalse(status["listening"])

    def test_a_woken_command_window_is_reported_as_listening(self):
        self.engine._mode = ve.MODE_COMMAND
        self.engine._listening = True
        status = self.engine.status()
        self.assertTrue(status["listening"])


def _read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        return handle.readframes(handle.getnframes())


@unittest.skipUnless(voice_support.model_available(), voice_support.SKIP_REASON)
class WakeRecognizerTests(unittest.TestCase):
    """Drives the real recognizer over the engine's own worker thread.

    A microphone cannot be driven from CI or an agent environment, but
    everything downstream of the audio callback can: these push the same byte
    chunks the callback would onto the same queue.
    """

    def setUp(self):
        self.engine = _engine()
        self.engine._ensure_model()
        self.results = []
        self.engine.subscribe(self.results.append)

    def tearDown(self):
        self.engine._mode = ve.MODE_IDLE
        self.engine._audio.put(None)

    def test_the_idle_grammar_is_one_phrase_wide(self):
        # The whole cost argument for always-on listening rests on this.
        self.assertEqual(wake.WAKE_GRAMMAR, [wake.WAKE_PHRASE])

    def test_silence_does_not_wake(self):
        self.engine._recognizer = self.engine._new_recognizer(wake.WAKE_GRAMMAR)
        self.engine._mode = ve.MODE_WAKE
        self.engine._audio = queue.Queue()
        self.engine._worker = threading.Thread(target=self.engine._consume, daemon=True)
        self.engine._worker.start()

        silence = b"\x00\x00" * ve.BLOCK_SIZE
        for _ in range(6):
            self.engine._audio.put(silence)
        deadline = time.time() + 5
        while not self.engine._audio.empty() and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)

        self.assertFalse(any(r.get("wake") for r in self.results))
        self.assertEqual(self.engine._mode, ve.MODE_WAKE)

    def test_an_expired_command_window_closes_itself(self):
        """A wake nobody followed up on must not leave the microphone
        capturing indefinitely."""
        self.engine._recognizer = self.engine._new_recognizer()
        self.engine._mode = ve.MODE_COMMAND
        self.engine._listening = True
        self.engine._utterances = []
        self.engine._command_deadline = time.monotonic() - 1  # already expired
        self.engine._audio = queue.Queue()
        self.engine._worker = threading.Thread(target=self.engine._consume, daemon=True)
        self.engine._worker.start()

        self.engine._audio.put(b"\x00\x00" * ve.BLOCK_SIZE)
        deadline = time.time() + 5
        while self.engine._mode == ve.MODE_COMMAND and time.time() < deadline:
            time.sleep(0.02)

        self.assertEqual(self.engine._mode, ve.MODE_WAKE)
        self.assertFalse(self.engine.status()["listening"])
        # The reader saw the indicator light up, so they are owed an answer.
        self.assertTrue(any(r["final"] for r in self.results))

    def test_the_wake_recognizer_is_rebuilt_for_the_next_wake(self):
        self.engine._recognizer = self.engine._new_recognizer()
        self.engine._mode = ve.MODE_COMMAND
        self.engine._utterances = ["next page"]

        self.engine._finish_command_window(self.engine._recognizer)

        self.assertEqual(self.engine._mode, ve.MODE_WAKE)
        self.assertIsNotNone(self.engine._recognizer)
        final = [r for r in self.results if r["final"]]
        self.assertEqual(final[-1]["text"], "next page")


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPOKEN_WAKE_WAV = FIXTURES / "hey_lector.wav"


@unittest.skipUnless(
    voice_support.model_available() and SPOKEN_WAKE_WAV.exists(),
    "needs a loadable speech model and tests/fixtures/hey_lector.wav",
)
class SpokenWakePhraseTests(unittest.TestCase):
    """The end-to-end claim: real speech saying the phrase actually wakes it.

    The fixtures are synthesized rather than recorded, so they can live in the
    repository and run anywhere. Recognition of synthesized speech is not
    identical to a human voice, but it is the same signal path, and it is what
    settled the phrasing rule during development (see `wake.py`).
    """

    def setUp(self):
        self.engine = _engine()
        self.engine._ensure_model()
        self.results = []
        self.engine.subscribe(self.results.append)

    def _play(self, audio: bytes) -> None:
        """Feed `audio` in exactly the block sizes the capture callback uses.

        A second of silence is appended because a live microphone never stops
        delivering: the pause after the phrase is what lets Vosk close the
        utterance, and the engine deliberately acts only on closed utterances
        (see `_consume_wake_chunk`). A bare WAV would end mid-utterance and
        test something no reader experiences.
        """
        audio = audio + b"\x00\x00" * ve.SAMPLE_RATE
        step = ve.BLOCK_SIZE * 2  # bytes per block, 16-bit mono
        for offset in range(0, len(audio), step):
            self.engine._consume_wake_chunk(
                self.engine._recognizer, audio[offset:offset + step]
            )
            if self.engine._mode == ve.MODE_COMMAND:
                return

    def _listen_for_wake(self) -> None:
        self.engine._recognizer = self.engine._new_recognizer(wake.WAKE_GRAMMAR)
        self.engine._mode = ve.MODE_WAKE

    def test_saying_the_phrase_opens_a_command_window(self):
        # Arrange
        self._listen_for_wake()

        # Act
        self._play(_read_wav(SPOKEN_WAKE_WAV))

        # Assert
        self.assertEqual(self.engine._mode, ve.MODE_COMMAND)
        self.assertTrue(any(r.get("wake") for r in self.results))
        self.assertGreater(self.engine._command_deadline, time.monotonic())

    def test_the_command_recognizer_replaces_the_wake_one(self):
        """After waking, the recognizer must know the command vocabulary — a
        grammar-pinned recognizer can only return words it was built with, so
        leaving the one-phrase grammar in place would make every command that
        follows a wake undecodable."""
        self.engine.set_vocabulary(["next", "page"])
        self._listen_for_wake()
        wake_recognizer = self.engine._recognizer

        self._play(_read_wav(SPOKEN_WAKE_WAV))

        self.assertIsNot(self.engine._recognizer, wake_recognizer)

    def test_ordinary_speech_does_not_wake(self):
        """The false-positive case that decides whether always-on listening is
        tolerable at all."""
        for name in ("reading_aloud.wav", "hey_there.wav"):
            fixture = FIXTURES / name
            if not fixture.exists():
                continue
            with self.subTest(fixture=name):
                self._listen_for_wake()
                self._play(_read_wav(fixture))
                self.assertEqual(self.engine._mode, ve.MODE_WAKE)


if __name__ == "__main__":
    unittest.main()
