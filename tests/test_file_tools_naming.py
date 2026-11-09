"""The file tools' default names and the Export "Pages" field (Milestone F1)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.file_tools import formats, messages, naming  # noqa: E402
from lector.features.reading import safe_write  # noqa: E402


class FreeNameTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.source = str(self.folder / "Report.pdf")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_default_name_is_beside_the_original(self):
        self.assertEqual(naming.free_file_name(self.source, naming.COMPRESSED), str(self.folder / "Report (compressed).pdf"))
        self.assertEqual(naming.free_file_name(self.source, naming.SEARCHABLE), str(self.folder / "Report (searchable).pdf"))

    def test_a_taken_name_is_skipped(self):
        (self.folder / "Report (compressed).pdf").write_bytes(b"x")
        (self.folder / "Report (compressed) (2).pdf").write_bytes(b"x")
        self.assertEqual(
            naming.free_file_name(self.source, naming.COMPRESSED), str(self.folder / "Report (compressed) (3).pdf")
        )

    def test_save_a_copy_keeps_its_own_suggestion(self):
        self.assertEqual(naming.free_file_name(self.source, naming.HIGHLIGHTED), str(self.folder / "Report (highlighted).pdf"))

    def test_an_export_folder_that_exists_is_never_reused(self):
        (self.folder / "Report (images)").mkdir()
        self.assertEqual(naming.free_stem(str(self.folder), "Report (images)", ""), "Report (images) (2)")

    def test_the_free_name_is_found_without_the_extension(self):
        self.assertEqual(naming.free_stem(str(self.folder), "IMG_2041", ".pdf"), "IMG_2041")
        (self.folder / "IMG_2041.pdf").write_bytes(b"x")
        self.assertEqual(naming.free_stem(str(self.folder), "IMG_2041", ".pdf"), "IMG_2041 (2)")


class ImageNameTests(unittest.TestCase):
    def test_numbers_are_padded_to_at_least_three_digits(self):
        self.assertEqual(naming.image_file_name("Report", 1, 9, "png"), "Report-p001.png")
        self.assertEqual(naming.image_file_name("Report", 34, 120, "jpg"), "Report-p034.jpg")

    def test_a_long_pdf_pads_to_its_page_counts_width(self):
        self.assertEqual(naming.image_file_name("Book", 7, 1200, "png"), "Book-p0007.png")


class RangeParserTests(unittest.TestCase):
    def test_ranges_and_single_pages(self):
        self.assertEqual(naming.parse_page_ranges("1-3, 8", 10), [1, 2, 3, 8])

    def test_en_and_em_dashes_and_spaces(self):
        self.assertEqual(naming.parse_page_ranges(" 1 – 3 ,8, 9—10 ", 10), [1, 2, 3, 8, 9, 10])

    def test_duplicates_and_overlaps_count_once_in_page_order(self):
        self.assertEqual(naming.parse_page_ranges("5, 1-3, 2, 3-4", 10), [1, 2, 3, 4, 5])

    def test_a_trailing_comma_is_ignored(self):
        self.assertEqual(naming.parse_page_ranges("2,", 10), [2])

    def test_out_of_range(self):
        for text in ("11", "9-12", "0", "0-2"):
            with self.subTest(text=text), self.assertRaises(naming.RangeError):
                naming.parse_page_ranges(text, 10)

    def test_out_of_range_says_how_many_pages_there_are(self):
        with self.assertRaises(naming.RangeError) as caught:
            naming.parse_page_ranges("12", 10)
        self.assertIn("10 pages", caught.exception.message)

    def test_backwards_range(self):
        with self.assertRaises(naming.RangeError) as caught:
            naming.parse_page_ranges("5-2", 10)
        self.assertIn("2–5", caught.exception.message)

    def test_garbage(self):
        for text in ("", "   ", "abc", "1-2-3", "1..3", "-4", "1;2", "x" * 600, None, 7):
            with self.subTest(text=text), self.assertRaises(naming.RangeError):
                naming.parse_page_ranges(text, 10)


class MessageTests(unittest.TestCase):
    def test_every_message_says_the_original_is_unchanged(self):
        reasons = list(safe_write.MESSAGES) + [
            messages.SAME_AS_SOURCE,
            messages.SOURCE_UNREADABLE,
            messages.WORKER_FAILED,
            messages.INPUT_UNREADABLE,
            messages.OCR_UNAVAILABLE,
            "made-up",
        ]
        for tool in messages.TOOLS:
            for reason in reasons:
                with self.subTest(tool=tool, reason=reason):
                    text = messages.message(reason, tool)
                    self.assertTrue(text.startswith("Couldn't "))
                    self.assertIn("unchanged.", text)


class FormatTests(unittest.TestCase):
    def test_accepted_types(self):
        names = ("a.PNG", "b.jpg", "c.jpeg", "d.tif", "e.tiff", "f.bmp", "g.gif", "h.epub", "i.xps", "j.oxps", "k.cbz", "l.fb2")
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(formats.is_convertible(name))

    def test_refused_types(self):
        for name in ("a.pdf", "b.docx", "c.webp", "d.heic", "noextension", "dir.png/file"):
            with self.subTest(name=name):
                self.assertFalse(formats.is_convertible(name))


if __name__ == "__main__":
    unittest.main()
