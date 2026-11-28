"""The one unsaved flag (`PdfDocument.is_dirty`) against real PDFs.

What it is held to (docs/ARCHITECTURE.md § "Unsaved state: one flag";
docs/DESIGN_SYSTEM.md § "Unsaved changes"):

* set by every change Save would write: highlights added, recoloured,
  restyled or removed, and page changes applied with Organise's Done;
* not set by anything else: page, zoom, a file tool's snapshot;
* cleared by Save and Save a copy, and by undo or redo landing exactly on
  the state last saved; set again by moving away from it;
* a save point that can no longer be reached (a new edit cleared the redo
  branch it was on, history trimmed past it) leaves the document unsaved
  until the next save.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pdf_support  # noqa: E402
from lector.features.reading import page_changes, page_plan  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.reading.page_plan import DOC, PlanPage  # noqa: E402


def _plan(*pages) -> page_plan.Plan:
    return tuple(PlanPage(DOC, *(p if isinstance(p, tuple) else (p,))) for p in pages)


class _DocCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.doc = PdfDocument()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.doc.close)
        self.doc.open(str(pdf_support.multi_page(self.folder, pages=4)))

    def highlight(self, page: int = 0, color: str = "yellow") -> int:
        return self.doc.highlight_word_indices(page, 0, 1, color)


class SetAndClearTests(_DocCase):
    def test_a_freshly_opened_pdf_is_saved(self):
        self.assertFalse(self.doc.is_dirty)

    def test_a_highlight_sets_it_and_save_clears_it(self):
        self.highlight()
        self.assertTrue(self.doc.is_dirty)
        self.doc.save_overwrite()
        self.assertFalse(self.doc.is_dirty)

    def test_save_a_copy_clears_it(self):
        self.highlight()
        self.doc.save_copy(str(self.folder / "copy.pdf"))
        self.assertFalse(self.doc.is_dirty)

    def test_recolour_restyle_and_remove_each_set_it(self):
        xref = self.highlight()
        changes = {
            "recolour": lambda: self.doc.set_highlight_color(0, xref, "green"),
            "restyle": lambda: self.doc.set_highlight_appearance(0, xref, opacity=0.3),
            "remove": lambda: self.doc.remove_highlight(0, xref),
        }
        for name, change in changes.items():
            with self.subTest(name):
                self.doc.save_overwrite()
                self.assertFalse(self.doc.is_dirty)
                self.assertTrue(change())
                self.assertTrue(self.doc.is_dirty)

    def test_page_zoom_and_a_tool_snapshot_leave_it_alone(self):
        self.doc.go_to_page(2)
        self.doc.set_zoom(1.5)
        self.doc.zoom_in()
        self.doc.snapshot()
        self.assertFalse(self.doc.is_dirty)

    def test_organise_done_sets_it_and_one_undo_takes_it_back(self):
        self.assertTrue(self.doc.apply_page_plan(_plan(3, 2, (1, 90), 0)))
        self.assertTrue(self.doc.is_dirty)
        self.doc.undo_highlight()
        self.assertFalse(self.doc.is_dirty)
        self.doc.redo_highlight()
        self.assertTrue(self.doc.is_dirty)


class UndoToTheSavedStateTests(_DocCase):
    def test_undo_after_a_save_sets_it_and_redo_clears_it(self):
        self.highlight()
        self.doc.save_overwrite()
        self.doc.undo_highlight()
        self.assertTrue(self.doc.is_dirty)
        self.doc.redo_highlight()
        self.assertFalse(self.doc.is_dirty)

    def test_undoing_every_edit_since_opening_is_saved_again(self):
        xref = self.highlight()
        self.doc.set_highlight_color(0, xref, "green")
        self.doc.undo_highlight()
        self.assertTrue(self.doc.is_dirty)
        self.doc.undo_highlight()
        self.assertFalse(self.doc.is_dirty)

    def test_a_save_point_on_a_cleared_redo_branch_is_gone(self):
        self.highlight(0)
        self.doc.save_overwrite()
        self.doc.undo_highlight()
        self.highlight(1)  # clears the redo branch the saved state was on
        self.assertTrue(self.doc.is_dirty)
        self.doc.undo_highlight()
        self.assertTrue(self.doc.is_dirty)  # nothing on screen is what is saved
        self.doc.save_overwrite()
        self.assertFalse(self.doc.is_dirty)

    def test_saved_highlights_and_page_changes_then_undo_and_redo(self):
        self.highlight()
        self.doc.apply_page_plan(_plan(1, 0, 2, 3))
        self.doc.save_overwrite()
        self.assertFalse(self.doc.is_dirty)
        self.doc.undo_highlight()  # the page change
        self.assertTrue(self.doc.is_dirty)
        self.doc.redo_highlight()
        self.assertFalse(self.doc.is_dirty)

    def test_a_slider_drag_back_to_where_it_began_is_saved(self):
        xref = self.highlight()
        self.doc.save_overwrite()
        start = self.doc._load_highlight(0, xref)[1].opacity
        self.doc.set_highlight_appearance(0, xref, opacity=0.3, gesture="g1")
        self.assertTrue(self.doc.is_dirty)
        self.doc.set_highlight_appearance(0, xref, opacity=start, gesture="g1")
        self.assertFalse(self.doc.is_dirty)

    def test_a_drag_that_rewrites_the_saved_step_leaves_it_unsaved(self):
        xref = self.highlight()
        self.doc.set_highlight_appearance(0, xref, opacity=0.3, gesture="g1")
        self.doc.save_overwrite()
        self.doc.set_highlight_appearance(0, xref, opacity=0.5, gesture="g1")
        self.assertTrue(self.doc.is_dirty)
        self.doc.undo_highlight()  # before the whole drag: not what was saved
        self.assertTrue(self.doc.is_dirty)


class TrimTests(_DocCase):
    def test_trimming_past_the_save_point_leaves_it_unsaved(self):
        self.doc.save_overwrite()  # saved at the very start
        for _ in range(page_changes.MAX_PAGE_CHANGES + 1):
            self.doc.apply_page_plan(_plan(1, 0, 2, 3))
        self.assertTrue(self.doc.take_history_trimmed())
        while self.doc.can_undo:
            self.doc.undo_highlight()
            self.assertTrue(self.doc.is_dirty)

    def test_trimming_below_the_save_point_keeps_it(self):
        for _ in range(page_changes.MAX_PAGE_CHANGES):
            self.doc.apply_page_plan(_plan(1, 0, 2, 3))
        self.doc.save_overwrite()
        self.doc.apply_page_plan(_plan(1, 0, 2, 3))  # trims the oldest
        self.assertTrue(self.doc.take_history_trimmed())
        self.assertTrue(self.doc.is_dirty)
        self.doc.undo_highlight()
        self.assertFalse(self.doc.is_dirty)


class SavedAtAfterTrimTests(unittest.TestCase):
    def test_moves_down_by_what_left_the_undo_stack(self):
        self.assertEqual(page_changes.saved_at_after_trim(3, 4, [1, 2], []), 1)

    def test_gone_when_it_was_below_the_cut(self):
        self.assertIsNone(page_changes.saved_at_after_trim(1, 4, [1, 2], []))

    def test_gone_when_beyond_the_redo_stacks_new_end(self):
        self.assertIsNone(page_changes.saved_at_after_trim(5, 2, [1, 2], [3]))
        self.assertEqual(page_changes.saved_at_after_trim(3, 2, [1, 2], [3]), 3)

    def test_none_stays_none(self):
        self.assertIsNone(page_changes.saved_at_after_trim(None, 2, [1], []))


if __name__ == "__main__":
    unittest.main()
