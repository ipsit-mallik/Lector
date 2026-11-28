"""A PDF opened only to organise its pages (Home's Tools page, a Recent or
Favorites menu; `Api.open_pdf_to_organise`).

* Opening it leaves Recent and the saved reading position alone.
* Cancel closes it again, as if it had never been opened: the file is let
  go of (it can be replaced or deleted at once), Insert's files too, and
  nothing is written to Recent or the position store. The reply says
  `closed` and `is_open: False`, which sends the page straight back to Home.
* Done makes it an ordinary open: into Recent, with its position, and the
  reader shows it with the changes applied and unsaved.
* A PDF whose pages can't be changed is an ordinary open from the start (the
  reader shows it and says why).
* An ordinary open (the reader's own Organise) is unchanged: Cancel keeps it
  open.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the restricted fixture

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.reading import ocr_cache  # noqa: E402
from lector.features.settings import store  # noqa: E402


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        for patcher in (
            mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json"),
            mock.patch.object(ocr_cache, "cache_dir", lambda: self.dir / "ocr-cache"),
            mock.patch.object(api_module.webview, "windows", []),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api._ocr = mock.Mock()
        self.addCleanup(self.api._doc.close)
        self.pdf = pdf_support.multi_page(self.dir, 4, name="Report.pdf")

    def recent(self) -> list[str]:
        return [entry["path"] for entry in store.get_recent_files()]

    def open_to_organise(self, path=None) -> dict:
        state = self.api.open_pdf_to_organise(str(path or self.pdf))
        self.assertTrue(state["is_open"])
        return state

    def assert_released(self, path: Path) -> None:
        """The file is no longer held: Windows lets it be renamed away."""
        moved = path.with_name(path.stem + " (moved).pdf")
        os.replace(path, moved)
        os.replace(moved, path)


class OpenOnlyToOrganiseTests(_Case):
    def test_opening_leaves_recent_and_the_position_alone(self):
        self.open_to_organise()
        self.assertEqual(self.recent(), [])
        self.assertIsNone(store.get_document_position(str(self.pdf)))

    def test_cancel_closes_it_and_leaves_nothing_behind(self):
        self.open_to_organise()
        self.api.organise_begin()
        self.api.organise_action("rotate", {"positions": [1], "degrees": 90})
        inserted = pdf_support.multi_page(self.dir, 2, name="Extra.pdf")
        source = self.api.organise_add_source(str(inserted))
        self.assertTrue(source.get("ok"), source)
        state = self.api.organise_cancel()
        self.assertEqual((state["is_open"], state["closed"]), (False, True))
        self.assertFalse(self.api._doc.is_open)
        self.assertIsNone(self.api._core.organise)
        self.assertEqual(self.recent(), [])
        self.assertIsNone(store.get_document_position(str(self.pdf)))
        self.assert_released(self.pdf)
        self.assert_released(inserted)
        self.assertEqual(self.api.get_state(), {"is_open": False})
        # Nothing it left beside the PDF.
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.suffix == ".pdf"),
                         ["Extra.pdf", "Report.pdf"])

    def test_cancel_with_nothing_changed_closes_it_too(self):
        self.open_to_organise()
        self.api.organise_begin()
        self.assertTrue(self.api.organise_cancel()["closed"])
        self.assert_released(self.pdf)

    def test_done_makes_it_an_ordinary_open_with_the_changes_unsaved(self):
        self.open_to_organise()
        self.api.organise_begin()
        self.api.organise_action("rotate", {"positions": [1], "degrees": 90})
        state = self.api.organise_done()
        self.assertTrue(state["is_open"])
        self.assertTrue(state["pages_changed"])
        self.assertTrue(state["is_dirty"])
        self.assertEqual(self.recent(), [str(self.pdf)])
        self.assertIsNotNone(store.get_document_position(str(self.pdf)))
        self.assertFalse(self.api._core.opened_for_organise)
        # From here it is the reader's: Cancel of a later Organise keeps it open.
        self.api.organise_begin()
        self.assertTrue(self.api.organise_cancel()["is_open"])

    def test_done_with_nothing_changed_is_an_ordinary_open_too(self):
        self.open_to_organise()
        self.api.organise_begin()
        state = self.api.organise_done()
        self.assertTrue(state["is_open"])
        self.assertFalse(state["is_dirty"])
        self.assertEqual(self.recent(), [str(self.pdf)])

    def test_a_pdf_whose_pages_cant_change_is_an_ordinary_open(self):
        doc = pymupdf.open()
        doc.new_page()
        path = self.dir / "restricted.pdf"
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
                 permissions=pymupdf.PDF_PERM_PRINT)
        doc.close()
        state = self.api.open_pdf_to_organise(str(path))
        self.assertTrue(state["organise_blocked"])
        self.assertFalse(self.api._core.opened_for_organise)
        self.assertEqual(self.recent(), [str(path)])

    def test_a_pdf_that_wont_open_changes_nothing(self):
        state = self.api.open_pdf_to_organise(str(self.dir / "missing.pdf"))
        self.assertIn("error", state)
        self.assertFalse(self.api._doc.is_open)
        self.assertEqual(self.recent(), [])


class OrdinaryOpenTests(_Case):
    def test_the_readers_own_organise_cancel_keeps_it_open(self):
        self.api.open_pdf(str(self.pdf))
        self.assertEqual(self.recent(), [str(self.pdf)])
        self.api.organise_begin()
        state = self.api.organise_cancel()
        self.assertTrue(state["is_open"])
        self.assertNotIn("closed", state)

    def test_opening_normally_after_an_organise_only_open_is_ordinary(self):
        self.open_to_organise()
        self.api.open_pdf(str(self.pdf))
        self.assertFalse(self.api._core.opened_for_organise)
        self.assertEqual(self.recent(), [str(self.pdf)])


if __name__ == "__main__":
    unittest.main()
