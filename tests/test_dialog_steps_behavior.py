"""The tools' "choose a PDF" and the tool as two steps of one dialog
(js/dialog-steps.js; docs/DESIGN_SYSTEM.md § "File tools" → "Stepped
dialog"), Back between them, Split with one range and its "Move the original
to the Recycle Bin afterwards", and Organise's picker fading out before the
reader opens.

The real tool-dialogs.js, tools-page.js and split-dialog.js run under Node
with a stand-in DOM (tests/dom_standin.py); Python is a stub. Skipped when
Node is not installed.
"""
import shutil
import unittest
from pathlib import Path

from dom_standin import run_node

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SCRIPTS = [
    FRONTEND / "js" / "dialog-motion.js",
    FRONTEND / "js" / "dialog-steps.js",
    FRONTEND / "pages" / "tool-dialogs.js",
    FRONTEND / "pages" / "tools-page.js",
    FRONTEND / "pages" / "split-dialog.js",
]

HARNESS = r"""
const calls = [];
const recent = ["C:/Docs/Report.pdf", "C:/Docs/Notes.pdf", "C:/Docs/Scan.pdf"].map((path) => ({
  path, name: path.split("/").pop().replace(".pdf", ""), page_count: 10, thumbnail: null,
}));
const destinations = [];
let jobHandler = null;
const moved = [];

function info(path) {
  return {
    ok: true, name: path.split("/").pop(), pages: 10, page_size: [595, 842], signed: false, dirty: false,
    textless: 0, textless_pages: [], split_every: 5, trash_name: "Recycle Bin",
    prefs: { export_format: "png", export_ppi: 150 },
    output: { folder: "C:/Docs", stem: "x", extension: "" },
  };
}

function preview(mode, value) {
  if (mode === "every") return { count: 2, parts: [[1, 5], [6, 10]], named: [], more: 0, gaps: null };
  if (value.includes(",")) return { count: 2, parts: [[1, 3], [4, 6]], named: [], more: 0, gaps: "Pages 7–10 aren’t in any file." };
  const [first, last] = value.split("–").map(Number);
  const end = Number.isFinite(last) ? last : first;
  const label = first === end ? `page ${first}` : `pages ${first}–${end}`;
  return { count: 1, parts: [[first, end]], named: [{ name: `Report (${label}).pdf`, pages: label }], more: 0,
    gaps: "Pages 7–10 aren’t in any file.", single: { label, first, last: end } };
}

let holdInfo = null;
async function callApi(method, ...args) {
  calls.push([method, ...args]);
  if (method === "get_recent_files") return recent;
  if (method === "file_tool_info" && holdInfo) return new Promise((resolve) => { holdInfo = () => resolve(info(args[1])); });
  if (method === "file_tool_info") return info(args[1]);
  if (method === "split_preview") return preview(args[1], args[2]);
  if (method === "check_tool_destination") return { ok: true, exists: false };
  if (method === "start_file_job") return { ok: true, id: "job1" };
  return {};
}

function createSaveDestination(options) {
  const element = makeEl("div");
  element.className = "save-dest";
  element.dataset.kind = options.kind;
  const made = { kind: options.kind, stem: options.stem, refreshed: 0, pending: false };
  destinations.push(made);
  return {
    element, isValid: () => !made.pending, value: () => ({ folder: "C:/Docs", name: made.stem }), setHint() {},
    closeMenu() {}, isMenuOpen: () => false, refreshName() { made.refreshed += 1; }, showError() {},
    setPending(on) { made.pending = on; }, setSuggestedStem(stem) { made.stem = stem; },
  };
}

const mounted = [];
const ctx = context({
  callApi, createSaveDestination,
  mountIcons: async (node) => { if (node && node.dataset) mounted.push(node.dataset.kind || null); },
  CompressPanel: { init() {}, closed() {}, opened() {}, freshState: () => null, destinationChanged() {} },
  FileJobs: { subscribe(fn) { if (!jobHandler) jobHandler = []; jobHandler.push(fn); }, watch() {}, current: () => null },
  PasswordDialog: { ask: async () => null }, ReplaceDialog: { open: async () => true },
  fileJobFraction: () => null, fileJobPhaseText: () => "", fileJobProgressLabel: () => "", showFileJobOutput() {},
  savePlaceClause: (w) => (w ? `, ${w}` : ""),
});
load(ctx, process.argv.slice(2));
const { ToolDialog, SplitDialog, PdfPicker } = vm.runInContext("({ ToolDialog, SplitDialog, PdfPicker })", ctx);
const jobEvent = (event) => jobHandler.forEach((fn) => fn(event));

const scrimOf = (id) => document.getElementById(id);
const card = (id) => scrimOf(id).querySelector(".dialog-card");
const title = (id) => textOf(card(id).querySelector(".dialog-title"));
const footer = (id) => card(id).querySelector(".dialog-footer").children.map((b) => b.textContent);
const button = (id, label) => card(id).querySelector(".dialog-footer").children.find((b) => b.textContent === label);
const rows = (id) => card(id).querySelectorAll(".pick-row");
const selectedRow = (id) => textOf(card(id).querySelector(".pick-row.selected .pick-name"));
const status = (id) => textOf(card(id).querySelector(".dialog-step-status"));
const infoCalls = () => calls.filter((c) => c[0] === "file_tool_info").length;

// Every time the dim would lift: a scrim's `hidden` set to true.
const hides = [];
function watchHidden(scrim) {
  let value = scrim.hidden;
  Object.defineProperty(scrim, "hidden", {
    get: () => value,
    set: (v) => { if (v && !value) hides.push(scrim.id); value = v; },
  });
}

function swaps(from) {
  return animations.slice(from).map((a) => ({
    cls: a.node.className,
    frames: a.frames.map((f) => f.height || f.transform || String(f.opacity)),
    opacity: a.frames.map((f) => (f.opacity === undefined ? null : f.opacity)),
    duration: a.options.duration, delay: a.options.delay || 0, easing: a.options.easing,
  }));
}

(async () => {
  const out = {};

  // ---- Export from the Tools page --------------------------------------------
  await ToolDialog.open("export", null, { choose: true });
  await settle();
  const id = "toolDialog";
  watchHidden(scrimOf(id));
  out.chooser = { title: title(id), rows: rows(id).length, footer: footer(id), selected: selectedRow(id),
    choosing: ToolDialog.isChoosing(), focus: document.activeElement === rows(id)[0] };
  let mark = animations.length;
  button(id, "Continue").click();
  await settle(20);
  out.tool = { title: title(id), footer: footer(id), status: status(id), infoCalls: infoCalls(),
    focusInCard: card(id).contains(document.activeElement),
    firstIsBack: card(id).querySelector(".dialog-footer").children[0].className };
  out.forward = swaps(mark);
  // Choose JPEG and a page range, then go back with Alt+←.
  card(id).querySelectorAll(".segmented-btn").find((b) => b.textContent === "JPEG").click();
  const pagesCard = card(id).querySelectorAll(".option-row").find((b) => b.textContent.startsWith("Pages"));
  pagesCard.click();
  mark = animations.length;
  const back = keydown("ArrowLeft", { altKey: true });
  await settle();
  out.back = { prevented: back.defaultPrevented, title: title(id), selected: selectedRow(id), status: status(id),
    focus: textOf(document.activeElement.querySelector(".pick-name")) };
  out.backward = swaps(mark);
  // The same PDF again: nothing is asked of Python, the choices are as left.
  button(id, "Continue").click();
  await settle(20);
  out.samePdf = { infoCalls: infoCalls(),
    jpeg: card(id).querySelector(".segmented-btn.selected").textContent,
    range: Boolean(card(id).querySelector("#toolRangeInput")) };
  // Another PDF: the format stays, the pages start again.
  keydown("ArrowLeft", { altKey: true });
  await settle();
  rows(id)[1].click();
  button(id, "Continue").click();
  await settle(20);
  out.otherPdf = { infoCalls: infoCalls(), lastSource: calls.filter((c) => c[0] === "file_tool_info").pop()[2],
    jpeg: card(id).querySelector(".segmented-btn.selected").textContent,
    range: Boolean(card(id).querySelector("#toolRangeInput")) };
  // Working: no Back.
  button(id, "Export 10 images").click();
  await settle(20);
  out.working = { footer: footer(id), alt: keydown("ArrowLeft", { altKey: true }).defaultPrevented };
  jobEvent({ id: "job1", state: "cancelled" });
  out.hidesWhileStepping = hides.slice();
  keydown("Escape");
  out.escOnTool = scrimOf(id).hidden;

  // Esc on the chooser closes the whole dialog. A second open while the
  // first is still reading the list (a double click) is ignored.
  const listsBefore = calls.filter((c) => c[0] === "get_recent_files").length;
  const firstOpen = ToolDialog.open("export", null, { choose: true });
  await ToolDialog.open("export", null, { choose: true });
  await firstOpen;
  await settle();
  out.doubleOpen = calls.filter((c) => c[0] === "get_recent_files").length - listsBefore;
  keydown("Escape");
  out.escOnChooser = scrimOf(id).hidden;

  // A slow bridge: the step changes on the click, before Python answers,
  // with placeholders; the answer fills them in place.
  holdInfo = true;
  await ToolDialog.open("export", null, { choose: true });
  await settle();
  const infoBefore = infoCalls();
  button(id, "Continue").click();
  out.slow = { title: title(id), footer: footer(id), primaryDisabled: button(id, "Export images").disabled,
    pending: destinations[destinations.length - 1].pending,
    placeholders: card(id).querySelectorAll(".skeleton").length, asked: infoCalls() - infoBefore };
  const release = holdInfo;
  holdInfo = null;
  release();
  await settle(20);
  out.slow.filled = { footer: footer(id), pending: destinations[destinations.length - 1].pending,
    placeholders: card(id).querySelectorAll(".skeleton").length };
  keydown("Escape");

  // Esc while Python hasn't answered closes at once; the late answer lands
  // in nothing.
  holdInfo = true;
  await ToolDialog.open("export", null, { choose: true });
  await settle();
  button(id, "Continue").click();
  keydown("Escape");
  out.escWhilePending = { closed: scrimOf(id).hidden };
  const late = holdInfo;
  holdInfo = null;
  late();
  await settle(20);
  out.escWhilePending.stillClosed = scrimOf(id).hidden;

  // Browse: the Open dialog over this one, which stays where it is.
  let finishBrowse = null;
  ToolDialog.configure({ browse: () => new Promise((resolve) => { finishBrowse = resolve; }) });
  await ToolDialog.open("export", null, { choose: true });
  await settle();
  const browseBtn = card(id).querySelector(".pick-browse");
  const hidesBefore = hides.length;
  browseBtn.focus();
  browseBtn.click();
  out.stacked = { hidden: scrimOf(id).hidden, inert: scrimOf(id).inert,
    under: scrimOf(id).classList.contains("dialog-scrim--under"),
    bodyFlag: document.body.classList.contains("has-stacked-dialog") };
  // Esc meant for the dialog on top doesn't close the one underneath.
  keydown("Escape");
  out.stacked.escKeptParent = !scrimOf(id).hidden;
  // A second dialog over the same one (a password over the Open dialog):
  // when it closes, the first is still over it, so it stays inert.
  const ctxStack = vm.runInContext("stackDialog", ctx);
  let finishInner = null;
  const inner = ctxStack({ scrim: scrimOf(id) }, () => new Promise((resolve) => { finishInner = resolve; }));
  finishInner(null);
  await inner;
  out.stacked.innerClosed = { inert: scrimOf(id).inert, under: scrimOf(id).classList.contains("dialog-scrim--under") };
  finishBrowse(null);
  await settle(20);
  out.stacked.after = { hidden: scrimOf(id).hidden, inert: scrimOf(id).inert,
    under: scrimOf(id).classList.contains("dialog-scrim--under"),
    bodyFlag: document.body.classList.contains("has-stacked-dialog"), focusBack: document.activeElement === browseBtn };
  out.hidesWhileStacked = hides.length - hidesBefore;
  keydown("Escape");

  // From a Recent menu: straight to the tool, no Back.
  await ToolDialog.open("export", "C:/Docs/Report.pdf");
  await settle();
  out.direct = { footer: footer(id), alt: keydown("ArrowLeft", { altKey: true }).defaultPrevented };
  keydown("Escape");

  // Reduced motion: the swap is instant.
  reducedMotion = true;
  await ToolDialog.open("export", null, { choose: true });
  await settle();
  mark = animations.length;
  button(id, "Continue").click();
  await settle(20);
  out.reduced = { animations: animations.length - mark, title: title(id) };
  keydown("Escape");
  reducedMotion = false;

  // ---- Split ---------------------------------------------------------------
  const sid = "splitDialog";
  // Back before Python has answered: the answer, when it comes, draws
  // nothing over the chooser.
  holdInfo = true;
  await SplitDialog.open(null, { choose: true });
  await settle();
  button(sid, "Continue").click();
  keydown("ArrowLeft", { altKey: true });
  const lateSplit = holdInfo;
  holdInfo = null;
  lateSplit();
  await settle(50);
  out.splitBackFirst = { title: title(sid), choosing: SplitDialog.isChoosing(), rows: rows(sid).length };
  keydown("Escape");
  await SplitDialog.open(null, { choose: true });
  await settle();
  out.splitChooser = title(sid);
  button(sid, "Continue").click();
  await settle(200);
  out.splitEvery = { footer: footer(sid), kind: card(sid).querySelector(".save-dest").dataset.kind,
    box: textOf(card(sid).querySelector(".split-trash")),
    outcome: textOf(card(sid).querySelector(".split-trash-outcome")),
    checked: card(sid).querySelector("#splitTrashOriginal").checked };
  const field = card(sid).querySelector(".split-field--ranges");
  field.dispatch("focus");
  field.value = "1–6";
  field.dispatch("input");
  await settle(200);
  out.splitOne = { footer: footer(sid), kind: card(sid).querySelector(".save-dest").dataset.kind,
    iconsMounted: mounted.includes("file"),
    fileStem: destinations.filter((d) => d.kind === "file").map((d) => d.stem) };
  const box = card(sid).querySelector("#splitTrashOriginal");
  box.checked = true;
  box.dispatch("change");
  out.ticked = { outcome: textOf(card(sid).querySelector(".split-trash-outcome")),
    desc: textOf(card(sid).querySelector(".dialog-desc")) };
  field.value = "1–3, 4–6";
  field.dispatch("input");
  await settle(200);
  out.splitTwo = { footer: footer(sid), kind: card(sid).querySelector(".save-dest").dataset.kind,
    outcome: textOf(card(sid).querySelector(".split-trash-outcome")) };
  // On a Mac, Option+← in a field being typed in is the caret's ("a word
  // left"); on Windows, and on an option or a checkbox, it is Back.
  const isStepBackKey = vm.runInContext("isStepBackKey", ctx);
  const alt = (target) => ({ key: "ArrowLeft", altKey: true, ctrlKey: false, metaKey: false, shiftKey: false, target });
  out.altKeys = { macField: isStepBackKey(alt(field), true), windowsField: isStepBackKey(alt(field), false),
    macBox: isStepBackKey(alt(box), true), macCard: isStepBackKey(alt(card(sid)), true) };
  field.value = "4";
  field.dispatch("input");
  await settle(200);
  out.splitPage = footer(sid);
  button(sid, "Save page 4 as a new PDF").click();
  await settle(20);
  out.splitStart = { checked: calls.filter((c) => c[0] === "check_tool_destination").pop()[1],
    options: calls.filter((c) => c[0] === "start_file_job").pop()[3] };
  out.splitWorking = footer(sid);
  SplitDialog.configure({ onOriginalMoved: (path) => moved.push(path), openOutput() {} });
  jobEvent({ id: "job1", state: "done", result: { single: true, files: 1, name: "Report (page 4).pdf",
    path: "C:/Docs/Report (page 4).pdf", where: "beside the original", original: { moved: true, trash_name: "Recycle Bin" } } });
  out.splitDone = { desc: textOf(card(sid).querySelector(".dialog-desc")),
    lines: card(sid).querySelector(".tool-result").children.map(textOf), footer: footer(sid), moved: moved.slice() };
  keydown("Escape");

  // A move that failed, after two files.
  await SplitDialog.open("C:/Docs/Report.pdf");
  await settle(200);
  button(sid, "Split into 2 files").click();
  await settle(20);
  jobEvent({ id: "job1", state: "done", result: { files: 2, name: "Report (split)", path: "C:/Docs/Report (split)",
    where: "beside the original", original: { moved: false, reason: "in_use", error: "it's open in another program.",
    trash_name: "Recycle Bin" } } });
  out.splitKept = { desc: textOf(card(sid).querySelector(".dialog-desc")),
    lines: card(sid).querySelector(".tool-result").children.map(textOf), footer: footer(sid) };
  keydown("Escape");

  // ---- Organise pages' picker ------------------------------------------------
  const accepted = [];
  let allow = false;
  const chosen = PdfPicker.choose("organise", { accept: async (path) => { accepted.push(path); return await allow; } });
  await settle();
  const pid = "pdfPickerScrim";
  out.organiseTitle = title(pid);
  button(pid, "Continue").click();
  await settle(20);
  out.organiseRefused = { open: !scrimOf(pid).hidden, label: button(pid, "Continue").textContent };
  // Cancel while the PDF is opening waits: the reader is about to have it.
  let finishOpening = null;
  allow = new Promise((resolve) => { finishOpening = () => resolve(true); });
  button(pid, "Continue").click();
  await settle();
  button(pid, "Cancel").click();
  keydown("Escape");
  out.organiseCancelWhileOpening = !scrimOf(pid).hidden;
  mark = animations.length;
  finishOpening();
  out.organiseChosen = await chosen;
  // It stays up ("Opening…", the dim) until the reader's first frame, which
  // is Organise's already: nothing fades to a bare Home in between.
  out.organiseAfterOpen = { open: !scrimOf(pid).hidden,
    fades: swaps(mark).filter((a) => a.cls.includes("dialog-scrim")).length };
  out.organiseAccepted = accepted;

  process.stdout.write(JSON.stringify(out));
})().catch((err) => { console.error(err.stack); process.exit(1); });
"""


