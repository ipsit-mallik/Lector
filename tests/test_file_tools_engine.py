"""The file tools' engine (Milestone F1), run in this process.

`InlineContext` (file_tools_support.py) runs each job's steps here rather
than in the worker, so these exercise exactly the functions the worker runs
and the job sequences around them, without spawning a process.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

import file_tools_support as fx  # noqa: E402
import pdf_support  # noqa: E402
from lector.features.file_tools import engine, tools  # noqa: E402
from lector.features.file_tools.jobs import JobCancelled, JobFailed  # noqa: E402
from lector.features.reading import ocr, ocr_cache  # noqa: E402

HAS_TESSDATA = (ocr.TESSDATA_DIR / f"{ocr.OCR_LANGUAGE}.traineddata").is_file()


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        engine._OPEN.clear()
        self._tmp.cleanup()

    def leftovers(self) -> list[str]:
        return sorted(n for n in os.listdir(self.folder) if n.startswith(".lector-"))


def _ppi(width_px: int, box_width_pt: float) -> float:
    return width_px / (box_width_pt / 72)


class CompressTests(_Folder):
    def compress(self, source: Path, level: str, data: bytes | None = None, password: str | None = None) -> tuple[dict, Path]:
        dest = self.folder / f"{source.stem} (compressed).pdf"
        result = tools.compress(fx.InlineContext(), tools.Source(str(source), data, password), str(dest), level)
        return result, dest

    def test_balanced_brings_a_photo_down_to_150_ppi(self):
        result, dest = self.compress(fx.photo_pdf(self.folder), "balanced")
        self.assertLess(result["after"], result["before"] * 0.95)
        for _, width, _, _ in fx.image_sizes(dest):
            self.assertAlmostEqual(_ppi(width, fx.PHOTO_BOX.width), 150, delta=2)

    def test_strong_brings_a_photo_down_to_96_ppi(self):
        _, dest = self.compress(fx.photo_pdf(self.folder), "strong")
        for _, width, _, _ in fx.image_sizes(dest):
            self.assertAlmostEqual(_ppi(width, fx.PHOTO_BOX.width), 96, delta=2)

    def test_no_level_ever_enlarges_an_image(self):
        for level in ("light", "balanced", "strong"):
            for build in (fx.photo_pdf, fx.low_res_photo_pdf, fx.bilevel_scan_pdf):
                with self.subTest(level=level, fixture=build.__name__):
                    source = build(self.folder, name=f"{build.__name__}-{level}.pdf")
                    before = {(w, h) for _, w, h, _ in fx.image_sizes(source)}
                    result, dest = self.compress(source, level)
                    if result.get("compact"):
                        continue
                    for _, width, height, _ in fx.image_sizes(dest):
                        self.assertTrue(any(width <= w and height <= h for w, h in before))

    def test_light_leaves_images_untouched(self):
        source = fx.photo_pdf(self.folder)
        before = [(w, h, bpc) for _, w, h, bpc in fx.image_sizes(source)]
        result, dest = self.compress(source, "light")
        if not result.get("compact"):
            self.assertEqual([(w, h, bpc) for _, w, h, bpc in fx.image_sizes(dest)], before)

    def test_every_annotation_is_kept_exactly(self):
        source = fx.photo_pdf(self.folder)
        expected = fx.annotations(source)
        for level in ("balanced", "strong"):
            with self.subTest(level=level):
                _, dest = self.compress(source, level)
                self.assertEqual(fx.annotations(dest), expected)
                os.remove(dest)

    def test_the_text_layer_is_kept(self):
        _, dest = self.compress(fx.photo_pdf(self.folder), "strong")
        doc = pymupdf.open(dest)
        self.assertTrue(doc[0].search_for(fx.KNOWN_WORD))
        doc.close()

    def test_transparency_keeps_its_soft_mask(self):
        _, dest = self.compress(fx.alpha_photo_pdf(self.folder), "balanced")
        doc = pymupdf.open(dest)
        self.assertGreater(doc[0].get_images(full=True)[0][1], 0)
        doc.close()

    def test_a_one_bit_scan_stays_one_bit(self):
        result, dest = self.compress(fx.bilevel_scan_pdf(self.folder), "strong")
        if not result.get("compact"):
            self.assertEqual([bpc for *_, bpc in fx.image_sizes(dest)], [1])
            self.assertEqual(fx.image_sizes(dest)[0][1:3], (1200, 1200))

    def test_under_five_percent_writes_nothing(self):
        # Already tidied, as a PDF from a careful tool is.
        loose = pdf_support.plain_text(self.folder, name="loose.pdf")
        source = self.folder / "plain.pdf"
        doc = pymupdf.open(loose)
        doc.subset_fonts()
        doc.save(source, garbage=4, deflate=True, clean=True)
        doc.close()
        result, dest = self.compress(source, "balanced")
        self.assertEqual(result, {"compact": True, "before": os.path.getsize(source), "level": "balanced"})
        self.assertFalse(dest.exists())
        self.assertEqual(self.leftovers(), [])

    def test_an_encrypted_pdf_stays_encrypted(self):
        source = fx.photo_pdf(self.folder, name="photos-plain.pdf")
        locked = self.folder / "locked-photos.pdf"
        doc = pymupdf.open(source)
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=pdf_support.PASSWORD, owner_pw="owner")
        doc.close()
        _, dest = self.compress(locked, "balanced", password=pdf_support.PASSWORD)
        out = pymupdf.open(dest)
        self.assertTrue(out.needs_pass)
        self.assertTrue(out.authenticate(pdf_support.PASSWORD))
        out.close()

    def test_a_wrong_password_fails_and_writes_nothing(self):
        locked = pdf_support.encrypted(self.folder)
        with self.assertRaises(JobFailed):
            self.compress(locked, "balanced", password="wrong")
        self.assertEqual(sorted(os.listdir(self.folder)), ["locked.pdf"])

    def test_the_readers_in_memory_copy_is_what_is_compressed(self):
        source = fx.photo_pdf(self.folder, pages=1)
        doc = pymupdf.open(source)
        page = doc[0]
        page.add_highlight_annot(pymupdf.Rect(50, 320, 200, 340)).update()
        data = doc.tobytes()
        doc.close()
        _, dest = self.compress(source, "balanced", data=data)
        self.assertEqual(len(fx.annotations(dest)[0]), len(fx.annotations(source)[0]) + 1)

    def test_a_cancelled_compress_leaves_no_file(self):
        source = fx.photo_pdf(self.folder)
        dest = self.folder / "out.pdf"
        for cancel_at in range(1, 5):
            with self.subTest(cancel_at=cancel_at):
                with self.assertRaises(JobCancelled):
                    tools.compress(fx.InlineContext(cancel_at=cancel_at), tools.Source(str(source)), str(dest), "balanced")
                self.assertFalse(dest.exists())
                self.assertEqual(self.leftovers(), [])
                self.assertEqual(engine._OPEN, {})

    def test_progress_is_reported_by_phase(self):
        ctx = fx.InlineContext()
        tools.compress(ctx, tools.Source(str(fx.photo_pdf(self.folder))), str(self.folder / "o.pdf"), "balanced")
        self.assertEqual(
            [phase for phase, *_ in ctx.reports],
            ["Copying the document", "Compressing images", "Subsetting fonts", "Writing the file", "Checking the file"],
        )


class DescribeTests(_Folder):
    def test_images_are_counted_once_by_xref(self):
        doc = pymupdf.open()
        xref = doc.new_page().insert_image(pymupdf.Rect(0, 0, 50, 50), pixmap=fx._noisy_photo(20, 20))
        for _ in range(3):
            doc.new_page().insert_image(pymupdf.Rect(0, 0, 50, 50), xref=xref)
        self.assertEqual(engine.describe_document(doc)["images"], 1)
        doc.close()

    def test_a_signed_pdf_is_detected(self):
        self.assertTrue(engine.describe_path(str(fx.signed_flag_pdf(self.folder)), None)["signed"])
        self.assertFalse(engine.describe_path(str(pdf_support.plain_text(self.folder)), None)["signed"])

    def test_textless_pages_are_listed(self):
        info = engine.describe_path(str(fx.scanned_text_pdf(self.folder)), None)
        self.assertEqual(info["textless"], [0])
        self.assertEqual(info["pages"], 2)

    def test_a_locked_pdf_asks_for_its_password(self):
        locked = str(pdf_support.encrypted(self.folder))
        self.assertEqual(engine.describe_path(locked, None), {"locked": True})
        self.assertEqual(engine.describe_path(locked, "nope"), {"locked": True})
        self.assertTrue(engine.describe_path(locked, pdf_support.PASSWORD)["needs_pass"])


class ExportTests(_Folder):
    def export(self, source: Path, fmt: str, ppi: int, pages: list[int]) -> dict:
        dest = self.folder / f"{source.stem} (images)"
        return tools.export_images(fx.InlineContext(), tools.Source(str(source)), str(dest), fmt, ppi, pages)

    def test_names_and_count(self):
        source = pdf_support.multi_page(self.folder, pages=12, name="Book.pdf")
        result = self.export(source, "png", 96, [0, 4, 11])
        self.assertEqual(result["count"], 3)
        self.assertEqual(sorted(os.listdir(result["path"])), ["Book-p001.png", "Book-p005.png", "Book-p012.png"])

    def test_pixel_size_per_resolution(self):
        source = pdf_support.plain_text(self.folder, name="A4.pdf")  # PyMuPDF's default page: A4
        for ppi in (96, 150, 300):
            with self.subTest(ppi=ppi):
                result = self.export(source, "png", ppi, [0])
                pix = pymupdf.Pixmap(os.path.join(result["path"], "A4-p001.png"))
                # MuPDF rounds a part pixel up at the edges.
                self.assertAlmostEqual(pix.width, 595 * ppi / 72, delta=1)
                self.assertAlmostEqual(pix.height, 842 * ppi / 72, delta=1)
                self.assertEqual(pix.alpha, 0)
                del pix
                shutil.rmtree(result["path"])

    def test_jpeg(self):
        result = self.export(pdf_support.plain_text(self.folder, name="J.pdf"), "jpeg", 96, [0])
        self.assertEqual(os.listdir(result["path"]), ["J-p001.jpg"])
        self.assertEqual(Path(result["path"], "J-p001.jpg").read_bytes()[:3], b"\xff\xd8\xff")

    def test_highlights_are_drawn_into_the_image(self):
        source = pdf_support.already_highlighted(self.folder)
        plain = pymupdf.open(source)
        bare = plain[0].get_pixmap(dpi=96, annots=False, alpha=False).samples
        plain.close()
        result = self.export(source, "png", 96, [0])
        drawn = pymupdf.Pixmap(os.path.join(result["path"], os.listdir(result["path"])[0])).samples
        self.assertNotEqual(bare, drawn)

    def test_a_rotated_page_comes_out_turned(self):
        result = self.export(pdf_support.rotated(self.folder, 90, name="R.pdf"), "png", 96, [0])
        pix = pymupdf.Pixmap(os.path.join(result["path"], "R-p001.png"))
        self.assertGreater(pix.width, pix.height)

    def test_an_existing_folder_is_not_written_into(self):
        source = pdf_support.plain_text(self.folder, name="P.pdf")
        taken = self.folder / "P (images)"
        taken.mkdir()
        (taken / "keep.txt").write_text("mine")
        result = self.export(source, "png", 96, [0])
        self.assertEqual(result["path"], str(self.folder / "P (images) (2)"))
        self.assertEqual(os.listdir(taken), ["keep.txt"])

    def test_a_cancelled_export_leaves_no_folder(self):
        source = pdf_support.multi_page(self.folder, pages=4, name="C.pdf")
        with self.assertRaises(JobCancelled):
            tools.export_images(
                fx.InlineContext(cancel_at=3), tools.Source(str(source)), str(self.folder / "C (images)"),
                "png", 96, [0, 1, 2, 3],
            )
        self.assertEqual(sorted(os.listdir(self.folder)), ["C.pdf"])


@unittest.skipUnless(HAS_TESSDATA, "needs assets/tessdata (python scripts/fetch_tessdata.py)")
class MakeSearchableTests(_Folder):
    def setUp(self):
        super().setUp()
        self._cache = mock.patch.object(ocr_cache, "cache_dir", lambda: self.folder / "ocr-cache")
        self._cache.start()

    def tearDown(self):
        self._cache.stop()
        super().tearDown()

    def run_tool(self, source: Path, known=tools.cached_words, password=None) -> tuple[dict, Path]:
        dest = self.folder / f"{source.stem} (searchable).pdf"
        result = tools.make_searchable(fx.InlineContext(), tools.Source(str(source), None, password), str(dest), [0], known)
        return result, dest

    def test_a_scanned_page_becomes_searchable(self):
        result, dest = self.run_tool(fx.scanned_text_pdf(self.folder))
        self.assertEqual(result["added"], 1)
        doc = pymupdf.open(dest)
        self.assertTrue(doc[0].search_for(fx.KNOWN_WORD))
        doc.close()

    def test_rotated_scans_line_up_with_the_picture(self):
        flat_result, flat = self.run_tool(fx.scanned_text_pdf(self.folder, name="flat.pdf"))
        doc = pymupdf.open(flat)
        expected = doc[0].search_for(fx.KNOWN_WORD)[0]  # as shown: unrotated
        doc.close()
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                _, dest = self.run_tool(fx.scanned_text_pdf(self.folder, name=f"rot{rotation}.pdf", rotation=rotation))
                doc = pymupdf.open(dest)
                page = doc[0]
                hits = page.search_for(fx.KNOWN_WORD)
                self.assertTrue(hits)
                shown = (hits[0] * page.rotation_matrix).normalize()
                text = page.get_text()
                doc.close()
                # Where the picture shows the word, within a couple of points,
                # and the line reads in order.
                for a, b in zip(shown, expected):
                    self.assertAlmostEqual(a, b, delta=3)
                self.assertIn(f"The {fx.KNOWN_WORD} stands", text)

    def test_pages_with_text_and_the_image_are_untouched(self):
        source = fx.scanned_text_pdf(self.folder)
        before = pymupdf.open(source)
        text_page = before[1].read_contents()
        images = [(i[2], i[3]) for i in before[0].get_images(full=True)]
        before.close()
        _, dest = self.run_tool(source)
        after = pymupdf.open(dest)
        self.assertEqual(after[1].read_contents(), text_page)
        self.assertEqual([(i[2], i[3]) for i in after[0].get_images(full=True)], images)
        after.close()

    def test_a_page_already_read_is_not_read_again(self):
        source = fx.scanned_text_pdf(self.folder)
        self.run_tool(source)
        with mock.patch.object(ocr, "recognize", side_effect=AssertionError("read twice")):
            result, _ = self.run_tool(source.rename(self.folder / "again.pdf"))
        self.assertEqual(result["added"], 1)

    def test_a_password_protected_pdfs_text_is_not_cached(self):
        source = fx.scanned_text_pdf(self.folder, name="open.pdf")
        locked = self.folder / "locked-scan.pdf"
        doc = pymupdf.open(source)
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=pdf_support.PASSWORD, owner_pw="o")
        doc.close()
        self.run_tool(locked, password=pdf_support.PASSWORD)
        self.assertFalse(list((self.folder / "ocr-cache").glob("*.json")))

    def test_a_picture_with_no_text_writes_no_file(self):
        doc = pymupdf.open()
        doc.new_page().insert_image(pymupdf.Rect(0, 0, 300, 300), pixmap=fx._noisy_photo(40, 40))
        blank = self.folder / "photo.pdf"
        doc.save(blank)
        doc.close()
        result, dest = self.run_tool(blank)
        self.assertEqual(result, {"written": False, "no_text": [1]})
        self.assertFalse(dest.exists())

    def test_missing_language_data_fails_before_anything_runs(self):
        with self.assertRaises(JobFailed):
            tools.make_searchable(
                fx.InlineContext(), tools.Source(str(fx.scanned_text_pdf(self.folder))), str(self.folder / "x.pdf"),
                [0], tools.cached_words, tessdata=str(self.folder),
            )


class ConvertTests(_Folder):
    def convert(self, inputs: list[Path], page_size: str = "image") -> tuple[dict, Path]:
        dest = self.folder / "out.pdf"
        result = tools.convert(fx.InlineContext(), [str(p) for p in inputs], str(dest), page_size)
        return result, dest

    def test_images_in_order_one_a_page(self):
        a = fx.png_file(self.folder, "a.png", (40, 30), shade=10)
        b = fx.jpeg_file(self.folder, "b.jpg", (30, 40), shade=240)
        result, dest = self.convert([b, a])
        self.assertEqual(result["pages"], 2)
        doc = pymupdf.open(dest)
        self.assertEqual([tuple(p.get_images(full=True)[0][2:4]) for p in doc], [(30, 40), (40, 30)])
        doc.close()

    def test_every_tiff_frame_is_a_page(self):
        result, _ = self.convert([fx.tiff_file(self.folder, frames=3)])
        self.assertEqual(result["pages"], 3)

    def test_an_epub_is_laid_out_on_a4(self):
        _, dest = self.convert([fx.epub_file(self.folder)])
        doc = pymupdf.open(dest)
        self.assertGreater(len(doc), 1)
        self.assertEqual((round(doc[0].rect.width), round(doc[0].rect.height)), (595, 842))
        # MuPDF sets "fi" as a ligature.
        self.assertIn("notes, with a few words", doc[0].get_text())
        doc.close()

    def test_same_as_image_keeps_the_images_size(self):
        _, dest = self.convert([fx.png_file(self.folder, "wide.png", (960, 480))])
        doc = pymupdf.open(dest)
        self.assertEqual((round(doc[0].rect.width), round(doc[0].rect.height)), (720, 360))
        doc.close()

    def test_a4_fits_a_big_image_inside_the_page_in_proportion(self):
        _, dest = self.convert([fx.png_file(self.folder, "big.png", (4000, 2000))], "a4")
        doc = pymupdf.open(dest)
        page = doc[0]
        self.assertEqual((round(page.rect.width), round(page.rect.height)), (595, 842))
        box = page.get_image_rects(page.get_images(full=True)[0][0])[0]
        self.assertAlmostEqual(box.width, 595, delta=1)
        self.assertAlmostEqual(box.width / box.height, 2, delta=0.01)
        self.assertAlmostEqual(box.y0, page.rect.height - box.y1, delta=1)  # centred
        doc.close()

    def test_letter_never_enlarges_a_small_image(self):
        _, dest = self.convert([fx.png_file(self.folder, "small.png", (96, 48))], "letter")
        doc = pymupdf.open(dest)
        box = doc[0].get_image_rects(doc[0].get_images(full=True)[0][0])[0]
        self.assertAlmostEqual(box.width, 72, delta=0.5)  # 96 px at 96 ppi is one inch
        self.assertAlmostEqual(box.x0, (612 - 72) / 2, delta=0.5)
        doc.close()

    def test_a_gif_is_its_first_frame(self):
        gif = self.folder / "one.gif"
        gif.write_bytes(bytes.fromhex(
            "47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"
        ))
        result, _ = self.convert([gif])
        self.assertEqual(result["pages"], 1)

    def test_an_unreadable_file_fails_and_writes_nothing(self):
        bad = self.folder / "bad.png"
        bad.write_bytes(b"not a png")
        with self.assertRaises(JobFailed):
            self.convert([fx.png_file(self.folder, "ok.png", (10, 10)), bad])
        self.assertFalse((self.folder / "out.pdf").exists())
        self.assertEqual(self.leftovers(), [])

    def test_describe_gives_type_pages_and_a_thumbnail(self):
        info = engine.convert_describe(str(fx.epub_file(self.folder)))
        self.assertEqual(info["type"], "EPUB")
        self.assertTrue(info["ebook"])
        self.assertGreater(info["pages"], 1)
        self.assertTrue(info["thumbnail"])
        with self.assertRaises(engine.UnsupportedInput):
            engine.convert_describe(str(pdf_support.plain_text(self.folder)))


if __name__ == "__main__":
    unittest.main()
