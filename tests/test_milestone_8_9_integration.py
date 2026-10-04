"""Integration tests for Milestone 8.9, exercised through the real `Api`.

Covers the three pieces this milestone adds: the backend `remove_recent_file`
list-entry removal (never touching the file on disk), the `HOME`-context
REMOVE_RECENT/REMOVE_PICKER intents that open the confirmation dialog, the
dialog's own `REMOVE_CONFIRM` context (CONFIRM_REMOVE/CANCEL), and the
"Save changes?" dialog's own `SAVE_CONFIRM` context (SAVE_COPY/SAVE_OVERWRITE/
DONT_SAVE/CANCEL) — distinct from the pre-existing `SAVE_DIALOG` context,
which belongs to the Save-As folder browser (Milestone 8.7).

Follows the same pattern as `test_milestone_8_4_integration.py`,
`test_milestone_8_7_integration.py`, and `test_milestone_8_8_integration.py`:
`Api()` is real, and only the two seams that need an actual window or an
actual settings file on disk are mocked.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import router  # noqa: E402


class RemoveRecentFileBackendTests(unittest.TestCase):
    """`store.remove_recent_file` in isolation — list-entry removal only,
    per docs/PRD.md's scope note."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_removes_only_the_named_entry(self):
        store.add_recent_file("/docs/a.pdf")
        store.add_recent_file("/docs/b.pdf")

        store.remove_recent_file("/docs/a.pdf")

        paths = [e["path"] for e in store.get_recent_files()]
        self.assertEqual(paths, ["/docs/b.pdf"])

    def test_is_a_no_op_when_the_path_is_not_present(self):
        store.add_recent_file("/docs/a.pdf")

        store.remove_recent_file("/docs/does-not-exist.pdf")

        paths = [e["path"] for e in store.get_recent_files()]
        self.assertEqual(paths, ["/docs/a.pdf"])

    def test_never_touches_the_file_on_disk(self):
        # The whole point of this being a settings-list operation rather than
        # a filesystem one: a path that was never a real file removes from
        # Recent exactly the same way a real one would.
        store.add_recent_file("/docs/never-existed-on-disk.pdf")

        store.remove_recent_file("/docs/never-existed-on-disk.pdf")

        self.assertEqual(store.get_recent_files(), [])
        self.assertFalse(Path("/docs/never-existed-on-disk.pdf").exists())


class ApiRemoveRecentFileTests(unittest.TestCase):
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

    def test_returns_the_refreshed_list_so_the_caller_needs_no_second_round_trip(self):
        store.add_recent_file("/docs/a.pdf")
        store.add_recent_file("/docs/b.pdf")

        result = self.api.remove_recent_file("/docs/a.pdf")

        self.assertEqual([e["path"] for e in result["entries"]], ["/docs/b.pdf"])
        self.assertEqual(result["entries"], self.api.get_recent_files())
        self.assertTrue(result["removed"])


