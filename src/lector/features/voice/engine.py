"""Offline speech recognition: Vosk model + microphone capture.

Milestone 5 scope (see `docs/TASKS.md`): push-to-talk only. There is no
always-on listening here and no wake phrase — the engine is silent until
`start_listening()` is called and stops the moment `stop_listening()` is.
That isolation is the point of the milestone: "does recognition work at all"
is answered before "does always-on listening work" is asked.

Two deliberate shapes:

* **Grammar-constrained, not transcription.** `docs/TECH_STACK.md` picks Vosk
  precisely because it can be pinned to a small fixed vocabulary. A
  `KaldiRecognizer` given a JSON word list will only ever return words from
  that list, which turns "did it hear me correctly" from an open-ended
  accuracy problem into a closed-set one. Milestone 6's
  `command_grammar.py` owns the real vocabulary; until it exists this module
  carries a provisional list (`PROVISIONAL_VOCABULARY`) so the engine can be
  exercised, and accepts any word list passed to the constructor.

* **The model is loaded lazily and its absence is not fatal.** `docs/PRD.md`
  requires the app to stay fully usable by mouse and keyboard when voice is
  unavailable (a skipped mic permission, a missing model). So a failure to
  load reports itself through `status()` rather than raising out of app
  startup.
"""
import json
import queue
import threading
from pathlib import Path

# Vosk's small English model is 16 kHz mono; feeding it anything else quietly
# degrades recognition rather than erroring, so the rate is fixed here rather
# than taken from the device's default.
SAMPLE_RATE = 16000
CHANNELS = 1
# ~125 ms of audio per callback. Small enough that releasing the push-to-talk
# key feels immediate, large enough not to wake the reader thread constantly.
BLOCK_SIZE = 2000

# Placeholder vocabulary so the recognizer is genuinely grammar-constrained
# from day one. Milestone 6 replaces this with `command_grammar.py`'s
# synonym-mapped command set; it is intentionally NOT being designed here,
# because TASKS.md puts the grammar and its synonym map in the next milestone
# and the ordering exists to keep recognition failures separable from
# vocabulary failures.
PROVISIONAL_VOCABULARY = [
    "next", "previous", "page", "back", "forward",
    "top", "bottom", "highlight", "save", "stop",
]

# Vosk's convention for "anything not in the grammar" — without it, unknown
# speech is force-fitted onto the nearest listed word, which would make every
# stray cough look like a command.
UNKNOWN_TOKEN = "[unk]"

# .../<repo>/src/lector/features/voice/engine.py -> .../<repo>/assets/vosk_model
MODEL_DIR = Path(__file__).resolve().parents[4] / "assets" / "vosk_model"


