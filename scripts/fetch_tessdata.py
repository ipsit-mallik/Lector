"""Download Tesseract's English language data into `assets/tessdata/`.

Mirrors `fetch_vad_model.py`'s shape: the file is binary data, so it is
gitignored rather than committed (see `.gitignore`), and is fetched once by
this script after cloning:

    python scripts/fetch_tessdata.py

Re-running is a no-op if the file is already in place; pass --force to
re-download.

Only the language data is fetched. The OCR engine itself is not: PyMuPDF's
wheel already has Tesseract compiled into MuPDF, so the one thing it lacks is
this file (`features/reading/ocr.py` points it here). `tessdata_fast` rather
than `tessdata_best`: about 4 MB instead of about 15, and for clean printed
pages the difference in accuracy is small next to the difference in speed.

Apache-2.0 licensed (github.com/tesseract-ocr/tessdata_fast).
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

# The 4.1.0 release, not the `main` branch, which can change under this
# script. Tesseract's native code parses the file, so it is only installed if
# it is exactly the file pinned here.
URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/eng.traineddata"
SHA256 = "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
DEST = Path(__file__).resolve().parents[1] / "assets" / "tessdata" / "eng.traineddata"


def _report(done: int, total: int) -> None:
    if total <= 0:
        return
    pct = done * 100 // total
    print(f"\r  {pct:3d}%  ({done // 1024} / {total // 1024} KB)", end="")


def fetch(force: bool = False) -> Path:
    if DEST.exists() and not force:
        print(f"Language data already present at {DEST} - nothing to do.")
        return DEST

    print(f"Downloading {URL}")
    buffer = bytearray()
    with urllib.request.urlopen(URL) as response:
        total = int(response.headers.get("Content-Length", 0))
        while chunk := response.read(1024 * 256):
            buffer.extend(chunk)
            _report(len(buffer), total)
    print()
    if hashlib.sha256(buffer).hexdigest() != SHA256:
        raise RuntimeError("the download does not match the pinned SHA-256; nothing was installed")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(bytes(buffer))

    print(f"Language data installed at {DEST}")
    return DEST


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()
    try:
        fetch(force=args.force)
    except Exception as exc:
        print(f"Failed to fetch the Tesseract language data: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
