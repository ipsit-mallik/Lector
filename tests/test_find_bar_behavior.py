"""Behaviour of frontend/pages/find-bar.js: debouncing, dropping stale and
out-of-order search replies, wrapping round from the page being read, the
status line, and moving between matches.

Run under Node with a small stand-in for the DOM (the module looks its
elements up by id when loaded) and a `callApi` whose replies the test
releases one by one, in whatever order it likes — which is how a reply can be
made to arrive after a newer search has started. Skipped when Node is not
installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "find-bar.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function fakeElement(id) {
  const listeners = {};
  const classes = new Set();
  return {
    id, value: "", textContent: "", disabled: false, attrs: {}, children: [], focused: 0,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      contains: (c) => classes.has(c),
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatch(type, ev = {}) {
      const event = { target: this, preventDefault() {}, stopPropagation() {}, ...ev };
      (listeners[type] || []).forEach((fn) => fn(event));
    },
    focus() { this.focused++; },
    select() {},
    replaceChildren(...kids) { this.children = kids; },
    appendChild(kid) { this.children.push(kid); },
    getBoundingClientRect: () => ({ left: 0, top: 0, right: 600, bottom: 800, width: 600, height: 800 }),
    style: {},
  };
}

const elements = {};
const document = {
  getElementById: (id) => (elements[id] = elements[id] || fakeElement(id)),
  querySelector: () => null,
  addEventListener() {},
  createElement: () => fakeElement("mark"),
  createDocumentFragment: () => fakeElement("fragment"),
};

// Every bridge call waits until the test releases it.
const calls = [];
function callApi(name, query, start, end) {
  return new Promise((resolve) => calls.push({ name, query, start, end, resolve }));
}
const slice = (pages, next_page = null, text_pages = 1) => ({ pages, next_page, text_pages });
const page = (page_index, hits) => ({ page_index, width: 600, height: 800, hits: Array.from({ length: hits }, () => [[10, 10, 20, 20]]) });

const scrolled = [];
const context = vm.createContext({
  document,
  window: { addEventListener() {} },
  setTimeout, clearTimeout, console,
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  callApi,
  scrollElementTo: (el, top) => scrolled.push(top),
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext("this.api = { configureFindBar, openFindBar, closeFindBar, isFindBarOpen, FIND_DEBOUNCE_MS };", context);
const api = context.api;

let readingPage = 0;
let focusReturned = 0;
api.configureFindBar({
  pageElement: () => document.getElementById("page"),
  showPage: async () => {},
  scroller: () => Object.assign(document.getElementById("scroller"), { scrollTop: 0, clientHeight: 800, clientWidth: 600 }),
  currentPage: () => readingPage,
  returnFocus: () => { focusReturned++; },
});

const input = document.getElementById("findInput");
const bar = document.getElementById("findBar");
const status = document.getElementById("findStatus");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const flush = () => sleep(0);
const type = async (text) => { input.value = text; input.dispatch("input"); await sleep(api.FIND_DEBOUNCE_MS + 20); };
const enter = (shiftKey = false) => bar.dispatch("keydown", { key: "Enter", shiftKey, target: input });
const reset = () => { api.closeFindBar(); calls.length = 0; input.value = ""; readingPage = 0; api.openFindBar(); };

const results = {};
async function check(name, fn) {
  reset();
  try { results[name] = (await fn()) === true; } catch (err) { results[name] = `threw: ${err.stack}`; }
}

(async () => {
  await check("typing quickly is one search, after the pause", async () => {
    input.value = "f"; input.dispatch("input");
    input.value = "fo"; input.dispatch("input");
    input.value = "fox"; input.dispatch("input");
    await sleep(api.FIND_DEBOUNCE_MS - 100);
    const early = calls.length;
    await sleep(150);
    return early === 0 && calls.length === 1 && calls[0].query === "fox";
  });

  await check("searches from the page being read, then wraps round to it", async () => {
    readingPage = 3;
    await type("fox");
    const first = calls.shift();
    first.resolve(slice([page(4, 1)]));
    await flush();
    const second = calls.shift();
    second.resolve(slice([page(1, 1)]));
    await flush();
    return first.start === 3 && first.end === null && second.start === 0 && second.end === 3
      && status.textContent === "2 of 2";
  });

  await check("keeps asking for slices until the backend says it is done", async () => {
    await type("fox");
    calls.shift().resolve(slice([page(0, 1)], 40));
    await flush();
    const next = calls.shift();
    const midway = status.textContent;
    next.resolve(slice([page(50, 2)]));
    await flush();
    return next.start === 40 && midway === "1 of 1…" && status.textContent === "1 of 3";
  });

  await check("a reply for an older query is dropped, whenever it arrives", async () => {
    await type("fo");
    const stale = calls.shift();
    await type("fox");
    const fresh = calls.shift();
    fresh.resolve(slice([page(0, 1)]));
    await flush();
    stale.resolve(slice([page(0, 5)], 10));
    await flush();
    // The stale one is not counted, and asks for no further slice.
    return status.textContent === "1 of 1" && calls.length === 0;
  });

  await check("closing drops what is in flight and returns focus to the reader", async () => {
    await type("fox");
    const inFlight = calls.shift();
    const before = focusReturned;
    bar.dispatch("keydown", { key: "Escape", target: input });
    inFlight.resolve(slice([page(0, 1)], 5));
    await flush();
    return !api.isFindBarOpen() && focusReturned === before + 1 && calls.length === 0
      && document.getElementById("findLayer").children.length === 0;
  });

  await check("says it is searching before anything is found", async () => {
    await type("fox");
    return status.textContent === "Searching…" && bar.classList.contains("is-searching");
  });

  await check("a PDF with no text layer says so instead of 'no results'", async () => {
    await type("fox");
    calls.shift().resolve(slice([], null, 0));
    await flush();
    return status.textContent === "No searchable text in this PDF";
  });

  await check("a PDF with text but no match says no results", async () => {
    await type("zebra");
    calls.shift().resolve(slice([], null, 3));
    await flush();
    return status.textContent === "No results" && document.getElementById("findNextBtn").disabled;
  });

  await check("Enter steps forward and wraps; Shift+Enter steps back", async () => {
    await type("fox");
    calls.shift().resolve(slice([page(0, 3)]));
    await flush();
    const seen = [status.textContent];
    enter(); seen.push(status.textContent);
    enter(); seen.push(status.textContent);
    enter(); seen.push(status.textContent);
    enter(true); seen.push(status.textContent);
    return seen.join("|") === "1 of 3|2 of 3|3 of 3|1 of 3|3 of 3";
  });

  await check("Enter before the pause searches at once instead of stepping", async () => {
    input.value = "fox"; input.dispatch("input");
    enter();
    return calls.length === 1 && calls[0].query === "fox";
  });

  await check("the first match is the next one from the page being read", async () => {
    readingPage = 2;
    await type("fox");
    calls.shift().resolve(slice([page(5, 1)]));
    await flush();
    calls.shift().resolve(slice([page(0, 1)]));
    await flush();
    // Page 5 is the reader's next match, and page 0's arrived later, ahead of it.
    return status.textContent === "2 of 2" && scrolled.length > 0;
  });

  await check("reopening runs the last query again", async () => {
    await type("fox");
    calls.shift().resolve(slice([page(0, 1)]));
    await flush();
    api.closeFindBar();
    calls.length = 0;
    api.openFindBar();
    return calls.length === 1 && calls[0].query === "fox";
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class FindBarBehaviourTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
        )
        cls.results = json.loads(out.stdout)

    def test_every_behaviour(self):
        self.assertTrue(self.results)
        for name, ok in self.results.items():
            with self.subTest(name):
                self.assertIs(ok, True, ok)


if __name__ == "__main__":
    unittest.main()
