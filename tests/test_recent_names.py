"""A file on the library pages is called by its file name (without ".pdf"),
never the PDF's Title metadata: both views of Recent, Favorites, every label
and tooltip on a row or card, the "⋯" menu, sorting by Name and the search.

The bug this holds shut: the list view named rows by the metadata title
("Scanned Document", "Scanned PDF" for two different files, "Signature")
while the grid view and the reader showed the file names.

recent-list.js, recent-card.js and library-search.js run under Node with a
stand-in DOM. Each entry is given a stray `title` as well (what recent.py used
to send), so a display path that still read it would show up here. Skipped
when Node is not installed.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "frontend" / "pages"
SCRIPTS = [PAGES / "recent-list.js", PAGES / "recent-card.js", PAGES / "library-search.js"]
NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function fakeElement(tag) {
  const classes = new Set();
  const el = {
    tagName: tag.toUpperCase(), children: [], parentElement: null, textContent: "", title: "",
    dataset: {}, attrs: {}, listeners: {}, tabIndex: 0, type: "", scrollWidth: 300, clientWidth: 100,
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(" "); },
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)), remove: (...c) => c.forEach((x) => classes.delete(x)),
      contains: (c) => classes.has(c), toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
    },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; },
    append(...kids) { kids.forEach((k) => this.appendChild(k)); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    fire(type) { (this.listeners[type] || []).forEach((fn) => fn({ stopPropagation() {}, preventDefault() {}, target: this })); },
    querySelectorAll(selector) {
      const wanted = selector.split(",").map((s) => s.trim().split(".").filter(Boolean));
      const found = [];
      const walk = (node) => node.children.forEach((c) => {
        if (wanted.some((cls) => cls.every((x) => c.classList.contains(x)))) found.push(c);
        walk(c);
      });
      walk(this);
      return found;
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
  };
  return el;
}

const menus = [];
const context = vm.createContext({
  document: { createElement: fakeElement },
  console, Array, String, Set,
  mountThumbnail: () => {}, mountIcon: () => {}, mountIcons: () => {},
  homeSection: "recent", toggleFavorite: () => {}, removeEntry: () => {}, openPath: () => {},
  recentCardMenuHandlers: { onOpen() {}, onShowInFolder() {}, onTool() {}, onRemove() {} },
  buildPopupMenuItem: ({ label }) => { const item = fakeElement("button"); item.textContent = label; return item; },
  buildPopupMenuSeparator: () => fakeElement("div"),
  openPopupMenu: (options) => menus.push(options.label),
  isPopupMenuOpen: () => false, closePopupMenu: () => {},
});
for (const file of process.argv.slice(2)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
const run = (expr) => vm.runInContext(expr, context);

// Everything a person can read or hear on an element and its children.
function shown(el, out = []) {
  for (const value of [el.textContent, el.title, el.dataset.tooltip, el.attrs["aria-label"]]) {
    if (value) out.push(value);
  }
  el.children.forEach((c) => shown(c, out));
  return out;
}

const entries = [
  { path: "C:/docs/PublicWaterMassMailing.pdf", name: "PublicWaterMassMailing", title: "Scanned Document",
    page_count: 2, relative_time: "2 min ago", opened_at: "2026-11-14T10:00:00", favorite: false },
  { path: "C:/docs/sample-scanned.pdf", name: "sample-scanned", title: "Scanned PDF",
    page_count: 1, relative_time: "1 hour ago", opened_at: "2026-11-14T09:00:00", favorite: true },
  { path: "C:/docs/lease 2026.pdf", name: "lease 2026", title: "Scanned PDF",
    page_count: 9, relative_time: "Yesterday", opened_at: "2026-11-13T09:00:00", favorite: false },
  { path: "C:/docs/Agreement.pdf", name: "Agreement", title: "Signature",
    page_count: 3, relative_time: "2 days ago", opened_at: "2026-11-12T09:00:00", favorite: false },
];
const handlers = { onSort() {}, onOpen() {}, onShowInFolder() {}, onToggleFavorite() {}, onRemove() {}, onTool() {} };
const out = { list: {}, grid: {}, favorites: {} };

const table = run("buildRecentTable")(entries, { key: "lastActive", direction: "desc" }, handlers, "recent");
for (const row of table.querySelectorAll(".recent-table-row.recent-item")) {
  row.fire("pointerover");  // the cut-off name's tooltip
  row.querySelector(".recent-more-btn").fire("click");  // the "⋯" menu
  out.list[row.querySelector(".recent-name").children.map((c) => c.textContent).join("")] = shown(row);
}
out.listMenus = menus.splice(0);

const grid = fakeElement("div");
for (const entry of entries) {
  const card = grid.appendChild(run("buildCard")(entry));
  card.querySelector(".recent-more-btn").fire("click");
  out.grid[card.querySelector(".title").textContent] = shown(card);
}
out.gridMenus = menus.splice(0);

run("homeSection = 'favorites'");
const favTable = run("buildRecentTable")(entries.filter((e) => e.favorite), { key: "name", direction: "asc" }, handlers, "favorites");
out.favorites = favTable.querySelectorAll(".recent-item").map((row) => shown(row));

out.sortedByName = run("sortRecentEntries")(entries, { key: "name", direction: "asc" }).map((e) => e.name);
const search = (q) => run("filterEntriesByQuery")(entries, q).map((e) => e.name);
out.search = { scanned: search("scanned"), signature: search("signature"), water: search("WATER") };
console.log(JSON.stringify(out));
"""

