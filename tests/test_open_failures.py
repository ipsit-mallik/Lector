"""Opening a PDF that can't be opened (hardening H5).

`Api.open_pdf` must answer each kind of bad file with its own plain sentence
(`open_errors.MESSAGES`), leave Recent alone, never touch the file, and leave
the PDF that was already open exactly as it was. Before H5 a failed open
closed the open document first and left the reader holding a closed one, and
a password-protected PDF was added to Recent before it failed.

PDFs are built in code; settings.json is a temporary one. Two cases lean on
how MuPDF (1.28.2 here) treats a damaged file: a PDF with an empty page tree
opens with no pages, and one cut off before its cross-reference table is
repaired on open. A PyMuPDF upgrade that changes either shows up here.
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the throwaway PDFs these tests open

from lector import api as api_module  # noqa: E402
from lector.features.home import file_drop  # noqa: E402
from lector.features.reading import document as document_module  # noqa: E402
from lector.features.reading import open_errors  # noqa: E402
from lector.features.settings import store  # noqa: E402

# A well-formed PDF whose page tree is empty: MuPDF opens it without a word.
NO_PAGES_PDF = (
    b"%PDF-1.7\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
)


def _pdf_bytes(pages: int = 3, **save_options) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} has some words on it")
    data = doc.tobytes(**save_options)
    doc.close()
    return data


def _locked_bytes() -> bytes:
    return _pdf_bytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")


def _png_bytes() -> bytes:
    return pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False).tobytes("png")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OpenTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-open-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def recent_paths(self) -> list[str]:
        return [e["path"] for e in store.get_recent_files()]

    def assert_refused(self, path, reason: str, password=None) -> dict:
        result = self.api.open_pdf(str(path), password)
        self.assertEqual(result, {"error": open_errors.MESSAGES[reason], "reason": reason})
        # The page that shows it already knows the file; the sentence never
        # names it or where it is.
        self.assertNotIn(Path(path).stem, result["error"])
        self.assertNotIn(str(self.dir), result["error"])
        return result


class EachKindOfBadFileTests(OpenTestCase):
    def cases(self) -> list[tuple[Path, str]]:
        folder = self.dir / "a folder.pdf"
        folder.mkdir()
        return [
            (self.write("zero.pdf", b""), open_errors.EMPTY),
            (self.write("junk.pdf", b"this is not a PDF, only some text"), open_errors.NOT_A_PDF),
            (self.write("picture.pdf", _png_bytes()), open_errors.NOT_A_PDF),
            (self.write("empty tree.pdf", NO_PAGES_PDF), open_errors.NO_PAGES),
            (self.write("locked.pdf", _locked_bytes()), open_errors.PASSWORD),
            (self.dir / "gone.pdf", open_errors.MISSING),
            (folder, open_errors.MISSING),
        ]

    def test_each_gets_its_own_sentence_and_changes_nothing(self):
        for path, reason in self.cases():
            with self.subTest(file=path.name):
                before = _sha256(path) if path.is_file() else None
                self.assert_refused(path, reason)
                self.assertEqual(self.recent_paths(), [])
                self.assertFalse(self.api.get_state()["is_open"])
                self.assertEqual(_sha256(path) if path.is_file() else None, before)

    def test_a_file_that_cannot_be_read_is_unreadable_not_damaged(self):
        path = self.write("held.pdf", _pdf_bytes())
        with mock.patch.object(document_module, "open", create=True,
                               side_effect=PermissionError(13, "Permission denied")):
            self.assert_refused(path, open_errors.UNREADABLE)
        self.assertEqual(self.recent_paths(), [])

    def test_a_file_deleted_between_the_check_and_mupdf_is_missing(self):
        path = self.write("racing.pdf", _pdf_bytes())
        with mock.patch.object(pymupdf, "open", side_effect=pymupdf.FileNotFoundError("no such file")):
            self.assert_refused(path, open_errors.MISSING)

    def test_a_file_that_vanishes_while_it_is_being_checked_is_missing(self):
        path = self.write("vanishing.pdf", _pdf_bytes())
        with mock.patch.object(document_module, "open", create=True,
                               side_effect=FileNotFoundError(2, "No such file")):
            self.assert_refused(path, open_errors.MISSING)

    def test_mupdf_refusing_with_a_value_error_is_not_a_pdf_and_is_logged(self):
        path = self.write("odd value.pdf", _pdf_bytes())
        with mock.patch.object(pymupdf, "open", side_effect=ValueError("bad filetype")):
            with self.assertLogs("lector.features.reading.document", "WARNING"):
                self.assert_refused(path, open_errors.NOT_A_PDF)

    def test_an_unexpected_mupdf_failure_gets_the_general_sentence_and_a_log_line(self):
        path = self.write("odd.pdf", _pdf_bytes())
        with mock.patch.object(pymupdf, "open", side_effect=RuntimeError("code=7: something new")):
            with self.assertLogs("lector.features.reading.document", "ERROR"):
                result = self.assert_refused(path, open_errors.UNKNOWN)
        self.assertNotIn("code=7", result["error"])

    def test_every_reason_has_a_sentence(self):
        reasons = {v for k, v in vars(open_errors).items() if k.isupper() and k != "MESSAGES"}
        self.assertEqual(reasons, set(open_errors.MESSAGES))

    def test_the_wording_says_the_pdf_not_that_pdf(self):
        for message in [*open_errors.MESSAGES.values(), file_drop.MISSING]:
            with self.subTest(message=message):
                self.assertNotIn("that pdf", message.lower())

    def test_a_dropped_file_that_is_gone_reads_the_same_as_one_opened(self):
        self.assertEqual(file_drop.MISSING, open_errors.MESSAGES[open_errors.MISSING])


class FilesThatStillOpenTests(OpenTestCase):
    def test_a_truncated_pdf_mupdf_can_repair_still_opens(self):
        data = _pdf_bytes()
        path = self.write("cut short.pdf", data[: data.rfind(b"xref")])
        state = self.api.open_pdf(str(path))
        self.assertTrue(state["is_open"])
        self.assertEqual(state["page_count"], 3)
        self.assertEqual(self.recent_paths(), [str(path)])

    def test_a_pdf_with_only_an_owner_password_opens_without_asking(self):
        # Printing or copying restricted, but anyone may read it.
        path = self.write("restricted.pdf", _pdf_bytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw=""))
        state = self.api.open_pdf(str(path))
        self.assertTrue(state["is_open"])
        self.assertFalse(self.api._doc.is_password_protected)
        self.assertTrue(self.api._doc.words_on_page(0))


class TheOpenDocumentSurvivesTests(OpenTestCase):
    """The H1 bug: a failed open used to close the PDF on screen first."""

    def setUp(self):
        super().setUp()
        self.first = self.write("first.pdf", _pdf_bytes())
        self.api.open_pdf(str(self.first))
        self.api._doc.go_to_page(1)
        self.api._doc.set_zoom(1.5)
        self.api._doc.highlight_word_indices(1, 0, 1)

    def assert_first_still_open(self):
        state = self.api.get_state()
        self.assertTrue(state["is_open"])
        self.assertEqual(state["title"], "first")
        self.assertEqual(state["page_index"], 1)
        self.assertEqual(state["zoom"], 1.5)
        self.assertTrue(state["is_dirty"])
        self.assertTrue(state["can_undo"])
        self.assertEqual(state["highlight_count_total"], 1)
        self.assertEqual(self.api._doc.path, str(self.first))
        self.assertEqual(self.api._path, str(self.first))
        self.assertIsNotNone(self.api._doc.render_page(1))
        self.assertEqual(self.recent_paths(), [str(self.first)])

    def test_after_every_kind_of_failure(self):
        locked = self.write("locked.pdf", _locked_bytes())
        bad = [
            (self.write("zero.pdf", b""), None),
            (self.write("junk.pdf", b"not a pdf"), None),
            (self.write("empty tree.pdf", NO_PAGES_PDF), None),
            (self.dir / "gone.pdf", None),
            (locked, None),
            (locked, "wrong"),
        ]
        for path, password in bad:
            with self.subTest(file=path.name, password=password):
                self.assertIn("error", self.api.open_pdf(str(path), password))
                self.assert_first_still_open()

    def test_undo_still_works_on_it(self):
        self.api.open_pdf(str(self.dir / "gone.pdf"))
        self.assertEqual(self.api._doc.undo_highlight(), 1)
        self.assertEqual(self.api.get_state()["highlight_count_total"], 0)


if __name__ == "__main__":
    unittest.main()
