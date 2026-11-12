"""Behaviour of the file tools' shared file list (frontend/js/file-list.js),
used by Convert to PDF and Combine files (Milestone F2.8): the drop zone,
the rows (position, name, the dialog's own muted line), Move up / Move down
disabled at the ends, Remove, Alt+↑/↓ on a focused row, a row dragged onto
another landing before or after it, and `moveFileListItem`.

Also: both dialogs build their list with it, and Combine's row line.
Run under Node with a small stand-in DOM. Skipped when Node is not
installed.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
FILE_LIST = FRONTEND / "js" / "file-list.js"
PAGES = FRONTEND / "pages"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function makeEl(tag) {
  const listeners = {};
  const classes = new Set();
  return {
    tagName: tag.toUpperCase(), children: [], attrs: {}, dataset: {}, textContent: "", disabled: false, draggable: false,
    classList: {
      add: (c) => classes.add(c), remove: (...cs) => cs.forEach((c) => classes.delete(c)),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: (c) => classes.has(c),
    },
    set className(v) { classes.clear(); v.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(" "); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type, props = {}) {
      const ev = { target: this, key: "", altKey: false, clientY: 0, preventDefault() {}, stopPropagation() {},
        dataTransfer: { setData() {}, effectAllowed: "" }, ...props };
      (listeners[type] || []).forEach((fn) => fn(ev));
    },
    appendChild(k) { this.children.push(k); return k; },
    append(...ks) { ks.forEach((k) => this.children.push(k)); },
    getBoundingClientRect() { return { top: 0, height: 48 }; },
  };
}
const context = vm.createContext({ document: { createElement: makeEl }, console });
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext("this.__api = { createFileList, buildDropZone, moveFileListItem };", context);
const api = context.__api;

const out = {};
const asked = [];
let added = 0;
const zone = api.buildDropZone({ text: "Drop PDFs or images here", onAdd: () => { added += 1; } });
out.zone = [zone.className, zone.children[0].children[1].textContent, zone.children[1].textContent, zone.children[1].dataset.keep];
zone.children[1].fire("click");
out.added = added;

const files = [
  { name: "Report.pdf", pages: 3, pdf: true }, { name: "Appendix.pdf", pages: 58, pdf: true }, { name: "Photo.jpg", pdf: false },
];
const list = api.createFileList({
  label: "Files, in the order they are joined",
  meta: (f) => (f.pdf ? `PDF · ${f.pages} pages` : "JPEG · 2.1 MB"),
  onMove: (from, to) => asked.push(["move", from, to]),
  onRemove: (i) => asked.push(["remove", i]),
});
const el = list.build(files);
out.list = [el.attrs.role, el.attrs["aria-label"]];
out.rows = el.children.map((row) => {
  const [, number, , text, actions] = row.children;
  return {
    number: number.textContent, name: text.children[0].textContent, meta: text.children[1].textContent,
    keep: row.dataset.keep, label: row.attrs["aria-label"], draggable: row.draggable,
    disabled: actions.children.map((b) => b.disabled), buttons: actions.children.map((b) => b.attrs["aria-label"]),
  };
});
const [r0, r1, r2] = el.children;
r1.children[4].children[0].fire("click");   // Move up
r1.children[4].children[1].fire("click");   // Move down
r1.children[4].children[2].fire("click");   // Remove
r0.fire("keydown", { key: "ArrowDown", altKey: true });
r0.fire("keydown", { key: "ArrowUp", altKey: true });   // the top: nothing
r2.fire("keydown", { key: "ArrowDown", altKey: true }); // the bottom: nothing
r1.fire("keydown", { key: "ArrowDown" });               // no Alt: nothing
// Drag row 0 onto the lower half of row 2 (after it), then row 2 onto the top half of row 0.
r0.fire("dragstart");
r2.fire("dragover", { clientY: 40 });
out.dropAfter = r2.classList.contains("is-drop-after");
r2.fire("drop");
r2.fire("dragstart");
r0.fire("dragover", { clientY: 5 });
r0.fire("drop");
// A drag that didn't start on a row (a file from outside) is not the list's.
r1.fire("dragover", { clientY: 40 });
out.foreign = r1.classList.contains("is-drop-after");
out.asked = asked;
out.rowAt = list.row(1) === r1;
out.moved = api.moveFileListItem(["a", "b", "c"], 0, 2);
out.same = api.moveFileListItem(["a", "b"], 1, 5);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class FileListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(["node", str(harness), str(FILE_LIST)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_the_drop_zone(self):
        self.assertEqual(self.out["zone"], ["drop-zone", "Drop PDFs or images here", "Add files…", "add"])
        self.assertEqual(self.out["added"], 1)

    def test_the_rows(self):
        self.assertEqual(self.out["list"], ["list", "Files, in the order they are joined"])
        rows = self.out["rows"]
        self.assertEqual([(r["number"], r["name"], r["meta"]) for r in rows], [
            ("1", "Report.pdf", "PDF · 3 pages"), ("2", "Appendix.pdf", "PDF · 58 pages"), ("3", "Photo.jpg", "JPEG · 2.1 MB"),
        ])
        self.assertEqual(rows[0]["label"], "1. Report.pdf. Alt+Up or Alt+Down moves it.")
        self.assertEqual(rows[1]["buttons"], ["Move up: Appendix.pdf", "Move down: Appendix.pdf", "Remove: Appendix.pdf"])
        self.assertEqual([r["disabled"] for r in rows], [[True, False, False], [False, False, False], [False, True, False]])
        self.assertEqual([r["keep"] for r in rows], ["row-0", "row-1", "row-2"])
        self.assertTrue(all(r["draggable"] for r in rows))

    def test_moving_and_removing(self):
        self.assertEqual(self.out["asked"], [
            ["move", 1, 0], ["move", 1, 2], ["remove", 1],  # the buttons
            ["move", 0, 1],                                  # Alt+↓ (not past either end, not without Alt)
            ["move", 0, 2],                                  # dragged after the last row
            ["move", 2, 0],                                  # dragged before the first
        ])
        self.assertTrue(self.out["dropAfter"])
        self.assertFalse(self.out["foreign"])
        self.assertTrue(self.out["rowAt"])
        self.assertEqual(self.out["moved"], ["b", "c", "a"])
        self.assertEqual(self.out["same"], ["a", "b"])


class BothDialogsUseItTests(unittest.TestCase):
    def test_convert_and_combine_build_their_lists_with_it(self):
        index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index('src="js/file-list.js"'), index.index('src="pages/convert-dialog.js"'))
        self.assertLess(index.index('src="js/file-list.js"'), index.index('src="pages/combine-dialog.js"'))
        for name, zone in (("convert-dialog.js", "Drop images or an e-book here"), ("combine-dialog.js", "Drop PDFs or images here")):
            with self.subTest(dialog=name):
                source = (PAGES / name).read_text(encoding="utf-8")
                self.assertIn("createFileList({", source)
                self.assertIn(f'buildDropZone({{ text: "{zone}"', source)
                self.assertNotIn("convert-row", source)

    def test_combines_row_line_and_its_rules(self):
        source = (PAGES / "combine-dialog.js").read_text(encoding="utf-8")
        self.assertIn('`PDF · ${plural(file.pages, "page", "pages")}`', source)
        self.assertIn('`${pages} · password entered`', source)
        self.assertIn("s.files.length >= 2", source)  # Combine needs two files
        self.assertIn('callApi("forget_combine_passwords")', source)


if __name__ == "__main__":
    unittest.main()
