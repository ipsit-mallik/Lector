"""Page changes (Milestone F2) against the reader's real undo stack.

Organise pages edits a plan; Done applies it to the open document as ONE
undo entry, which undo and redo swap with the document it held
(docs/ARCHITECTURE.md § "Page changes (Milestone F2)"). Highlight edits on
that same stack name their page by index and their annotation by xref,
through a shared `_HighlightRef`. What this file holds the design to:

* the design gate's spike, kept as regression tests: a highlight undoes
  across a page change, edits above and below one stay in step (which needs
  the shared refs' xrefs restored, not just the bytes), a deleted
  highlighted page survives an overwrite save, a protected PDF stays
  protected;
* undo restores page content and annotations byte for byte, and the reader
  stays on the page it was reading;
* the 3-change and 300 MB caps trim the oldest change and everything below
  it, and say so once;
* saves after a page change leave no trace of a deleted page in the file;
* only a PDF whose owner allows it (modify or assemble) can be changed;
* what MuPDF (1.28.2) does that the design leans on.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402  # PyMuPDF: builds fixtures and reads results back

import pdf_support  # noqa: E402
from lector.features.annotations import highlighter  # noqa: E402
from lector.features.reading import open_errors, page_changes, page_plan  # noqa: E402
from lector.features.reading.document import PdfDocument  # noqa: E402
from lector.features.reading.page_plan import DOC, PlanPage  # noqa: E402

MARKER = b"QUOKKA-ONLY-ON-PAGE-2"


def _plan(*pages) -> page_plan.Plan:
    """A plan of the open document's pages: each an index, or (index, turn)."""
    return tuple(PlanPage(DOC, *(p if isinstance(p, tuple) else (p,))) for p in pages)


def _highlight_xrefs(doc: PdfDocument) -> dict[int, list[int]]:
    """Page index -> the xrefs of its highlights, for pages that have any."""
    found = {
        i: [annot.xref for annot in highlighter.highlight_annots(doc._doc[i])]
        for i in range(doc.page_count)
    }
    return {i: xrefs for i, xrefs in found.items() if xrefs}


def _page_texts(doc: PdfDocument) -> list[str]:
    return [doc._doc[i].get_text().strip()[:6] for i in range(doc.page_count)]


def _fingerprint(doc: PdfDocument) -> list[tuple]:
    """Each page's content stream bytes, rotation and annotation objects."""
    return [
        (
            page.read_contents(),
            page.rotation,
            [doc._doc.xref_object(annot.xref) for annot in page.annots()],
        )
        for page in doc._doc
    ]


def _file_holds(path: Path, needle: bytes) -> bool:
    """Whether `needle` is anywhere in the file: its raw bytes, any object,
    or any stream once decoded (deflated content hides it from a plain
    byte search)."""
    if needle in Path(path).read_bytes():
        return True
    with pymupdf.open(path) as doc:
        for xref in range(1, doc.xref_length()):
            if needle in doc.xref_object(xref).encode("latin-1", "replace"):
                return True
            if doc.xref_is_stream(xref) and needle in (doc.xref_stream(xref) or b""):
                return True
    return False


def _marked_pdf(folder: Path) -> Path:
    """`multi_page`'s 4 pages, page 2 (index 1) also drawing a content
    stream that holds `MARKER` (as a comment), which nothing else uses."""
    path = pdf_support.multi_page(folder, 4, name="source.pdf")
    doc = pymupdf.open(path)
    page = doc[1]
    extra = doc.get_new_xref()
    doc.update_object(extra, "<<>>")
    doc.update_stream(extra, b"% " + MARKER + b"\n")
    contents = " ".join(f"{xref} 0 R" for xref in page.get_contents())
    doc.xref_set_key(page.xref, "Contents", f"[{contents} {extra} 0 R]")
    del page
    marked = Path(folder) / "marked.pdf"
    doc.save(marked)
    doc.close()
    return marked


def _restricted_pdf(folder: Path, permissions: int, user_pw: str = "") -> Path:
    doc = pymupdf.open()
    for n in range(2):
        doc.new_page().insert_text((72, 100), pdf_support.page_text(n))
    path = Path(folder) / f"restricted-{permissions}-{bool(user_pw)}.pdf"
    doc.save(
        path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw=user_pw,
        permissions=permissions,
    )
    doc.close()
    return path


