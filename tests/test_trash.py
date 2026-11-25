"""Moving an original to the Recycle Bin (Split's "Move the original to the
Recycle Bin afterwards"): never a permanent delete, and a plain reason when
it can't be moved.

On Windows the real shell is used on files in a temporary folder: a file
really goes to the Recycle Bin (its "$I" record is there, so it can be
restored) and the test then empties that one item again; a file another
program holds, a read-only file and a missing one are refused and left
where they were. The guard that stops a delete the shell says can't be
recycled is checked by calling it as the shell would.
"""
import ctypes
import os
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector.bridge import file_tool_jobs  # noqa: E402
from lector.features.file_tools import trash  # noqa: E402

WINDOWS = sys.platform == "win32"


def _long_path(path: str) -> str:
    """`path` with its folder's long names (the temporary folder can be
    spelled "IPSIT_~1"; the Recycle Bin records the long name)."""
    folder, name = os.path.split(path)
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.GetLongPathNameW(folder, buffer, len(buffer)):
        folder = buffer.value
    return os.path.join(folder, name)


def _recycle_bin_records(original: str) -> list[Path]:
    """The "$I" records (and so the items) in this drive's Recycle Bin whose
    original path is `original`."""
    original = _long_path(original)
    root = Path(os.path.splitdrive(original)[0] + "\\$Recycle.Bin")
    found = []
    for record in root.glob("*/$I*"):
        try:
            data = record.read_bytes()
        except OSError:
            continue
        if len(data) < 28:
            continue
        version = struct.unpack_from("<q", data, 0)[0]
        if version == 2:
            length = struct.unpack_from("<i", data, 24)[0]
            path = data[28:28 + 2 * length].decode("utf-16-le").rstrip("\0")
        else:
            path = data[24:24 + 520].decode("utf-16-le").split("\0", 1)[0]
        if os.path.normcase(path) == os.path.normcase(original):
            found.append(record)
    return found


def _empty_items(records: list[Path]) -> None:
    """Remove those items from the Recycle Bin for good (the test's own)."""
    for record in records:
        item = record.with_name("$R" + record.name[2:])
        for path in (item, record):
            try:
                os.remove(path)
            except OSError:
                pass


class ReasonTests(unittest.TestCase):
    def test_every_reason_has_a_plain_sentence(self):
        for reason in (trash.IN_USE, trash.READ_ONLY, trash.NO_PERMISSION, trash.MISSING, trash.NO_BIN, trash.UNKNOWN,
                       trash.STOPPED, trash.CHANGED):
            with self.subTest(reason=reason):
                sentence = trash.reason_sentence(reason)
                self.assertTrue(sentence.endswith("."))
                self.assertNotIn("\\", sentence)
        self.assertEqual(trash.reason_sentence(trash.IN_USE), "it's open in another program.")
        self.assertEqual(trash.reason_sentence("something new"), "something went wrong.")

    def test_a_missing_file_is_refused_before_anything_is_asked(self):
        missing = os.path.join(tempfile.gettempdir(), "lector-no-such-file.pdf")
        self.assertEqual(trash.move_to_trash(missing), trash.MISSING)
        self.assertEqual(trash.move_to_trash(None), trash.MISSING)

    def test_the_place_is_named_for_the_platform(self):
        self.assertEqual(trash.TRASH_NAME, "Trash" if sys.platform == "darwin" else "Recycle Bin")


@unittest.skipUnless(WINDOWS, "the Windows shell")
class WindowsGuardTests(unittest.TestCase):
    def test_a_delete_the_shell_wont_recycle_is_stopped(self):
        guard = trash.RecycleGuard()
        self.assertEqual(guard._pre_delete(None, 0, None), ctypes.c_long(0x80004004).value)
        self.assertTrue(guard.refused)
        odd = trash.RecycleGuard()
        self.assertEqual(odd._pre_delete(None, None, None), trash._E_ABORT)
        kept = trash.RecycleGuard()
        self.assertEqual(kept._pre_delete(None, trash.TSF_DELETE_RECYCLE_IF_POSSIBLE, None), 0)
        self.assertFalse(kept.refused)

    def test_the_operation_asks_to_recycle_and_never_shows_anything(self):
        for flag in (0x0040, 0x00080000, 0x0004, 0x0010, 0x0400, 0x00100000):
            with self.subTest(flag=hex(flag)):
                self.assertTrue(trash.OPERATION_FLAGS & flag)

    def test_what_the_shell_answered_means(self):
        with tempfile.TemporaryDirectory() as folder:
            here = os.path.join(folder, "still.pdf")
            Path(here).write_bytes(b"x")
            gone = os.path.join(folder, "gone.pdf")
            self.assertEqual(trash.windows_outcome(here, 0, True, True, False), trash.NO_BIN)
            self.assertIsNone(trash.windows_outcome(gone, 0, False, False, True))
            self.assertEqual(trash.windows_outcome(here, 0x80070020, False, False, False), trash.IN_USE)
            self.assertEqual(trash.windows_outcome(here, 0x80070005, False, False, False), trash.NO_PERMISSION)
            self.assertEqual(trash.windows_outcome(here, 0x80004005, False, False, False), trash.UNKNOWN)


