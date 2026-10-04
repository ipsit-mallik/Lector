"""Backend for the Recent list view's table (docs/DESIGN_SYSTEM.md, "Recent
list view"): the metadata title and raw timestamp `list_recent()` hands the
Name and Last active columns, the removal toast's Undo, and the row menu's
"Show in folder".

Every test writes to a temporary settings file, never the real one.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402  # PyMuPDF: builds the throwaway PDFs these tests list

from lector import api as api_module  # noqa: E402
from lector.features.home import recent  # noqa: E402
from lector.features.settings import store  # noqa: E402


class _TempSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def _make_pdf(self, name: str, title: str | None = None, pages: int = 1) -> str:
        path = str(Path(self.dir.name) / name)
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        if title is not None:
            doc.set_metadata({"title": title})
        doc.save(path)
        doc.close()
        return path


class RemoveAndRestoreTests(_TempSettings):
    def test_remove_returns_the_entry_and_the_index_it_sat_at(self):
        for path in ("/docs/c.pdf", "/docs/b.pdf", "/docs/a.pdf"):
            store.add_recent_file(path)

        index, entry = store.remove_recent_file("/docs/b.pdf")

        self.assertEqual(index, 1)
        self.assertEqual(entry["path"], "/docs/b.pdf")

    def test_remove_returns_none_when_the_path_is_not_listed(self):
        store.add_recent_file("/docs/a.pdf")

        self.assertIsNone(store.remove_recent_file("/docs/missing.pdf"))

    def test_restore_puts_the_entry_back_where_it_was_with_its_position(self):
        for path in ("/docs/c.pdf", "/docs/b.pdf", "/docs/a.pdf"):
            store.add_recent_file(path)
        store.set_document_position("/docs/b.pdf", 41, store.STRIP, 1.5)
        before = store.get_recent_files()

        store.restore_recent_file(*store.remove_recent_file("/docs/b.pdf"))

        self.assertEqual(store.get_recent_files(), before)
        self.assertEqual(
            store.get_document_position("/docs/b.pdf"),
            {"page_index": 41, "layout_mode": store.STRIP, "zoom": 1.5},
        )

    def test_restore_reports_a_file_that_was_reopened_and_changes_nothing(self):
        store.add_recent_file("/docs/b.pdf")
        store.add_recent_file("/docs/a.pdf")
        removed = store.remove_recent_file("/docs/b.pdf")
        store.add_recent_file("/docs/b.pdf")
        reopened = store.get_recent_files()

        status = store.restore_recent_file(*removed)

        self.assertEqual(status, store.ALREADY_LISTED)
        self.assertEqual(store.get_recent_files(), reopened)

    def test_restore_declines_rather_than_push_the_oldest_entry_out_when_full(self):
        store.add_recent_file("/docs/removed.pdf")
        removed = store.remove_recent_file("/docs/removed.pdf")
        for i in range(store.RECENT_FILES_CAP):
            store.add_recent_file(f"/docs/{i:02d}.pdf")
        before = store.get_recent_files()

        status = store.restore_recent_file(*removed)

        self.assertEqual(status, store.LIST_FULL)
        self.assertEqual(store.get_recent_files(), before)

    def test_restore_keeps_position_and_malformed_items_in_a_hand_edited_file(self):
        store.add_recent_file("/docs/c.pdf")
        store.add_recent_file("/docs/b.pdf")
        store.add_recent_file("/docs/a.pdf")
        data = store._load()
        data["recent_files"].insert(0, "not-an-entry")  # shifts every raw index by one
        store._save(data)
        removed = store.remove_recent_file("/docs/b.pdf")

        status = store.restore_recent_file(*removed)

        self.assertEqual(status, store.RESTORED)
        self.assertEqual(
            [e["path"] for e in store.get_recent_files()],
            ["/docs/a.pdf", "/docs/b.pdf", "/docs/c.pdf"],
        )
        self.assertEqual(store._load()["recent_files"][0], "not-an-entry")


class ListRecentFieldsTests(_TempSettings):
    def test_title_comes_from_the_pdf_metadata(self):
        store.add_recent_file(self._make_pdf("dbms_cert.pdf", title="Data Base Management System"))

        [entry] = recent.list_recent()

        self.assertEqual(entry["title"], "Data Base Management System")
        self.assertEqual(entry["name"], "dbms_cert")

    def test_title_falls_back_to_the_filename_when_metadata_is_missing_or_blank(self):
        store.add_recent_file(self._make_pdf("untitled.pdf"))
        store.add_recent_file(self._make_pdf("blank title.pdf", title="   "))

        titles = [e["title"] for e in recent.list_recent()]

        self.assertEqual(titles, ["blank title", "untitled"])

    def test_title_is_collapsed_to_a_single_line(self):
        store.add_recent_file(self._make_pdf("multi.pdf", title="Annual\r\n Report\n 2026"))

        [entry] = recent.list_recent()

        self.assertEqual(entry["title"], "Annual Report 2026")

    def test_title_falls_back_to_the_filename_for_an_unreadable_file(self):
        store.add_recent_file("/docs/gone.pdf")

        [entry] = recent.list_recent()

        self.assertEqual(entry["title"], "gone")
        self.assertEqual(entry["page_count"], 0)

    def test_entries_carry_the_raw_timestamp_for_sorting_and_no_progress(self):
        store.add_recent_file(self._make_pdf("a.pdf", pages=3))

        [entry] = recent.list_recent()

        self.assertEqual(entry["opened_at"], store.get_recent_files()[0]["opened_at"])
        self.assertEqual(entry["relative_time"], "just now")
        self.assertEqual(entry["page_count"], 3)
        self.assertNotIn("progress_percent", entry)


class ApiUndoAndShowInFolderTests(_TempSettings):
    def setUp(self):
        super().setUp()
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)

    def test_undo_restores_the_last_removed_entry(self):
        for path in ("/docs/c.pdf", "/docs/b.pdf", "/docs/a.pdf"):
            store.add_recent_file(path)
        self.api.remove_recent_file("/docs/b.pdf")

        result = self.api.undo_remove_recent_file()

        self.assertEqual(result["status"], store.RESTORED)
        self.assertEqual(
            [e["path"] for e in result["entries"]],
            ["/docs/a.pdf", "/docs/b.pdf", "/docs/c.pdf"],
        )

    def test_removing_a_path_that_is_not_listed_reports_it_and_keeps_the_earlier_undo(self):
        store.add_recent_file("/docs/b.pdf")
        store.add_recent_file("/docs/a.pdf")
        self.assertTrue(self.api.remove_recent_file("/docs/a.pdf")["removed"])

        stale = self.api.remove_recent_file("/docs/a.pdf")  # stale view: already gone
        result = self.api.undo_remove_recent_file()

        self.assertFalse(stale["removed"])
        self.assertEqual([e["path"] for e in result["entries"]], ["/docs/a.pdf", "/docs/b.pdf"])

    def test_undo_is_one_level_and_a_no_op_with_nothing_to_undo(self):
        store.add_recent_file("/docs/b.pdf")
        store.add_recent_file("/docs/a.pdf")
        self.api.remove_recent_file("/docs/a.pdf")
        self.api.undo_remove_recent_file()

        result = self.api.undo_remove_recent_file()

        self.assertIsNone(result["status"])
        self.assertEqual([e["path"] for e in result["entries"]], ["/docs/a.pdf", "/docs/b.pdf"])

    def test_undo_reports_a_full_list(self):
        store.add_recent_file("/docs/removed.pdf")
        self.api.remove_recent_file("/docs/removed.pdf")
        for i in range(store.RECENT_FILES_CAP):
            store.add_recent_file(f"/docs/{i:02d}.pdf")

        result = self.api.undo_remove_recent_file()

        self.assertEqual(result["status"], store.LIST_FULL)
        self.assertEqual(len(result["entries"]), store.RECENT_FILES_CAP)

    def test_show_in_folder_reveals_a_listed_file_that_exists(self):
        path = self._make_pdf("a.pdf")
        store.add_recent_file(path)
        with mock.patch.object(recent, "reveal_in_file_manager") as reveal:
            result = self.api.show_in_folder(path)

        self.assertEqual(result, {"error": None})
        reveal.assert_called_once_with(path)

    def test_show_in_folder_refuses_a_path_not_in_recent(self):
        path = self._make_pdf("a.pdf")
        with mock.patch.object(recent, "reveal_in_file_manager") as reveal:
            result = self.api.show_in_folder(path)

        self.assertIsNotNone(result["error"])
        reveal.assert_not_called()

    def test_show_in_folder_reports_a_file_that_is_gone(self):
        store.add_recent_file("/docs/gone.pdf")
        with mock.patch.object(recent, "reveal_in_file_manager") as reveal:
            result = self.api.show_in_folder("/docs/gone.pdf")

        self.assertEqual(result["error"], "That file has been moved or deleted.")
        reveal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
