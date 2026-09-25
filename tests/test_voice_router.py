"""Tests for the contextual voice command router (Milestone 8.4).

`unittest`, matching `test_command_grammar.py` and the rest of this voice
test suite. Run with:

    python -m unittest discover -s tests

Two things this suite exists to pin down: global commands (undo/redo/help/go
home) resolve the same way regardless of which context is active, and the
`reading` context still produces exactly what `command_grammar.resolve`
alone would — the "migrate without changing behavior" contract 8.4 promises.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.voice import command_grammar  # noqa: E402
from lector.features.voice import router  # noqa: E402


class GlobalCommandTests(unittest.TestCase):
    def test_undo_resolves_from_the_home_context(self):
        result = router.resolve(router.HOME, "undo")
        self.assertEqual(result["command"]["intent"], router.UNDO)
        self.assertIsNone(result["clarify"])

    def test_redo_resolves_from_the_reading_context(self):
        result = router.resolve(router.READING, "redo")
        self.assertEqual(result["command"]["intent"], router.REDO)

    def test_help_resolves_via_either_of_its_two_phrasings(self):
        self.assertEqual(
            router.resolve(router.HOME, "help")["command"]["intent"], router.HELP
        )
        self.assertEqual(
            router.resolve(router.HOME, "what can i say")["command"]["intent"],
            router.HELP,
        )

    def test_go_home_resolves_from_the_settings_context(self):
        result = router.resolve(router.SETTINGS, "go home")
        self.assertEqual(result["command"]["intent"], router.GO_HOME)

    def test_global_commands_are_available_in_every_context(self):
        for context in router.CONTEXTS:
            result = router.resolve(context, "undo")
            self.assertEqual(
                result["command"]["intent"], router.UNDO, msg=f"context={context}"
            )

    def test_a_global_command_is_found_among_alternatives(self):
        # Same "try every n-best hypothesis" tolerance command_grammar.resolve
        # gives its own grammar.
        result = router.resolve(router.HOME, "undue", alternatives=["undo"])
        self.assertEqual(result["command"]["intent"], router.UNDO)

    def test_unrelated_speech_resolves_to_nothing_in_a_context_with_no_grammar(self):
        # `save_dialog` has no scoped grammar yet (Milestones 8.7-8.8), so
        # once no global command matches there is nothing left to try.
        # `picker` gained its own scoped grammar in Milestone 8.6 — see
        # PickerContextTests.test_unrelated_speech_still_resolves_to_nothing.
        result = router.resolve(router.SAVE_DIALOG, "banana")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])

    def test_global_matching_has_no_near_miss_clarification_tier(self):
        # A near-miss on "go home" must not produce a clarify payload the way
        # command_grammar's own near-miss phrases do — 8.4 intentionally
        # excludes global commands from that tier.
        result = router.resolve(router.HOME, "go hone")
        self.assertIsNone(result["clarify"])


class ReadingContextDelegationTests(unittest.TestCase):
    def test_a_confident_reading_command_matches_the_same_as_command_grammar(self):
        text = "next page"
        self.assertEqual(
            router.resolve(router.READING, text),
            command_grammar.resolve(text),
        )

    def test_a_near_miss_reading_phrase_still_clarifies_the_same_way(self):
        text = "nest pah"
        self.assertEqual(
            router.resolve(router.READING, text),
            command_grammar.resolve(text),
        )

    def test_reading_alternatives_are_passed_through_to_command_grammar(self):
        result = router.resolve(router.READING, "nest pah", alternatives=["next page"])
        self.assertEqual(result["command"]["intent"], "NEXT_PAGE")

    def test_non_reading_contexts_do_not_fall_back_to_the_reading_grammar(self):
        # "next page" is only meaningful once a document is open; a context
        # with its own scoped grammar (Home's, here) must not additionally
        # inherit reading's, or Home would silently gain reading commands on
        # top of its own.
        result = router.resolve(router.HOME, "next page")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class HomeContextTests(unittest.TestCase):
    def test_open_settings_resolves_from_its_one_phrasing(self):
        result = router.resolve(router.HOME, "open settings")
        self.assertEqual(result["command"]["intent"], router.OPEN_SETTINGS)
        self.assertIsNone(result["clarify"])

    def test_open_recent_resolves_via_either_of_its_two_phrasings(self):
        self.assertEqual(
            router.resolve(router.HOME, "open recent")["command"]["intent"],
            router.OPEN_RECENT,
        )
        self.assertEqual(
            router.resolve(router.HOME, "resume reading")["command"]["intent"],
            router.OPEN_RECENT,
        )

    def test_home_matching_has_no_near_miss_clarification_tier(self):
        result = router.resolve(router.HOME, "open settins")
        self.assertIsNone(result["clarify"])

    def test_settings_only_phrases_do_not_resolve_in_home(self):
        result = router.resolve(router.HOME, "light theme")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class SettingsContextTests(unittest.TestCase):
    def test_each_theme_phrase_resolves_to_its_own_intent(self):
        cases = {
            "light theme": router.THEME_LIGHT,
            "dark theme": router.THEME_DARK,
            "sepia theme": router.THEME_SEPIA,
        }
        for phrase, intent in cases.items():
            result = router.resolve(router.SETTINGS, phrase)
            self.assertEqual(result["command"]["intent"], intent, msg=phrase)
            self.assertIsNone(result["clarify"])

    def test_save_behavior_phrases_resolve_to_their_intents(self):
        self.assertEqual(
            router.resolve(router.SETTINGS, "save a copy")["command"]["intent"],
            router.SAVE_COPY,
        )
        self.assertEqual(
            router.resolve(router.SETTINGS, "overwrite the original")["command"]["intent"],
            router.SAVE_OVERWRITE,
        )

    def test_reopen_behavior_phrases_resolve_to_their_intents(self):
        self.assertEqual(
            router.resolve(router.SETTINGS, "continue where i left off")["command"]["intent"],
            router.REOPEN_CONTINUE,
        )
        self.assertEqual(
            router.resolve(router.SETTINGS, "start at the beginning")["command"]["intent"],
            router.REOPEN_START,
        )

    def test_settings_matching_has_no_near_miss_clarification_tier(self):
        result = router.resolve(router.SETTINGS, "dork theme")
        self.assertIsNone(result["clarify"])

    def test_home_only_phrases_do_not_resolve_in_settings(self):
        result = router.resolve(router.SETTINGS, "open recent")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class PickerContextTests(unittest.TestCase):
    def test_a_spoken_number_resolves_to_pick_with_its_index(self):
        result = router.resolve(router.PICKER, "three")
        self.assertEqual(result["command"]["intent"], router.PICK)
        self.assertEqual(result["command"]["index"], 3)
        self.assertIsNone(result["clarify"])

    def test_a_multi_word_spoken_number_resolves_to_its_value(self):
        result = router.resolve(router.PICKER, "twenty one")
        self.assertEqual(result["command"]["intent"], router.PICK)
        self.assertEqual(result["command"]["index"], 21)

    def test_a_digit_resolves_the_same_as_its_spoken_form(self):
        result = router.resolve(router.PICKER, "3")
        self.assertEqual(result["command"]["intent"], router.PICK)
        self.assertEqual(result["command"]["index"], 3)

    def test_zero_does_not_resolve_since_badges_are_1_indexed(self):
        result = router.resolve(router.PICKER, "zero")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])

    def test_cancel_resolves_via_either_of_its_two_phrasings(self):
        self.assertEqual(
            router.resolve(router.PICKER, "cancel")["command"]["intent"], router.CANCEL
        )
        self.assertEqual(
            router.resolve(router.PICKER, "never mind")["command"]["intent"], router.CANCEL
        )

    def test_a_number_is_found_among_alternatives(self):
        result = router.resolve(router.PICKER, "free", alternatives=["three"])
        self.assertEqual(result["command"]["intent"], router.PICK)
        self.assertEqual(result["command"]["index"], 3)

    def test_unrelated_speech_still_resolves_to_nothing(self):
        result = router.resolve(router.PICKER, "banana")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class VocabularyTests(unittest.TestCase):
    def test_reading_vocabulary_is_a_superset_of_global_and_command_grammar(self):
        vocab = set(router.vocabulary_for(router.READING))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(set(command_grammar.VOCABULARY).issubset(vocab))

    def test_a_context_with_no_scoped_grammar_gets_only_global_vocabulary(self):
        # PICKER gained its own scoped grammar in Milestone 8.6 (see
        # PickerContextTests below); SAVE_DIALOG is still one of the contexts
        # with none, pending 8.7.
        self.assertEqual(
            set(router.vocabulary_for(router.SAVE_DIALOG)), set(router.GLOBAL_VOCABULARY)
        )

    def test_home_vocabulary_is_a_superset_of_global_and_its_own_phrases(self):
        vocab = set(router.vocabulary_for(router.HOME))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(
            set(router._vocabulary_from_phrases(router.HOME_PHRASES)).issubset(vocab)
        )

    def test_settings_vocabulary_is_a_superset_of_global_and_its_own_phrases(self):
        vocab = set(router.vocabulary_for(router.SETTINGS))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(
            set(router._vocabulary_from_phrases(router.SETTINGS_PHRASES)).issubset(vocab)
        )

    def test_default_context_is_reading_to_preserve_pre_router_behavior(self):
        # Milestone 8.1-8.3's integration tests call `Api._on_voice_result`
        # with no context set and expect reading commands to resolve; this
        # pins down the default that makes that continue to work.
        self.assertEqual(router.DEFAULT_CONTEXT, router.READING)


if __name__ == "__main__":
    unittest.main()
