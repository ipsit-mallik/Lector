"""Regression tests over the shared test PDFs (hardening H6).

What H2-H5 did not already pin, run against each kind of PDF in
tests/pdf_support.py rather than one hand-built page:

- a highlight survives both kinds of save and comes back with its colour and
  words, on plain, multi-page, rotated, unicode, repaired and protected files;
- a page with no text layer is searched without error and finds nothing, and
  can't be highlighted by accident;
- recolouring or removing a highlight (Lector's or another app's) and then
  saving writes exactly that, and an undone change is not written;
- on a rotated page, every box the reading view is sent (characters,
  highlights, find matches) and every region it sends back (the voice
  viewport) is on the page as shown, which is the page image it lays them
  over. Before H6 they were on the unrotated page: on a page turned 90° a drag
  selected the wrong words and find marks landed on blank paper.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pdf_support
import pymupdf  # PyMuPDF: renders the page to check where boxes land

from lector import api as api_module
from lector.features.annotations import highlighter
from lector.features.reading.document import PdfDocument
from lector.features.settings import store

GREEN = tuple(round(c, 3) for c in highlighter.HIGHLIGHT_COLORS["green"])
PINK = tuple(round(c, 3) for c in highlighter.HIGHLIGHT_COLORS["pink"])

# Darker than this, in every channel, is ink rather than paper or highlight.
INK = 100


class TempFolderCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-h6-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def opened(self, path: Path, password: str | None = None) -> PdfDocument:
        doc = PdfDocument()
        doc.open(str(path), password)
        self.addCleanup(doc.close)
        return doc


def _word_index(doc: PdfDocument, page_index: int, text: str) -> int:
    return next(i for i, w in enumerate(doc.words_on_page(page_index)) if w.text == text)


def _highlight_words(doc: PdfDocument, page_index: int, first: str, last: str, color: str = "green") -> int:
    return doc.highlight_word_indices(
        page_index, _word_index(doc, page_index, first), _word_index(doc, page_index, last), color
    )


class HighlightRoundTripTests(TempFolderCase):
    """Highlight a run of words, save, read the file back with a fresh reader."""

    def cases(self) -> list[tuple[str, Path, str | None, int, str, str, str]]:
        """(label, file, password, page, first word, last word, text covered)."""
        d = self.dir
        cases = [
            ("plain", pdf_support.plain_text(d), None, 0, "brown", "fox", "brown fox"),
            ("multi-page, last page", pdf_support.multi_page(d, pages=5), None, 4, "about", "topic5", "about topic5"),
            ("unicode", pdf_support.unicode_text(d), None, 0, "Ωmega", "東京", "Ωmega Straße 東京"),
            ("repaired", pdf_support.truncated(d), None, 0, "brown", "fox", "brown fox"),
            ("protected", pdf_support.encrypted(d), pdf_support.PASSWORD, 0, "brown", "fox", "brown fox"),
        ]
        cases += [
            (f"rotated {r}", pdf_support.rotated(d, r), None, 0, "brown", "fox", "brown fox") for r in (90, 180, 270)
        ]
        return cases

    def test_a_saved_copy_holds_the_highlight_and_the_original_does_not(self):
        for label, path, password, page, first, last, text in self.cases():
            with self.subTest(label):
                before = pdf_support.sha256(path)
                doc = self.opened(path, password)
                _highlight_words(doc, page, first, last)
                copy = self.dir / f"{path.stem} copy.pdf"
                doc.save_copy(str(copy))
                want = [[(GREEN, text)] if n == page else [] for n in range(doc.page_count)]
                self.assertEqual(pdf_support.highlights_on_disk(copy, password), want)
                self.assertEqual(pdf_support.sha256(path), before)
                again = self.opened(copy, password)
                (found,) = again.highlights_on_page(page)
                self.assertEqual(found.color, "green")
                self.assertEqual(again.text_in_rects(page, found.rects), text)

    def test_an_overwrite_holds_the_highlight(self):
        for label, path, password, page, first, last, text in self.cases():
            with self.subTest(label):
                doc = self.opened(path, password)
                _highlight_words(doc, page, first, last)
                doc.save_overwrite()
                self.assertFalse(doc.is_dirty)
                want = [[(GREEN, text)] if n == page else [] for n in range(doc.page_count)]
                self.assertEqual(pdf_support.highlights_on_disk(path, password), want)


class PageWithNoTextTests(TempFolderCase):
    def test_search_finds_nothing_and_says_nothing_was_searchable(self):
        doc = self.opened(pdf_support.image_only(self.dir))
        self.assertEqual(doc.search("words", 0, None), {"pages": [], "next_page": None, "text_pages": 0})

    def test_search_counts_only_the_pages_that_have_text(self):
        # A scanned page between two born-digital ones.
        merged = pymupdf.open()
        for path in (pdf_support.plain_text(self.dir), pdf_support.image_only(self.dir),
                     pdf_support.plain_text(self.dir, name="again.pdf")):
            with pymupdf.open(path) as part:
                merged.insert_pdf(part)
        merged.save(self.dir / "mixed.pdf")
        merged.close()
        result = self.opened(self.dir / "mixed.pdf").search("fox", 0, None)
        self.assertEqual([p["page_index"] for p in result["pages"]], [0, 2])
        self.assertEqual(result["text_pages"], 2)

    def test_it_has_nothing_to_highlight_and_stays_unchanged(self):
        doc = self.opened(pdf_support.image_only(self.dir))
        self.assertEqual(doc.chars_on_page(0), [])
        self.assertIsNone(doc.highlight_char_indices(0, 0, 5))
        self.assertIsNone(doc.highlight_word_indices(0, 0, 5))
        self.assertFalse(doc.is_dirty)
        self.assertFalse(doc.can_undo)

    def test_unicode_words_are_found(self):
        # As written: MuPDF ignores case for ASCII letters only, so "привет"
        # does not find "Привет" (recorded in docs/HARDENING.md, H6).
        doc = self.opened(pdf_support.unicode_text(self.dir))
        for needle in ("Straße", "東京", "Привет", "ΩMEGA", "café"):
            with self.subTest(needle):
                (page,) = doc.search(needle, 0, None)["pages"]
                self.assertEqual(len(page["hits"]), 1)


class ChangeThenSaveTests(TempFolderCase):
    """Edits to highlights already in the file, then each kind of save."""

    SAVES = ("copy", "overwrite")

    def fresh(self, how: str) -> None:
        """A new already-highlighted PDF open as self.doc, with its two
        highlights' xrefs at hand."""
        self.path = pdf_support.already_highlighted(self.dir, name=f"{how}.pdf")
        self.doc = self.opened(self.path)
        self.foreign, self.uncoloured = (h.xref for h in self.doc.highlights_on_page(0))

    def saved(self, how: str) -> list[tuple]:
        """Save `how`, then the page's highlights as the written file holds them."""
        target = self.path
        if how == "copy":
            target = self.dir / f"{self.path.stem} copy.pdf"
            self.doc.save_copy(str(target))
        else:
            self.doc.save_overwrite()
        self.assertFalse(self.doc.is_dirty)
        self.written = target
        (on_page,) = pdf_support.highlights_on_disk(target)
        return on_page

    def test_a_recoloured_foreign_highlight_is_saved_in_its_new_colour_with_its_note(self):
        for how in self.SAVES:
            with self.subTest(how):
                self.fresh(how)
                self.assertTrue(self.doc.set_highlight_color(0, self.foreign, "pink"))
                self.assertEqual(self.saved(how), [(PINK, "quick brown"), (None, "lazy dog.")])
                with pymupdf.open(self.written) as written:
                    info = next(written[0].annots()).info
                    self.assertEqual((info["content"], info["title"]), ("a note", "someone"))

    def test_a_removed_highlight_is_not_saved(self):
        for how in self.SAVES:
            with self.subTest(how):
                self.fresh(how)
                self.assertTrue(self.doc.remove_highlight(0, self.uncoloured))
                self.assertEqual(self.saved(how), [(pdf_support.FOREIGN_RGB, "quick brown")])

    def test_removing_every_highlight_saves_a_file_with_none(self):
        for how in self.SAVES:
            with self.subTest(how):
                self.fresh(how)
                self.doc.remove_highlight(0, self.foreign)
                self.doc.remove_highlight(0, self.uncoloured)
                self.assertEqual(self.saved(how), [])

    def test_an_undone_removal_and_an_undone_recolour_are_not_saved(self):
        # A highlight with no colour of its own comes back from an undone
        # removal in the default colour (PdfDocument.remove_highlight).
        default = tuple(round(c, 3) for c in highlighter.HIGHLIGHT_COLORS[highlighter.DEFAULT_COLOR])
        for how in self.SAVES:
            with self.subTest(how):
                self.fresh(how)
                self.doc.set_highlight_color(0, self.foreign, "green")
                self.doc.remove_highlight(0, self.uncoloured)
                self.doc.undo_highlight()
                self.doc.undo_highlight()
                self.assertTrue(self.doc.is_dirty)  # undoing is itself an unsaved change
                self.assertEqual(
                    self.saved(how), [(pdf_support.FOREIGN_RGB, "quick brown"), (default, "lazy dog.")]
                )

    def test_a_new_highlight_added_then_recoloured_then_saved(self):
        for how in self.SAVES:
            with self.subTest(how):
                self.fresh(how)
                xref = _highlight_words(self.doc, 0, "jumps", "over", "green")
                self.doc.set_highlight_color(0, xref, "pink")
                self.assertEqual(
                    self.saved(how),
                    [(pdf_support.FOREIGN_RGB, "quick brown"), (None, "lazy dog."), (PINK, "jumps over")],
                )


