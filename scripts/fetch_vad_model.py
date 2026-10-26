"""Download the offline Silero VAD model into `assets/silero_vad.onnx`.

Mirrors `fetch_vosk_model.py`'s shape: the model is binary data, so it is
gitignored rather than committed (see `.gitignore`), and is fetched once by
this script after cloning:

    python scripts/fetch_vad_model.py

Re-running is a no-op if the model is already in place; pass --force to
re-download.

The model itself is ~2 MB - `features/voice/audio_frontend.py` runs it
through `onnxruntime` directly rather than depending on the `silero-vad` PyPI
package, which pulls in `torch` and `torchaudio` (hundreds of MB) for what is,
at inference time, just this one small ONNX graph. This script instead
downloads one pinned `silero-vad` wheel from PyPI and pulls only the one model
file out of it, never installing the package or its heavy dependencies.

MIT licensed (github.com/snakers4/silero-vad), matching docs/PRD.md's
licensing constraint.
"""
import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

# The 6.2.3 wheel, rather than whichever release PyPI calls latest. onnxruntime's
# native code parses the model, so the wheel is only opened if it is exactly the
# file pinned here (the same SHA-256 PyPI lists for it).
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/84/ef/"
    "9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/"
    "silero_vad-6.2.3-py3-none-any.whl"
)
WHEEL_SHA256 = "7b7f5436cfcb02fae583a05b512ea96467fd449fe54cb49a5e4f06c51a1e43b8"
MODEL_PATH_IN_WHEEL = "silero_vad/data/silero_vad.onnx"
DEST = Path(__file__).resolve().parents[1] / "assets" / "silero_vad.onnx"


def _report(done: int, total: int) -> None:
    if total <= 0:
        return
    pct = done * 100 // total
    print(f"\r  {pct:3d}%  ({done // 1024} / {total // 1024} KB)", end="")


def fetch(force: bool = False) -> Path:
    if DEST.exists() and not force:
        print(f"Model already present at {DEST} - nothing to do.")
        return DEST

    print(f"Downloading {WHEEL_URL}")
    buffer = bytearray()
    with urllib.request.urlopen(WHEEL_URL) as response:
        total = int(response.headers.get("Content-Length", 0))
        while chunk := response.read(1024 * 256):
            buffer.extend(chunk)
            _report(len(buffer), total)
    if hashlib.sha256(buffer).hexdigest() != WHEEL_SHA256:
        raise RuntimeError("the download does not match the pinned SHA-256; nothing was installed")
    print("\nExtracting model file...")

    with zipfile.ZipFile(io.BytesIO(bytes(buffer))) as zf:
        model_bytes = zf.read(MODEL_PATH_IN_WHEEL)

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(model_bytes)

    print(f"Model installed at {DEST}")
    return DEST


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()
    try:
        fetch(force=args.force)
    except Exception as exc:
        print(f"Failed to fetch the Silero VAD model: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
