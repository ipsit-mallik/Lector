"""The file tools' shared file list (frontend/js/file-list.js, on the shared
frontend/js/pointer-reorder.js) inside the real Combine files dialog
(frontend/pages/combine-dialog.js), with the real save-destination.js and
filedrop.js, run under Node with a stand-in DOM that lays the rows out (60px
each, four visible). Convert to PDF builds its list the same way (checked
from its source below).

What is held here:

* **Empty and with files.** No files: the dashed drop zone with Add files….
  With files: the strip ("3 files · 41 pages", the drop hint, a compact Add
  files…) over the list; the footer has no count any more.
* **Rows.** Position, name, the dialog's muted line, the labels, Move up /
  Move down disabled at the ends, Remove, Alt+↑/↓.
* **Dragging is pointer events only.** No row is `draggable` and nothing
  listens for the browser's drag events, so a drag of a row never becomes a
  native drop (which pywebview would hand to `Api._on_file_drop`). A press
  that moves under 5px is a click. A drag lands where the same Move up /
  Move down presses would; Esc calls it off with nothing changed (and the
  dialog stays open); no bridge call is made by a drag and every thumbnail
  image is the same element afterwards (nothing asked for or decoded again).
  Near the list's bottom edge the drag scrolls it. The preview hangs 40px
  right of and 16px below the pointer; the insertion line spans the row.
* **The scroll position** survives the re-render a move causes.
* **Files dropped from outside** (the window's `lector:filedrop`, what
  Python sends after a real drop) are still added.
* **The name** follows the list (asked of Python with the whole list) until
  the reader types one; a typed name survives a reorder, an addition and a
  removal; emptying the field hands it back to the automatic name, which
  returns when focus leaves.

Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
JS = FRONTEND / "js"
PAGES = FRONTEND / "pages"
SCRIPTS = [JS / "dialog-motion.js", JS / "filedrop.js", JS / "pointer-reorder.js", JS / "file-list.js", JS / "save-destination.js",
           PAGES / "combine-dialog.js"]

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// ---- A stand-in DOM, with just enough layout for the list ----------------
const ROW_H = 60, VISIBLE_ROWS = 4, LIST_TOP = 200, LIST_LEFT = 100, LIST_W = 472;
const nativeDragTypes = new Set(["dragstart", "drag", "dragover", "drop", "dragend"]);
const nativeDragListeners = [];
const animations = [];

function matches(node, selector) {
  return selector.split(",").some((part) => {
    part = part.trim();
    const attr = part.match(/^\[([\w-]+)="([^"]*)"\]$/);
    if (attr) {
      const key = attr[1].startsWith("data-") ? attr[1].slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()) : null;
      return key ? node.dataset[key] === attr[2] : node.attrs[attr[1]] === attr[2];
    }
    const [tag, ...classes] = part.split(".");
    if (tag && node.tagName !== tag.toUpperCase()) return false;
    return classes.every((c) => node.classList.contains(c));
  });
}

function makeEl(tag) {
  const listeners = {};
  const classes = new Set();
  const style = { setProperty(k, v) { this[k] = v; }, getPropertyValue(k) { return this[k] || ""; } };
  const node = {
    tagName: tag.toUpperCase(), children: [], parentElement: null, attrs: {}, dataset: {}, style,
    hidden: false, disabled: false, tabIndex: -1, value: "", placeholder: "", id: "", type: "",
    _scrollTop: 0, _text: "",
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(v) { this._text = String(v); this.children = []; },
    classList: {
      add: (...cs) => cs.forEach((c) => classes.add(c)), remove: (...cs) => cs.forEach((c) => classes.delete(c)),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: (c) => classes.has(c),
    },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(" "); },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(type, fn) {
      if (nativeDragTypes.has(type)) nativeDragListeners.push(type);
      (listeners[type] = listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) { const l = listeners[type] || []; if (l.includes(fn)) l.splice(l.indexOf(fn), 1); },
    dispatch(type, props = {}) {
      const ev = { type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; }, ...props };
      for (let n = this; n && !ev.stopped; n = n.parentElement) (n._listeners()[type] || []).slice().forEach((fn) => fn(ev));
      return ev;
    },
    _listeners: () => listeners,
    appendChild(k) { if (k.parentElement) k.remove(); k.parentElement = this; this.children.push(k); return k; },
    append(...ks) { ks.forEach((k) => this.appendChild(k)); },
    insertBefore(k, ref) {
      if (k.parentElement) k.remove();
      const i = ref ? this.children.indexOf(ref) : -1;
      k.parentElement = this;
      if (i < 0) this.children.push(k); else this.children.splice(i, 0, k);
      return k;
    },
    getAnimations: () => [],
    replaceChildren(...ks) { this.children.forEach((c) => { c.parentElement = null; }); this.children = []; this.append(...ks); },
    remove() { const p = this.parentElement; if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentElement = null; } },
    replaceWith(k) { const p = this.parentElement; const i = p.children.indexOf(this); this.parentElement = null; k.parentElement = p; p.children[i] = k; },
    contains(k) { for (let n = k; n; n = n.parentElement) if (n === this) return true; return false; },
    closest(sel) { for (let n = this; n; n = n.parentElement) if (n.tagName && matches(n, sel)) return n; return null; },
    querySelectorAll(sel) { const out = []; const walk = (n) => n.children.forEach((c) => { if (matches(c, sel)) out.push(c); walk(c); }); walk(this); return out; },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    get isConnected() { let n = this; while (n.parentElement) n = n.parentElement; return n === document.body; },
    focus() { document.activeElement = this; },
    setPointerCapture() {}, releasePointerCapture() {}, hasPointerCapture: () => false,
    setSelectionRange() {}, selectionStart: 0, selectionEnd: 0,
    animate(frames, options) { animations.push({ node: this, frames, options }); return { finished: Promise.resolve(), cancel() {} }; },
    get scrollTop() { return this._scrollTop; },
    set scrollTop(v) {
      const next = Math.max(0, Math.min(v, this.scrollHeight - this.clientHeight));
      if (next === this._scrollTop) return;
      this._scrollTop = next;
      // As a browser does: a scroll event follows, before the next frame.
      setTimeout(() => this.dispatch("scroll"), 0);
    },
    get clientHeight() { return this.classList.contains("file-list-scroll") ? VISIBLE_ROWS * ROW_H : 0; },
    get scrollHeight() {
      const list = this.querySelector(".file-list");
      return list ? list.querySelectorAll(".file-list-row").length * ROW_H : 0;
    },
    get offsetTop() { return this.classList.contains("file-list-row") ? this.parentElement.querySelectorAll(".file-list-row").indexOf(this) * ROW_H : 0; },
    get offsetHeight() { return this.classList.contains("file-list-row") ? ROW_H : 0; },
    getBoundingClientRect() {
      const scroller = this.closest(".file-list-scroll");
      const shift = scroller && scroller !== this ? scroller.scrollTop : 0;
      let top = 0, height = 0;
      if (this.classList.contains("file-list-scroll")) { top = LIST_TOP; height = VISIBLE_ROWS * ROW_H; }
      else if (this.classList.contains("file-list")) { top = LIST_TOP - shift; height = this.querySelectorAll(".file-list-row").length * ROW_H; }
      else if (this.classList.contains("file-list-row")) { top = LIST_TOP - shift + this.offsetTop; height = ROW_H; }
      return { left: LIST_LEFT, right: LIST_LEFT + LIST_W, top, bottom: top + height, width: LIST_W, height };
    },
  };
  return node;
}

const docListeners = {};
const document = {
  createElement: makeEl, activeElement: null, documentElement: makeEl("html"),
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
  removeEventListener(type, fn) { const l = docListeners[type] || []; if (l.includes(fn)) l.splice(l.indexOf(fn), 1); },
  querySelector: () => null,
};
document.body = makeEl("body");

const winListeners = { capture: {}, bubble: {} };
const window = {
  addEventListener(type, fn, capture) { const bag = capture ? winListeners.capture : winListeners.bubble; (bag[type] = bag[type] || []).push(fn); },
  removeEventListener(type, fn, capture) { const l = (capture ? winListeners.capture : winListeners.bubble)[type] || []; if (l.includes(fn)) l.splice(l.indexOf(fn), 1); },
  dispatchEvent(ev) {
    for (const bag of [winListeners.capture, winListeners.bubble]) {
      for (const fn of (bag[ev.type] || []).slice()) { if (ev.stopped) return; fn(ev); }
    }
  },
};

// ---- What the dialog needs from elsewhere -------------------------------
const calls = [];
const toasts = [];
const base = (p) => p.split(/[\\/]/).pop();
const stemOf = (p) => base(p).replace(/\.[^.]+$/, "");
let thumbnailsAsked = 0;
function describe(paths) {
  return paths.map((path) => {
    const pdf = path.toLowerCase().endsWith(".pdf");
    return { path, name: base(path), type: pdf ? "PDF" : "JPEG", size: 1000, pages: pdf ? 20 : 1, pdf,
      thumbnail: `THUMB-${stemOf(path)}` };
  });
}
async function callApi(method, ...args) {
  calls.push([method, ...args]);
  if (method === "describe_combine_inputs") return describe(args[0]);
  if (method === "suggest_tool_name") return { stem: `auto:${(args[3] || []).map(stemOf).join("+")}`, extension: ".pdf" };
  if (method === "check_output_folder") return { ok: true };
  if (/thumbnail/.test(method)) thumbnailsAsked += 1;
  return { ok: true };
}

const context = vm.createContext({
  window, document, console, setTimeout, clearTimeout, queueMicrotask, Promise, Map, WeakMap, Set, Math, JSON, String, Array,
  requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame: clearTimeout,
  matchMedia: () => ({ matches: false }),
  getComputedStyle: () => ({ columnGap: "0px", rowGap: "0px", getPropertyValue: () => "" }),
  callApi, showToast: (t) => toasts.push(t),
  el(tag, className, text) { const n = makeEl(tag); if (className) n.className = className; if (text !== undefined) n.textContent = text; return n; },
  iconEl(name) { const n = makeEl("span"); n.dataset.icon = name; return n; },
  mountIcons: async () => {}, mountIcon: async () => {}, mountMissingIcons: async () => {},
  createVoiceScope: () => ({ enter() {}, leave() {} }), trapFocus: () => () => {},
  FileJobs: { subscribe() {}, watch() {} },
  PasswordDialog: { ask: async () => null }, ReplaceDialog: { open: async () => true },
  dirName: (p) => p.replace(/[\\/][^\\/]*$/, ""),
  plural: (n, one, many) => `${n} ${n === 1 ? one : many}`,
  formatFileSize: () => "1 KB", listPages: (names) => names.join(", "),
  savePlaceClause: () => "", showFileJobOutput() {}, isTypingTarget: () => false,
  fileJobFraction: () => null, fileJobPhaseText: () => "",
});
for (const file of process.argv.slice(2)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
const run = (expr) => vm.runInContext(expr, context);
const settle = () => new Promise((r) => setTimeout(r, 5));

const dialog = () => document.body.querySelector(".dialog-card");
const list = () => dialog().querySelector(".file-list");
const scroller = () => dialog().querySelector(".file-list-scroll");
const rows = () => list().querySelectorAll(".file-list-row");
const names = () => rows().map((r) => r.querySelector(".file-list-name").textContent);
const stems = () => names().map((n) => n.replace(/\.[^.]+$/, ""));
const nameField = () => dialog().querySelector(".save-dest-input");
const button = (row, label) => row.querySelectorAll("button").find((b) => b.getAttribute("aria-label").startsWith(label));
const rowCenter = (i) => LIST_TOP + i * ROW_H + ROW_H / 2 - scroller().scrollTop;

function pointer(target, type, x, y) {
  const props = { button: 0, isPrimary: true, pointerId: 1, clientX: x, clientY: y, timeStamp: 1 };
  if (type === "pointerdown") return target.dispatch(type, props);
  const ev = { type, target, preventDefault() {}, stopPropagation() {}, ...props };
  window.dispatchEvent(ev);
  return ev;
}
// Press row `from`'s handle and move to y, frame by frame; release unless asked not to.
async function dragRow(from, toY, { release = true, steps = 4 } = {}) {
  const handle = rows()[from].querySelector(".file-list-handle");
  const y0 = rowCenter(from);
  pointer(handle, "pointerdown", 120, y0);
  for (let i = 1; i <= steps; i++) { pointer(handle, "pointermove", 120, y0 + ((toY - y0) * i) / steps); await settle(); }
  if (release) { pointer(handle, "pointerup", 120, toY); await settle(); }
}
const dropFiles = (paths) => window.dispatchEvent({ type: "lector:filedrop", detail: { combinable: paths } });

(async () => {
  const out = {};
  const Combine = run("CombineDialog");
  Combine.open();
  await settle();
  const zone = dialog().querySelector(".drop-zone");
  out.empty = { zone: Boolean(zone), text: zone && zone.querySelector("span").children[1].textContent,
                add: zone && zone.querySelector("button").textContent, list: Boolean(dialog().querySelector(".file-list-row")) };

  // Files dropped from outside the app: the window's lector:filedrop.
  dropFiles(["C:/docs/Report.pdf", "C:/docs/Appendix.pdf", "C:/docs/Photo.jpg"]);
  await settle();
  out.dropped = names();
  const strip = dialog().querySelector(".file-list-strip");
  out.strip = { count: strip.querySelector(".file-list-count").textContent,
                hint: strip.querySelector(".file-list-hint").textContent,
                add: strip.querySelector("button").className, zone: Boolean(dialog().querySelector(".drop-zone")),
                footer: dialog().querySelector(".dialog-footer").children.map((b) => b.textContent) };
  out.rows = rows().map((r) => ({
    number: r.querySelector(".file-list-number").textContent, meta: r.querySelector(".file-list-meta").textContent,
    label: r.getAttribute("aria-label"), draggable: Boolean(r.draggable),
    disabled: r.querySelectorAll("button").map((b) => b.disabled),
  }));
  out.name = nameField().value;

  // More files, so the list scrolls (4 rows show). The rows already there,
  // and their thumbnails, are the same objects afterwards (no re-render),
  // and so is the footer's primary button; the new rows show at once, with
  // their names and placeholders, before Python has described them.
  const rowsBeforeAdd = rows();
  const imgsBeforeAdd = rowsBeforeAdd.map((r) => r.querySelector("img"));
  const areaBeforeAdd = dialog().querySelector(".file-list-area");
  const primaryBeforeAdd = dialog().querySelector(".dialog-footer").children[1];
  dropFiles(["C:/docs/a.jpg", "C:/docs/b.jpg", "C:/docs/c.jpg", "C:/docs/d.jpg"]);
  out.pendingRows = { names: names(), pending: rows().map((r) => r.getAttribute("aria-busy")) };
  await settle();
  out.seven = names();
  // A drop of files already listed: skipped (each file has one row), with
  // the same toast as Add files….
  const toastsBefore = toasts.length;
  dropFiles(["C:/docs/a.jpg", "C:/docs/Report.pdf"]);
  await settle();
  out.dropAgain = { names: names(), toasts: toasts.splice(toastsBefore) };
  // Two batches back to back, the second while the first is still being
  // described: both join the list, in order.
  dropFiles(["C:/docs/e.jpg"]);
  dropFiles(["C:/docs/f.jpg"]);
  out.backToBack = { atOnce: names().slice(-2) };
  await settle();
  await settle();
  out.backToBack.described = { names: names().slice(-2),
    pending: rows().slice(-2).map((r) => r.getAttribute("aria-busy")) };
  // Out again, so the rest of the run starts from the seven files.
  for (let i = 0; i < 2; i++) { button(rows()[rows().length - 1], "Remove").dispatch("click"); await settle(); }
  out.keptOnAdd = {
    rows: rowsBeforeAdd.map((r, i) => rows()[i] === r),
    imgs: imgsBeforeAdd.map((img, i) => Boolean(img) && rows()[i].querySelector("img") === img),
    area: dialog().querySelector(".file-list-area") === areaBeforeAdd,
    primary: dialog().querySelector(".dialog-footer").children[1] === primaryBeforeAdd,
  };
  out.nativeDragListeners = nativeDragListeners.slice();
  out.anyDraggable = rows().some((r) => r.draggable);

  // The same move by buttons and by a drag: row 0 to after row 2.
  const before = names();
  const imgsBefore = new Map(rows().map((r) => [r.querySelector(".file-list-name").textContent, r.querySelector("img")]));
  button(rows()[0], "Move down").dispatch("click"); await settle();
  button(rows()[1], "Move down").dispatch("click"); await settle();
  out.byButtons = names();
  // Back to the start, by Alt+↑ twice on the moved row.
  rows()[2].dispatch("keydown", { key: "ArrowUp", altKey: true }); await settle();
  rows()[1].dispatch("keydown", { key: "ArrowUp", altKey: true }); await settle();
  out.backToStart = JSON.stringify(names()) === JSON.stringify(before);
  calls.length = 0;
  animations.length = 0;
  // Drop over the lower half of row 2: after it.
  const dropY = rowCenter(2) + 10;
  await dragRow(0, dropY, { release: false });
  out.during = {
    lifted: rows()[0].classList.contains("is-dragging"),
    preview: (() => { const p = document.body.querySelector(".file-list-lift"); return p && { transform: p.style.transform, name: p.querySelector(".file-list-name").textContent }; })(),
    bar: (() => { const b = list().querySelector(".file-list-insertion"); return { hidden: b.hidden, top: b.style.top, left: b.style.left, width: b.style.width }; })(),
  };
  pointer(rows()[0], "pointerup", 120, dropY);
  await settle();
  out.byDrag = names();
  out.dragCalls = calls.filter((c) => c[0] !== "suggest_tool_name").map((c) => c[0]);
  out.nameAskedWith = calls.filter((c) => c[0] === "suggest_tool_name").map((c) => c[4].map(stemOf));
  out.flip = animations.filter((a) => a.node.classList.contains("file-list-row")).length;
  out.sameImages = rows().every((r) => imgsBefore.get(r.querySelector(".file-list-name").textContent) === r.querySelector("img"));
  out.previewGone = !document.body.querySelector(".file-list-lift");
  out.thumbnailsAsked = thumbnailsAsked;

  // A press that moves 3px is no drag.
  const handle = rows()[1].querySelector(".file-list-handle");
  pointer(handle, "pointerdown", 120, rowCenter(1));
  pointer(handle, "pointermove", 122, rowCenter(1) + 2);
  pointer(handle, "pointerup", 122, rowCenter(1) + 2);
  await settle();
  out.click = { order: names(), preview: Boolean(document.body.querySelector(".file-list-lift")) };

  // Esc calls a drag off: nothing moves and the dialog stays open.
  const beforeEsc = names();
  await dragRow(1, rowCenter(3), { release: false });
  const esc = { type: "keydown", key: "Escape", preventDefault() {}, stopPropagation() { this.stopped = true; } };
  window.dispatchEvent(esc);
  if (!esc.stopped) (docListeners.keydown || []).forEach((fn) => fn(esc));
  await settle();
  pointer(rows()[1], "pointerup", 120, rowCenter(3));
  await settle();
  out.esc = { same: JSON.stringify(names()) === JSON.stringify(beforeEsc), open: Combine.isOpen(),
              lifted: rows().some((r) => r.classList.contains("is-dragging")), bar: list().querySelector(".file-list-insertion").hidden };

  // Near the bottom edge the drag scrolls the list, and the scroll survives the drop.
  scroller().scrollTop = 0;
  const edgeY = LIST_TOP + VISIBLE_ROWS * ROW_H - 4;
  await dragRow(0, edgeY, { release: false, steps: 2 });
  for (let i = 0; i < 6; i++) await settle();
  out.autoscroll = scroller().scrollTop;
  const firstBefore = names()[0];
  pointer(rows()[0], "pointerup", 120, edgeY);
  await settle();
  out.scrollAfterDrop = scroller().scrollTop;
  out.firstMoved = names()[0] !== firstBefore;
  // The dropped row has focus and is wholly in view (the list may scroll on
  // a little to show it, never back).
  const focusedRow = document.activeElement && document.activeElement.closest(".file-list-row");
  out.droppedRowInView = Boolean(focusedRow) && focusedRow.offsetTop >= scroller().scrollTop &&
    focusedRow.offsetTop + ROW_H <= scroller().scrollTop + VISIBLE_ROWS * ROW_H;

  // The name: typed, it survives a reorder, an addition and a removal.
  const field = nameField();
  field.value = "My bundle";
  field.dispatch("input");
  button(rows()[0], "Move down").dispatch("click"); await settle();
  dropFiles(["C:/docs/e.jpg"]); await settle();
  button(rows()[3], "Remove").dispatch("click"); await settle();
  out.typedKept = nameField().value;
  // Emptied: the automatic name is back once focus leaves, and follows again.
  // A drop from outside changes the list while the reader is still in the field.
  field.focus();
  field.value = "";
  field.dispatch("input");
  dropFiles(["C:/docs/f.jpg"]); await settle();
  out.whileEmpty = { value: nameField().value, placeholder: nameField().placeholder };
  document.activeElement = null;
  field.dispatch("blur");
  await settle();
  out.restored = nameField().value;
  out.restoredMatchesList = nameField().value === `auto:${stems().join("+")}`;
  button(rows()[0], "Move down").dispatch("click"); await settle();
  out.followsAgain = nameField().value === `auto:${stems().join("+")}`;

  out.toasts = toasts;
  // A pending row settled: described in place, or gone; a row removed
  // meanwhile stays removed.
  const listed = [{ path: "C:/docs/a.pdf", name: "a.pdf" }, { path: "C:/Docs/B.pdf", name: "B.pdf", pending: true }];
  out.settled = {
    described: run("settlePendingFile")(listed, "c:/docs/b.pdf", { path: "C:/Docs/B.pdf", name: "B.pdf", pages: 3 }),
    gone: run("settlePendingFile")(listed, "C:/Docs/B.pdf", null).map((f) => f.name),
    removed: run("settlePendingFile")(listed.slice(0, 1), "C:/Docs/B.pdf", { name: "B.pdf" }).map((f) => f.name),
    waiting: [run("hasPendingFile")(listed, "c:/docs/b.pdf"), run("hasPendingFile")(listed, "C:/docs/a.pdf")],
    untouched: listed.length,
  };
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class FileListInCombineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(["node", str(harness), *map(str, SCRIPTS)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_no_files_is_the_drop_zone(self):
        self.assertEqual(self.out["empty"], {"zone": True, "text": "Drop PDFs or images here", "add": "Add files…", "list": False})

    def test_with_files_the_strip_and_the_count_moves_out_of_the_footer(self):
        self.assertEqual(self.out["strip"], {
            "count": "3 files · 41 pages",
            "hint": "Drop more files anywhere on the list",
            "add": "btn btn-secondary btn-sm",
            "zone": False,
            "footer": ["Cancel", "Combine · 41 pages"],
        })

    def test_the_rows(self):
        rows = self.out["rows"]
        self.assertEqual([(r["number"], r["meta"]) for r in rows],
                         [("1", "PDF · 20 pages"), ("2", "PDF · 20 pages"), ("3", "JPEG · 1 KB")])
        self.assertEqual(rows[0]["label"], "1. Report.pdf. Alt+Up or Alt+Down moves it.")
        self.assertEqual([r["disabled"] for r in rows], [[True, False, False], [False, False, False], [False, True, False]])

    def test_files_dropped_from_outside_are_added(self):
        self.assertEqual(self.out["dropped"], ["Report.pdf", "Appendix.pdf", "Photo.jpg"])
        self.assertEqual(self.out["seven"][3:], ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])

    def test_adding_files_keeps_the_rows_already_there_and_shows_the_new_ones_at_once(self):
        kept = self.out["keptOnAdd"]
        self.assertEqual(kept["rows"], [True, True, True])
        self.assertEqual(kept["imgs"], [True, True, True])
        self.assertTrue(kept["area"])
        self.assertTrue(kept["primary"])
        pending = self.out["pendingRows"]
        self.assertEqual(pending["names"][3:], ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
        self.assertEqual(pending["pending"], ["false"] * 3 + ["true"] * 4)

    def test_files_added_while_others_are_described_are_not_dropped(self):
        both = self.out["backToBack"]
        self.assertEqual(both["atOnce"], ["e.jpg", "f.jpg"])
        self.assertEqual(both["described"], {"names": ["e.jpg", "f.jpg"], "pending": ["false", "false"]})

    def test_a_drop_of_files_already_listed_adds_no_second_row(self):
        again = self.out["dropAgain"]
        self.assertEqual(again["names"], self.out["seven"])
        self.assertEqual(again["toasts"], ["2 files were already in the list"])

    def test_no_native_drag_and_drop_so_a_drag_never_reaches_the_file_drop(self):
        # Only filedrop.js's window-level listeners (for files from outside)
        # hear the browser's drag events; no row or list does.
        self.assertEqual(self.out["nativeDragListeners"], [])
        self.assertFalse(self.out["anyDraggable"])
        self.assertFalse(any(r["draggable"] for r in self.out["rows"]))

    def test_a_drag_lands_where_the_same_button_moves_do(self):
        self.assertTrue(self.out["backToStart"])
        self.assertEqual(self.out["byDrag"], self.out["byButtons"])
        self.assertEqual(self.out["byDrag"][:3], ["Appendix.pdf", "Photo.jpg", "Report.pdf"])

    def test_while_dragging_the_preview_hangs_off_the_pointer_and_the_line_spans_the_row(self):
        during = self.out["during"]
        self.assertTrue(during["lifted"])
        self.assertEqual(during["preview"]["name"], "Report.pdf")
        # Pointer at (120, row 2's centre + 10 = 360): the preview 40 right, 16 below.
        self.assertEqual(during["preview"]["transform"], "translate3d(160px, 376px, 0)")
        # After row 2: the line at its bottom (180px into the list), 2px thick, full width.
        self.assertEqual(during["bar"], {"hidden": False, "top": "179px", "left": "0px", "width": "472px"})

    def test_a_drag_asks_nothing_of_python_and_redraws_no_thumbnail(self):
        # Only the automatic name follows the new order (asked with the whole list).
        self.assertEqual(self.out["dragCalls"], [])
        self.assertEqual(self.out["nameAskedWith"][-1][:3], ["Appendix", "Photo", "Report"])
        self.assertTrue(self.out["sameImages"])
        self.assertEqual(self.out["thumbnailsAsked"], 0)
        self.assertTrue(self.out["previewGone"])
        self.assertGreater(self.out["flip"], 0)  # the rows slide to their places

    def test_a_short_press_is_a_click(self):
        self.assertEqual(self.out["click"]["order"], self.out["byDrag"])
        self.assertFalse(self.out["click"]["preview"])

    def test_esc_calls_a_drag_off_and_leaves_the_dialog_open(self):
        self.assertEqual(self.out["esc"], {"same": True, "open": True, "lifted": False, "bar": True})

    def test_near_the_bottom_edge_the_list_scrolls_and_the_drop_keeps_the_scroll(self):
        self.assertGreater(self.out["autoscroll"], 0)
        # Not back to the top: the scroll survives the re-render a drop causes.
        self.assertGreaterEqual(self.out["scrollAfterDrop"], self.out["autoscroll"])
        self.assertTrue(self.out["droppedRowInView"])
        self.assertTrue(self.out["firstMoved"])

    def test_the_name_follows_the_list_until_typed_and_comes_back_when_emptied(self):
        self.assertEqual(self.out["name"], "auto:Report+Appendix+Photo")
        self.assertEqual(self.out["typedKept"], "My bundle")
        # Empty while typing: not refilled under the reader's fingers, only the placeholder.
        self.assertEqual(self.out["whileEmpty"]["value"], "")
        self.assertTrue(self.out["whileEmpty"]["placeholder"].startswith("auto:"))
        self.assertTrue(self.out["restoredMatchesList"], self.out["restored"])
        self.assertTrue(self.out["followsAgain"])

    def test_a_pending_row_settles_in_place_or_goes_and_a_removed_one_stays_removed(self):
        settled = self.out["settled"]
        self.assertEqual(settled["described"][1], {"path": "C:/Docs/B.pdf", "name": "B.pdf", "pages": 3})
        self.assertEqual(settled["gone"], ["a.pdf"])
        self.assertEqual(settled["removed"], ["a.pdf"])
        self.assertEqual(settled["waiting"], [True, False])
        self.assertEqual(settled["untouched"], 2)

    def test_nothing_went_wrong(self):
        self.assertEqual(self.out["toasts"], [])


class ConvertBuildsTheSameListTests(unittest.TestCase):
    def test_convert_and_combine_use_the_shared_list_and_sized_card(self):
        index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index('src="js/pointer-reorder.js"'), index.index('src="js/file-list.js"'))
        self.assertLess(index.index('src="js/file-list.js"'), index.index('src="pages/convert-dialog.js"'))
        self.assertLess(index.index('src="js/file-list.js"'), index.index('src="pages/combine-dialog.js"'))
        for name, zone in (("convert-dialog.js", "Drop images or an e-book here"), ("combine-dialog.js", "Drop PDFs or images here")):
            with self.subTest(dialog=name):
                source = (PAGES / name).read_text(encoding="utf-8")
                self.assertIn("createFileList({", source)
                self.assertIn(f'files.build(s.files, {{ emptyText: "{zone}"', source)
                self.assertIn("dialog-card--file-list", source)
                self.assertIn("inputPaths()", source)  # the name is asked for with the whole list
                self.assertNotIn("tool-footer-count", source)

    def test_combines_row_line_and_its_rules(self):
        source = (PAGES / "combine-dialog.js").read_text(encoding="utf-8")
        self.assertIn('`PDF · ${plural(file.pages, "page", "pages")}`', source)
        self.assertIn('`${pages} · password entered`', source)
        self.assertIn("s.files.length >= 2", source)  # Combine needs two files
        self.assertIn('callApi("forget_combine_passwords")', source)

    def test_the_list_has_no_native_drag(self):
        source = (JS / "file-list.js").read_text(encoding="utf-8")
        self.assertNotIn("draggable = true", source)
        for word in ("dragstart", "dragover", "setData", "effectAllowed"):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
