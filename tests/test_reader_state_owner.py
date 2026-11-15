"""The reader's state has one owner (`frontend/pages/reading.js`'s `state`) and
one way in (`adoptState`).

The bug this holds shut: choosing a highlight colour stored it, but the reply
(the whole reader state) was used only by highlight-options.js, so
reading.js's `state` kept the old colour, and `updateChrome()`, which redraws
the colour pill on every page boundary while scrolling, put the old colour
back on the pill and the card while new highlights went on in the new one.

What is held here:

* **Python's side.** Every `Api` method that answers with `_state()` is
  listed, so a new one has to be added here (and adopted in the frontend). The
  colour chosen is in every state that follows: page turns and the strip's
  scroll in both layouts, zoom, a highlight (which is drawn in it), Save
  (overwrite), and the PDF closed and opened again.
* **The frontend's side.** Every call to one of those methods hands its reply
  to `adoptState` (directly, or through a function that does), apart from the
  two that are not the reader's: Settings' own colour picker, and opening a
  PDF, after which the reader always reloads. Nothing outside `adoptState`
  replaces `state`. The card's swatch and the voice "highlight color <colour>"
  share the one setter that adopts.
* **`adoptState` itself** (under Node): it takes a state, keeps the tracked
  page when asked to, and ignores anything that isn't a state.

The running app is checked the same way by hand: choose a colour, scroll and
turn pages in Book and Strip, and the pill, the card and the next highlight
all keep it.
"""
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.shared import theme  # noqa: E402

API_PY = ROOT / "src" / "lector" / "api.py"
FRONTEND = ROOT / "frontend"
READING_JS = FRONTEND / "pages" / "reading.js"
NODE = shutil.which("node")

# Every Api method whose reply is (or carries, for perform_save) the reader's
# whole state. A new one fails test_the_list_matches_api_py until it is added
# here and its reply adopted in the frontend.
STATE_METHODS = {
    "open_pdf", "set_layout_mode", "get_state", "next_page", "prev_page", "goto_page",
    "zoom_in", "zoom_out", "set_zoom", "highlight_chars", "add_ink_stroke",
    "set_highlight_color", "recolor_highlight", "set_highlight_appearance", "remove_highlight",
    "undo_highlight", "redo_highlight", "organise_cancel", "organise_done",
    "highlight_by_voice", "perform_save",
}

# Calls whose reply is deliberately not adopted, and why.
NOT_THE_READERS = {
    # Settings has no reader state; the reader reads get_state when it loads.
    ("pages/settings.js", "set_highlight_color"),
    # Opening a PDF: Home and Settings have no reader state, and the reader
    # reloads onto the new document whenever one opens.
    ("pages/open-pdf.js", "open_pdf"),
}

# Functions that adopt the state they are handed, so a reply passed straight
# to one of them counts as adopted.
ADOPTING_HANDOFFS = ("adoptState(", "finishSave(", "highlightHooks.afterChange(")


def _methods_returning_state() -> set[str]:
    """Api methods whose body returns `self._state()`, a dict built on it, or
    `_after_history_step` (which returns it)."""
    tree = ast.parse(API_PY.read_text(encoding="utf-8"))
    api_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    found = set()
    for fn in api_class.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_"):
            continue
        source = ast.unparse(fn)
        if "self._state()" in source or "self._after_history_step(" in source:
            found.add(fn.name)
    return found


def _enclosing_function(source: str, at: int) -> str:
    """The top-level function around offset `at`: from the last line before it
    that starts one at column 0 to the "}" that closes it."""
    starts = [source.rfind(marker, 0, at) for marker in ("\nfunction ", "\nasync function ")]
    start = max(starts)
    end = source.find("\n}", at)
    return source[max(start, 0): end if end != -1 else len(source)]


def _state_calls():
    """(file, method, offset, source) for every callApi of a state method;
    reading.js's undo/redo passes the name in a variable, `method`."""
    pattern = re.compile(r'callApi\(\s*(?:"(\w+)"|(method)\))')
    for path in sorted(FRONTEND.rglob("*.js")):
        rel = path.relative_to(FRONTEND).as_posix()
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            name = match.group(1) or "undo_highlight"
            if name in STATE_METHODS:
                yield rel, name, match.start(), source


