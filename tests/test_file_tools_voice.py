"""Voice for the file tools (Milestone F1, F1.8): the reader's three commands,
Home's "open tools", and the tool dialogs' own grammar (router.FILE_TOOL)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.voice import command_grammar as grammar  # noqa: E402
from lector.features.voice import fuzzy, reference, router  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
READING_JS = (FRONTEND / "pages" / "reading.js").read_text(encoding="utf-8")
HOME_JS = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
TOOL_INTENTS = (grammar.COMPRESS_FILE, grammar.MAKE_SEARCHABLE, grammar.EXPORT_IMAGES)


def intent(context: str, text: str) -> str | None:
    command = router.resolve(context, text)["command"]
    return command["intent"] if command else None


class ReaderCommandTests(unittest.TestCase):
    def test_each_phrasing_opens_its_tool(self):
        expected = {
            "compress this file": grammar.COMPRESS_FILE,
            "compress": grammar.COMPRESS_FILE,
            "make it smaller": grammar.COMPRESS_FILE,
            "make searchable": grammar.MAKE_SEARCHABLE,
            "make this searchable": grammar.MAKE_SEARCHABLE,
            "export as images": grammar.EXPORT_IMAGES,
            "export pages": grammar.EXPORT_IMAGES,
            "save as images": grammar.EXPORT_IMAGES,
        }
        for text, want in expected.items():
            with self.subTest(text=text):
                self.assertEqual(intent(router.READING, text), want)

    def test_they_do_not_crowd_the_commands_already_there(self):
        others = [p for i, ps in grammar.PHRASES.items() if i not in TOOL_INTENTS for p in ps]
        others += [p for ps in router.GLOBAL_PHRASES.values() for p in ps]
        for tool in TOOL_INTENTS:
            for phrase in grammar.PHRASES[tool]:
                for other in others:
                    with self.subTest(phrase=phrase, other=other):
                        self.assertLess(fuzzy.similarity(phrase, other), fuzzy.DEFAULT_THRESHOLD)

    def test_saving_is_still_saving(self):
        self.assertEqual(intent(router.READING, "save the file"), grammar.SAVE)
        self.assertEqual(intent(router.READING, "save"), grammar.SAVE)

    def test_the_reader_acts_on_each(self):
        for name in TOOL_INTENTS:
            with self.subTest(intent=name):
                self.assertRegex(READING_JS, rf"\b{name}: \(\) => openTool\(")

    def test_the_words_can_be_heard(self):
        for phrase in [p for i in TOOL_INTENTS for p in grammar.PHRASES[i]]:
            for word in phrase.split():
                with self.subTest(word=word):
                    self.assertIn(word, grammar.VOCABULARY)


class HomeCommandTests(unittest.TestCase):
    def test_open_tools(self):
        self.assertEqual(intent(router.HOME, "open tools"), router.OPEN_TOOLS)
        self.assertIn("tools", router.vocabulary_for(router.HOME))
        self.assertRegex(HOME_JS, r'OPEN_TOOLS: \(\) => setHomeSection\("tools"\)')

    def test_it_is_listed_under_the_app_itself_for_home(self):
        app = next(s for s in reference.sections(router.HOME) if s["id"] == "app")
        self.assertEqual(app["title"], "The app itself")
        card = next(c for c in app["commands"] if "open tools" in c["phrases"])
        self.assertEqual(card["contexts"], [router.HOME])


class ReferenceTests(unittest.TestCase):
    def test_the_reader_lists_a_files_section(self):
        files = next(s for s in reference.sections(router.READING) if s["id"] == "files")
        self.assertEqual(files["title"], "Files")
        listed = {p for c in files["commands"] for p in c["phrases"]}
        for tool in TOOL_INTENTS:
            self.assertLessEqual(set(grammar.PHRASES[tool]), listed)

    def test_the_dialog_grammar_is_listed(self):
        prompts = next(s for s in reference.sections(router.READING) if s["id"] == "prompts")
        confirm = [c for c in prompts["commands"] if "confirm" in c["phrases"]]
        self.assertEqual([c["contexts"] for c in confirm], [[router.FILE_TOOL]])


class DialogGrammarTests(unittest.TestCase):
    def test_confirm_cancel_and_numbers(self):
        self.assertEqual(intent(router.FILE_TOOL, "confirm"), router.TOOL_CONFIRM)
        self.assertEqual(intent(router.FILE_TOOL, "cancel"), router.CANCEL)
        self.assertEqual(intent(router.FILE_TOOL, "never mind"), router.CANCEL)
        command = router.resolve(router.FILE_TOOL, "three")["command"]
        self.assertEqual((command["intent"], command["index"]), (router.DIALOG_PICK, 3))

    def test_it_is_modal(self):
        for text in ("quit", "close app", "open settings", "go home", "next page", "compress"):
            with self.subTest(text=text):
                self.assertIsNone(router.resolve(router.FILE_TOOL, text)["command"])
        vocabulary = set(router.vocabulary_for(router.FILE_TOOL))
        self.assertNotIn("quit", vocabulary)
        self.assertIn("confirm", vocabulary)

    def test_every_page_with_a_tool_dialog_routes_it(self):
        self.assertIn("ToolDialog.handleCommand(command)", READING_JS)
        self.assertIn("StopJobDialog.handleCommand(command)", READING_JS)
        self.assertRegex(HOME_JS, r"\[StopJobDialog, ToolDialog, ConvertDialog, CombineDialog, PdfPicker\]")

    def test_every_tool_dialog_uses_the_file_tool_scope(self):
        for name in ("pages/tool-dialogs.js", "pages/convert-dialog.js", "pages/combine-dialog.js",
                     "pages/tools-page.js", "js/file-jobs.js"):
            with self.subTest(file=name):
                source = (FRONTEND / name).read_text(encoding="utf-8")
                self.assertTrue(re.search(r'createVoiceScope\("file_tool"\)', source))


if __name__ == "__main__":
    unittest.main()
