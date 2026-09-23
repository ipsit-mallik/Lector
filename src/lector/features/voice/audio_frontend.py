"""Audio preprocessing for the capture path: gain normalization, basic
spectral noise suppression, and VAD-based endpointing - applied to every
chunk before it reaches Vosk's recognizer (`engine.py`).

Milestone 8.2. Three stages, run in order by `AudioFrontend.process()`:

1. **Noise suppression** - spectral subtraction against a noise floor
   estimated continuously from the incoming audio itself. There is no
   calibration step: neither a push-to-talk hold nor a post-wake command
   window guarantees a leading stretch of silence to measure the room from,
   so the floor has to be tracked as audio arrives rather than measured once
   up front.
2. **Gain normalization** - scales the (now-cleaned) signal toward a target
   loudness, so a reader speaking quietly or holding the microphone close
   both land in the range Vosk was trained on, instead of a level that
   depends on hardware and distance.
3. **Voice-activity detection** - Silero VAD, run over the processed audio,
   answers "is this chunk speech" per `VAD_WINDOW_SAMPLES`. `engine.py` uses
   this to close the post-wake command window on trailing silence rather than
   always waiting out the full `wake.COMMAND_WINDOW_SECONDS`.

Deliberately does not depend on the `silero-vad` PyPI package, which pulls in
`torch` and `torchaudio` - hundreds of MB - for what is, at inference time, a
small ONNX graph. `onnxruntime` runs the same model in a few MB, which matches
how `engine.py` already treats Vosk: a small runtime plus a fetched/bundled
model file (`scripts/fetch_vad_model.py`), not a full ML framework. The
inference math below (context window, state shape, input/output names)
mirrors the `OnnxWrapper` class the `silero-vad` package itself uses
internally, so it stays compatible with the model file that script fetches.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

# .../<repo>/src/lector/features/voice/audio_frontend.py -> .../<repo>/assets
ASSETS_DIR = Path(__file__).resolve().parents[4] / "assets"
VAD_MODEL_PATH = ASSETS_DIR / "silero_vad.onnx"

# Silero VAD's fixed window/context sizes at 16 kHz - not configurable, the
# model was trained on exactly this framing.
VAD_WINDOW_SAMPLES = 512
VAD_CONTEXT_SAMPLES = 64
VAD_STATE_SHAPE = (2, 1, 128)
VAD_DEFAULT_THRESHOLD = 0.5

# Target RMS (float32 samples, [-1, 1]) that gain normalization aims for, and
# the cap on how far it may amplify a chunk.
TARGET_RMS = 0.15
MAX_GAIN = 8.0

# Spectral-subtraction tuning. NOISE_RISE_RATE is how fast the tracked noise
# floor is allowed to climb when a frame looks louder than the current
# estimate - slow, so a burst of speech is not immediately absorbed into
# "this is just noise now" (silence, by contrast, is allowed to lower the
# floor immediately - see `AudioFrontend.suppress_noise`). SPECTRAL_FLOOR
# keeps a small fraction of the original signal rather than subtracting to
# exact zero, which is what avoids the "musical noise" artefact classic
# spectral subtraction is known for.
NOISE_RISE_RATE = 0.1
NOISE_SUBTRACTION_FACTOR = 1.5
SPECTRAL_FLOOR = 0.05


def _pcm16_to_float(chunk: bytes) -> np.ndarray:
    """Raw little-endian int16 PCM bytes -> float32 samples in [-1, 1]."""
    ints = np.frombuffer(chunk, dtype="<i2")
    return ints.astype(np.float32) / 32768.0


def _float_to_pcm16(samples: np.ndarray) -> bytes:
    clamped = np.clip(samples, -1.0, 1.0)
    return (clamped * 32767.0).astype("<i2").tobytes()


def normalize_gain(samples: np.ndarray, target_rms: float = TARGET_RMS,
                    max_gain: float = MAX_GAIN) -> np.ndarray:
    """Scale `samples` toward `target_rms`, clamped both ways: never
    amplified past `max_gain` (so near-silence is not turned into audible
    hiss) and never pushed past [-1, 1] (so loud speech is not clipped
    further than it already was)."""
    if samples.size == 0:
        return samples
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < 1e-6:
        return samples
    gain = min(target_rms / rms, max_gain)
    return np.clip(samples * gain, -1.0, 1.0)


class SileroVAD:
    """Streaming wrapper around the Silero VAD ONNX model.

    Mirrors `VoiceEngine`'s treatment of the Vosk model: a missing model file
    is a supported state, not a crash - voice is an accelerator
    (`docs/PRD.md`), and its absence must not take audio preprocessing down
    with it. `speech_probabilities()` simply returns no results when the
    model cannot be loaded, which `is_speech()` then reads as "not speech"
    rather than raising.
    """

    def __init__(self, model_path: Path | None = None, sample_rate: int = 16000):
        self._model_path = Path(model_path) if model_path else VAD_MODEL_PATH
        self._sample_rate = sample_rate
        self._session = None
        self._load_error: str | None = None
        self.reset()

    def reset(self) -> None:
        """Clear streaming state. Call between unrelated audio sessions -
        carrying one session's trailing context into the next would let its
        last samples bias the very first window of the next."""
        self._state = np.zeros(VAD_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(VAD_CONTEXT_SAMPLES, dtype=np.float32)

    def is_available(self) -> bool:
        return self._session is not None or (
            self._load_error is None and self._model_path.exists()
        )

    def _ensure_session(self) -> bool:
        if self._session is not None:
            return True
        if self._load_error is not None:
            return False
        if not self._model_path.exists():
            self._load_error = (
                f"No VAD model at {self._model_path}. "
                "Run: python scripts/fetch_vad_model.py"
            )
            return False
        try:
            import onnxruntime

            opts = onnxruntime.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = onnxruntime.InferenceSession(
                str(self._model_path), sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            self._load_error = f"Could not load the VAD model: {exc}"
            return False
        return True

    def speech_probabilities(self, samples: np.ndarray) -> list[float]:
        """Per-`VAD_WINDOW_SAMPLES`-window speech probability across
        `samples`. The final partial window is zero-padded rather than
        dropped, so a short chunk still gets an answer. Returns an empty
        list if the model is unavailable."""
        if not self._ensure_session():
            return []
        probs = []
        for start in range(0, len(samples), VAD_WINDOW_SAMPLES):
            window = samples[start:start + VAD_WINDOW_SAMPLES]
            if len(window) < VAD_WINDOW_SAMPLES:
                window = np.pad(window, (0, VAD_WINDOW_SAMPLES - len(window)))
            probs.append(self._infer(window))
        return probs

    def _infer(self, window: np.ndarray) -> float:
        model_input = np.concatenate([self._context, window]).astype(np.float32)
        model_input = model_input[np.newaxis, :]
        ort_inputs = {
            "input": model_input,
            "state": self._state,
            "sr": np.array(self._sample_rate, dtype=np.int64),
        }
        out, state = self._session.run(None, ort_inputs)
        self._state = state
        self._context = window[-VAD_CONTEXT_SAMPLES:]
        return float(out[0][0])

    def is_speech(self, samples: np.ndarray, threshold: float = VAD_DEFAULT_THRESHOLD) -> bool:
        """Whether any window in `samples` scores as speech. A chunk this
        short (125 ms in `engine.py`) is treated as one unit rather than
        reporting per-window, since the caller only needs a single decision
        per chunk to gate endpointing."""
        return any(p >= threshold for p in self.speech_probabilities(samples))


class AudioFrontend:
    """Runs every captured chunk through noise suppression, gain
    normalization, and VAD before it reaches the recognizer.

    One instance is meant to live for a single listening session (a
    push-to-talk hold, or a wake-to-command stretch) - `reset()` between
    sessions clears both the noise-floor estimate and the VAD's streaming
    state, so one utterance's background noise does not bias the next.
    """

    def __init__(self, vad: SileroVAD | None = None):
        self._vad = vad if vad is not None else SileroVAD()
        self._noise_estimate: np.ndarray | None = None

    def reset(self) -> None:
        self._noise_estimate = None
        self._vad.reset()

    def suppress_noise(self, samples: np.ndarray) -> np.ndarray:
        """Basic spectral-subtraction noise suppression against a noise
        floor tracked continuously from the incoming audio."""
        if samples.size == 0:
            return samples
        spectrum = np.fft.rfft(samples)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)

        if self._noise_estimate is None or self._noise_estimate.shape != magnitude.shape:
            self._noise_estimate = magnitude.copy()
        else:
            # Climb slowly toward a louder frame (it might be speech, not a
            # new noise floor); drop immediately toward a quieter one (the
            # room really did get quieter, and holding onto a stale high
            # estimate would just under-suppress the next few frames).
            rising = magnitude > self._noise_estimate
            self._noise_estimate = np.where(
                rising,
                self._noise_estimate * (1 - NOISE_RISE_RATE) + magnitude * NOISE_RISE_RATE,
                magnitude,
            )

        floor = SPECTRAL_FLOOR * magnitude
        cleaned_magnitude = np.maximum(
            magnitude - NOISE_SUBTRACTION_FACTOR * self._noise_estimate, floor
        )
        cleaned_spectrum = cleaned_magnitude * np.exp(1j * phase)
        return np.fft.irfft(cleaned_spectrum, n=samples.size).astype(np.float32)

    def process(self, chunk: bytes) -> tuple[bytes, bool]:
        """Run one raw PCM chunk through the full pipeline.

        Returns `(processed_bytes, is_speech)`: `processed_bytes` is what
        should reach the recognizer in `chunk`'s place, and `is_speech` is
        what `engine.py` uses for VAD-based endpointing.
        """
        samples = _pcm16_to_float(chunk)
        cleaned = self.suppress_noise(samples)
        normalized = normalize_gain(cleaned)
        is_speech = self._vad.is_speech(normalized)
        return _float_to_pcm16(normalized), is_speech
