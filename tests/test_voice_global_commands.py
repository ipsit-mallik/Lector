"""Milestone 8.12: global voice commands, per-screen grammar swap, modal scopes.

Unit tests for `router` (the phrase tables and `resolve`) and for how `Api`
swaps the recognizer's grammar when the screen changes. The per-screen
integration tests live in `test_milestone_8_12_integration.py`.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import router  # noqa: E402

# Every context that matches globals before its own table. DICTATION is the
# one exception (see router's module docstring) and is tested separately.
GLOBAL_CONTEXTS = tuple(c for c in router.CONTEXTS if c != router.DICTATION)
# The modal scopes only let a named few globals through (see router).
OPEN_CONTEXTS = tuple(c for c in GLOBAL_CONTEXTS if c not in router.MODAL_ALLOWED_GLOBALS)

# Every fixed-phrase table a context can own, keyed by that context.
SCOPED_TABLES = {
    router.HOME: router.HOME_PHRASES,
    router.SETTINGS: router.SETTINGS_PHRASES,
    router.PICKER: router.PICKER_PHRASES,
    router.OPEN_DIALOG: router.OPEN_DIALOG_PHRASES,
    router.SAVE_DIALOG: router.SAVE_DIALOG_PHRASES,
    router.REMOVE_CONFIRM: router.REMOVE_CONFIRM_PHRASES,
    router.SAVE_CONFIRM: router.SAVE_CONFIRM_PHRASES,
    router.REFERENCE: router.REFERENCE_PHRASES,
    router.OVERWRITE_CONFIRM: router.OVERWRITE_CONFIRM_PHRASES,
}


def _intent(context: str, phrase: str):
    return (router.resolve(context, phrase).get("command") or {}).get("intent")


class GlobalCommandTests(unittest.TestCase):
    """"open a PDF", "open settings", "go to Recent", "what can I say" work
    on every screen, not only the one that first had a table for them."""

    EXPECTED = {
        router.OPEN_PDF: ("open a pdf", "open pdf"),
        router.OPEN_SETTINGS: ("open settings",),
        router.GO_RECENT: ("go to recent", "show recent files"),
        router.HELP: ("what can i say", "help"),
    }

    def test_each_phrase_resolves_to_its_intent_in_every_context(self):
        for context in OPEN_CONTEXTS:
            for intent, phrases in self.EXPECTED.items():
                for phrase in phrases:
                    with self.subTest(context=context, phrase=phrase):
                        self.assertEqual(_intent(context, phrase), intent)

    def test_uppercase_recognizer_output_still_resolves(self):
        self.assertEqual(_intent(router.SETTINGS, "Open A PDF"), router.OPEN_PDF)

    def test_dictation_does_not_hijack_dictated_text(self):
        for phrase in ("open a pdf", "open settings", "go to recent"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(_intent(router.DICTATION, phrase))

    def test_open_settings_is_no_longer_home_scoped(self):
        self.assertNotIn(router.OPEN_SETTINGS, router.HOME_PHRASES)
        self.assertIn(router.OPEN_SETTINGS, router.GLOBAL_PHRASES)

    def test_every_global_word_is_in_every_context_vocabulary(self):
        for context in OPEN_CONTEXTS:
            vocabulary = set(router.vocabulary_for(context))
            for word in ("pdf", "settings", "recent", "say"):
                with self.subTest(context=context, word=word):
                    self.assertIn(word, vocabulary)


class OpenNumberTests(unittest.TestCase):
    """"open number N" opens the Nth row of what Recent/Favorites shows."""

    def test_spoken_and_digit_numbers(self):
        for text, index in (
            ("open number three", 3),
            ("open number 3", 3),
            ("open number ten", 10),
            ("open number twenty one", 21),
        ):
            with self.subTest(text=text):
                command = router.resolve(router.HOME, text)["command"]
                self.assertEqual(command["intent"], router.OPEN_NUMBER)
                self.assertEqual(command["index"], index)

    def test_a_number_alone_is_not_a_command_on_home(self):
        # Bare numbers only mean something while the picker's badges are up.
        self.assertIsNone(_intent(router.HOME, "three"))

    def test_zero_and_a_missing_number_are_rejected(self):
        self.assertIsNone(_intent(router.HOME, "open number zero"))
        self.assertIsNone(_intent(router.HOME, "open number"))

    def test_it_is_home_only(self):
        for context in (router.SETTINGS, router.READING, router.REFERENCE):
            with self.subTest(context=context):
                self.assertNotEqual(_intent(context, "open number three"), router.OPEN_NUMBER)

    def test_it_does_not_steal_open_recent(self):
        self.assertEqual(_intent(router.HOME, "open recent"), router.OPEN_RECENT)

    def test_the_number_words_are_in_home_vocabulary(self):
        vocabulary = set(router.vocabulary_for(router.HOME))
        self.assertTrue({"number", "three", "twenty"} <= vocabulary)


class ModalScopeTests(unittest.TestCase):
    """"What can I say?" and the overwrite confirmation are modal scopes."""

    def test_the_new_contexts_are_declarable(self):
        self.assertIn(router.REFERENCE, router.CONTEXTS)
        self.assertIn(router.OVERWRITE_CONFIRM, router.CONTEXTS)

    def test_reference_closes_by_voice(self):
        for phrase in ("got it", "go back", "cancel", "never mind"):
            with self.subTest(phrase=phrase):
                self.assertEqual(_intent(router.REFERENCE, phrase), router.CANCEL)

    def test_reference_close_phrases_cannot_be_mistaken_for_close_app(self):
        # A misheard "close" must never quit the app from inside a help panel.
        for phrase in ("got it", "go back"):
            with self.subTest(phrase=phrase):
                self.assertNotEqual(_intent(router.REFERENCE, phrase), router.CLOSE_APP)

    def test_overwrite_needs_an_explicit_spoken_confirmation(self):
        self.assertEqual(
            _intent(router.OVERWRITE_CONFIRM, "confirm overwrite"), router.CONFIRM_OVERWRITE
        )
        self.assertEqual(_intent(router.OVERWRITE_CONFIRM, "cancel"), router.CANCEL)

    def test_a_bare_yes_does_not_confirm_an_overwrite(self):
        for phrase in ("yes", "okay", "do it", "overwrite the original"):
            with self.subTest(phrase=phrase):
                self.assertNotEqual(
                    _intent(router.OVERWRITE_CONFIRM, phrase), router.CONFIRM_OVERWRITE
                )

    def test_confirm_overwrite_is_only_heard_inside_its_own_scope(self):
        for context in (router.SETTINGS, router.HOME, router.READING):
            with self.subTest(context=context):
                self.assertIsNone(_intent(context, "confirm overwrite"))

    def test_modal_vocabularies_do_not_carry_the_screen_grammar_beneath(self):
        self.assertNotIn("theme", router.vocabulary_for(router.OVERWRITE_CONFIRM))
        self.assertNotIn("favorite", router.vocabulary_for(router.REFERENCE))


class ModalScopesAreReallyModalTests(unittest.TestCase):
    """While a modal is up the router itself must refuse screen-level
    globals. They used to be inert only because each page's listener happened
    to swallow them; a page that forgot would navigate or quit behind a dialog."""

    SCREEN_LEVEL = (
        "open a pdf", "open settings", "go to recent", "go home", "quit", "close app", "undo", "redo",
    )

    def test_screen_level_globals_do_not_resolve_inside_a_modal(self):
        for context in router.MODAL_ALLOWED_GLOBALS:
            for phrase in self.SCREEN_LEVEL:
                with self.subTest(context=context, phrase=phrase):
                    self.assertIsNone(_intent(context, phrase))

    def test_the_panel_still_answers_help_and_dictation(self):
        self.assertEqual(_intent(router.REFERENCE, "what can i say"), router.HELP)
        self.assertEqual(_intent(router.REFERENCE, "start search"), router.START_DICTATION)

    def test_the_overwrite_confirmation_allows_no_globals_at_all(self):
        self.assertEqual(router.MODAL_ALLOWED_GLOBALS[router.OVERWRITE_CONFIRM], frozenset())
        self.assertIsNone(_intent(router.OVERWRITE_CONFIRM, "what can i say"))

    def test_a_modal_recognizer_is_not_even_listening_for_quit(self):
        for context in router.MODAL_ALLOWED_GLOBALS:
            vocabulary = set(router.vocabulary_for(context))
            with self.subTest(context=context):
                self.assertNotIn("quit", vocabulary)
                self.assertNotIn("undo", vocabulary)
                self.assertNotIn("home", vocabulary)

    def test_a_modal_still_hears_its_own_phrases(self):
        self.assertEqual(_intent(router.REFERENCE, "got it"), router.CANCEL)
        self.assertEqual(_intent(router.OVERWRITE_CONFIRM, "confirm overwrite"), router.CONFIRM_OVERWRITE)

    def test_the_other_modal_dialogs_are_unchanged(self):
        # Out of scope here: they have always matched globals first.
        self.assertEqual(_intent(router.REMOVE_CONFIRM, "quit"), router.CLOSE_APP)


class EveryPhraseResolvesToItselfTests(unittest.TestCase):
    """Collision guard: with global-first matching and fuzzy tolerance, adding
    a phrase to one table must not make another phrase land on a different
    intent."""

    def test_global_phrases_resolve_to_their_own_intent_everywhere(self):
        for context in OPEN_CONTEXTS:
            for intent, phrases in router.GLOBAL_PHRASES.items():
                for phrase in phrases:
                    with self.subTest(context=context, phrase=phrase):
                        self.assertEqual(_intent(context, phrase), intent)

    def test_scoped_phrases_resolve_to_their_own_intent(self):
        for context, table in SCOPED_TABLES.items():
            for intent, phrases in table.items():
                for phrase in phrases:
                    with self.subTest(context=context, phrase=phrase):
                        self.assertEqual(_intent(context, phrase), intent)


class VoiceCannotSwitchItselfOffTests(unittest.TestCase):
    """Voice turning off voice would lock out a reader who has no way back."""

    PHRASES = (
        "turn off voice", "voice off", "disable voice", "stop listening",
        "turn off push to talk", "turn off the wake phrase", "mute", "stop voice",
    )

    def test_no_phrase_for_it_resolves_in_any_context(self):
        for context in router.CONTEXTS:
            for phrase in self.PHRASES:
                with self.subTest(context=context, phrase=phrase):
                    self.assertIsNone(_intent(context, phrase))

    def test_no_intent_is_named_for_it(self):
        intents = set(router.GLOBAL_PHRASES)
        for table in SCOPED_TABLES.values():
            intents |= set(table)
        for intent in intents:
            with self.subTest(intent=intent):
                self.assertNotIn("VOICE", intent)
                self.assertNotIn("MUTE", intent)


class GrammarSwapTests(unittest.TestCase):
    """Per-screen grammar swap: `Api` hands the recognizer the new context's
    vocabulary whenever the screen or modal changes."""

    def setUp(self):
        # An isolated settings file, so building `Api` neither reads the
        # developer's real preferences nor starts wake listening on their mic.
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api._voice = mock.MagicMock()

    def _applied(self) -> set[str]:
        return set(self.api._voice.set_vocabulary.call_args[0][0])

    def test_each_screen_gets_its_own_grammar(self):
        self.api.set_voice_context(router.HOME)
        home = self._applied()
        self.api.set_voice_context(router.SETTINGS)
        settings = self._applied()

        self.assertIn("favorite", home)
        self.assertNotIn("favorite", settings)
        self.assertIn("theme", settings)
        self.assertNotIn("theme", home)

    def test_globals_survive_every_swap(self):
        for context in (router.HOME, router.SETTINGS, router.READING):
            self.api.set_voice_context(context)
            self.assertTrue({"pdf", "settings", "recent"} <= self._applied())

    def test_push_swaps_to_the_modal_grammar_and_pop_restores_the_screen(self):
        self.api.set_voice_context(router.SETTINGS)
        before = self._applied()

        pushed = self.api.push_voice_context(router.REFERENCE)
        during = self._applied()
        popped = self.api.pop_voice_context()
        after = self._applied()

        self.assertEqual(pushed["context"], router.REFERENCE)
        self.assertNotEqual(during, before)
        self.assertEqual(popped["context"], router.SETTINGS)
        self.assertEqual(after, before)

    def test_modals_nest_and_unwind_in_order(self):
        self.api.set_voice_context(router.HOME)
        self.api.push_voice_context(router.REFERENCE)
        self.api.push_voice_context(router.DICTATION)

        self.assertEqual(self.api.pop_voice_context()["context"], router.REFERENCE)
        self.assertEqual(self.api.pop_voice_context()["context"], router.HOME)

    def test_popping_with_nothing_pushed_leaves_the_context_alone(self):
        self.api.set_voice_context(router.SETTINGS)

        self.assertEqual(self.api.pop_voice_context()["context"], router.SETTINGS)

    def test_a_screen_declaring_itself_clears_stale_modals(self):
        # A page that navigates away mid-modal must not leave the next page
        # popping into the previous page's context.
        self.api.set_voice_context(router.HOME)
        self.api.push_voice_context(router.REFERENCE)

        self.api.set_voice_context(router.SETTINGS)

        self.assertEqual(self.api.pop_voice_context()["context"], router.SETTINGS)

    def test_pushing_an_unknown_context_is_rejected_and_changes_nothing(self):
        self.api.set_voice_context(router.HOME)

        with self.assertRaises(ValueError):
            self.api.push_voice_context("nonsense")

        self.assertEqual(self.api.pop_voice_context()["context"], router.HOME)

    def test_dictation_pushed_from_a_modal_returns_to_that_modal(self):
        self.api.set_voice_context(router.HOME)
        self.api.push_voice_context(router.REFERENCE)
        self.api.push_voice_context(router.DICTATION)
        self.api._voice.set_vocabulary.assert_called_with(None)

        self.assertEqual(self.api.pop_voice_context()["context"], router.REFERENCE)


if __name__ == "__main__":
    unittest.main()