class RemoveConfirmContextRouterTests(unittest.TestCase):
    """`router.resolve` for HOME's new intents and the new `REMOVE_CONFIRM`
    context, in isolation."""

    def test_remove_recent_resolves_via_either_of_its_two_phrasings(self):
        for phrase in ("remove recent", "delete recent"):
            with self.subTest(phrase=phrase):
                result = router.resolve(router.HOME, phrase)
                self.assertEqual(result["command"]["intent"], router.REMOVE_RECENT)

    def test_remove_picker_resolves_via_either_of_its_two_phrasings(self):
        for phrase in ("remove a file", "delete a file"):
            with self.subTest(phrase=phrase):
                result = router.resolve(router.HOME, phrase)
                self.assertEqual(result["command"]["intent"], router.REMOVE_PICKER)

    def test_confirm_remove_resolves_in_the_remove_confirm_context(self):
        for phrase in ("remove it", "confirm remove"):
            with self.subTest(phrase=phrase):
                result = router.resolve(router.REMOVE_CONFIRM, phrase)
                self.assertEqual(result["command"]["intent"], router.CONFIRM_REMOVE)

    def test_cancel_resolves_in_the_remove_confirm_context(self):
        result = router.resolve(router.REMOVE_CONFIRM, "never mind")
        self.assertEqual(result["command"]["intent"], router.CANCEL)

    def test_a_global_command_still_resolves_in_the_remove_confirm_context(self):
        result = router.resolve(router.REMOVE_CONFIRM, "undo")
        self.assertEqual(result["command"]["intent"], router.UNDO)

    def test_home_only_phrases_do_not_resolve_in_the_remove_confirm_context(self):
        result = router.resolve(router.REMOVE_CONFIRM, "remove recent")
        self.assertIsNone(result["command"])

    def test_remove_confirm_matching_has_no_near_miss_clarification_tier(self):
        result = router.resolve(router.REMOVE_CONFIRM, "gibberish that matches nothing")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class SaveConfirmContextRouterTests(unittest.TestCase):
    """`router.resolve` for the "Save changes?" dialog's own `SAVE_CONFIRM`
    context — distinct from `SAVE_DIALOG`, the Save-As folder browser's
    context (Milestone 8.7)."""

    def test_save_copy_resolves_in_the_save_confirm_context(self):
        result = router.resolve(router.SAVE_CONFIRM, "save a copy")
        self.assertEqual(result["command"]["intent"], router.SAVE_COPY)

    def test_save_overwrite_resolves_in_the_save_confirm_context(self):
        result = router.resolve(router.SAVE_CONFIRM, "overwrite the original")
        self.assertEqual(result["command"]["intent"], router.SAVE_OVERWRITE)

    def test_dont_save_resolves_via_either_of_its_two_phrasings(self):
        for phrase in ("don't save", "do not save"):
            with self.subTest(phrase=phrase):
                result = router.resolve(router.SAVE_CONFIRM, phrase)
                self.assertEqual(result["command"]["intent"], router.DONT_SAVE)

    def test_cancel_resolves_in_the_save_confirm_context(self):
        result = router.resolve(router.SAVE_CONFIRM, "never mind")
        self.assertEqual(result["command"]["intent"], router.CANCEL)

    def test_save_confirm_phrases_do_not_resolve_in_save_dialog(self):
        # SAVE_DIALOG is the Save-As folder browser (Milestone 8.7) — a
        # different dialog with a different grammar (row numbers, DIALOG_UP,
        # DIALOG_CONFIRM), despite the shared "save"-flavored vocabulary.
        result = router.resolve(router.SAVE_DIALOG, "save a copy")
        self.assertIsNone(result["command"])

    def test_save_confirm_matching_has_no_near_miss_clarification_tier(self):
        result = router.resolve(router.SAVE_CONFIRM, "gibberish that matches nothing")
        self.assertIsNone(result["command"])
        self.assertIsNone(result["clarify"])


class ApiVoiceContextSwitchingTests(unittest.TestCase):
    """`Api.set_voice_context`/`_on_voice_result` actually wired to the two
    new contexts, mirroring `test_milestone_8_4_integration.py`."""

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

        self.window = mock.Mock()
        patcher = mock.patch.object(api_module.webview, "windows", [self.window])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dispatched_detail(self) -> dict:
        self.window.evaluate_js.assert_called_once()
        script = self.window.evaluate_js.call_args[0][0]
        start = script.index("detail: ") + len("detail: ")
        payload, _ = json.JSONDecoder().raw_decode(script[start:])
        return payload

    def _speak(self, text: str) -> dict:
        self.api._on_voice_result(
            {"text": text, "final": True, "wake": False, "alternatives": []}
        )
        return self._dispatched_detail()

    def test_remove_confirm_is_a_recognized_context(self):
        result = self.api.set_voice_context(router.REMOVE_CONFIRM)
        self.assertEqual(result, {"context": router.REMOVE_CONFIRM})

    def test_save_confirm_is_a_recognized_context(self):
        result = self.api.set_voice_context(router.SAVE_CONFIRM)
        self.assertEqual(result, {"context": router.SAVE_CONFIRM})

    def test_confirm_remove_resolves_once_switched_to_remove_confirm(self):
        self.api.set_voice_context(router.REMOVE_CONFIRM)

        detail = self._speak("confirm remove")

        self.assertEqual(detail["command"]["intent"], router.CONFIRM_REMOVE)

    def test_save_overwrite_resolves_once_switched_to_save_confirm(self):
        self.api.set_voice_context(router.SAVE_CONFIRM)

        detail = self._speak("overwrite the original")

        self.assertEqual(detail["command"]["intent"], router.SAVE_OVERWRITE)

    def test_switching_back_to_home_restores_the_home_grammar(self):
        self.api.set_voice_context(router.REMOVE_CONFIRM)
        self.api.set_voice_context(router.HOME)

        detail = self._speak("remove recent")

        self.assertEqual(detail["command"]["intent"], router.REMOVE_RECENT)


if __name__ == "__main__":
    unittest.main()
