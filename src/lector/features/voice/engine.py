"""Offline speech recognition: Vosk model + microphone capture.

The engine has two ways in, per `docs/PRD.md`'s "dual voice activation", and
they are independent — either, both, or neither may be running:

* **Push-to-talk** (Milestone 5). Silent until `start_listening()` is called,
  stopping the moment `stop_listening()` is. Building it first, on its own,
  is what let "does recognition work at all" be answered before "does
  always-on listening work" was asked.

* **Wake phrase** (Milestone 8). `start_wake_listening()` holds the
  microphone open against the one-phrase grammar in `wake.py` and, on hearing
  "Hey Lector", swaps in the full command vocabulary for a bounded window
  before falling back to idle. Only one of the two can hold the microphone at
  a time, so push-to-talk takes it over and hands it back — a reader who
  enables both is never made to choose between them in the moment.

Two deliberate shapes:

* **Grammar-constrained, not transcription.** `docs/TECH_STACK.md` picks Vosk
  precisely because it can be pinned to a small fixed vocabulary. A
  `KaldiRecognizer` given a JSON word list will only ever return words from
  that list, which turns "did it hear me correctly" from an open-ended
  accuracy problem into a closed-set one. `command_grammar.py` owns the
  vocabulary — it is derived there from the command phrasings themselves, so
  a synonym cannot be added without the recognizer also learning its words —
  and this module simply pins its recognizer to that list. A different word
  list can still be passed to the constructor, which is what the tests do.

* **The model loads off the caller's thread and its absence is not fatal.**
  `docs/PRD.md` requires the app to stay fully usable by mouse and keyboard
  when voice is unavailable (a skipped mic permission, a missing model), so a
  failure to load reports itself through `status()` rather than raising out of
  app startup. The load itself is several seconds of work and is started by
  `warm_up()` on a background thread, because doing it inside a bridge call
  makes that call outlive the page that issued it.
"""
import json
import queue
import threading
import time
from pathlib import Path

from lector.features.voice import command_grammar, wake

# Vosk's small English model is 16 kHz mono; feeding it anything else quietly
# degrades recognition rather than erroring, so the rate is fixed here rather
# than taken from the device's default.
SAMPLE_RATE = 16000
CHANNELS = 1
# ~125 ms of audio per callback. Small enough that releasing the push-to-talk
# key feels immediate, large enough not to wake the reader thread constantly.
BLOCK_SIZE = 2000

# The words the recognizer may return, and therefore the only words any
# command can be built from. Derived from the navigation grammar rather than
# listed here, so the two can never drift apart. Milestone 7 extends it with
# the highlight vocabulary; this module does not need to know when it does.
DEFAULT_VOCABULARY = command_grammar.VOCABULARY

# Vosk's convention for "anything not in the grammar" — without it, unknown
# speech is force-fitted onto the nearest listed word, which would make every
# stray cough look like a command.
UNKNOWN_TOKEN = "[unk]"

# .../<repo>/src/lector/features/voice/engine.py -> .../<repo>/assets/vosk_model
MODEL_DIR = Path(__file__).resolve().parents[4] / "assets" / "vosk_model"

# What the open microphone is currently for. Only one mode can hold the
# device, so this is a single value rather than a set of flags.
MODE_IDLE = "idle"            # microphone closed
MODE_PUSH_TO_TALK = "push_to_talk"  # a key is being held
MODE_WAKE = "wake"            # open, but only listening for the wake phrase
MODE_COMMAND = "command"      # woken; listening for the command that follows


