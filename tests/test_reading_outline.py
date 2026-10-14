"""The reader's outline panel, progress bar and Settings > Reading, on the
Python side.

* `features/reading/outline.py`: the bookmark tree and the progress bar's
  chapter segments, from `get_toc()` rows.
* `Api.get_outline`, `get_page_thumbnail`, the state's `highlighted_pages`
  (the progress bar's markers, live through undo and redo), and the reading
  preferences: default view, progress bar on/off, the panel left open.
* `Api.open_pdf` opening a never-opened PDF in the default view, and a
  previously opened one in its own.
* The four voice commands, routed in the reader and listed in "What can I say?".

`unittest`, like the rest of the suite. PDFs are built with PyMuPDF in a temp
folder and settings.json is redirected to one, so nothing touches the
developer's own files.
"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402

from lector import api as api_module  # noqa: E402
from lector.features.reading import outline  # noqa: E402
from lector.features.settings import store  # noqa: E402
from lector.features.voice import command_grammar as grammar  # noqa: E402
from lector.features.voice import reference, router  # noqa: E402


class OutlineTreeTests(unittest.TestCase):
    def test_rows_nest_by_level(self):
        toc = [[1, "One", 1], [2, "One.a", 2], [2, "One.b", 3], [1, "Two", 4], [2, "Two.a", 5]]
        tree = outline.outline_tree(toc, 6)
        self.assertEqual([e["title"] for e in tree], ["One", "Two"])
        self.assertEqual([e["title"] for e in tree[0]["children"]], ["One.a", "One.b"])
        self.assertEqual(tree[1]["children"][0]["page_index"], 4)

    def test_pages_become_zero_based_and_unresolvable_ones_none(self):
        tree = outline.outline_tree([[1, "Cover", 1], [1, "Gone", -1], [1, "Past the end", 99]], 5)
        self.assertEqual([e["page_index"] for e in tree], [0, None, None])

    def test_a_level_that_skips_ahead_hangs_under_the_last_open_entry(self):
        tree = outline.outline_tree([[1, "Part", 1], [3, "Deep", 2], [1, "Next", 3]], 3)
        self.assertEqual(tree[0]["children"][0]["title"], "Deep")
        self.assertEqual(tree[1]["title"], "Next")

    def test_titles_are_tidied_and_never_empty(self):
        tree = outline.outline_tree([[1, "  Chapter\n  One ", 1], [1, "", 2]], 2)
        self.assertEqual([e["title"] for e in tree], ["Chapter One", "Untitled"])

    def test_malformed_rows_are_skipped(self):
        self.assertEqual(outline.outline_tree([None, [1], "x", [1, "Ok", 1]], 1)[0]["title"], "Ok")


class ChapterSegmentTests(unittest.TestCase):
    def _tree(self, *pages):
        return [{"title": f"C{p}", "page_index": p, "children": []} for p in pages]

    def test_one_segment_per_top_level_chapter_covering_every_page(self):
        segments = outline.chapter_segments(self._tree(0, 3, 7), 10)
        self.assertEqual(
            [(s["start"], s["end"]) for s in segments], [(0, 2), (3, 6), (7, 9)]
        )

    def test_pages_before_the_first_chapter_are_an_untitled_segment(self):
        segments = outline.chapter_segments(self._tree(2, 5), 8)
        self.assertEqual(segments[0], {"title": None, "start": 0, "end": 1})
        self.assertEqual(segments[-1]["end"], 7)

    def test_chapters_are_put_in_page_order_and_shared_starts_merge(self):
        segments = outline.chapter_segments(self._tree(5, 0, 5), 9)
        self.assertEqual([(s["title"], s["start"]) for s in segments], [("C0", 0), ("C5", 5)])

    def test_no_usable_chapter_means_no_segments(self):
        self.assertEqual(outline.chapter_segments([], 4), [])
        tree = [{"title": "x", "page_index": None, "children": []}]
        self.assertEqual(outline.chapter_segments(tree, 4), [])


class _ApiCase(unittest.TestCase):
    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        patcher = mock.patch.object(
            store, "_settings_path", lambda: Path(self.settings_dir.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.settings_dir.cleanup)
        self.pdf_dir = tempfile.TemporaryDirectory(prefix="lector-pdfs-")
        self.addCleanup(self.pdf_dir.cleanup)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)

    def make_pdf(self, name: str, pages: int = 6, toc: list | None = None) -> str:
        path = str(Path(self.pdf_dir.name) / name)
        doc = fitz.open()
        for i in range(pages):
            page = doc.new_page(width=300, height=400)
            page.insert_text((40, 60), f"Page {i + 1} quick brown fox", fontsize=12)
        if toc:
            doc.set_toc(toc)
        doc.save(path)
        doc.close()
        return path


class OutlineAndThumbnailApiTests(_ApiCase):
    def test_outline_and_chapters_for_a_pdf_with_bookmarks(self):
        path = self.make_pdf("book.pdf", 6, [[1, "Intro", 1], [2, "Why", 2], [1, "Body", 4]])
        self.api.open_pdf(path)
        result = self.api.get_outline()
        self.assertEqual([e["title"] for e in result["entries"]], ["Intro", "Body"])
        self.assertEqual(result["entries"][0]["children"][0]["page_index"], 1)
        self.assertEqual(
            [(c["title"], c["start"], c["end"]) for c in result["chapters"]],
            [("Intro", 0, 2), ("Body", 3, 5)],
        )

    def test_a_pdf_without_an_outline_has_none(self):
        self.api.open_pdf(self.make_pdf("plain.pdf"))
        self.assertEqual(self.api.get_outline(), {"entries": [], "chapters": []})

    def test_nothing_open_has_no_outline(self):
        self.assertEqual(self.api.get_outline(), {"entries": [], "chapters": []})

    def test_a_thumbnail_is_the_width_asked_for_whatever_the_zoom(self):
        self.api.open_pdf(self.make_pdf("thumb.pdf"))
        self.api.set_zoom(2.5)
        thumb = self.api.get_page_thumbnail(2, 120)
        self.assertEqual(thumb["width"], 120)
        self.assertEqual(thumb["height"], 160)  # the page's 3:4
        self.assertTrue(base64.b64decode(thumb["image"]).startswith(b"\x89PNG"))

    def test_a_thumbnail_width_is_clamped_and_a_missing_page_is_empty(self):
        self.api.open_pdf(self.make_pdf("clamp.pdf"))
        self.assertEqual(self.api.get_page_thumbnail(0, 100000)["width"], api_module.MAX_THUMBNAIL_PX)
        self.assertEqual(self.api.get_page_thumbnail(0, 1)["width"], api_module.MIN_THUMBNAIL_PX)
        self.assertIsNone(self.api.get_page_thumbnail(99, 120)["image"])


class HighlightedPagesTests(_ApiCase):
    """The progress bar's markers come from the state every highlight change
    already returns, so they follow adds, undo, redo and removal live."""

    def setUp(self):
        super().setUp()
        self.api.open_pdf(self.make_pdf("marks.pdf"))

    def _highlight(self, page_index: int) -> dict:
        return self.api.highlight_chars(page_index, 0, 3)

    def test_a_new_document_has_no_marked_pages(self):
        self.assertEqual(self.api.get_state()["highlighted_pages"], [])

    def test_highlighted_pages_are_listed_in_order_before_saving(self):
        self._highlight(4)
        self._highlight(1)
        state = self._highlight(2)
        self.assertEqual(state["highlighted_pages"], [1, 2, 4])
        self.assertTrue(state["is_dirty"])

    def test_undo_and_redo_move_the_markers(self):
        self._highlight(3)
        self.assertEqual(self.api.undo_highlight()["highlighted_pages"], [])
        self.assertEqual(self.api.redo_highlight()["highlighted_pages"], [3])

    def test_removing_the_last_highlight_on_a_page_drops_its_marker(self):
        self._highlight(0)
        state = self._highlight(0)
        self.assertEqual(state["highlighted_pages"], [0])
        ids = [h["id"] for h in self.api.get_page_highlights(0)["highlights"]]
        self.api.remove_highlight(0, ids[0])
        self.assertEqual(self.api.remove_highlight(0, ids[1])["highlighted_pages"], [])

    def test_counts_still_agree_with_the_markers(self):
        self._highlight(1)
        state = self._highlight(1)
        self.assertEqual(state["highlight_count_total"], 2)


class ReadingPreferenceTests(_ApiCase):
    def test_defaults(self):
        prefs = self.api.get_reading_preferences()
        self.assertEqual(prefs["default_view"], store.BOOK)
        self.assertTrue(prefs["progress_bar"])
        self.assertFalse(prefs["outline_open"])
        self.assertEqual(prefs["highlight_color"], "yellow")
        self.assertEqual(len(prefs["highlight_palette"]), 5)

    def test_each_preference_is_stored(self):
        self.api.set_default_view(store.STRIP)
        self.api.set_progress_bar(False)
        prefs = self.api.set_outline_open(True)
        self.assertEqual(prefs["default_view"], store.STRIP)
        self.assertFalse(prefs["progress_bar"])
        self.assertTrue(prefs["outline_open"])
        self.assertTrue(store.get_outline_open())

    def test_an_unknown_view_is_ignored_at_the_bridge(self):
        self.assertEqual(self.api.set_default_view("sideways")["default_view"], store.BOOK)

    def test_the_store_refuses_an_unknown_view_and_falls_back_on_a_bad_file(self):
        with self.assertRaises(ValueError):
            store.set_default_view("sideways")
        data = store._load()
        data.update({"default_view": 7, "progress_bar": "yes", "outline_panel_open": None})
        store._save(data)
        self.assertEqual(store.get_default_view(), store.BOOK)
        self.assertTrue(store.get_progress_bar())
        self.assertFalse(store.get_outline_open())

    def test_the_highlight_color_is_the_one_the_reader_uses(self):
        self.api.set_highlight_color("pink")
        self.assertEqual(self.api.get_reading_preferences()["highlight_color"], "pink")


class DefaultViewOnOpenTests(_ApiCase):
    def test_a_never_opened_pdf_opens_in_the_default_view(self):
        self.api.set_default_view(store.STRIP)
        state = self.api.open_pdf(self.make_pdf("new.pdf"))
        self.assertEqual(state["layout_mode"], store.STRIP)
        self.assertEqual(state["page_index"], 0)

    def test_a_previously_opened_pdf_keeps_its_own_view_and_page(self):
        path = self.make_pdf("known.pdf")
        self.api.open_pdf(path)
        self.api.set_layout_mode(store.BOOK)
        self.api.goto_page(3)
        self.api.set_default_view(store.STRIP)
        state = self.api.open_pdf(path)
        self.assertEqual((state["layout_mode"], state["page_index"]), (store.BOOK, 3))

    def test_start_at_the_beginning_uses_page_one_in_the_default_view(self):
        path = self.make_pdf("restart.pdf")
        self.api.open_pdf(path)
        self.api.goto_page(4)
        store.set_reopen_behavior(store.START)
        self.api.set_default_view(store.STRIP)
        state = self.api.open_pdf(path)
        self.assertEqual((state["layout_mode"], state["page_index"]), (store.STRIP, 0))


class VoiceCommandTests(unittest.TestCase):
    PHRASES = {
        "show outline": grammar.SHOW_OUTLINE,
        "show pages": grammar.SHOW_PAGES,
        "hide outline": grammar.HIDE_OUTLINE,
        "toggle progress bar": grammar.TOGGLE_PROGRESS_BAR,
    }

    def test_each_phrase_resolves_in_the_reader(self):
        for phrase, intent in self.PHRASES.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(router.resolve(router.READING, phrase)["command"]["intent"], intent)

    def test_show_and_hide_have_one_phrasing_each(self):
        self.assertEqual(grammar.PHRASES[grammar.SHOW_OUTLINE], ("show outline",))
        self.assertEqual(grammar.PHRASES[grammar.HIDE_OUTLINE], ("hide outline",))

    def test_the_words_are_in_the_readers_vocabulary(self):
        words = set(router.vocabulary_for(router.READING))
        self.assertLessEqual({"show", "hide", "outline", "pages", "toggle", "progress", "bar"}, words)

    def test_not_heard_outside_the_reader(self):
        for context in (router.HOME, router.SETTINGS):
            self.assertIsNone(router.resolve(context, "show outline")["command"])

    def test_a_spoken_number_picks_an_entry_while_the_picker_is_up(self):
        command = router.resolve(router.PICKER, "twelve")["command"]
        self.assertEqual((command["intent"], command["index"]), (router.PICK, 12))

    def test_listed_in_what_can_i_say(self):
        section = next(s for s in reference.sections(router.READING) if s["id"] == "panel")
        listed = {p for c in section["commands"] for p in c["phrases"]}
        self.assertEqual(listed, set(self.PHRASES))
        self.assertTrue(section["available"])


if __name__ == "__main__":
    unittest.main()
