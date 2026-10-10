"""Home lists its files without waiting for their thumbnails.

Measured on a 20-file Recent list with nothing cached, rendering the first-page
thumbnails was about 83% of the list's load time (opening each PDF for its page
count and title was the rest). So `list_recent()`/`list_favorites()` hand back
the rows at once, with a thumbnail only when one is already rendered, and mark
the others `thumbnail_pending`; the page asks for them a few at a time with `get_thumbnails` and
shows a placeholder in its slot meanwhile.

Also here: `page_state()`, what the page server writes into Home's `<html>` so
the page knows its grid/list view and how many files to expect before any
bridge call (frontend_server.with_page_state).

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


class _TempSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)
        recent.clear_caches()
        self.addCleanup(recent.clear_caches)

    def _make_pdf(self, name: str, pages: int = 1) -> str:
        path = str(Path(self.dir.name) / name)
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()
        return path


class ListWithoutThumbnailsTests(_TempSettings):
    def test_the_list_does_not_render_thumbnails(self):
        store.add_recent_file(self._make_pdf("a.pdf", pages=3))

        with mock.patch.object(fitz.Page, "get_pixmap", side_effect=AssertionError("rendered")):
            [entry] = recent.list_recent()

        self.assertIsNone(entry["thumbnail"])
        self.assertTrue(entry["thumbnail_pending"])
        self.assertEqual(entry["page_count"], 3)

    def test_favorites_are_listed_the_same_way(self):
        path = self._make_pdf("a.pdf")
        store.add_recent_file(path)
        store.add_favorite(path)

        with mock.patch.object(fitz.Page, "get_pixmap", side_effect=AssertionError("rendered")):
            [entry] = recent.list_favorites()

        self.assertTrue(entry["thumbnail_pending"])

    def test_a_rendered_thumbnail_is_listed_from_then_on(self):
        path = self._make_pdf("a.pdf")
        store.add_recent_file(path)

        png = recent.load_thumbnail(path)
        [entry] = recent.list_recent()

        self.assertTrue(png)
        self.assertEqual(entry["thumbnail"], png)
        self.assertFalse(entry["thumbnail_pending"])

    def test_a_thumbnail_is_rendered_once_until_the_file_changes(self):
        path = self._make_pdf("a.pdf")
        first = recent.load_thumbnail(path)

        with mock.patch.object(fitz.Page, "get_pixmap", side_effect=AssertionError("rendered")):
            self.assertEqual(recent.load_thumbnail(path), first)

    def test_an_unreadable_file_has_no_thumbnail_to_wait_for(self):
        store.add_recent_file(str(Path(self.dir.name) / "gone.pdf"))

        [entry] = recent.list_recent()

        self.assertIsNone(entry["thumbnail"])
        self.assertFalse(entry["thumbnail_pending"])
        self.assertIsNone(recent.load_thumbnail(entry["path"]))


class GetThumbnailsApiTests(_TempSettings):
    def test_returns_each_listed_files_thumbnail_by_path(self):
        a, b = self._make_pdf("a.pdf"), self._make_pdf("b.pdf")
        store.add_recent_file(a)
        store.add_recent_file(b)

        thumbnails = api_module.Api().get_thumbnails([a, b])

        self.assertEqual(set(thumbnails), {a, b})
        self.assertTrue(thumbnails[a] and thumbnails[b])

    def test_returns_the_thumbnail_of_a_favorite_no_longer_in_recent(self):
        path = self._make_pdf("a.pdf")
        store.add_recent_file(path)
        store.add_favorite(path)
        store.remove_recent_file(path)

        self.assertTrue(api_module.Api().get_thumbnails([path])[path])

    def test_renders_nothing_for_a_path_the_page_does_not_list(self):
        listed, elsewhere = self._make_pdf("a.pdf"), self._make_pdf("elsewhere.pdf")
        store.add_recent_file(listed)

        with mock.patch.object(recent, "load_thumbnail", return_value="png") as load:
            thumbnails = api_module.Api().get_thumbnails([listed, elsewhere])

        self.assertEqual(thumbnails, {listed: "png", elsewhere: None})
        load.assert_called_once_with(listed)


class PageStateTests(_TempSettings):
    def test_defaults_on_a_fresh_install(self):
        self.assertEqual(
            recent.page_state(),
            {"data-recent-view": "grid", "data-recent-count": "0", "data-favorites-count": "0"},
        )

    def test_reports_the_saved_view_and_both_counts(self):
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            store.add_recent_file(f"/docs/{name}")
        store.add_favorite("/docs/a.pdf")
        store.set_recent_view(store.RECENT_VIEW_LIST)

        self.assertEqual(
            recent.page_state(),
            {"data-recent-view": "list", "data-recent-count": "3", "data-favorites-count": "1"},
        )

    def test_does_not_open_any_pdf(self):
        store.add_recent_file(self._make_pdf("a.pdf"))

        with mock.patch.object(fitz, "open", side_effect=AssertionError("opened")):
            recent.page_state()


if __name__ == "__main__":
    unittest.main()
