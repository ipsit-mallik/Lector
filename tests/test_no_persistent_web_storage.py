"""Nothing the app needs may live in web storage that outlasts a launch.

Lector's pages come from a server on a port picked fresh for each launch, so
each launch is a new origin with empty `localStorage` and `IndexedDB`.
Everything that must survive a restart lives in `settings.json` (see
`features/settings/store.py`). Notes passed from one page to the next during a
launch (which Home section to open on, "open the Open dialog", the sidebar
highlight fade) use `sessionStorage`, which lasts as long as the window does.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SOURCES = sorted(p for p in FRONTEND.rglob("*") if p.suffix in (".html", ".js"))
HAND_OFF_KEYS = ("lector-home-section", "lector-open-dialog", "lector-nav-from")


class NoPersistentWebStorageTest(unittest.TestCase):
    def test_no_page_or_script_uses_storage_that_outlasts_the_window(self):
        for path in SOURCES:
            with self.subTest(file=str(path.relative_to(FRONTEND))):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"\blocalStorage\b|\bindexedDB\b|document\.cookie")

    def test_the_page_to_page_notes_travel_in_session_storage(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in SOURCES)
        for key in HAND_OFF_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, text)
        self.assertRegex(text, r'sessionStorage\.setItem\("lector-home-section"')
        self.assertRegex(text, re.compile(r"sessionStorage\.setItem\(OPEN_DIALOG_HANDOFF_KEY"))
        self.assertRegex(text, r'sessionStorage\.setItem\("lector-open-dialog"')


if __name__ == "__main__":
    unittest.main()
