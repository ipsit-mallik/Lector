"""Unlocking a password-protected PDF (hardening H5).

`Api.open_pdf(path, password)` opens a protected PDF with either of its
passwords. Saving it keeps it protected, both ways: MuPDF's default would
write a copy without its password, and the check after a save used to refuse
any file that needs one. Its OCR text is never cached on disk, and the
password itself never reaches the log, settings.json, Recent or anything the
page is sent.

PDFs are built in code; settings.json and the OCR cache are temporary ones.
"""
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the throwaway PDFs these tests open

from lector import api as api_module  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading import ocr, ocr_cache, open_errors, safe_write  # noqa: E402
from lector.features.settings import store  # noqa: E402

# Planted so a leak anywhere is a plain string match.
PASSWORD = "Zebra-Quartz-913"
OWNER_PASSWORD = "Owner-Walnut-277"
PROSE = (
    "It is recommended that a book be used which can be ruled the same as the "
    "form below, so that a record of all the tests may be preserved for reference."
)


def _locked_pdf(path: Path, user_pw: str = PASSWORD, scanned_page: bool = False) -> Path:
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} has some words on it")
    if scanned_page:
        source = pymupdf.open()
        text_page = source.new_page(width=420, height=300)
        text_page.insert_textbox(pymupdf.Rect(30, 30, 390, 270), "A scanned page of text", fontsize=14)
        page = doc.new_page(width=420, height=300)
        page.insert_image(page.rect, stream=text_page.get_pixmap(dpi=150).tobytes("png"))
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_pw, owner_pw=OWNER_PASSWORD)
    doc.close()
    return path


def _plain_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Some words")
    doc.save(path)
    doc.close()
    return path


def _opened_with(path: Path, password: str | None) -> "pymupdf.Document | None":
    """`path` opened and unlocked with `password`, or None if it needs one
    and that isn't it. `needs_pass` is read before unlocking (see
    document._unlock)."""
    doc = pymupdf.open(path)
    if doc.needs_pass and not (password and doc.authenticate(password)):
        doc.close()
        return None
    return doc


def _payload_for_text(text: str) -> dict:
    """What `ocr.recognize` would return for a page reading `text`: a text
    page's own boxes (as tests/test_ocr.py's `_payload_for`)."""
    page = pymupdf.open().new_page(width=420, height=300)
    page.insert_textbox(pymupdf.Rect(30, 30, 390, 270), text, fontsize=14)
    return {
        "chars": [
            [c.rect.x0, c.rect.y0, c.rect.x1, c.rect.y1, c.text, c.block_no, c.line_no, c.char_no]
            for c in highlighter.chars_on_page(page)
        ],
        "words": [
            [w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1, w.text, w.block_no, w.line_no, w.word_no]
            for w in highlighter.words_on_page(page)
        ],
    }


def _highlights(doc: "pymupdf.Document") -> int:
    return sum(highlighter.highlight_count(page) for page in doc)


class PasswordTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-locked-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.settings_file = self.dir / "settings.json"
        for patcher in (
            mock.patch.object(store, "_settings_path", lambda: self.settings_file),
            mock.patch.object(ocr_cache, "cache_dir", lambda: self.dir / "ocr-cache"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)
        self.locked = _locked_pdf(self.dir / "locked.pdf")

    def recent_paths(self) -> list[str]:
        return [e["path"] for e in store.get_recent_files()]


class UnlockingTests(PasswordTestCase):
    def test_without_a_password_it_asks_for_one(self):
        result = self.api.open_pdf(str(self.locked))
        self.assertEqual(result["reason"], open_errors.PASSWORD)
        self.assertEqual(self.recent_paths(), [])

    def test_a_wrong_password_is_refused_and_not_added_to_recent(self):
        for wrong in ("", "zebra-quartz-913", PASSWORD + " ", "nope"):
            with self.subTest(password=wrong):
                result = self.api.open_pdf(str(self.locked), wrong)
                self.assertEqual(result, {
                    "error": open_errors.MESSAGES[open_errors.WRONG_PASSWORD],
                    "reason": open_errors.WRONG_PASSWORD,
                })
                self.assertEqual(self.recent_paths(), [])
                self.assertFalse(self.api.get_state()["is_open"])

    def test_a_password_the_page_could_not_have_typed_is_simply_wrong(self):
        # A lone surrogate is a valid JavaScript string but not valid text.
        result = self.api.open_pdf(str(self.locked), chr(0xD800))
        self.assertEqual(result["reason"], open_errors.WRONG_PASSWORD)
        self.assertEqual(self.recent_paths(), [])

    def test_a_password_that_is_not_text_is_refused(self):
        for value in (1234, ["secret"], {"pw": PASSWORD}):
            with self.subTest(value=value):
                result = self.api.open_pdf(str(self.locked), value)
                self.assertEqual(result["reason"], open_errors.UNKNOWN)
                self.assertFalse(self.api.get_state()["is_open"])

    def test_the_right_password_opens_it_with_its_text(self):
        state = self.api.open_pdf(str(self.locked), PASSWORD)
        self.assertTrue(state["is_open"])
        self.assertEqual(state["page_count"], 2)
        self.assertEqual(self.recent_paths(), [str(self.locked)])
        # PyMuPDF locks a document again if needs_pass is read after it is
        # unlocked, and its pages then read as empty: this would catch that.
        self.assertTrue(self.api._doc.words_on_page(0))
        self.assertIsNotNone(self.api._doc.render_page(0))
        self.assertTrue(self.api._doc.is_password_protected)

    def test_the_owner_password_opens_it_too(self):
        self.assertTrue(self.api.open_pdf(str(self.locked), OWNER_PASSWORD)["is_open"])
        self.assertTrue(self.api._doc.words_on_page(0))

    def test_a_password_outside_ascii_works(self):
        path = _locked_pdf(self.dir / "unicode.pdf", user_pw="pässwörd-東京")
        self.assertTrue(self.api.open_pdf(str(path), "pässwörd-東京")["is_open"])

    def test_the_password_is_forgotten_once_the_pdf_is_closed_or_replaced(self):
        self.api.open_pdf(str(self.locked), PASSWORD)
        self.api.open_pdf(str(_plain_pdf(self.dir / "plain.pdf")))
        self.assertFalse(self.api._doc.is_password_protected)
        self.api.open_pdf(str(self.locked), PASSWORD)
        self.api._doc.close()
        self.assertFalse(self.api._doc.is_password_protected)


class SavingKeepsThePasswordTests(PasswordTestCase):
    def setUp(self):
        super().setUp()
        self.api.open_pdf(str(self.locked), PASSWORD)
        self.api._doc.highlight_word_indices(0, 0, 1)

    def test_overwriting_keeps_it_protected_with_the_highlight(self):
        result = self.api.perform_save(store.OVERWRITE)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(_opened_with(self.locked, None))
        saved = _opened_with(self.locked, PASSWORD)
        self.assertEqual(_highlights(saved), 1)
        saved.close()
        # Still reading on, unlocked, after the reopen around the move.
        self.assertTrue(self.api._doc.words_on_page(0))
        self.assertFalse(self.api.get_state()["is_dirty"])

    def test_a_copy_keeps_the_same_password(self):
        copy = self.dir / "copy.pdf"
        result = self.api.perform_save(store.COPY, str(copy))
        self.assertTrue(result["ok"], result)
        self.assertIsNone(_opened_with(copy, None))
        saved = _opened_with(copy, PASSWORD)
        self.assertEqual(_highlights(saved), 1)
        saved.close()

    def test_a_copy_of_an_unprotected_pdf_stays_unprotected(self):
        self.api.open_pdf(str(_plain_pdf(self.dir / "plain.pdf")))
        self.api._doc.highlight_word_indices(0, 0, 0)
        copy = self.dir / "plain copy.pdf"
        self.assertTrue(self.api.perform_save(store.COPY, str(copy))["ok"])
        saved = pymupdf.open(copy)
        self.assertFalse(saved.needs_pass)
        saved.close()

    def test_a_copy_of_a_restricted_pdf_keeps_its_restrictions(self):
        restricted = self.dir / "restricted.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Some words")
        doc.save(restricted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw=OWNER_PASSWORD,
                 user_pw="", permissions=pymupdf.PDF_PERM_ACCESSIBILITY)
        doc.close()
        before = pymupdf.open(restricted)
        permissions = before.permissions
        before.close()
        self.api.open_pdf(str(restricted))
        self.api._doc.highlight_word_indices(0, 0, 0)
        copy = self.dir / "restricted copy.pdf"
        self.assertTrue(self.api.perform_save(store.COPY, str(copy))["ok"])
        saved = pymupdf.open(copy)
        self.assertEqual(saved.permissions, permissions)
        saved.close()

    def test_a_failed_overwrite_leaves_it_open_unlocked_and_unsaved(self):
        with mock.patch.object(safe_write, "_REPLACE_RETRY_SECONDS", 0), \
                mock.patch.object(safe_write, "replace", side_effect=PermissionError(13, "denied")):
            result = self.api.perform_save(store.OVERWRITE)
        self.assertFalse(result["ok"])
        doc = self.api._doc
        self.assertTrue(doc.is_open)
        self.assertTrue(doc.is_password_protected)
        self.assertTrue(doc.words_on_page(0))
        self.assertEqual(doc.highlight_counts(), {0: 1})
        self.assertTrue(doc.is_dirty)
        # The file on disk is as it was: protected, without the highlight.
        on_disk = _opened_with(self.locked, PASSWORD)
        self.assertEqual(_highlights(on_disk), 0)
        on_disk.close()

    def test_a_saved_file_that_lost_its_password_is_refused(self):
        real_write = self.api._doc._write_full

        def without_encryption(path, **options):
            real_write(path)  # MuPDF's default: no encryption

        with mock.patch.object(self.api._doc, "_write_full", side_effect=without_encryption):
            result = self.api.perform_save(store.OVERWRITE)
        self.assertEqual(result["error"], safe_write.MESSAGES[safe_write.VERIFY_FAILED])
        self.assertIsNone(_opened_with(self.locked, None))


