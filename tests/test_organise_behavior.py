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
    "page-change-busy.js", "organise-timing.js", "organise-plan.js", "organise-thumbs.js", "organise-motion.js",
    "organise-drag.js", "organise-grid.js", "danger-dialog.js", "organise-discard.js", "organise-insert.js",
    "organise-extract.js", "organise.js",
)]

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const COLS = 7, CELL_W = 148, CELL_H = 168, GAP_X = 8, GAP_Y = 12;
const docListeners = { capture: {}, bubble: {} };
let domMoves = 0;
// Web Animations: each runs until the next macrotask (settle() lets it end),
// or until the test lets it go when `holdAnimations` is set.
const animations = [];
let holdAnimations = false;
let reducedMotion = process.env.REDUCED_MOTION === "1";
function startAnimation(el, keyframes, options) {
  let resolve;
  const anim = {
    el, keyframes, options, playState: "running",
    finished: new Promise((r) => { resolve = r; }),
    finish() { if (this.playState !== "running") return; this.playState = "finished"; resolve(this); },
    cancel() { this.finish(); },
  };
  animations.push(anim);
  if (!holdAnimations) setTimeout(() => anim.finish(), 0);
  return anim;
}
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
    removeEventListener(type, fn) { const bag = listeners[type] || []; if (bag.includes(fn)) bag.splice(bag.indexOf(fn), 1); },
    listeners,
    captured: null,
    setPointerCapture(id) { this.captured = id; },
    releasePointerCapture(id) { if (this.captured === id) this.captured = null; },
    hasPointerCapture(id) { return this.captured === id; },
    appendChild(kid) {
      if (kid.isFragment) { const kids = kid.children.slice(); kid.children = []; kids.forEach((k) => this.appendChild(k)); return kid; }
      if (kid.parentNode) kid.parentNode.children.splice(kid.parentNode.children.indexOf(kid), 1);
      kid.parentNode = this;
      this.children.push(kid);
      return kid;
    },
    append(...kids) { kids.forEach((k) => this.appendChild(k)); },
    insertBefore(kid, ref) {
      if (!ref) return this.appendChild(kid);
      if (kid.parentNode) kid.parentNode.children.splice(kid.parentNode.children.indexOf(kid), 1);
      kid.parentNode = this;
      this.children.splice(this.children.indexOf(ref), 0, kid);
      domMoves += 1;
      return kid;
    },
    remove() { if (this.parentNode) { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; } },
    get nextElementSibling() { const sibs = this.parentNode ? this.parentNode.children : []; return sibs[sibs.indexOf(this) + 1] || null; },
    get nextSibling() { return this.nextElementSibling; },
    animate(keyframes, options) { return startAnimation(this, keyframes, options); },
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
    // The grid scrolls in its 600px-high scroller: cells (and what is in
    // them, given the cell's box) move up as it does.
    layout() {
      const scrolled = (byId.organiseScroll && byId.organiseScroll.scrollTop) || 0;
      if (this.parentNode && this.parentNode.id === "organiseGrid") {
        const i = this.parentNode.children.indexOf(this);
        const col = i % COLS, row = Math.floor(i / COLS);
        return { left: col * (CELL_W + GAP_X), top: row * (CELL_H + GAP_Y) - scrolled, width: CELL_W, height: CELL_H };
      }
      for (let n = this.parentNode; n; n = n.parentNode) if (n.parentNode && n.parentNode.id === "organiseGrid") return n.layout();
      if (this.id === "organiseScroll") return { left: 0, top: 0, width: 1100, height: 600 };
      if (this.id === "organiseGrid") return { left: 0, top: -scrolled, width: 1100, height: 2000 };
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

// The window's own listeners (capture: first of all; bubble: last).
const winListeners = { capture: {}, bubble: {} };
const winBag = (capture) => (capture === true || (capture && capture.capture) ? winListeners.capture : winListeners.bubble);
const windowListenerCount = () => [winListeners.capture, winListeners.bubble]
  .reduce((n, bag) => n + Object.values(bag).reduce((m, fns) => m + fns.length, 0), 0);

function dispatch(target, type, props = {}) {
  let stopped = false, immediate = false;
  const ev = {
    type, target, key: "", ctrlKey: false, shiftKey: false, metaKey: false, altKey: false, repeat: false,
    clientX: 0, clientY: 0, relatedTarget: null, defaultPrevented: false, timeStamp: performance.now(),
    dataTransfer: { setData() {}, effectAllowed: "", dropEffect: "" },
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { stopped = true; },
    stopImmediatePropagation() { stopped = true; immediate = true; },
    ...props,
  };
  const run = (fns) => { for (const fn of [...(fns || [])]) { fn(ev); if (immediate) return true; } return false; };
  if (run(winListeners.capture[type]) || stopped) return ev;
  if (target === "window") { run(winListeners.bubble[type]); return ev; }
  if (run(docListeners.capture[type])) return ev;
  for (let n = target; n && !stopped; n = n.parentNode) {
    if (run(n.listeners[type])) return ev;
  }
  if (!stopped && run(docListeners.bubble[type])) return ev;
  if (!stopped) run(winListeners.bubble[type]);
  return ev;
}

// A mouse pointer event (pointer 1, the primary button held).
function pointer(target, type, x, y, props = {}) {
  return dispatch(target, type, { pointerId: 1, pointerType: "mouse", isPrimary: true, button: 0, buttons: 1, clientX: x, clientY: y, ...props });
}

const observed = [];
class FakeObserver { constructor(cb) { this.cb = cb; } observe(el) { observed.push({ el, obs: this }); } unobserve() {} }
let gridObserver = null;
function intersectAll() {
  const batch = observed.splice(0);
  gridObserver = gridObserver || (batch.find((b) => b.obs) || {}).obs;
  if (batch.length && gridObserver) gridObserver.cb(batch.map(({ el }) => ({ target: el, isIntersecting: true })));
}

// Python's draft, as far as this view can tell: a PlanHistory of its own
// (not organise-plan.js, so it checks the page's predictions).
let plans = [Array.from({ length: 12 }, (_, i) => ({ key: `doc:${i}`, source: "doc", index: i, turn: 0, new: false }))];
let at = 0;
const sourcePages = {};
const calls = [];
const held = { thumbnails: [], done: null, actions: [] };
let nextSource = 0;
let jobCount = 0;
let holdThumbnails = false, holdDone = false, holdActions = false, actionReply = null, doneReply = null;
// Python doing something other than the page predicted: the next action
// answers with the draft as it was plus a step of its own.
let divergeNext = false;
const pagesNow = () => plans[at];
const sameDraft = (a, b) => a.length === b.length && a.every((p, i) => p.key === b[i].key && p.turn === b[i].turn);
function draft(extra = {}) {
  return { ok: true, pages: pagesNow().map((p) => ({ ...p })), can_undo: at > 0, can_redo: at < plans.length - 1, steps: at, ...extra };
}
function push(next) {
  if (sameDraft(next, pagesNow())) return false;
  plans = [...plans.slice(0, at + 1), next];
  at += 1;
  return true;
}
function moveIn(pages, positions, gap) {
  const block = positions.map((i) => pages[i]);
  const rest = pages.filter((_, i) => !positions.includes(i));
  const to = gap - positions.filter((i) => i < gap).length;
  return { next: [...rest.slice(0, to), ...block, ...rest.slice(to)], selection: block.map((_, k) => to + k) };
}
function act(kind, a) {
  const pages = pagesNow();
  if (kind === "insert") {
    const count = sourcePages[a.source] || 1;
    const added = Array.from({ length: count }, (_, i) => ({ key: `${a.source}:${i}`, source: a.source, index: i, turn: 0, new: true }));
    return { next: [...pages.slice(0, a.gap), ...added, ...pages.slice(a.gap)], selection: added.map((_, i) => a.gap + i) };
  }
  if (kind === "move") return moveIn(pages, a.positions, a.gap);
  if (kind === "shift") {
    const lo = a.positions[0], hi = a.positions[a.positions.length - 1];
    return moveIn(pages, a.positions, a.step < 0 ? Math.max(lo - 1, 0) : Math.min(hi + 2, pages.length));
  }
  if (kind === "rotate") {
    return { next: pages.map((p, i) => (a.positions.includes(i) ? { ...p, turn: (p.turn + a.degrees + 360) % 360 } : p)), selection: a.positions };
  }
  if (kind === "delete") return { next: pages.filter((_, i) => !a.positions.includes(i)), selection: [] };
  return null;
}
function answerAction(kind, a) {
  if (actionReply) return { ...draft(), ...actionReply };
  if (divergeNext) {
    divergeNext = false;
    const { next } = act("rotate", { positions: [pagesNow().length - 1], degrees: 180 });
    push(next);
    return draft({ changed: true, selection: [] });
  }
  const { next, selection } = act(kind, a);
  const changed = push(next);
  return draft({ changed, selection });
}
function callApi(method, ...args) {
  calls.push([method, ...args]);
  if (method === "organise_begin") return Promise.resolve(draft({ page_index: 2 }));
  if (method === "organise_thumbnail") {
    const turn = args[3] || 0;
    const reply = { image: `AAAA${turn}`, width: turn % 180 ? 120 : 85, height: turn % 180 ? 85 : 120 };
    if (!holdThumbnails) return Promise.resolve(reply);
    return new Promise((resolve) => held.thumbnails.push(() => resolve(reply)));
  }
  if (method === "organise_action") {
    const [kind, a] = args;
    if (!holdActions) return Promise.resolve(answerAction(kind, a));
    return new Promise((resolve) => held.actions.push(() => resolve(answerAction(kind, a))));
  }
  if (method === "organise_add_source") {
    const [path, password] = args;
    const name = path.split("/").pop();
    if (name === "junk.pdf") return Promise.resolve({ ok: false, reason: "unreadable", error: "Couldn't read junk.pdf. It may be damaged.", name });
    if (name === "locked.pdf" && password !== "secret") {
      return Promise.resolve({ ok: false, reason: password ? "wrong_password" : "password", error: "The password isn't right. Try again.", name });
    }
    const source = `insert-${nextSource++}`;
    sourcePages[source] = name === "locked.pdf" ? 1 : 3;
    return Promise.resolve({ ok: true, source, pages: sourcePages[source], name });
  }
  if (method === "organise_extract_setup") {
    const [positions] = args;
    const run = positions.length === 1 ? `page ${positions[0] + 1}` : `pages ${positions[0] + 1}–${positions[positions.length - 1] + 1}`;
    return Promise.resolve({
      ok: true, count: positions.length, label: run, every_page: positions.length === pagesNow().length,
      output: { folder: "C:/Docs", stem: `Annual Report (${run})`, extension: ".pdf" },
    });
  }
  if (method === "check_tool_destination") return Promise.resolve({ ok: true, exists: false, name: `${args[3]}.pdf`, folder: args[2] });
  if (method === "start_file_job") return Promise.resolve({ ok: true, id: `job-${++jobCount}` });
  if (method === "organise_undo" || method === "organise_redo") {
    const answer = () => {
      const back = method === "organise_undo";
      if (back ? !at : at >= plans.length - 1) return draft({ ok: false, reason: "nothing" });
      at += back ? -1 : 1;
      return draft();
    };
    if (!holdActions) return Promise.resolve(answer());
    return new Promise((resolve) => held.actions.push(() => resolve(answer())));
  }
  if (method === "organise_cancel") { plans = [plans[0]]; at = 0; return Promise.resolve({ is_open: true, pages_changed: false }); }
  if (method === "organise_done") {
    if (doneReply === "reject") return Promise.reject(new Error("bridge"));
    const reply = doneReply || { is_open: true, pages_changed: at > 0, history_trimmed: false };
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
  document, console, setTimeout, clearTimeout, performance,
  matchMedia: (query) => ({ matches: query.includes("reduce") && reducedMotion }),
  window: {
    location: { reload: () => { reloads += 1; } },
    addEventListener(type, fn, capture) { const bag = winBag(capture); (bag[type] = bag[type] || []).push(fn); },
    removeEventListener(type, fn, capture) { const fns = winBag(capture)[type] || []; if (fns.includes(fn)) fns.splice(fns.indexOf(fn), 1); },
  },
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
  "this.__api = { openOrganise, isOrganiseOpen, finishOrganise, cancelOrganise, organiseAct };",
  context
);
const api = context.__api;
const organiseActNow = (kind, args) => api.organiseAct(kind, args);
// The draft the grid shows (not the pages fading out of it), and Python's:
// "doc:3", or "doc:3@90deg" when turned (from the cell's own label, since a
// turned page's own image is shown unrotated once it has arrived).
const liveKeys = () => grid.children.filter((c) => !c.classList.contains("is-leaving")).map((c) => {
  const turned = /rotated (\d+)°/.exec(c.attrs["aria-label"] || "");
  return turned ? `${c.dataset.key}@${turned[1]}deg` : c.dataset.key;
});
const pyKeys = () => pagesNow().map((p) => (p.turn ? `${p.key}@${p.turn}deg` : p.key));
// Microtasks only: what runs before the next frame.
const flush = async () => { for (let i = 0; i < 10; i++) await null; };
const failureToasts = () => toasts.filter((t) => t.text.startsWith("Couldn't")).map((t) => t.text);
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }
const cell = (i) => grid.children[i];
const selectedPositions = () => grid.children.map((c, i) => (c.attrs["aria-selected"] === "true" ? i : -1)).filter((i) => i >= 0);
const key = (target, k, mods = {}) => dispatch(target, "keydown", { key: k, ...mods });
const actions = () => calls.filter((c) => c[0] === "organise_action").map((c) => [c[1], c[2]]);
const spot = (i) => { const r = cell(i).getBoundingClientRect(); return { left: r.left + 20, right: r.right - 20, y: r.top + 60 }; };
// A mouse drag of the page in `from` to (x, y), as the browser sends it:
// pressed, moved past the 5px threshold, moved there, then released (with
// the click that follows a release). `end`: "up", or "none" to leave it held,
// or how it is called off: "esc", "cancel" (pointercancel), "blur".
function pdrag(from, x, y, { end = "up" } = {}) {
  const r = from.getBoundingClientRect();
  const sx = r.left + 70, sy = r.top + 60;
  pointer(from, "pointerdown", sx, sy);
  pointer(from, "pointermove", sx + 6, sy);
  pointer(grid, "pointermove", x, y);
  if (end === "up") release(x, y);
  else if (end === "esc") dispatch(activeElement || grid, "keydown", { key: "Escape" });
  else if (end === "cancel") pointer(grid, "pointercancel", x, y, { buttons: 0 });
  else if (end === "blur") dispatch("window", "blur");
}
function release(x, y) {
  pointer(grid, "pointerup", x, y, { buttons: 0 });
  dispatch(grid, "click", { clientX: x, clientY: y });
}
const lifted = () => document.body.children.find((k) => k.classList.contains("organise-lift")) || null;
const order = () => liveKeys().join(" ");

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
    intersectAll(); await settle();
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    const p = spot(1);
    const cell8 = cell(8).getBoundingClientRect();
    pdrag(cell(8), p.left, p.y, { end: "none" });
    // Placed at once where the drag began (6px on from the press); the
    // frame after moves it to the pointer.
    const before = lifted() && lifted().style.transform;
    await tick();
    const preview = lifted();
    const bar = byId.organiseInsertion;
    const out = {
      draggingSelected: selectedPositions(), faded: cell(8).classList.contains("is-dragging"),
      noTextSelection: grid.classList.contains("is-pointer-dragging"),
      captured: grid.captured,
      previewAtStart: before,
      pointerMoved: [p.left - (cell8.left + 76), p.y - (cell8.top + 60)],
      preview: preview && {
        transform: preview.style.transform,
        sameImage: preview.children[0].children[0].src === cell(8).children[0].children[0].children[0].src,
        badge: preview.children.length > 1,
      },
      bar: { shown: !bar.hidden, left: bar.style.left },
    };
    // Moves between frames: drawn once, at the last of them, moved exactly
    // as far as the pointer.
    for (const dx of [5, 10, 15, 20]) pointer(grid, "pointermove", p.left + dx, p.y + dx);
    out.betweenFrames = preview.style.transform === out.preview.transform;
    await tick();
    out.nextFrame = preview.style.transform;
    release(p.left + 20, p.y + 20); await settle();
    out.move = actions()[0];
    out.order = grid.children.slice(0, 3).map((c) => c.dataset.key);
    out.selectedAfter = selectedPositions(); // the click after the release cleared nothing
    out.barAfterDrop = bar.hidden;
    out.fadedAfterDrop = grid.children.some((c) => c.classList.contains("is-dragging"));
    out.previewAfterDrop = Boolean(lifted());
    out.textSelectionAfter = grid.classList.contains("is-pointer-dragging");
    out.capturedAfter = grid.captured;
    out.windowListeners = windowListenerCount();
    // A block dropped back on itself: nothing to do.
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    const q = spot(4);
    pdrag(cell(3), q.right, q.y); await settle();
    out.selfDrop = actions().length;
    // Two lifted: the badge says so.
    pdrag(cell(3), q.right, q.y, { end: "none" }); await tick();
    out.badge = lifted().children[1] ? lifted().children[1].textContent : null;
    release(q.right, q.y); await settle();
    // Past the right half of a row's last page: after it.
    dispatch(cell(0), "click");
    const r = spot(6);
    pdrag(cell(0), r.right, r.y, { end: "none" }); await tick();
    out.rowEndBar = bar.style.left;
    release(r.right, r.y); await settle();
    out.rowEnd = actions()[1];
    return out;
  },
  // A press that moves less than 5px is a click; the same moves dragged and
  // by Ctrl+arrow end the same; a drag called off changes nothing.
  async pointerDragging() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const thumbs = () => calls.filter((c) => c[0] === "organise_thumbnail").length;
    const drawn = thumbs();
    const out = {};
    const r = cell(2).getBoundingClientRect();
    const down = pointer(cell(2), "pointerdown", r.left + 70, r.top + 60);
    out.pressPrevented = down.defaultPrevented; // no native image drag or selection
    pointer(cell(2), "pointermove", r.left + 73, r.top + 62);
    await tick();
    pointer(cell(2), "pointerup", r.left + 73, r.top + 62, { buttons: 0 });
    dispatch(cell(2), "click"); await settle();
    out.click = {
      selected: selectedPositions(), dragged: grid.children.some((c) => c.classList.contains("is-dragging")),
      preview: Boolean(lifted()), actions: actions().length, listeners: windowListenerCount(),
    };
    const undoAll = async () => { while (at > 0) { key(activeElement, "z", { ctrlKey: true }); await settle(); } };
    const result = () => ({ order: order(), python: pyKeys().join(" "), selected: selectedPositions() });
    // One page dragged after the next one; then the same by Ctrl+→.
    dispatch(cell(0), "click");
    pdrag(cell(0), spot(1).right, spot(1).y); await settle();
    out.oneDragged = result();
    await undoAll();
    dispatch(cell(0), "click");
    key(activeElement, "ArrowRight", { ctrlKey: true }); await settle();
    out.oneKeyed = result();
    await undoAll();
    // Two pages (3 and 4) dragged after page 6, the second of them pressed.
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    pdrag(cell(4), spot(5).right, spot(5).y); await settle();
    out.twoDragged = result();
    await undoAll();
    dispatch(cell(3), "click"); dispatch(cell(4), "click", { shiftKey: true });
    key(activeElement, "ArrowRight", { ctrlKey: true }); await settle();
    out.twoKeyed = result();
    await undoAll();
    const sentBefore = actions().length;
    // Called off three ways: nothing changes, Organise stays open, the
    // preview goes back to its page.
    for (const how of ["esc", "cancel", "blur"]) {
      const was = order();
      const animated = animations.length;
      dispatch(cell(5), "click");
      pdrag(cell(5), spot(9).left, spot(9).y, { end: how });
      const back = animations.slice(animated).find((a) => a.el.classList.contains("organise-lift"));
      // The release after Esc or a lost focus is no drop (or click) either;
      // a pointercancel is the end of the pointer, with no release to come.
      if (how !== "cancel") release(spot(9).left, spot(9).y);
      await settle();
      out[how] = {
        same: order() === was, open: api.isOrganiseOpen(), sent: actions().length - sentBefore,
        back: back ? back.options.duration : null, previewLeft: Boolean(lifted()),
        faded: grid.children.some((c) => c.classList.contains("is-dragging")), bar: !byId.organiseInsertion.hidden,
        listeners: windowListenerCount(), selected: selectedPositions(),
      };
    }
    // HTML5 drag-and-drop is not used at all: nothing draggable, no drag
    // listeners, so a drag of pages never reaches pywebview's drop handler.
    const dragTypes = ["dragstart", "dragover", "dragenter", "dragleave", "drop", "dragend"];
    const listens = (el) => dragTypes.some((t) => (el.listeners[t] || []).length);
    out.native = {
      draggable: grid.children.some((c) => c.draggable === true),
      listeners: [grid, scroller, ...grid.children].some(listens),
    };
    out.thumbs = thumbs() - drawn;
    return out;
  },
  // Near the grid's bottom edge a drag scrolls it, faster nearer the edge,
  // and the slot follows the pages under the pointer.
  async autoscroll() {
    await api.openOrganise(); await settle();
    scroller.scrollHeight = 1200; scroller.clientHeight = 600; scroller.scrollTop = 0;
    dispatch(cell(0), "click");
    const x = spot(2).left;
    pdrag(cell(0), x, 590, { end: "none" });
    const steps = [];
    for (let i = 0; i < 4; i++) { await tick(); steps.push(scroller.scrollTop); }
    const out = { steps };
    pointer(grid, "pointermove", x, 570); // further from the edge: slower
    await tick();
    const was = scroller.scrollTop;
    await tick();
    out.slower = scroller.scrollTop - was;
    pointer(grid, "pointermove", x, 250); // clear of the edge: stops
    await tick();
    const still = scroller.scrollTop;
    await tick();
    out.stopped = scroller.scrollTop === still;
    out.scrolled = scroller.scrollTop;
    // Where the drop lands, worked out from the scrolled pages: the bar there.
    const y = 250 + scroller.scrollTop;
    const row = Math.floor(y / (CELL_H + GAP_Y));
    out.expectedGap = row * COLS + 2;
    out.inCell = y % (CELL_H + GAP_Y) < CELL_H;
    out.barTop = byId.organiseInsertion.style.top;
    out.expectedBarTop = `${row * (CELL_H + GAP_Y) + 10}px`;
    release(x, 250); await settle();
    out.move = actions()[0];
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
    const p = spot(0);
    pdrag(cell(11), p.left, p.y); await settle();
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
    while (held.thumbnails.length) { held.thumbnails.shift()(); await settle(); }
    // Asked once the rotation has reached Python (the reply is still held).
    out.doneAsked = calls.filter((c) => c[0] === "organise_done").length;
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
  // Each kind of action shows in the grid before Python has answered, and
  // what it shows is what Python then answers.
  async optimistic() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    holdActions = true;
    const out = {};
    const steps = () => calls.filter((c) => /^organise_(action|undo|redo)$/.test(c[0])).length;
    async function check(name, act) {
      const before = steps();
      act();
      // Microtasks only (a dialog's answer is one): still before the next
      // frame, and before Python has answered.
      for (let i = 0; i < 10; i++) await null;
      const shown = liveKeys();
      const answered = held.actions.length === 0;
      while (held.actions.length) { held.actions.shift()(); await settle(); }
      out[name] = { shownAtOnce: shown, answered, python: pyKeys(), grid: liveKeys(), sent: steps() - before, toasts: failureToasts() };
    }
    await check("drag", () => {
      dispatch(cell(4), "click");
      const p = spot(1);
      pdrag(cell(4), p.left, p.y);
    });
    await check("shift", () => key(activeElement, "ArrowRight", { ctrlKey: true }));
    await check("rotate", () => { dispatch(cell(6), "click"); dispatch(byId.organiseRotateRightBtn, "click"); });
    key(activeElement, "Delete"); await settle();
    await check("delete", () => question("organiseDelete").answer(true));
    await settle();
    await check("undo", () => key(activeElement, "z", { ctrlKey: true }));
    await check("redo", () => key(activeElement, "y", { ctrlKey: true }));
    picks.push({ path: "C:/Files/Appendix.pdf" });
    dispatch(byId.organiseInsertBtn, "click"); await settle();
    await check("insert", () => insertDialog().answer(true));
    out.count = byId.organiseCount.textContent;
    return out;
  },
  // Python answers differently (or refuses): the grid goes to Python's
  // draft, says so, and drops what was queued behind it.
  async diverged() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    holdActions = true;
    dispatch(cell(0), "click");
    divergeNext = true;
    key(activeElement, "ArrowRight", { ctrlKey: true });
    const out = { shownAtOnce: liveKeys().slice(0, 3) };
    key(activeElement, "ArrowRight", { ctrlKey: true }); // queued behind it
    out.queuedShown = liveKeys().slice(0, 3);
    await tick();
    out.inFlight = held.actions.length;
    while (held.actions.length) { held.actions.shift()(); await settle(); }
    out.grid = liveKeys();
    out.python = pyKeys();
    out.sent = actions().length;
    out.toast = toasts[toasts.length - 1].text;
    out.count = byId.organiseCount.textContent;
    out.undoTitle = byId.organiseUndoBtn.title;
    return out;
  },
  // Five Ctrl+→ in a row, faster than Python answers: each shows at once,
  // they reach Python one at a time and in order, and end where they should.
  async rapid() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    holdActions = true;
    dispatch(cell(0), "click");
    const out = { shown: [], inFlight: [] };
    for (let i = 0; i < 5; i++) {
      key(activeElement, "ArrowRight", { ctrlKey: true });
      out.shown.push(liveKeys().indexOf("doc:0"));
      await tick();
      out.inFlight.push(held.actions.length);
    }
    while (held.actions.length) {
      held.actions.shift()(); await settle();
      out.inFlight.push(held.actions.length);
    }
    out.sent = actions().map(([kind, a]) => [kind, a.positions, a.step]);
    out.grid = liveKeys();
    out.python = pyKeys();
    out.toasts = failureToasts();
    out.focus = activeElement && activeElement.dataset.key;
    return out;
  },
  // Thumbnails: none for a move, delete, undo or redo; one in the
  // background for each page Rotate turned.
  async thumbnailRequests() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const thumbs = () => calls.filter((c) => c[0] === "organise_thumbnail");
    const out = { drawn: thumbs().length };
    const imgOf = (c) => c.children[0].children[0].children[0];
    const imgs = new Map(grid.children.map((c) => [c.dataset.key, imgOf(c)]));
    const count = async (act) => { const before = thumbs().length; await act(); await settle(); return thumbs().length - before; };
    dispatch(cell(2), "click");
    out.drag = await count(async () => {
      const before = thumbs().length;
      const p = spot(9);
      pdrag(cell(2), p.right, p.y, { end: "none" });
      for (let i = 0; i < 3; i++) await tick();
      out.duringDrag = thumbs().length - before;
      release(p.right, p.y);
    });
    out.shift = await count(() => key(activeElement, "ArrowLeft", { ctrlKey: true }));
    out.delete = await count(async () => { key(activeElement, "Delete"); await settle(); question("organiseDelete").answer(true); });
    out.undo = await count(() => key(activeElement, "z", { ctrlKey: true }));
    out.redo = await count(() => key(activeElement, "y", { ctrlKey: true }));
    out.undoAgain = await count(() => key(activeElement, "z", { ctrlKey: true }));
    // The same <img> elements throughout (a deleted page's comes back with
    // it), never cleared.
    out.sameImages = grid.children.length === 12 && grid.children.every((c) => imgs.get(c.dataset.key) === imgOf(c));
    out.noneCleared = grid.children.every((c) => !imgOf(c).hidden);
    dispatch(cell(1), "click"); dispatch(cell(3), "click", { ctrlKey: true });
    out.rotatedKeys = [cell(1).dataset.key, cell(3).dataset.key];
    out.rotate = await count(() => dispatch(byId.organiseRotateRightBtn, "click"));
    out.rotateCalls = thumbs().slice(-2).map((c) => [`${c[1]}:${c[2]}`, c[4]]);
    out.rotateBack = await count(() => dispatch(byId.organiseRotateLeftBtn, "click"));
    out.undoRotate = await count(() => key(activeElement, "z", { ctrlKey: true }));
    out.redoRotate = await count(() => key(activeElement, "y", { ctrlKey: true }));
    return out;
  },
  // A turn: the image on screen turns at once and is never cleared; the
  // page drawn at its new turn takes its place once the rotation is over,
  // turned back to 0 with no transition, so nothing visibly changes.
  async rotateSwap() {
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const paper = () => cell(4).children[0].children[0];
    const img = () => paper().children[0];
    dispatch(cell(4), "click");
    const srcBefore = img().src;
    dispatch(byId.organiseRotateRightBtn, "click");
    const out = { turnAtOnce: paper().style["--turn"], imageKept: img().src === srcBefore && !img().hidden };
    await settle();
    out.whileTurning = { turn: paper().style["--turn"], same: img().src === srcBefore };
    await new Promise((r) => setTimeout(r, 260)); await settle();
    out.after = { turn: paper().style["--turn"], swapped: img().src !== srcBefore, hidden: img().hidden,
      size: [paper().style.width, paper().style.height] };
    // Round the clock the short way: back from 90 is -90, never +270.
    dispatch(byId.organiseRotateLeftBtn, "click");
    out.back = paper().style["--turn"];
    return out;
  },
  // Motion: a move slides (the moved page first), a second action finishes
  // the first at once, a delete fades then closes the gap, an insert fades in.
  async motion() {
    holdAnimations = true;
    await api.openOrganise(); await settle();
    intersectAll(); await settle();
    const out = { onOpen: animations.length };
    const slides = (from) => animations.slice(from).filter((a) => String(a.keyframes[0].transform || "").startsWith("translate"));
    dispatch(cell(2), "click");
    let from = animations.length;
    key(activeElement, "ArrowRight", { ctrlKey: true });
    out.move = slides(from).map((a) => [a.el.dataset.key, a.keyframes[0].transform, a.options.duration, a.options.easing]);
    const first = animations.slice(from);
    key(activeElement, "ArrowRight", { ctrlKey: true });
    out.firstFinishedBySecond = first.length > 0 && first.every((a) => a.playState === "finished");
    animations.forEach((a) => a.finish()); await settle();
    from = animations.length;
    key(activeElement, "Delete"); await tick();
    question("organiseDelete").answer(true); await flush();
    const fades = animations.slice(from).filter((a) => a.keyframes[1].opacity === 0);
    out.deleteFade = fades.map((a) => [a.el.dataset.key, a.options.duration, a.keyframes[1].transform]);
    out.ghosts = grid.children.filter((c) => c.classList.contains("is-leaving")).length;
    out.liveDuringFade = liveKeys().length;
    out.slidBeforeFadeEnds = slides(from).length;
    fades.forEach((a) => a.finish()); await tick(); await tick();
    out.ghostsAfterFade = grid.children.filter((c) => c.classList.contains("is-leaving")).length;
    out.gapSlide = slides(from).map((a) => [a.el.dataset.key, a.keyframes[0].transform, a.options.duration]).slice(0, 2);
    animations.forEach((a) => a.finish()); await settle();
    // A delete cut short by the next action: the fading pages go at once.
    dispatch(cell(0), "click");
    key(activeElement, "Delete"); await tick();
    question("organiseDelete").answer(true); await flush();
    out.ghostsBeforeCut = grid.children.filter((c) => c.classList.contains("is-leaving")).length;
    out.fadingIgnoresClicks = (dispatch(cell(0), "click"), !byId.organiseSelectionText.textContent.includes("selected"));
    dispatch(grid.children.find((c) => !c.classList.contains("is-leaving")), "click");
    key(activeElement, "ArrowRight", { ctrlKey: true });
    out.ghostsWhenCut = grid.children.filter((c) => c.classList.contains("is-leaving")).length;
    animations.forEach((a) => a.finish()); await settle();
    from = animations.length;
    picks.push({ path: "C:/Files/Appendix.pdf" });
    dispatch(byId.organiseInsertBtn, "click"); await settle();
    insertDialog().answer(true); await flush();
    out.insertFades = animations.slice(from).filter((a) => a.keyframes[0].opacity === 0).map((a) => a.el.dataset.key);
    out.insertSlides = slides(from).length;
    animations.forEach((a) => a.finish()); await settle();
    out.total = animations.length;
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
    // Python refuses what the page already showed: back to Python's draft.
    actionReply = { ok: false, reason: "bad_positions" };
    dispatch(byId.organiseRotateLeftBtn, "click");
    out.turnedAtOnce = cell(0).children[0].children[0].style["--turn"];
    await settle();
    out.toast = toasts.map((t) => t.text);
    out.arg = actions()[0];
    out.turnAfter = cell(0).children[0].children[0].style["--turn"];
    out.count = byId.organiseCount.textContent;
    out.stillSelected = selectedPositions();
    // Deleting every page is refused before anything is sent.
    actionReply = null;
    key(cell(0), "a", { ctrlKey: true });
    const sent = actions().length;
    out.everyPage = organiseActNow("delete", { positions: [...Array(12).keys()] });
    out.everyPageSent = actions().length - sent;
    out.everyPageToast = toasts[toasts.length - 1].text;
    return out;
  },
};