def _hex_rgb(hex_colour: str) -> tuple:
    value = hex_colour.lstrip("#")
    return tuple(round(int(value[i: i + 2], 16) / 255, 2) for i in (0, 2, 4))


class PythonSideTests(unittest.TestCase):
    def test_the_list_matches_api_py(self):
        self.assertEqual(_methods_returning_state(), STATE_METHODS)


class FrontendAdoptsEveryStateTests(unittest.TestCase):
    def test_every_state_reply_reaches_adopt_state(self):
        seen = set()
        for rel, name, at, source in _state_calls():
            seen.add((rel, name))
            if (rel, name) in NOT_THE_READERS:
                continue
            with self.subTest(file=rel, method=name, line=source.count("\n", 0, at) + 1):
                body = _enclosing_function(source, at)
                self.assertTrue(any(h in body for h in ADOPTING_HANDOFFS), body[:400])
        # The scan really found the calls (a renamed callApi would pass empty).
        for expected in [("pages/highlight-options.js", "set_highlight_color"),
                         ("pages/reading.js", "goto_page"), ("pages/organise.js", "organise_done"),
                         ("pages/organise.js", "organise_cancel"), ("pages/reading.js", "perform_save"),
                         ("pages/highlight-colors.js", "set_highlight_appearance"),
                         ("pages/reading.js", "undo_highlight")]:
            self.assertIn(expected, seen)

    def test_the_handoffs_themselves_adopt(self):
        reading = READING_JS.read_text(encoding="utf-8")
        save = reading[reading.index("function finishSave("):]
        self.assertIn("adoptState(outcome.state)", save[: save.index("\n}")])
        hook = reading[reading.index("afterChange: async (next) => {"):]
        self.assertIn("adoptState(next)", hook[: hook.index("},")])

    def test_nothing_else_replaces_the_reader_s_state(self):
        reading = READING_JS.read_text(encoding="utf-8")
        assignments = re.findall(r"^\s*state = (.+)$", reading, flags=re.M)
        # adoptState's own, and the strip's scroll moving the tracked page
        # (a new object, never an edit of an adopted one).
        self.assertEqual(len(assignments), 2, assignments)
        self.assertIn("{ ...state, page_index: index };", assignments)
        self.assertNotRegex(reading, r"\bstate\.\w+\s*=[^=]")

    def test_update_chrome_draws_the_colour_from_the_state(self):
        reading = READING_JS.read_text(encoding="utf-8")
        chrome = reading[reading.index("function updateChrome()"):]
        self.assertIn("showActiveHighlightColor(state.highlight_color)", chrome[: chrome.index("\n}")])

    def test_the_card_and_the_voice_command_share_the_setter_that_adopts(self):
        options = (FRONTEND / "pages" / "highlight-options.js").read_text(encoding="utf-8")
        setter = options[options.index("async function setActiveHighlightColor("):]
        self.assertIn('adoptState(await callApi("set_highlight_color", name), { keepPage: true })',
                      setter[: setter.index("\n}")])
        self.assertIn("buildSwatches(highlightOptionsSwatches, setActiveHighlightColor)", options)
        reading = READING_JS.read_text(encoding="utf-8")
        self.assertIn("await setActiveHighlightColor(command.color)", reading)


ADOPT_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8").split(String.fromCharCode(13, 10)).join(String.fromCharCode(10));
const start = source.indexOf("function adoptState(");
const end = source.indexOf("\n}\n", start) + 2;
const context = vm.createContext({});
vm.runInContext("let state = { is_open: false };\n" + source.slice(start, end) +
  "\nthis.adopt = adoptState; this.get = () => state; this.set = (s) => { state = s; };", context);
