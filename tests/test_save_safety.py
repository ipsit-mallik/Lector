"""Saving never damages a file that is already on disk (hardening H2).

The promises held here:

* **A save is all or nothing.** Both "Save a copy" and "Overwrite the
  original" write a temporary file beside the destination, check it, and move
  it into place in one step. A failure at any point — the write, the check,
  the move — leaves the original (and any file already at a copy's
  destination) byte-for-byte as it was, leaves no temporary file behind, and
  leaves the highlights open and unsaved in the reader.
* **The reader carries on after an overwrite** on the same page and zoom, with
  undo and redo still working, because the document is closed and reopened
  around the move rather than started afresh.
* **Every failure is a sentence for the reader** (`safe_write.MESSAGES`), not
  MuPDF's text, through `Api.perform_save`.
* **Nothing is replaced unasked.** A copy onto the open PDF itself, under any
  spelling, is refused; a signed PDF is never overwritten; the suggested copy
  name skips names that are taken; `describe_save_target` tells the Save-As
  screen when to ask.

PDFs are built in code with PyMuPDF; no binary fixtures.
"""
import errno
import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fitz  # noqa: E402  # PyMuPDF: builds the throwaway PDFs these tests save

from lector import api as api_module  # noqa: E402
from lector.features.reading import safe_write  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.reading.safe_write import SaveError  # noqa: E402
from lector.features.settings import store  # noqa: E402

PAGE_TEXT = "Alpha beta gamma delta epsilon on page {}"


class _Sharing(PermissionError):
    """A Windows sharing violation, on any platform (`winerror` is only a
    real attribute on Windows)."""

    winerror = 32


def _make_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), PAGE_TEXT.format(i))
    doc.save(path)
    doc.close()
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _highlights_on_disk(path: Path) -> list[int]:
    doc = fitz.open(path)
    try:
        return [len(list(page.annots(types=[fitz.PDF_ANNOT_HIGHLIGHT]) or [])) for page in doc]
    finally:
        doc.close()


def _write_half_then_fail(self, path, *args, **kwargs):
    """Stands in for `fitz.Document.save`: starts the file, then fails the
    way MuPDF's writer does (RuntimeError, not OSError)."""
    with open(path, "wb") as f:
        f.write(b"%PDF-1.7\n% cut off here")
    raise RuntimeError("code=1: write error")


def _one_blank_page() -> bytes:
    doc = fitz.open()
    doc.new_page()
    try:
        return doc.tobytes()
    finally:
        doc.close()


# Built up front: tobytes() goes through Document.save, which the stand-in
# below replaces.
_ANOTHER_PDF = _one_blank_page()


def _write_another_pdf(self, path, *args, **kwargs):
    """Stands in for `fitz.Document.save`: writes a valid PDF that is not
    this document (one page, no highlights), so only the check can catch it."""
    Path(path).write_bytes(_ANOTHER_PDF)


class SaveSafetyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lector-save-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.original = _make_pdf(self.dir / "report.pdf")
        self.doc = PdfDocument()
        self.doc.open(str(self.original))
        self.addCleanup(self.doc.close)

    def highlight(self, page_index: int = 1) -> int:
        xref = self.doc.highlight_word_indices(page_index, 0, 1)
        self.assertIsNotNone(xref)
        return xref

    def assert_no_temp_files(self):
        self.assertEqual(sorted(self.dir.glob(f"{safe_write.TEMP_PREFIX}*")), [])

    def assert_still_open_and_unsaved(self, highlights: dict[int, int]):
        self.assertTrue(self.doc.is_open)
        self.assertTrue(self.doc.is_dirty)
        self.assertEqual(self.doc.highlight_counts(), highlights)
        self.assertIsNotNone(self.doc.render_page(0))

    def reason_of(self, save) -> str:
        with self.assertRaises(SaveError) as caught:
            save()
        return caught.exception.reason


