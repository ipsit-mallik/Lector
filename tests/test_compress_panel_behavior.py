"""Behaviour of frontend/pages/compress-panel.js (and its rules,
compress-rules.js) and Compress's parts of
frontend/js/file-jobs.js (docs/PRD.md § "Post-v1: file tools" → Compress).

Held here:
- Quality: three separate option cards (the shared `.option-row card-lift`
  radio card, so they have its hover); a level under 5% smaller is "No
  change", disabled, dashed, with the file's own size and the tooltip; the
  default selection rules (Balanced until the estimates are in; then Balanced
  if it helps, else Smallest file, else Best quality; a card the reader
  picked stays unless it turns out "No change");
- Size limit: pills at or above the file's size are hidden ("already under
  50 KB" when none is left); pills below the floor are disabled with the
  floor in their tooltip; pills not yet decided are muted and inert under a
  "Checking…" card; a selected pill picks the best step at 97% of its limit
  or less (the same answer as compress_plan.choose, case by case) and the
  result card says what that step does in plain words; the last limit used
  is selected once it is reachable, and otherwise none is; "Other size…"
  checks the typed value against the floor, and Esc closes it;
- sizes count 1 KB as 1,000 bytes; the options each tab sends; the Done
  state's readability note;
- a big file's estimates: the page's `compressDecision` gives the same
  answer as limit_plan.decide, case by case; a pill is enabled on an
  estimate at 80% of its limit or less, dimmed at 130% or more, and
  "Checking…" between; its card shows the estimate and changes when a real
  result changes the step; the limit chosen is sent to Python;
- a result older than one already shown (by its sequence number) is
  ignored, from an event or from the snapshot;
- "Not enough free disk space": in the result card and in Quality, Compress
  off in Size limit, and a new Save in folder asks again;
- the Done state says when a lower step was used to fit.

Run under Node like tests/test_error_text_behavior.py, with a small stand-in
for the DOM: the panel builds its cards from `el` calls, which build plain
objects here, so what it renders can be read back. Skipped when Node is not
installed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.file_tools import compress_plan, limit_plan  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SCRIPTS = [FRONTEND / "js" / "file-jobs.js", FRONTEND / "pages" / "compress-rules.js", FRONTEND / "pages" / "compress-panel.js"]

# (sizes by step, None for a step that failed; limit) → (step, settled).
# Run through compress_plan.choose here and compressChoice in the page: the
# job checks again what the page offered, so the two must agree.
CHOICE_CASES = [
    ({}, 100_000),
    ({9: 9_000}, 100_000),
    ({9: 9_000, 0: 900_000}, 100_000),
    ({9: 9_000, 0: 900_000, 8: 10_000, 7: 29_000, 6: 57_000}, 100_000),
    ({9: 9_000, 0: 900_000, 8: 10_000, 7: 29_000, 6: 57_000, 5: 98_000}, 100_000),
    ({9: 9_000, 0: 900_000, 8: 10_000, 7: 29_000, 6: 57_000, 5: 97_000}, 100_000),
    ({9: 9_000, 0: 900_000, 8: 10_000, 7: 29_000, 6: None, 5: 98_000}, 100_000),
    ({9: 200_000, 0: 900_000}, 100_000),
    ({9: None, 0: 900_000}, 100_000),
    ({n: 500_000 for n in range(10)}, 100_000),
    ({9: 500_000, 0: 400_000}, 600_000),
    ({9: 9_000, 1: 50_000}, 100_000),
]


def _flat(size: int) -> dict:
    return {n: size for n in range(10)}


# (real sizes, estimates, limit) → (step, settled), through limit_plan.decide
# and compressDecision. Estimates are step → size, None for one that failed.
_LADDER = [5_000_000, 4_000_000, 2_000_000, 1_500_000, 1_200_000, 700_000, 600_000, 300_000, 100_000, 90_000]
# Steps 1–3 estimated just over 1 MB (98–105%).
_CLOSE = [3_000_000, 1_050_000, 1_020_000, 980_000, 700_000, 600_000, 500_000, 400_000, 300_000, 100_000]
DECISION_CASES = [
    ({}, None, 1_000_000),
    ({}, _flat(800_000), 1_000_000),
    ({}, _flat(800_001), 1_000_000),
    ({}, _flat(1_300_000), 1_000_000),
    ({}, _flat(1_299_999), 1_000_000),
    ({}, {9: 100_000}, 1_000_000),
    ({}, {9: 2_000_000, 0: 9_000_000}, 1_000_000),
    ({5: 1_100_000}, {**_flat(5_000_000), **{n: 700_000 for n in range(5, 10)}}, 1_000_000),
    ({4: 900_000, 3: 1_200_000}, {**_flat(5_000_000), **{n: 700_000 for n in range(5, 10)}}, 1_000_000),
    ({9: 1_050_000}, {0: 9_000_000, 9: 1_000_000}, 1_000_000),
    ({}, {**_flat(5_000_000), 9: None, 8: 600_000}, 1_000_000),
    ({9: None}, {**_flat(2_000_000), 0: 3_000_000}, 1_000_000),
    ({}, dict(enumerate(_LADDER)), 2_000_000),
    ({}, dict(enumerate(_LADDER)), 1_250_000),
    ({8: 840_000}, {**_flat(9_000_000), 7: 1_900_000, 8: 850_000, 9: 850_000}, 2_000_000),
    # A better step estimated just over the limit, or one the sample couldn't
    # make, is in doubt: reachable, not final, until it is made for real.
    ({4: 700_000, 9: 100_000, 0: 3_000_000}, dict(enumerate(_CLOSE)), 1_000_000),
    ({}, dict(enumerate(_CLOSE)), 1_000_000),
    ({4: 700_000, 3: 955_000}, dict(enumerate(_CLOSE)), 1_000_000),
    ({4: 700_000, 3: 955_000, 2: 1_030_000}, dict(enumerate(_CLOSE)), 1_000_000),
    ({4: 700_000}, {**dict(enumerate(_CLOSE)), 3: None}, 1_000_000),
    ({4: 700_000, 3: None}, {**dict(enumerate(_CLOSE)), 2: 1_300_000}, 1_000_000),
    ({}, {**dict(enumerate(_CLOSE)), 3: 1_300_000}, 1_000_000),
]

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// ---- A stand-in for the DOM: just enough for the panel's own building ----
class FakeNode {
  constructor(tag) {
    this.tag = tag; this.className = ""; this.children = []; this.attrs = {}; this.dataset = {};
    this.listeners = {}; this.textContent = ""; this.style = { setProperty() {} };
  }
  get classList() {
    const self = this;
    const set = () => new Set(self.className.split(/\s+/).filter(Boolean));
    return {
      add: (...names) => { const s = set(); names.forEach((n) => s.add(n)); self.className = [...s].join(" "); },
      toggle: (name, on) => { const s = set(); if (on) s.add(name); else s.delete(name); self.className = [...s].join(" "); },
      contains: (name) => set().has(name),
    };
  }
  append(...kids) { kids.forEach((k) => this.children.push(typeof k === "string" ? Object.assign(new FakeNode("#text"), { textContent: k }) : k)); }
  appendChild(k) { this.children.push(k); return k; }
  prepend(k) { this.children.unshift(k); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  click() { if (this.listeners.click) this.listeners.click(); }
  focus() {}
}
const all = (node, out = []) => { out.push(node); (node.children || []).forEach((c) => all(c, out)); return out; };
const byClass = (root, name) => all(root).filter((n) => n.classList && n.classList.contains(name));
const textOf = (node) => (node.textContent || "") + (node.children || []).map(textOf).join("");

const listeners = {};
const answers = { start_compress_estimates: { ok: true, session: 7 }, get_compress_estimates: {} };
const apiCalls = [];
const context = vm.createContext({
  console,
  setTimeout,
  window: { addEventListener: (type, fn) => { listeners[type] = fn; } },
  document: { getElementById: () => null },
  callApi: (method, ...args) => { apiCalls.push([method, ...args]); return Promise.resolve(answers[method] || { ok: true }); },
  el: (tag, className, text) => { const n = new FakeNode(tag); if (className) n.className = className; if (text !== undefined) n.textContent = text; return n; },
  iconEl: (name, className = "") => { const n = new FakeNode("span"); n.className = className; n.dataset.icon = name; return n; },
  initRadioCards: () => {},
  plural: (n, one, many) => `${n} ${n === 1 ? one : many}`,
});
for (const file of process.argv.slice(1)) vm.runInContext(fs.readFileSync(file, "utf8"), context);
const run = (code) => vm.runInContext(code, context);

const results = {};
const check = (name, got, want) => {
  results[name] = JSON.stringify(got) === JSON.stringify(want) || `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`;
};

// ---- Pure rules ----
const KB = 1000, MB = 1000 * 1000;
check("sizes count 1 KB as 1,000 bytes",
  run(`[compressSize(96000), compressSize(1100000), compressSize(999600), compressSize(300), compressSize(2730000)]`),
  ["96 KB", "1.1 MB", "1.0 MB", "1 KB", "2.7 MB"]);
check("a step fits at 97% of the limit", run(`[compressFits(97000, 100000), compressFits(97001, 100000)]`), [true, false]);
check("the Other size field", run(`["100", "1", "9999", "0", "10000", "", "1.5", "1e3", " 12"].map(compressLimitValue)`),
  [100, 1, 9999, null, null, null, null, null, null]);
check("the ladder's length", run("COMPRESS_LADDER_STEPS"), Number(process.env.LADDER_STEPS));
const rows = (sizes) => Object.fromEntries(Object.entries(sizes).map(([k, v]) => [k, v === null ? { state: "failed" } : { state: "done", size: v }]));
check("compressChoice agrees with compress_plan.choose",
  JSON.parse(process.env.CHOICE_CASES).map(([sizes, limit]) => run(`compressChoice(${JSON.stringify(rows(sizes))}, ${limit})`)),
  JSON.parse(process.env.CHOICE_WANT));
const estimateRows = (sizes) => sizes === null ? null
  : Object.fromEntries(Object.entries(sizes).map(([k, v]) => [k, v === null ? null : { size: v, kind: "good" }]));
check("compressDecision agrees with limit_plan.decide",
  JSON.parse(process.env.DECISION_CASES).map(([real, est, limit]) =>
    run(`compressDecision(${JSON.stringify(rows(real))}, ${JSON.stringify(estimateRows(est))}, ${limit})`)),
  JSON.parse(process.env.DECISION_WANT));

const level = (step, helps, size = 1000) => ({ [step]: { state: "done", size, helps } });
check("default: Balanced when it helps",
  run(`compressDefaultLevel(${JSON.stringify({ ...level(0, true), ...level(2, true), ...level(4, true) })})`), "balanced");
check("default: Smallest file when Balanced doesn't",
  run(`compressDefaultLevel(${JSON.stringify({ ...level(0, false), ...level(2, false), ...level(4, true) })})`), "strong");
check("default: Best quality when neither does",
  run(`compressDefaultLevel(${JSON.stringify({ ...level(0, true), ...level(2, false), ...level(4, false) })})`), "light");
check("default: none when no level helps",
  run(`compressDefaultLevel(${JSON.stringify({ ...level(0, false), ...level(2, false), ...level(4, false) })})`), null);
check("default: a failed estimate still counts",
  run(`compressDefaultLevel(${JSON.stringify({ ...level(0, false), 2: { state: "failed" }, ...level(4, true) })})`), "balanced");

check("the progress label has no attempts now", run(`fileJobProgressLabel({ unit: "step", current: 1, total: 4 }, 0.25)`), "25%");
check("the toast for a limit that can't be reached",
  run(`fileJobOutcomeToast({ state: "done", tool: "compress", result: { unreachable: true, label: "100 KB" } }).message`),
  "This file can't get under 100 KB. Nothing was saved.");

// ---- The panel, rendered into the stand-in ----
let current = null;
const body = new FakeNode("div");
let footer = [];
const panel = run("CompressPanel");
const render = () => { body.children = []; footer = []; panel.renderSetup(current); };
context.hooks = {
  state: () => current,
  hidden: () => false,
  body,
  rerender: () => render(),
  updatePrimary() {},
  segmented: () => new FakeNode("div"),
  destinationBlock: () => new FakeNode("div"),
  notesBlock: () => null,
  setupReady: () => panel.ready(current),
  footerButton: (label, fn, opts = {}) => { footer.push({ label, disabled: Boolean(opts.disabled) }); },
  start() {}, close() {}, openOutput() {},
};
run("CompressPanel.init(hooks)");
const event = (step, row) => listeners["lector:compressestimate"]({ detail: { session: 7, step, ...row } });
const done = (step, size, kind = "sharp", helps = true) => event(step, { state: "done", size, helps, kind });
const fresh = (mode = "quality", size = 2.7 * MB, prefs = {}) => {
  const info = { size, pages: 10, name: "sample-scanned.pdf",
    prefs: { compress_mode: mode, compress_limit: 200, compress_unit: "KB", ...prefs } };
  current = { phase: "setup", info, compress: panel.freshState(info), destination: { value: () => ({ folder: "" }) } };
  current.compress.session = 7;
  render();
  return current;
};
const compressOn = () => !footer.find((b) => b.label === "Compress").disabled;
const pills = () => byClass(body, "compress-pill");
const pillText = () => pills().map((p) => textOf(p));

{
  const s = fresh();
  check("the header is one line of facts", panel.description(s), "sample-scanned.pdf · 2.7 MB · 10 pages");
  const cards = byClass(body, "compress-level");
  check("Quality: three separate cards", cards.length, 3);
  check("each one the shared option row, with its hover (card-lift)",
    cards.every((c) => c.classList.contains("option-row") && c.classList.contains("card-lift")), true);
  check("in the shared option list", byClass(body, "tool-options").length === 1 && byClass(body, "compress-levels").length === 1, true);
  check("Estimating until a size is in", byClass(body, "compress-estimating").length, 3);
  check("Balanced is selected before the estimates are in", s.compress.level, "balanced");
  done(2, 2.69 * MB, "sharp", false);
  check("an early No change moves the selection off it", s.compress.level, "strong");
  done(4, 1.1 * MB, "good", true);
  done(0, 2.7 * MB, "sharp", false);
  check("then the default: Smallest file", s.compress.level, "strong");
  const [best, balanced, smallest] = byClass(body, "compress-level");
  check("No change: disabled", [best.getAttribute("aria-disabled"), balanced.getAttribute("aria-disabled"), smallest.getAttribute("aria-disabled")], ["true", "true", null]);
  check("No change: the file's size over \"No change\"", textOf(byClass(balanced, "compress-size")[0]), "2.7 MBNo change");
  check("No change: its tooltip", balanced.dataset.tooltip, "This file is already compressed to this level.");
  check("a card that helps: its size over its saving", textOf(byClass(smallest, "compress-size")[0]), "About 1.1 MB59% smaller");
  check("a disabled card ignores a click", (balanced.click(), s.compress.level), "strong");
  check("Compress is on", compressOn(), true);
  check("the quality options", panel.jobOptions(s), { mode: "quality", level: "strong", session: 7 });
}
{
  const s = fresh();
  s.compress.level = "light"; s.compress.picked = true;
  done(2, 10); done(4, 10); done(0, 2.6 * MB);
  check("a card the reader picked stays", s.compress.level, "light");
}
{
  const s = fresh();
  [0, 2, 4].forEach((step) => done(step, 2.7 * MB, "sharp", false));
  check("no level helps: Compress is off", compressOn(), false);
  check("and a hint says so", byClass(body, "compress-hint").length, 1);
}
{
  const s = fresh();
  done(2, 1.0 * MB);
  listeners["lector:compressestimate"]({ detail: { session: 99, step: 4, state: "done", size: 1, helps: false } });
  check("a result for another session is ignored", s.compress.steps[4] === undefined, true);
  event(2, { state: "stopped" });
  check("a stopped one goes back to Estimating", s.compress.steps[2] === undefined, true);
}

// Size limit. The 10-page scan's real ladder (bytes, best quality first).
const SCAN10 = [900983, 900961, 551793, 262308, 147254, 60038, 57710, 29255, 10739, 9389];
const KINDS10 = ["sharp", "sharp", "sharp", "sharp", "good", "good", "readable", "bw", "bw", "bw"];
const checkAll = (sizes = SCAN10, kinds = KINDS10) => [9, 0, 8, 7, 6, 5, 4, 3, 2, 1].forEach((step) => done(step, sizes[step], kinds[step]));
{
  fresh("limit", 250 * KB);
  check("pills at or above the file's size are hidden", pillText(), ["50 KB", "100 KB", "200 KB"]);
  fresh("limit", 200 * KB);
  check("a pill equal to the size is hidden too", pillText(), ["50 KB", "100 KB"]);
  fresh("limit", 40 * KB);
  check("no pill left: the file is already under 50 KB", [pills().length, textOf(byClass(body, "compress-hint")[0])],
    [0, "This file is already under 50 KB."]);
}
{
  const s = fresh("limit", 901188);
  check("while checking, every pill is muted and inert",
    pills().every((p) => p.classList.contains("is-checking") && p.getAttribute("aria-disabled") === "true"), true);
  check("and the card says it is checking", textOf(byClass(body, "compress-outcome")[0]), "Checking which sizes this file can reach…");
  check("with a shimmer bar", byClass(body, "compress-shimmer").length, 1);
  check("Compress is off while checking", compressOn(), false);
  done(9, 9389, "bw");
  check("the smallest step known: every pill it fits is enabled", pills().filter((p) => !p.getAttribute("aria-disabled")).length, 5);
  check("the remembered 200 KB is selected once reachable", s.compress.limit, { value: 200, unit: "KB" });
  check("but its card waits for the step above it", textOf(byClass(body, "compress-outcome")[0]), "Checking which sizes this file can reach…");
  checkAll();
  check("200 KB picks the best step under 194,000 bytes", textOf(byClass(body, "compress-outcome")[0]),
    "Good for reading on screenColour kept. Images slightly softer.About 147 KB");
  check("Compress is on", compressOn(), true);
  check("the limit options", panel.jobOptions(s), { mode: "limit", limit: 200, unit: "KB", session: 7 });
  pills()[0].click();
  check("50 KB: black and white", textOf(byClass(body, "compress-outcome")[0]),
    "Black and whiteScanned pages become black and white. Text stays readable.About 29 KB");
  check("its tile icon", byClass(body, "compress-outcome-tile")[0].dataset.icon, "compress_bw");
  pills()[3].click();
  check("300 KB: sharp", textOf(byClass(body, "compress-outcome")[0]), "SharpColour kept. Close to the original.About 262 KB");
}
{
  // A step that made scanned pages grey, or colour under 72 ppi: Readable.
  const sizes = [900000, 800000, 600000, 400000, 300000, 150000, 95000, 60000, 50000, 40000];
  const kinds = ["sharp", "sharp", "sharp", "sharp", "good", "good", "readable", "bw", "bw", "bw"];
  fresh("limit", 900000, { compress_limit: 100 });
  checkAll(sizes, kinds);
  check("100 KB: readable", textOf(byClass(body, "compress-outcome")[0]),
    "ReadableImages are soft. Check small text after saving.About 95 KB");
}
{
  // The 1,000-page text PDF: nothing reaches 50–500 KB.
  const s = fresh("limit", 628023);
  checkAll(Array(10).fill(497426), Array(10).fill("sharp"));
  check("pills below the floor are disabled", pills().every((p) => p.classList.contains("is-too-small") && p.getAttribute("aria-disabled") === "true"), true);
  check("with the floor in the tooltip", pills()[2].dataset.tooltip, "Too small for this file. The smallest it can go is about 497 KB.");
  check("the remembered limit, too small here, is not selected", s.compress.limit, null);
  check("Compress is off with nothing selected", compressOn(), false);
  pills()[4].click();
  check("a disabled pill ignores a click", s.compress.limit, null);
}
{
  const s = fresh("limit", 901188);
  checkAll();
  byClass(body, "compress-other-link")[0].click();
  check("Other size… opens its row, and the link says how to go back",
    [byClass(body, "compress-other").length, textOf(byClass(body, "compress-other-link")[0])], [1, "Use a preset"]);
  const type = (text) => { s.compress.otherText = text; render(); };
  type("5");
  check("below the floor: the field's error", byClass(body, "compress-limit-input")[0].classList.contains("is-error"), true);
  check("and why", textOf(byClass(body, "compress-field-error")[0]), "Too small. The smallest this file can go is about 9 KB.");
  check("Compress is off", compressOn(), false);
  type("120");
  check("a value it can reach shows the result card", textOf(byClass(body, "compress-outcome")[0]),
    "Good for reading on screenColour kept. Images slightly softer.About 60 KB");
  check("Compress is on", compressOn(), true);
  check("the typed limit is sent", panel.jobOptions(s), { mode: "limit", limit: 120, unit: "KB", session: 7 });
  check("no pill looks selected meanwhile", pills().some((p) => p.classList.contains("selected")), false);
  type("2"); s.compress.otherUnit = "MB"; render();
  check("at or above the file's size: already under", textOf(byClass(body, "compress-hint")[0]), "This file is already under 2 MB.");
  check("and Compress is off", compressOn(), false);
  let stopped = false;
  byClass(body, "compress-other")[0].listeners.keydown({ key: "Escape", preventDefault() {}, stopPropagation() { stopped = true; } });
  check("Esc closes the row, not the dialog", [byClass(body, "compress-other").length, stopped], [0, true]);
  check("the remembered pill is back", panel.jobOptions(s), { mode: "limit", limit: 200, unit: "KB", session: 7 });
}

// A big file: pills decided on estimates, with room to spare.
{
  const s = fresh("limit", 110 * MB, { compress_limit: 2, compress_unit: "MB" });
  check("before the estimates, every pill is checking",
    pills().every((p) => p.classList.contains("is-checking")), true);
  const est = (sizes) => Object.fromEntries(sizes.map((size, step) => [step, { size, kind: step >= 7 ? "bw" : step >= 5 ? "good" : "sharp" }]));
  // About a 110 MB scan's ladder: 1 MB's floor is at 103%, so it waits.
  const LADDER110 = [110e6, 110e6, 58e6, 28e6, 14.4e6, 5.08e6, 4.84e6, 2.28e6, 1.03e6, 1.03e6];
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 10, estimates: est(LADDER110) } });
  const states = () => pills().map((p) => [textOf(p), p.classList.contains("is-too-small") ? "too_small"
    : p.classList.contains("is-checking") ? "checking" : "reachable"]);
  check("estimates: 50–500 KB dimmed (130% or more), 1 MB checking (103%), 2 MB enabled (52%)", states(),
    [["50 KB", "too_small"], ["100 KB", "too_small"], ["200 KB", "too_small"], ["300 KB", "too_small"],
     ["500 KB", "too_small"], ["1 MB", "checking"], ["2 MB", "reachable"]]);
  check("the remembered 2 MB is selected", s.compress.limit, { value: 2, unit: "MB" });
  // Step 7 is estimated at 114% of 2 MB: too close to rule out, so 2 MB's
  // card waits for it to be made for real.
  check("a better step in doubt: the card is checking", byClass(body, "compress-outcome")[0].classList.contains("is-checking"), true);
  check("and Compress waits", compressOn(), false);
  done(7, 2.5e6, "bw");
  check("once it is really over, its card is like any other: the step and About its estimate", textOf(byClass(body, "compress-outcome")[0]),
    "Black and whiteScanned pages become black and white. Text stays readable.About 1.0 MB");
  check("Compress is on for it", compressOn(), true);
  check("the chosen limit is sent to Python",
    apiCalls.filter((c) => c[0] === "select_compress_limit").slice(-1)[0], ["select_compress_limit", 7, 2, "MB"]);
  check("a dimmed pill's tooltip gives the estimated floor",
    pills()[0].dataset.tooltip, "Too small for this file. The smallest it can go is about 1.0 MB.");
  // The real step 8 came out over the limit: the card moves to the next step down.
  done(8, 2.3e6, "bw");
  check("a real result over the limit moves the answer to the next step",
    context.compressDecision(s.compress.steps, s.compress.estimates, 2e6), { step: 9, settled: true });
  check("and the card with it", textOf(byClass(body, "compress-outcome")[0]),
    "Black and whiteScanned pages become black and white. Text stays readable.About 1.0 MB");
  done(9, 0.9e6, "bw");
  check("its real size once it is in", textOf(byClass(body, "compress-outcome")[0]),
    "Black and whiteScanned pages become black and white. Text stays readable.About 900 KB");
  check("1 MB is decided by the real floor now", states()[5], ["1 MB", "reachable"]);
}
{
  // Between the margins: Checking until a real step decides it.
  fresh("limit", 110 * MB, { compress_limit: 50 });
  const sizes = Object.fromEntries([...Array(10)].map((_, n) => [n, { size: n >= 8 ? 900_000 : 50e6, kind: "bw" }]));
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 3, estimates: sizes } });
  const one = pills().find((p) => textOf(p) === "1 MB");
  check("90% of 1 MB: checking", one.classList.contains("is-checking"), true);
  check("2 MB at 45%: enabled", pills().find((p) => textOf(p) === "2 MB").getAttribute("aria-disabled"), null);
  check("with a checking card while 1 MB is open", byClass(body, "compress-outcome")[0].classList.contains("is-checking"), true);
}

// The stale result race: what is older than what is shown is ignored.
{
  const s = fresh("limit", 901188);
  event(9, { state: "done", size: 9389, helps: true, kind: "bw", seq: 5 });
  event(9, { state: "working", seq: 4 });
  check("an older Working after a newer result is ignored", s.compress.steps[9].state, "done");
  event(9, { state: "stopped", seq: 3 });
  check("so is an older Stopped", s.compress.steps[9] && s.compress.steps[9].state, "done");
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 9, estimates: { 9: { size: 1, kind: "bw" } } } });
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 8, estimates: null } });
  check("older estimates are ignored", s.compress.estimates, { 9: { size: 1, kind: "bw" } });
  event(9, { state: "stopped", seq: 6 });
  check("a newer one applies", s.compress.steps[9], undefined);
}
// The snapshot asked for when the dialog opens comes back after newer events
// (run once the blocks above have finished with the shared dialog state).
setTimeout(() => {
  const s = fresh("limit", 901188);
  answers.get_compress_estimates = {
    steps: { 9: { state: "working", seq: 2 }, 0: { state: "done", size: 900983, helps: false, kind: "sharp", seq: 3 } },
    estimates: null, estimates_seq: 0, nospace: false, nospace_seq: 0,
  };
  const opening = panel.opened(s);
  event(9, { state: "done", size: 9389, helps: true, kind: "bw", seq: 4 });
  opening.then(() => {
    check("a stale snapshot doesn't bring back Checking", s.compress.steps[9].state, "done");
    check("what it has that is new still applies", s.compress.steps[0].state, "done");
    answers.get_compress_estimates = {};
    finish();
  });
}, 20);

// Not enough free disk space.
{
  const s = fresh("limit", 901188);
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 30, nospace: true } });
  check("Size limit: the result card says so", textOf(byClass(body, "compress-outcome")[0]),
    "Not enough free disk space to check sizes. Free up space or choose another Save in folder.");
  check("and Compress is off", compressOn(), false);
  s.compress.mode = "quality"; render();
  check("Quality: the same message in place of its estimates",
    [byClass(body, "compress-estimating").length, textOf(byClass(body, "compress-hint")[0])],
    [0, "Not enough free disk space to check sizes. Free up space or choose another Save in folder."]);
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 29, nospace: false } });
  check("an older all-clear doesn't hide it", s.compress.nospace, true);
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 31, nospace: false } });
  check("a newer all-clear does", s.compress.nospace, false);
}
{
  // "Not enough space" after the selected limit was settled on real steps:
  // its card and Compress stay.
  const s = fresh("limit", 901188, { compress_limit: 200, compress_unit: "KB" });
  [[9, 9389], [0, 900983], [3, 262308], [4, 147254]].forEach(([step, size], n) =>
    event(step, { state: "done", size, helps: true, kind: "good", seq: 50 + n }));
  check("200 KB settled on step 4", [s.compress.limit, compressOn()], [{ value: 200, unit: "KB" }, true]);
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 60, nospace: true } });
  check("a late 'not enough space' keeps a settled card", textOf(byClass(body, "compress-outcome")[0]).startsWith("Good"), true);
  check("and Compress", compressOn(), true);
  check("but only for a real step", context.compressLimitReady({}, { 4: { size: 1, kind: "good" }, 3: { size: 9e9 } }, 200000, true), false);
}
// Save in changed after "not enough space" (on its own, once the blocks
// above are done with the shared state and the bridge stand-in).
setTimeout(() => {
  const s = fresh("limit", 901188);
  listeners["lector:compressestimate"]({ detail: { session: 7, seq: 40, nospace: true } });
  s.compress.folder = "D:\\Full";
  apiCalls.length = 0;
  panel.destinationChanged(s);
  setTimeout(() => {
    check("a new Save in folder asks again", apiCalls.some((c) => c[0] === "start_compress_estimates"), true);
    // Save in changed *before* the "not enough space" for the old folder
    // came: that event itself asks again.
    const t = fresh("limit", 901188);
    t.compress.folder = "D:\\Full";
    apiCalls.length = 0;
    listeners["lector:compressestimate"]({ detail: { session: 7, seq: 70, nospace: true } });
    setTimeout(() => {
      check("a late 'not enough space' for a folder since left asks again",
        apiCalls.some((c) => c[0] === "start_compress_estimates"), true);
      finish();
    }, 0);
  }, 0);
}, 40);

// The Done state's note.
{
  const s = fresh("limit");
  const note = (r) => {
    body.children = [];
    s.outcome = { result: { path: "x.pdf", before: 900000, after: 190000, name: "x.pdf", folder_label: "Downloads", label: "200 KB", ...r } };
    s.compress.cleared = true;
    panel.renderResult(s);
    return byClass(body, "compress-hint").map(textOf);
  };
  check("limit, black and white: the note", note({ mode: "limit", kind: "bw" }),
    ["Pages are now black and white at a lower resolution. Open the file to check it's easy to read."]);
  check("limit, readable: the note", note({ mode: "limit", kind: "readable" }),
    ["Pages are now at a lower resolution. Open the file to check it's easy to read."]);
  check("limit, good or sharp: none", [note({ mode: "limit", kind: "good" }), note({ mode: "limit", kind: "sharp" })], [[], []]);
  check("within the limit", textOf(byClass(body, "compress-line")[0]), "That's within your 200 KB limit. Saved to Downloads as x.pdf.");
  check("quality: as before", note({ mode: "quality", lowered: true, black_and_white: false }),
    ["Pages are now at a lower resolution. Open the file to check it's easy to read."]);
  check("a drop into black and white says so", note({ mode: "limit", kind: "bw", dropped: "bw" }),
    ["To fit, Lector used black and white.",
     "Pages are now black and white at a lower resolution. Open the file to check it's easy to read."]);
  check("a drop in colour says the quality was lowered", note({ mode: "limit", kind: "good", dropped: "quality" }),
    ["To fit, Lector lowered the image quality."]);
  check("no drop, no line", note({ mode: "limit", kind: "good", dropped: null }), []);
}

// The background work couldn't start (an unreadable file): no endless Checking.
{
  const calls = [];
  const before = context.callApi;
  context.callApi = (method) => { calls.push(method); return Promise.resolve(method === "start_compress_estimates" ? { ok: false } : { ok: true }); };
  const s = fresh("limit", 901188);
  s.compress.session = null;
  panel.opened(s).then(() => {
    check("a failed start says so instead of Checking",
      [byClass(body, "compress-outcome").length, textOf(byClass(body, "compress-hint")[0] || new FakeNode("p"))],
      [0, "Couldn't check this file's sizes. Quality still works."]);
    // A dialog closed while its start was on the way doesn't stop the next one's work.
    calls.length = 0;
    context.callApi = (method) => { calls.push(method); return Promise.resolve({ ok: true, session: 8 }); };
    const old = fresh("limit", 901188);
    const pending = panel.opened(old);
    fresh("limit", 901188);  // closed, and another dialog opened
    return pending.then(() => {
      check("a late answer for a closed dialog sends no second stop", calls.filter((m) => m === "stop_compress_estimates").length, 0);
      context.callApi = before;
      finish();
    });
  });
}

let pendingChecks = 4; // the four async checks above, and the end of this script
function finish() { if (--pendingChecks === 0) console.log(JSON.stringify(results)); }
setTimeout(() => finish(), 0);
"""


