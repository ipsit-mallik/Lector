"""Choosing several files at once in the in-app Open dialog (Combine's and
Convert's Add files…): click, Ctrl+click, Shift+click, Ctrl+A, Space, the
"Add 3 files" button and the "3 selected" beside it, files coming back in the
folder's order; and the list skipping files it already has.

The real file-browser.js and file-list.js run under Node with a stand-in DOM
(tests/dom_standin.py). Skipped when Node is not installed.
"""
import re
import shutil
import unittest
from pathlib import Path

from dom_standin import run_node

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
FILE_BROWSER_JS = FRONTEND / "js" / "file-browser.js"
FILE_LIST_JS = FRONTEND / "js" / "file-list.js"

HARNESS = r"""
const listing = {
  path: "C:/Docs", parent: "C:/", error: null,
  entries: [
    { name: "Sub", path: "C:/Docs/Sub", is_dir: true },
    { name: "a.pdf", path: "C:/Docs/a.pdf", is_dir: false },
    { name: "b.png", path: "C:/Docs/b.png", is_dir: false },
    { name: "c.pdf", path: "C:/Docs/c.pdf", is_dir: false },
    { name: "d.epub", path: "C:/Docs/d.epub", is_dir: false },
  ],
};
const contexts = [];
async function callApi(method, ...args) {
  if (method === "set_voice_context") contexts.push(args[0]);
  if (method === "browse_directory") return listing;
  if (method === "list_quick_access" || method === "list_drives") return { entries: [] };
  return {};
}

function dialog(prefix, withAdd) {
  const scrim = makeEl("div"); scrim.id = `${prefix}Scrim`; scrim.hidden = true;
  const title = makeEl("p"); title.className = "dialog-title"; title.textContent = "Open PDF";
  const parts = ["Path", "Quick", "Tree", "List"].map((name) => { const n = makeEl("div"); n.id = `${prefix}${name}`; return n; });
  const search = makeEl("input"); search.id = `${prefix}Search`;
  const footer = makeEl("div"); footer.className = "dialog-footer";
  const hint = makeEl("span"); hint.className = "hint"; hint.textContent = "Click a folder or PDF.";
  const count = makeEl("span"); count.id = `${prefix}SelectedCount`; count.hidden = true;
  const cancel = makeEl("button"); cancel.id = `${prefix}CancelBtn`; cancel.textContent = "Cancel";
  const add = makeEl("button"); add.id = `${prefix}AddBtn`; add.hidden = true;
  footer.append(hint, count, cancel);
  if (withAdd) footer.append(add);
  scrim.append(title, ...parts, search, footer);
  document.body.appendChild(scrim);
  return { scrim, list: parts[3], search, count, add, hint, cancel };
}

const ctx = context({ callApi, scrollElementTo() {}, scrollDestination: () => 0 });
load(ctx, process.argv.slice(2));
const { createFileBrowser, skipListedFiles } = vm.runInContext("({ createFileBrowser, skipListedFiles })", ctx);

const home = dialog("open", true);
const browser = createFileBrowser({
  scrimId: "openScrim", listId: "openList", pathId: "openPath", quickId: "openQuick", treeId: "openTree",
  searchId: "openSearch", cancelBtnId: "openCancelBtn", addBtnId: "openAddBtn", countId: "openSelectedCount",
  mode: "open", voiceContext: "open_dialog", restingContext: "home",
});
const rows = () => home.list.querySelectorAll(".browser-row");
const row = (name) => rows().find((r) => textOf(r.querySelector(".browser-row-name")) === name);
const chosen = () => rows().filter((r) => r.getAttribute("aria-selected") === "true").map((r) => textOf(r.querySelector(".browser-row-name")));
const state = () => ({ chosen: chosen(), add: home.add.textContent, disabled: home.add.disabled,
  count: home.count.hidden ? null : home.count.textContent });

(async () => {
  const out = {};
  let result = browser.open({ dir: "C:/Docs", kind: "insert", title: "Add files to combine", multiple: true });
  await settle(20);
  out.start = { ...state(), addShown: !home.add.hidden, role: home.list.getAttribute("role"),
    multi: home.list.getAttribute("aria-multiselectable"), hint: home.hint.textContent,
    checks: rows().filter((r) => r.querySelector(".browser-row-check")).length };
  row("b.png").dispatch("click");
  out.click = state();
  row("d.epub").dispatch("click", { ctrlKey: true });
  out.ctrlAdd = state();
  row("b.png").dispatch("click", { ctrlKey: true });
  out.ctrlRemove = state();
  row("a.pdf").dispatch("click");
  row("d.epub").dispatch("click", { shiftKey: true });
  out.shift = state();
  row("a.pdf").dispatch("click");
  home.search.focus();
  out.ctrlAInSearch = keydown("a", { ctrlKey: true }).defaultPrevented;
  row("a.pdf").focus();
  out.ctrlA = { prevented: keydown("a", { ctrlKey: true }).defaultPrevented, ...state() };
  out.focusedAfterCtrlA = textOf(document.activeElement.querySelector(".browser-row-name"));
  keydown(" ");
  out.space = state();
  keydown(" ");
  out.spaceAgain = state();
  // The search hides some rows: they are no longer chosen.
  home.search.value = "pdf";
  home.search.dispatch("input");
  out.searched = state();
  home.search.value = "";
  home.search.dispatch("input");
  // Chosen in the order clicked, added in the folder's order.
  row("d.epub").dispatch("click");
  row("a.pdf").dispatch("click", { ctrlKey: true });
  home.add.click();
  out.added = await result;
  out.closed = { hidden: home.scrim.hidden, addHidden: home.add.hidden, hint: home.hint.textContent };

  // Enter adds what is chosen.
  result = browser.open({ dir: "C:/Docs", kind: "insert", multiple: true });
  await settle(20);
  row("c.pdf").dispatch("click");
  out.enter = { prevented: keydown("Enter").defaultPrevented, result: await result };

  // A spoken number adds just that file; a double click too.
  result = browser.open({ dir: "C:/Docs", kind: "insert", multiple: true });
  await settle(20);
  browser.handleCommand({ intent: "DIALOG_PICK", index: 3 });
  out.spoken = await result;
  result = browser.open({ dir: "C:/Docs", kind: "insert", multiple: true });
  await settle(20);
  row("a.pdf").dispatch("dblclick");
  out.doubleClick = await result;

  // Without `multiple` (Home's Open PDF) a click opens the file, as before.
  result = browser.open({ dir: "C:/Docs" });
  await settle(20);
  out.single = { addHidden: home.add.hidden, role: home.list.getAttribute("role") };
  row("c.pdf").dispatch("click");
  out.single.result = await result;

  // A dialog with no Add button (Organise's Insert) stays one file a pick.
  const insert = dialog("insert", false);
  const insertBrowser = createFileBrowser({
    scrimId: "insertScrim", listId: "insertList", pathId: "insertPath", quickId: "insertQuick", treeId: "insertTree",
    searchId: "insertSearch", cancelBtnId: "insertCancelBtn", mode: "open", voiceContext: "open_dialog",
  });
  result = insertBrowser.open({ dir: "C:/Docs", kind: "insert", multiple: true });
  await settle(20);
  insert.list.querySelectorAll(".browser-row")[2].dispatch("click");
  out.insert = await result;

  // A picker opened for another context (Organise's Extract) hands that back.
  contexts.length = 0;
  result = browser.open({ dir: "C:/Docs", restingContext: "organise" });
  await settle(20);
  home.cancel.click();
  await result;
  out.contexts = contexts.slice();

  // Shift+↑/↓ grows and shrinks the run from the anchor, as Shift+click does.
  result = browser.open({ dir: "C:/Docs", kind: "insert", multiple: true });
  await settle(20);
  row("b.png").dispatch("click");
  keydown("ArrowDown", { shiftKey: true });
  keydown("ArrowDown", { shiftKey: true });
  out.shiftDown = chosen();
  keydown("ArrowUp", { shiftKey: true });
  out.shiftUp = chosen();
  // A search keeps the anchor on its file, so Shift+click runs from it.
  row("c.pdf").dispatch("click");
  home.search.value = "pdf";
  home.search.dispatch("input");
  row("a.pdf").dispatch("click", { shiftKey: true });
  out.shiftAfterSearch = chosen();
  home.cancel.click();
  await result;

  // The file list leaves out what it already has, and says how many.
  out.skipped = skipListedFiles([{ path: "C:\\Docs\\a.pdf" }], ["c:/docs/A.pdf", "C:/Docs/b.png", "C:/Docs/b.png", "C:/Docs/c.pdf"]);
  out.skipOne = skipListedFiles([{ path: "C:/Docs/a.pdf" }], ["C:/Docs/a.pdf", "C:/Docs/b.png"]);
  out.toasts = toasts.slice();
  process.stdout.write(JSON.stringify(out));
})().catch((err) => { console.error(err.stack); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class MultiSelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(HARNESS, [FILE_BROWSER_JS, FILE_LIST_JS])

    def test_the_dialog_offers_several_files(self):
        start = self.out["start"]
        self.assertEqual((start["role"], start["multi"]), ("listbox", "true"))
        self.assertTrue(start["addShown"])
        self.assertEqual((start["add"], start["disabled"], start["count"], start["chosen"]),
                         ("Add files", True, None, []))
        self.assertEqual(start["checks"], 5)
        self.assertIn("Ctrl+click or Shift+click", start["hint"])

    def test_a_click_chooses_that_file_alone(self):
        self.assertEqual(self.out["click"], {"chosen": ["b.png"], "add": "Add 1 file", "disabled": False,
                                             "count": "1 selected"})

    def test_ctrl_click_adds_and_takes_away(self):
        self.assertEqual(self.out["ctrlAdd"]["chosen"], ["b.png", "d.epub"])
        self.assertEqual((self.out["ctrlAdd"]["add"], self.out["ctrlAdd"]["count"]), ("Add 2 files", "2 selected"))
        self.assertEqual(self.out["ctrlRemove"]["chosen"], ["d.epub"])

    def test_shift_click_chooses_the_range_of_files(self):
        self.assertEqual(self.out["shift"]["chosen"], ["a.pdf", "b.png", "c.pdf", "d.epub"])
        self.assertEqual(self.out["shift"]["add"], "Add 4 files")

    def test_ctrl_a_chooses_every_file_shown_but_not_while_typing_a_search(self):
        self.assertFalse(self.out["ctrlAInSearch"])
        self.assertTrue(self.out["ctrlA"]["prevented"])
        self.assertEqual(self.out["ctrlA"]["chosen"], ["a.pdf", "b.png", "c.pdf", "d.epub"])

    def test_space_toggles_the_focused_file(self):
        self.assertEqual(self.out["focusedAfterCtrlA"], "a.pdf")
        self.assertEqual(self.out["space"]["chosen"], ["b.png", "c.pdf", "d.epub"])
        self.assertEqual(self.out["spaceAgain"]["chosen"], ["a.pdf", "b.png", "c.pdf", "d.epub"])

    def test_rows_hidden_by_the_search_drop_out(self):
        self.assertEqual(self.out["searched"]["chosen"], ["a.pdf", "c.pdf"])
        self.assertEqual(self.out["searched"]["count"], "2 selected")

    def test_files_come_back_in_the_folders_order(self):
        self.assertEqual(self.out["added"], {"paths": ["C:/Docs/a.pdf", "C:/Docs/d.epub"]})
        self.assertEqual(self.out["closed"], {"hidden": True, "addHidden": True, "hint": "Click a folder or PDF."})

    def test_enter_a_number_and_a_double_click_add(self):
        self.assertTrue(self.out["enter"]["prevented"])
        self.assertEqual(self.out["enter"]["result"], {"paths": ["C:/Docs/c.pdf"]})
        self.assertEqual(self.out["spoken"], {"paths": ["C:/Docs/b.png"]})
        self.assertEqual(self.out["doubleClick"], {"paths": ["C:/Docs/a.pdf"]})

    def test_without_multiple_and_without_an_add_button_one_file_is_picked(self):
        self.assertEqual(self.out["single"], {"addHidden": True, "role": None, "result": {"path": "C:/Docs/c.pdf"}})
        self.assertEqual(self.out["insert"], {"path": "C:/Docs/b.png"})

    def test_shift_arrows_grow_and_shrink_the_run_from_the_anchor(self):
        self.assertEqual(self.out["shiftDown"], ["b.png", "c.pdf", "d.epub"])
        self.assertEqual(self.out["shiftUp"], ["b.png", "c.pdf"])

    def test_a_search_keeps_the_shift_click_anchor_on_its_file(self):
        self.assertEqual(self.out["shiftAfterSearch"], ["a.pdf", "c.pdf"])

    def test_closing_hands_back_the_context_it_was_opened_for(self):
        self.assertEqual(self.out["contexts"], ["open_dialog", "organise"])

    def test_files_already_in_the_list_are_skipped_with_a_toast(self):
        self.assertEqual(self.out["skipped"], ["C:/Docs/b.png", "C:/Docs/c.pdf"])
        self.assertEqual(self.out["skipOne"], ["C:/Docs/b.png"])
        self.assertEqual(self.out["toasts"], ["2 files were already in the list", "1 file was already in the list"])


class WiringTests(unittest.TestCase):
    def test_combine_and_convert_add_several_and_skip_what_they_have(self):
        home = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
        opened = re.findall(r"addFiles: async \(\) => \{\s*const result = await openDialog\.open\(\{[^}]*multiple: true", home)
        self.assertEqual(len(opened), 2)
        for name in ("combine", "convert"):
            source = (FRONTEND / "pages" / f"{name}-dialog.js").read_text(encoding="utf-8")
            with self.subTest(dialog=name):
                self.assertIn("skipListedFiles(s.files, paths)", source)
        # Organise's Insert keeps one file a pick: its dialog has no Add button.
        source = FILE_BROWSER_JS.read_text(encoding="utf-8")
        opener = source[source.index("function ensureOpenFileDialog"):]
        opener = opener[:opener.index("\n}\n")]
        self.assertNotIn("addBtnId", opener)


if __name__ == "__main__":
    unittest.main()
