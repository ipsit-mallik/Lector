"""Integration tests for Milestone 8.12, per screen.

Home, Settings and Reading each get the global voice commands; Home gets
"open number N" and an empty state whose "TRY SAYING" chips are read from the
command registry; Settings gets a spoken confirmation for "overwrite the
original"; "What can I say?" is a modal scope on all three.

Two kinds of check, because the failure this milestone fixes is a wiring one
(a phrase the grammar knew that no page acted on):

* **Wiring** — every intent the router can send to a screen has a handler in
  that screen's `VOICE_ACTIONS`, read straight from the page's JavaScript.
* **Flows** — a real `Api` with a recognizer result injected, to check what a
  spoken phrase turns into on each screen and across modal scopes.

Follows `test_milestone_8_9_integration.py`: `Api()` is real, and only the
seams needing a window or a settings file on disk are mocked.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import reference, router  # noqa: E402

FRONTEND = ROOT / "frontend"
HOME_JS = (FRONTEND / "pages" / "home.js").read_text(encoding="utf-8")
SETTINGS_JS = (FRONTEND / "pages" / "settings.js").read_text(encoding="utf-8")
READING_JS = (FRONTEND / "pages" / "reading.js").read_text(encoding="utf-8")
REFERENCE_JS = (FRONTEND / "pages" / "command-reference.js").read_text(encoding="utf-8")
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
SETTINGS_HTML = (FRONTEND / "pages" / "settings.html").read_text(encoding="utf-8")
READING_HTML = (FRONTEND / "pages" / "reading.html").read_text(encoding="utf-8")
ONBOARDING_HTML = (FRONTEND / "pages" / "onboarding.html").read_text(encoding="utf-8")
VOICE_JS = (FRONTEND / "js" / "voice.js").read_text(encoding="utf-8")
HOME_CSS = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")

GLOBAL_NAVIGATION = (router.OPEN_PDF, router.GO_RECENT, router.OPEN_SETTINGS, router.HELP)


def voice_actions_block(js: str) -> str:
    block = re.search(r"const VOICE_ACTIONS = \{(.*?)\n\};", js, flags=re.S)
    assert block, "no VOICE_ACTIONS table found"
    return block.group(1)


def reference_test_phrase(phrase: str) -> str:
    """A phrase from the list with its variable filled in, to say it for real."""
    return (
        phrase.replace("{page}", "12").replace("{number}", "3").replace("{words}", "the quick brown fox")
    )


def handled_intents(js: str) -> set[str]:
    """The intents a page's `VOICE_ACTIONS` table has a handler for."""
    return set(re.findall(r"^\s+([A-Z][A-Z_]+):", voice_actions_block(js), flags=re.M))


class WiringTests(unittest.TestCase):
    """Every intent the router can deliver to a screen is handled there."""

    def test_every_screen_handles_the_global_navigation_commands(self):
        for name, js in (("home", HOME_JS), ("settings", SETTINGS_JS), ("reading", READING_JS)):
            for intent in (*GLOBAL_NAVIGATION, router.CLOSE_APP):
                with self.subTest(screen=name, intent=intent):
                    self.assertIn(intent, handled_intents(js))

    def test_home_handles_all_of_its_own_commands(self):
        scoped = set(router.HOME_PHRASES) | {router.OPEN_NUMBER}
        self.assertLessEqual(scoped, handled_intents(HOME_JS))

    def test_settings_handles_all_of_its_own_commands(self):
        self.assertLessEqual(set(router.SETTINGS_PHRASES), handled_intents(SETTINGS_JS))

    def test_every_chip_is_something_home_actually_does(self):
        # "Never show a chip for a command that doesn't actually work."
        for phrase in reference.try_saying(router.HOME):
            intent = router.resolve(router.HOME, phrase)["command"]["intent"]
            with self.subTest(phrase=phrase, intent=intent):
                self.assertIn(intent, handled_intents(HOME_JS))

    def test_every_panel_phrase_is_handled_on_the_screen_that_shows_it(self):
        for context, js in (
            (router.HOME, HOME_JS), (router.SETTINGS, SETTINGS_JS), (router.READING, READING_JS),
        ):
            # Only the sections that work on this screen: the list shows every
            # section everywhere (and says where the others work), so a Home-only
            # command being unhandled by Settings is correct and not a gap.
            for section in reference.sections(context):
                if not section["available"]:
                    continue
                for command in section["commands"]:
                    for phrase in command["phrases"]:
                        text = reference_test_phrase(phrase)
                        intent = router.resolve(context, text)["command"]["intent"]
                        with self.subTest(context=context, phrase=phrase, intent=intent):
                            self.assertIn(intent, handled_intents(js))

    def test_no_screen_can_switch_voice_off_by_voice(self):
        for name, js in (("home", HOME_JS), ("settings", SETTINGS_JS), ("reading", READING_JS)):
            with self.subTest(screen=name):
                self.assertNotIn("voice_activation", voice_actions_block(js))
                self.assertNotIn("VOICE", " ".join(handled_intents(js)))