class VoiceEngine:
    """Push-to-talk speech recognition over a fixed vocabulary.

    Lifecycle: `start_listening()` opens the microphone and begins feeding
    audio to the recognizer on a worker thread; `stop_listening()` closes it
    and returns the final recognized phrase. Callers subscribe to results
    with `subscribe()` instead of polling, which is what lets wake-phrase
    listening — where nobody is holding a key to have a return value handed
    back to — push its results through the same channel.
    """

    def __init__(self, vocabulary: list[str] | None = None, model_dir: Path | None = None):
        self._vocabulary = list(vocabulary) if vocabulary else list(DEFAULT_VOCABULARY)
        self._model_dir = Path(model_dir) if model_dir else MODEL_DIR
        self._model = None
        self._load_error: str | None = None
        self._recognizer = None
        self._stream = None
        self._audio: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._listening = False
        self._mode = MODE_IDLE
        # Whether the *caller* has asked for wake listening, as opposed to
        # whether it happens to be running right now. Push-to-talk borrows
        # the microphone and this is what tells `stop_listening` to hand it
        # back rather than leaving the reader's hands-free mode silently off.
        self._wake_requested = False
        # When the post-wake command window closes. Only meaningful in
        # MODE_COMMAND; read and written on the worker thread.
        self._command_deadline = 0.0
        self._lock = threading.Lock()
        # Guards the model load specifically. Separate from `_lock` (which
        # guards listening state) because a warm-up can hold it for seconds
        # and must not block a status check that only wants to know whether
        # a model exists on disk.
        self._model_lock = threading.Lock()
        self._warming = False
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

        `result` is `{"text": str, "final": bool, "wake": bool}` — partial
        results stream while the key is held (or while a post-wake command
        window is open), and exactly one final result is emitted when it
        ends. `wake` is True on exactly one payload per wake: the moment the
        phrase is heard, before any command has been spoken, so the UI can
        show that Lector is listening without waiting for words.

        Callbacks run on the engine's worker thread, so anything touching the
        UI must marshal to its own thread itself.
        """
        self._subscribers.append(callback)

    def _emit(self, text: str, final: bool, wake: bool = False) -> None:
        payload = {"text": text, "final": final, "wake": wake}
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

    def _model_is_installed(self) -> bool:
        """Whether there is a model on disk to load — the cheap half of "is
        voice available".

        Split out from the load itself because the two cost wildly different
        amounts: this is a stat, while `_ensure_model` is seconds of work.
        `status()` needs an answer on a bridge thread and can only afford
        this half (see `warm_up`).
        """
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
        return True

    def _ensure_model(self) -> bool:
        """Load the Vosk model on first use. Returns False (and records why)
        if it cannot be loaded, rather than raising.

        Takes `_model_lock` so a warm-up already in flight is waited on
        rather than raced: building a second `vosk.Model` would cost another
        ~70 MB and several seconds for a result that is thrown away.
        """
        with self._model_lock:
            if self._model is not None:
                return True
            if not self._model_is_installed():
                return False
            try:
                import vosk

                vosk.SetLogLevel(-1)  # Vosk logs decoding chatter to stderr by default.
                self._model = vosk.Model(str(self._model_dir))
            except Exception as exc:
                self._load_error = f"Could not load the speech model: {exc}"
                return False
            return True

    def warm_up(self) -> None:
        """Start loading the model on a background thread. Returns at once.

        Loading takes roughly three seconds. It used to happen inside the
        first `status()` call, which is made from every page that shows the
        mic indicator — and a bridge call that blocks for seconds is a
        seconds-long window in which the reader can navigate to another page.
        When they do, the JS callback pywebview is holding to deliver the
        return value dies with the old document, and pywebview's worker
        thread raises an unhandled `JavascriptException` against a callback
        that "is not a function". Doing the load here instead means the
        bridge call returns immediately and the load overlaps whatever the
        reader does next.

        Safe to call more than once; a load already finished or in flight is
        left alone.
        """
        with self._model_lock:
            if self._model is not None or self._load_error is not None or self._warming:
                return
            self._warming = True

        def load() -> None:
            try:
                self._ensure_model()
            finally:
                self._warming = False

        threading.Thread(target=load, name="voice-model-warmup", daemon=True).start()

    def _new_recognizer(self, vocabulary: list[str] | None = None):
        """A recognizer pinned to `vocabulary`, defaulting to the command one.

        The argument exists for wake listening, which pins a recognizer to
        `wake.WAKE_GRAMMAR` instead — a separate, far smaller word list that
        must not disturb the command vocabulary the next push-to-talk hold
        will use.
        """
        import vosk

        words = self._vocabulary if vocabulary is None else vocabulary
        grammar = json.dumps(list(words) + [UNKNOWN_TOKEN])
        recognizer = vosk.KaldiRecognizer(self._model, SAMPLE_RATE, grammar)
        recognizer.SetWords(False)
        return recognizer

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        """Swap the recognized word list; takes effect on the next
        `start_listening()`, or on the next wake. The navigation grammar is
        already the default — this is how Milestone 7 widens the vocabulary
        to the words currently on screen once highlighting can be spoken."""
        self._vocabulary = list(vocabulary)

    # ---------------------------------------------------------------- #
    # Status                                                             #
    # ---------------------------------------------------------------- #

    def status(self) -> dict:
        """What the UI's mic indicator needs: is voice usable, and if not, why.

        Deliberately does *not* force the model to load — see `warm_up` for
        why a slow answer here is worse than a provisional one. It reports
        whether a model is installed, plus `loading` while the background
        load is still running, so a caller that wants a settled answer can
        ask again instead of committing to "ready" or "unavailable" on the
        strength of a half-finished one.
        """
        installed = self._model_is_installed()
        return {
            "available": installed,
            "listening": self._listening,
            "error": self._load_error,
            "loading": installed and self._model is None,
            # Idle wake listening is deliberately reported separately from
            # `listening`: the microphone is open in both, but only one of
            # them is capturing something the reader meant as a command, and
            # an indicator that cannot tell them apart would either cry wolf
            # or hide an open microphone.
            "wake_listening": self._mode in (MODE_WAKE, MODE_COMMAND),
            "wake_requested": self._wake_requested,
        }

    # ---------------------------------------------------------------- #
    # Push-to-talk                                                       #
    # ---------------------------------------------------------------- #

    def start_listening(self) -> dict:
        """Open the microphone and begin recognizing. Idempotent: holding the
        key already produces repeat keydown events, so a second call while
        listening is a no-op rather than a second stream.

        Takes the microphone over from idle wake listening if that is what
        currently holds it; `stop_listening` hands it back. A reader with
        both activation modes on can reach for the key mid-sentence without
        having to think about which one is in charge.
        """
        with self._lock:
            if self._listening:
                return self.status()
            if self._mode != MODE_IDLE:
                self._end_session()
            if not self._ensure_model():
                return self.status()
            started = self._begin_session(MODE_PUSH_TO_TALK, self._new_recognizer)
            if started is not None:
                return started
            self._listening = True
        return self.status()

    def _begin_session(self, mode: str, make_recognizer) -> dict | None:
        """Open the stream and start the worker. Returns None on success, or
        a status dict describing the failure.

        Assumes `_lock` is held and the model is loaded. `make_recognizer` is
        a callable rather than a recognizer so that the (not free) work of
        building one only happens once the stream is known to be wanted, and
        so wake and command modes can differ in nothing but that callable.
        """
        try:
            import sounddevice

            self._recognizer = make_recognizer()
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
            self._mode = MODE_IDLE
            return {"available": False, "listening": False, "error": self._load_error}

        self._mode = mode
        self._worker = threading.Thread(target=self._consume, daemon=True)
        self._worker.start()
        return None

    def _end_session(self) -> None:
        """Close the stream and stop the worker, whatever mode it was in.

        Assumes `_lock` is held. Leaves `_recognizer` in place: push-to-talk
        still needs it afterwards to ask for the final result.
        """
        self._listening = False
        self._mode = MODE_IDLE
        self._teardown_stream()
        # Unblock the worker's queue wait so it can finish and exit.
        self._audio.put(None)
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

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
            self._end_session()

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
        # Hand the microphone back to idle wake listening if that is what the
        # reader asked for; push-to-talk only ever borrowed it.
        if self._wake_requested:
            self.start_wake_listening()
        return {"text": text, "final": True, "available": True,
                "listening": False, "error": None}

    # ---------------------------------------------------------------- #
    # Wake phrase                                                        #
    # ---------------------------------------------------------------- #

    def start_wake_listening(self) -> dict:
        """Hold the microphone open against the wake-phrase grammar.

        Idempotent, and safe to call when voice is unavailable — a missing
        model reports itself through `status()` exactly as it does for
        push-to-talk, because `docs/PRD.md` requires the app to keep working
        without voice at all.

        Deliberately does not fight push-to-talk for the device: if a key is
        being held right now, the request is remembered and honoured when
        that hold ends.
        """
        with self._lock:
            self._wake_requested = True
            if self._mode in (MODE_WAKE, MODE_COMMAND) or self._listening:
                return self.status()
            if not self._ensure_model():
                return self.status()
            failed = self._begin_session(
                MODE_WAKE, lambda: self._new_recognizer(wake.WAKE_GRAMMAR)
            )
            if failed is not None:
                return failed
        return self.status()

    def stop_wake_listening(self) -> dict:
        """Close the idle microphone and stop honouring wake requests.

        Called when the reader turns the wake phrase off in Settings, and on
        shutdown. Leaves push-to-talk alone: it is a separate activation
        mode, and turning one off must not take the other with it.
        """
        with self._lock:
            self._wake_requested = False
            if self._mode in (MODE_WAKE, MODE_COMMAND):
                self._end_session()
                self._recognizer = None
        return self.status()

    def shutdown(self) -> None:
        """Release the microphone on app exit.

        Wake listening is stopped *first* so that the flag telling
        `stop_listening` to hand the microphone back is already cleared —
        otherwise closing the app mid-hold would reopen the device on the
        way out.
        """
        self.stop_wake_listening()
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
        """Feed queued audio to the recognizer, emitting results as they firm
        up. Runs until a session ends and queues the sentinel.

        One loop serves every mode because the audio handling is identical in
        all of them; only what a decoded phrase *means* differs, and that is
        the two branches below.
        """
        last_partial = ""
        while True:
            chunk = self._audio.get()
            if chunk is None:
                return
            recognizer = self._recognizer
            if recognizer is None:
                return
            try:
                if self._mode == MODE_WAKE:
                    last_partial = ""
                    self._consume_wake_chunk(recognizer, chunk)
                    continue
                if self._mode == MODE_COMMAND and time.monotonic() > self._command_deadline:
                    # Nothing (or nothing more) was said in the window the
                    # wake opened. Close it out as a final result so the UI
                    # settles back to idle instead of implying an open
                    # microphone that is no longer listening for a command.
                    self._finish_command_window(recognizer)
                    last_partial = ""
                    continue
                if recognizer.AcceptWaveform(chunk):
                    text = _clean(json.loads(recognizer.Result()).get("text", ""))
                    if text:
                        self._utterances.append(text)
                        # Still not `final` under push-to-talk: the key is
                        # held, so the reader may keep speaking. The phrase is
                        # shown as it firms up and only counts as the command
                        # on release.
                        self._emit(" ".join(self._utterances), final=False)
                    last_partial = ""
                    if text and self._mode == MODE_COMMAND:
                        # Nobody is holding a key to mark the end of a spoken
                        # command here, so a completed utterance is the end of
                        # it — waiting out the rest of the window would just
                        # delay acting on what was already understood.
                        self._finish_command_window(recognizer)
                else:
                    partial = _clean(json.loads(recognizer.PartialResult()).get("partial", ""))
                    if partial and partial != last_partial:
                        last_partial = partial
                        self._emit(partial, final=False)
            except Exception:
                # A malformed frame is not worth ending the listening session
                # over; the next chunk usually decodes fine.
                continue

    def _consume_wake_chunk(self, recognizer, chunk) -> None:
        """Listen for the wake phrase in one chunk, and switch modes if it is
        there.

        Only *completed* utterances are checked, never partial ones. This was
        measured rather than assumed: "Hey there, how are you today" passes
        through a partial reading of exactly `hey lector` for one chunk before
        Vosk revises it to `hey [unk]` and settles on that as its final. A
        partial is a hypothesis the decoder is still free to retract, so
        waking on one means waking on speech the recognizer itself goes on to
        disagree with — and a wake nobody asked for is the failure that makes
        an always-on microphone not worth having.

        The cost is that the phrase must be followed by a breath: Vosk closes
        an utterance on a pause, so "Hey Lector, next page" said flat out
        wakes only at the end of the whole sentence. That is the trade the
        panel's wording teaches around ("say the phrase first"), and it is
        the right way round — a beat of latency against a false wake.

        Nothing is emitted for speech that is *not* the phrase: an idle
        listener that narrated every stray sound back to the reader would be
        its own kind of privacy problem.
        """
        if not recognizer.AcceptWaveform(chunk):
            return
        text = _clean(json.loads(recognizer.Result()).get("text", ""))
        if not wake.contains_wake_phrase(text):
            return

        self._utterances = []
        self._recognizer = self._new_recognizer()
        self._mode = MODE_COMMAND
        self._listening = True
        self._command_deadline = time.monotonic() + wake.COMMAND_WINDOW_SECONDS
        self._emit("", final=False, wake=True)

    def _finish_command_window(self, recognizer) -> None:
        """Emit whatever the post-wake window captured and return to idle.

        An empty phrase is emitted rather than swallowed: the reader saw the
        indicator light up when the wake fired, so they are owed an answer —
        "didn't catch that" — instead of an indicator that quietly goes dark.
        """
        try:
            tail = _clean(json.loads(recognizer.FinalResult()).get("text", ""))
        except (ValueError, AttributeError):
            tail = ""
        text = " ".join(p for p in [*self._utterances, tail] if p)
        self._utterances = []
        self._listening = False
        self._mode = MODE_WAKE
        self._recognizer = self._new_recognizer(wake.WAKE_GRAMMAR)
        self._emit(text, final=True)


def _clean(text: str) -> str:
    """Drop Vosk's unknown-word markers and collapse whitespace.

    `[unk]` is a decoding artifact, not something a caller should ever have to
    pattern-match against.
    """
    words = [w for w in (text or "").split() if w != UNKNOWN_TOKEN]
    return " ".join(words)
