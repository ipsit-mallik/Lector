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

    def test_global_commands_are_available_in_every_context_except_dictation(self):
        # DICTATION is the one deliberate exception (Milestone 8.8) — see
        # test_global_commands_do_not_resolve_while_dictation_is_active below,
        # and DictationContextTests for its own behavior in full.
        for context in router.CONTEXTS:
            if context == router.DICTATION:
                continue
            result = router.resolve(context, "undo")
            self.assertEqual(
                result["command"]["intent"], router.UNDO, msg=f"context={context}"
            )

    def test_a_global_command_is_found_among_alternatives(self):
        # Same "try every n-best hypothesis" tolerance command_grammar.resolve
        # gives its own grammar.
        result = router.resolve(router.HOME, "undue", alternatives=["undo"])
        self.assertEqual(result["command"]["intent"], router.UNDO)

    def test_global_commands_do_not_resolve_while_dictation_is_active(self):
        # DictationContextTests below covers `dictation`'s own behavior in
        # detail; this pins down that it is the one context excluded from
        # "global commands are matched before a context is consulted at all"
        # (see the module docstring) — a dictated sentence that happens to
        # contain "undo" must not be hijacked into the UNDO command.
        result = router.resolve(router.DICTATION, "undo")
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


class OpenDialogContextTests(unittest.TestCase):
    def test_a_spoken_number_resolves_to_dialog_pick_with_its_index(self):
        result = router.resolve(router.OPEN_DIALOG, "three")
        self.assertEqual(result["command"]["intent"], router.DIALOG_PICK)
        self.assertEqual(result["command"]["index"], 3)
        self.assertIsNone(result["clarify"])

    def test_a_digit_resolves_the_same_as_its_spoken_form(self):
        result = router.resolve(router.OPEN_DIALOG, "3")
        self.assertEqual(result["command"]["intent"], router.DIALOG_PICK)
        self.assertEqual(result["command"]["index"], 3)

    def test_go_up_resolves_via_any_of_its_phrasings(self):
        for phrase in ("go up", "up a folder", "back a folder", "parent folder"):
            result = router.resolve(router.OPEN_DIALOG, phrase)
            self.assertEqual(result["command"]["intent"], router.DIALOG_UP, msg=phrase)

    def test_cancel_resolves_via_either_of_its_two_phrasings(self):
        self.assertEqual(
            router.resolve(router.OPEN_DIALOG, "cancel")["command"]["intent"], router.CANCEL
        )
        self.assertEqual(
            router.resolve(router.OPEN_DIALOG, "never mind")["command"]["intent"], router.CANCEL
        )

    def test_save_here_does_not_resolve_since_open_has_nothing_to_confirm(self):
        result = router.resolve(router.OPEN_DIALOG, "save here")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])

    def test_unrelated_speech_still_resolves_to_nothing(self):
        result = router.resolve(router.OPEN_DIALOG, "banana")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class SaveDialogContextTests(unittest.TestCase):
    def test_a_spoken_number_resolves_to_dialog_pick_with_its_index(self):
        result = router.resolve(router.SAVE_DIALOG, "twenty one")
        self.assertEqual(result["command"]["intent"], router.DIALOG_PICK)
        self.assertEqual(result["command"]["index"], 21)

    def test_go_up_resolves_the_same_as_in_open_dialog(self):
        result = router.resolve(router.SAVE_DIALOG, "go up")
        self.assertEqual(result["command"]["intent"], router.DIALOG_UP)

    def test_cancel_resolves(self):
        result = router.resolve(router.SAVE_DIALOG, "cancel")
        self.assertEqual(result["command"]["intent"], router.CANCEL)

    def test_save_here_resolves_via_any_of_its_phrasings(self):
        for phrase in ("save here", "save it", "confirm save"):
            result = router.resolve(router.SAVE_DIALOG, phrase)
            self.assertEqual(result["command"]["intent"], router.DIALOG_CONFIRM, msg=phrase)

    def test_unrelated_speech_still_resolves_to_nothing(self):
        result = router.resolve(router.SAVE_DIALOG, "banana")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class DictationContextTests(unittest.TestCase):
    def test_done_resolves_to_stop_dictation(self):
        result = router.resolve(router.DICTATION, "done")
        self.assertEqual(result["command"]["intent"], router.STOP_DICTATION)
        self.assertIsNone(result["clarify"])

    def test_cancel_resolves_via_either_of_its_two_phrasings(self):
        self.assertEqual(
            router.resolve(router.DICTATION, "cancel")["command"]["intent"], router.CANCEL
        )
        self.assertEqual(
            router.resolve(router.DICTATION, "never mind")["command"]["intent"], router.CANCEL
        )

    def test_arbitrary_dictated_text_resolves_to_no_command(self):
        # This is the entire point of the context: unlike every other one,
        # "resolves to nothing" here does not mean "wasn't understood" — the
        # caller (Api._on_voice_result / command-reference.js) treats this
        # together with the original text as dictated content.
        result = router.resolve(router.DICTATION, "chapter three summary")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])

    def test_a_global_command_word_inside_dictated_text_is_not_hijacked(self):
        # "undo" would resolve instantly as a global command in every other
        # context (see GlobalCommandTests) — dictation is the one place a
        # reader must be able to say it and have it land in the search field
        # instead.
        for phrase in ("undo", "help", "go home", "redo", "start search"):
            result = router.resolve(router.DICTATION, phrase)
            self.assertIsNone(result["command"], msg=phrase)
            self.assertIsNone(result["clarify"], msg=phrase)

    def test_a_stop_phrase_is_found_among_alternatives(self):
        result = router.resolve(router.DICTATION, "dun", alternatives=["done"])
        self.assertEqual(result["command"]["intent"], router.STOP_DICTATION)