class _DocCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.doc = PdfDocument()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.doc.close)

    def _open(self, pages: int = 4) -> None:
        self.doc.open(str(pdf_support.multi_page(self.folder, pages)))


class PageChangeUndoTests(_DocCase):
    """The design gate's spike, now against `apply_page_plan`."""

    def test_the_spike_highlight_undo_across_a_page_change(self):
        # A highlight on page 3; a plan that deletes page 2 (the one before
        # it) and moves page 3 to the front, turned; undo the page change;
        # undo the highlight.
        self._open(4)
        xref = self.doc.highlight_word_indices(2, 0, 1, "green")
        self.assertEqual(_highlight_xrefs(self.doc), {2: [xref]})

        self.assertTrue(self.doc.apply_page_plan(_plan((2, 90), 0, 3)))
        self.assertEqual(_page_texts(self.doc), ["Page 3", "Page 1", "Page 4"])
        self.assertEqual(self.doc._doc[0].rotation, 90)
        self.assertEqual(_highlight_xrefs(self.doc), {0: [xref]})  # moved, same xref

        self.doc.undo_highlight()
        self.assertEqual(_page_texts(self.doc), ["Page 1", "Page 2", "Page 3", "Page 4"])
        self.assertEqual(self.doc._doc[2].rotation, 0)
        self.assertEqual(_highlight_xrefs(self.doc), {2: [xref]})

        self.assertEqual(self.doc.undo_highlight(), 2)
        self.assertEqual(_highlight_xrefs(self.doc), {})
        self.assertFalse(self.doc.can_undo)

        # Forwards again: the highlight (re-added under a new xref), then the
        # page change, which brings back the document as it was undone — the
        # highlight under its old xref, and the shared ref with it.
        self.doc.redo_highlight()
        self.assertNotEqual(_highlight_xrefs(self.doc), {2: [xref]})
        self.doc.redo_highlight()
        self.assertEqual(self.doc.page_count, 3)
        self.assertEqual(self.doc._doc[0].rotation, 90)
        self.assertEqual(_highlight_xrefs(self.doc), {0: [xref]})
        self.assertEqual(self.doc._undo_stack[0].ref.xref, xref)

        # Back down once more: still in step.
        self.doc.undo_highlight()
        self.assertEqual(self.doc.undo_highlight(), 2)
        self.assertEqual(_highlight_xrefs(self.doc), {})

    def test_an_edit_made_after_the_page_change_keeps_the_stack_in_step(self):
        # E1 (add) | page change | E2 (remove). Undoing E2 re-adds the
        # highlight under a NEW xref and repoints the shared ref; undoing the
        # page change must put the ref back, or E1 looks for the wrong xref.
        self._open(4)
        first = self.doc.highlight_word_indices(2, 0, 1, "green")
        self.doc.apply_page_plan(_plan(2, 0, 3))
        self.assertTrue(self.doc.remove_highlight(0, first))  # it is on index 0 now

        self.assertEqual(self.doc.undo_highlight(), 0)  # E2
        second = _highlight_xrefs(self.doc)[0][0]
        self.doc.undo_highlight()  # the page change
        self.assertEqual(_highlight_xrefs(self.doc), {2: [first]})
        self.assertEqual(self.doc.undo_highlight(), 2)  # E1 finds it
        self.assertEqual(_highlight_xrefs(self.doc), {})

        self.doc.redo_highlight()  # E1
        self.doc.redo_highlight()  # the page change
        self.assertEqual(_highlight_xrefs(self.doc), {0: [second]})
        self.assertEqual(self.doc.redo_highlight(), 0)  # E2 removes it again
        self.assertEqual(_highlight_xrefs(self.doc), {})

    def test_deleting_a_highlighted_page_keeps_history_through_an_overwrite_save(self):
        self._open(4)
        kept = self.doc.highlight_word_indices(0, 0, 1, "blue")
        dropped = self.doc.highlight_word_indices(1, 0, 1, "pink")
        self.doc.apply_page_plan(_plan(0, 2, 3))
        self.assertEqual(_highlight_xrefs(self.doc), {0: [kept]})

        self.doc.save_overwrite()  # its staleness check must not clear history
        self.assertTrue(self.doc.can_undo)
        self.assertFalse(self.doc.is_dirty)

        self.doc.undo_highlight()
        self.assertEqual(_highlight_xrefs(self.doc), {0: [kept], 1: [dropped]})
        self.assertTrue(self.doc.is_dirty)  # the file on disk has 3 pages
        self.assertEqual(self.doc.undo_highlight(), 1)
        self.assertEqual(self.doc.undo_highlight(), 0)
        self.assertEqual(_highlight_xrefs(self.doc), {})

    def test_an_encrypted_pdf_round_trips_with_its_password_and_xrefs(self):
        path = pdf_support.multi_page(self.folder, 3, name="plain.pdf")
        locked = self.folder / "locked.pdf"
        with pymupdf.open(path) as doc:
            doc.save(
                locked, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                user_pw=pdf_support.PASSWORD, owner_pw=pdf_support.PASSWORD[::-1],
            )
        self.doc.open(str(locked), pdf_support.PASSWORD)
        xref = self.doc.highlight_word_indices(2, 0, 1, "green")

        self.doc.apply_page_plan(_plan(2, 0))
        self.assertEqual(_highlight_xrefs(self.doc), {0: [xref]})
        self.doc.undo_highlight()
        self.assertEqual(_highlight_xrefs(self.doc), {2: [xref]})
        self.assertEqual(self.doc.undo_highlight(), 2)

        data, password = self.doc.snapshot()
        self.assertEqual(password, pdf_support.PASSWORD)
        self.assertTrue(pymupdf.open(stream=data, filetype="pdf").needs_pass)  # still protected

        self.doc.redo_highlight()
        self.doc.redo_highlight()
        copy = self.folder / "copy.pdf"
        self.doc.save_copy(str(copy))
        with pymupdf.open(copy) as saved:
            self.assertTrue(saved.needs_pass)