class RotatedPageBoxesTests(TempFolderCase):
    """Boxes the reading view lays over the page image are on that image.

    The truth is the rendered page itself: at zoom 1 a pixel is a point, so a
    character's box must cover ink, and a highlight's box its tint.
    """

    ROTATIONS = (0, 90, 180, 270)

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        windows = mock.patch.object(api_module.webview, "windows", [mock.Mock()])
        windows.start()
        self.addCleanup(windows.stop)

    def api_for(self, rotation: int) -> api_module.Api:
        api = api_module.Api()
        api._voice = mock.MagicMock()
        self.addCleanup(api._doc.close)
        api.open_pdf(str(pdf_support.rotated(self.dir, rotation)))
        return api

    def rendered(self, api: api_module.Api) -> "pymupdf.Pixmap":
        return api._doc._doc[0].get_pixmap(alpha=False)

    def word_box(self, chars: dict, word: str) -> tuple[float, float, float, float]:
        """The box around `word`'s characters in `get_page_chars`, by index."""
        start = pdf_support.PLAIN_TEXT.index(word)
        boxes = chars["chars"][start:start + len(word)]
        return (min(b["x0"] for b in boxes), min(b["y0"] for b in boxes),
                max(b["x1"] for b in boxes), max(b["y1"] for b in boxes))

    def pixels(self, pix: "pymupdf.Pixmap", box) -> list[tuple]:
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        return [pix.pixel(x, y) for x in range(max(x0, 0), min(x1, pix.width))
                for y in range(max(y0, 0), min(y1, pix.height))]

    def test_character_boxes_cover_their_ink(self):
        for rotation in self.ROTATIONS:
            with self.subTest(rotation=rotation):
                api = self.api_for(rotation)
                chars = api.get_page_chars(0)
                pix = self.rendered(api)
                self.assertEqual((chars["width"], chars["height"]), (pix.width, pix.height))
                for c in chars["chars"]:
                    self.assertTrue(0 <= c["x0"] <= c["x1"] <= chars["width"], c)
                    self.assertTrue(0 <= c["y0"] <= c["y1"] <= chars["height"], c)
                ink = [p for p in self.pixels(pix, self.word_box(chars, "fox")) if max(p) < INK]
                self.assertGreater(len(ink), 20)

    def test_a_highlight_box_covers_its_tint(self):
        for rotation in self.ROTATIONS:
            with self.subTest(rotation=rotation):
                api = self.api_for(rotation)
                start = pdf_support.PLAIN_TEXT.index("fox")
                api.highlight_chars(0, start, start + 2)
                (found,) = api.get_page_highlights(0)["highlights"]
                self.assertEqual(found["text"], "fox")
                (r,) = found["rects"]
                tint = [p for p in self.pixels(self.rendered(api), (r["x0"], r["y0"], r["x1"], r["y1"]))
                        if max(p) >= INK and min(p) < 230]
                self.assertGreater(len(tint), 0.5 * (r["x1"] - r["x0"]) * (r["y1"] - r["y0"]))

    def test_a_find_match_is_where_its_characters_are(self):
        for rotation in self.ROTATIONS:
            with self.subTest(rotation=rotation):
                api = self.api_for(rotation)
                (page,) = api.find_in_document("fox")["pages"]
                (((x0, y0, x1, y1),),) = page["hits"]
                want = self.word_box(api.get_page_chars(0), "fox")
                for got, expected in zip((x0, y0, x1, y1), want):
                    self.assertAlmostEqual(got, expected, delta=1.5)
                pix = self.rendered(api)
                self.assertEqual((page["width"], page["height"]), (pix.width, pix.height))
                ink = [p for p in self.pixels(pix, (x0, y0, x1, y1)) if max(p) < INK]
                self.assertGreater(len(ink), 20)

    def test_voice_matches_the_words_in_the_region_shown(self):
        for rotation in self.ROTATIONS:
            with self.subTest(rotation=rotation):
                api = self.api_for(rotation)
                _, y0, _, y1 = self.word_box(api.get_page_chars(0), "fox")
                api.start_listening([{"page_index": 0, "y0": y0, "y1": y1}])
                self.assertTrue(api.highlight_by_voice("fox")["voice_highlight"]["matched"])
                (found,) = api.get_page_highlights(0)["highlights"]
                self.assertEqual(found["text"], "fox")

    def test_with_no_document_open_there_is_nothing_to_turn(self):
        doc = PdfDocument()
        self.assertEqual(doc.as_shown(0, []), [])
        self.assertEqual(doc.words_as_shown(0), [])
        api = api_module.Api()
        api._voice = mock.MagicMock()
        api.start_listening([{"page_index": 0, "y0": 0, "y1": 500}])
        self.assertFalse(api.highlight_by_voice("fox")["voice_highlight"]["matched"])

    def test_voice_does_not_match_words_outside_the_region_shown(self):
        # The band the line sits in on the *unrotated* page: what the old
        # code matched against, whatever the reader could see there.
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                api = self.api_for(rotation)
                line = api._doc._doc[0].get_text("words")
                y0, y1 = min(w[1] for w in line), max(w[3] for w in line)
                api.start_listening([{"page_index": 0, "y0": y0, "y1": y1}])
                self.assertFalse(api.highlight_by_voice("fox")["voice_highlight"]["matched"])


if __name__ == "__main__":
    unittest.main()