class SharedScriptWiringTests(unittest.TestCase):
    def test_home_and_settings_load_the_shared_voice_and_help_scripts(self):
        for name, html in (("home", INDEX_HTML), ("settings", SETTINGS_HTML)):
            for script in ("voice-box.js", "filedrop.js", "command-reference.js"):
                with self.subTest(screen=name, script=script):
                    self.assertIn(script, html)

    def test_reading_loads_drop_handling(self):
        self.assertIn("filedrop.js", READING_HTML)

    def test_the_help_panel_markup_lives_in_one_place(self):
        self.assertIn('id="referenceDialog"', REFERENCE_JS)
        for name, html in (("home", INDEX_HTML), ("settings", SETTINGS_HTML), ("reading", READING_HTML)):
            with self.subTest(screen=name):
                self.assertNotIn('id="referenceDialog"', html)

    def test_the_help_panel_scopes_voice_through_the_serialized_scope(self):
        self.assertIn('createVoiceScope("reference")', REFERENCE_JS)
        self.assertIn('createVoiceScope("dictation")', REFERENCE_JS)
        # Dictation used to return to a hardcoded "reading"; it must not now.
        self.assertNotIn('"set_voice_context", "reading"', REFERENCE_JS)

    def test_no_page_pushes_or_pops_voice_context_directly(self):
        # A raw push followed by a pop raced: a quick close could pop before the
        # push landed and leave the screen's own commands dead. Only
        # createVoiceScope (which serializes them) may touch the bridge calls.
        for name, js in (("command-reference.js", REFERENCE_JS), ("settings.js", SETTINGS_JS),
                         ("home.js", HOME_JS), ("reading.js", READING_JS)):
            with self.subTest(file=name):
                self.assertNotIn('callApi("push_voice_context"', js)
                self.assertNotIn('callApi("pop_voice_context"', js)
        self.assertIn('callApi("push_voice_context"', VOICE_JS)
        self.assertIn('callApi("pop_voice_context"', VOICE_JS)

    def test_every_scope_shares_one_queue_so_nested_pops_stay_in_order(self):
        self.assertEqual(VOICE_JS.count("let voiceScopeQueue"), 1)

    def test_settings_overwrite_confirmation_uses_the_serialized_scope(self):
        self.assertIn('createVoiceScope("overwrite_confirm")', SETTINGS_JS)

    def test_overwrite_confirmation_does_not_focus_a_button(self):
        # A keyboard-focused button owns Space (voice.js), which would swallow
        # the spoken "confirm overwrite" and press Cancel on release.
        self.assertNotIn("overwriteConfirmCancelBtn.focus()", SETTINGS_JS)
        self.assertNotIn("overwriteConfirmBtn.focus()", SETTINGS_JS)
        self.assertIn("overwriteConfirmCard.focus()", SETTINGS_JS)
        self.assertIn('tabindex="-1"', SETTINGS_HTML)

    def test_a_dropped_file_cannot_navigate_any_page_away(self):
        # filedrop.js is what cancels `dragover`; a page without it lets the
        # WebView navigate to a dropped file.
        for name, html in (("home", INDEX_HTML), ("settings", SETTINGS_HTML),
                           ("reading", READING_HTML), ("onboarding", ONBOARDING_HTML)):
            with self.subTest(screen=name):
                self.assertIn("filedrop.js", html)

    def test_every_screen_has_a_mouse_route_to_what_can_i_say(self):
        self.assertIn('id="voiceHelpBtn"', INDEX_HTML)
        self.assertIn('id="voiceHelpBtn"', SETTINGS_HTML)
        self.assertIn("voiceHelpBtn", HOME_JS)
        self.assertIn("voiceHelpBtn", SETTINGS_JS)