class OcrTextIsNotCachedTests(PasswordTestCase):
    SCANNED_PAGE = 2

    def read_scanned_page(self, path: Path, password: str | None) -> mock.MagicMock:
        self.api.open_pdf(str(path), password)
        job = self.api._doc.begin_ocr(self.SCANNED_PAGE)
        self.assertIsNotNone(job)
        with mock.patch.object(ocr_cache, "store") as stored:
            self.assertTrue(self.api._doc.finish_ocr(job, ocr.DONE, _payload_for_text(PROSE)))
        self.assertEqual(self.api._doc.ocr_status(self.SCANNED_PAGE), ocr.DONE)
        return stored

    def test_a_protected_pdfs_page_is_read_but_not_cached(self):
        locked = _locked_pdf(self.dir / "scan.pdf", scanned_page=True)
        self.read_scanned_page(locked, PASSWORD).assert_not_called()

    def test_an_unprotected_pdfs_page_still_is(self):
        doc = _opened_with(_locked_pdf(self.dir / "scan.pdf", scanned_page=True), PASSWORD)
        doc.save(self.dir / "open scan.pdf")
        doc.close()
        self.read_scanned_page(self.dir / "open scan.pdf", None).assert_called_once()

    def test_a_protected_pdfs_job_finishing_after_another_pdf_opened_is_dropped(self):
        self.api.open_pdf(str(_locked_pdf(self.dir / "scan.pdf", scanned_page=True)), PASSWORD)
        job = self.api._doc.begin_ocr(self.SCANNED_PAGE)
        self.assertFalse(job.cacheable)
        self.api.open_pdf(str(_plain_pdf(self.dir / "plain.pdf")))
        with mock.patch.object(ocr_cache, "store") as stored:
            self.assertFalse(self.api._doc.finish_ocr(job, ocr.DONE, _payload_for_text(PROSE)))
        stored.assert_not_called()

    def test_a_job_finishing_after_the_pdf_was_closed_is_dropped(self):
        self.api.open_pdf(str(_locked_pdf(self.dir / "scan.pdf", scanned_page=True)), PASSWORD)
        job = self.api._doc.begin_ocr(self.SCANNED_PAGE)
        self.api._doc.close()
        with mock.patch.object(ocr_cache, "store") as stored:
            self.assertFalse(self.api._doc.finish_ocr(job, ocr.DONE, _payload_for_text(PROSE)))
        stored.assert_not_called()


class ThePasswordGoesNowhereTests(PasswordTestCase):
    def capture_every_log_record(self) -> list[logging.LogRecord]:
        records: list[logging.LogRecord] = []
        handler = logging.Handler(logging.DEBUG)
        handler.emit = records.append
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, handler)
        return records

    def test_not_in_the_log_settings_recent_or_what_the_page_is_sent(self):
        records = self.capture_every_log_record()
        sent = [
            self.api.open_pdf(str(self.locked), "wrong guess"),
            self.api.open_pdf(str(self.locked), PASSWORD),
            self.api.get_state(),
        ]
        self.api._doc.highlight_word_indices(0, 0, 1)
        sent.append(self.api.perform_save(store.OVERWRITE))
        self.api._doc.highlight_word_indices(1, 0, 1)
        with mock.patch.object(safe_write, "replace", side_effect=OSError(28, "No space")):
            sent.append(self.api.perform_save(store.OVERWRITE))
        sent.append(self.api.get_recent_files())

        formatter = logging.Formatter()
        logged = "\n".join(
            record.getMessage()
            + (formatter.formatException(record.exc_info) if record.exc_info else "")
            for record in records
        )
        self.assertNotIn(PASSWORD, logged)
        self.assertNotIn(PASSWORD, json.dumps(sent))
        self.assertNotIn(PASSWORD, self.settings_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
