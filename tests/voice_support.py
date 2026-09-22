"""Shared setup for the voice tests: where `src` is, and where a real speech
model can be found.

Both voice test modules need the same two things, and they need them to agree,
so they live here rather than being copied.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.voice import engine as ve  # noqa: E402

# `engine.MODEL_DIR` is derived from the installed package's own location,
# which is right for the app and wrong for a checkout that borrows an already
# downloaded model: a git worktree's `assets/vosk_model/` holds nothing but the
# placeholder file. Pointing LECTOR_VOSK_MODEL at a real model lets the
# recognizer tests run there instead of silently skipping.
MODEL_DIR = Path(os.environ.get("LECTOR_VOSK_MODEL") or ve.MODEL_DIR)

SKIP_REASON = (
    "no loadable speech model - run: python scripts/fetch_vosk_model.py, or "
    "set LECTOR_VOSK_MODEL to an existing one"
)


def engine(**kwargs) -> ve.VoiceEngine:
    """A VoiceEngine pointed at whatever model this run should use."""
    kwargs.setdefault("model_dir", MODEL_DIR)
    return ve.VoiceEngine(**kwargs)


def model_available() -> bool:
    """Whether a model can actually be *loaded*.

    Deliberately stronger than `status()["available"]`, which only checks that
    the directory is non-empty: a checkout carrying just the placeholder file
    passes that cheap check and then fails inside Vosk, which would turn a
    skip into a confusing pile of errors.
    """
    return engine()._ensure_model()
