"""Voice in Organise pages, as far as Milestone F2.3 takes it: "done" applies
the page changes and goes back to reading, "cancel" is the Cancel button
(asking first when there are changes), and in "Discard page changes?" the
file tools' grammar answers: "confirm" discards, "cancel" keeps editing.

The rest of Organise's commands (select, rotate, delete) come with F2.10.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector.features.voice import reference, router  # noqa: E402

PAGES = ROOT / "frontend" / "pages"
READING_JS = (PAGES / "reading.js").read_text(encoding="utf-8")
ORGANISE_JS = (PAGES / "organise.js").read_text(encoding="utf-8")
DISCARD_JS = (PAGES / "organise-discard.js").read_text(encoding="utf-8")


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

    def test_the_discard_question_hears_confirm_and_cancel_only(self):
        self.assertIn("file_tool", DISCARD_JS)
        self.assertEqual(_intent(router.FILE_TOOL, "confirm"), router.TOOL_CONFIRM)
        self.assertEqual(_intent(router.FILE_TOOL, "cancel"), router.CANCEL)
        self.assertIsNone(_intent(router.FILE_TOOL, "done"))
        self.assertIsNone(_intent(router.FILE_TOOL, "quit"))  # modal: no globals

    def test_both_are_listed_in_what_can_i_say(self):
        section = next(s for s in reference.sections(router.READING) if s["id"] == "organising")
        phrases = {p for command in section["commands"] for p in command["phrases"]}
        self.assertTrue({"done", "cancel"} <= phrases)


class OrganiseVoiceWiringTests(unittest.TestCase):
    """The page side: which function each intent reaches."""

    def test_done_and_cancel_reach_the_two_buttons_functions(self):
        block = re.search(r"const ORGANISE_VOICE_ACTIONS = \{(.*?)\n\};", READING_JS, re.S).group(1)
        self.assertRegex(block, r"ORGANISE_DONE: \(\) => finishOrganise\(\)")
        self.assertRegex(block, r"CANCEL: \(\) => cancelOrganise\(\)")
        button = re.search(r'organiseCancelBtn\.addEventListener\("click", \(\) => (\w+)\(\)\)', ORGANISE_JS)
        self.assertEqual(button.group(1), "cancelOrganise")

    def test_the_discard_question_takes_spoken_answers_before_organise_does(self):
        handler = READING_JS[READING_JS.index('window.addEventListener("lector:command"'):]
        self.assertLess(handler.index("OrganiseDiscardDialog.isOpen()"), handler.index("ORGANISE_VOICE_ACTIONS"))
        self.assertRegex(DISCARD_JS, r'command\.intent === "TOOL_CONFIRM"\) close\(true\)')
        self.assertRegex(DISCARD_JS, r'command\.intent === "CANCEL"\) close\(false\)')

    def test_organise_claims_its_context_and_gives_it_back(self):
        self.assertIn('callApi("set_voice_context", "organise")', ORGANISE_JS)
        self.assertIn('callApi("set_voice_context", "reading")', ORGANISE_JS)
        self.assertIn(router.ORGANISE, router.CONTEXTS)


if __name__ == "__main__":
    unittest.main()