@unittest.skipUnless(WINDOWS, "the Windows shell")
class WindowsRecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lector-trash-")
        self.folder = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_file_goes_to_the_recycle_bin_and_can_be_restored_from_there(self):
        original = self.folder / "Report.pdf"
        original.write_bytes(b"%PDF-1.4 Lector trash test")
        self.addCleanup(lambda: _empty_items(_recycle_bin_records(str(original))))
        self.assertIsNone(trash.move_to_trash(str(original)))
        self.assertFalse(original.exists())
        self.assertEqual(len(_recycle_bin_records(str(original))), 1, "not in the Recycle Bin: deleted for good?")

    def test_a_file_another_program_holds_is_left_where_it_is(self):
        held = self.folder / "Held.pdf"
        held.write_bytes(b"x")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                         ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        # Open for reading without letting anyone delete it, as a PDF viewer does.
        handle = kernel32.CreateFileW(str(held), 0x80000000, 0x1, None, 3, 0, None)
        self.assertNotIn(handle, (None, ctypes.c_void_p(-1).value))
        try:
            self.assertEqual(trash.move_to_trash(str(held)), trash.IN_USE)
        finally:
            kernel32.CloseHandle(handle)
        self.assertTrue(held.exists())
        self.assertEqual(_recycle_bin_records(str(held)), [])

    def test_a_read_only_file_is_left_where_it_is(self):
        locked = self.folder / "ReadOnly.pdf"
        locked.write_bytes(b"x")
        os.chmod(locked, stat.S_IREAD)
        self.addCleanup(os.chmod, locked, stat.S_IWRITE | stat.S_IREAD)
        self.assertEqual(trash.move_to_trash(str(locked)), trash.READ_ONLY)
        self.assertTrue(locked.exists())



class SplitThenMoveTests(unittest.TestCase):
    """Split's last step (`_then_trash`): the original moves only when the
    job wasn't stopped and the file is the one that was split."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lector-split-trash-")
        self.original = os.path.join(self._tmp.name, "Report.pdf")
        Path(self.original).write_bytes(b"%PDF-1.4 the original")
        self.moved = []
        self.released = []
        self.controller = SimpleNamespace(
            _release_document=self.released.append, _forget_file=lambda path: None)
        patcher = mock.patch.object(trash, "move_to_trash", side_effect=lambda p: self.moved.append(p))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, during=lambda: None, cancelled=False):
        def run(ctx):
            during()
            return {"files": 2}

        job = file_tool_jobs.FileToolJobsController._then_trash(self.controller, run, self.original)
        return job(SimpleNamespace(cancelled=cancelled))

    def test_the_original_moves_after_the_split(self):
        result = self._run()
        self.assertEqual(self.moved, [self.original])
        self.assertEqual(self.released, [self.original])
        self.assertTrue(result["original"]["moved"])

    def test_stop_pressed_after_the_last_file_keeps_the_original(self):
        result = self._run(cancelled=True)
        self.assertEqual(self.moved, [])
        self.assertEqual(result["original"]["reason"], trash.STOPPED)
        self.assertEqual(result["files"], 2)

    def test_an_original_changed_during_the_split_is_kept(self):
        def edit():
            Path(self.original).write_bytes(b"%PDF-1.4 changed by another program, and longer")

        result = self._run(during=edit)
        self.assertEqual(self.moved, [])
        self.assertEqual(self.released, [])
        self.assertEqual(result["original"]["reason"], trash.CHANGED)


if __name__ == "__main__":
    unittest.main()
