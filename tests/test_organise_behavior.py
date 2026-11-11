"""Behaviour of Organise pages' view (frontend/pages/organise.js,
organise-grid.js) and "Applying page changes…" (page-change-busy.js).

Run under Node with a small stand-in DOM: the grid laid out 7 columns wide
(148px cells, 8px column gap, 12px row gap, as organise.css lays it out at
1280px), an IntersectionObserver the test fires by hand, and a `callApi`
standing in for Python's draft (Api.organise_*) whose thumbnail and Done
replies the test can hold back. What is pinned here:

* selecting with click, Ctrl+click, Shift+click and the background; the
  action bar's disabled states and the selection line;
* the keyboard: roving focus, Shift+arrows, Space, Ctrl+←/→, Ctrl+A,
  Delete, Ctrl+Z;
* dragging: an unselected page is selected first, the drop lands in the
  right gap (the insertion bar shows it), a block dropped on itself does
  nothing;
* thumbnails: lazy, three at a time, drawn once per page, none started once
  Done has begun;
* Done: no changes leaves at once with no busy card; changes put the card up
  before Python has answered, then reload the reader with the note; keys do
  nothing behind the card.

Skipped when Node is not installed.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "frontend" / "pages"
SCRIPTS = [PAGES / name for name in (
    "page-change-busy.js", "organise-grid.js", "danger-dialog.js", "organise-discard.js", "organise-insert.js",
    "organise-extract.js", "organise.js",
)]

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const COLS = 7, CELL_W = 148, CELL_H = 168, GAP_X = 8, GAP_Y = 12;
const docListeners = { capture: {}, bubble: {} };
const byId = {};
let activeElement = null;
let root = null;

function makeEl(tag, id) {
  const listeners = {};
  const classes = new Set();
  const el = {
    tagName: tag.toUpperCase(), id, parentNode: null, children: [], attrs: {}, dataset: {},
    textContent: "", hidden: false, tabIndex: 0, src: "", title: "",
    style: { setProperty(k, v) { this[k] = v; }, removeProperty(k) { delete this[k]; } },
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: (c) => classes.has(c),
    },
    set className(v) { classes.clear(); v.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(" "); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    listeners,
    appendChild(kid) {
      if (kid.isFragment) { const kids = kid.children.slice(); kid.children = []; kids.forEach((k) => this.appendChild(k)); return kid; }
      if (kid.parentNode) kid.parentNode.children.splice(kid.parentNode.children.indexOf(kid), 1);
      kid.parentNode = this;
      this.children.push(kid);
      return kid;
    },
    append(...kids) { kids.forEach((k) => this.appendChild(k)); },
    replaceChildren(...kids) { this.children.forEach((k) => { k.parentNode = null; }); this.children = []; kids.forEach((k) => this.appendChild(k)); },
    get lastElementChild() { return this.children[this.children.length - 1] || null; },
    get isConnected() { for (let n = this; n; n = n.parentNode) if (n === root) return true; return false; },
    contains(other) { for (let n = other; n; n = n.parentNode) if (n === this) return true; return false; },
    focus() { activeElement = this; },
    scrollIntoView() {},
    get offsetWidth() { return 3; },
    get offsetTop() { return this.layout().top; },
    getBoundingClientRect() {
      const l = this.layout();
      return { left: l.left, top: l.top, right: l.left + l.width, bottom: l.top + l.height, width: l.width, height: l.height };
    },
    layout() {
      if (this.parentNode && this.parentNode.id === "organiseGrid") {
        const i = this.parentNode.children.indexOf(this);
        const col = i % COLS, row = Math.floor(i / COLS);
        return { left: col * (CELL_W + GAP_X), top: row * (CELL_H + GAP_Y), width: CELL_W, height: CELL_H };
      }
      if (this.id === "organiseScroll") return { left: 0, top: 0, width: 1100, height: 600 };
      return { left: 0, top: 0, width: 1100, height: 2000 };
    },
    querySelector(sel) {
      const cls = sel.startsWith(".") ? sel.slice(1) : null;
      const walk = (n) => { for (const k of n.children) { if (cls && k.classList.contains(cls)) return k; const f = walk(k); if (f) return f; } return null; };
      return walk(this);
    },
    querySelectorAll(sel) {
      const out = [];
      const attr = { "[data-needs-selection]": "needs" }[sel];
      const walk = (n) => n.children.forEach((k) => { if (attr && attr in k.dataset) out.push(k); walk(k); });
      walk(this);
      return out;
    },
  };
  if (id) byId[id] = el;
  return el;
}

root = makeEl("div", "root");
function add(parent, tag, id, dataset = {}) { const el = makeEl(tag, id); Object.assign(el.dataset, dataset); parent.appendChild(el); return el; }
const readingView = add(root, "div", "readingView");
const view = add(readingView, "section", "organiseView");
view.hidden = true;
["organiseDocName", "organiseSelectionText"].forEach((id) => add(view, "span", id));
["organiseUndoBtn", "organiseRedoBtn", "organiseCancelBtn", "organiseDoneBtn", "organiseSelectAllBtn"].forEach((id) => add(view, "button", id));
add(view, "button", "organiseInsertBtn");
["organiseRotateLeftBtn", "organiseRotateRightBtn"].forEach((id) => add(view, "button", id, { needs: "" }));
add(view, "button", "organiseDeleteBtn", { needs: "" });
add(view, "button", "organiseExtractBtn", { needs: "" });
const scroller = add(view, "div", "organiseScroll");
const grid = add(scroller, "div", "organiseGrid");
add(scroller, "div", "organiseInsertion").hidden = true;
add(root, "span", "organiseCount").hidden = true;
add(root, "div", "pageChangeBusy").hidden = true;

const htmlClasses = new Set();
const document = {
  getElementById: (id) => {
    if (byId[id]) return byId[id];
    const walk = (n) => { for (const k of n.children) { if (k.id === id) return k; const f = walk(k); if (f) return f; } return null; };
    return walk(root);
  },
  createElement: (tag) => makeEl(tag),
  createTextNode: (text) => { const n = makeEl("#text"); n.textContent = text; return n; },
  createDocumentFragment: () => { const f = makeEl("fragment"); f.isFragment = true; return f; },
  querySelector: (sel) => (sel === ".reading-view" ? readingView : null),
  documentElement: { classList: { add: (c) => htmlClasses.add(c), remove: (c) => htmlClasses.delete(c), contains: (c) => htmlClasses.has(c) } },
  body: add(root, "div", "body"),
  addEventListener(type, fn, capture) { const bag = capture ? docListeners.capture : docListeners.bubble; (bag[type] = bag[type] || []).push(fn); },
};

function dispatch(target, type, props = {}) {
  let stopped = false, immediate = false;
  const ev = {
    type, target, key: "", ctrlKey: false, shiftKey: false, metaKey: false, altKey: false, repeat: false,
    clientX: 0, clientY: 0, relatedTarget: null, defaultPrevented: false,
    dataTransfer: { setData() {}, effectAllowed: "", dropEffect: "" },
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { stopped = true; },
    stopImmediatePropagation() { stopped = true; immediate = true; },
    ...props,
  };
  for (const fn of docListeners.capture[type] || []) { fn(ev); if (immediate) return ev; }
  for (let n = target; n && !stopped; n = n.parentNode) {
    for (const fn of n.listeners[type] || []) { fn(ev); if (immediate) return ev; }
  }
  if (!stopped) for (const fn of docListeners.bubble[type] || []) { fn(ev); if (immediate) return ev; }
  return ev;
}

const observed = [];
class FakeObserver { constructor(cb) { this.cb = cb; } observe(el) { observed.push({ el, obs: this }); } unobserve() {} }
let gridObserver = null;
function intersectAll() {
  const batch = observed.splice(0);
  gridObserver = gridObserver || (batch.find((b) => b.obs) || {}).obs;
  if (batch.length && gridObserver) gridObserver.cb(batch.map(({ el }) => ({ target: el, isIntersecting: true })));
}

// Python's draft, as far as this view can tell.
let pagesNow = Array.from({ length: 12 }, (_, i) => ({ key: `doc:${i}`, source: "doc", index: i, turn: 0, new: false }));
let steps = 0;
const calls = [];
const held = { thumbnails: [], done: null };
let nextSource = 0;
let jobCount = 0;
let holdThumbnails = false, holdDone = false, actionReply = null, doneReply = null;
function draft(extra = {}) {
  return { ok: true, pages: pagesNow.map((p) => ({ ...p })), can_undo: steps > 0, can_redo: false, steps, ...extra };
}
function moveTo(positions, gap) {
  const block = positions.map((i) => pagesNow[i]);
  const rest = pagesNow.filter((_, i) => !positions.includes(i));
  const at = gap - positions.filter((i) => i < gap).length;
  pagesNow = [...rest.slice(0, at), ...block, ...rest.slice(at)];
  return block.map((_, k) => at + k);
}
function callApi(method, ...args) {
  calls.push([method, ...args]);
  if (method === "organise_begin") return Promise.resolve(draft({ page_index: 2 }));
  if (method === "organise_thumbnail") {
    const reply = { image: "AAAA", width: 85, height: 120 };
    if (!holdThumbnails) return Promise.resolve(reply);
    return new Promise((resolve) => held.thumbnails.push(() => resolve(reply)));
  }
  if (method === "organise_action") {
    if (actionReply) return Promise.resolve(actionReply);
    steps += 1;
    const [kind, a] = args;
    if (kind === "insert") {
      const count = a.source === "insert-0" ? 3 : 1;
      const added = Array.from({ length: count }, (_, i) => ({ key: `${a.source}:${i}`, source: a.source, index: i, turn: 0, new: true }));
      pagesNow = [...pagesNow.slice(0, a.gap), ...added, ...pagesNow.slice(a.gap)];
      return Promise.resolve(draft({ changed: true, selection: added.map((_, i) => a.gap + i) }));
    }
    if (kind === "move") return Promise.resolve(draft({ changed: true, selection: moveTo(a.positions, a.gap) }));
    if (kind === "shift") {
      const lo = a.positions[0], hi = a.positions[a.positions.length - 1];
      const gap = a.step < 0 ? Math.max(lo - 1, 0) : Math.min(hi + 2, pagesNow.length);
      return Promise.resolve(draft({ changed: true, selection: moveTo(a.positions, gap) }));
    }
    return Promise.resolve(draft({ changed: true, selection: a.positions }));
  }
  if (method === "organise_add_source") {
    const [path, password] = args;
    const name = path.split("/").pop();
    if (name === "junk.pdf") return Promise.resolve({ ok: false, reason: "unreadable", error: "Couldn't read junk.pdf. It may be damaged.", name });
    if (name === "locked.pdf" && password !== "secret") {
      return Promise.resolve({ ok: false, reason: password ? "wrong_password" : "password", error: "The password isn't right. Try again.", name });
    }
    const source = `insert-${nextSource++}`;
    return Promise.resolve({ ok: true, source, pages: name === "locked.pdf" ? 1 : 3, name });
  }
  if (method === "organise_extract_setup") {
    const [positions] = args;
    const run = positions.length === 1 ? `page ${positions[0] + 1}` : `pages ${positions[0] + 1}–${positions[positions.length - 1] + 1}`;
    return Promise.resolve({
      ok: true, count: positions.length, label: run, every_page: positions.length === pagesNow.length,
      output: { folder: "C:/Docs", stem: `Annual Report (${run})`, extension: ".pdf" },
    });
  }
  if (method === "check_tool_destination") return Promise.resolve({ ok: true, exists: false, name: `${args[3]}.pdf`, folder: args[2] });
  if (method === "start_file_job") return Promise.resolve({ ok: true, id: `job-${++jobCount}` });
  if (method === "organise_undo") { steps = Math.max(0, steps - 1); return Promise.resolve(draft()); }
  if (method === "organise_cancel") { steps = 0; return Promise.resolve({ is_open: true, pages_changed: false }); }
  if (method === "organise_done") {
    if (doneReply === "reject") return Promise.reject(new Error("bridge"));
    const reply = doneReply || { is_open: true, pages_changed: steps > 0, history_trimmed: false };
    if (!holdDone) return Promise.resolve(reply);
    return new Promise((resolve) => { held.done = () => resolve(reply); });
  }
  return Promise.resolve({ ok: true });
}

// The real danger dialogs (danger-dialog.js, organise-discard.js); these
// read and answer them as the reader would.
function question(id) {
  const scrim = document.getElementById(id);
  const card = scrim.children[0];
  const [body, footer] = card.children;
  const [, extra, info] = body.children;
  const [, safe, danger] = footer.children;
  return {
    open: !scrim.hidden,
    title: document.getElementById(`${id}Title`).textContent,
    desc: document.getElementById(`${id}Desc`).textContent,
    info: info.children[1].textContent,
    safe: safe.textContent, danger: danger.textContent,
    focused: activeElement === safe ? "safe" : activeElement === danger ? "danger" : null,
    strip: extra.children[0] ? extra.children[0].children.map((k) => (k.children.length ? k.children[1].textContent : k.textContent)) : [],
    answer: (yes) => dispatch(yes ? danger : safe, "click"),
  };
}
const discard = { get open() { return question("organiseDiscard").open; } };

// Insert…: the Open dialog answers with the next of `picks`; the password
// box tries "secret".
const picks = [], browsed = [], passwordAsked = [];
const openFileDialog = { open: (opts) => { browsed.push(opts); return Promise.resolve(picks.shift() || { cancelled: true }); } };
const PasswordDialog = {
  ask: async ({ name, attempt }) => { passwordAsked.push(name); const tried = await attempt("secret"); return tried.done || null; },
};
// Extract…: FileJobs and the destination block as the dialog sees them; the
// test plays the job's events.
const jobListeners = [];
let watchedJob = null;
const FileJobs = { subscribe: (fn) => jobListeners.push(fn), watch: (id) => { watchedJob = id; }, current: () => null };
const emitJob = (event) => jobListeners.forEach((fn) => fn(event));
function createSaveDestination(opts) {
  return {
    element: makeEl("div"), isValid: () => true, value: () => ({ folder: opts.originFolder, name: opts.stem }),
    showError() {}, isMenuOpen: () => false, closeMenu() {}, setHint() {},
  };
}
function extractDialog() {
  const scrim = document.getElementById("organiseExtract");
  const [body, footer] = scrim.children[0].children;
  const content = body.children[1];
  const row = content.children.find((k) => k.classList.contains("checkbox-row"));
  const error = content.children.find((k) => k.classList.contains("organise-extract-error"));
  const progress = content.children.find((k) => k.classList.contains("job-progress"));
  return {
    open: !scrim.hidden,
    title: document.getElementById("organiseExtractTitle").textContent,
    desc: document.getElementById("organiseExtractDesc").textContent,
    check: row ? row.children.map((k) => k.textContent).join("") : null,
    box: row ? row.children[0] : null,
    error: error ? error.textContent : null,
    phase: progress ? progress.children[0].children[0].textContent : null,
    buttons: footer.children.map((b) => b.textContent),
    focused: footer.children.indexOf(activeElement),
    press: (label) => dispatch(footer.children.find((b) => b.textContent === label), "click"),
  };
}
function insertDialog() {
  const scrim = document.getElementById("organiseInsert");
  const [body, footer] = scrim.children[0].children;
  const [, source, places] = body.children;
  const [, label, choose] = source.children;
  const [cancel, insert] = footer.children;
  return {
    open: !scrim.hidden,
    title: document.getElementById("organiseInsertTitle").textContent,
    desc: document.getElementById("organiseInsertDesc").textContent,
    file: label.children.map((k) => k.textContent).join(" "),
    places: places.children.map((row) => [row.children[1].children[0].textContent, row.children[2].textContent, row.classList.contains("selected")]),
    primary: insert.textContent,
    focused: activeElement === insert ? "insert" : activeElement === choose ? "choose" : null,
    pick: (i) => dispatch(places.children[i], "click"),
    choose: () => dispatch(choose, "click"),
    answer: (yes) => dispatch(yes ? insert : cancel, "click"),
  };
}

const toasts = [];
const storage = {};
const deleted = [];
let reloads = 0;
const context = vm.createContext({
  document, console, setTimeout, clearTimeout,
  window: { location: { reload: () => { reloads += 1; } }, addEventListener() {} },
  sessionStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: (k) => { delete storage[k]; } },
  IntersectionObserver: FakeObserver,
  requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame: clearTimeout,
  getComputedStyle: () => ({ columnGap: "8px" }),
  devicePixelRatio: 1,
  callApi, mountIcon: () => {}, mountIcons: () => {},
  createVoiceScope: () => ({ enter() {}, leave() { return Promise.resolve(); } }), trapFocus: () => () => {},
  initRadioCards: () => {}, ensureOpenFileDialog: () => openFileDialog, PasswordDialog,
  FileJobs, createSaveDestination, ReplaceDialog: { open: async () => true },
  plural: (n, one, many) => `${n} ${n === 1 ? one : many}`,
  savePlaceClause: (where) => (where.startsWith("beside") ? `, ${where}` : ` ${where}`),
  fileJobFraction: (e) => (e && e.total ? e.current / e.total : null),
  fileJobPhaseText: (e) => (e && e.phase ? e.phase : "Starting…"),
  showFileJobOutput: () => {}, isTypingTarget: (t) => Boolean(t) && t.tagName === "INPUT" && t.type === "text",
  showToast: (text, opts = {}) => {
    toasts.push({ text, action: opts.actionLabel || null, actions: (opts.actions || []).map((a) => a.label) });
    return () => {};
  },
  __deleted: deleted,
});
for (const file of process.argv.slice(2)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
vm.runInContext(
  "configureOrganise({ title: () => 'Annual Report' });" +
  "this.__api = { openOrganise, isOrganiseOpen, finishOrganise, cancelOrganise };",
  context
);
const api = context.__api;
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }
const cell = (i) => grid.children[i];
const selectedPositions = () => grid.children.map((c, i) => (c.attrs["aria-selected"] === "true" ? i : -1)).filter((i) => i >= 0);
const key = (target, k, mods = {}) => dispatch(target, "keydown", { key: k, ...mods });
const actions = () => calls.filter((c) => c[0] === "organise_action").map((c) => [c[1], c[2]]);
const spot = (i) => { const r = cell(i).getBoundingClientRect(); return { left: r.left + 20, right: r.right - 20, y: r.top + 60 }; };

const scenarios = {
  async selection() {
    await api.openOrganise(); await settle();
    const out = {
      title: byId.organiseDocName.textContent, viewShown: !view.hidden,
      organising: readingView.classList.contains("is-organising"), count: byId.organiseCount.textContent,
    };
    out.idle = {
      text: byId.organiseSelectionText.textContent,
      rotate: [byId.organiseRotateLeftBtn.attrs["aria-disabled"], byId.organiseRotateLeftBtn.title],
      insert: byId.organiseInsertBtn.attrs["aria-disabled"] || null,
    };
    dispatch(cell(3), "click"); out.click = selectedPositions();
    dispatch(cell(5), "click", { ctrlKey: true }); out.ctrlClick = selectedPositions();
    dispatch(cell(7), "click", { shiftKey: true }); out.shiftClick = selectedPositions();
    out.text = byId.organiseSelectionText.textContent;
    out.rotateNow = byId.organiseRotateLeftBtn.attrs["aria-disabled"];
    dispatch(cell(5), "click", { ctrlKey: true }); out.ctrlOff = selectedPositions();
    dispatch(grid, "click"); out.background = selectedPositions();
    out.after = byId.organiseSelectionText.textContent;
    dispatch(byId.organiseSelectAllBtn, "click");
    out.all = selectedPositions().length; out.label = byId.organiseSelectAllBtn.textContent;
    dispatch(byId.organiseSelectAllBtn, "click"); out.none = selectedPositions().length;
    out.cell = [cell(0).attrs["aria-label"], cell(0).attrs.role, cell(3).attrs["aria-label"]];
    return out;
  },
  async keyboard() {
    await api.openOrganise(); await settle();
    const out = { focus: grid.children.indexOf(activeElement) };
    key(activeElement, "ArrowRight"); out.right = grid.children.indexOf(activeElement);
    key(activeElement, "ArrowRight", { shiftKey: true });
    key(activeElement, "ArrowRight", { shiftKey: true });
    out.shift = selectedPositions();
    key(activeElement, " "); out.space = selectedPositions();
    key(activeElement, "ArrowDown"); out.down = grid.children.indexOf(activeElement);
    key(activeElement, "ArrowUp"); out.up = grid.children.indexOf(activeElement);
    key(activeElement, "Home"); out.home = grid.children.indexOf(activeElement);
    key(activeElement, "End"); out.end = grid.children.indexOf(activeElement);
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    key(activeElement, "ArrowRight", { ctrlKey: true }); await settle();
    out.ctrlArrow = actions()[0]; out.moved = selectedPositions();
    const last = toasts[toasts.length - 1];
    out.toast = last && [last.text, last.action];
    key(activeElement, "Delete"); out.deleteAsked = question("organiseDelete").title;
    question("organiseDelete").answer(false); await settle();
    key(activeElement, "a", { ctrlKey: true }); out.ctrlA = selectedPositions().length;
    key(activeElement, "z", { ctrlKey: true }); await settle();
    out.undo = calls.some((c) => c[0] === "organise_undo");
    out.count = byId.organiseCount.textContent;
    return out;
  },
  async drag() {
    await api.openOrganise(); await settle();
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    dispatch(cell(8), "dragstart");
    const out = { draggingSelected: selectedPositions(), faded: cell(8).classList.contains("is-dragging") };
    const p = spot(1);
    dispatch(grid, "dragover", { clientX: p.left, clientY: p.y });
    const bar = byId.organiseInsertion;
    out.bar = { shown: !bar.hidden, left: bar.style.left };
    dispatch(grid, "drop", { clientX: p.left, clientY: p.y }); await settle();
    out.move = actions()[0];
    out.order = grid.children.slice(0, 3).map((c) => c.dataset.key);
    out.barAfterDrop = bar.hidden;
    out.fadedAfterDrop = grid.children.some((c) => c.classList.contains("is-dragging"));
    // A block dropped back on itself: nothing to do.
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    dispatch(cell(3), "dragstart");
    const q = spot(4);
    dispatch(grid, "dragover", { clientX: q.right, clientY: q.y });
    dispatch(grid, "drop", { clientX: q.right, clientY: q.y }); await settle();
    out.selfDrop = actions().length;
    // Past the right half of a row's last page: after it.
    dispatch(cell(0), "dragstart");
    const r = spot(6);
    dispatch(grid, "dragover", { clientX: r.right, clientY: r.y });
    out.rowEndBar = bar.style.left;
    dispatch(grid, "drop", { clientX: r.right, clientY: r.y }); await settle();
    out.rowEnd = actions()[1];
    return out;
  },
  async thumbnails() {
    holdThumbnails = true;
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const thumbCalls = () => calls.filter((c) => c[0] === "organise_thumbnail");
    const out = { first: thumbCalls().length, args: thumbCalls()[0] };
    held.thumbnails.shift()(); await settle();
    out.afterOne = thumbCalls().length;
    const paper = cell(0).children[0].children[0];
    out.painted = [paper.children[0].src.startsWith("data:image/png"), paper.style.width, paper.children[0].hidden];
    while (held.thumbnails.length) { held.thumbnails.shift()(); await settle(); }
    out.total = thumbCalls().length;
    dispatch(cell(11), "click");
    dispatch(cell(11), "dragstart");
    const p = spot(0);
    dispatch(grid, "drop", { clientX: p.left, clientY: p.y }); await settle();
    intersectAll(); await settle();
    out.afterMove = thumbCalls().length;
    out.movedPainted = cell(0).children[0].children[0].children[0].src.startsWith("data:image/png");
    return out;
  },
  async doneWithoutChanges() {
    await api.openOrganise(); await settle();
    await api.finishOrganise(); await settle();
    return {
      done: calls.filter((c) => c[0] === "organise_done").length, busyShown: !byId.pageChangeBusy.hidden,
      open: api.isOrganiseOpen(), viewHidden: view.hidden,
      organising: readingView.classList.contains("is-organising"), reloads,
    };
  },
  async doneWithChanges() {
    holdDone = true; holdThumbnails = true;
    await api.openOrganise(); await settle();
    dispatch(cell(2), "click");
    dispatch(byId.organiseRotateRightBtn, "click"); await settle();
    const out = { rotate: actions()[0], count: byId.organiseCount.textContent };
    intersectAll(); await settle();
    const thumbsBefore = calls.filter((c) => c[0] === "organise_thumbnail").length;
    dispatch(byId.organiseDoneBtn, "click");
    out.busyAtOnce = !byId.pageChangeBusy.hidden;
    out.ariaBusy = document.body.attrs["aria-busy"];
    out.doneAsked = calls.filter((c) => c[0] === "organise_done").length;
    while (held.thumbnails.length) { held.thumbnails.shift()(); await settle(); }
    out.thumbsStartedAfterDone = calls.filter((c) => c[0] === "organise_thumbnail").length - thumbsBefore;
    const before = calls.length;
    const ev = key(cell(0), "z", { ctrlKey: true }); await settle();
    out.keyBlocked = [ev.defaultPrevented, calls.length - before];
    held.done(); await settle();
    out.reloads = reloads;
    out.note = JSON.parse(storage["lector-page-change"] || "null");
    out.busyStill = !byId.pageChangeBusy.hidden;
    return out;
  },
  async cancelWithout() {
    await api.openOrganise(); await settle();
    const viaKey = { esc: key(cell(0), "Escape").defaultPrevented };
    await settle();
    viaKey.open = api.isOrganiseOpen();
    await api.openOrganise(); await settle();
    dispatch(byId.organiseCancelBtn, "click"); await settle();
    return {
      viaKey, viaButtonOpen: api.isOrganiseOpen(), asked: Number(discard.open),
      cancels: calls.filter((c) => c[0] === "organise_cancel").length,
      done: calls.filter((c) => c[0] === "organise_done").length,
      busy: !byId.pageChangeBusy.hidden, reloads,
      contexts: calls.filter((c) => c[0] === "set_voice_context").map((c) => c[1]),
    };
  },
  async cancelWith() {
    await api.openOrganise(); await settle();
    dispatch(cell(2), "click");
    dispatch(byId.organiseRotateRightBtn, "click"); await settle();
    dispatch(cell(5), "click");
    dispatch(byId.organiseRotateLeftBtn, "click"); await settle();
    const out = {};
    key(cell(0), "Escape"); await settle();
    const asked = question("organiseDiscard");
    out.escAsked = { open: asked.open, title: asked.title, desc: asked.desc, info: asked.info, safe: asked.safe, danger: asked.danger, focused: asked.focused };
    out.openWhileAsking = api.isOrganiseOpen();
    question("organiseDiscard").answer(false); await settle();  // Keep editing
    out.afterKeep = { open: api.isOrganiseOpen(), steps: byId.organiseCount.textContent, cancels: calls.filter((c) => c[0] === "organise_cancel").length, focus: grid.children.indexOf(activeElement) };
    dispatch(byId.organiseCancelBtn, "click"); await settle();
    out.buttonAsked = discard.open;
    // A second Cancel while it is asking does nothing.
    dispatch(byId.organiseCancelBtn, "click"); await settle();
    out.askedOnce = discard.open;
    // Esc in the question is Keep editing, and doesn't reach Organise.
    key(cell(0), "Escape"); await settle();
    out.escInQuestion = { open: discard.open, organise: api.isOrganiseOpen() };
    dispatch(byId.organiseCancelBtn, "click"); await settle();
    question("organiseDiscard").answer(true); await settle();  // Discard changes
    out.afterDiscard = {
      open: api.isOrganiseOpen(), cancels: calls.filter((c) => c[0] === "organise_cancel").length,
      done: calls.filter((c) => c[0] === "organise_done").length, busy: !byId.pageChangeBusy.hidden, reloads,
      context: calls.filter((c) => c[0] === "set_voice_context").map((c) => c[1]).pop(),
      viewHidden: view.hidden,
    };
    return out;
  },
  async doneFails() {
    // One failure mode per run (FAIL_MODE), on a fresh page: no thumbnail is
    // drawn before Done, so any drawn after it shows the grid isn't frozen.
    await api.openOrganise(); await settle();
    dispatch(cell(2), "click");
    dispatch(byId.organiseRotateRightBtn, "click"); await settle();
    doneReply = process.env.FAIL_MODE === "reject" ? "reject" : { error: "Couldn't apply the page changes." };
    dispatch(byId.organiseDoneBtn, "click"); await settle();
    intersectAll(); await settle();
    return {
      open: api.isOrganiseOpen(), busy: !byId.pageChangeBusy.hidden, reloads,
      toast: toasts[toasts.length - 1] && toasts[toasts.length - 1].text,
      thumbsAfter: calls.filter((c) => c[0] === "organise_thumbnail").length,
    };
  },
  async deleteFlow() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const out = {};
    dispatch(cell(3), "click"); dispatch(cell(5), "click", { shiftKey: true });
    dispatch(byId.organiseDeleteBtn, "click"); await settle();
    const q = question("organiseDelete");
    out.asked = { open: q.open, title: q.title, desc: q.desc, info: q.info, safe: q.safe, danger: q.danger, focused: q.focused, strip: q.strip };
    q.answer(false); await settle();
    out.afterCancel = { open: question("organiseDelete").open, actions: actions().length, selected: selectedPositions(), focus: grid.children.indexOf(activeElement) };
    key(activeElement, "Delete"); await settle();
    out.keyAsks = question("organiseDelete").open;
    question("organiseDelete").answer(true); await settle();
    out.deleted = actions()[0];
    const last = toasts[toasts.length - 1];
    out.toast = [last.text, last.action];
    out.count = byId.organiseCount.textContent;
    // A scattered selection, one page, and more than six.
    dispatch(cell(0), "click"); dispatch(cell(2), "click", { ctrlKey: true }); dispatch(cell(7), "click", { ctrlKey: true });
    dispatch(byId.organiseDeleteBtn, "click"); await settle();
    out.scattered = [question("organiseDelete").title, question("organiseDelete").desc];
    question("organiseDelete").answer(false); await settle();
    dispatch(cell(1), "click");
    dispatch(byId.organiseDeleteBtn, "click"); await settle();
    out.single = [question("organiseDelete").title, question("organiseDelete").desc, question("organiseDelete").danger];
    question("organiseDelete").answer(false); await settle();
    dispatch(cell(0), "click"); dispatch(cell(7), "click", { shiftKey: true });
    dispatch(byId.organiseDeleteBtn, "click"); await settle();
    out.many = question("organiseDelete").strip;
    question("organiseDelete").answer(false); await settle();
    // Every page selected: refused before the question.
    const before = toasts.length;
    key(activeElement, "a", { ctrlKey: true });
    dispatch(byId.organiseDeleteBtn, "click"); await settle();
    out.everyPage = { asked: question("organiseDelete").open, toast: toasts.slice(before).map((t) => t.text) };
    return out;
  },
  async insertFlow() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const out = {};
    dispatch(cell(3), "click"); dispatch(cell(5), "click", { shiftKey: true });
    picks.push({ path: "C:/Files/Appendix.pdf" });
    dispatch(byId.organiseInsertBtn, "click"); await settle();
    const first = insertDialog();
    out.browsed = browsed[0];
    out.asked = { open: first.open, title: first.title, desc: first.desc, file: first.file, places: first.places, primary: first.primary, focused: first.focused };
    // Choose…: another file, a protected one, unlocked; the first is let go.
    picks.push({ path: "C:/Files/locked.pdf" });
    first.choose(); await settle();
    const second = insertDialog();
    out.chosenAgain = { open: second.open, file: second.file, primary: second.primary, focused: second.focused, password: passwordAsked.slice() };
    out.forgot = calls.filter((c) => c[0] === "organise_forget_source").map((c) => c[1]);
    second.pick(2); // At the end
    out.endPicked = insertDialog().places.map((p) => p[2]);
    insertDialog().answer(true); await settle();
    out.inserted = actions()[0];
    out.selected = selectedPositions();
    out.focus = grid.children.indexOf(activeElement);
    out.newPill = cell(12).classList.contains("is-new");
    const last = toasts[toasts.length - 1];
    out.toast = [last.text, last.action];
    out.count = byId.organiseCount.textContent;
    out.closed = insertDialog().open;
    // Nothing selected: only "At the end". Cancel lets the file go.
    dispatch(grid, "click");
    picks.push({ path: "C:/Files/Appendix.pdf" });
    dispatch(byId.organiseInsertBtn, "click"); await settle();
    out.idlePlaces = insertDialog().places;
    key(activeElement, "Escape"); await settle();
    out.escClosed = { open: insertDialog().open, organise: api.isOrganiseOpen(), actions: actions().length };
    out.forgotAfterCancel = calls.filter((c) => c[0] === "organise_forget_source").map((c) => c[1]);
    // A file that can't be used: the toast says why, no dialog.
    picks.push({ path: "C:/Files/junk.pdf" });
    dispatch(byId.organiseInsertBtn, "click"); await settle();
    out.junk = { open: insertDialog().open, toast: toasts[toasts.length - 1].text };
    // The Open dialog cancelled: nothing at all.
    const before = calls.length;
    dispatch(byId.organiseInsertBtn, "click"); await settle();
    out.browseCancelled = calls.slice(before).map((c) => c[0]);
    return out;
  },
  async extractFlow() {
    await api.openOrganise(); await settle();
    const out = {};
    dispatch(cell(3), "click"); dispatch(cell(5), "click", { shiftKey: true });
    dispatch(byId.organiseExtractBtn, "click"); await settle();
    const asked = extractDialog();
    out.asked = { open: asked.open, title: asked.title, desc: asked.desc, check: asked.check, buttons: asked.buttons, focused: asked.focused, checked: asked.box.checked };
    // Also delete, then Extract: the job starts and the dialog shows it.
    asked.box.checked = true; dispatch(asked.box, "change");
    asked.press("Extract"); await settle();
    out.started = calls.filter((c) => c[0] === "start_file_job").map((c) => c.slice(1));
    out.watched = watchedJob;
    key(activeElement, "Escape"); await settle();
    emitJob({ id: "job-1", state: "working", phase: "Taking the pages", current: 1, total: 4, unit: "step" });
    const working = extractDialog();
    out.working = { open: working.open, phase: working.phase, buttons: working.buttons, actions: actions().length };
    emitJob({ id: "job-1", state: "done", result: { path: "C:/Docs/Annual Report (pages 4–6).pdf", name: "Annual Report (pages 4–6).pdf", where: "beside the original", pages: 3 } });
    await settle();
    out.afterDone = { open: extractDialog().open, deleted: actions()[0], count: byId.organiseCount.textContent };
    const toast = toasts[toasts.length - 1];
    out.deleteToast = [toast.text, toast.actions];
    // One page, nothing deleted: Open and Show in folder.
    dispatch(cell(0), "click");
    dispatch(byId.organiseExtractBtn, "click"); await settle();
    out.single = { title: extractDialog().title, desc: extractDialog().desc, check: extractDialog().check };
    extractDialog().press("Extract"); await settle();
    emitJob({ id: "job-2", state: "done", result: { path: "C:/Docs/Annual Report (page 1).pdf", name: "Annual Report (page 1).pdf", where: "in Documents", pages: 1 } });
    await settle();
    const saved = toasts[toasts.length - 1];
    out.savedToast = [saved.text, saved.actions];
    out.actionsAfterSingle = actions().length;
    // A failure: the dialog stays, saying why; Esc then cancels it.
    dispatch(byId.organiseExtractBtn, "click"); await settle();
    extractDialog().press("Extract"); await settle();
    emitJob({ id: "job-3", state: "failed", error: "Couldn't extract the pages: the disk is full. The original is unchanged." });
    await settle();
    out.failed = { open: extractDialog().open, error: extractDialog().error, buttons: extractDialog().buttons };
    key(activeElement, "Escape"); await settle();
    out.escClosed = { open: extractDialog().open, organise: api.isOrganiseOpen() };
    // Every page selected: nothing to delete, so no box.
    key(cell(0), "a", { ctrlKey: true });
    dispatch(byId.organiseExtractBtn, "click"); await settle();
    out.everyPage = extractDialog().check;
    return out;
  },
  async refusals() {
    await api.openOrganise(); await settle();
    dispatch(byId.organiseRotateLeftBtn, "click"); await settle();
    const out = { disabledClick: actions().length };
    dispatch(cell(1), "click");
    out.extractNow = [byId.organiseExtractBtn.attrs["aria-disabled"], byId.organiseExtractBtn.title];
    key(activeElement, "Delete");
    dispatch(byId.organiseDeleteBtn, "click"); await settle();
    out.deleteNow = byId.organiseDeleteBtn.attrs["aria-disabled"];
    dispatch(cell(0), "click");
    actionReply = { ok: false, reason: "every_page" };
    dispatch(byId.organiseRotateLeftBtn, "click"); await settle();
    out.toast = toasts.map((t) => t.text);
    out.arg = actions()[0];
    out.stillSelected = selectedPositions();
    return out;
  },
};

scenarios[process.env.SCENARIO]().then(
  (out) => process.stdout.write(JSON.stringify(out)),
  (err) => { process.stderr.write(String(err && err.stack)); process.exit(1); },
);
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OrganiseViewBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # From a file: as `node -e` the harness is past Windows' command-line limit.
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.harness = Path(tmp.name) / "organise-harness.js"
        cls.harness.write_text(HARNESS, encoding="utf-8")

    def run_scenario(self, name: str, **env) -> dict:
        result = subprocess.run(
            ["node", str(self.harness), *map(str, SCRIPTS)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env={**os.environ, "SCENARIO": name, **env},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_selecting_with_the_mouse(self):
        out = self.run_scenario("selection")
        self.assertEqual(out["title"], "Annual Report")
        self.assertTrue(out["viewShown"])
        self.assertTrue(out["organising"])
        self.assertEqual(out["count"], "12 pages")
        # Insert works without a selection.
        self.assertEqual(out["idle"], {"text": "12 pages", "rotate": ["true", "Select pages first"], "insert": None})
        self.assertEqual(out["click"], [3])
        self.assertEqual(out["ctrlClick"], [3, 5])
        self.assertEqual(out["shiftClick"], [5, 6, 7])  # from the last click, Ctrl or not
        self.assertEqual(out["text"], "3 of 12 selected")
        self.assertEqual(out["rotateNow"], "false")
        self.assertEqual(out["ctrlOff"], [6, 7])
        self.assertEqual(out["background"], [])
        self.assertEqual(out["after"], "12 pages")
        self.assertEqual((out["all"], out["label"], out["none"]), (12, "Select none", 0))
        self.assertEqual(out["cell"], ["Page 1", "option", "Page 4"])

    def test_the_keyboard(self):
        out = self.run_scenario("keyboard")
        self.assertEqual(out["focus"], 2)  # the page being read
        self.assertEqual(out["right"], 3)
        self.assertEqual(out["shift"], [3, 4, 5])
        self.assertEqual(out["space"], [3, 4])
        self.assertEqual(out["down"], 11)  # a row down, kept on the last page
        self.assertEqual(out["up"], 4)
        self.assertEqual(out["home"], 0)
        self.assertEqual(out["end"], 11)
        self.assertEqual(out["ctrlArrow"], ["shift", {"positions": [3, 4], "step": 1}])
        self.assertEqual(out["moved"], [4, 5])
        self.assertEqual(out["toast"], ["Moved 2 pages", "Undo"])
        self.assertEqual(out["deleteAsked"], "Delete 2 pages?")  # the Delete key asks
        self.assertEqual(out["ctrlA"], 12)
        self.assertTrue(out["undo"])
        self.assertEqual(out["count"], "12 pages")  # the move undone

    def test_dragging(self):
        out = self.run_scenario("drag")
        self.assertEqual(out["draggingSelected"], [8])  # an unselected page is selected first
        self.assertTrue(out["faded"])
        self.assertTrue(out["bar"]["shown"])
        self.assertEqual(out["bar"]["left"], "150.5px")  # in the column gap left of page 2
        self.assertEqual(out["move"], ["move", {"positions": [8], "gap": 1}])
        self.assertEqual(out["order"], ["doc:0", "doc:8", "doc:1"])
        self.assertTrue(out["barAfterDrop"])
        self.assertFalse(out["fadedAfterDrop"])
        self.assertEqual(out["selfDrop"], 1)  # dropped on itself: no second call
        self.assertEqual(out["rowEndBar"], f"{6 * 156 + 148 + 4 - 1.5}px")
        self.assertEqual(out["rowEnd"], ["move", {"positions": [0], "gap": 7}])

    def test_thumbnails_are_lazy_three_at_a_time_and_drawn_once(self):
        out = self.run_scenario("thumbnails")
        self.assertEqual(out["first"], 3)
        self.assertEqual(out["args"], ["organise_thumbnail", "doc", 0, 120])
        self.assertEqual(out["afterOne"], 4)
        self.assertEqual(out["painted"], [True, "85px", False])
        self.assertEqual(out["total"], 12)
        self.assertEqual(out["afterMove"], 12)  # moving pages draws nothing again
        self.assertTrue(out["movedPainted"])

    def test_done_without_changes_just_leaves(self):
        out = self.run_scenario("doneWithoutChanges")
        self.assertEqual(out, {
            "done": 1, "busyShown": False, "open": False, "viewHidden": True, "organising": False, "reloads": 0,
        })

    def test_done_with_changes_shows_the_card_first_blocks_keys_and_reloads(self):
        out = self.run_scenario("doneWithChanges")
        self.assertEqual(out["rotate"], ["rotate", {"positions": [2], "degrees": 90}])
        self.assertEqual(out["count"], "12 pages · 1 unsaved change")
        self.assertTrue(out["busyAtOnce"])  # before Python has answered
        self.assertEqual(out["ariaBusy"], "true")
        self.assertEqual(out["doneAsked"], 1)
        self.assertEqual(out["thumbsStartedAfterDone"], 0)
        self.assertEqual(out["keyBlocked"], [True, 0])
        self.assertEqual(out["reloads"], 1)
        self.assertEqual(out["note"], {"unsaved": True, "trimmed": False})
        self.assertTrue(out["busyStill"])  # until the reloaded page has drawn

    def test_cancel_and_esc_without_changes_leave_at_once(self):
        out = self.run_scenario("cancelWithout")
        self.assertEqual(out["viaKey"], {"esc": True, "open": False})
        self.assertFalse(out["viaButtonOpen"])
        self.assertEqual(out["asked"], 0)  # nothing to lose, nothing asked
        self.assertEqual((out["cancels"], out["done"]), (2, 0))
        self.assertFalse(out["busy"])
        self.assertEqual(out["reloads"], 0)
        self.assertEqual(out["contexts"], ["organise", "reading", "organise", "reading"])

    def test_cancel_and_esc_with_changes_ask_and_keep_editing_or_discard(self):
        out = self.run_scenario("cancelWith")
        self.assertEqual(out["escAsked"], {
            "open": True,
            "title": "Discard page changes?",
            "desc": "Your 2 changes to the pages won’t be applied. The document stays as it was.",
            "info": "To keep them, choose Keep editing, then Done.",
            "safe": "Keep editing", "danger": "Discard changes",
            "focused": "safe",  # Enter by reflex keeps the work
        })
        self.assertTrue(out["openWhileAsking"])
        self.assertEqual(out["afterKeep"], {"open": True, "steps": "12 pages · 2 unsaved changes", "cancels": 0, "focus": 5})
        self.assertTrue(out["buttonAsked"])
        self.assertTrue(out["askedOnce"])  # a second Cancel while asking changes nothing
        self.assertEqual(out["escInQuestion"], {"open": False, "organise": True})
        self.assertEqual(out["afterDiscard"], {
            "open": False, "cancels": 1, "done": 0, "busy": False, "reloads": 0,
            "context": "reading", "viewHidden": True,
        })

    def test_delete_asks_first_and_is_one_undoable_step(self):
        out = self.run_scenario("deleteFlow")
        self.assertEqual(out["asked"], {
            "open": True,
            "title": "Delete 3 pages?",
            "desc": "Pages 4–6 are removed from Annual Report.",
            "info": "Undo brings them back. The PDF on disk doesn’t change until you save.",
            "safe": "Cancel", "danger": "Delete 3 pages",
            "focused": "safe",
            "strip": ["4", "5", "6"],
        })
        self.assertEqual(out["afterCancel"], {"open": False, "actions": 0, "selected": [3, 4, 5], "focus": 5})
        self.assertTrue(out["keyAsks"])  # the Delete key asks too
        self.assertEqual(out["deleted"], ["delete", {"positions": [3, 4, 5]}])
        self.assertEqual(out["toast"], ["Deleted 3 pages", "Undo"])
        self.assertEqual(out["count"], "12 pages · 1 unsaved change")
        self.assertEqual(out["scattered"], ["Delete 3 pages?", "3 pages are removed from Annual Report."])
        self.assertEqual(out["single"], ["Delete 1 page?", "Page 2 is removed from Annual Report.", "Delete 1 page"])
        self.assertEqual(out["many"], ["1", "2", "3", "4", "5", "6", "+2 more"])
        self.assertEqual(out["everyPage"], {
            "asked": False, "toast": ["A PDF needs at least one page. Leave one unselected."],
        })

    def test_insert_asks_where_then_puts_every_page_in_selected(self):
        out = self.run_scenario("insertFlow")
        self.assertEqual(out["browsed"], {"dir": None, "kind": "insert", "title": "Insert pages from"})
        self.assertEqual(out["asked"], {
            "open": True,
            "title": "Insert pages",
            "desc": "Adds pages from another PDF or from images.",
            "file": "Appendix.pdf · 3 pages",
            "places": [
                ["Before the selection", "before page 4", False],
                ["After the selection", "after page 6", True],  # the default with a selection
                ["At the end", "after page 12", False],
            ],
            "primary": "Insert 3 pages",
            "focused": "insert",
        })
        self.assertEqual(out["chosenAgain"], {
            "open": True, "file": "locked.pdf · 1 page", "primary": "Insert 1 page", "focused": "choose",
            "password": ["locked.pdf"],
        })
        self.assertEqual(out["forgot"], ["insert-0"])  # the file it replaced
        self.assertEqual(out["endPicked"], [False, False, True])
        self.assertEqual(out["inserted"], ["insert", {"source": "insert-1", "gap": 12}])
        self.assertEqual(out["selected"], [12])
        self.assertEqual(out["focus"], 12)
        self.assertTrue(out["newPill"])
        self.assertEqual(out["toast"], ["Inserted 1 page from locked.pdf", "Undo"])
        self.assertEqual(out["count"], "13 pages · 1 unsaved change")
        self.assertFalse(out["closed"])
        self.assertEqual(out["idlePlaces"], [["At the end", "after page 13", True]])  # the only place
        self.assertEqual(out["escClosed"], {"open": False, "organise": True, "actions": 1})
        self.assertEqual(out["forgotAfterCancel"], ["insert-0", "insert-2"])
        self.assertEqual(out["junk"], {"open": False, "toast": "Couldn't read junk.pdf. It may be damaged."})
        self.assertEqual(out["browseCancelled"], [])

    def test_extract_saves_then_deletes_as_one_step_only_when_asked(self):
        out = self.run_scenario("extractFlow")
        self.assertEqual(out["asked"], {
            "open": True, "title": "Extract 3 pages", "desc": "Saves pages 4–6 as a new PDF.",
            "check": "Also delete them from this PDF", "buttons": ["Cancel", "Extract"], "focused": 1,
            "checked": False,
        })
        self.assertEqual(out["started"], [["extract", "open", {
            "folder": "C:/Docs", "name": "Annual Report (pages 4–6)", "positions": [3, 4, 5],
        }]])
        self.assertEqual(out["watched"], "job-1")  # its end is the dialog's, not FileJobs' toast
        self.assertEqual(out["working"], {
            "open": True, "phase": "Taking the pages", "buttons": ["Cancel"], "actions": 0,  # Esc didn't close it
        })
        self.assertEqual(out["afterDone"], {
            "open": False, "deleted": ["delete", {"positions": [3, 4, 5]}], "count": "12 pages · 1 unsaved change",
        })
        self.assertEqual(out["deleteToast"], [
            "Saved Annual Report (pages 4–6).pdf, beside the original and removed them here", ["Undo", "Show in folder"],
        ])
        self.assertEqual(out["single"], {
            "title": "Extract 1 page", "desc": "Saves page 1 as a new PDF.", "check": "Also delete it from this PDF",
        })
        self.assertEqual(out["savedToast"], ["Saved Annual Report (page 1).pdf in Documents", ["Open", "Show in folder"]])
        self.assertEqual(out["actionsAfterSingle"], 1)  # nothing deleted
        self.assertEqual(out["failed"], {
            "open": True, "error": "Couldn't extract the pages: the disk is full. The original is unchanged.",
            "buttons": ["Cancel", "Extract"],
        })
        self.assertEqual(out["escClosed"], {"open": False, "organise": True})
        self.assertIsNone(out["everyPage"])

    def test_a_failed_done_keeps_the_draft_and_the_grid_working(self):
        toasts = {
            "error": "Couldn't apply the page changes.",
            "reject": "Couldn't apply the page changes. The PDF is as it was; your changes are still here.",
        }
        for mode, toast in toasts.items():
            with self.subTest(mode):
                out = self.run_scenario("doneFails", FAIL_MODE=mode)
                self.assertTrue(out["open"])
                self.assertFalse(out["busy"])
                self.assertEqual(out["reloads"], 0)
                self.assertEqual(out["toast"], toast)
                self.assertGreater(out["thumbsAfter"], 0)  # the grid is not left frozen

    def test_disabled_actions_do_nothing_and_refusals_say_why(self):
        out = self.run_scenario("refusals")
        self.assertEqual(out["disabledClick"], 0)
        self.assertEqual(out["extractNow"], ["false", ""])  # Extract arrived with F2.6
        self.assertEqual(out["deleteNow"], "false")  # Delete arrived with F2.4
        self.assertEqual(out["arg"], ["rotate", {"positions": [0], "degrees": -90}])
        self.assertEqual(out["toast"], ["A PDF needs at least one page. Leave one unselected."])
        self.assertEqual(out["stillSelected"], [0])


if __name__ == "__main__":
    unittest.main()
