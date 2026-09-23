"""Tests for the capture-path audio preprocessing (Milestone 8.2).

`unittest` from the standard library, matching the rest of the suite. Run with:

    python -m unittest discover -s tests

The gain and noise-suppression math is tested directly, since it is pure
numpy with no external dependency. The VAD is tested against the real model
where one is available, skipping otherwise - mirroring how `test_voice_engine.py`
treats the Vosk model.
"""
import unittest
from unittest import mock

import numpy as np

import voice_support

from lector.features.voice import audio_frontend as af  # noqa: E402


def _tone(frequency: float, duration: float, amplitude: float = 0.5,
          sample_rate: int = af.VAD_WINDOW_SAMPLES * 31) -> np.ndarray:
    """A sine tone, for feeding something with real energy through the
    pipeline without needing a recorded voice sample."""
    n = int(duration * sample_rate)
    t = np.arange(n) / sample_rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def _float_chunk_to_pcm16(samples: np.ndarray) -> bytes:
    return af._float_to_pcm16(samples)


class PcmConversionTests(unittest.TestCase):
    def test_round_trips_within_quantization_error(self):
        original = np.array([0, 16384, -16384, 32767, -32768], dtype="<i2").tobytes()
        samples = af._pcm16_to_float(original)
        restored = af._float_to_pcm16(samples)
        restored_ints = np.frombuffer(restored, dtype="<i2")
        original_ints = np.frombuffer(original, dtype="<i2")
        # 16-bit round-trip loses at most one quantization step per sample.
        self.assertTrue(np.all(np.abs(restored_ints.astype(int) - original_ints.astype(int)) <= 1))

    def test_pcm16_to_float_scales_into_unit_range(self):
        chunk = np.array([32767, -32768], dtype="<i2").tobytes()
        samples = af._pcm16_to_float(chunk)
        self.assertAlmostEqual(float(samples[0]), 1.0, places=3)
        self.assertAlmostEqual(float(samples[1]), -1.0, places=3)

    def test_float_to_pcm16_clamps_out_of_range_values(self):
        samples = np.array([2.0, -2.0], dtype=np.float32)
        restored = af._float_to_pcm16(samples)
        ints = np.frombuffer(restored, dtype="<i2")
        self.assertEqual(ints[0], 32767)
        self.assertEqual(ints[1], -32767)


class NormalizeGainTests(unittest.TestCase):
    def test_boosts_a_quiet_signal_toward_the_target_rms(self):
        # Loud enough that the gain needed to reach TARGET_RMS stays under
        # MAX_GAIN - otherwise the cap, not the target, decides the output
        # level, which is `test_gain_is_capped_at_max_gain` below instead.
        quiet = _tone(440, 0.05, amplitude=0.05)
        normalized = af.normalize_gain(quiet)
        rms = float(np.sqrt(np.mean(np.square(normalized))))
        self.assertAlmostEqual(rms, af.TARGET_RMS, delta=0.02)

    def test_gain_is_capped_at_max_gain(self):
        # RMS ~0.001 would need ~150x gain to reach TARGET_RMS - far past
        # MAX_GAIN, so the cap is what actually decides the output level.
        near_silent = _tone(440, 0.05, amplitude=0.001)
        input_rms = float(np.sqrt(np.mean(np.square(near_silent))))
        normalized = af.normalize_gain(near_silent)
        output_rms = float(np.sqrt(np.mean(np.square(normalized))))
        self.assertLess(output_rms, af.TARGET_RMS)
        self.assertAlmostEqual(output_rms / input_rms, af.MAX_GAIN, delta=0.5)

    def test_near_silence_is_left_unchanged(self):
        silence = np.zeros(af.VAD_WINDOW_SAMPLES, dtype=np.float32)
        self.assertTrue(np.array_equal(af.normalize_gain(silence), silence))

    def test_does_not_clip_past_unit_range(self):
        loud = _tone(440, 0.05, amplitude=0.9)
        normalized = af.normalize_gain(loud)
        self.assertLessEqual(float(np.max(np.abs(normalized))), 1.0)

    def test_empty_array_returns_empty(self):
        result = af.normalize_gain(np.array([], dtype=np.float32))
        self.assertEqual(result.size, 0)