class StartDictationTests(unittest.TestCase):
    def test_start_search_resolves_as_a_global_command_from_reading(self):
        # Entering dictation happens from whatever context has the reference
        # panel open (today, always `reading`) — a normal global-command
        # match, not a special case the way exiting dictation is.
        result = router.resolve(router.READING, "start search")
        self.assertEqual(result["command"]["intent"], router.START_DICTATION)
        self.assertIsNone(result["clarify"])

    def test_start_search_is_available_in_every_context(self):
        for context in router.CONTEXTS:
            if context == router.DICTATION:
                continue  # Covered by DictationContextTests instead.
            result = router.resolve(context, "start search")
            self.assertEqual(
                result["command"]["intent"], router.START_DICTATION, msg=f"context={context}"
            )


class VocabularyTests(unittest.TestCase):
    def test_reading_vocabulary_is_a_superset_of_global_and_command_grammar(self):
        vocab = set(router.vocabulary_for(router.READING))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(set(command_grammar.VOCABULARY).issubset(vocab))

    def test_dictation_vocabulary_is_a_superset_of_global_and_its_own_phrases(self):
        # Never actually handed to the recognizer while dictation is active —
        # Api._refresh_voice_vocabulary bypasses this in favor of
        # VoiceEngine.set_vocabulary(None) (open vocabulary) instead — but
        # still a coherent value in its own right, the same as every other
        # context's entry.
        vocab = set(router.vocabulary_for(router.DICTATION))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(
            set(router._vocabulary_from_phrases(router.DICTATION_PHRASES)).issubset(vocab)
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

    def test_open_dialog_vocabulary_is_a_superset_of_global_and_its_own_phrases(self):
        vocab = set(router.vocabulary_for(router.OPEN_DIALOG))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(
            set(router._vocabulary_from_phrases(router.OPEN_DIALOG_PHRASES)).issubset(vocab)
        )
        self.assertTrue(set(router._PICKER_NUMBER_WORDS).issubset(vocab))

    def test_save_dialog_vocabulary_is_a_superset_of_global_and_its_own_phrases(self):
        vocab = set(router.vocabulary_for(router.SAVE_DIALOG))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(
            set(router._vocabulary_from_phrases(router.SAVE_DIALOG_PHRASES)).issubset(vocab)
        )
        self.assertTrue(set(router._PICKER_NUMBER_WORDS).issubset(vocab))

    def test_default_context_is_reading_to_preserve_pre_router_behavior(self):
        # Milestone 8.1-8.3's integration tests call `Api._on_voice_result`
        # with no context set and expect reading commands to resolve; this
        # pins down the default that makes that continue to work.
        self.assertEqual(router.DEFAULT_CONTEXT, router.READING)


if __name__ == "__main__":
    unittest.main()