class ApplyTests(_DocCase):
    def test_a_plan_gives_its_order_and_rotations(self):
        self._open(5)
        self.doc._doc[3].set_rotation(90)  # a page already turned turns on from there
        self.doc.apply_page_plan(_plan((4, 180), (3, 90), (0, 270)))
        self.assertEqual([p.get_text().strip()[:6] for p in self.doc._doc], ["Page 5", "Page 4", "Page 1"])
        self.assertEqual([p.rotation for p in self.doc._doc], [180, 180, 270])
        self.assertTrue(self.doc.is_dirty)

    def test_undo_restores_content_and_annotations_byte_for_byte(self):
        self._open(4)
        self.doc.highlight_word_indices(1, 0, 2, "green")
        self.doc.highlight_word_indices(3, 1, 3, "blue", opacity=1.0)
        before = _fingerprint(self.doc)
        self.doc.apply_page_plan(_plan((3, 90), 0, 2))
        self.assertNotEqual(_fingerprint(self.doc), before)
        self.doc.undo_highlight()
        self.assertEqual(_fingerprint(self.doc), before)

    def test_the_reader_stays_on_the_page_it_was_reading(self):
        self._open(5)
        self.doc.go_to_page(2)
        self.doc.apply_page_plan(_plan(4, 2, 0, 1, 3))
        self.assertEqual(self.doc.page_index, 1)  # page 3 is second now
        self.doc.undo_highlight()
        self.assertEqual(self.doc.page_index, 2)
        self.doc.redo_highlight()
        self.assertEqual(self.doc.page_index, 1)

    def test_a_deleted_current_page_leaves_the_reader_on_the_one_before_it(self):
        self._open(5)
        self.doc.go_to_page(3)
        self.doc.apply_page_plan(_plan(4, 0, 2))  # page 4 deleted; page 3 is last now
        self.assertEqual(self.doc.page_index, 2)
        self.doc.go_to_page(0)
        self.doc.apply_page_plan(_plan(1, 2))  # page 5 (first now) deleted, none before it
        self.assertEqual(self.doc.page_index, 0)

    def test_an_unchanged_plan_records_nothing(self):
        self._open(3)
        self.assertFalse(self.doc.apply_page_plan(page_plan.initial(3)))
        self.assertFalse(self.doc.can_undo)
        self.assertFalse(self.doc.is_dirty)

    def test_a_plan_that_isnt_this_documents_changes_nothing(self):
        self._open(3)
        before = _fingerprint(self.doc)
        for plan in ((), _plan(0, 0), _plan(0, 3), (PlanPage("insert-9", 0),)):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.doc.apply_page_plan(plan)
        self.assertEqual(_fingerprint(self.doc), before)
        self.assertFalse(self.doc.can_undo)

    def test_the_undo_label_names_the_page_changes(self):
        self._open(3)
        self.doc.highlight_word_indices(0, 0, 1)
        self.assertEqual(self.doc.undo_kind, page_changes.HIGHLIGHT)
        self.doc.apply_page_plan(_plan(1, 0, 2))
        self.assertEqual(self.doc.undo_kind, page_changes.PAGE_CHANGES)
        self.assertIsNone(self.doc.redo_kind)
        self.doc.undo_highlight()
        self.assertEqual(self.doc.redo_kind, page_changes.PAGE_CHANGES)
        self.assertEqual(self.doc.undo_kind, page_changes.HIGHLIGHT)

    def test_an_appearance_gesture_never_merges_into_a_page_change(self):
        self._open(3)
        xref = self.doc.highlight_word_indices(0, 0, 1)
        self.doc.set_highlight_appearance(0, xref, opacity=0.5, gesture="drag-1")
        self.doc.apply_page_plan(_plan(1, 0, 2))
        self.assertTrue(self.doc.set_highlight_appearance(1, xref, opacity=0.8, gesture="drag-1"))
        self.assertEqual(self.doc.undo_kind, page_changes.HIGHLIGHT)
        self.doc.undo_highlight()  # the opacity, after the page change
        self.assertEqual(self.doc.undo_kind, page_changes.PAGE_CHANGES)

    def test_pages_from_another_pdf_come_in_with_their_annotations(self):
        self._open(2)
        other = PdfDocument()
        self.addCleanup(other.close)
        other.open(str(pdf_support.multi_page(self.folder, 3, name="other.pdf")))
        other.highlight_word_indices(1, 0, 1, "pink")
        data, _ = other.snapshot()

        source, count = self.doc.add_page_source(data)
        self.assertEqual(count, 3)
        plan, new = page_plan.insert(page_plan.initial(2), 1, source, count)
        self.assertEqual(new, (1, 2, 3))
        self.assertIsNotNone(self.doc.render_plan_thumbnail(source, 2, 64))
        self.doc.apply_page_plan(plan)
        self.assertEqual(self.doc.page_count, 5)
        self.assertEqual(_page_texts(self.doc), ["Page 1", "Page 1", "Page 2", "Page 3", "Page 2"])
        self.assertEqual(list(_highlight_xrefs(self.doc)), [2])
        self.doc.undo_highlight()
        self.assertEqual(self.doc.page_count, 2)

    def test_a_protected_source_needs_its_password(self):
        self._open(1)
        data = pdf_support.encrypted(self.folder).read_bytes()
        with self.assertRaises(open_errors.OpenError) as caught:
            self.doc.add_page_source(data)
        self.assertEqual(caught.exception.reason, open_errors.PASSWORD)
        with self.assertRaises(open_errors.OpenError):
            self.doc.add_page_source(data, "wrong")
        source, count = self.doc.add_page_source(data, pdf_support.PASSWORD)
        self.doc.apply_page_plan(page_plan.insert(page_plan.initial(1), 1, source, count)[0])
        self.assertEqual(self.doc.page_count, 2)
        self.assertIn("quick brown fox", self.doc._doc[1].get_text())

    def test_bookmarks_and_links_to_deleted_pages_go_and_the_rest_follow(self):
        source = pymupdf.open()
        for n in range(4):
            source.new_page().insert_text((72, 100), f"Page {n + 1}")
        source.set_toc([[1, "One", 1], [1, "Two", 2], [1, "Three", 3], [2, "Three-a", 3], [1, "Four", 4]])
        page = source[0]
        page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 50, 50), "page": 1})
        page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(60, 10, 90, 50), "page": 3})
        del page
        path = self.folder / "linked.pdf"
        source.save(path)
        source.close()

        self.doc.open(str(path))
        self.doc.apply_page_plan(_plan(3, 0, 2))  # page 2 deleted; page 4 first
        self.assertEqual(
            self.doc._doc.get_toc(),
            [[1, "One", 2], [1, "Three", 3], [2, "Three-a", 3], [1, "Four", 1]],
        )
        links = [(link["kind"], link["page"]) for link in self.doc._doc[1].get_links()]
        self.assertEqual(links, [(pymupdf.LINK_GOTO, 0)])  # only the link to page 4, now first
        self.doc.undo_highlight()
        self.assertEqual(len(self.doc._doc.get_toc()), 5)