class RoundTripTests(SaveSafetyTestCase):
    def test_a_copy_has_the_highlight_and_the_original_is_untouched(self):
        before = _sha256(self.original)
        self.highlight()
        copy = self.dir / "copy.pdf"

        self.doc.save_copy(str(copy))

        self.assertEqual(_highlights_on_disk(copy), [0, 1, 0])
        self.assertEqual(_sha256(self.original), before)
        self.assertFalse(self.doc.is_dirty)
        self.assert_no_temp_files()

    def test_an_overwrite_has_the_highlight_and_the_reader_carries_on_where_it_was(self):
        self.doc.go_to_page(1)
        self.doc.set_zoom(1.5)
        self.highlight()

        self.doc.save_overwrite()

        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])
        self.assertFalse(self.doc.is_dirty)
        self.assertEqual((self.doc.page_index, self.doc.zoom), (1, 1.5))
        self.assert_no_temp_files()

    def test_undo_and_redo_still_work_after_an_overwrite(self):
        self.highlight()
        self.doc.save_overwrite()

        self.assertTrue(self.doc.can_undo)
        self.assertEqual(self.doc.undo_highlight(), 1)
        self.assertEqual(self.doc.highlight_counts(), {})
        self.assertEqual(self.doc.redo_highlight(), 1)
        self.assertEqual(self.doc.highlight_counts(), {1: 1})

        self.doc.save_overwrite()
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])

    def test_saving_twice_in_a_row_overwrites_cleanly_both_times(self):
        self.highlight(0)
        self.doc.save_overwrite()
        self.highlight(2)
        self.doc.save_overwrite()

        self.assertEqual(_highlights_on_disk(self.original), [1, 0, 1])

    def test_a_pdf_mupdf_had_to_repair_can_now_be_overwritten(self):
        # saveIncr() refuses a repaired file ("Can't do incremental writes on
        # a repaired file"), and a cut-off download is a common one.
        data = self.original.read_bytes()
        damaged = self.dir / "damaged.pdf"
        damaged.write_bytes(data[:-150])
        self.doc.open(str(damaged))
        self.highlight(0)

        self.doc.save_overwrite()

        self.assertEqual(_highlights_on_disk(damaged), [1, 0, 0])

    def test_a_copy_may_replace_an_existing_file_once_the_reader_has_agreed(self):
        existing = _make_pdf(self.dir / "existing.pdf", pages=7)
        self.highlight()

        self.doc.save_copy(str(existing))

        self.assertEqual(_highlights_on_disk(existing), [0, 1, 0])