def _decision_cases() -> tuple[list, list]:
    def keyed(sizes):
        return None if sizes is None else {str(k): v for k, v in sizes.items()}

    cases = [[keyed(real), keyed(est), limit] for real, est, limit in DECISION_CASES]
    want = []
    for real, est, limit in DECISION_CASES:
        step, settled = limit_plan.decide(real, est, limit)
        want.append({"step": step, "settled": settled})
    return cases, want


def _choice_cases() -> tuple[list, list]:
    cases = [[{str(k): v for k, v in sizes.items()}, limit] for sizes, limit in CHOICE_CASES]
    want = []
    for sizes, limit in CHOICE_CASES:
        step, settled = compress_plan.choose(sizes, limit)
        want.append({"step": step, "settled": settled})
    return cases, want


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class CompressPanelBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases, want = _choice_cases()
        decisions, decided = _decision_cases()
        env = {
            **os.environ,
            "CHOICE_CASES": json.dumps(cases),
            "CHOICE_WANT": json.dumps(want),
            "DECISION_CASES": json.dumps(decisions),
            "DECISION_WANT": json.dumps(decided),
            "LADDER_STEPS": str(len(compress_plan.LADDER)),
        }
        out = subprocess.run(
            ["node", "-e", HARNESS, *map(str, SCRIPTS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=env,
        )
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.results = json.loads(out.stdout.strip().splitlines()[-1])

    def test_every_rule_holds(self):
        self.assertGreater(len(self.results), 50)
        for name, outcome in self.results.items():
            with self.subTest(name):
                self.assertIs(outcome, True, outcome)


class CompressStylesTests(unittest.TestCase):
    """The cards are the shared option row, so they hover as every other
    option list does; a "No change" card is dashed and doesn't."""

    CSS = (FRONTEND / "shared" / "file-tools.css").read_text(encoding="utf-8")

    def test_no_change_cards_are_dashed_and_dont_lift(self):
        rule = re.search(r'\.tool-options \.compress-level\[aria-disabled="true"\][^{]*\{([^}]*)\}', self.CSS)
        self.assertIsNotNone(rule)
        for declaration in ("border-style: dashed", "transform: none", "box-shadow: none", "cursor: default"):
            self.assertIn(declaration, rule.group(1))

    def test_the_pill_row_is_seven_equal_columns_32px_tall(self):
        self.assertRegex(self.CSS, r"\.compress-pills \{[^}]*grid-template-columns: repeat\(7, minmax\(0, 1fr\)\)")
        self.assertRegex(self.CSS, r"\.compress-pill \{[^}]*height: 32px")

    def test_disabled_pills_are_dashed_or_muted(self):
        self.assertRegex(self.CSS, r"\.compress-pill\.is-too-small \{[^}]*border-style: dashed")
        self.assertRegex(self.CSS, r"\.compress-pill\.is-checking \{[^}]*color: var\(--text-muted\)")


if __name__ == "__main__":
    unittest.main()