class CapTests(_DocCase):
    def test_a_fourth_change_drops_the_oldest_and_everything_below_it(self):
        self._open(4)
        self.doc.highlight_word_indices(0, 0, 1)  # below every page change
        for _ in range(3):
            self.doc.apply_page_plan(_plan(3, 0, 1, 2))
        self.assertFalse(self.doc.take_history_trimmed())
        self.doc.highlight_word_indices(0, 0, 1, "pink")  # between changes 3 and 4
        self.doc.apply_page_plan(_plan(3, 0, 1, 2))

        kinds = [type(entry).__name__ for entry in self.doc._undo_stack]
        self.assertEqual(kinds, ["PageChange", "PageChange", "_Edit", "PageChange"])
        self.assertTrue(self.doc.take_history_trimmed())
        self.assertFalse(self.doc.take_history_trimmed())  # said once
        for _ in range(4):
            self.doc.undo_highlight()
        self.assertFalse(self.doc.can_undo)
        # As it was after the first change: its highlight stays, now on page 2.
        self.assertEqual(list(_highlight_xrefs(self.doc)), [1])

    def test_the_byte_cap_trims_too(self):
        self._open(4)
        size = len(self.doc.snapshot()[0])
        # Room for one snapshot, not two (a reordered one can differ by a few bytes).
        with mock.patch.object(page_changes, "MAX_PAGE_CHANGE_BYTES", size * 3 // 2):
            self.doc.apply_page_plan(_plan(1, 0, 2, 3))
            self.assertFalse(self.doc.take_history_trimmed())
            self.doc.apply_page_plan(_plan(1, 0, 2, 3))
            self.assertTrue(self.doc.take_history_trimmed())
        self.assertEqual(len(self.doc._undo_stack), 1)

    def test_trim_reaches_the_redo_stack_when_the_undo_stack_has_no_change(self):
        held = page_changes.DocState(b"x" * 10, {}, ())
        changes = [page_changes.PageChange(held, (0,)) for _ in range(3)]
        undo, redo, trimmed = page_changes.trim(["edit"], changes, max_count=2)
        self.assertEqual((undo, redo, trimmed), (["edit"], changes[1:], True))


class SaveAfterPageChangeTests(_DocCase):
    def test_a_deleted_page_leaves_no_trace_in_the_saved_file(self):
        self.doc.open(str(_marked_pdf(self.folder)))
        before = self.folder / "before.pdf"
        self.doc.save_copy(str(before))
        self.assertTrue(_file_holds(before, MARKER))  # the check can see it

        xref = self.doc.highlight_word_indices(1, 0, 1, "green")  # on the page to delete
        self.doc.apply_page_plan(_plan(0, 2, 3))
        copy = self.folder / "copy.pdf"
        self.doc.save_copy(str(copy))
        self.assertFalse(_file_holds(copy, MARKER))
        with pymupdf.open(copy) as saved:
            self.assertEqual(len(saved), 3)

        self.doc.undo_highlight()  # Undo page changes: the page, and its highlight
        self.assertEqual(_page_texts(self.doc), ["Page 1", "Page 2", "Page 3", "Page 4"])
        self.assertEqual(_highlight_xrefs(self.doc), {1: [xref]})
        self.assertEqual(self.doc.undo_highlight(), 1)

    def test_an_overwrite_save_leaves_no_trace_either(self):
        path = _marked_pdf(self.folder)
        self.doc.open(str(path))
        self.doc.apply_page_plan(_plan(0, 2, 3))
        self.doc.save_overwrite()
        self.assertFalse(_file_holds(path, MARKER))
        self.doc.undo_highlight()
        self.assertEqual(self.doc.page_count, 4)


class PermissionTests(_DocCase):
    def test_a_pdf_whose_owner_forbids_changes_is_blocked(self):
        self.doc.open(str(_restricted_pdf(self.folder, pymupdf.PDF_PERM_PRINT)))
        self.assertEqual(self.doc.page_change_block(), page_changes.RESTRICTED)
        with self.assertRaises(ValueError):
            self.doc.apply_page_plan(_plan(1, 0))
        self.assertEqual(self.doc.page_count, 2)

    def test_either_modify_or_assemble_is_enough(self):
        for permission in (pymupdf.PDF_PERM_MODIFY, pymupdf.PDF_PERM_ASSEMBLE):
            with self.subTest(permission=permission):
                self.doc.open(str(_restricted_pdf(self.folder, permission)))
                self.assertIsNone(self.doc.page_change_block())
                self.assertTrue(self.doc.apply_page_plan(_plan(1, 0)))

    def test_the_owner_password_has_every_permission(self):
        path = _restricted_pdf(self.folder, pymupdf.PDF_PERM_PRINT, user_pw="user")
        self.doc.open(str(path), "user")
        self.assertEqual(self.doc.page_change_block(), page_changes.RESTRICTED)
        self.doc.open(str(path), "owner")
        self.assertIsNone(self.doc.page_change_block())
        self.doc.apply_page_plan(_plan(1, 0))
        self.doc.undo_highlight()  # reopened from its bytes with the same password
        self.assertIsNone(self.doc.page_change_block())

    def test_nothing_open_is_not_loaded(self):
        self.assertEqual(PdfDocument().page_change_block(), page_changes.NOT_LOADED)


class MuPdfBehaviourTests(unittest.TestCase):
    """What MuPDF (1.28.2) does that the design leans on; an upgrade that
    changes any of it fails here first."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_delete_pages_would_leave_a_bookmark_pointing_nowhere(self):
        # Why the plan is applied with select(), not delete_pages().
        doc = pymupdf.open()
        for n in range(3):
            doc.new_page().insert_text((72, 100), f"Page {n + 1}")
        doc.set_toc([[1, "One", 1], [1, "Two", 2], [1, "Three", 3]])
        doc = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
        doc.delete_pages([1])
        self.assertIn([1, "Two", -1], doc.get_toc())

    def test_garbage_1_drops_a_deleted_page_without_renumbering(self):
        doc = PdfDocument()
        self.addCleanup(doc.close)
        doc.open(str(pdf_support.multi_page(self.folder, 4)))
        moved = doc.highlight_word_indices(2, 0, 1, "green")
        gone_page = doc._doc.page_xref(1)
        gone_contents = doc._doc[1].get_contents()
        doc._doc.select([2, 0, 3])

        plain, collected = self.folder / "plain.pdf", self.folder / "collected.pdf"
        doc._doc.save(plain, deflate=True)
        doc._doc.save(collected, deflate=True, garbage=1)
        self.assertEqual(_highlight_xrefs(doc), {0: [moved]})  # the live document is untouched

        with pymupdf.open(plain) as saved:  # without it, the deleted page stays in the file
            self.assertNotEqual(saved.xref_object(gone_page), "null")
        with pymupdf.open(collected) as saved:
            self.assertEqual(saved.xref_object(gone_page), "null")
            for xref in gone_contents:
                self.assertEqual(saved.xref_object(xref), "null")
            self.assertEqual(
                [annot.xref for annot in highlighter.highlight_annots(saved[0])], [moved]
            )

    def test_mupdf_does_not_enforce_permissions_so_the_reader_must(self):
        path = _restricted_pdf(self.folder, pymupdf.PDF_PERM_PRINT)
        with pymupdf.open(path) as restricted:
            self.assertFalse(restricted.needs_pass)
            self.assertFalse(restricted.permissions & pymupdf.PDF_PERM_MODIFY)
            self.assertFalse(restricted.permissions & pymupdf.PDF_PERM_ASSEMBLE)
            restricted.select([1, 0])
            self.assertEqual(len(restricted), 2)


if __name__ == "__main__":
    unittest.main()