NAMES = ["PublicWaterMassMailing", "sample-scanned", "lease 2026", "Agreement"]
TITLES = ["Scanned Document", "Scanned PDF", "Signature"]


@unittest.skipIf(NODE is None, "Node is not installed")
class RecentNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "names.js"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run([NODE, str(harness), *map(str, SCRIPTS)], capture_output=True,
                                    text=True, encoding="utf-8", timeout=30)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def assertNoMetadataTitle(self, strings):
        for text in strings:
            for title in TITLES:
                self.assertNotIn(title, text)

    def test_the_list_and_the_grid_name_each_file_by_its_file_name(self):
        for view in ("list", "grid"):
            with self.subTest(view):
                self.assertEqual(sorted(self.out[view]), sorted(NAMES))

    def test_every_label_tooltip_and_menu_uses_the_file_name(self):
        for view in ("list", "grid"):
            for name, strings in self.out[view].items():
                with self.subTest(view=view, file=name):
                    self.assertNoMetadataTitle(strings)
                    labels = [s for s in strings if name in s]
                    self.assertIn(f"More actions for {name}", labels)
                    self.assertTrue(any(s.startswith(f"Open {name}") for s in labels), labels)
                    self.assertTrue(any(f"{name} to Favorites" in s or f"{name} from Favorites" in s
                                        for s in labels), labels)
        self.assertEqual(self.out["listMenus"], [f"Actions for {n}" for n in NAMES])
        self.assertEqual(self.out["gridMenus"], [f"Actions for {n}" for n in NAMES])

    def test_a_cut_off_name_s_tooltip_is_the_file_name(self):
        strings = self.out["list"]["PublicWaterMassMailing"]
        # The head is wider than its box in the stand-in, so the tooltip is set.
        self.assertGreaterEqual(strings.count("PublicWaterMassMailing"), 2)

    def test_favorites_use_the_file_name_too(self):
        [row] = self.out["favorites"]
        self.assertIn("sample-scanned", row)
        self.assertNoMetadataTitle(row)

    def test_sorting_by_name_uses_file_names(self):
        self.assertEqual(self.out["sortedByName"],
                         ["Agreement", "lease 2026", "PublicWaterMassMailing", "sample-scanned"])

    def test_search_finds_what_is_shown_and_not_the_hidden_title(self):
        self.assertEqual(self.out["search"], {
            "scanned": ["sample-scanned"],
            "signature": [],
            "water": ["PublicWaterMassMailing"],
        })


if __name__ == "__main__":
    unittest.main()