class VoiceStatusHonestyTests(unittest.TestCase):
    def test_no_page_hardcodes_voice_ready_before_the_engine_answers(self):
        for name, html in (("home", INDEX_HTML), ("settings", SETTINGS_HTML)):
            with self.subTest(screen=name):
                self.assertNotIn("VOICE READY", html)
                self.assertIn("CHECKING VOICE", html)

    def test_a_failed_voice_scope_is_reported_not_papered_over(self):
        for name, js in (("home", HOME_JS), ("settings", SETTINGS_JS)):
            with self.subTest(screen=name):
                self.assertIn("showUnavailable", js)


class EmptyStateTests(unittest.TestCase):
    def test_the_card_is_solid_not_dashed(self):
        rule = re.search(r"\.empty-state \{(.*?)\}", HOME_CSS, flags=re.S).group(1)
        self.assertNotIn("dashed", rule)
        self.assertNotIn("dotted", rule)

    def test_the_chips_are_not_hardcoded_anywhere_in_the_frontend(self):
        # They must come from the registry, so no page or stylesheet may carry
        # the phrases as literals.
        for phrase in ("open a PDF", "what can I say", "open settings"):
            for name, text in (("home.js", HOME_JS), ("index.html", INDEX_HTML), ("home.css", HOME_CSS)):
                # Comments may quote a phrase; only code can hardcode one.
                text = re.sub(r"/\*.*?\*/|<!--.*?-->", "", text, flags=re.S)
                text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
                with self.subTest(phrase=phrase, file=name):
                    self.assertNotIn(f"“{phrase}”", text)
                    self.assertNotIn(f'"{phrase}"', text)

    def test_the_chips_are_read_from_the_registry(self):
        self.assertIn("try_saying", HOME_JS)
        self.assertIn("get_command_reference", HOME_JS)

    def test_the_card_has_every_element_in_the_mockup(self):
        for needle in (
            "empty-state-icon", "empty-state-open", "or drop a file anywhere in this window",
            "empty-state-voice", "Try saying", "empty-state-chips",
        ):
            with self.subTest(element=needle):
                self.assertIn(needle, HOME_JS)

    def test_the_card_works_at_narrow_widths_and_in_every_theme_through_tokens_only(self):
        self.assertIn("@media (max-width: 640px)", HOME_CSS)
        block = HOME_CSS[HOME_CSS.index(".empty-state {"):HOME_CSS.index(".voice-box.starting")]
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", block), "a hardcoded colour")
        self.assertIsNone(re.search(r"rgba?\(", block), "a hardcoded colour")


