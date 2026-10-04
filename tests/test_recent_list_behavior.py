"""Behaviour of frontend/pages/recent-list.js's pure helpers: the middle
ellipsis split, column sorting and the page-count label.

Run under Node, the same way tests/test_scrollbars_behavior.py runs
scrollbars.js: the script is loaded into a context with no window or document,
which it supports (its DOM wiring only starts when a document exists). Skipped
when Node is not installed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "recent-list.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({});
vm.runInContext(
  source +
    "\nthis.api = { splitForMiddleEllipsis, sortRecentEntries, nextRecentSort," +
    " formatPageCount, DEFAULT_RECENT_SORT };",
  context
);
const api = context.api;

const LONG = "Chronicles-of-the-Enchanted-Vanguard-Seraphina-and-the-Divine-Mandate (highlighted)";
const entry = (title, opened_at, page_count) => ({ title, opened_at, page_count });
const ENTRIES = [
  entry("b report", "2026-10-03T10:00:00", 4),
  entry("A guide", "2026-10-03T12:30:00", 357),
  entry("c notes", "2026-10-01T09:00:00", 1),
  entry("Report 10", "2026-10-02T08:00:00", 2),
  entry("Report 9", "2026-10-02T07:00:00", 2),
];
const titles = (list) => list.map((e) => e.title);

const results = {};
const check = (name, fn) => {
  try {
    results[name] = fn() === true;
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};

// Middle ellipsis.
check("a short name is not split", () => {
  const { head, tail } = api.splitForMiddleEllipsis("JAVA OOP certificate");
  return head === "JAVA OOP certificate" && tail === "";
});

check("the distinguishing tail of a long name stays whole", () => {
  const { tail } = api.splitForMiddleEllipsis(LONG);
  return tail === "Mandate (highlighted)";
});

check("head + tail is always the original name", () => {
  return [LONG, "1.Artificial Intelligence vs Augmented Intelligence_EN",
          "x".repeat(80), "a".repeat(31)].every((t) => {
    const { head, tail } = api.splitForMiddleEllipsis(t);
    return head + tail === t;
  });
});

check("a long name with no word break keeps a fixed-length tail", () => {
  const { head, tail } = api.splitForMiddleEllipsis("x".repeat(80));
  return tail.length === 12 && head.length === 68;
});

check("the tail never starts with the separator it was split on", () => {
  const { head, tail } = api.splitForMiddleEllipsis("1.Artificial Intelligence vs Augmented Intelligence_EN");
  return !/^[\s\-_.]/.test(tail) && /[\s\-_.]$/.test(head);
});

// Sorting.
check("default sort is Last active, newest first", () =>
  api.DEFAULT_RECENT_SORT.key === "lastActive" && api.DEFAULT_RECENT_SORT.direction === "desc");

check("Last active descending puts the newest first", () =>
  titles(api.sortRecentEntries(ENTRIES, { key: "lastActive", direction: "desc" }))
    .join("|") === "A guide|b report|Report 10|Report 9|c notes");

check("Name ascending ignores case and orders numbers naturally", () =>
  titles(api.sortRecentEntries(ENTRIES, { key: "name", direction: "asc" }))
    .join("|") === "A guide|b report|c notes|Report 9|Report 10");

check("Pages ascending sorts numerically", () =>
  api.sortRecentEntries(ENTRIES, { key: "pages", direction: "asc" })
    .map((e) => e.page_count).join(",") === "1,2,2,4,357");

check("sorting returns a new array and leaves the input order alone", () => {
  const before = titles(ENTRIES).join("|");
  const sorted = api.sortRecentEntries(ENTRIES, { key: "name", direction: "asc" });
  return sorted !== ENTRIES && titles(ENTRIES).join("|") === before;
});

check("clicking the active column flips its direction", () => {
  const next = api.nextRecentSort({ key: "lastActive", direction: "desc" }, "lastActive");
  return next.key === "lastActive" && next.direction === "asc";
});

check("a newly chosen column starts at its own first direction", () => {
  const name = api.nextRecentSort({ key: "lastActive", direction: "desc" }, "name");
  const back = api.nextRecentSort(name, "lastActive");
  return name.direction === "asc" && back.direction === "desc";
});

// Page count label.
check("one page is singular", () => api.formatPageCount(1) === "1 page");
check("many pages are plural", () => api.formatPageCount(357) === "357 pages");
check("an unreadable file shows a dash", () => api.formatPageCount(0) === "—");

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class RecentListHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 15)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
