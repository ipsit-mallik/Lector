"""Favorites: the stored list, the Favorites view's entries, the bridge
methods, and the Home voice phrases (docs/DESIGN_SYSTEM.md, "Favorites").

Every test writes to a temporary settings file, never the real one.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402  # PyMuPDF: builds the throwaway PDFs these tests list

from lector import api as api_module  # noqa: E402
from lector.features.home import recent  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import reference, router  # noqa: E402


class _TempSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def _make_pdf(self, name: str, pages: int = 1) -> str:
        path = str(Path(self.dir.name) / name)
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()
        return path


class FavoritesStoreTests(_TempSettings):
    def test_starts_empty(self):
        self.assertEqual(store.get_favorites(), [])
        self.assertFalse(store.is_favorite("/docs/a.pdf"))

    def test_adding_pins_newest_first_and_dedupes(self):
        self.assertTrue(store.add_favorite("/docs/a.pdf"))
        self.assertTrue(store.add_favorite("/docs/b.pdf"))
        self.assertFalse(store.add_favorite("/docs/a.pdf"))

        self.assertEqual([e["path"] for e in store.get_favorites()], ["/docs/b.pdf", "/docs/a.pdf"])
        self.assertTrue(store.is_favorite("/docs/a.pdf"))

    def test_removing_unpins_only_that_file_and_reports_a_no_op(self):
        store.add_favorite("/docs/a.pdf")
        store.add_favorite("/docs/b.pdf")

        self.assertTrue(store.remove_favorite("/docs/a.pdf"))
        self.assertFalse(store.remove_favorite("/docs/a.pdf"))

        self.assertEqual([e["path"] for e in store.get_favorites()], ["/docs/b.pdf"])

    def test_a_favorite_outlives_its_recent_entry(self):
        store.add_recent_file("/docs/a.pdf")
        store.add_favorite("/docs/a.pdf")

        store.remove_recent_file("/docs/a.pdf")
        for i in range(store.RECENT_FILES_CAP + 1):
            store.add_recent_file(f"/docs/{i:02d}.pdf")

        self.assertTrue(store.is_favorite("/docs/a.pdf"))

    def test_malformed_stored_items_are_skipped_not_raised_on(self):
        store.add_favorite("/docs/a.pdf")
        data = store._load()
        data["favorites"] = ["nope", {"added_at": "x"}, {"path": 5, "added_at": "x"}, *data["favorites"]]
        store._save(data)

        self.assertEqual([e["path"] for e in store.get_favorites()], ["/docs/a.pdf"])

        data["favorites"] = "not-a-list"
        store._save(data)
        self.assertEqual(store.get_favorites(), [])


class FavoritesListingTests(_TempSettings):
    def test_recent_entries_carry_the_favorite_flag(self):
        starred = self._make_pdf("starred.pdf")
        plain = self._make_pdf("plain.pdf")
        store.add_recent_file(starred)
        store.add_recent_file(plain)
        store.add_favorite(starred)

        flags = {e["name"]: e["favorite"] for e in recent.list_recent()}

        self.assertEqual(flags, {"starred": True, "plain": False})

    def test_favorites_view_lists_newest_favorited_first_in_the_recent_shape(self):
        a = self._make_pdf("a.pdf", pages=3)
        b = self._make_pdf("b.pdf")
        store.add_recent_file(a)
        store.add_recent_file(b)
        store.add_favorite(a)
        store.add_favorite(b)

        entries = recent.list_favorites()

        self.assertEqual([e["name"] for e in entries], ["b", "a"])
        self.assertTrue(all(e["favorite"] for e in entries))
        self.assertEqual(set(entries[0]), set(recent.list_recent()[0]))
        self.assertEqual(entries[1]["page_count"], 3)

    def test_last_active_comes_from_recent_and_is_blank_once_it_has_fallen_off(self):
        listed = self._make_pdf("listed.pdf")
        forgotten = self._make_pdf("forgotten.pdf")
        store.add_recent_file(listed)
        store.add_favorite(listed)
        store.add_favorite(forgotten)  # pinned earlier, no longer in Recent

        by_name = {e["name"]: e for e in recent.list_favorites()}

        self.assertEqual(by_name["listed"]["relative_time"], "just now")
        self.assertEqual(by_name["listed"]["opened_at"], store.get_recent_files()[0]["opened_at"])
        self.assertEqual(by_name["forgotten"]["relative_time"], "")
        self.assertEqual(by_name["forgotten"]["opened_at"], "")

    def test_an_unreadable_favorite_is_still_listed(self):
        store.add_favorite("/docs/gone.pdf")

        [entry] = recent.list_favorites()

        self.assertEqual(entry["title"], "gone")
        self.assertEqual(entry["page_count"], 0)


class FavoritesApiTests(_TempSettings):
    def setUp(self):
        super().setUp()
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)

    def test_a_file_in_recent_can_be_pinned_and_unpinned(self):
        path = self._make_pdf("a.pdf")
        store.add_recent_file(path)

        pinned = self.api.set_favorite(path, True)
        listed = self.api.get_favorites()
        unpinned = self.api.set_favorite(path, False)

        self.assertEqual(pinned, {"favorite": True, "error": None})
        self.assertEqual([e["path"] for e in listed], [path])
        self.assertEqual(unpinned, {"favorite": False, "error": None})
        self.assertEqual(self.api.get_favorites(), [])

    def test_an_arbitrary_path_cannot_be_pinned(self):
        result = self.api.set_favorite(self._make_pdf("stranger.pdf"), True)

        self.assertFalse(result["favorite"])
        self.assertIsNotNone(result["error"])
        self.assertEqual(store.get_favorites(), [])

    def test_a_favorite_that_left_recent_can_still_be_unpinned(self):
        path = self._make_pdf("a.pdf")
        store.add_favorite(path)

        result = self.api.set_favorite(path, False)

        self.assertEqual(result, {"favorite": False, "error": None})

    def test_removing_from_recent_leaves_the_favorite_alone(self):
        path = self._make_pdf("a.pdf")
        store.add_recent_file(path)
        self.api.set_favorite(path, True)

        self.api.remove_recent_file(path)

        self.assertEqual([e["path"] for e in self.api.get_favorites()], [path])


def _intent(context: str, phrase: str):
    return (router.resolve(context, phrase).get("command") or {}).get("intent")


class FavoritesVoiceTests(unittest.TestCase):
    PHRASES = {
        router.OPEN_FAVORITES: ("open favorites", "show favorites"),
        router.SHOW_RECENT: ("show recent files",),
        router.FAVORITE_RECENT: ("favorite recent", "star recent"),
        router.FAVORITE_PICKER: ("favorite a file", "star a file"),
    }

    def test_every_phrase_resolves_to_its_own_intent_on_home(self):
        for intent, phrases in self.PHRASES.items():
            for phrase in phrases:
                with self.subTest(phrase=phrase):
                    self.assertEqual(_intent(router.HOME, phrase), intent)

    def test_existing_home_phrases_are_not_taken_over(self):
        existing = {
            router.OPEN_RECENT: ("open recent", "resume reading"),
            router.REMOVE_RECENT: ("remove recent", "delete recent"),
            router.REMOVE_PICKER: ("remove a file", "delete a file"),
            router.OPEN_PICKER: ("pick a file", "choose a file"),
            router.OPEN_SETTINGS: ("open settings",),
        }
        for intent, phrases in existing.items():
            for phrase in phrases:
                with self.subTest(phrase=phrase):
                    self.assertEqual(_intent(router.HOME, phrase), intent)

    def test_favorites_phrases_are_home_only(self):
        for context in (router.READING, router.SETTINGS):
            for phrases in self.PHRASES.values():
                for phrase in phrases:
                    with self.subTest(context=context, phrase=phrase):
                        self.assertNotIn(_intent(context, phrase), self.PHRASES)

    def test_the_help_panel_lists_them(self):
        titles = [c["title"] for c in reference.categories(router.HOME)]

        self.assertIn("Favorites", titles)


if __name__ == "__main__":
    unittest.main()