const out = {};
const first = { is_open: true, page_index: 3, highlight_color: "green" };
out.adopted = context.adopt(first) === first && context.get() === first;
context.set({ is_open: true, page_index: 9, highlight_color: "green" });
const reply = { is_open: true, page_index: 3, highlight_color: "yellow", zoom_pct: 125 };
context.adopt(reply, { keepPage: true });
out.keptPage = context.get();
out.replyUntouched = reply.page_index === 3;
context.adopt(reply);
out.tookPage = context.get().page_index;
for (const junk of [null, undefined, { error: "Couldn't" }, { ok: true }, "text"]) context.adopt(junk);
out.afterJunk = context.get() === reply;
console.log(JSON.stringify(out));
"""


@unittest.skipIf(NODE is None, "Node is not installed")
class AdoptStateBehaviourTests(unittest.TestCase):
    def test_adopt_state_takes_states_keeps_the_page_when_asked_and_ignores_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "adopt.js"
            harness.write_text(ADOPT_HARNESS, encoding="utf-8")
            result = subprocess.run([NODE, str(harness), str(READING_JS)], capture_output=True,
                                    text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertTrue(out["adopted"])
        self.assertEqual(out["keptPage"], {"is_open": True, "page_index": 9,
                                           "highlight_color": "yellow", "zoom_pct": 125})
        self.assertTrue(out["replyUntouched"])
        self.assertEqual(out["tookPage"], 3)
        self.assertTrue(out["afterJunk"])


class ChosenColourStaysTests(unittest.TestCase):
    """The colour chosen is in every state the reader is handed afterwards."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-state-owner-")
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(store, "_settings_path", lambda: Path(self.dir.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pdf = str(Path(self.dir.name) / "four pages.pdf")
        doc = pymupdf.open()
        for n in range(4):
            doc.new_page().insert_text((72, 100), f"Page {n + 1} has some words to highlight here.")
        doc.save(self.pdf)
        doc.close()
        # The last colour used was green.
        store.set_highlight_color("green")
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)  # Windows will not delete an open file
        self.assertEqual(self.api.open_pdf(self.pdf)["highlight_color"], "green")

    def _stroke_on(self, page_index: int) -> tuple:
        doc = pymupdf.open(self.pdf)
        try:
            annot = next(doc[page_index].annots())
            return tuple(round(c, 2) for c in annot.colors["stroke"])
        finally:
            doc.close()

    def test_every_following_state_carries_the_chosen_colour(self):
        self.assertEqual(self.api.set_highlight_color("yellow")["highlight_color"], "yellow")
        replies = {
            "next_page (Book)": self.api.next_page(),
            "Strip": self.api.set_layout_mode(store.STRIP),
            "goto_page (the strip's scroll)": self.api.goto_page(2),
            "prev_page (Strip)": self.api.prev_page(),
            "Book": self.api.set_layout_mode(store.BOOK),
            "zoom_in": self.api.zoom_in(),
            "set_zoom": self.api.set_zoom(1.0),
            "highlight_chars": self.api.highlight_chars(1, 0, 6),
            "get_state": self.api.get_state(),
        }
        for what, state in replies.items():
            with self.subTest(what):
                self.assertEqual(state["highlight_color"], "yellow")

    def test_the_next_highlight_is_drawn_in_it_and_save_and_reopen_keep_it(self):
        self.api.set_highlight_color("yellow")
        self.api.next_page()
        self.api.highlight_chars(1, 0, 6)
        saved = self.api.perform_save(store.OVERWRITE)
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["state"]["highlight_color"], "yellow")
        # On the page in yellow, not the green the session started with.
        self.assertEqual(self._stroke_on(1), _hex_rgb(theme.shared_token("highlight-yellow")))

        # Closed and opened again: a new session reads it back from settings.
        self.api._doc.close()
        reopened = api_module.Api()
        self.addCleanup(reopened.shutdown_voice)
        self.addCleanup(reopened._doc.close)
        self.assertEqual(reopened.open_pdf(self.pdf)["highlight_color"], "yellow")
        self.assertEqual(reopened.next_page()["highlight_color"], "yellow")


if __name__ == "__main__":
    unittest.main()
