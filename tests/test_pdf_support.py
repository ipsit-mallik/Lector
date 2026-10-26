"""The shared test PDFs (tests/pdf_support.py) are what they say they are.

Every other test that uses one trusts its name: a "scanned" page that quietly
grew a text layer, or an "encrypted" file that opens without a password,
would turn those tests into ones that pass for the wrong reason. Read back
with a fresh PyMuPDF document, not through Lector.
"""
import tempfile
import unittest
from pathlib import Path

import pdf_support
import pymupdf  # PyMuPDF: reads the built PDFs back


class PdfSupportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-pdfs-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def opened(self, path: Path) -> "pymupdf.Document":
        doc = pymupdf.open(path)
        self.addCleanup(doc.close)
        return doc

    def test_plain_text_is_one_page_of_its_sentence(self):
        doc = self.opened(pdf_support.plain_text(self.dir))
        self.assertEqual(doc.page_count, 1)
        self.assertEqual(doc[0].get_text().strip(), pdf_support.PLAIN_TEXT)

    def test_multi_page_tells_its_pages_apart(self):
        doc = self.opened(pdf_support.multi_page(self.dir, pages=4))
        self.assertEqual([p.get_text().strip() for p in doc], [pdf_support.page_text(n) for n in range(4)])

    def test_rotated_turns_the_page_and_keeps_its_text(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                page = self.opened(pdf_support.rotated(self.dir, rotation))[0]
                self.assertEqual(page.rotation, rotation)
                self.assertEqual(page.get_text().strip(), pdf_support.PLAIN_TEXT)

    def test_image_only_has_a_picture_and_no_text(self):
        page = self.opened(pdf_support.image_only(self.dir))[0]
        self.assertEqual(page.get_text().strip(), "")
        self.assertEqual(len(page.get_images()), 1)

    def test_encrypted_needs_its_password_and_opens_with_it(self):
        doc = self.opened(pdf_support.encrypted(self.dir))
        self.assertTrue(doc.needs_pass)
        self.assertFalse(doc.authenticate("not it"))
        self.assertTrue(doc.authenticate(pdf_support.PASSWORD))
        self.assertEqual(doc[0].get_text().strip(), pdf_support.PLAIN_TEXT)

    def test_unicode_text_comes_back_as_written(self):
        page = self.opened(pdf_support.unicode_text(self.dir))[0]
        self.assertEqual(page.get_text().strip(), pdf_support.UNICODE_TEXT)

    def test_already_highlighted_holds_a_foreign_and_an_uncoloured_highlight(self):
        path = pdf_support.already_highlighted(self.dir)
        (on_page,) = pdf_support.highlights_on_disk(path)
        self.assertEqual(on_page, [(pdf_support.FOREIGN_RGB, "quick brown"), (None, "lazy dog.")])

    def test_zero_byte_is_empty(self):
        self.assertEqual(pdf_support.zero_byte(self.dir).stat().st_size, 0)

    def test_truncated_has_lost_its_xref_and_opens_repaired(self):
        path = pdf_support.truncated(self.dir)
        self.assertNotIn(b"\nxref", path.read_bytes())
        doc = self.opened(path)
        self.assertTrue(doc.is_repaired)
        self.assertEqual(doc[0].get_text().strip(), pdf_support.PLAIN_TEXT)


if __name__ == "__main__":
    unittest.main()
