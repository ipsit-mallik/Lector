"""Insert pages' placement logic (frontend/pages/organise-insert-places.js),
run under Node: which places a selection allows and what each is called, the
default, the preview strip's items, the caption, and when the segmented
control falls back from one row to a 2×2 grid.

Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "organise-insert-places.js"

RUNNER = r"""
const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const P = vm.runInContext("OrganiseInsertPlaces", context);
const calls = JSON.parse(process.argv[2]);
const out = calls.map(([fn, ...args]) => {
  if (fn === "preview") args[3] = new Set(args[3]);
  return P[fn](...args);
});
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class InsertPlacesTests(unittest.TestCase):
    def call(self, *calls):
        done = subprocess.run(
            ["node", "-e", RUNNER, str(SCRIPT), json.dumps(calls)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return json.loads(done.stdout)

    def one(self, *call):
        return self.call(list(call))[0]

    # ---- places -------------------------------------------------------------

    def test_with_a_selection_all_four_places_in_document_order(self):
        # Pages 4–6 of 12 selected (positions are 0-based).
        places = self.one("places", [3, 4, 5], 12)
        self.assertEqual(
            [(p["value"], p["label"], p["gap"]) for p in places],
            [("start", "At the start", 0), ("before", "Before page 4", 3),
             ("after", "After page 6", 6), ("end", "At the end", 12)],
        )

    def test_a_scattered_selection_uses_its_first_and_last_page(self):
        places = self.one("places", [8, 1, 4], 9)  # in any order
        self.assertEqual([p["label"] for p in places],
                         ["At the start", "Before page 2", "After page 9", "At the end"])
        self.assertEqual([p["gap"] for p in places], [0, 1, 9, 9])

    def test_without_a_selection_only_the_start_and_the_end(self):
        places = self.one("places", [], 9)
        self.assertEqual([(p["value"], p["label"], p["gap"]) for p in places],
                         [("start", "At the start", 0), ("end", "At the end", 9)])

    def test_long_page_numbers_are_written_in_full(self):
        places = self.one("places", [1249], 3000)
        self.assertEqual([p["label"] for p in places],
                         ["At the start", "Before page 1250", "After page 1250", "At the end"])

    def test_the_default_is_after_the_selection_else_the_end(self):
        self.assertEqual(self.call(["defaultPlace", [3]], ["defaultPlace", []]), ["after", "end"])

    # ---- preview ------------------------------------------------------------

    def items(self, total, gap, count, selected=()):
        return self.one("preview", total, gap, count, list(selected))

    @staticmethod
    def text(items):
        out = []
        for item in items:
            if item["kind"] == "page":
                out.append(f"{item['page']}*" if item["selected"] else str(item["page"]))
            elif item["kind"] == "gap":
                out.append("…")
            else:
                out.append(f"+{item['count']}")
        return " ".join(out)

    def test_after_page_2_of_9(self):
        self.assertEqual(self.text(self.items(9, 2, 1, [1])), "1 2* +1 3 … 9")

    def test_after_page_157_of_200(self):
        self.assertEqual(self.text(self.items(200, 157, 12, [156])), "1 … 157* +12 158 … 200")

    def test_before_page_157_of_200(self):
        self.assertEqual(self.text(self.items(200, 156, 12, [156])), "1 … 156 +12 157* … 200")

    def test_at_the_start_and_at_the_end(self):
        self.assertEqual(self.text(self.items(200, 0, 3)), "+3 1 … 200")
        self.assertEqual(self.text(self.items(200, 200, 3)), "1 … 200 +3")

    def test_a_single_skipped_page_is_shown_rather_than_dots(self):
        # 1 _2_ 3 +N 4 _5_ 6: one page between is the page itself, never "…".
        self.assertEqual(self.text(self.items(6, 3, 2)), "1 2 3 +2 4 5 6")

    def test_small_documents(self):
        self.assertEqual(self.text(self.items(1, 0, 4)), "+4 1")
        self.assertEqual(self.text(self.items(1, 1, 4)), "1 +4")
        self.assertEqual(self.text(self.items(2, 1, 4, [0])), "1* +4 2")

    def test_never_more_than_seven_items_and_one_inserted_block(self):
        for total in (1, 2, 3, 5, 9, 40, 200, 5000):
            for gap in sorted({0, 1, 2, total // 2, total - 1, total}):
                if not 0 <= gap <= total:
                    continue
                with self.subTest(total=total, gap=gap):
                    items = self.items(total, gap, 12)
                    self.assertLessEqual(len(items), 7)
                    blocks = [i for i in items if i["kind"] == "insert"]
                    self.assertEqual(blocks, [{"kind": "insert", "count": 12}])
                    pages = [i["page"] for i in items if i["kind"] == "page"]
                    self.assertEqual(pages, sorted(set(pages)))
                    self.assertIn(1, pages)
                    self.assertIn(total, pages)
                    # The block sits between pages `gap` and `gap + 1`.
                    at = items.index(blocks[0])
                    before = [i["page"] for i in items[:at] if i["kind"] == "page"]
                    self.assertEqual(before[-1] if before else 0, gap)

    # ---- caption ------------------------------------------------------------

    def test_caption_for_one_page_and_for_several(self):
        self.assertEqual(
            self.call(["caption", 2, 1, 9], ["caption", 2, 12, 9], ["caption", 0, 1, 1], ["caption", 9, 3, 9]),
            ["Becomes page 3 of 10", "Become pages 3–14 of 21", "Becomes page 1 of 2", "Become pages 10–12 of 12"],
        )

    # ---- one row, or 2×2 ----------------------------------------------------

    def test_four_fit_in_one_row(self):
        # 456px inside the track, 3px gaps, 18px of padding and border each.
        self.assertEqual(self.one("columns", [78, 92, 86, 70], 456, 3, 18), 4)

    def test_long_labels_fall_back_to_two_by_two(self):
        self.assertEqual(self.one("columns", [78, 120, 118, 70], 456, 3, 18), 2)

    def test_one_column_only_when_even_two_dont_fit(self):
        self.assertEqual(self.one("columns", [78, 260, 118, 70], 456, 3, 18), 1)

    def test_two_options_stay_side_by_side_when_they_fit(self):
        self.assertEqual(self.one("columns", [78, 70], 456, 3, 18), 2)
        self.assertEqual(self.one("columns", [300, 70], 456, 3, 18), 1)

    def test_a_label_exactly_filling_its_cell_fits(self):
        # (456 - 9) / 4 = 111.75 per cell, less 18 = 93.75 for the label.
        self.assertEqual(self.one("columns", [93, 93, 93, 93], 456, 3, 18), 4)
        self.assertEqual(self.one("columns", [94, 93, 93, 93], 456, 3, 18), 2)


if __name__ == "__main__":
    unittest.main()