class _Harness:
    out = None

    @classmethod
    def result(cls) -> dict:
        if cls.out is None:
            cls.out = run_node(HARNESS, SCRIPTS)
        return cls.out


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class SteppedDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _Harness.result()

    def test_the_chooser_is_the_first_step(self):
        chooser = self.out["chooser"]
        self.assertEqual(chooser["title"], "Export as images — choose a PDF")
        self.assertEqual((chooser["rows"], chooser["selected"]), (3, "Report"))
        self.assertEqual(chooser["footer"], ["Cancel", "Continue"])
        self.assertTrue(chooser["choosing"])
        self.assertTrue(chooser["focus"])

    def test_continue_shows_the_tool_in_the_same_card_without_lifting_the_dim(self):
        tool = self.out["tool"]
        self.assertEqual(tool["title"], "Export as images")
        self.assertEqual(tool["footer"], ["Back", "Cancel", "Export 10 images"])
        self.assertIn("dialog-footer-back", tool["firstIsBack"])
        self.assertEqual(tool["status"], "Export as images")
        self.assertTrue(tool["focusInCard"])
        self.assertEqual(self.out["hidesWhileStepping"], [])

    def test_the_step_change_is_one_height_change_and_a_cross_fade_with_no_blank_frame(self):
        forward = self.out["forward"]
        height = [a for a in forward if "dialog-card" in a["cls"]]
        self.assertEqual(len(height), 1)
        self.assertEqual(height[0]["duration"], 200)
        self.assertEqual(height[0]["easing"], "cubic-bezier(0.2, 0, 0, 1)")
        self.assertTrue(all(f.endswith("px") for f in height[0]["frames"]))
        self.assertNotEqual(height[0]["frames"][0], height[0]["frames"][1])
        # The old step's copy fades out over the new one, which is in place
        # underneath at full opacity from the start: no frame is blank.
        ghost = [a for a in forward if "dialog-step-ghost" in a["cls"]][0]
        self.assertEqual((ghost["opacity"], ghost["duration"], ghost["delay"]), ([1, 0], 120, 0))
        fresh = [a for a in forward if a["cls"] in ("dialog-body", "dialog-footer")]
        self.assertEqual(len(fresh), 2)
        for animation in fresh:
            self.assertEqual(animation["frames"][0], "translateX(12px)")
            self.assertEqual(animation["opacity"], [None, None])
            self.assertEqual(animation["delay"], 0)
        backward = [a for a in self.out["backward"] if a["cls"] in ("dialog-body", "dialog-footer")]
        self.assertTrue(backward)
        self.assertTrue(all(a["frames"][0] == "translateX(-12px)" for a in backward))

    def test_reduced_motion_swaps_at_once(self):
        self.assertEqual(self.out["reduced"], {"animations": 0, "title": "Export as images"})

    def test_back_returns_to_the_chooser_with_the_pdf_still_chosen(self):
        back = self.out["back"]
        self.assertTrue(back["prevented"])
        self.assertEqual(back["title"], "Export as images — choose a PDF")
        self.assertEqual((back["selected"], back["focus"]), ("Report", "Report"))
        self.assertEqual(back["status"], "Export as images — choose a PDF")

    def test_the_same_pdf_keeps_the_settings_and_another_resets_what_depends_on_it(self):
        self.assertEqual(self.out["samePdf"], {"infoCalls": 1, "jpeg": "JPEG", "range": True})
        other = self.out["otherPdf"]
        self.assertEqual((other["infoCalls"], other["lastSource"]), (2, "C:/Docs/Notes.pdf"))
        self.assertEqual(other["jpeg"], "JPEG")
        self.assertFalse(other["range"])

    def test_no_back_while_working_or_when_opened_with_a_pdf(self):
        self.assertNotIn("Back", self.out["working"]["footer"])
        self.assertFalse(self.out["working"]["alt"])
        self.assertEqual(self.out["direct"]["footer"], ["Cancel", "Export 10 images"])
        self.assertFalse(self.out["direct"]["alt"])

    def test_a_second_open_while_the_first_reads_is_ignored(self):
        self.assertEqual(self.out["doubleOpen"], 1)

    def test_esc_on_either_step_closes_the_whole_dialog(self):
        self.assertTrue(self.out["escOnChooser"])
        self.assertTrue(self.out["escOnTool"])

    def test_the_step_changes_before_python_answers(self):
        slow = self.out["slow"]
        self.assertEqual(slow["title"], "Export as images")
        self.assertEqual(slow["footer"], ["Back", "Cancel", "Export images"])
        self.assertTrue(slow["primaryDisabled"])
        self.assertTrue(slow["pending"])
        self.assertGreater(slow["placeholders"], 0)
        # Asked once, when the PDF was chosen in the list, not again.
        self.assertEqual(slow["asked"], 0)
        filled = slow["filled"]
        self.assertEqual(filled["footer"], ["Back", "Cancel", "Export 10 images"])
        self.assertFalse(filled["pending"])
        self.assertEqual(filled["placeholders"], 0)

    def test_esc_while_python_hasnt_answered_closes_at_once(self):
        self.assertEqual(self.out["escWhilePending"], {"closed": True, "stillClosed": True})

    def test_another_dialog_opens_over_this_one_which_stays_mounted(self):
        stacked = self.out["stacked"]
        self.assertEqual(stacked["hidden"], False)
        self.assertTrue(stacked["inert"] and stacked["under"] and stacked["bodyFlag"])
        self.assertTrue(stacked["escKeptParent"])
        self.assertEqual(stacked["innerClosed"], {"inert": True, "under": True})
        after = stacked["after"]
        self.assertEqual(
            after, {"hidden": False, "inert": False, "under": False, "bodyFlag": False, "focusBack": True}
        )
        self.assertEqual(self.out["hidesWhileStacked"], 0)


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class SplitOneRangeAndRecycleBinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _Harness.result()

    def test_an_answer_after_back_draws_nothing_over_the_chooser(self):
        self.assertEqual(self.out["splitBackFirst"], {"title": "Split — choose a PDF", "choosing": True, "rows": 3})

    def test_split_is_stepped_too(self):
        self.assertEqual(self.out["splitChooser"], "Split — choose a PDF")
        self.assertEqual(self.out["splitEvery"]["footer"], ["Back", "Cancel", "Split into 2 files"])

    def test_one_range_saves_one_pdf_and_the_button_says_so(self):
        one = self.out["splitOne"]
        self.assertEqual(one["footer"][-1], "Save pages 1–6 as a new PDF")
        self.assertEqual(one["kind"], "file")
        self.assertEqual(one["fileStem"], ["Report (pages 1–6)"])
        # The block swapped in after the card was drawn gets its icons too.
        self.assertTrue(one["iconsMounted"])
        self.assertEqual(self.out["splitPage"][-1], "Save page 4 as a new PDF")
        two = self.out["splitTwo"]
        self.assertEqual((two["footer"][-1], two["kind"]), ("Split into 2 files", "folder"))

    def test_option_left_in_a_text_field_on_a_mac_moves_the_caret_not_back(self):
        self.assertEqual(self.out["altKeys"], {"macField": False, "windowsField": True, "macBox": True, "macCard": True})

    def test_the_recycle_bin_box_and_the_line_under_it(self):
        every = self.out["splitEvery"]
        self.assertEqual(every["box"], "Move the original to the Recycle Bin afterwards")
        self.assertFalse(every["checked"])
        self.assertEqual(every["outcome"], "The original stays as it is.")
        self.assertEqual(
            self.out["ticked"]["outcome"],
            "Report.pdf will be moved to the Recycle Bin once the new file is saved. You can restore it from there.",
        )
        self.assertEqual(self.out["ticked"]["desc"], "Report.pdf · 10 pages. Makes new files.")
        self.assertEqual(
            self.out["splitTwo"]["outcome"],
            "Report.pdf will be moved to the Recycle Bin once the new files are saved. You can restore it from there.",
        )

    def test_the_job_is_asked_for_one_file_and_the_move(self):
        start = self.out["splitStart"]
        self.assertEqual(start["checked"], "split_one")
        self.assertEqual((start["options"]["mode"], start["options"]["value"], start["options"]["trash"]),
                         ("ranges", "4", True))
        self.assertNotIn("Back", self.out["splitWorking"])

    def test_done_says_what_happened_to_the_original(self):
        done = self.out["splitDone"]
        self.assertEqual(done["desc"], "Done.")
        self.assertEqual(done["lines"], ["Saved Report (page 4).pdf. Beside the original.",
                                         "The original was moved to the Recycle Bin."])
        self.assertEqual(done["footer"], ["Show in folder", "Open", "Done"])
        self.assertEqual(done["moved"], ["C:/Docs/Report.pdf"])
        kept = self.out["splitKept"]
        self.assertEqual(kept["desc"], "Done. The original is unchanged.")
        self.assertEqual(kept["lines"][1], "Your new files are saved. The original couldn't be moved to the "
                                           "Recycle Bin: it's open in another program.")
        self.assertEqual(kept["footer"], ["Show in folder", "Done"])


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OrganisePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _Harness.result()

    def test_a_pdf_that_couldnt_open_keeps_the_picker(self):
        self.assertEqual(self.out["organiseTitle"], "Organise pages — choose a PDF")
        self.assertEqual(self.out["organiseRefused"], {"open": True, "label": "Continue"})

    def test_cancel_while_the_pdf_opens_waits(self):
        self.assertTrue(self.out["organiseCancelWhileOpening"])

    def test_the_picker_stays_until_the_reader_takes_over(self):
        self.assertEqual(self.out["organiseChosen"], "C:/Docs/Report.pdf")
        self.assertEqual(self.out["organiseAccepted"], ["C:/Docs/Report.pdf", "C:/Docs/Report.pdf"])
        self.assertEqual(self.out["organiseAfterOpen"], {"open": True, "fades": 0})


if __name__ == "__main__":
    unittest.main()
