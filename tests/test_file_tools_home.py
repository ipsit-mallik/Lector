"""Home's side of the file tools (Milestone F1): the Open dialog's "convert"
listing and images or e-books dropped on the window."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.dialogs import browser  # noqa: E402
from lector.features.home import file_drop  # noqa: E402


def _drop(*paths: str) -> dict:
    return {"dataTransfer": {"files": [{"name": Path(p).name, "pywebviewFullPath": p} for p in paths]}}


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, name: str) -> str:
        path = self.folder / name
        path.write_bytes(b"x")
        return str(path)


class ConvertListingTests(_Folder):
    def test_convert_lists_images_and_e_books_not_pdfs(self):
        for name in ("a.pdf", "b.PNG", "c.jpg", "d.epub", "e.docx", "f.tiff", "g.webp"):
            self.touch(name)
        (self.folder / "sub").mkdir()
        listed = browser.list_directory(str(self.folder), browser.CONVERT_FILTER)
        self.assertEqual([e["name"] for e in listed["entries"]], ["sub", "b.PNG", "c.jpg", "d.epub", "f.tiff"])

    def test_pdfs_stay_the_default_and_an_unknown_filter_means_pdfs(self):
        self.touch("a.pdf")
        self.touch("b.png")
        for kind in (browser.PDF_FILTER, "anything"):
            with self.subTest(kind=kind):
                listed = browser.list_directory(str(self.folder), kind)
                self.assertEqual([e["name"] for e in listed["entries"]], ["a.pdf"])


class DroppedImageTests(_Folder):
    def test_images_and_e_books_are_offered_to_convert(self):
        image, book = self.touch("scan.png"), self.touch("notes.epub")
        self.assertEqual(file_drop.convertible_from_drop(_drop(image, book)), [image, book])

    def test_a_refused_image_points_at_convert(self):
        image = self.touch("scan.png")
        outcome = file_drop.with_convertible(file_drop.pdf_from_drop(_drop(image)), [image])
        self.assertEqual(outcome["convertible"], [image])
        self.assertEqual(outcome["error"], f"{file_drop.NOT_A_PDF} {file_drop.CONVERT_HINT}")
        self.assertIn("Tools › Convert to PDF", outcome["error"])

    def test_anything_else_is_refused_as_before(self):
        doc = self.touch("letter.docx")
        event = _drop(doc)
        outcome = file_drop.with_convertible(file_drop.pdf_from_drop(event), file_drop.convertible_from_drop(event))
        self.assertEqual(outcome, {"error": file_drop.NOT_A_PDF})

    def test_a_pdf_in_the_drop_still_opens(self):
        pdf, image = self.touch("book.pdf"), self.touch("cover.jpg")
        outcome = file_drop.with_convertible(file_drop.pdf_from_drop(_drop(pdf, image)), [image])
        self.assertEqual(outcome["path"], pdf)
        self.assertNotIn("error", outcome)

    def test_a_missing_image_is_not_offered(self):
        self.assertEqual(file_drop.convertible_from_drop(_drop(str(self.folder / "gone.png"))), [])


if __name__ == "__main__":
    unittest.main()
