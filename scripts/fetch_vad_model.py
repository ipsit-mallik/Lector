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
at inference time, just this one small ONNX graph. This script instead reads
the `silero-vad` wheel's own file listing from PyPI's JSON API - the standard
way any Python tool locates a package's files - and pulls only the one model
file out of it, never installing the package or its heavy dependencies.

MIT licensed (github.com/snakers4/silero-vad), matching docs/PRD.md's
licensing constraint.
"""
import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

PYPI_METADATA_URL = "https://pypi.org/pypi/silero-vad/json"
MODEL_PATH_IN_WHEEL = "silero_vad/data/silero_vad.onnx"
DEST = Path(__file__).resolve().parents[1] / "assets" / "silero_vad.onnx"


def _report(done: int, total: int) -> None:
    if total <= 0:
        return
    pct = done * 100 // total
    print(f"\r  {pct:3d}%  ({done // 1024} / {total // 1024} KB)", end="")


def _wheel_url() -> str:
    with urllib.request.urlopen(PYPI_METADATA_URL) as response:
        metadata = json.loads(response.read())
    for entry in metadata["urls"]:
        if entry["packagetype"] == "bdist_wheel" and entry["filename"].endswith("-py3-none-any.whl"):
            return entry["url"]
    raise RuntimeError("No py3-none-any wheel found in the silero-vad PyPI metadata")


def fetch(force: bool = False) -> Path:
    if DEST.exists() and not force:
        print(f"Model already present at {DEST} - nothing to do.")
        return DEST

    wheel_url = _wheel_url()
    print(f"Downloading {wheel_url}")
    buffer = bytearray()
    with urllib.request.urlopen(wheel_url) as response:
        total = int(response.headers.get("Content-Length", 0))
        while chunk := response.read(1024 * 256):
            buffer.extend(chunk)
            _report(len(buffer), total)
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