class UxReviewTests(unittest.TestCase):
    """Fixes from the UI/UX review of the 8.12 front end."""

    def test_the_voice_box_has_one_atomic_status_region_on_both_screens(self):
        for name, html in (("home", INDEX_HTML), ("settings", SETTINGS_HTML)):
            with self.subTest(screen=name):
                self.assertEqual(html.count('id="voiceLive"'), 1)
                self.assertRegex(html, r'<[^>]*id="voiceLive"[^>]*role="status"[^>]*aria-atomic="true"')
                self.assertIn('class="sr-only"', html)

    def test_both_pages_hand_the_region_to_the_voice_box(self):
        for name, js in (("home", HOME_JS), ("settings", SETTINGS_JS)):
            with self.subTest(screen=name):
                self.assertIn('live: document.getElementById("voiceLive")', js)

    def test_the_visible_voice_text_is_not_itself_a_live_region(self):
        # Partial transcripts arrive many times a second; only the summary
        # region may announce.
        for name, html in (("home", INDEX_HTML), ("settings", SETTINGS_HTML)):
            with self.subTest(screen=name):
                self.assertNotIn("aria-live", html.replace('id="voiceLive"', ""))

    def test_the_sr_only_utility_exists_in_the_shared_css(self):
        components = (FRONTEND / "shared" / "components.css").read_text(encoding="utf-8")
        self.assertRegex(components, r"\.sr-only\s*\{[^}]*position:\s*absolute")

    def test_favorites_empty_state_offers_a_way_to_recent(self):
        self.assertIn("empty-state-action", HOME_JS)
        self.assertIn('setHomeSection("recent")', HOME_JS)
        self.assertIn("Go to Recent", HOME_JS)

    def test_recents_empty_state_does_not_grow_a_go_to_recent_button(self):
        # Already on Recent; the button is Favorites-only.
        block = HOME_JS[HOME_JS.index("function buildEmptyState"):]
        block = block[:block.index("\n}\n")]
        # The Favorites-only early branch has to come before the button, which
        # is built only for sections other than Recent.
        self.assertIn("Go to Recent", block)
        self.assertLess(block.index('section !== "recent"'), block.index("Go to Recent"))

    def test_the_what_can_i_say_button_meets_the_24px_target_minimum(self):
        rule = re.search(r"\.voice-help-btn \{(.*?)\}", HOME_CSS, flags=re.S).group(1)
        self.assertRegex(rule, r"min-height:\s*24px")

    def test_the_chips_do_not_look_like_buttons(self):
        rule = re.search(r"\.empty-state-chips li \{(.*?)\}", HOME_CSS, flags=re.S).group(1)
        self.assertNotRegex(rule, r"border:\s*1px")
        self.assertNotIn("cursor: pointer", rule)
        self.assertIn("cursor: default", rule)


