"""Home's ways into Tier 2 (Milestone F2.7, Split live since F2.9): the
Tools page's "Pages" section (frontend/pages/tools-page.js) and Organise
pages… / Split… in a Recent row's or grid card's ⋯ menu (recent-list.js's
buildToolItems), with Home sending a PDF to the reader with Organise open
and a PDF to Split's dialog (home.js).

The Tools page and the menu items are run under Node with a small stand-in
DOM; home.js's wiring is read as text. Skipped when Node is not installed.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
PAGES = FRONTEND / "pages"
HOME_JS = (PAGES / "home.js").read_text(encoding="utf-8")
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function makeEl(tag) {
  const listeners = {};
  const classes = new Set();
  return {
    tagName: tag.toUpperCase(), children: [], attrs: {}, dataset: {}, textContent: "", title: "", id: "", type: "",
    classList: { add: (c) => classes.add(c), contains: (c) => classes.has(c) },
    set className(v) { classes.clear(); v.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(" "); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    click() { (listeners.click || []).forEach((fn) => fn({})); },
    appendChild(k) { this.children.push(k); return k; },
    append(...ks) { ks.forEach((k) => this.children.push(k)); },
  };
}
const document = { createElement: makeEl, createTextNode: (t) => { const n = makeEl("#text"); n.textContent = t; return n; } };
const chosen = [];
const context = vm.createContext({
  document, console,
  buildPopupMenuItem: ({ icon, label, onSelect, className }) => {
    const item = makeEl("button");
    item.className = className; item.dataset.icon = icon; item.textContent = label;
    item.addEventListener("click", onSelect);
    return item;
  },
  buildPopupMenuSeparator: (className) => { const s = makeEl("div"); s.className = className; s.separator = true; return s; },
});
for (const file of process.argv.slice(2)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
vm.runInContext("this.__api = { buildToolsPage, buildToolItems, TOOL_NAMES };", context);
const api = context.__api;

const page = api.buildToolsPage((id) => chosen.push(id));
const sections = page.children.map((section) => {
  const [title, sub, grid] = section.children;
  return {
    title: title.textContent, sub: sub.textContent,
    cards: grid.children.map((card) => ({
      id: card.dataset.tool, icon: card.children[0].dataset.icon, name: card.children[1].textContent,
      desc: card.children[2].textContent, disabled: card.getAttribute("aria-disabled"), title: card.title,
    })),
  };
});
for (const section of page.children) for (const card of section.children[2].children) card.click();

const menuTools = [];
const items = api.buildToolItems({ path: "C:/Docs/Report.pdf" }, { onTool: (tool, entry) => menuTools.push([tool, entry.path]) });
const menu = items.map((item) => (item.separator ? "—" : [item.textContent, item.dataset.icon, item.getAttribute("aria-disabled"), item.title]));
items.forEach((item) => { if (!item.separator) item.click(); });

process.stdout.write(JSON.stringify({
  sections, chosen, menu, menuTools, names: api.TOOL_NAMES,
  noHandler: api.buildToolItems({ path: "x" }, {}).length,
}));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class PagesSectionAndMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(
                ["node", str(harness), str(PAGES / "tools-page.js"), str(PAGES / "recent-list.js")],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_the_pages_section_follows_shrink_and_convert(self):
        titles = [s["title"] for s in self.out["sections"]]
        self.assertEqual(titles, ["Shrink and convert", "Pages"])
        pages = self.out["sections"][1]
        self.assertEqual(pages["sub"], "Rearrange whole pages and files. What is on each page never changes.")
        self.assertEqual(
            [(c["id"], c["icon"], c["name"], c["desc"]) for c in pages["cards"]],
            [
                ("organise", "organise", "Organise pages", "Rotate, delete, reorder, extract or insert pages."),
                ("combine", "combine", "Combine files", "Join several PDFs and images into one PDF."),
                ("split", "split", "Split", "Break a PDF into several, by page ranges or every N pages."),
            ],
        )

    def test_every_card_is_live(self):
        cards = {c["id"]: c for c in self.out["sections"][1]["cards"]}
        for ready in ("organise", "combine", "split"):
            self.assertEqual((cards[ready]["disabled"], cards[ready]["title"]), (None, ""))
        self.assertEqual(
            self.out["chosen"], ["compress", "searchable", "export", "convert", "organise", "combine", "split"]
        )

    def test_the_picker_names_organise(self):
        self.assertEqual(self.out["names"]["organise"], "Organise pages")

    def test_the_recent_menu_has_organise_and_split_under_export(self):
        self.assertEqual(self.out["menu"], [
            "—",
            ["Compress…", "compress", None, ""],
            ["Make searchable…", "searchable", None, ""],
            ["Export as images…", "export_images", None, ""],
            ["Organise pages…", "organise", None, ""],
            ["Split…", "split", None, ""],
        ])
        self.assertEqual(self.out["menuTools"], [
            ["compress", "C:/Docs/Report.pdf"], ["searchable", "C:/Docs/Report.pdf"],
            ["export", "C:/Docs/Report.pdf"], ["organise", "C:/Docs/Report.pdf"],
            ["split", "C:/Docs/Report.pdf"],
        ])
        self.assertEqual(self.out["noHandler"], 0)

    def test_the_new_icons_exist(self):
        for name in ("combine", "split", "organise"):
            self.assertTrue((FRONTEND / "shared" / "icons" / f"{name}.svg").is_file(), name)


class HomeSendsThePdfToOrganiseTests(unittest.TestCase):
    def test_both_ways_in_open_the_pdf_then_leave_the_readers_note(self):
        # The Recent menu and the Tools page's picker share one router.
        self.assertRegex(HOME_JS, r"onTool: \(tool, entry\) => openToolFor\(tool, entry\.path\)")
        self.assertRegex(HOME_JS, r"if \(path\) openToolFor\(tool, path\)")
        router = HOME_JS[HOME_JS.index("function openToolFor"):]
        router = router[:router.index("\n}\n")]
        self.assertIn('if (tool === "organise") openPathToOrganise(path);', router)
        self.assertIn('else if (tool === "split") SplitDialog.open(path);', router)
        body = HOME_JS[HOME_JS.index("async function openPathToOrganise"):]
        body = body[:body.index("\n}\n")]
        # The note only once the PDF is open; then the reader.
        self.assertLess(body.index("await openPdf(path"), body.index("sessionStorage.setItem(OPEN_ORGANISE_NOTE"))
        self.assertLess(body.index("sessionStorage.setItem"), body.index('"pages/reading.html"'))

    def test_home_and_the_reader_name_the_same_note(self):
        home = re.search(r'const OPEN_ORGANISE_NOTE = "([^"]+)"', HOME_JS).group(1)
        reader = re.search(r'const OPEN_ORGANISE_NOTE = "([^"]+)"', READING_JS).group(1)
        self.assertEqual(home, reader)
        # The reader takes the note once and opens Organise.
        self.assertRegex(READING_JS, r"sessionStorage\.removeItem\(OPEN_ORGANISE_NOTE\);\s*startOrganise\(\)")


if __name__ == "__main__":
    unittest.main()
