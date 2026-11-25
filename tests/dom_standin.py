"""A stand-in DOM for running frontend scripts under Node (no browser), shared
by the stepped dialog's, the file browser's and Organise's return tests.

`DOM_JS` defines `makeEl`, `document`, `window`, `animations` (every
`animate()` call), `keydown(key, mods)` (a key on the focused element,
bubbling to the document as in a page), `context(extra)` (a vm context with
the usual page helpers stubbed) and `load(context, files)`. Elements support
what the dialogs use: children and parents, classes, attributes, `dataset`,
`hidden`, focus, events that bubble, `cloneNode`, and selectors made of
tags, classes, ids, attributes, `:not(...)`, `:scope >` and descendants.
Layout is faked: an element's height is 20px for each element inside it, so
two steps with different content have different heights.

`run_node(harness, files)` runs a harness (this DOM first) and returns what
it printed, as JSON.
"""
import json
import subprocess
import tempfile
from pathlib import Path

DOM_JS = r"""
const fs = require("fs");
const vm = require("vm");

const animations = [];

function parseCompound(text) {
  const parts = { tag: null, classes: [], id: null, attrs: [], nots: [] };
  let rest = text;
  const take = (re) => { const m = rest.match(re); if (m) rest = rest.slice(m[0].length); return m; };
  let m = take(/^(\*|[a-zA-Z][\w-]*)/);
  if (m && m[0] !== "*") parts.tag = m[0].toUpperCase();
  while (rest.length) {
    if ((m = take(/^\.([\w-]+)/))) parts.classes.push(m[1]);
    else if ((m = take(/^#([\w-]+)/))) parts.id = m[1];
    else if ((m = take(/^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/))) parts.attrs.push([m[1], m[2]]);
    else if ((m = take(/^:not\(([^)]*)\)/))) parts.nots.push(parseCompound(m[1]));
    else throw new Error(`stand-in DOM: can't read selector "${text}"`);
  }
  return parts;
}

function attrOf(node, name) {
  if (name === "hidden") return node.hidden ? "" : null;
  if (name === "id") return node.id || null;
  if (name === "tabindex") return String(node.tabIndex);
  if (name.startsWith("data-")) {
    const key = name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase());
    return key in node.dataset ? node.dataset[key] : null;
  }
  return name in node.attrs ? node.attrs[name] : null;
}

function matchCompound(node, parts) {
  if (!node || !node.tagName) return false;
  if (parts.tag && node.tagName !== parts.tag) return false;
  if (parts.id && node.id !== parts.id) return false;
  if (!parts.classes.every((c) => node.classList.contains(c))) return false;
  for (const [name, value] of parts.attrs) {
    const has = attrOf(node, name);
    if (has === null || (value !== undefined && has !== value)) return false;
  }
  return parts.nots.every((n) => !matchCompound(node, n));
}

function matchesOne(node, selector, scope) {
  let text = selector.trim();
  let direct = false;
  if (text.startsWith(":scope >")) { direct = true; text = text.slice(8).trim(); }
  const steps = text.split(/\s+/).map(parseCompound);
  if (!matchCompound(node, steps[steps.length - 1])) return false;
  if (direct) return node.parentElement === scope;
  let n = node.parentElement;
  for (let i = steps.length - 2; i >= 0; i--) {
    while (n && !matchCompound(n, steps[i])) n = n.parentElement;
    if (!n) return false;
    n = n.parentElement;
  }
  return true;
}

function matches(node, selector, scope) {
  return selector.split(",").some((part) => matchesOne(node, part, scope));
}

function makeEl(tag) {
  const listeners = {};
  const classes = new Set();
  const style = {
    setProperty(k, v) { this[k] = v; }, getPropertyValue(k) { return this[k] || ""; }, removeProperty(k) { delete this[k]; },
  };
  const node = {
    tagName: tag.toUpperCase(), children: [], parentElement: null, attrs: {}, dataset: {}, style,
    hidden: false, disabled: false, tabIndex: -1, value: "", placeholder: "", id: "", type: "", checked: false,
    title: "", inert: false, scrollTop: 0, _text: "",
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(v) { this._text = String(v); this.children.forEach((c) => { c.parentElement = null; }); this.children = []; },
    // Only ever cleared this way by the scripts under test.
    set innerHTML(v) { if (v !== "") throw new Error("stand-in DOM: innerHTML only clears"); this.textContent = ""; },
    get innerHTML() { return ""; },
    // As a browser lists them: running ones, and finished ones that hold
    // their end (`fill: "forwards"`), until cancelled.
    _anims: [],
    getAnimations() {
      return this._anims
        .filter((a) => !a.cancelled && (!a.finished || (a.options && a.options.fill === "forwards")))
        .map((a) => ({ record: a, cancel() { a.cancelled = true; }, finish() { a.finished = true; } }));
    },
    classList: {
      add: (...cs) => cs.forEach((c) => classes.add(c)), remove: (...cs) => cs.forEach((c) => classes.delete(c)),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: (c) => classes.has(c),
    },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(" "); },
    setAttribute(k, v) { if (k === "id") this.id = String(v); else this.attrs[k] = String(v); },
    getAttribute(k) { return attrOf(this, k); },
    removeAttribute(k) { if (k === "id") this.id = ""; else delete this.attrs[k]; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener(type, fn) { const l = listeners[type] || []; if (l.includes(fn)) l.splice(l.indexOf(fn), 1); },
    dispatch(type, props = {}) {
      const ev = { type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; }, ...props };
      for (let n = this; n && !ev.stopped; n = n.parentElement) (n._listeners()[type] || []).slice().forEach((fn) => fn(ev));
      return ev;
    },
    _listeners: () => listeners,
    click(props = {}) { if (!this.disabled) this.dispatch("click", props); },
    get firstChild() { return this.children[0] || null; },
    appendChild(k) { if (k.parentElement) k.remove(); k.parentElement = this; this.children.push(k); return k; },
    append(...ks) { ks.forEach((k) => this.appendChild(typeof k === "string" ? document.createTextNode(k) : k)); },
    prepend(...ks) { ks.reverse().forEach((k) => { if (k.parentElement) k.remove(); k.parentElement = this; this.children.unshift(k); }); },
    insertBefore(k, ref) {
      if (k.parentElement) k.remove();
      const i = ref ? this.children.indexOf(ref) : -1;
      k.parentElement = this;
      if (i < 0) this.children.push(k); else this.children.splice(i, 0, k);
      return k;
    },
    replaceChildren(...ks) { this.children.forEach((c) => { c.parentElement = null; }); this.children = []; this._text = ""; this.append(...ks); },
    remove() { const p = this.parentElement; if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentElement = null; } },
    replaceWith(k) {
      const p = this.parentElement; const i = p.children.indexOf(this);
      if (k.parentElement) k.remove();
      this.parentElement = null; k.parentElement = p; p.children[i] = k;
    },
    contains(k) { for (let n = k; n; n = n.parentElement) if (n === this) return true; return false; },
    closest(sel) { for (let n = this; n; n = n.parentElement) if (n.tagName && matches(n, sel, n.parentElement)) return n; return null; },
    matches(sel) { return matches(this, sel, this.parentElement); },
    querySelectorAll(sel) {
      const out = [];
      const walk = (n) => n.children.forEach((c) => { if (c.tagName !== "#TEXT" && matches(c, sel, this)) out.push(c); walk(c); });
      walk(this);
      return out;
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    cloneNode(deep) {
      const copy = makeEl(this.tagName.toLowerCase());
      copy.className = this.className; copy.id = this.id; copy._text = this._text;
      Object.assign(copy.attrs, this.attrs); Object.assign(copy.dataset, this.dataset);
      if (deep) this.children.forEach((c) => copy.appendChild(c.cloneNode(true)));
      return copy;
    },
    get isConnected() { let n = this; while (n.parentElement) n = n.parentElement; return n === document.body; },
    focus() { document.activeElement = this; },
    blur() { if (document.activeElement === this) document.activeElement = null; },
    setSelectionRange() {}, selectionStart: 0, selectionEnd: 0,
    animate(frames, options) {
      const record = { node: this, frames, options, finished: false, cancelled: false };
      animations.push(record);
      this._anims.push(record);
      let resolve;
      const finished = new Promise((r) => { resolve = r; });
      setTimeout(() => { record.finished = true; resolve(); }, 1);
      return { finished, finish() { record.finished = true; resolve(); }, cancel() { record.cancelled = true; } };
    },
    getBoundingClientRect() {
      const height = 20 * this.querySelectorAll("*").length;
      return { top: 0, bottom: height, left: 0, right: 500, width: 500, height };
    },
  };
  return node;
}

const docListeners = {};
const document = {
  createElement: makeEl, activeElement: null, documentElement: makeEl("html"),
  createTextNode(text) { const n = makeEl("#text"); n._text = String(text); return n; },
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
  removeEventListener(type, fn) { const l = docListeners[type] || []; if (l.includes(fn)) l.splice(l.indexOf(fn), 1); },
  querySelector(sel) { return document.body.querySelector(sel); },
  querySelectorAll(sel) { return document.body.querySelectorAll(sel); },
  getElementById(id) { return document.body.querySelector(`#${id}`); },
};
document.body = makeEl("body");

// A key on the focused element, bubbling to the document as in a page.
function keydown(key, mods = {}) {
  const ev = { type: "keydown", key, defaultPrevented: false, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...mods };
  const target = document.activeElement || document.body;
  ev.target = target;
  for (let n = target; n && !ev.stopped; n = n.parentElement) (n._listeners()[ev.type] || []).slice().forEach((fn) => fn(ev));
  if (!ev.stopped) (docListeners.keydown || []).slice().forEach((fn) => fn(ev));
  return ev;
}

const window = { addEventListener() {}, removeEventListener() {}, location: { href: "index.html", reload() {} } };
let reducedMotion = false;
const toasts = [];

function context(extra = {}) {
  return vm.createContext({
    window, document, console, setTimeout, clearTimeout, queueMicrotask, Promise, Map, WeakMap, Set, Math, JSON,
    String, Array, Number, Boolean, Object, Error,
    requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame: clearTimeout,
    matchMedia: () => ({ matches: reducedMotion }),
    getComputedStyle: () => ({ getPropertyValue: (name) => ({ "--dur-base": "200ms", "--ease-out": "ease" }[name] || "") }),
    showToast: (t) => toasts.push(t),
    mountIcons: async () => {}, mountIcon: async () => {},
    createVoiceScope: () => ({ enter() {}, leave() {} }), trapFocus: () => () => {},
    initRadioCards() {},
    isTypingTarget: (n) => Boolean(n) && (n.tagName === "INPUT" || n.tagName === "TEXTAREA"),
    plural: (n, one, many) => `${n} ${n === 1 ? one : many}`,
    ...extra,
  });
}

function load(ctx, files) {
  for (const file of files) vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
}

const settle = (ms = 10) => new Promise((r) => setTimeout(r, ms));
const textOf = (n) => (n ? n.textContent : null);
"""


def run_node(harness: str, files: list[Path], timeout: int = 60) -> dict:
    """Run `harness` (after DOM_JS) under Node with `files` as its argv, and
    return the JSON it printed."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "harness.js"
        script.write_text(DOM_JS + harness, encoding="utf-8")
        result = subprocess.run(
            ["node", str(script), *[str(f) for f in files]],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)
