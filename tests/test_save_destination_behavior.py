"""Behaviour of frontend/js/save-destination.js (Milestone F1, F1.10): the file
tools' Name + "Save in" block, and how the result line and the finished-job
toast say where the output went (frontend/js/file-jobs.js).

Held here: the block starts beside the original every time; the extension is
outside the editable name; an empty name or one with \\ / : * ? " < > |
shows the error line and makes the block invalid; the menu opens from the
keyboard with focus on the chosen place, lists RECENT only when there is one,
and Esc closes the menu only (the dialog behind never sees the key); a
folder that can't be written to blocks the run until another is chosen; the
name follows the folder until the reader types one.

Run under Node like tests/test_replace_dialog_behavior.py, with a small fake
DOM. Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "js"
SCRIPTS = [ROOT / "file-jobs.js", ROOT / "save-destination.js"]

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// ---- A small DOM: elements with children, attributes, classes, focus, and
// events that bubble up to the document unless stopPropagation is called.
const docListeners = {};
function fakeElement(tag) {
  const classes = new Set();
  const el = {
    tagName: tag.toUpperCase(), children: [], parent: null, attrs: {}, listeners: {},
    dataset: {}, style: {}, hidden: false, id: "", title: "", type: "", value: "", htmlFor: "",
    tabIndex: 0, spellcheck: true, autocomplete: "", selectionStart: 0, selectionEnd: 0,
    _text: "",
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle(c, force) { const on = force === undefined ? !classes.has(c) : force; on ? classes.add(c) : classes.delete(c); return on; },
    },
    get className() { return [...classes].join(" "); },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(v) { this._text = String(v); this.children = []; },
    get isConnected() { let n = this; while (n.parent) n = n.parent; return n === body; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    appendChild(kid) { if (kid.parent) kid.remove(); kid.parent = this; this.children.push(kid); return kid; },
    append(...kids) { kids.forEach((k) => this.appendChild(k)); },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; },
    contains(other) { for (let n = other; n; n = n.parent) if (n === this) return true; return false; },
    focus() { document.activeElement = this; },
    click() { dispatch(this, "click", {}); },
    setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
    getBoundingClientRect: () => ({ top: 600, left: 0, width: 400, height: 36 }),
    get scrollHeight() { return 300; },
    querySelectorAll(sel) {
      const m = /^\[role='(\w+)'\]$/.exec(sel);
      const out = [];
      const walk = (n) => n.children.forEach((c) => { if (m ? c.attrs.role === m[1] : c.classList.contains(sel.slice(1))) out.push(c); walk(c); });
      walk(this);
      return out;
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  return el;
}
function dispatch(target, type, init) {
  let stopped = false;
  const ev = { type, target, defaultPrevented: false, ...init,
    preventDefault() { ev.defaultPrevented = true; }, stopPropagation() { stopped = true; } };
  for (let n = target; n && !stopped; n = n.parent) (n.listeners[type] || []).forEach((fn) => fn(ev));
  if (!stopped) (docListeners[type] || []).forEach((fn) => fn(ev));
  return ev;
}
const body = fakeElement("body");
const document = {
  activeElement: null, body,
  createElement: (tag) => fakeElement(tag),
  addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) { docListeners[type] = (docListeners[type] || []).filter((f) => f !== fn); },
};

// ---- The bridge, answered here.
const calls = [];
const unwritable = new Set();
let places = { recent: [], known: [] };
const answers = {
  check_output_folder: (folder) => ({ ok: !unwritable.has(folder) }),
  get_save_places: () => places,
  remember_output_folder: () => ({ ok: true }),
};
function callApi(name, ...args) {
  calls.push([name, ...args]);
  return Promise.resolve(answers[name] ? answers[name](...args) : null);
}
const context = vm.createContext({ document, callApi, console, window: { addEventListener() {} },
  showToast() {}, setTimeout, clearTimeout });
for (const file of process.argv.slice(1)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
vm.runInContext("this.api = { createSaveDestination, savePlaceClause, fileJobOutcomeToast, saveDestNameError };", context);
const { createSaveDestination, savePlaceClause, fileJobOutcomeToast } = context.api;

const flush = () => new Promise((r) => setTimeout(r, 0));
const find = (root, cls) => root.querySelector("." + cls);
const key = (el, k) => dispatch(el, "keydown", { key: k });
let dialogSawEscape = 0;
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") dialogSawEscape += 1; });

function make(options = {}) {
  const changes = [];
  const dest = createSaveDestination({
    kind: "file", extension: ".pdf", stem: "Report (compressed)", originFolder: "D:\\Reports",
    suggestName: async (folder) => `free in ${folder.split("\\").pop()}`, pickFolder: async () => "E:\\Picked",
    onChange: () => changes.push(1), ...options,
  });
  body.appendChild(dest.element);
  return { dest, changes, el: dest.element };
}
function typeName(el, text) {
  const input = find(el, "save-dest-input");
  input.value = text;
  dispatch(input, "input", {});
}
const optionNames = (el) => el.querySelectorAll("[role='option']").map((o) => find(o, "save-dest-option-name").textContent);
const heads = (el) => el.querySelectorAll(".save-dest-group-head").map((h) => h.textContent);

const results = {};
async function check(name, fn) {
  try { results[name] = (await fn()) === true || "assertion false"; }
  catch (err) { results[name] = `threw: ${err.message}`; }
  body.children = [];
}

(async () => {
  await check("it starts beside the original, on every new dialog", async () => {
    places = { recent: [], known: [{ key: "documents", name: "Documents", path: "C:\\Docs" }] };
    const first = make();
    await flush();
    const button = find(first.el, "save-dest-button");
    const startsRight = find(button, "save-dest-folder").textContent === "Reports" &&
      find(button, "save-dest-where").textContent === "beside the original" &&
      first.dest.value().folder === "D:\\Reports";
    key(button, "Enter");
    await flush();
    first.el.querySelectorAll("[role='option']")[1].click(); // somewhere else
    await flush();
    const moved = first.dest.value().folder === "C:\\Docs";
    const second = make();
    await flush();
    return startsRight && moved && second.dest.value().folder === "D:\\Reports" &&
      find(second.el, "save-dest-where").textContent === "beside the original";
  });

  await check("the extension sits outside the editable name", async () => {
    const { dest, el } = make();
    await flush();
    const ext = find(el, "save-dest-ext");
    typeName(el, "  Annual  ");
    const folderKind = make({ kind: "folder", extension: "", stem: "Report (images)" });
    return ext.textContent === ".pdf" && find(el, "save-dest-input").value === "  Annual  " &&
      dest.value().name === "Annual" && !find(folderKind.el, "save-dest-ext") &&
      find(folderKind.el, "save-dest-label").textContent === "Folder" &&
      find(el, "save-dest-label").textContent === "Name";
  });

  await check("an empty name or a character Windows refuses shows the error and blocks", async () => {
    const { dest, el, changes } = make();
    await flush();
    const error = el.querySelectorAll(".save-dest-error")[0];
    const before = dest.isValid() && error.hidden;
    typeName(el, "");
    const empty = !dest.isValid() && !error.hidden && error.textContent === "Type a name.";
    const illegal = ["\\", "/", ":", "*", "?", '"', "<", ">", "|"].every((c) => {
      typeName(el, `Report${c}`);
      return !dest.isValid() && error.textContent.startsWith("A name can't contain");
    });
    typeName(el, "Report");
    return before && empty && illegal && dest.isValid() && error.hidden && changes.length >= 11;
  });

  await check("Enter opens the menu on the chosen place; RECENT shows only when there is one", async () => {
    places = { recent: [], known: [{ key: "documents", name: "Documents", path: "C:\\Users\\r\\Documents" }] };
    const a = make();
    await flush();
    const button = find(a.el, "save-dest-button");
    const ev = key(button, "Enter");
    await flush();
    const menu = find(a.el, "save-dest-menu");
    const noRecent = JSON.stringify(heads(a.el)) === JSON.stringify(["On this PC"]);
    const focusOnChosen = document.activeElement.getAttribute("aria-selected") === "true" &&
      find(document.activeElement, "save-dest-option-name").textContent === "Beside the original";
    const order = JSON.stringify(optionNames(a.el)) ===
      JSON.stringify(["Beside the original", "Documents", "Choose another folder…"]);
    places = { recent: [{ path: "E:\\Notes", name: "Notes" }], known: places.known };
    const b = make();
    await flush();
    key(find(b.el, "save-dest-button"), " ");
    await flush();
    const withRecent = JSON.stringify(heads(b.el)) === JSON.stringify(["Recent", "On this PC"]) &&
      optionNames(b.el)[1] === "Notes";
    return ev.defaultPrevented && Boolean(menu) && menu.getAttribute("role") === "listbox" &&
      button.getAttribute("aria-expanded") === "true" && noRecent && focusOnChosen && order && withRecent;
  });

  await check("Esc closes the menu only, back to the button; the dialog never sees it", async () => {
    const { dest, el } = make();
    await flush();
    const button = find(el, "save-dest-button");
    key(button, "Enter");
    await flush();
    dialogSawEscape = 0;
    key(document.activeElement, "Escape");
    const menuGone = !find(el, "save-dest-menu") && !dest.isMenuOpen();
    return menuGone && el.isConnected && dialogSawEscape === 0 && document.activeElement === button &&
      button.getAttribute("aria-expanded") === "false";
  });

  await check("arrows, Home and End move; Enter picks and closes", async () => {
    places = { recent: [{ path: "E:\\Notes", name: "Notes" }],
      known: [{ key: "desktop", name: "Desktop", path: "C:\\Users\\r\\Desktop" }] };
    const { dest, el } = make();
    await flush();
    const button = find(el, "save-dest-button");
    key(button, "Enter");
    await flush();
    const items = el.querySelectorAll("[role='option']");
    key(document.activeElement, "ArrowDown");
    const second = document.activeElement === items[1];
    key(document.activeElement, "End");
    const last = document.activeElement === items[items.length - 1];
    key(document.activeElement, "Home");
    key(document.activeElement, "ArrowDown");
    key(document.activeElement, "ArrowDown");
    calls.length = 0;
    key(document.activeElement, "Enter");
    await flush();
    const picked = dest.value().folder === "C:\\Users\\r\\Desktop" && !dest.isMenuOpen() &&
      find(button, "save-dest-folder").textContent === "Desktop" &&
      find(button, "save-dest-where").textContent === "C:\\Users\\r\\Desktop";
    const notRemembered = !calls.some((c) => c[0] === "remember_output_folder");
    return second && last && picked && notRemembered && document.activeElement === button;
  });

  await check("picking from RECENT or Choose another folder… remembers it", async () => {
    places = { recent: [{ path: "E:\\Notes", name: "Notes" }], known: [] };
    const { dest, el } = make();
    await flush();
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    calls.length = 0;
    el.querySelectorAll("[role='option']")[1].click();
    await flush();
    const recent = calls.some((c) => c[0] === "remember_output_folder" && c[1] === "E:\\Notes");
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    calls.length = 0;
    const options = el.querySelectorAll("[role='option']");
    options[options.length - 1].click();
    await flush();
    await flush();
    const other = calls.some((c) => c[0] === "remember_output_folder" && c[1] === "E:\\Picked");
    return recent && other && dest.value().folder === "E:\\Picked";
  });

  await check("a click outside closes the menu", async () => {
    const { dest, el } = make();
    await flush();
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    const opened = dest.isMenuOpen();
    const outside = fakeElement("div");
    body.appendChild(outside);
    dispatch(outside, "pointerdown", {});
    return opened && !dest.isMenuOpen();
  });

  await check("an unwritable folder blocks the run until another is chosen", async () => {
    places = { recent: [], known: [{ key: "documents", name: "Documents", path: "C:\\Docs" }] };
    unwritable.add("D:\\Reports");
    const { dest, el } = make();
    await flush();
    const error = el.querySelectorAll(".save-dest-error")[1];
    const blocked = !dest.isValid() && !error.hidden &&
      error.textContent === "Can't save in this folder. Choose another.";
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    el.querySelectorAll("[role='option']")[1].click();
    await flush();
    unwritable.clear();
    return blocked && dest.isValid() && error.hidden;
  });

  await check("a folder Lector can't write to takes no RECENT slot", async () => {
    unwritable.add("E:\\Picked");
    const { dest, el } = make();
    await flush();
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    calls.length = 0;
    const options = el.querySelectorAll("[role='option']");
    options[options.length - 1].click();
    await flush();
    await flush();
    unwritable.clear();
    return dest.value().folder === "E:\\Picked" && !dest.isValid() &&
      !calls.some((c) => c[0] === "remember_output_folder");
  });

  await check("the name follows the folder until the reader types one", async () => {
    places = { recent: [], known: [{ key: "documents", name: "Documents", path: "C:\\Docs" },
      { key: "downloads", name: "Downloads", path: "C:\\Down" }] };
    const { el } = make();
    await flush();
    const input = find(el, "save-dest-input");
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    el.querySelectorAll("[role='option']")[1].click();
    await flush();
    const followed = input.value === "free in Docs";
    typeName(el, "Mine");
    key(find(el, "save-dest-button"), "Enter");
    await flush();
    el.querySelectorAll("[role='option']")[2].click();
    await flush();
    return followed && input.value === "Mine";
  });

  await check("Python's answer at Start goes under the row it is about", async () => {
    const { dest, el } = make();
    await flush();
    dest.showError("name", "That's the original file. Choose another name.");
    const [nameError] = el.querySelectorAll(".save-dest-error");
    const shown = !dest.isValid() && nameError.textContent === "That's the original file. Choose another name.";
    typeName(el, "Another");
    return shown && dest.isValid();
  });

  await check("the result line and the toast say where the output went", async () => {
    const clauses = [savePlaceClause("beside the original"), savePlaceClause("in Documents"),
      savePlaceClause("on the Desktop"), savePlaceClause(undefined)];
    const done = (tool, result) => fileJobOutcomeToast({ state: "done", tool, result }).message;
    const toasts = [
      done("compress", { after: 2 * 1024 * 1024, path: "x", where: "in Documents" }),
      done("searchable", { added: 3, path: "x", where: "on the Desktop" }),
      done("export", { count: 12, path: "x", where: "beside the original" }),
      done("convert", { name: "scan.pdf", pages: 4, path: "x", where: "beside the first file" }),
    ];
    return JSON.stringify(clauses) === JSON.stringify([", beside the original", " in Documents", " on the Desktop", ""]) &&
      JSON.stringify(toasts) === JSON.stringify([
        "Compressed copy saved in Documents · 2.0 MB",
        "Searchable copy saved on the Desktop · text added to 3 pages",
        "12 images exported beside the original",
        "Created scan.pdf beside the first file · 4 pages",
      ]);
  });

  console.log(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class SaveDestinationBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(
            ["node", "-e", HARNESS, *map(str, SCRIPTS)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.results = json.loads(out.stdout.strip().splitlines()[-1])

    def test_every_scenario(self):
        self.assertTrue(self.results)
        for name, outcome in self.results.items():
            with self.subTest(name):
                self.assertIs(outcome, True, outcome)


if __name__ == "__main__":
    unittest.main()
