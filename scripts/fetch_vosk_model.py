"""Download the offline Vosk speech model into `assets/vosk_model/`.

The model is ~40 MB of binary data, so it is gitignored rather than committed
(see `.gitignore`) — `docs/ARCHITECTURE.md` calls it a "bundled" model, which
it is at *packaging* time; for a working copy it is fetched once by this
script. Run it after cloning:

    python scripts/fetch_vosk_model.py

Re-running is a no-op if the model is already in place; pass --force to
re-download.
"""
import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEST = Path(__file__).resolve().parents[1] / "assets" / "vosk_model"

# The small English model is deliberate: docs/TECH_STACK.md constrains Vosk to
# a fixed command grammar, not general transcription, so the large model's
# extra accuracy on open-ended speech buys nothing and costs ~1.8 GB of
# install size against a project whose premise is being lighter than Adobe
# Reader.


# A Vosk model directory always carries these. Testing for them rather than
# for "the directory is non-empty" matters because `assets/vosk_model/` is
# checked into git as an empty directory via a `.gitkeep` file — which would
# otherwise read as an already-installed model.
MODEL_MARKERS = ("am", "conf", "graph")


def is_installed(dest: Path) -> bool:
    return all((dest / marker).is_dir() for marker in MODEL_MARKERS)


def _report(done: int, total: int) -> None:
    if total <= 0:
        return
    pct = done * 100 // total
    print(f"\r  {pct:3d}%  ({done // 1024 // 1024} / {total // 1024 // 1024} MB)", end="")


def fetch(force: bool = False) -> Path:
    if is_installed(DEST) and not force:
        print(f"Model already present at {DEST} - nothing to do.")
        return DEST

    print(f"Downloading {MODEL_URL}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "model.zip"
        with urllib.request.urlopen(MODEL_URL) as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length", 0))
            done = 0
            while chunk := response.read(1024 * 256):
                out.write(chunk)
                done += len(chunk)
                _report(done, total)
        print("\nExtracting...")

        extracted = Path(tmp) / "unzipped"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extracted)

        # The archive wraps everything in a single versioned directory; flatten
        # it so the app's model path stays stable across model versions.
        roots = [p for p in extracted.iterdir() if p.is_dir()]
        source = roots[0] if len(roots) == 1 else extracted

        # Move the model's contents *into* DEST rather than replacing DEST
        # itself, so the tracked `.gitkeep` that keeps the directory in git
        # survives a --force re-download.
        DEST.mkdir(parents=True, exist_ok=True)
        for marker in MODEL_MARKERS:
            stale = DEST / marker
            if stale.is_dir():
                shutil.rmtree(stale)
        for child in source.iterdir():
            shutil.move(str(child), str(DEST / child.name))

    print(f"Model installed at {DEST}")
    return DEST


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()
    try:
        fetch(force=args.force)
    except Exception as exc:
        print(f"Failed to fetch the Vosk model: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
