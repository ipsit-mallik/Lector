"""settings.json can't be lost to a crash, a damaged file or two writers at
once (docs/HARDENING.md H4).

Every test writes to a temporary settings file, never the real one in
%APPDATA%, the same isolation `test_settings_store.py` uses.
"""
import ast
import errno
import inspect
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.reading import safe_write  # noqa: E402
from lector.features.settings import store  # noqa: E402

STORE_SOURCE = Path(store.__file__)


class _SettingsFileCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="lector-settings-")
        self.folder = Path(self.dir.name)
        self.path = self.folder / "settings.json"
        patcher = mock.patch.object(store, "_settings_path", lambda: self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)
        # A fresh "problems reported" set, so log-once state can't leak between tests.
        ongoing = mock.patch.object(store, "_ongoing", set())
        ongoing.start()
        self.addCleanup(ongoing.stop)

    def write_raw(self, raw: bytes) -> None:
        self.path.write_bytes(raw)

    def corrupt_copies(self) -> list[Path]:
        return sorted(self.folder.glob("settings.json.corrupt-*"))

    def stored(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class InterruptedWriteTests(_SettingsFileCase):
    """A write that fails or is cut short leaves the old file exactly as it
    was. A failure is logged, not raised: a setting is never why a PDF fails
    to open or a page fails to turn (developer decision A)."""

    def setUp(self):
        super().setUp()
        store.set_theme("dark")
        store.add_recent_file("C:/docs/a.pdf")
        self.before = self.path.read_bytes()

    def assert_untouched(self):
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(store.get_theme(), "dark")
        self.assertFalse((self.folder / "settings.json.tmp").exists())

    def test_a_failure_flushing_to_disk_leaves_the_old_file(self):
        with mock.patch.object(store.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")), \
                self.assertLogs(store.log, level="WARNING") as logs:
            store.set_theme("sepia")
        self.assert_untouched()
        self.assertEqual(len(logs.output), 1)

    def test_a_failure_moving_the_new_file_into_place_leaves_the_old_file(self):
        with mock.patch.object(safe_write, "_REPLACE_RETRY_SECONDS", 0), \
                mock.patch.object(safe_write.os, "replace", side_effect=PermissionError(errno.EACCES, "denied")):
            store.add_recent_file("C:/docs/b.pdf")
            store.set_document_position("C:/docs/a.pdf", 4, store.BOOK, 1.0)
        self.assert_untouched()

    def test_a_write_that_keeps_failing_is_logged_once_until_one_succeeds(self):
        with mock.patch.object(store.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")), \
                self.assertLogs(store.log, level="INFO") as logs:
            for page in range(5):
                store.set_document_position("C:/docs/a.pdf", page, store.BOOK, 1.0)
        self.assertEqual(len(logs.output), 1)

        with self.assertLogs(store.log, level="INFO") as logs:
            store.set_theme("sepia")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Resolved", logs.output[0])
        self.assertEqual(store.get_theme(), "sepia")

    def test_being_stopped_halfway_through_writing_leaves_the_old_file(self):
        def half_then_stop(obj, f, **kwargs):
            f.write('{"theme": "se')
            raise KeyboardInterrupt

        with mock.patch.object(store.json, "dump", side_effect=half_then_stop):
            with self.assertRaises(KeyboardInterrupt):
                store.set_theme("sepia")
        self.assert_untouched()

    def test_a_full_disk_leaves_the_old_file(self):
        with mock.patch.object(store.os, "fsync", side_effect=OSError(errno.ENOSPC, "No space left")):
            store.add_recent_file("C:/docs/b.pdf")
        self.assert_untouched()
        self.assertEqual([e["path"] for e in store.get_recent_files()], ["C:/docs/a.pdf"])

    def test_a_successful_write_leaves_no_temporary_file(self):
        store.set_theme("sepia")
        self.assertEqual(store.get_theme(), "sepia")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["settings.json"])


class DamagedFileTests(_SettingsFileCase):
    """A file that is there but doesn't parse is kept, not overwritten."""

    DAMAGED = {
        "truncated mid-write": b'{"theme": "dark", "recent_files": [{"path": "C:/a.pdf", "ope',
        "not an object": b'["theme", "dark"]',
        "not UTF-8": b'{"theme": "\xff\xfe dark"}',
        "nested too deep": b"[" * 100_000,
        "empty": b"",
    }

    def test_a_damaged_file_loads_defaults_and_is_kept_byte_for_byte(self):
        for label, raw in self.DAMAGED.items():
            with self.subTest(label):
                for leftover in self.folder.iterdir():
                    leftover.unlink()
                self.write_raw(raw)

                self.assertEqual(store.get_theme(), "light")
                self.assertEqual(store.get_recent_files(), [])

                copies = self.corrupt_copies()
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0].read_bytes(), raw)
                self.assertFalse(self.path.exists())

    def test_the_next_change_starts_a_fresh_file_and_leaves_the_copy_alone(self):
        raw = self.DAMAGED["truncated mid-write"]
        self.write_raw(raw)

        store.set_theme("sepia")

        self.assertEqual(self.stored(), {"theme": "sepia", "schema_version": store.SCHEMA_VERSION})
        self.assertEqual([c.read_bytes() for c in self.corrupt_copies()], [raw])

    def test_only_the_newest_damaged_copies_are_kept(self):
        for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
            (self.folder / f"settings.json.corrupt-{stamp}").write_bytes(b"old")
        self.write_raw(b"{not json")

        store.get_theme()

        copies = self.corrupt_copies()
        self.assertEqual(len(copies), store.CORRUPT_COPIES_KEPT)
        self.assertEqual(copies[0].name, "settings.json.corrupt-20260103-000000")
        self.assertEqual(copies[-1].read_bytes(), b"{not json")

    def test_a_damaged_file_that_cant_be_moved_aside_is_not_written_over(self):
        raw = self.DAMAGED["truncated mid-write"]
        self.write_raw(raw)

        with mock.patch.object(safe_write.os, "replace", side_effect=OSError(errno.EIO, "I/O error")):
            store.set_theme("sepia")

        self.assertEqual(self.path.read_bytes(), raw)

    def test_two_damaged_files_in_the_same_second_both_keep_their_copy(self):
        fixed = mock.Mock(wraps=store.datetime.datetime)
        fixed.now.return_value = store.datetime.datetime(2026, 10, 25, 11, 39, 31)
        with mock.patch.object(store.datetime, "datetime", fixed):
            self.write_raw(b"{first")
            store.get_theme()
            self.write_raw(b"{second")
            store.get_theme()

        self.assertEqual(
            {c.name: c.read_bytes() for c in self.corrupt_copies()},
            {
                "settings.json.corrupt-20261025-113931": b"{first",
                "settings.json.corrupt-20261025-113931-2": b"{second",
            },
        )

    def test_a_damaged_file_that_stays_stuck_is_logged_once(self):
        self.write_raw(b"{not json")
        with mock.patch.object(safe_write.os, "replace", side_effect=OSError(errno.EIO, "I/O error")), \
                self.assertLogs(store.log, level="WARNING") as logs:
            for _ in range(4):
                store.get_recent_files()
                store.set_theme("sepia")
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.path.read_bytes(), b"{not json")

    def test_a_file_saved_with_a_byte_order_mark_is_still_read(self):
        self.write_raw(b"\xef\xbb\xbf" + json.dumps({"theme": "dark"}).encode("utf-8"))

        self.assertEqual(store.get_theme(), "dark")
        self.assertEqual(self.corrupt_copies(), [])

    def test_nothing_from_the_file_reaches_the_log(self):
        self.write_raw(b'{"recent_files": [{"path": "C:/private/secret-plan.pdf"')

        with self.assertLogs(store.log, level="WARNING") as logs:
            store.get_recent_files()

        self.assertNotIn("secret-plan", "\n".join(logs.output))


