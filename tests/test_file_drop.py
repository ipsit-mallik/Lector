"""Window-wide drag-and-drop of a PDF (Milestone 8.12).

`file_drop.pdf_from_drop` turns pywebview's `drop` DOM event into the one PDF
to open (or a reader-facing reason it cannot be), and `Api` binds that handler
on every page load and forwards the outcome to the page as `lector:filedrop`.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.home import file_drop  # noqa: E402
from lector.features.settings import store  # noqa: E402

DISPATCH_PREFIX = "window.dispatchEvent(new CustomEvent('lector:filedrop', {detail: "
DISPATCH_SUFFIX = "}))"


def _event(*paths: str | None, names: list[str] | None = None) -> dict:
    """A pywebview drop event; a `None` path is a file pywebview could not
    resolve to a location (no `pywebviewFullPath` key)."""
    files = []
    for i, path in enumerate(paths):
        entry = {"name": (names[i] if names else Path(path or "x").name)}
        if path is not None:
            entry["pywebviewFullPath"] = path
        files.append(entry)
    return {"type": "drop", "dataTransfer": {"files": files}}


class PdfFromDropTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-drop-")
        self.addCleanup(self.dir.cleanup)

    def _file(self, name: str) -> str:
        path = Path(self.dir.name) / name
        path.write_bytes(b"%PDF-1.4\n")
        return str(path)

    def test_a_single_pdf_is_returned_by_its_full_path(self):
        path = self._file("a.pdf")

        self.assertEqual(file_drop.pdf_from_drop(_event(path)), {"path": path})

    def test_the_extension_is_matched_case_insensitively(self):
        path = self._file("SCAN.PDF")

        self.assertEqual(file_drop.pdf_from_drop(_event(path)), {"path": path})

    def test_a_non_pdf_is_refused_with_a_reason(self):
        result = file_drop.pdf_from_drop(_event(self._file("notes.docx")))

        self.assertEqual(result, {"error": file_drop.NOT_A_PDF})

    def test_the_first_pdf_wins_when_several_files_are_dropped(self):
        docx, first, second = self._file("a.docx"), self._file("b.pdf"), self._file("c.pdf")

        result = file_drop.pdf_from_drop(_event(docx, first, second))

        self.assertEqual(result, {"path": first})

    def test_a_drop_with_no_files_is_ignored(self):
        # Dragging selected text around the page also fires `drop`.
        self.assertIsNone(file_drop.pdf_from_drop({"type": "drop", "dataTransfer": {"files": []}}))
        self.assertIsNone(file_drop.pdf_from_drop({"type": "drop"}))
        self.assertIsNone(file_drop.pdf_from_drop({"type": "drop", "dataTransfer": None}))

    def test_a_file_pywebview_could_not_locate_is_reported_not_dropped_silently(self):
        result = file_drop.pdf_from_drop(_event(None, names=["a.pdf"]))

        self.assertEqual(result, {"error": file_drop.UNREADABLE})

    def test_a_pdf_that_no_longer_exists_says_so_not_that_it_is_not_a_pdf(self):
        missing = str(Path(self.dir.name) / "gone.pdf")

        result = file_drop.pdf_from_drop(_event(missing))

        self.assertEqual(result, {"error": file_drop.MISSING})
        self.assertNotEqual(file_drop.MISSING, file_drop.NOT_A_PDF)

    def test_a_folder_named_like_a_pdf_is_not_opened(self):
        folder = Path(self.dir.name) / "tricky.pdf"
        folder.mkdir()

        self.assertEqual(
            file_drop.pdf_from_drop(_event(str(folder))), {"error": file_drop.MISSING}
        )

    def test_a_real_pdf_is_preferred_over_a_missing_one_in_the_same_drop(self):
        missing = str(Path(self.dir.name) / "gone.pdf")
        real = self._file("real.pdf")

        self.assertEqual(file_drop.pdf_from_drop(_event(missing, real)), {"path": real})


class ApiFileDropTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)

        self.window = mock.Mock()
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pdf(self, name: str = "a.pdf") -> str:
        path = Path(self.dir.name) / name
        path.write_bytes(b"%PDF-1.4\n")
        return str(path)

    def _dispatched(self) -> dict:
        """The `detail` the page was sent, parsed back out of the script."""
        script = self.window.evaluate_js.call_args[0][0]
        self.assertTrue(script.startswith(DISPATCH_PREFIX), script)
        self.assertTrue(script.endswith(DISPATCH_SUFFIX), script)
        return json.loads(script[len(DISPATCH_PREFIX):-len(DISPATCH_SUFFIX)])

    def test_binding_registers_a_drop_handler_that_prevents_the_default(self):
        window = mock.Mock()

        self.api.bind_file_drop(window)

        event_name, handler = window.dom.document.on.call_args[0]
        self.assertEqual(event_name, "drop")
        # Without this the WebView navigates to the dropped file and the whole
        # app is replaced by a bare PDF viewer.
        self.assertTrue(handler.prevent_default)
        self.assertEqual(handler.callback, self.api._on_file_drop)

    def test_a_dropped_pdf_is_forwarded_to_the_page(self):
        path = self._pdf()

        self.api._on_file_drop(_event(path))

        self.assertEqual(self._dispatched(), {"path": path})

    def test_a_path_with_quotes_survives_the_trip_to_javascript(self):
        awkward = self._pdf("it's a test file.pdf")

        self.api._on_file_drop(_event(awkward))

        self.assertEqual(self._dispatched(), {"path": awkward})

    def test_a_refused_drop_forwards_the_reason(self):
        self.api._on_file_drop(_event(self._pdf("a.txt")))

        self.assertEqual(self._dispatched(), {"error": file_drop.NOT_A_PDF})

    def test_a_drop_with_no_files_forwards_nothing(self):
        self.api._on_file_drop({"type": "drop", "dataTransfer": {"files": []}})

        self.window.evaluate_js.assert_not_called()

    def test_dropping_never_opens_or_records_the_file_by_itself(self):
        # Opening is the page's decision (Reading must ask about unsaved
        # highlights first), so the backend only reports what was dropped.
        self.api._on_file_drop(_event(self._pdf()))

        self.assertEqual(store.get_recent_files(), [])

    def test_a_drop_during_teardown_with_no_window_does_not_raise(self):
        with mock.patch.object(api_module.webview, "windows", []):
            self.api._on_file_drop(_event(self._pdf()))

    def test_a_file_given_at_launch_reaches_the_page_the_way_a_drop_does(self):
        # Same event, so each page keeps its own meaning for "open this file"
        # (Reading still asks about unsaved highlights first).
        path = self._pdf()

        self.api.open_file_from_launch(path)

        self.assertEqual(self._dispatched(), {"path": path})

    def test_a_launch_file_that_is_not_a_pdf_forwards_the_reason(self):
        self.api.open_file_from_launch(self._pdf("notes.txt"))

        self.assertEqual(self._dispatched(), {"error": file_drop.NOT_A_PDF})


class PdfFromPathTests(unittest.TestCase):
    """A file named on the command line (`python -m lector a.pdf`), including one
    a second launch hands to the running Lector."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-launch-")
        self.addCleanup(self.dir.cleanup)

    def test_a_pdf_is_returned_by_its_path(self):
        path = Path(self.dir.name) / "a.PDF"
        path.write_bytes(b"%PDF-1.4\n")

        self.assertEqual(file_drop.pdf_from_path(str(path)), {"path": str(path)})

    def test_a_non_pdf_is_refused(self):
        path = Path(self.dir.name) / "a.txt"
        path.write_text("x", encoding="utf-8")

        self.assertEqual(file_drop.pdf_from_path(str(path)), {"error": file_drop.NOT_A_PDF})

    def test_a_missing_pdf_or_a_folder_says_it_cannot_be_found(self):
        folder = Path(self.dir.name) / "tricky.pdf"
        folder.mkdir()
        for path in (str(Path(self.dir.name) / "gone.pdf"), str(folder)):
            with self.subTest(path=path):
                self.assertEqual(file_drop.pdf_from_path(path), {"error": file_drop.MISSING})


if __name__ == "__main__":
    unittest.main()
