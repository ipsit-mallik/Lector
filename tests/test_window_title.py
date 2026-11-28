"""The window's title follows the one unsaved flag and the page on screen.

"Report – Lector" in the reader, "*Report – Lector" while it has unsaved
changes, "Lector" on Home, Settings and the welcome screens
(docs/DESIGN_SYSTEM.md § "Unsaved changes"). And the native window is still
found for its title-bar colours and to come forward once its title has
changed (`window_chrome`, which once looked for the exact text "Lector").
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.shared import window_chrome, window_title  # noqa: E402

READER_URL = "http://127.0.0.1:5000/pages/reading.html"
HOME_URL = "http://127.0.0.1:5000/index.html"


class ComposeTests(unittest.TestCase):
    def test_off_the_reader_it_is_the_app_alone(self):
        self.assertEqual(window_title.compose(None, False), "Lector")
        self.assertEqual(window_title.compose("", True), "Lector")

    def test_the_document_then_the_app_with_an_en_dash(self):
        self.assertEqual(window_title.compose("Combined 27-11-2026", False), "Combined 27-11-2026 – Lector")

    def test_unsaved_starts_with_an_asterisk(self):
        self.assertEqual(window_title.compose("Combined 27-11-2026", True), "*Combined 27-11-2026 – Lector")

    def test_every_composed_title_is_recognised(self):
        for title in ("Lector", "Report – Lector", "*Report – Lector"):
            with self.subTest(title):
                self.assertTrue(window_title.is_lector_title(title))
        for title in ("Notepad", "Lector help", "Report - Lector"):
            with self.subTest(title):
                self.assertFalse(window_title.is_lector_title(title))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(store, "_settings_path", lambda: Path(self.dir.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)
        self.window = mock.Mock()
        self.window.events.shown.is_set.return_value = True
        self.window.get_current_url.return_value = READER_URL
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pdf = pdf_support.multi_page(Path(self.dir.name), pages=2, name="Report.pdf")

    def titles(self) -> list[str]:
        return [call.args[0] for call in self.window.set_title.call_args_list]

    def open_in_reader(self) -> None:
        self.api.open_pdf(str(self.pdf))
        self.api.on_page_loaded(self.window)

    def test_the_reader_names_the_document(self):
        self.open_in_reader()
        self.assertEqual(self.titles()[-1], "Report – Lector")

    def test_a_highlight_adds_the_asterisk_and_saving_takes_it_away(self):
        self.open_in_reader()
        self.api._doc.highlight_word_indices(0, 0, 1, "yellow")
        self.api.get_state()
        self.assertEqual(self.titles()[-1], "*Report – Lector")
        self.api.perform_save("overwrite", None)
        self.assertEqual(self.titles()[-1], "Report – Lector")

    def test_undo_back_to_the_saved_state_takes_it_away(self):
        self.open_in_reader()
        self.api._doc.highlight_word_indices(0, 0, 1, "yellow")
        self.api.undo_highlight()
        self.assertEqual(self.titles()[-1], "Report – Lector")

    def test_home_says_lector_even_with_an_unsaved_document_open(self):
        self.open_in_reader()
        self.api._doc.highlight_word_indices(0, 0, 1, "yellow")
        self.window.get_current_url.return_value = HOME_URL
        self.api.on_page_loaded(self.window)
        self.assertEqual(self.titles()[-1], "Lector")

    def test_set_only_when_it_changes(self):
        self.open_in_reader()
        before = len(self.titles())
        for _ in range(3):
            self.api.get_state()
        self.assertEqual(len(self.titles()), before)

    def test_not_before_the_window_is_shown(self):
        self.window.events.shown.is_set.return_value = False
        self.open_in_reader()
        self.window.set_title.assert_not_called()
        self.window.events.shown.is_set.return_value = True
        self.api.sync_window_title()
        self.assertEqual(self.titles(), ["Report – Lector"])

    def test_no_window_is_quietly_nothing(self):
        with mock.patch.object(api_module.webview, "windows", []):
            self.open_in_reader()
            self.api.get_state()

    def test_a_failure_to_set_it_never_fails_the_call(self):
        self.window.set_title.side_effect = RuntimeError("gone")
        self.open_in_reader()
        self.assertTrue(self.api.get_state()["is_open"])


class FindWindowTests(unittest.TestCase):
    """`_find_window` with user32 stood in for: found once by a Lector
    title, then by its handle whatever the title says."""

    def setUp(self):
        self.titles = {101: "Some other app", 202: "Lector"}
        user32 = mock.Mock()
        user32.IsWindow.side_effect = lambda hwnd: hwnd.value in self.titles
        patcher = mock.patch.object(window_chrome, "_enum_window", self.enum)
        patcher.start()
        self.addCleanup(patcher.stop)
        windll = mock.Mock(user32=user32)
        patcher = mock.patch.object(window_chrome.ctypes, "windll", windll, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(window_chrome, "_hwnd", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.enumerated = 0

    def enum(self, matches):
        self.enumerated += 1
        return next((hwnd for hwnd, text in self.titles.items() if matches(text)), None)

    def test_found_by_title_then_kept_by_handle(self):
        self.assertEqual(window_chrome._find_window(window_chrome.WINDOW_TITLE), 202)
        self.titles[202] = "*Report – Lector"
        self.assertEqual(window_chrome._find_window(window_chrome.WINDOW_TITLE), 202)
        self.assertEqual(self.enumerated, 1)

    def test_a_changed_title_is_found_too(self):
        self.titles[202] = "*Report – Lector"
        self.assertEqual(window_chrome._find_window(window_chrome.WINDOW_TITLE), 202)

    def test_a_closed_window_is_looked_for_again(self):
        window_chrome._find_window(window_chrome.WINDOW_TITLE)
        del self.titles[202]
        self.assertIsNone(window_chrome._find_window(window_chrome.WINDOW_TITLE))
        self.assertEqual(self.enumerated, 2)


if __name__ == "__main__":
    unittest.main()