class UnreadableFileTests(_SettingsFileCase):
    """A file that is there but can't be read (held by another program,
    no permission) is never replaced with defaults plus one change."""

    def setUp(self):
        super().setUp()
        store.set_theme("dark")
        store.add_favorite("C:/docs/keep.pdf")
        self.before = self.path.read_bytes()
        patcher = mock.patch.object(store, "_READ_RETRY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_change_is_not_saved_over_a_file_that_cant_be_read(self):
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "read_bytes", side_effect=denied):
            self.assertEqual(store.get_theme(), "light")
            store.set_theme("sepia")
            store.add_recent_file("C:/docs/new.pdf")
            store.add_favorite("C:/docs/other.pdf")

        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(store.get_theme(), "dark")
        self.assertTrue(store.is_favorite("C:/docs/keep.pdf"))

    def test_once_the_file_can_be_read_again_changes_are_saved(self):
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "read_bytes", side_effect=denied):
            store.set_theme("sepia")

        store.set_theme("sepia")

        self.assertEqual(store.get_theme(), "sepia")
        self.assertTrue(store.is_favorite("C:/docs/keep.pdf"))

    def test_a_file_held_for_a_moment_is_read_after_a_retry(self):
        held = PermissionError(errno.EACCES, "The process cannot access the file")
        held.winerror = store._WIN_SHARING_VIOLATION
        real_read = Path.read_bytes
        calls = []

        def held_twice(path):
            calls.append(path)
            if len(calls) <= 2:
                raise held
            return real_read(path)

        with mock.patch.object(Path, "read_bytes", held_twice):
            self.assertEqual(store.get_theme(), "dark")
        self.assertEqual(len(calls), 3)

    def test_a_file_that_stays_unreadable_is_logged_once(self):
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "read_bytes", side_effect=denied), \
                self.assertLogs(store.log, level="WARNING") as logs:
            for _ in range(4):
                store.get_theme()
                store.set_theme("sepia")
        self.assertEqual(len(logs.output), 1)

    def test_a_getter_that_couldnt_read_holds_no_later_write(self):
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "read_bytes", side_effect=denied):
            store.get_theme()

        store._save({"theme": "sepia"})

        self.assertEqual(store.get_theme(), "sepia")
        self.assertFalse(store._hold_writes)

    def test_a_nested_setter_does_not_release_the_outer_ones_hold(self):
        inner = store._serialized(lambda: None)

        def outer():
            data = store._load()
            inner()
            store._save({**data, "theme": "sepia"})

        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "read_bytes", side_effect=denied):
            store._serialized(outer)()

        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(store._hold_writes)

    def test_access_denied_is_not_retried(self):
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "read_bytes", side_effect=denied) as read:
            store.get_theme()
        self.assertEqual(read.call_count, 1)