scenarios[process.env.SCENARIO]().then(
  (out) => process.stdout.write(JSON.stringify(out)),
  (err) => { process.stderr.write(String(err && err.stack)); process.exit(1); },
);
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
def translate(transform: str) -> tuple[float, float]:
    """The x and y of a `translate3d(...)` transform."""
    inner = transform.split("translate3d(", 1)[1].split(")", 1)[0]
    x, y = (float(part.strip().removesuffix("px")) for part in inner.split(",")[:2])
    return x, y


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
        self.assertTrue(out["noTextSelection"])
        self.assertEqual(out["captured"], 1)  # the pointer is held by the grid
        # The lifted preview: the page's own image, at 90%, following the
        # pointer from where the drag began (placed then, not a frame later).
        preview = out["preview"]
        self.assertTrue(preview["sameImage"])
        self.assertFalse(preview["badge"])  # one page: no count
        start = translate(out["previewAtStart"])
        now = translate(preview["transform"])
        self.assertEqual((now[0] - start[0], now[1] - start[1]), tuple(out["pointerMoved"]))
        self.assertTrue(preview["transform"].endswith("scale(0.9)"))
        # Moves between frames draw nothing; the next frame draws the last.
        self.assertTrue(out["betweenFrames"])
        later = translate(out["nextFrame"])
        self.assertEqual((later[0] - now[0], later[1] - now[1]), (20, 20))
        self.assertTrue(out["bar"]["shown"])
        self.assertEqual(out["bar"]["left"], "150.5px")  # in the column gap left of page 2
        self.assertEqual(out["move"], ["move", {"positions": [8], "gap": 1}])
        self.assertEqual(out["order"], ["doc:0", "doc:8", "doc:1"])
        self.assertEqual(out["selectedAfter"], [1])  # the click after the release cleared nothing
        self.assertTrue(out["barAfterDrop"])
        self.assertFalse(out["fadedAfterDrop"])
        self.assertFalse(out["previewAfterDrop"])
        self.assertFalse(out["textSelectionAfter"])
        self.assertIsNone(out["capturedAfter"])
        self.assertEqual(out["windowListeners"], 0)  # nothing left listening
        self.assertEqual(out["selfDrop"], 1)  # dropped on itself: no second call
        self.assertEqual(out["badge"], "2 pages")
        self.assertEqual(out["rowEndBar"], f"{6 * 156 + 148 + 4 - 1.5}px")
        self.assertEqual(out["rowEnd"], ["move", {"positions": [0], "gap": 7}])

    def test_a_press_that_barely_moves_is_a_click_not_a_drag(self):
        out = self.run_scenario("pointerDragging")
        self.assertTrue(out["pressPrevented"])  # no native image drag or text selection
        self.assertEqual(out["click"], {"selected": [2], "dragged": False, "preview": False, "actions": 0, "listeners": 0})

    def test_dragging_ends_where_the_same_ctrl_arrow_move_does(self):
        out = self.run_scenario("pointerDragging")
        self.assertEqual(out["oneDragged"], out["oneKeyed"])
        self.assertEqual(out["oneDragged"]["order"].split()[:2], ["doc:1", "doc:0"])
        self.assertEqual(out["twoDragged"], out["twoKeyed"])
        self.assertEqual(out["twoDragged"]["order"].split()[:6], ["doc:0", "doc:1", "doc:2", "doc:5", "doc:3", "doc:4"])
        self.assertEqual(out["twoDragged"]["order"], out["twoDragged"]["python"])

    def test_a_drag_called_off_changes_nothing(self):
        out = self.run_scenario("pointerDragging")
        for how in ("esc", "cancel", "blur"):
            with self.subTest(how):
                self.assertEqual(out[how], {
                    "same": True, "open": True, "sent": 0,  # Esc ends the drag, not Organise
                    "back": 150,  # the preview goes back to its page
                    "previewLeft": False, "faded": False, "bar": False, "listeners": 0,
                    "selected": [5],  # the release after it is no click
                })

    def test_a_drag_cancelled_under_reduced_motion_goes_back_at_once(self):
        out = self.run_scenario("pointerDragging", REDUCED_MOTION="1")
        for how in ("esc", "cancel", "blur"):
            with self.subTest(how):
                self.assertIsNone(out[how]["back"])
                self.assertFalse(out[how]["previewLeft"])

    def test_dragging_pages_uses_no_native_drag_and_drop(self):
        out = self.run_scenario("pointerDragging")
        # Nothing for pywebview's document drop listener (Api.bind_file_drop)
        # to hear: no page is draggable and nothing listens for drag events.
        self.assertEqual(out["native"], {"draggable": False, "listeners": False})
        self.assertEqual(out["thumbs"], 0)  # no thumbnail drawn again for any of it

    def test_dragging_near_the_bottom_edge_scrolls_the_grid(self):
        out = self.run_scenario("autoscroll")
        # 10px from the edge: 20 * (1 - 10/48) px a frame, every frame.
        step = 20 * (1 - 10 / 48)
        for i, at in enumerate(out["steps"]):
            self.assertAlmostEqual(at, step * (i + 1))
        self.assertAlmostEqual(out["slower"], 20 * (1 - 30 / 48))  # 30px away: slower
        self.assertTrue(out["stopped"])  # clear of the edge
        # The drop lands among the pages scrolled under the pointer.
        self.assertTrue(out["inCell"])
        self.assertEqual(out["barTop"], out["expectedBarTop"])
        self.assertEqual(out["move"], ["move", {"positions": [0], "gap": out["expectedGap"]}])

    def test_thumbnails_are_lazy_three_at_a_time_and_drawn_once(self):
        out = self.run_scenario("thumbnails")
        self.assertEqual(out["first"], 3)
        self.assertEqual(out["args"], ["organise_thumbnail", "doc", 0, 120, 0])  # the page at its turn
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
        self.assertEqual(out["count"], "9 pages · 1 unsaved change")
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
            "open": False, "deleted": ["delete", {"positions": [3, 4, 5]}], "count": "9 pages · 1 unsaved change",
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

    def test_every_kind_of_action_shows_at_once_and_matches_python(self):
        out = self.run_scenario("optimistic")
        for kind in ("drag", "shift", "rotate", "delete", "undo", "redo", "insert"):
            with self.subTest(kind):
                step = out[kind]
                # Shown before Python answered, and exactly what it answered.
                self.assertEqual(step["shownAtOnce"], step["python"])
                self.assertEqual(step["grid"], step["python"])
                self.assertEqual(step["sent"], 1)
                self.assertFalse(step["answered"])
                self.assertEqual(step["toasts"], [])
        self.assertEqual(out["drag"]["shownAtOnce"][:3], ["doc:0", "doc:4", "doc:1"])
        self.assertIn("doc:6@90deg", out["rotate"]["shownAtOnce"])
        self.assertEqual(len(out["delete"]["shownAtOnce"]), 11)
        self.assertEqual(len(out["insert"]["shownAtOnce"]), 14)
        self.assertEqual(out["count"], "14 pages · 5 unsaved changes")

    def test_a_step_python_does_differently_goes_back_to_pythons_draft(self):
        out = self.run_scenario("diverged")
        self.assertEqual(out["shownAtOnce"], ["doc:1", "doc:0", "doc:2"])
        self.assertEqual(out["queuedShown"], ["doc:1", "doc:2", "doc:0"])
        self.assertEqual(out["inFlight"], 1)  # one at a time
        # Python's draft (it turned the last page instead), on screen.
        self.assertEqual(out["grid"], out["python"])
        self.assertEqual(out["python"][0], "doc:0")
        self.assertEqual(out["python"][-1], "doc:11@180deg")
        self.assertEqual(out["sent"], 1)  # the step queued behind it was dropped
        self.assertEqual(out["toast"], "Couldn't move that page")
        self.assertEqual(out["count"], "12 pages · 1 unsaved change")
        self.assertEqual(out["undoTitle"], "Undo  (Ctrl+Z)")

    def test_five_quick_moves_reach_python_in_order_and_end_right(self):
        out = self.run_scenario("rapid")
        self.assertEqual(out["shown"], [1, 2, 3, 4, 5])  # each at once
        self.assertTrue(all(n <= 1 for n in out["inFlight"]), out["inFlight"])  # never two at once
        self.assertEqual(out["sent"], [["shift", [i], 1] for i in range(5)])
        self.assertEqual(out["grid"], out["python"])
        self.assertEqual(out["python"][:6], ["doc:1", "doc:2", "doc:3", "doc:4", "doc:5", "doc:0"])
        self.assertEqual(out["toasts"], [])
        self.assertEqual(out["focus"], "doc:0")

    def test_no_thumbnail_is_fetched_for_a_move_delete_undo_or_redo(self):
        out = self.run_scenario("thumbnailRequests")
        self.assertEqual(out["drawn"], 12)
        self.assertEqual(out["duringDrag"], 0)  # nor while a drag is held
        for kind in ("drag", "shift", "delete", "undo", "redo", "undoAgain"):
            with self.subTest(kind):
                self.assertEqual(out[kind], 0)
        self.assertTrue(out["sameImages"])
        self.assertTrue(out["noneCleared"])
        # Rotate: one background request per turned page, at its new turn.
        self.assertEqual(out["rotate"], 2)
        self.assertEqual(sorted(out["rotateCalls"]), sorted([key, 90] for key in out["rotatedKeys"]))
        # Back to turns already drawn: nothing more.
        self.assertEqual([out["rotateBack"], out["undoRotate"], out["redoRotate"]], [0, 0, 0])

    def test_a_turn_rotates_the_image_on_screen_then_swaps_in_the_turned_page(self):
        out = self.run_scenario("rotateSwap")
        self.assertEqual(out["turnAtOnce"], "90deg")
        self.assertTrue(out["imageKept"])
        self.assertEqual(out["whileTurning"], {"turn": "90deg", "same": True})
        self.assertEqual(out["after"], {"turn": "0deg", "swapped": True, "hidden": False, "size": ["120px", "85px"]})
        self.assertEqual(out["back"], "-90deg")

    def test_motion_slides_fades_and_never_waits(self):
        out = self.run_scenario("motion")
        self.assertEqual(out["onOpen"], 0)  # opening draws the grid still
        moved = {key: (shift, duration) for key, shift, duration, _ in out["move"]}
        self.assertEqual(moved["doc:2"], ("translate(-156px, 0px)", 140))  # the moved page lands first
        self.assertEqual(moved["doc:3"], ("translate(156px, 0px)", 180))
        self.assertEqual({easing for *_, easing in out["move"]}, {"cubic-bezier(0.2, 0.8, 0.2, 1)"})
        self.assertTrue(out["firstFinishedBySecond"])
        self.assertEqual(out["deleteFade"], [["doc:2", 120, "scale(0.92)"]])
        self.assertEqual((out["ghosts"], out["liveDuringFade"], out["slidBeforeFadeEnds"]), (1, 11, 0))
        self.assertEqual(out["ghostsAfterFade"], 0)
        self.assertEqual(out["gapSlide"][0][2], 180)
        self.assertEqual((out["ghostsBeforeCut"], out["ghostsWhenCut"]), (1, 0))
        self.assertTrue(out["fadingIgnoresClicks"])
        self.assertEqual(out["insertFades"], ["insert-0:0", "insert-0:1", "insert-0:2"])
        self.assertGreater(out["insertSlides"], 0)

    def test_reduced_motion_moves_nothing_and_is_just_as_immediate(self):
        out = self.run_scenario("motion", REDUCED_MOTION="1")
        self.assertEqual(out["total"], 0)
        self.assertEqual((out["ghosts"], out["liveDuringFade"]), (0, 11))  # a deleted page goes at once
        self.assertEqual(out["insertFades"], [])
        out = self.run_scenario("rotateSwap", REDUCED_MOTION="1")
        self.assertEqual(out["turnAtOnce"], "90deg")
        self.assertEqual(out["whileTurning"]["turn"], "0deg")  # the turned page as soon as it is drawn

    def test_disabled_actions_do_nothing_and_refusals_say_why(self):
        out = self.run_scenario("refusals")
        self.assertEqual(out["disabledClick"], 0)
        self.assertEqual(out["extractNow"], ["false", ""])  # Extract arrived with F2.6
        self.assertEqual(out["deleteNow"], "false")  # Delete arrived with F2.4
        self.assertEqual(out["arg"], ["rotate", {"positions": [0], "degrees": -90}])
        # Shown at once, then taken back when Python refused it, with a toast.
        self.assertEqual(out["turnedAtOnce"], "-90deg")
        self.assertEqual(out["toast"], ["Couldn't rotate that page"])
        self.assertEqual(out["turnAfter"], "0deg")
        self.assertEqual(out["count"], "12 pages")
        self.assertEqual(out["stillSelected"], [0])
        # A refusal the page can see itself is said at once and never sent.
        self.assertEqual(out["everyPage"], {"ok": False, "reason": "every_page"})
        self.assertEqual(out["everyPageSent"], 0)
        self.assertEqual(out["everyPageToast"], "A PDF needs at least one page. Leave one unselected.")


if __name__ == "__main__":
    unittest.main()
