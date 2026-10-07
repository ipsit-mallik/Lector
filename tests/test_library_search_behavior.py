"""Behaviour of frontend/pages/library-search.js's pure helpers: the live
filter, the placeholder, the filtered count and the shortcut detection.

Run under Node the same way tests/test_recent_list_behavior.py runs
recent-list.js: the script is loaded into a context with no window or
document, which it supports (its DOM wiring only starts when
`createLibrarySearch` is called). Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "library-search.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({ setTimeout, clearTimeout });
vm.runInContext(
  source +
    "\nthis.api = { filterEntriesByQuery, searchPlaceholder, formatFileCount," +
    " createDebouncer, SEARCH_DEBOUNCE_MS, isSearchShortcut, isTypingTarget };",
  context
);
const api = context.api;

const entry = (name, title) => ({ name, title });
const ENTRIES = [
  entry("Annual Report 2025.pdf", "Annual Report"),
  entry("invoice-0042.pdf", "invoice-0042"),
  entry("thesis_final.pdf", "Quantum Gardens"),
  entry("REPORT draft.pdf", "Draft"),
];
const names = (list) => list.map((e) => e.name);

// A check may return a promise (the debounce ones wait on real timers).
const pending = [];
const results = {};
const check = (name, fn) => {
  pending.push(
    Promise.resolve()
      .then(fn)
      .then((ok) => { results[name] = ok === true; })
      .catch((err) => { results[name] = `threw: ${err.message}`; })
  );
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

check("an empty query keeps every entry", () => {
  return api.filterEntriesByQuery(ENTRIES, "") === ENTRIES;
});

check("a whitespace-only query keeps every entry", () => {
  return api.filterEntriesByQuery(ENTRIES, "   ").length === ENTRIES.length;
});

check("matches a file name case-insensitively", () => {
  return names(api.filterEntriesByQuery(ENTRIES, "report")).join("|")
    === "Annual Report 2025.pdf|REPORT draft.pdf";
});

check("matches the PDF's own title, which the list view shows", () => {
  return names(api.filterEntriesByQuery(ENTRIES, "quantum")).join("|") === "thesis_final.pdf";
});

check("surrounding whitespace in the query is ignored", () => {
  return names(api.filterEntriesByQuery(ENTRIES, "  invoice ")).join("|") === "invoice-0042.pdf";
});

check("keeps the original order", () => {
  return names(api.filterEntriesByQuery(ENTRIES, ".pdf")).join("|") === names(ENTRIES).join("|");
});

check("no match gives an empty array", () => {
  return api.filterEntriesByQuery(ENTRIES, "zzz").length === 0;
});

check("treats regex characters literally", () => {
  return api.filterEntriesByQuery(ENTRIES, ".*").length === 0
    && api.filterEntriesByQuery(ENTRIES, "(").length === 0;
});

check("an entry with no title still matches by name", () => {
  const list = [{ name: "plain.pdf" }];
  return api.filterEntriesByQuery(list, "plain").length === 1;
});

check("does not mutate the input", () => {
  const copy = JSON.stringify(ENTRIES);
  api.filterEntriesByQuery(ENTRIES, "report");
  return JSON.stringify(ENTRIES) === copy;
});

check("the placeholder names the page", () => {
  return api.searchPlaceholder("Recent") === "Search Recent"
    && api.searchPlaceholder("Favorites") === "Search Favorites";
});

check("the count reads '12 files', and '1 file' for one", () => {
  return api.formatFileCount(12, 12, false) === "12 files"
    && api.formatFileCount(1, 1, false) === "1 file";
});

check("while filtering the count reads '3 of 12 files'", () => {
  return api.formatFileCount(3, 12, true) === "3 of 12 files"
    && api.formatFileCount(0, 12, true) === "0 of 12 files"
    && api.formatFileCount(1, 1, true) === "1 of 1 file";
});

check("an empty list says there are no files yet", () => {
  return api.formatFileCount(0, 0, false) === "no files yet";
});

check("the debounce is about 200ms", () => api.SEARCH_DEBOUNCE_MS === 200);

check("a debouncer fires once, with the latest value, after the pause", async () => {
  const seen = [];
  const debouncer = api.createDebouncer((value) => seen.push(value), 30);
  debouncer.schedule("a");
  await sleep(10);
  debouncer.schedule("ab");
  await sleep(10);
  debouncer.schedule("abc");
  if (seen.length !== 0) return false;
  await sleep(60);
  return seen.join("|") === "abc";
});

check("a cancelled debouncer never fires", async () => {
  const seen = [];
  const debouncer = api.createDebouncer((value) => seen.push(value), 20);
  debouncer.schedule("stale");
  debouncer.cancel();
  await sleep(50);
  return seen.length === 0;
});

check("a debouncer can be used again after it fires", async () => {
  const seen = [];
  const debouncer = api.createDebouncer((value) => seen.push(value), 15);
  debouncer.schedule("one");
  await sleep(40);
  debouncer.schedule("two");
  await sleep(40);
  return seen.join("|") === "one|two";
});

const key = (k, mods = {}) => ({ key: k, ctrlKey: false, metaKey: false, altKey: false, ...mods });

check("'/' is a search shortcut", () => api.isSearchShortcut(key("/")) === "slash");
check("Ctrl+F is a search shortcut", () => api.isSearchShortcut(key("f", { ctrlKey: true })) === "find");
check("Cmd+F is a search shortcut", () => api.isSearchShortcut(key("F", { metaKey: true })) === "find");
check("a plain 'f' is not", () => api.isSearchShortcut(key("f")) === null);
check("Ctrl+'/' is not", () => api.isSearchShortcut(key("/", { ctrlKey: true })) === null);

check("inputs count as typing targets", () => {
  return api.isTypingTarget({ tagName: "INPUT" }) && !api.isTypingTarget({ tagName: "BUTTON" })
    && !api.isTypingTarget(null);
});

Promise.all(pending).then(() => console.log(JSON.stringify(results)));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class LibrarySearchHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Node harness failed:\n{proc.stderr}")
        cls.results = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_every_check_passes(self):
        failures = {name: res for name, res in self.results.items() if res is not True}
        self.assertEqual(failures, {})
        self.assertGreater(len(self.results), 10)


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class HeaderWiringTests(unittest.TestCase):
    """The header markup and scripts that the behaviour above depends on."""

    def read(self, *parts):
        return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")

    def test_search_script_loads_before_home_js(self):
        html = self.read("index.html")
        self.assertLess(html.index("pages/library-search.js"), html.index("pages/home.js"))

    def test_old_top_bar_and_unwired_search_box_are_gone(self):
        html = self.read("index.html")
        for leftover in ("top-row", "search-input", "Search your library", "recent-heading"):
            self.assertNotIn(leftover, html)

    def test_header_controls_start_hidden_in_the_order_search_toggle_open(self):
        html = self.read("index.html")
        order = [html.index(f'id="{i}"') for i in ("headerSearch", "viewToggle", "openPdfBtn")]
        self.assertEqual(order, sorted(order))
        for control in ("headerSearch", "viewToggle", "openPdfBtn"):
            self.assertRegex(html, rf'<[^>]*is-absent[^>]*id="{control}"')

    def test_absent_controls_keep_their_space(self):
        css = self.read("pages", "home.css")
        self.assertRegex(css, r"\.is-absent\s*\{[^}]*visibility:\s*hidden")
        self.assertNotRegex(css, r"\.is-absent\s*\{[^}]*display:\s*none")

    def test_ctrl_o_is_bound_on_home_settings_and_reading(self):
        for page in (("pages", "home.js"), ("pages", "settings.js"), ("pages", "reading.js")):
            source = self.read(*page)
            self.assertRegex(source, r'"o"', page)
            self.assertIn("ctrlKey", source, page)

    def test_settings_uses_the_shared_header_row_with_no_controls(self):
        html = self.read("pages", "settings.html")
        self.assertIn('class="page-header"', html)
        self.assertNotIn("headerSearch", html)
        self.assertNotIn("openPdfBtn", html)

    def test_search_is_an_icon_button_in_the_spacer_left_of_the_toggle(self):
        html = self.read("index.html")
        order = [
            html.index(marker)
            for marker in ('class="page-header-fill"', 'id="headerSearchToggle"', 'id="viewToggle"')
        ]
        self.assertEqual(order, sorted(order))
        self.assertRegex(html, r'id="headerSearchToggle"[^>]*aria-expanded="false"')

    def test_the_header_row_is_one_shared_stylesheet(self):
        components = self.read("shared", "components.css")
        self.assertIn('@import "library-header.css"', components)
        shared = self.read("shared", "library-header.css")
        home = self.read("pages", "home.css")
        for selector in (".header-search", ".view-toggle", ".page-count", ".page-header-fill"):
            self.assertIn(selector, shared)
            self.assertNotRegex(home, rf"(?m)^{selector.replace('.', r'[.]')}\b")

    def test_one_control_height_for_search_toggle_and_open_pdf(self):
        shared = self.read("shared", "library-header.css")
        self.assertRegex(shared, r"\.header-search\s*\{[^}]*width:\s*var\(--control-h\)")
        self.assertRegex(shared, r"\.view-toggle\s*\{[^}]*height:\s*var\(--control-h\)")
        self.assertRegex(shared, r"\.header-open-btn\s*\{[^}]*height:\s*var\(--control-h\)")

    def test_search_grow_uses_the_shared_motion_tokens(self):
        shared = self.read("shared", "library-header.css")
        self.assertRegex(
            shared, r"transition:\s*width var\(--dur-base\) var\(--ease-out\)"
        )

    def test_grid_card_script_loads_before_home_js(self):
        html = self.read("index.html")
        self.assertLess(html.index("pages/recent-card.js"), html.index("pages/home.js"))


if __name__ == "__main__":
    unittest.main()