class SchemaVersionTests(_SettingsFileCase):
    def test_every_save_records_the_schema_version(self):
        store.set_progress_bar(False)
        self.assertEqual(self.stored()["schema_version"], store.SCHEMA_VERSION)

    def test_a_file_from_before_the_version_key_loads_as_it_is(self):
        self.write_raw(json.dumps({"theme": "sepia", "recent_view": "list"}).encode("utf-8"))

        self.assertEqual(store.get_theme(), "sepia")
        self.assertEqual(store.get_recent_view(), "list")
        self.assertEqual(self.corrupt_copies(), [])

    def test_a_newer_lectors_version_number_is_kept(self):
        self.write_raw(json.dumps({"schema_version": store.SCHEMA_VERSION + 4}).encode("utf-8"))

        store.set_theme("dark")

        self.assertEqual(self.stored()["schema_version"], store.SCHEMA_VERSION + 4)

    def test_a_nonsense_version_is_replaced_with_this_one(self):
        for bad in ("2", True, None, 0):
            with self.subTest(bad=bad):
                self.write_raw(json.dumps({"schema_version": bad}).encode("utf-8"))
                store.set_theme("dark")
                self.assertEqual(self.stored()["schema_version"], store.SCHEMA_VERSION)

    def test_unknown_keys_survive_every_kind_of_write(self):
        extra = {
            "a_future_setting": {"nested": [1, 2, {"deep": True}]},
            "another": "kept",
        }
        self.write_raw(json.dumps(extra).encode("utf-8"))

        store.set_theme("dark")
        store.add_recent_file("C:/docs/a.pdf")
        store.set_document_position("C:/docs/a.pdf", 2, store.STRIP, 1.5)
        store.add_favorite("C:/docs/a.pdf")
        store.remove_recent_file("C:/docs/a.pdf")
        store.set_outline_width(250)

        stored = self.stored()
        for key, value in extra.items():
            self.assertEqual(stored[key], value)