class InterruptedSaveTests(SaveSafetyTestCase):
    def test_a_write_that_fails_halfway_leaves_the_original_exactly_as_it_was(self):
        before = _sha256(self.original)
        self.highlight()

        with mock.patch.object(fitz.Document, "save", _write_half_then_fail):
            reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.UNKNOWN)
        self.assertEqual(_sha256(self.original), before)
        self.assert_no_temp_files()
        self.assert_still_open_and_unsaved({1: 1})

    def test_a_copy_that_fails_halfway_leaves_the_file_already_there_exactly_as_it_was(self):
        existing = _make_pdf(self.dir / "existing.pdf", pages=7)
        before = (_sha256(self.original), _sha256(existing))
        self.highlight()

        with mock.patch.object(fitz.Document, "save", _write_half_then_fail):
            self.reason_of(lambda: self.doc.save_copy(str(existing)))

        self.assertEqual((_sha256(self.original), _sha256(existing)), before)
        self.assert_no_temp_files()
        self.assert_still_open_and_unsaved({1: 1})

    def test_a_written_file_that_does_not_match_the_document_is_discarded(self):
        before = _sha256(self.original)
        self.highlight()

        with mock.patch.object(fitz.Document, "save", _write_another_pdf):
            reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.VERIFY_FAILED)
        self.assertEqual(_sha256(self.original), before)
        self.assert_no_temp_files()
        self.assert_still_open_and_unsaved({1: 1})

    def test_a_move_that_fails_after_the_original_was_let_go_keeps_the_highlights_open(self):
        # The overwrite has to close the open original before it can move the
        # new file over it; a failure then must not leave a closed document.
        before = _sha256(self.original)
        self.highlight()

        with mock.patch.object(safe_write.os, "replace", side_effect=PermissionError(errno.EACCES, "denied")):
            reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.PERMISSION)
        self.assertEqual(_sha256(self.original), before)
        self.assert_no_temp_files()
        self.assert_still_open_and_unsaved({1: 1})
        # …and the next try, with nothing in the way, saves them.
        self.doc.save_overwrite()
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])

    def test_a_brief_sharing_violation_on_the_move_is_waited_out(self):
        # An antivirus scan or the indexer can hold a fresh file for a moment.
        real_replace = os.replace
        attempts = []

        def busy_twice(src, dest):
            attempts.append(dest)
            if len(attempts) <= 2:
                raise _Sharing(errno.EACCES, "in use")
            real_replace(src, dest)

        self.highlight()
        with mock.patch.object(safe_write, "_REPLACE_RETRY_SECONDS", 0), \
                mock.patch.object(safe_write.os, "replace", busy_twice):
            self.doc.save_overwrite()

        self.assertEqual(len(attempts), 3)
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])

    def test_highlights_dropped_by_the_last_resort_reload_are_not_said_to_be_open(self):
        # The move failed and the written file could not be read back either,
        # so the original is reloaded from disk without this session's
        # highlights; the message must not claim they are still there.
        before = _sha256(self.original)
        self.highlight()

        def no_streams(*args, **kwargs):
            if "stream" in kwargs:
                raise RuntimeError("code=7: cannot read")
            return _REAL_OPEN(*args, **kwargs)

        with mock.patch.object(safe_write.os, "replace", side_effect=PermissionError(errno.EACCES, "denied")), \
                mock.patch.object(fitz, "open", no_streams):
            reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.HIGHLIGHTS_LOST)
        self.assertNotIn("still open", safe_write.MESSAGES[reason])
        self.assertEqual(_sha256(self.original), before)
        self.assert_no_temp_files()
        self.assertTrue(self.doc.is_open)
        self.assertFalse(self.doc.is_dirty)
        self.assertEqual(self.doc.highlight_counts(), {})

    def test_when_not_even_the_original_reopens_the_document_is_let_go_of(self):
        before = _sha256(self.original)
        self.highlight()

        def nothing_reopens(*args, **kwargs):
            # The check before the move opens the temporary file by path;
            # every reopen after the failed move (from bytes, then the
            # original) fails.
            if "stream" in kwargs or str(args[0]) == str(self.original):
                raise RuntimeError("code=7: cannot read")
            return _REAL_OPEN(*args, **kwargs)

        with mock.patch.object(safe_write.os, "replace", side_effect=PermissionError(errno.EACCES, "denied")), \
                mock.patch.object(fitz, "open", nothing_reopens), \
                self.assertLogs("lector.features.reading.document", level="ERROR"):
            reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.UNSAVED_NOT_REOPENED)
        self.assertTrue(SaveError(reason).closes_document)
        self.assertEqual(_sha256(self.original), before)
        self.assert_no_temp_files()
        self.assertFalse(self.doc.is_open)
        self.assertFalse(self.doc.is_dirty)

    def test_an_interrupt_during_the_move_is_not_turned_into_a_save_error(self):
        self.highlight()

        with mock.patch.object(safe_write.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.doc.save_overwrite()

        self.assert_no_temp_files()
        self.assert_still_open_and_unsaved({1: 1})


_REAL_OPEN = fitz.open


class AfterTheMoveTests(SaveSafetyTestCase):
    """Once the checked file has been moved into place the save has happened:
    nothing that goes wrong afterwards may be reported as "unchanged", and
    the reader must never be left holding a closed document."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(safe_write, "_REPLACE_RETRY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def failing_reopen(self, times: int, streams_too: bool = False):
        """`fitz.open` that fails the first `times` opens of the original by
        path (only the reopen after the move opens it so; the check opens the
        temporary file), and every open from bytes if `streams_too`."""
        failures = []

        def flaky(*args, **kwargs):
            by_path = bool(args) and str(args[0]) == str(self.original)
            if (by_path and len(failures) < times) or (streams_too and "stream" in kwargs):
                failures.append(args or kwargs)
                raise RuntimeError("code=2: cannot open file")
            return _REAL_OPEN(*args, **kwargs)

        return mock.patch.object(fitz, "open", flaky)

    def assert_saved_and_reading_on(self):
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])
        self.assertTrue(self.doc.is_open)
        self.assertFalse(self.doc.is_dirty)
        self.assertIsNotNone(self.doc.render_page(0))
        self.assertEqual(self.doc.highlight_counts(), {1: 1})
        self.assert_no_temp_files()

    def test_a_folder_that_cannot_be_opened_to_flush_it_does_not_fail_a_save(self):
        # macOS: a folder can be writable but not readable (~/Public/Drop Box).
        real_open = os.open
        refused = []

        def no_folders(path, flags, *args):
            if os.path.isdir(path):
                refused.append(path)
                raise PermissionError(errno.EACCES, "denied")
            return real_open(path, flags, *args)

        self.highlight()
        copy = self.dir / "copy.pdf"
        with mock.patch.object(safe_write.os, "name", "posix"), \
                mock.patch.object(safe_write.os, "open", no_folders):
            self.doc.save_copy(str(copy))
            self.doc.highlight_word_indices(2, 0, 1)
            self.doc.save_overwrite()

        self.assertEqual(_highlights_on_disk(copy), [0, 1, 0])
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 1])
        self.assertFalse(self.doc.is_dirty)
        self.assert_no_temp_files()
        self.assertEqual(len(refused), 2)  # one refused flush per save

    def test_a_reopen_that_fails_once_after_the_move_is_retried(self):
        self.highlight()

        with self.failing_reopen(times=1):
            self.doc.save_overwrite()

        self.assert_saved_and_reading_on()
        self.assertEqual(self.doc.undo_highlight(), 1)

    def test_a_reopen_that_keeps_failing_falls_back_on_the_saved_bytes(self):
        self.highlight()

        with self.failing_reopen(times=safe_write._REPLACE_ATTEMPTS):
            self.doc.save_overwrite()

        self.assert_saved_and_reading_on()
        # …and that document saves again like any other.
        self.doc.highlight_word_indices(2, 0, 1)
        self.doc.save_overwrite()
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 1])

    def test_a_pdf_that_cannot_be_reopened_at_all_is_reported_as_saved(self):
        self.highlight()

        with self.failing_reopen(times=safe_write._REPLACE_ATTEMPTS, streams_too=True), \
                self.assertLogs("lector.features.reading.document", level="ERROR"):
            reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.SAVED_NOT_REOPENED)
        self.assertNotIn("unchanged", safe_write.MESSAGES[reason])
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])
        self.assertFalse(self.doc.is_open)
        self.assertFalse(self.doc.is_dirty)
        self.assert_no_temp_files()


class RefusedSaveTests(SaveSafetyTestCase):
    def test_a_signed_pdf_is_never_overwritten_but_can_be_copied(self):
        signed = fitz.open(self.original)
        signed.xref_set_key(signed.pdf_catalog(), "AcroForm", "<</Fields[]/SigFlags 3>>")
        signed_path = self.dir / "signed.pdf"
        signed.save(signed_path)
        signed.close()
        before = _sha256(signed_path)
        self.doc.open(str(signed_path))
        self.highlight()

        self.assertEqual(self.reason_of(self.doc.save_overwrite), safe_write.SIGNED)
        self.assertEqual(_sha256(signed_path), before)
        self.assert_still_open_and_unsaved({1: 1})

        self.doc.save_copy(str(self.dir / "signed (highlighted).pdf"))

    def test_a_copy_onto_the_open_pdf_under_another_spelling_is_refused(self):
        before = _sha256(self.original)
        self.highlight()
        (self.dir / "sub").mkdir()
        other_spelling = str(self.dir / "sub" / ".." / "report.pdf")

        reason = self.reason_of(lambda: self.doc.save_copy(other_spelling))

        self.assertEqual(reason, safe_write.IS_OPEN_DOCUMENT)
        self.assertEqual(_sha256(self.original), before)
        self.assert_still_open_and_unsaved({1: 1})

    def test_a_read_only_original_is_reported_before_anything_is_touched(self):
        before = _sha256(self.original)
        self.highlight()
        os.chmod(self.original, stat.S_IREAD)
        self.addCleanup(os.chmod, self.original, stat.S_IREAD | stat.S_IWRITE)

        reason = self.reason_of(self.doc.save_overwrite)

        self.assertEqual(reason, safe_write.READ_ONLY)
        self.assertEqual(_sha256(self.original), before)
        self.assert_no_temp_files()
        self.assert_still_open_and_unsaved({1: 1})

    def test_a_read_only_file_at_a_copys_destination_is_left_alone(self):
        existing = _make_pdf(self.dir / "existing.pdf", pages=7)
        before = _sha256(existing)
        os.chmod(existing, stat.S_IREAD)
        self.addCleanup(os.chmod, existing, stat.S_IREAD | stat.S_IWRITE)
        self.highlight()

        with mock.patch.object(safe_write, "_REPLACE_RETRY_SECONDS", 0):
            reason = self.reason_of(lambda: self.doc.save_copy(str(existing)))

        self.assertIn(reason, (safe_write.READ_ONLY, safe_write.PERMISSION))
        self.assertEqual(_sha256(existing), before)
        self.assert_no_temp_files()

    def test_a_copy_into_a_folder_that_is_gone_says_so(self):
        self.highlight()

        reason = self.reason_of(lambda: self.doc.save_copy(str(self.dir / "gone" / "copy.pdf")))

        self.assertEqual(reason, safe_write.FOLDER_MISSING)
        self.assert_still_open_and_unsaved({1: 1})


class ClassifyTests(unittest.TestCase):
    """How an OS error raised while saving becomes one of the reasons."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lector-classify-")
        self.addCleanup(self._tmp.cleanup)
        self.dest = str(_make_pdf(Path(self._tmp.name) / "a.pdf", pages=1))

    def test_each_os_error_maps_to_its_reason(self):
        cases = {
            safe_write.LOCKED: _Sharing(errno.EACCES, "in use"),
            safe_write.DISK_FULL: OSError(errno.ENOSPC, "No space left on device"),
            safe_write.DRIVE_GONE: OSError(errno.EIO, "I/O error"),
            safe_write.PERMISSION: PermissionError(errno.EACCES, "denied"),
            safe_write.FILE_MISSING: FileNotFoundError(errno.ENOENT, "gone"),
            safe_write.UNKNOWN: OSError(errno.EINVAL, "odd"),
        }
        for reason, exc in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(safe_write.classify(exc, self.dest), reason)

    def test_an_opaque_writer_failure_on_a_full_disk_is_a_full_disk(self):
        with mock.patch.object(safe_write.shutil, "disk_usage", return_value=mock.Mock(free=0)):
            reason = safe_write.classify(RuntimeError("code=1: write error"), self.dest)
        self.assertEqual(reason, safe_write.DISK_FULL)

    @unittest.skipUnless(os.name == "nt", "drive letters are Windows-only")
    def test_a_path_on_a_drive_that_is_not_there_is_a_missing_drive(self):
        free = next(d for d in "ZYXWVUTSRQ" if not os.path.exists(d + ":" + os.sep))
        reason = safe_write.classify(FileNotFoundError(errno.ENOENT, "x"), f"{free}:\\docs\\a.pdf")
        self.assertEqual(reason, safe_write.DRIVE_GONE)

    def test_every_message_says_what_to_do_or_that_nothing_was_lost(self):
        # The two exceptions say what did happen instead: the highlights
        # were dropped, or were saved.
        said_otherwise = {
            safe_write.HIGHLIGHTS_LOST: "highlights since your last save are gone",
            safe_write.UNSAVED_NOT_REOPENED: "highlights since your last save are gone",
            safe_write.SAVED_NOT_REOPENED: "highlights were saved",
        }
        for reason, message in safe_write.MESSAGES.items():
            with self.subTest(reason=reason):
                self.assertIn(said_otherwise.get(reason, "highlights are still open"), message)
                self.assertNotIn("code=", message)


