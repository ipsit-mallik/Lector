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
        # `home` has no scoped grammar yet (Milestone 8.5), so once no global
        # command matches there is nothing left to try.
        result = router.resolve(router.HOME, "banana")
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
        # with no scoped grammar of its own must not accidentally inherit
        # reading's, or Home would silently gain reading commands.
        result = router.resolve(router.HOME, "next page")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class VocabularyTests(unittest.TestCase):
    def test_reading_vocabulary_is_a_superset_of_global_and_command_grammar(self):
        vocab = set(router.vocabulary_for(router.READING))
        self.assertTrue(set(router.GLOBAL_VOCABULARY).issubset(vocab))
        self.assertTrue(set(command_grammar.VOCABULARY).issubset(vocab))

    def test_a_context_with_no_scoped_grammar_gets_only_global_vocabulary(self):
        self.assertEqual(
            set(router.vocabulary_for(router.HOME)), set(router.GLOBAL_VOCABULARY)
        )

    def test_default_context_is_reading_to_preserve_pre_router_behavior(self):
        # Milestone 8.1-8.3's integration tests call `Api._on_voice_result`
        # with no context set and expect reading commands to resolve; this
        # pins down the default that makes that continue to work.
        self.assertEqual(router.DEFAULT_CONTEXT, router.READING)


if __name__ == "__main__":
    unittest.main()
