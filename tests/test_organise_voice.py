"""Voice in Organise pages (Milestones F2.3 and F2.10): "done" applies the
page changes and goes back to reading, "cancel" is the Cancel button (asking
first when there are changes), and in "Discard page changes?" the file
tools' grammar answers: "confirm" discards, "cancel" keeps editing. "select
all" / "select none", "rotate left" / "rotate right" and "delete pages" act
on the selection as the action bar does ("delete pages" asks first), "undo"
and "redo" are the draft's own; "organise pages" opens it from the reader,
and "combine files" and "split" open those tools on Home. All of it is in
"What can I say?", Organise's section leading while Organise is open.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector.features.voice import reference, router  # noqa: E402

PAGES = ROOT / "frontend" / "pages"
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
ORGANISE_JS = (PAGES / "organise.js").read_text(encoding="utf-8")
DANGER_JS = (PAGES / "danger-dialog.js").read_text(encoding="utf-8")
INSERT_JS = (PAGES / "organise-insert.js").read_text(encoding="utf-8")
EXTRACT_JS = (PAGES / "organise-extract.js").read_text(encoding="utf-8")
HOME_JS = (PAGES / "home.js").read_text(encoding="utf-8")


def _intent(context: str, text: str) -> str | None:
    command = router.resolve(context, text)["command"]
    return command and command["intent"]


class OrganiseGrammarTests(unittest.TestCase):
    def test_done_and_cancel_in_organise(self):
        for phrase in ("done", "exit organise", "back to reading"):
            with self.subTest(phrase=phrase):
                self.assertEqual(_intent(router.ORGANISE, phrase), router.ORGANISE_DONE)
        self.assertEqual(_intent(router.ORGANISE, "cancel"), router.CANCEL)

    def test_the_globals_still_reach_organise(self):
        for phrase, intent in (("undo", router.UNDO), ("redo", router.REDO), ("what can i say", router.HELP),
                               ("go home", router.GO_HOME), ("quit", router.CLOSE_APP)):
            with self.subTest(phrase=phrase):
                self.assertEqual(_intent(router.ORGANISE, phrase), intent)

    def test_the_danger_questions_hear_confirm_and_cancel_only(self):
        self.assertIn('createVoiceScope("file_tool")', DANGER_JS)
        self.assertEqual(_intent(router.FILE_TOOL, "confirm"), router.TOOL_CONFIRM)
        self.assertEqual(_intent(router.FILE_TOOL, "cancel"), router.CANCEL)
        self.assertIsNone(_intent(router.FILE_TOOL, "done"))
        self.assertIsNone(_intent(router.FILE_TOOL, "quit"))  # modal: no globals

    def test_both_are_listed_in_what_can_i_say(self):
        section = next(s for s in reference.sections(router.READING) if s["id"] == "organising")
        phrases = {p for command in section["commands"] for p in command["phrases"]}
        self.assertTrue({"done", "cancel"} <= phrases)

    def test_selecting_turning_and_deleting_in_organise(self):
        for phrase, intent in (
            ("select all", router.SELECT_ALL), ("select none", router.SELECT_NONE),
            ("rotate left", router.ROTATE_LEFT), ("rotate right", router.ROTATE_RIGHT),
            ("delete pages", router.DELETE_PAGES),
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(_intent(router.ORGANISE, phrase), intent)
                # Organise's own: the reader doesn't hear them.
                self.assertNotEqual(_intent(router.READING, phrase), intent)
        # Opposites stay apart: a mishearing never turns one into the other.
        self.assertNotEqual(_intent(router.ORGANISE, "rotate lift"), router.ROTATE_RIGHT)

    def test_organise_pages_opens_it_from_the_reader(self):
        for phrase in ("organise pages", "organize pages", "rearrange pages"):
            with self.subTest(phrase=phrase):
                self.assertEqual(_intent(router.READING, phrase), "ORGANISE_PAGES")

    def test_combine_files_and_split_on_home(self):
        self.assertEqual(_intent(router.HOME, "combine files"), router.COMBINE_FILES)
        self.assertEqual(_intent(router.HOME, "split"), router.SPLIT_PDF)
        self.assertIsNone(_intent(router.READING, "combine files"))

    def test_every_new_phrase_is_in_what_can_i_say(self):
        listed = {p.lower() for command in reference.commands(router.READING) for p in command["phrases"]}
        for phrase in ("select all", "select none", "rotate left", "rotate right", "delete pages",
                       "organise pages", "organize pages", "rearrange pages", "combine files", "split"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, listed)
        organising = next(s for s in reference.sections(router.READING) if s["id"] == "organising")
        self.assertEqual(
            [c["phrases"][0] for c in organising["commands"]],
            ["done", "cancel", "select all", "select none", "rotate left", "rotate right",
             "delete pages", "undo", "redo"],
        )
        files = next(s for s in reference.sections(router.HOME) if s["id"] == "files")
        self.assertTrue(files["available"])  # it holds Home's two openers


class OrganiseVoiceWiringTests(unittest.TestCase):
    """The page side: which function each intent reaches."""

    def test_done_and_cancel_reach_the_two_buttons_functions(self):
        block = re.search(r"const ORGANISE_VOICE_ACTIONS = \{(.*?)\n\};", READING_JS, re.S).group(1)
        self.assertRegex(block, r"ORGANISE_DONE: \(\) => finishOrganise\(\)")
        self.assertRegex(block, r"CANCEL: \(\) => cancelOrganise\(\)")
        button = re.search(r'organiseCancelBtn\.addEventListener\("click", \(\) => (\w+)\(\)\)', ORGANISE_JS)
        self.assertEqual(button.group(1), "cancelOrganise")

    def test_the_danger_questions_take_spoken_answers_before_organise_does(self):
        # Discard and Delete: "confirm" is the danger button, "cancel" the safe one.
        handler = READING_JS[READING_JS.index('window.addEventListener("lector:command"'):]
        questions = handler.index(
            "[OrganiseDiscardDialog, OrganiseDeleteDialog, OrganiseInsertDialog, OrganiseExtractDialog]"
        )
        self.assertLess(questions, handler.index("ORGANISE_VOICE_ACTIONS"))
        self.assertRegex(DANGER_JS, r'command\.intent === "TOOL_CONFIRM"\) close\(true\)')
        self.assertRegex(DANGER_JS, r'command\.intent === "CANCEL"\) close\(false\)')

    def test_extract_hears_confirm_and_cancel(self):
        # Extract…: "confirm" is Extract; "cancel" stops a running job, else closes.
        self.assertIn('createVoiceScope("file_tool")', EXTRACT_JS)
        handler = EXTRACT_JS[EXTRACT_JS.index("function handleCommand"):]
        self.assertRegex(handler, r'"TOOL_CONFIRM"\) \{\s*if \(primaryBtn && !primaryBtn\.disabled\) primaryBtn\.click\(\)')
        self.assertRegex(handler, r'"CANCEL"\) \{\s*if \(s\.phase === "working"\) cancelJob\(\)')

    def test_insert_hears_confirm_cancel_and_a_place_number(self):
        # Insert…: "confirm" inserts, "cancel" cancels, "two" picks the second place.
        self.assertIn('createVoiceScope("file_tool")', INSERT_JS)
        self.assertRegex(INSERT_JS, r'command\.intent === "TOOL_CONFIRM"\) confirm\(\)')
        self.assertRegex(INSERT_JS, r'command\.intent === "CANCEL"\) close\(null\)')
        self.assertIn('command.intent === "DIALOG_PICK"', INSERT_JS)
        self.assertEqual(_intent(router.FILE_TOOL, "two"), router.DIALOG_PICK)
        # Its Open dialog is routed before anything else in Organise.
        handler = READING_JS[READING_JS.index('window.addEventListener("lector:command"'):]
        self.assertLess(handler.index("openFileDialog.isOpen()"), handler.index("ORGANISE_VOICE_ACTIONS"))

    def test_organise_claims_its_context_and_gives_it_back(self):
        self.assertIn('callApi("set_voice_context", "organise")', ORGANISE_JS)
        self.assertIn('callApi("set_voice_context", "reading")', ORGANISE_JS)
        self.assertIn(router.ORGANISE, router.CONTEXTS)

    def test_each_organise_intent_reaches_the_action_bars_function(self):
        block = re.search(r"const ORGANISE_VOICE_ACTIONS = \{(.*?)\n\};", READING_JS, re.S).group(1)
        self.assertIn("SELECT_ALL: () => organiseVoiceSelect(true)", block)
        self.assertIn("SELECT_NONE: () => organiseVoiceSelect(false)", block)
        self.assertIn("ROTATE_LEFT: () => organiseVoiceOnSelection(() => organiseRotate(-90))", block)
        self.assertIn("ROTATE_RIGHT: () => organiseVoiceOnSelection(() => organiseRotate(90))", block)
        self.assertIn("DELETE_PAGES: () => organiseVoiceOnSelection(() => organiseDelete())", block)
        # The same functions the buttons call.
        self.assertIn('getElementById("organiseRotateLeftBtn"), () => organiseRotate(-90)', ORGANISE_JS)
        self.assertIn('getElementById("organiseDeleteBtn"), () => organiseDelete()', ORGANISE_JS)

    def test_the_reader_and_home_open_the_tools(self):
        reader = re.search(r"const VOICE_ACTIONS = \{(.*?)\n\};", READING_JS, re.S).group(1)
        self.assertIn("ORGANISE_PAGES: () => startOrganise()", reader)
        self.assertIn('COMBINE_FILES: () => openToolByVoice("combine")', HOME_JS)
        self.assertIn('SPLIT_PDF: () => openToolByVoice("split")', HOME_JS)
        body = HOME_JS[HOME_JS.index("async function openToolByVoice"):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index('await setHomeSection("tools")'), body.index("openToolFromPage(tool)"))

    def test_what_can_i_say_leads_with_organising_while_it_is_open(self):
        self.assertIn('focusSection: () => (isOrganiseOpen() ? "organising" : null)', READING_JS)

    def test_closing_what_can_i_say_with_cancel_never_reaches_organise(self):
        # Found in the running app: "got it" (CANCEL) closed the list, then the
        # reader's handler pressed Organise's Cancel ("Discard page changes?").
        # The list stops the event, which only works if its listener comes first.
        reference_js = (PAGES / "command-reference.js").read_text(encoding="utf-8")
        listener = reference_js[reference_js.index('window.addEventListener("lector:command"'):]
        self.assertLess(listener.index("if (referenceDialog.hidden) return;"),
                        listener.index("ev.stopImmediatePropagation();"))
        for html, own in (("pages/reading.html", "reading.js"), ("index.html", "pages/home.js"),
                          ("pages/settings.html", "settings.js")):
            with self.subTest(page=html):
                text = (ROOT / "frontend" / html).read_text(encoding="utf-8")
                self.assertLess(text.index("command-reference.js\"></script>"), text.index(f'src="{own}"'))


VOICE_HELPERS_HARNESS = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const grab = (name) => {
  const start = source.indexOf(`function ${name}(`);
  return source.slice(start, source.indexOf("\n}\n", start) + 3);
};
const toasts = [], ran = [], selections = [];
let selected = [];
let organiseOpen = true;
const ORGANISE_NEEDS_SELECTION = "Select pages first";
const showToast = (text) => toasts.push(text);
const organiseGrid = {
  selectedPositions: () => selected,
  selectAll: () => selections.push("all"),
  selectNone: () => selections.push("none"),
};
// The two functions' own source from the repo's organise.js, run against the
// stubs above; nothing from outside the repo is evaluated.
eval(grab("organiseVoiceOnSelection") + grab("organiseVoiceSelect"));
organiseVoiceOnSelection(() => ran.push("rotate"));
selected = [2, 3];
organiseVoiceOnSelection(() => ran.push("rotate"));
organiseVoiceSelect(true);
organiseVoiceSelect(false);
organiseOpen = false;
organiseVoiceOnSelection(() => ran.push("closed"));
organiseVoiceSelect(true);
process.stdout.write(JSON.stringify({ toasts, ran, selections }));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OrganiseVoiceHelperTests(unittest.TestCase):
    def test_nothing_selected_says_why_and_select_maps_to_the_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(VOICE_HELPERS_HARNESS, encoding="utf-8")
            result = subprocess.run(["node", str(harness), str(PAGES / "organise.js")],
                                    capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["toasts"], ["Select pages first"])
        self.assertEqual(out["ran"], ["rotate"])  # once, with a selection; nothing once closed
        self.assertEqual(out["selections"], ["all", "none"])


if __name__ == "__main__":
    unittest.main()