class ApiSaveTests(unittest.TestCase):
    """What reaches the frontend: `perform_save`, `describe_save_target`,
    `get_save_dialog_start`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lector-api-save-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        patcher = mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.original = _make_pdf(self.dir / "report.pdf")
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)
        self.api.open_pdf(str(self.original))
        self.api._doc.highlight_word_indices(1, 0, 1)

    def test_each_failure_reaches_the_reader_as_its_sentence_and_the_file_is_unchanged(self):
        before = _sha256(self.original)
        failures = {
            safe_write.LOCKED: _Sharing(errno.EACCES, "in use"),
            safe_write.DISK_FULL: OSError(errno.ENOSPC, "No space left on device"),
            safe_write.DRIVE_GONE: OSError(errno.EIO, "I/O error"),
            safe_write.PERMISSION: PermissionError(errno.EACCES, "denied"),
        }
        for reason, exc in failures.items():
            with self.subTest(reason=reason):
                # The pre-flight open is the first thing an overwrite does.
                with mock.patch.object(safe_write, "open", side_effect=exc, create=True):
                    outcome = self.api.perform_save(store.OVERWRITE)

                self.assertEqual(outcome, {"ok": False, "error": safe_write.MESSAGES[reason]})
                self.assertEqual(_sha256(self.original), before)
                self.assertTrue(self.api._doc.is_dirty)

    def test_a_failure_while_writing_a_copy_reaches_the_reader_as_its_sentence(self):
        with mock.patch.object(
            safe_write.tempfile, "mkstemp", side_effect=OSError(errno.ENOSPC, "No space left on device")
        ):
            outcome = self.api.perform_save(store.COPY, str(self.dir / "copy.pdf"))

        self.assertEqual(outcome, {"ok": False, "error": safe_write.MESSAGES[safe_write.DISK_FULL]})
        self.assertFalse((self.dir / "copy.pdf").exists())

    def test_a_successful_save_reports_the_new_state(self):
        outcome = self.api.perform_save(store.OVERWRITE)

        self.assertTrue(outcome["ok"])
        self.assertFalse(outcome["state"]["is_dirty"])
        self.assertEqual(_highlights_on_disk(self.original), [0, 1, 0])

    def test_describe_save_target_says_whether_the_path_is_taken_and_by_what(self):
        taken = _make_pdf(self.dir / "taken.pdf", pages=1)
        cases = {
            str(self.dir / "free.pdf"): (False, False),
            str(taken): (True, False),
            str(self.original): (True, True),
        }
        for path, (exists, is_open_document) in cases.items():
            with self.subTest(path=path):
                target = self.api.describe_save_target(path)
                self.assertEqual((target["exists"], target["is_open_document"]), (exists, is_open_document))
                self.assertEqual(target["name"], os.path.basename(path))
                self.assertEqual(target["folder"], os.path.dirname(path))

    def test_the_suggested_copy_name_skips_names_that_are_taken(self):
        self.assertEqual(self.api.get_save_dialog_start()["filename"], "report (highlighted).pdf")

        _make_pdf(self.dir / "report (highlighted).pdf", pages=1)
        _make_pdf(self.dir / "report (highlighted) (2).pdf", pages=1)

        self.assertEqual(self.api.get_save_dialog_start()["filename"], "report (highlighted) (3).pdf")


if __name__ == "__main__":
    unittest.main()
