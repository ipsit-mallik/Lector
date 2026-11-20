"""The reader page's own scripts, as the tests read them.

The reader's script is `reading.js` (start-up) plus the `reading-*.js` modules
the rest of it is split into (docs/ARCHITECTURE.md § "Reader page layout").
A test that checks the reader's source reads all of them through here, in the
order `reading.html` loads them, so it holds whichever of the files a piece
of code lives in.
"""
import re
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "frontend" / "pages"
READING_HTML = PAGES / "reading.html"

_READER_TAG = re.compile(r'<script src="(reading(?:-[a-z]+(?:-[a-z]+)*)?\.js)"></script>')


def reader_script_paths() -> list[Path]:
    """`reading-*.js` and `reading.js`, in the order reading.html loads them."""
    names = _READER_TAG.findall(READING_HTML.read_text(encoding="utf-8"))
    if not names or names[-1] != "reading.js":
        raise AssertionError(f"reading.html should load reading.js last; found {names}")
    return [PAGES / name for name in names]


def reader_js() -> str:
    """Every reader script's text, one after another in load order."""
    return "\n".join(path.read_text(encoding="utf-8") for path in reader_script_paths())


def reader_script_defining(name: str) -> Path:
    """The reader script that declares the top-level function `name`."""
    pattern = re.compile(rf"^(?:async )?function {re.escape(name)}\(", re.M)
    for path in reader_script_paths():
        if pattern.search(path.read_text(encoding="utf-8")):
            return path
    raise AssertionError(f"No reader script declares function {name}")
