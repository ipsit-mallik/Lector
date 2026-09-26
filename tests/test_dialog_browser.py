"""Tests for `features/dialogs/browser.py` (Milestone 8.7).

`unittest`, matching the rest of the suite. Run with:

    python -m unittest discover -s tests

Exercises `list_directory` against a real temporary directory tree (folders
and PDFs are simplest to prove correct as actual files rather than mocks),
and `default_open_dir`/`default_save_dir` against a mocked
`features.settings.store`, since they only need its return shape, not a real
settings.json.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.dialogs import browser  # noqa: E402


class ListDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-browser-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_folders_are_listed_before_files_both_alphabetically(self):
        (self.root / "Zebra").mkdir()
        (self.root / "apple").mkdir()
        (self.root / "b.pdf").touch()
        (self.root / "A.pdf").touch()

        result = browser.list_directory(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["apple", "Zebra", "A.pdf", "b.pdf"])
        self.assertTrue(result["entries"][0]["is_dir"])
        self.assertTrue(result["entries"][1]["is_dir"])
        self.assertFalse(result["entries"][2]["is_dir"])
        self.assertFalse(result["entries"][3]["is_dir"])

    def test_non_pdf_files_are_omitted(self):
        (self.root / "notes.txt").touch()
        (self.root / "doc.pdf").touch()

        result = browser.list_directory(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["doc.pdf"])

    def test_dotfolders_are_omitted(self):
        (self.root / ".git").mkdir()
        (self.root / "visible").mkdir()

        result = browser.list_directory(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertEqual(names, ["visible"])

    def test_an_empty_directory_reports_no_entries_and_no_error(self):
        result = browser.list_directory(str(self.root))

        self.assertEqual(result["entries"], [])
        self.assertIsNone(result["error"])

    def test_parent_is_reported_for_a_non_root_directory(self):
        child = self.root / "child"
        child.mkdir()

        result = browser.list_directory(str(child))

        # Compared via `.resolve()` on both sides: Windows can report a
        # temp dir's short (8.3) form for one of the two independently
        # resolved paths, which is a filesystem quirk unrelated to what
        # this is checking — that the parent is `self.root`, however it is
        # spelled.
        self.assertEqual(Path(result["parent"]).resolve(), self.root.resolve())

    def test_the_filesystem_root_reports_no_parent(self):
        root = Path(self.tmp.name).parents[-1]

        result = browser.list_directory(str(root))

        self.assertIsNone(result["parent"])

    def test_an_unreadable_directory_reports_an_error_instead_of_raising(self):
        missing = self.root / "does-not-exist"

        result = browser.list_directory(str(missing))

        self.assertEqual(result["entries"], [])
        self.assertIsNotNone(result["error"])


class DefaultOpenDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-browser-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_uses_the_most_recent_files_folder_when_it_still_exists(self):
        recent_file = self.root / "report.pdf"
        recent_file.touch()
        with mock.patch.object(
            browser.settings, "get_recent_files", return_value=[{"path": str(recent_file)}]
        ):
            result = browser.default_open_dir()

        self.assertEqual(result, str(self.root))

    def test_falls_back_to_home_when_there_is_no_recent_file(self):
        with mock.patch.object(browser.settings, "get_recent_files", return_value=[]):
            result = browser.default_open_dir()

        self.assertEqual(result, str(Path.home()))

    def test_falls_back_to_home_when_the_recent_folder_is_gone(self):
        gone = self.root / "deleted" / "report.pdf"
        with mock.patch.object(
            browser.settings, "get_recent_files", return_value=[{"path": str(gone)}]
        ):
            result = browser.default_open_dir()

        self.assertEqual(result, str(Path.home()))


class DefaultSaveDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lector-browser-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_uses_the_suggested_paths_folder_when_it_exists(self):
        suggested = str(self.root / "report (highlighted).pdf")

        result = browser.default_save_dir(suggested)

        self.assertEqual(result, str(self.root))

    def test_falls_back_to_home_when_that_folder_is_gone(self):
        suggested = str(self.root / "deleted" / "report (highlighted).pdf")

        result = browser.default_save_dir(suggested)

        self.assertEqual(result, str(Path.home()))


if __name__ == "__main__":
    unittest.main()
