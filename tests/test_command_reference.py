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
from lector.features.voice import router  # noqa: E402
from lector.features.voice import wake  # noqa: E402


def _all_commands(context: str = router.READING) -> list[dict]:
    return [cmd for category in reference.categories(context) for cmd in category["commands"]]


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


class ContextScopingTests(unittest.TestCase):
    """Milestone 8.10: the panel shows only the active context's commands."""

    def test_home_context_is_scoped_away_from_the_reading_grammar(self):
        titles = [c["title"] for c in reference.categories(router.HOME)]
        self.assertNotIn("Moving around", titles)
        self.assertNotIn("Highlighting", titles)

    def test_settings_context_is_scoped_away_from_the_reading_grammar(self):
        titles = [c["title"] for c in reference.categories(router.SETTINGS)]
        self.assertNotIn("Moving around", titles)
        self.assertNotIn("Highlighting", titles)

    def test_every_context_without_its_own_categories_falls_back_to_reading(self):
        # Only HOME/SETTINGS have their own scoped grammar so far
        # (Milestone 8.5) — every other context, including any future one
        # this test doesn't yet know the name of, keeps the pre-8.10 default
        # rather than advertising nothing.
        for context in router.CONTEXTS:
            if context in (router.HOME, router.SETTINGS):
                continue
            with self.subTest(context=context):
                self.assertEqual(reference.categories(context), reference.categories())

    def test_every_home_phrase_is_advertised_and_understood(self):
        # `HOME_PHRASES` is Home's whole scoped grammar — every intent there
        # should show up in the panel, and every example the panel offers
        # should resolve through the router the way `READING`'s examples
        # resolve through `command_grammar`.
        advertised_intents = set()
        for command in _all_commands(router.HOME):
            for phrase in command["examples"]:
                with self.subTest(phrase=phrase):
                    resolved = router.resolve(router.HOME, phrase)["command"]
                    self.assertIsNotNone(
                        resolved, f"the Home panel offers {phrase!r} but the router rejects it"
                    )
                    advertised_intents.add(resolved["intent"])
        self.assertEqual(advertised_intents, set(router.HOME_PHRASES))

    def test_every_settings_phrase_is_advertised_and_understood(self):
        advertised_intents = set()
        for command in _all_commands(router.SETTINGS):
            for phrase in command["examples"]:
                with self.subTest(phrase=phrase):
                    resolved = router.resolve(router.SETTINGS, phrase)["command"]
                    self.assertIsNotNone(
                        resolved, f"the Settings panel offers {phrase!r} but the router rejects it"
                    )
                    advertised_intents.add(resolved["intent"])
        self.assertEqual(advertised_intents, set(router.SETTINGS_PHRASES))

    def test_no_home_or_settings_category_is_empty(self):
        for context in (router.HOME, router.SETTINGS):
            for category in reference.categories(context):
                with self.subTest(context=context, category=category["title"]):
                    self.assertTrue(category["commands"])

    def test_home_and_settings_commands_have_a_description_and_equivalent(self):
        for context in (router.HOME, router.SETTINGS):
            for command in _all_commands(context):
                with self.subTest(context=context, command=command["examples"]):
                    self.assertTrue(command["description"].strip())
                    self.assertTrue(command["equivalent"].strip())

    def test_the_panel_threads_context_through_to_categories(self):
        for context in (router.HOME, router.SETTINGS, router.READING):
            with self.subTest(context=context):
                self.assertEqual(
                    [c["title"] for c in reference.panel(context)["categories"]],
                    [c["title"] for c in reference.categories(context)],
                )


if __name__ == "__main__":
    unittest.main()