class VoiceEngine:
    """Push-to-talk speech recognition over a fixed vocabulary.

    Lifecycle: `start_listening()` opens the microphone and begins feeding
    audio to the recognizer on a worker thread; `stop_listening()` closes it
    and returns the final recognized phrase. Callers subscribe to results
    with `subscribe()` instead of polling, so a future always-on mode
    (Milestone 8) can push results through the same channel.
    """

    def __init__(self, vocabulary: list[str] | None = None, model_dir: Path | None = None):
        self._vocabulary = list(vocabulary) if vocabulary else list(PROVISIONAL_VOCABULARY)
        self._model_dir = Path(model_dir) if model_dir else MODEL_DIR
        self._model = None
        self._load_error: str | None = None
        self._recognizer = None
        self._stream = None
        self._audio: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._listening = False
        self._lock = threading.Lock()
        self._subscribers: list = []
        # Utterances Vosk finished decoding *before* the key was released.
        # `Result()` consumes them, so `FinalResult()` at stop time only ever
        # returns whatever is still in flight — without keeping these, a
        # phrase that Vosk considered complete mid-hold would be silently
        # dropped from the result handed back to the caller.
        self._utterances: list[str] = []

    # ---------------------------------------------------------------- #
    # Subscription                                                       #
    # ---------------------------------------------------------------- #

    def subscribe(self, callback) -> None:
        """Register `callback(result: dict)` for every recognition result.

        `result` is `{"text": str, "final": bool}` — partial results stream
        while the key is held, and exactly one final result is emitted on
        release. Callbacks run on the engine's worker thread, so anything
        touching the UI must marshal to its own thread itself.
        """
        self._subscribers.append(callback)

    def _emit(self, text: str, final: bool) -> None:
        payload = {"text": text, "final": final}
        for callback in list(self._subscribers):
            try:
                callback(payload)
            except Exception:
                # A broken subscriber must not take the microphone down with
                # it; voice is an accelerator, not the app's only input path.
                pass

    # ---------------------------------------------------------------- #
    # Model                                                              #
    # ---------------------------------------------------------------- #

    def _ensure_model(self) -> bool:
        """Load the Vosk model on first use. Returns False (and records why)
        if it cannot be loaded, rather than raising."""
        if self._model is not None:
            return True
        if self._load_error is not None:
            return False
        if not self._model_dir.exists() or not any(self._model_dir.iterdir()):
            self._load_error = (
                f"No speech model at {self._model_dir}. "
                "Run: python scripts/fetch_vosk_model.py"
            )
            return False
        try:
            import vosk

            vosk.SetLogLevel(-1)  # Vosk logs decoding chatter to stderr by default.
            self._model = vosk.Model(str(self._model_dir))
        except Exception as exc:
            self._load_error = f"Could not load the speech model: {exc}"
            return False
        return True

    def _new_recognizer(self):
        import vosk

        grammar = json.dumps(self._vocabulary + [UNKNOWN_TOKEN])
        recognizer = vosk.KaldiRecognizer(self._model, SAMPLE_RATE, grammar)
        recognizer.SetWords(False)
        return recognizer

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        """Swap the recognized word list. Milestone 6 calls this with the real
        command grammar; takes effect on the next `start_listening()`."""
        self._vocabulary = list(vocabulary)

    # ---------------------------------------------------------------- #
    # Status                                                             #
    # ---------------------------------------------------------------- #

    def status(self) -> dict:
        """What the UI's mic indicator needs: is voice usable, and if not, why."""
        available = self._ensure_model()
        return {
            "available": available,
            "listening": self._listening,
            "error": self._load_error,
        }

    # ---------------------------------------------------------------- #
    # Push-to-talk                                                       #
    # ---------------------------------------------------------------- #

    def start_listening(self) -> dict:
        """Open the microphone and begin recognizing. Idempotent: holding the
        key already produces repeat keydown events, so a second call while
        listening is a no-op rather than a second stream."""
        with self._lock:
            if self._listening:
                return self.status()
            if not self._ensure_model():
                return self.status()
            try:
                import sounddevice

                self._recognizer = self._new_recognizer()
                self._utterances = []
                self._audio = queue.Queue()
                self._stream = sounddevice.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype="int16",
                    channels=CHANNELS,
                    callback=self._on_audio,
                )
                self._stream.start()
            except Exception as exc:
                self._load_error = f"Could not open the microphone: {exc}"
                self._teardown_stream()
                return {"available": False, "listening": False, "error": self._load_error}

            self._listening = True
            self._worker = threading.Thread(target=self._consume, daemon=True)
            self._worker.start()
        return self.status()

    def stop_listening(self) -> dict:
        """Close the microphone and return the final recognized phrase.

        Returns `{"text": str, "final": True, ...}`; `text` is empty when
        nothing intelligible was said, which callers should treat as "no
        command" rather than as an error.
        """
        with self._lock:
            if not self._listening:
                return {"text": "", "final": True, "available": self._model is not None,
                        "listening": False, "error": self._load_error}
            self._listening = False
            self._teardown_stream()

        # Unblock the worker's queue wait so it can finish and exit.
        self._audio.put(None)
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

        tail = ""
        if self._recognizer is not None:
            try:
                tail = _clean(json.loads(self._recognizer.FinalResult()).get("text", ""))
            except (ValueError, AttributeError):
                tail = ""
        self._recognizer = None

        # Utterances Vosk already closed out, plus whatever was still being
        # decoded when the key came up, in the order they were spoken.
        parts = [p for p in ([*self._utterances, tail]) if p]
        self._utterances = []
        text = " ".join(parts)

        self._emit(text, final=True)
        return {"text": text, "final": True, "available": True,
                "listening": False, "error": None}

    def shutdown(self) -> None:
        """Release the microphone on app exit."""
        if self._listening:
            self.stop_listening()

    # ---------------------------------------------------------------- #
    # Internals                                                          #
    # ---------------------------------------------------------------- #

    def _teardown_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """sounddevice callback — runs on PortAudio's thread, so it does no
        work beyond handing the bytes off."""
        self._audio.put(bytes(indata))

    def _consume(self) -> None:
        """Feed queued audio to the recognizer, emitting partial results as
        they firm up. Runs until `stop_listening()` queues the sentinel."""
        last_partial = ""
        while True:
            chunk = self._audio.get()
            if chunk is None:
                return
            recognizer = self._recognizer
            if recognizer is None:
                return
            try:
                if recognizer.AcceptWaveform(chunk):
                    text = _clean(json.loads(recognizer.Result()).get("text", ""))
                    if text:
                        self._utterances.append(text)
                        # Still not `final`: the key is held, so the reader may
                        # keep speaking. The phrase is shown as it firms up and
                        # only counts as the command on release.
                        self._emit(" ".join(self._utterances), final=False)
                    last_partial = ""
                else:
                    partial = _clean(json.loads(recognizer.PartialResult()).get("partial", ""))
                    if partial and partial != last_partial:
                        last_partial = partial
                        self._emit(partial, final=False)
            except Exception:
                # A malformed frame is not worth ending the listening session
                # over; the next chunk usually decodes fine.
                continue


def _clean(text: str) -> str:
    """Drop Vosk's unknown-word markers and collapse whitespace.

    `[unk]` is a decoding artifact, not something a caller should ever have to
    pattern-match against.
    """
    words = [w for w in (text or "").split() if w != UNKNOWN_TOKEN]
    return " ".join(words)
