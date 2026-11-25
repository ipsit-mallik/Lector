"""Option-card groups (js/ui.js's `initRadioCards`) and the "choose a PDF"
list that uses one (pages/tools-page.js's `createPdfChooser`).

- An arrow with Alt, Ctrl or Meta held is left to whoever else wants it: a
  stepped dialog's Back is Alt+←, and a focused option card used to take it
  as "the previous option" and stop it there.
- The chooser's list is drawn again on every Back and Browse; its arrow keys
  still move one row (each render used to add another handler, so ↓ skipped
  rows).

The real `initRadioCards` runs under Node with the stand-in DOM
(tests/dom_standin.py). Skipped when Node is not installed.
"""
import shutil
import unittest
from pathlib import Path

from dom_standin import run_node

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
FILES = [FRONTEND / "js" / "ui.js", FRONTEND / "pages" / "tools-page.js"]

HARNESS = r"""
const [uiFile, toolsFile] = process.argv.slice(2);
const ui = fs.readFileSync(uiFile, "utf8");
const start = ui.indexOf("function initRadioCards");
const close = /\r?\n}\r?\n/g;
close.lastIndex = start;
const end = close.exec(ui).index + 3;
const ctx = context({
  MutationObserver: class { observe() {} },
  callApi: async (method) => (method === "get_recent_files"
    ? ["A", "B", "C", "D"].map((n) => ({ path: `C:/${n}.pdf`, name: n, page_count: 1, thumbnail: null }))
    : {}),
});
delete ctx.initRadioCards;
vm.runInContext(ui.slice(start, end), ctx);
load(ctx, [toolsFile]);
const initRadioCards = vm.runInContext("initRadioCards", ctx);
const createPdfChooser = vm.runInContext("createPdfChooser", ctx);

(async () => {
  const out = {};

  // ---- Alt, Ctrl and Meta arrows pass through ----------------------------
  const group = makeEl("div");
  document.body.appendChild(group);
  const cards = ["One", "Two", "Three"].map((label, i) => {
    const b = makeEl("button");
    b.textContent = label;
    if (i === 1) b.classList.add("selected");
    b.addEventListener("click", () => { cards.forEach((c) => c.classList.toggle("selected", c === b)); });
    group.appendChild(b);
    return b;
  });
  initRadioCards(group, { selectOnArrow: true });
  let reachedDocument = 0;
  document.addEventListener("keydown", () => { reachedDocument += 1; });
  cards[1].focus();
  const alt = keydown("ArrowLeft", { altKey: true });
  const ctrl = keydown("ArrowRight", { ctrlKey: true });
  out.modified = { reachedDocument, prevented: alt.defaultPrevented || ctrl.defaultPrevented,
    selected: cards.findIndex((c) => c.classList.contains("selected")), focus: cards.indexOf(document.activeElement) };
  const plain = keydown("ArrowLeft");
  out.plain = { prevented: plain.defaultPrevented, selected: cards.findIndex((c) => c.classList.contains("selected")) };

  // ---- The chooser's list, drawn again and again ---------------------------
  const chooser = createPdfChooser({ toolName: "Compress", browse: async () => null });
  await chooser.load();
  const parts = { badge: makeEl("div"), titleEl: makeEl("p"), descEl: makeEl("p"), bodyEl: makeEl("div"),
    footerEl: makeEl("div") };
  document.body.appendChild(parts.bodyEl);
  const draw = () => chooser.render({ ...parts, onContinue() {}, onCancel() {} });
  // First show, then three Backs (each draws the chooser again).
  for (let i = 0; i < 4; i++) draw();
  const rows = () => parts.bodyEl.querySelectorAll(".pick-row");
  rows()[0].focus();
  keydown("ArrowDown");
  out.chooser = { focus: rows().indexOf(document.activeElement),
    selected: rows().findIndex((r) => r.classList.contains("selected")) };

  process.stdout.write(JSON.stringify(out));
})().catch((err) => { console.error(err.stack); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class RadioCardKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(HARNESS, FILES)

    def test_arrows_with_alt_or_ctrl_are_left_for_other_shortcuts(self):
        modified = self.out["modified"]
        self.assertEqual(modified["reachedDocument"], 2)
        self.assertFalse(modified["prevented"])
        self.assertEqual((modified["selected"], modified["focus"]), (1, 1))

    def test_a_plain_arrow_still_moves_and_chooses(self):
        self.assertEqual(self.out["plain"], {"prevented": True, "selected": 0})

    def test_the_chooser_moves_one_row_however_often_it_was_drawn(self):
        self.assertEqual(self.out["chooser"], {"focus": 1, "selected": 1})


if __name__ == "__main__":
    unittest.main()