class SuppressNoiseTests(unittest.TestCase):
    def setUp(self):
        self.frontend = af.AudioFrontend()

    def test_output_is_the_same_length_as_the_input(self):
        samples = _tone(440, 0.05)
        cleaned = self.frontend.suppress_noise(samples)
        self.assertEqual(cleaned.shape, samples.shape)

    def test_empty_input_returns_empty(self):
        result = self.frontend.suppress_noise(np.array([], dtype=np.float32))
        self.assertEqual(result.size, 0)

    def test_steady_low_level_noise_is_attenuated_once_the_floor_is_learned(self):
        # A steady tone at constant amplitude is what the noise-floor tracker
        # is meant to absorb: after several identical frames, `suppress_noise`
        # should treat it as the floor and suppress it, not treat it as speech.
        noise = _tone(300, 0.05, amplitude=0.05)
        for _ in range(10):
            cleaned = self.frontend.suppress_noise(noise)
        noise_energy = float(np.mean(np.square(noise)))
        cleaned_energy = float(np.mean(np.square(cleaned)))
        self.assertLess(cleaned_energy, noise_energy)

    def test_a_loud_frame_after_quiet_ones_is_not_fully_absorbed(self):
        # NOISE_RISE_RATE exists precisely so a burst of speech is not
        # immediately folded into "this is just the noise floor now".
        quiet = _tone(300, 0.05, amplitude=0.02)
        for _ in range(5):
            self.frontend.suppress_noise(quiet)
        loud = _tone(300, 0.05, amplitude=0.5)
        cleaned = self.frontend.suppress_noise(loud)
        cleaned_energy = float(np.mean(np.square(cleaned)))
        loud_energy = float(np.mean(np.square(loud)))
        # A frame this much louder than the learned floor should survive
        # suppression as mostly itself, not be flattened to near zero.
        self.assertGreater(cleaned_energy, 0.1 * loud_energy)


@unittest.skipUnless(voice_support.vad_model_available(), voice_support.VAD_SKIP_REASON)
class SileroVADTests(unittest.TestCase):
    def setUp(self):
        self.vad = af.SileroVAD(model_path=voice_support.VAD_MODEL_PATH)

    def test_is_available_when_the_model_exists(self):
        self.assertTrue(self.vad.is_available())

    def test_silence_is_not_speech(self):
        silence = np.zeros(af.VAD_WINDOW_SAMPLES * 4, dtype=np.float32)
        self.assertFalse(self.vad.is_speech(silence))

    def test_speech_probabilities_returns_one_score_per_window(self):
        samples = np.zeros(af.VAD_WINDOW_SAMPLES * 3, dtype=np.float32)
        probs = self.vad.speech_probabilities(samples)
        self.assertEqual(len(probs), 3)

    def test_a_short_partial_window_is_still_scored(self):
        samples = np.zeros(af.VAD_WINDOW_SAMPLES // 2, dtype=np.float32)
        probs = self.vad.speech_probabilities(samples)
        self.assertEqual(len(probs), 1)

    def test_reset_clears_streaming_state(self):
        self.vad.is_speech(np.ones(af.VAD_WINDOW_SAMPLES, dtype=np.float32) * 0.5)
        self.vad.reset()
        self.assertTrue(np.array_equal(self.vad._state, np.zeros(af.VAD_STATE_SHAPE, dtype=np.float32)))
        self.assertTrue(np.array_equal(
            self.vad._context, np.zeros(af.VAD_CONTEXT_SAMPLES, dtype=np.float32)
        ))


class SileroVADMissingModelTests(unittest.TestCase):
    """A missing VAD model must degrade quietly, exactly like a missing Vosk
    model does elsewhere in this engine - voice is an accelerator
    (docs/PRD.md), and its absence must not take audio preprocessing down."""

    def setUp(self):
        self.vad = af.SileroVAD(model_path="no-such-vad-model.onnx")

    def test_is_available_is_false(self):
        self.assertFalse(self.vad.is_available())

    def test_speech_probabilities_returns_empty_rather_than_raising(self):
        samples = np.ones(af.VAD_WINDOW_SAMPLES, dtype=np.float32)
        self.assertEqual(self.vad.speech_probabilities(samples), [])

    def test_is_speech_is_false_rather_than_raising(self):
        samples = np.ones(af.VAD_WINDOW_SAMPLES, dtype=np.float32)
        self.assertFalse(self.vad.is_speech(samples))


class AudioFrontendProcessTests(unittest.TestCase):
    def setUp(self):
        self.frontend = af.AudioFrontend()

    def test_processed_output_is_the_same_byte_length_as_the_input(self):
        chunk = _float_chunk_to_pcm16(_tone(440, 0.05))
        processed, _ = self.frontend.process(chunk)
        self.assertEqual(len(processed), len(chunk))

    def test_silence_in_is_reported_as_not_speech(self):
        chunk = b"\x00\x00" * af.VAD_WINDOW_SAMPLES
        _, is_speech = self.frontend.process(chunk)
        self.assertFalse(is_speech)

    def test_reset_clears_the_noise_estimate(self):
        chunk = _float_chunk_to_pcm16(_tone(440, 0.05))
        self.frontend.process(chunk)
        self.assertIsNotNone(self.frontend._noise_estimate)

        self.frontend.reset()

        self.assertIsNone(self.frontend._noise_estimate)

    def test_reset_delegates_to_the_vad(self):
        with mock.patch.object(self.frontend._vad, "reset") as mock_reset:
            self.frontend.reset()
        mock_reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