class LostUpdateTests(_SettingsFileCase):
    """Two setters whose reads and saves would interleave. Each save is
    slowed, so without the lock the second setter always reads before the
    first has saved, and its save drops the first change."""

    def test_two_changes_made_at_once_are_both_kept(self):
        real_save = store._save
        errors = []

        def slow_save(data):
            time.sleep(0.1)
            real_save(data)

        def run(action):
            try:
                action()
            except Exception as exc:  # reported by the assertion below
                errors.append(exc)

        with mock.patch.object(store, "_save", slow_save):
            threads = [
                threading.Thread(target=run, args=(lambda: store.add_favorite("C:/docs/fav.pdf"),)),
                threading.Thread(target=run, args=(lambda: store.add_recent_file("C:/docs/recent.pdf"),)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        self.assertTrue(store.is_favorite("C:/docs/fav.pdf"))
        self.assertEqual([e["path"] for e in store.get_recent_files()], ["C:/docs/recent.pdf"])


class ConcurrentWriterTests(_SettingsFileCase):
    """The H1 reproduction: page turns writing positions on one thread while
    the outline panel is resized on another used to empty Recent and
    Favorites in every run."""

    RUN_SECONDS = 1.0

    def test_two_writers_at_once_lose_nothing(self):
        paths = [f"C:/docs/{i:02}.pdf" for i in range(store.RECENT_FILES_CAP)]
        for path in reversed(paths):
            store.add_recent_file(path)
        store.add_favorite(paths[0])
        deadline = time.monotonic() + self.RUN_SECONDS
        last = {}
        errors = []

        def turn_pages():
            page = 0
            while time.monotonic() < deadline:
                page += 1
                store.set_document_position(paths[0], page, store.BOOK, 1.0)
            last["page"] = page

        def resize_outline():
            width = store.OUTLINE_WIDTH_MIN
            while time.monotonic() < deadline:
                width = store.OUTLINE_WIDTH_MIN + (width + 1) % 200
                store.set_outline_width(width)
            last["width"] = width

        def run(target):
            try:
                target()
            except Exception as exc:  # reported by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(t,)) for t in (turn_pages, resize_outline)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual([e["path"] for e in store.get_recent_files()], paths)
        self.assertTrue(store.is_favorite(paths[0]))
        self.assertEqual(store.get_document_position(paths[0])["page_index"], last["page"])
        self.assertEqual(store.get_outline_width(), last["width"])


class EveryWriterIsSerializedTests(unittest.TestCase):
    """Read-only checks on store.py's source and functions; nothing here
    touches a settings file."""

    def test_every_function_that_saves_holds_the_lock(self):
        tree = ast.parse(STORE_SOURCE.read_text(encoding="utf-8"))
        unguarded = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name == "_save":
                continue
            saves = any(
                isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_save"
                for call in ast.walk(node)
            )
            guarded = any(isinstance(d, ast.Name) and d.id == "_serialized" for d in node.decorator_list)
            if saves and not guarded:
                unguarded.append(node.name)
        self.assertEqual(unguarded, [])

    def test_the_lock_keeps_each_setters_name_signature_and_docstring(self):
        setter = store.set_document_position
        self.assertEqual(setter.__name__, "set_document_position")
        self.assertEqual(
            list(inspect.signature(setter).parameters), ["path", "page_index", "layout_mode", "zoom"]
        )
        self.assertIn("leaving recent-list order alone", setter.__doc__)


if __name__ == "__main__":
    unittest.main()
