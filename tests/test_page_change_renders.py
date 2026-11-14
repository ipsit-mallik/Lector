"""Page changes never pull the document out from under a render (F2.3).

The reader asks for pages and thumbnails on bridge threads of their own:
the Organise grid's thumbnails, the reader's page images (and the strip's
look-ahead), the outline panel's and the progress bar's previews, find. Done,
Undo page changes and Redo page changes replace the open document (or
rearrange it in place), so each waits for every read already running, and
reads asked for meanwhile wait until the change is complete
(`reading/doc_gate.py`).
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.reading import doc_gate, ocr_cache  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.settings import store  # noqa: E402

# How long a test lets a swap try to get past a held render before deciding
# that it is waiting, as it should.
HELD_S = 0.4


class _ApiCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        for patcher in (
            mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json"),
            mock.patch.object(ocr_cache, "cache_dir", lambda: self.dir / "ocr-cache"),
            mock.patch.object(api_module.webview, "windows", [mock.Mock()]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.api._voice = mock.MagicMock()
        self.api._ocr = mock.Mock()
        self.addCleanup(self.api._doc.close)
        self.api.open_pdf(str(pdf_support.multi_page(self.dir, 6)))

    def _draft_deleting_two(self):
        self.api.organise_begin()
        self.api.organise_action("delete", {"positions": [0, 1]})


class HeldRenderTests(_ApiCase):
    def test_done_waits_for_a_thumbnail_being_drawn_and_nothing_fails(self):
        inside, release = threading.Event(), threading.Event()
        real = PdfDocument._thumbnail

        def slow_thumbnail(page, width_px, turn=0):
            inside.set()
            release.wait(5)
            return real(page, width_px, turn)

        results, errors = {}, []

        def render():
            try:
                results["thumb"] = self.api.organise_thumbnail("doc", 5, 92)
            except Exception as exc:  # what this test is here to catch
                errors.append(exc)

        def done():
            try:
                results["done"] = self.api.organise_done()
            except Exception as exc:
                errors.append(exc)

        self._draft_deleting_two()
        with mock.patch.object(PdfDocument, "_thumbnail", staticmethod(slow_thumbnail)):
            renderer = threading.Thread(target=render)
            renderer.start()
            self.assertTrue(inside.wait(5))
            applier = threading.Thread(target=done)
            applier.start()
            applier.join(HELD_S)
            self.assertTrue(applier.is_alive(), "Done went ahead while a thumbnail was being drawn")
            self.assertEqual(self.api._doc.page_count, 6)
            release.set()
            renderer.join(5)
            applier.join(5)

        self.assertEqual(errors, [])
        self.assertIsNotNone(results["thumb"]["image"])  # drawn from the document it started on
        self.assertTrue(results["done"]["pages_changed"])
        self.assertEqual(self.api._doc.page_count, 4)

    def test_a_render_asked_for_during_a_change_waits_for_it(self):
        self._draft_deleting_two()
        self.api.organise_done()
        inside, release = threading.Event(), threading.Event()
        real = self.api._doc._restore_state

        def slow_restore(state):
            inside.set()
            release.wait(5)
            real(state)

        out, errors = {}, []

        def render():
            try:
                out["image"] = self.api.get_page_image(5, 1.0)
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(self.api._doc, "_restore_state", side_effect=slow_restore):
            undoer = threading.Thread(target=self.api.undo_highlight)
            undoer.start()
            self.assertTrue(inside.wait(5))
            renderer = threading.Thread(target=render)
            renderer.start()
            renderer.join(HELD_S)
            self.assertTrue(renderer.is_alive(), "a render ran in the middle of Undo page changes")
            release.set()
            undoer.join(5)
            renderer.join(5)
        self.assertEqual(errors, [])
        self.assertIsNotNone(out["image"]["image"])  # page 6 exists again after the undo


class ReviewRaceTests(_ApiCase):
    """Found in the F2.3 review; each hung, lost a file or desynced before."""

    def _run_together(self, *targets, timeout=10):
        errors = []

        def guarded(fn):
            def run():
                try:
                    fn()
                except Exception as exc:
                    errors.append(exc)
            return run

        threads = [threading.Thread(target=guarded(fn)) for fn in targets]
        for thread in threads:
            thread.start()
            time.sleep(0.15)  # in order: each starts while the one before is busy
        for thread in threads:
            thread.join(timeout)
        self.assertFalse(any(thread.is_alive() for thread in threads), "deadlocked")
        self.assertEqual(errors, [])

    def test_a_save_and_a_page_change_at_once_both_finish(self):
        # The save held the save lock and its check then waited on the gate,
        # while the change held the gate and waited on the save lock.
        real_write = PdfDocument._write_full

        def slow_write(doc, path, **options):
            time.sleep(0.5)
            real_write(doc, path, **options)

        self._draft_deleting_two()
        copy = self.dir / "copy.pdf"
        with mock.patch.object(PdfDocument, "_write_full", slow_write):
            self._run_together(
                lambda: self.assertTrue(self.api.perform_save("copy", str(copy))["ok"]),
                lambda: self.assertTrue(self.api.organise_done()["pages_changed"]),
            )
        self.assertEqual(self.api._doc.page_count, 4)
        self.assertTrue(copy.exists())

    def test_an_open_while_an_undo_waits_keeps_the_new_pdf(self):
        # The undo popped its page change, then waited; an open slipped in,
        # and the stale change replaced the new PDF with the old one's pages.
        self._draft_deleting_two()
        self.api.organise_done()
        other = pdf_support.multi_page(self.dir, 2, name="other.pdf")
        inside, release = threading.Event(), threading.Event()
        real = PdfDocument._thumbnail

        def held_thumbnail(page, width_px):
            inside.set()
            release.wait(5)
            return real(page, width_px)

        with mock.patch.object(PdfDocument, "_thumbnail", staticmethod(held_thumbnail)):
            reader = threading.Thread(target=lambda: self.api.get_page_thumbnail(0, 64))
            reader.start()
            self.assertTrue(inside.wait(5))
            undoer = threading.Thread(target=self.api.undo_highlight)
            undoer.start()
            time.sleep(0.2)
            opener = threading.Thread(target=lambda: self.api.open_pdf(str(other)))
            opener.start()
            time.sleep(0.2)
            release.set()
            for thread in (reader, undoer, opener):
                thread.join(10)
        self.assertEqual(self.api._doc.page_count, 2)
        self.assertTrue(self.api._doc.path.endswith("other.pdf"))
        self.assertFalse(self.api.get_state()["is_dirty"])

    def test_two_quick_undos_report_what_each_took_back(self):
        # A highlight on top of a page change: the page sends two
        # highlight-kind undos; the second must say it took back pages.
        self._draft_deleting_two()
        self.api.organise_done()
        self.api.highlight_chars(0, 0, 3)
        replies = []
        self._run_together(
            lambda: replies.append(self.api.undo_highlight()),
            lambda: replies.append(self.api.undo_highlight()),
        )
        self.assertEqual(sorted(r["pages_changed"] for r in replies), [False, True])
        self.assertEqual(self.api._doc.page_count, 6)
        self.assertEqual(self.api.get_state()["redo_kind"], "page_changes")


class ConcurrentRenderTests(_ApiCase):
    def test_renders_running_through_done_undo_and_redo_never_fail(self):
        stop = threading.Event()
        errors = []

        def keep_rendering():
            while not stop.is_set():
                try:
                    for index in range(6):
                        self.api.organise_thumbnail("doc", index, 64)
                        self.api.get_page_thumbnail(index, 64)
                        self.api.get_page_image(index, 1.0)
                    self.api.get_page_sizes()
                    self.api.find_in_document("topic", 0, None)
                except Exception as exc:
                    errors.append(exc)
                    return

        renderers = [threading.Thread(target=keep_rendering) for _ in range(3)]
        for thread in renderers:
            thread.start()
        try:
            for _ in range(4):
                self._draft_deleting_two()
                self.assertTrue(self.api.organise_done()["pages_changed"])
                self.assertTrue(self.api.undo_highlight()["pages_changed"])
                self.assertTrue(self.api.redo_highlight()["pages_changed"])
                self.assertTrue(self.api.undo_highlight()["pages_changed"])
                time.sleep(0.01)
        finally:
            stop.set()
            for thread in renderers:
                thread.join(10)
        self.assertEqual(errors, [])


class GateTests(unittest.TestCase):
    def test_a_reader_may_read_again_inside_its_own_read(self):
        gate = doc_gate.DocGate()
        with gate.reading(), gate.reading():
            pass

    def test_the_changer_may_read_while_it_changes(self):
        gate = doc_gate.DocGate()
        with gate.changing(), gate.reading():
            pass

    def test_a_waiting_change_holds_back_new_readers(self):
        gate = doc_gate.DocGate()
        order = []
        first_in, release = threading.Event(), threading.Event()

        def first_reader():
            with gate.reading():
                first_in.set()
                release.wait(5)
                order.append("first read")

        def changer():
            with gate.changing():
                order.append("change")

        def late_reader():
            with gate.reading():
                order.append("late read")

        threads = [threading.Thread(target=first_reader)]
        threads[0].start()
        first_in.wait(5)
        threads.append(threading.Thread(target=changer))
        threads[1].start()
        time.sleep(0.1)  # the change is now waiting for the first read
        threads.append(threading.Thread(target=late_reader))
        threads[2].start()
        time.sleep(0.1)
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(order, ["first read", "change", "late read"])


if __name__ == "__main__":
    unittest.main()
