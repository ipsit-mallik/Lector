"""Tests for the "What can I say?" reference payload (Milestone 8).

The panel's whole value is that a reader can trust it, so the central test
here is not about formatting: it feeds every phrase the panel advertises back
through the parser and insists the parser understands it. A reference that
lists a phrase the recognizer would reject is worse than no reference, because
the reader concludes voice control is broken rather than that the wording was
wrong.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.voice import command_grammar  # noqa: E402
from lector.features.voice import reference  # noqa: E402
from lector.features.voice import wake  # noqa: E402


def _all_commands() -> list[dict]:
    return [cmd for category in reference.categories() for cmd in category["commands"]]


class ExampleTruthfulnessTests(unittest.TestCase):
    def test_every_advertised_phrase_is_understood(self):
        for command in _all_commands():
            for phrase in command["examples"]:
                with self.subTest(phrase=phrase):
                    self.assertIsNotNone(
                        command_grammar.parse(phrase),
                        f"the panel offers {phrase!r} but the grammar rejects it",
                    )

    def test_the_page_number_example_parses_as_a_jump(self):
        # "Go to page 12" is the one example carrying an argument, so it is
        # the one most likely to drift out of the grammar's reach.
        phrases = [
            phrase
            for command in _all_commands()
            for phrase in command["examples"]
            if "12" in phrase
        ]
        self.assertTrue(phrases)
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                parsed = command_grammar.parse(phrase)
                self.assertEqual(parsed["intent"], command_grammar.GOTO_PAGE)
                self.assertEqual(parsed["page"], 12)

    def test_the_highlight_examples_carry_the_words_to_mark(self):
        highlights = next(
            c for c in reference.categories() if c["title"] == "Highlighting"
        )
        for command in highlights["commands"]:
            for phrase in command["examples"]:
                with self.subTest(phrase=phrase):
                    parsed = command_grammar.parse(phrase)
                    self.assertTrue(parsed.get("query"))


class PanelShapeTests(unittest.TestCase):
    def test_every_command_offers_a_keyboard_or_mouse_equivalent(self):
        # docs/PRD.md's parity requirement, restated as something the reader
        # can check: the panel claims every voice command also has a button or
        # a shortcut, and this is what keeps that claim honest.
        for command in _all_commands():
            with self.subTest(command=command["examples"]):
                self.assertTrue(command["equivalent"].strip())

    def test_every_command_explains_what_it_does(self):
        for command in _all_commands():
            with self.subTest(command=command["examples"]):
                self.assertTrue(command["description"].strip())

    def test_no_category_is_empty(self):
        # An empty category reads as a feature that is broken rather than one
        # that does not exist yet.
        for category in reference.categories():
            with self.subTest(category=category["title"]):
                self.assertTrue(category["commands"])

    def test_search_is_absent_until_it_exists(self):
        # docs/PRD.md names a "Finding words" category, but in-document search
        # has not been built. It belongs here when it is, and not before.
        titles = [c["title"] for c in reference.categories()]
        self.assertNotIn("Finding words", titles)

    def test_the_panel_names_both_ways_of_starting(self):
        panel = reference.panel()

        self.assertEqual(panel["wake_phrase"], wake.WAKE_PHRASE)
        self.assertTrue(panel["push_to_talk_key"])

    def test_the_displayed_phrase_is_the_recognized_one(self):
        # Settings and onboarding both print `wake_phrase_display`, so the
        # nicely-cased form has to stay the same words as the grammar's.
        panel = reference.panel()

        self.assertEqual(panel["wake_phrase_display"].lower(), panel["wake_phrase"])

    def test_the_panel_carries_its_categories(self):
        self.assertEqual(
            [c["title"] for c in reference.panel()["categories"]],
            [c["title"] for c in reference.categories()],
        )


if __name__ == "__main__":
    unittest.main()
