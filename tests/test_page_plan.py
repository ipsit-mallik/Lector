"""Organise pages' draft (features/reading/page_plan.py): rotate, delete,
move by drag and by Ctrl+arrows, insert, and Organise's own Undo/Redo.

Plans are pure data; what applying one does to a PDF is in
tests/test_page_changes.py.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.reading import page_plan  # noqa: E402
from lector.features.reading.page_plan import DOC, PlanError, PlanPage  # noqa: E402


def _order(plan) -> list[str]:
    """The plan as page keys, "doc:0" written "0" and an inserted page
    "insert-0:1" written "i1", turns after an @."""
    names = []
    for page in plan:
        name = str(page.index) if page.source == DOC else f"i{page.index}"
        names.append(f"{name}@{page.turn}" if page.turn else name)
    return names


class RotateTests(unittest.TestCase):
    def test_rotate_turns_only_the_chosen_pages_and_wraps(self):
        plan = page_plan.initial(4)
        plan = page_plan.rotate(plan, [1, 3], 90)
        self.assertEqual(_order(plan), ["0", "1@90", "2", "3@90"])
        plan = page_plan.rotate(plan, [1], -90)
        plan = page_plan.rotate(plan, [3], 90)
        plan = page_plan.rotate(plan, [3], 90)
        plan = page_plan.rotate(plan, [3], 90)
        self.assertEqual(_order(plan), ["0", "1", "2", "3"])  # back to unturned
        self.assertTrue(page_plan.is_unchanged(plan, 4))

    def test_left_from_unturned_is_270(self):
        self.assertEqual(_order(page_plan.rotate(page_plan.initial(2), [0], -90)), ["0@270", "1"])

    def test_bad_positions_and_angles_are_refused(self):
        plan = page_plan.initial(3)
        for positions in ([], [3], [-1], [0, 0], [True], ["1"]):
            with self.subTest(positions=positions), self.assertRaises(PlanError):
                page_plan.rotate(plan, positions, 90)
        with self.assertRaises(PlanError):
            page_plan.rotate(plan, [0], 45)


class DeleteTests(unittest.TestCase):
    def test_delete_removes_scattered_pages(self):
        plan = page_plan.delete(page_plan.initial(6), [4, 1, 5])
        self.assertEqual(_order(plan), ["0", "2", "3"])

    def test_deleting_every_page_is_refused(self):
        plan = page_plan.initial(3)
        with self.assertRaises(PlanError) as caught:
            page_plan.delete(plan, [0, 1, 2])
        self.assertEqual(caught.exception.reason, page_plan.EVERY_PAGE)
        self.assertEqual(len(page_plan.delete(plan, [0, 1])), 1)  # one left is fine


class MoveTests(unittest.TestCase):
    def test_a_block_moves_in_its_own_order(self):
        # Drag pages 5 and 2 (selected in that order or not) before page 1:
        # they land as 2, 5 — the plan's order, not the click order.
        plan, selection = page_plan.move(page_plan.initial(6), [5, 2], 1)
        self.assertEqual(_order(plan), ["0", "2", "5", "1", "3", "4"])
        self.assertEqual(selection, (1, 2))

    def test_moving_to_the_end_and_to_the_start(self):
        plan, selection = page_plan.move(page_plan.initial(4), [0, 1], 4)
        self.assertEqual(_order(plan), ["2", "3", "0", "1"])
        self.assertEqual(selection, (2, 3))
        plan, selection = page_plan.move(page_plan.initial(4), [3], 0)
        self.assertEqual(_order(plan), ["3", "0", "1", "2"])
        self.assertEqual(selection, (0,))

    def test_dropping_a_block_on_itself_changes_nothing(self):
        start = page_plan.initial(5)
        for gap in (1, 2, 3, 4):  # inside the block or right at its edges
            with self.subTest(gap=gap):
                plan, selection = page_plan.move(start, [1, 2, 3], gap)
                self.assertEqual(plan, start)
                self.assertEqual(selection, (1, 2, 3))

    def test_a_scattered_block_dropped_inside_itself_is_gathered(self):
        plan, selection = page_plan.move(page_plan.initial(5), [0, 2, 4], 2)
        self.assertEqual(_order(plan), ["1", "0", "2", "4", "3"])
        self.assertEqual(selection, (1, 2, 3))

    def test_bad_gaps_are_refused(self):
        for gap in (-1, 6, None, 1.5, True):
            with self.subTest(gap=gap), self.assertRaises(PlanError):
                page_plan.move(page_plan.initial(5), [0], gap)

    def test_turns_travel_with_their_page(self):
        plan = page_plan.rotate(page_plan.initial(3), [2], 180)
        plan, _ = page_plan.move(plan, [2], 0)
        self.assertEqual(_order(plan), ["2@180", "0", "1"])


class ShiftTests(unittest.TestCase):
    def test_ctrl_arrows_move_a_block_one_place(self):
        plan, selection = page_plan.shift(page_plan.initial(6), [2, 3], 1)
        self.assertEqual(_order(plan), ["0", "1", "4", "2", "3", "5"])
        self.assertEqual(selection, (3, 4))
        plan, selection = page_plan.shift(plan, list(selection), -1)
        self.assertEqual(_order(plan), ["0", "1", "2", "3", "4", "5"])
        self.assertEqual(selection, (2, 3))

    def test_at_either_end_the_block_stays_put(self):
        start = page_plan.initial(4)
        self.assertEqual(page_plan.shift(start, [0, 1], -1), (start, (0, 1)))
        self.assertEqual(page_plan.shift(start, [3], 1), (start, (3,)))

    def test_a_scattered_selection_is_gathered_by_one_step(self):
        plan, selection = page_plan.shift(page_plan.initial(6), [1, 4], -1)
        self.assertEqual(_order(plan), ["1", "4", "0", "2", "3", "5"])
        self.assertEqual(selection, (0, 1))


class InsertTests(unittest.TestCase):
    def test_insert_before_after_and_at_the_end(self):
        start = page_plan.initial(4)
        cases = {
            "before the selection (page 2)": (1, ["0", "i0", "i1", "1", "2", "3"], (1, 2)),
            "after the selection (page 3)": (3, ["0", "1", "2", "i0", "i1", "3"], (3, 4)),
            "at the end": (4, ["0", "1", "2", "3", "i0", "i1"], (4, 5)),
        }
        for name, (gap, order, selection) in cases.items():
            with self.subTest(name):
                plan, new = page_plan.insert(start, gap, "insert-0", 2)
                self.assertEqual(_order(plan), order)
                self.assertEqual(new, selection)
                self.assertTrue(all(plan[i].is_new for i in new))

    def test_an_inserted_page_has_its_own_key(self):
        plan, _ = page_plan.insert(page_plan.initial(1), 1, "insert-0", 1)
        self.assertEqual([p.key for p in plan], ["doc:0", "insert-0:0"])
        self.assertEqual(plan[1], PlanPage("insert-0", 0))

    def test_bad_inserts_are_refused(self):
        start = page_plan.initial(2)
        for gap, source, count in ((0, DOC, 1), (0, "insert-0", 0), (5, "insert-0", 1), (0, "insert-0", True)):
            with self.subTest(gap=gap, source=source, count=count), self.assertRaises(PlanError):
                page_plan.insert(start, gap, source, count)
        plan, _ = page_plan.insert(start, 0, "insert-0", 1)
        with self.assertRaises(PlanError):  # the same file twice
            page_plan.insert(plan, 0, "insert-0", 1)


class HistoryTests(unittest.TestCase):
    def test_undo_and_redo_walk_the_plans(self):
        start = page_plan.initial(3)
        history = page_plan.PlanHistory(start)
        self.assertFalse(history.can_undo)
        rotated = page_plan.rotate(start, [0], 90)
        self.assertTrue(history.push(rotated))
        deleted = page_plan.delete(rotated, [1])
        history.push(deleted)
        self.assertEqual(history.steps, 2)
        self.assertEqual(history.undo(), rotated)
        self.assertEqual(history.undo(), start)
        self.assertIsNone(history.undo())
        self.assertEqual(history.redo(), rotated)
        self.assertEqual(history.steps, 1)

    def test_a_new_action_drops_redo_and_a_no_op_records_nothing(self):
        start = page_plan.initial(3)
        history = page_plan.PlanHistory(start)
        history.push(page_plan.rotate(start, [0], 90))
        history.undo()
        self.assertTrue(history.can_redo)
        moved, _ = page_plan.move(start, [2], 0)
        history.push(moved)
        self.assertFalse(history.can_redo)
        self.assertFalse(history.push(moved))
        self.assertEqual(history.steps, 1)


if __name__ == "__main__":
    unittest.main()