class FlowTests(unittest.TestCase):
    """A spoken phrase through the real `Api`, on each screen and across modals."""

    def setUp(self):
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

        self.window = mock.Mock()
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hear(self, text: str) -> dict | None:
        """Inject a final recognition result; return the command the page got."""
        self.window.evaluate_js.reset_mock()
        self.api._on_voice_result({"final": True, "text": text, "alternatives": []})
        script = self.window.evaluate_js.call_args[0][0]
        marker = "{detail: "
        detail = json.loads(script[script.index(marker) + len(marker):].removesuffix("}))"))
        return detail["command"]

    def test_home_hears_the_globals_and_open_number(self):
        self.api.set_voice_context(router.HOME)

        self.assertEqual(self._hear("open a pdf")["intent"], router.OPEN_PDF)
        self.assertEqual(self._hear("go to recent")["intent"], router.GO_RECENT)
        self.assertEqual(self._hear("open settings")["intent"], router.OPEN_SETTINGS)
        command = self._hear("open number two")
        self.assertEqual((command["intent"], command["index"]), (router.OPEN_NUMBER, 2))

    def test_settings_hears_the_globals_and_its_own_commands_but_not_homes(self):
        self.api.set_voice_context(router.SETTINGS)

        self.assertEqual(self._hear("open a pdf")["intent"], router.OPEN_PDF)
        self.assertEqual(self._hear("go to recent")["intent"], router.GO_RECENT)
        self.assertEqual(self._hear("dark theme")["intent"], router.THEME_DARK)
        self.assertEqual(self._hear("start at the beginning")["intent"], router.REOPEN_START)
        self.assertIsNone(self._hear("open number two"))
        self.assertIsNone(self._hear("favorite recent"))

    def test_reading_hears_the_globals_alongside_its_own_grammar(self):
        self.api.set_voice_context(router.READING)

        self.assertEqual(self._hear("open settings")["intent"], router.OPEN_SETTINGS)
        self.assertEqual(self._hear("next page")["intent"], "NEXT_PAGE")

    def test_overwrite_by_voice_needs_a_spoken_confirmation_and_scope_is_restored(self):
        self.api.set_voice_context(router.SETTINGS)
        self.assertEqual(self._hear("overwrite the original")["intent"], router.SAVE_OVERWRITE)

        # The page opens its confirmation and pushes the modal scope.
        self.api.push_voice_context(router.OVERWRITE_CONFIRM)
        self.assertIsNone(self._hear("overwrite the original"))
        self.assertIsNone(self._hear("dark theme"))
        self.assertEqual(self._hear("confirm overwrite")["intent"], router.CONFIRM_OVERWRITE)

        # Confirmed or cancelled, the page pops and Settings is listening again.
        self.api.pop_voice_context()
        self.assertIsNone(self._hear("confirm overwrite"))
        self.assertEqual(self._hear("dark theme")["intent"], router.THEME_DARK)

    def test_hearing_overwrite_does_not_change_the_setting_by_itself(self):
        # The router only reports the intent; the page decides. Nothing on the
        # Python side writes the save behaviour as a side effect of hearing it.
        before = store.get_save_behavior()
        self.api.set_voice_context(router.SETTINGS)

        self._hear("overwrite the original")

        self.assertEqual(store.get_save_behavior(), before)

    def test_cancelling_the_confirmation_is_heard_inside_its_scope(self):
        self.api.set_voice_context(router.SETTINGS)
        self.api.push_voice_context(router.OVERWRITE_CONFIRM)

        self.assertEqual(self._hear("never mind")["intent"], router.CANCEL)

    def test_the_help_panel_is_a_modal_scope_on_every_screen(self):
        for screen, behind in (
            (router.HOME, "open number two"),
            (router.SETTINGS, "dark theme"),
            (router.READING, "next page"),
        ):
            with self.subTest(screen=screen):
                self.api.set_voice_context(screen)
                self.assertIsNotNone(self._hear(behind))

                self.api.push_voice_context(router.REFERENCE)
                self.assertIsNone(self._hear(behind), "the screen beneath is still listening")
                self.assertEqual(self._hear("got it")["intent"], router.CANCEL)

                self.api.pop_voice_context()
                self.assertIsNotNone(self._hear(behind), "the screen's grammar was not restored")

    def test_dictation_inside_the_panel_returns_to_the_panel_then_the_screen(self):
        self.api.set_voice_context(router.SETTINGS)
        self.api.push_voice_context(router.REFERENCE)
        self.assertEqual(self._hear("start search")["intent"], router.START_DICTATION)
        self.api.push_voice_context(router.DICTATION)

        self.api.pop_voice_context()
        self.assertEqual(self._hear("got it")["intent"], router.CANCEL)
        self.api.pop_voice_context()
        self.assertEqual(self._hear("dark theme")["intent"], router.THEME_DARK)

    def test_the_registry_payload_is_scoped_to_the_active_screen(self):
        self.api.set_voice_context(router.HOME)
        home = self.api.get_command_reference()
        self.api.set_voice_context(router.SETTINGS)
        settings = self.api.get_command_reference()

        self.assertEqual(home["try_saying"], ["open a PDF", "what can I say", "open settings"])
        self.assertEqual(settings["try_saying"], [])
        # The same sections on every screen; only which come first differs.
        self.assertEqual(
            sorted(s["id"] for s in home["sections"]), sorted(s["id"] for s in settings["sections"])
        )
        self.assertNotEqual(
            [s["id"] for s in home["sections"]], [s["id"] for s in settings["sections"]]
        )


if __name__ == "__main__":
    unittest.main()
