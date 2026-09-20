"""Dev-only helper: relaunches `python -m lector` whenever a source or
frontend file changes.

pywebview (like PySide6 before it) has no hot-reload of a live window —
restarting the process is the practical equivalent, since window/app state
doesn't persist across edits anyway. Watches both `src/` (Python) and
`frontend/` (HTML/CSS/JS) now that UI code lives there instead. Uses only
the standard library (mtime polling), so it adds no runtime dependency to
pyproject.toml.

Usage:
    python scripts/dev_watch.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH_DIRS = [ROOT / "src", ROOT / "frontend"]
WATCH_EXTENSIONS = (".py", ".html", ".css", ".js")
POLL_SECONDS = 0.5


def _snapshot() -> dict[Path, float]:
    return {
        p: p.stat().st_mtime
        for watch_dir in WATCH_DIRS
        for p in watch_dir.rglob("*")
        if p.suffix in WATCH_EXTENSIONS and "__pycache__" not in p.parts
    }


def main() -> None:
    proc: subprocess.Popen | None = None
    last = _snapshot()

    def launch() -> subprocess.Popen:
        print("[dev_watch] launching lector...")
        env = os.environ.copy()
        src = str(ROOT / "src")
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.Popen(
            [sys.executable, "-m", "lector"],
            cwd=ROOT,
            env=env,
        )

    try:
        proc = launch()
        while True:
            time.sleep(POLL_SECONDS)
            current = _snapshot()
            if current != last:
                last = current
                print("[dev_watch] change detected, restarting...")
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                proc = launch()
            elif proc.poll() is not None:
                # App was closed by the user — wait for the next change to relaunch.
                print("[dev_watch] app closed; waiting for a change to relaunch (Ctrl+C to quit)")
                while current == last:
                    time.sleep(POLL_SECONDS)
                    current = _snapshot()
                last = current
                proc = launch()
    except KeyboardInterrupt:
        print("\n[dev_watch] stopping...")
        if proc and proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
