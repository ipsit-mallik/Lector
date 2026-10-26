"""Only one Lector runs at a time.

A second launch hands its file (if it was given one) to the running Lector,
which comes to the front and opens it the way it opens a dropped PDF, and the
second launch then exits. The lock is a per-user named pipe on Windows (a Unix
socket elsewhere): only one process can create the first instance of a pipe
name, so the claim is atomic.

The message is JSON read with a size cap, never `Connection.recv()`: that
unpickles, and any local process can connect.

Both ends prove they hold this user's key before a path is sent, so a program
that took the pipe name first never learns which file is being opened, and one
without the key can't make Lector open anything.
"""

import json
import os
import pickle
import sys
import tempfile
import threading
import time
import unittest
import uuid
from multiprocessing.connection import Client, Listener, answer_challenge, deliver_challenge
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import __main__ as app_main  # noqa: E402
from lector.shared import single_instance  # noqa: E402

WAIT = 5
KEY = b"k" * single_instance.KEY_BYTES


def _address() -> str:
    """A fresh address per test, so no test can meet another's lock."""
    name = f"lector-test-{uuid.uuid4().hex}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{name}"
    return str(Path(os.environ.get("TMPDIR", "/tmp")) / f"{name}.sock")


class _Received:
    """Records what the running instance was handed."""

    def __init__(self):
        self.items: list[str | None] = []
        self.arrived = threading.Event()

    def __call__(self, path):
        self.items.append(path)
        self.arrived.set()

    def wait(self):
        if not self.arrived.wait(WAIT):
            raise AssertionError("nothing was handed over")
        self.arrived.clear()
        return self.items[-1]


class ClaimTest(unittest.TestCase):
    def setUp(self):
        self.address = _address()
        self.received = _Received()
        self.lock = single_instance.claim(self.address, None, self.received, authkey=KEY)
        self.assertIsNotNone(self.lock)
        self.addCleanup(self.lock.release)

    def test_a_second_launch_hands_its_file_over_and_does_not_claim(self):
        self.assertIsNone(single_instance.claim(self.address, r"C:\docs\a.pdf", lambda p: None, authkey=KEY))
        self.assertEqual(self.received.wait(), r"C:\docs\a.pdf")

    def test_a_second_launch_with_no_file_still_brings_lector_forward(self):
        self.assertIsNone(single_instance.claim(self.address, None, lambda p: None, authkey=KEY))
        self.assertIsNone(self.received.wait())

    def test_every_later_launch_is_heard_not_just_the_first(self):
        for path in ("one.pdf", "two.pdf", "three.pdf"):
            single_instance.claim(self.address, path, lambda p: None, authkey=KEY)
            self.assertEqual(self.received.wait(), path)

    def test_once_released_the_next_launch_claims(self):
        self.lock.release()
        again = single_instance.claim(self.address, None, lambda p: None, authkey=KEY)
        self.assertIsNotNone(again)
        again.release()

    def _send_raw(self, payload: bytes, key: bytes | None = KEY):
        with Client(self.address, family=single_instance.FAMILY) as conn:
            try:
                if key is not None:
                    answer_challenge(conn, key)
                    deliver_challenge(conn, key)
                conn.send_bytes(payload)
            except (BrokenPipeError, EOFError, single_instance.AuthenticationError):
                pass  # cut off mid-write, or turned away; that is the point

    def _assert_still_listening_and_nothing_arrived(self):
        single_instance.claim(self.address, "real.pdf", lambda p: None, authkey=KEY)
        self.assertEqual(self.received.wait(), "real.pdf")
        self.assertEqual(self.received.items, ["real.pdf"])

    def test_messages_that_are_not_the_json_shape_are_ignored(self):
        for payload in (
            pickle.dumps({"path": "a.pdf"}),
            b"not json",
            json.dumps(["a.pdf"]).encode(),
            json.dumps({"path": 7}).encode(),
            b"x" * (single_instance.MAX_MESSAGE + 1),
        ):
            with self.subTest(payload=payload[:20]):
                self._send_raw(payload)
        self._assert_still_listening_and_nothing_arrived()

    def test_a_launch_without_the_key_cannot_have_a_file_opened(self):
        message = json.dumps({"path": "planted.pdf"}).encode()
        with self.assertLogs(single_instance.log, level="WARNING"):
            self._send_raw(message, key=None)
            self._send_raw(message, key=b"w" * single_instance.KEY_BYTES)
        self._assert_still_listening_and_nothing_arrived()

    def test_a_handler_that_fails_does_not_stop_the_lock_listening(self):
        self.received.items.clear()
        failing = single_instance.claim(
            _address(), None, mock.Mock(side_effect=RuntimeError("boom")), authkey=KEY
        )
        self.addCleanup(failing.release)
        with self.assertLogs(single_instance.log, level="ERROR"):
            single_instance.claim(failing.address, "a.pdf", lambda p: None, authkey=KEY)
            single_instance.claim(failing.address, "b.pdf", lambda p: None, authkey=KEY)


class HandOffTest(unittest.TestCase):
    def test_handing_off_to_nobody_reports_failure(self):
        self.assertFalse(single_instance.hand_off(_address(), "a.pdf", KEY))


class ImpostorTest(unittest.TestCase):
    """Another program created the pipe name before Lector did (another
    account on the same PC can: pipe names are machine-wide)."""

    PATH = r"C:\Users\reader\Documents\private.pdf"

    def setUp(self):
        self.address = _address()
        self.listener = Listener(self.address, family=single_instance.FAMILY)
        self.addCleanup(self.listener.close)
        self.heard: list[bytes] = []
        patcher = mock.patch.object(single_instance, "_HANDOFF_TIMEOUT_S", 0.5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _serve(self, talk):
        def run():
            while True:
                try:
                    conn = self.listener.accept()
                except OSError:
                    return
                try:
                    talk(conn)
                except (OSError, EOFError, single_instance.AuthenticationError):
                    pass

        threading.Thread(target=run, daemon=True).start()

    def _claim(self):
        started = time.monotonic()
        lock = single_instance.claim(self.address, self.PATH, lambda p: None, authkey=KEY)
        return lock, time.monotonic() - started

    def assert_path_never_reached_it(self):
        self.assertFalse(any(b"private" in heard for heard in self.heard), self.heard)

    def test_one_that_just_listens_hears_nothing_and_the_launch_gives_up(self):
        # What the old hand-off fell for: read the message, say "ok".
        self._serve(lambda conn: (self.heard.append(conn.recv_bytes(4096)), conn.send_bytes(b"ok")))
        with self.assertLogs(single_instance.log, level="ERROR"):
            lock, took = self._claim()
        self.assertIsNone(lock)
        self.assertLess(took, 10)
        self.assert_path_never_reached_it()

    WRONG = b"w" * single_instance.KEY_BYTES

    def _assert_turned_away(self, talk):
        self._serve(talk)
        with self.assertLogs(single_instance.log, level="ERROR"):
            lock, _took = self._claim()
        self.assertIsNone(lock)
        self.assert_path_never_reached_it()

    def test_one_that_speaks_the_handshake_with_another_key_hears_nothing(self):
        def talk(conn):
            deliver_challenge(conn, self.WRONG)
            answer_challenge(conn, self.WRONG)
            self.heard.append(conn.recv_bytes(4096))

        self._assert_turned_away(talk)

    def test_one_that_waves_the_launch_through_unchecked_still_hears_nothing(self):
        def talk(conn):
            conn.send_bytes(b"#CHALLENGE#{sha256}" + b"x" * 40)
            conn.recv_bytes(256)
            conn.send_bytes(b"#WELCOME#")
            answer_challenge(conn, self.WRONG)
            self.heard.append(conn.recv_bytes(4096))

        self._assert_turned_away(talk)

    def test_one_that_never_answers_cannot_hang_the_launch(self):
        held = []
        self._serve(lambda conn: (held.append(conn), time.sleep(30)))
        with self.assertLogs(single_instance.log, level="ERROR"):
            lock, took = self._claim()
        self.assertIsNone(lock)
        self.assertLess(took, 5)


class LoadKeyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "Lector"

    def test_the_first_launch_makes_a_key_and_later_ones_reuse_it(self):
        key = single_instance.load_key(self.folder)
        self.assertEqual(len(key), single_instance.KEY_BYTES)
        self.assertEqual((self.folder / single_instance.KEY_FILE).read_bytes(), key)
        self.assertEqual(single_instance.load_key(self.folder), key)

    def test_each_user_folder_gets_its_own_random_key(self):
        other = self.folder.parent / "Other"
        self.assertNotEqual(single_instance.load_key(self.folder), single_instance.load_key(other))

    @unittest.skipIf(sys.platform == "win32", "on Windows the folder's own access list keeps it private")
    def test_only_this_user_can_read_it(self):
        single_instance.load_key(self.folder)
        self.assertEqual((self.folder / single_instance.KEY_FILE).stat().st_mode & 0o777, 0o600)

    def test_a_damaged_key_file_is_replaced(self):
        self.folder.mkdir()
        (self.folder / single_instance.KEY_FILE).write_bytes(b"short")
        with mock.patch.object(single_instance, "_RETRY_DELAY_S", 0):
            key = single_instance.load_key(self.folder)
        self.assertEqual(len(key), single_instance.KEY_BYTES)
        self.assertEqual((self.folder / single_instance.KEY_FILE).read_bytes(), key)

    def test_a_folder_that_cannot_be_used_still_gives_a_key_and_says_so(self):
        self.folder.write_text("a file where the folder should be")
        with self.assertLogs(single_instance.log, level="WARNING"):
            key = single_instance.load_key(self.folder)
        self.assertEqual(len(key), single_instance.KEY_BYTES)


class DefaultAddressTest(unittest.TestCase):
    def test_each_user_has_their_own_lock(self):
        with mock.patch.object(single_instance.getpass, "getuser", return_value="ana"):
            ana = single_instance.default_address()
        with mock.patch.object(single_instance.getpass, "getuser", return_value="bo"):
            bo = single_instance.default_address()
        self.assertNotEqual(ana, bo)

    @unittest.skipUnless(sys.platform == "win32", "named pipes are Windows-only")
    def test_on_windows_it_is_a_named_pipe(self):
        self.assertTrue(single_instance.default_address().startswith("\\\\.\\pipe\\"))


class LaunchPathTest(unittest.TestCase):
    def test_a_file_argument_is_made_absolute(self):
        # The running Lector has a different working directory.
        self.assertEqual(app_main.launch_path(["a.pdf"]), os.path.abspath("a.pdf"))

    def test_no_argument_means_no_file(self):
        self.assertIsNone(app_main.launch_path([]))


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler()


class MainTest(unittest.TestCase):
    def setUp(self):
        self.window = mock.Mock()
        self.window.events.loaded = _Event()
        self.window.events.closing = _Event()
        self.window.events.shown = _Event()
        for name, value in (
            ("webview", mock.Mock(**{"create_window.return_value": self.window})),
            ("Api", mock.Mock()),
            ("FrontendServer", mock.Mock()),
            ("clear_http_cache", mock.Mock()),
            ("window_chrome", mock.Mock(WINDOW_TITLE="Lector")),
            ("single_instance", mock.Mock()),
            # The real one writes a log folder and replaces the process's
            # exception hooks.
            ("app_log", mock.Mock()),
        ):
            patcher = mock.patch.object(app_main, name, value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_a_second_launch_exits_before_building_anything(self):
        self.single_instance.claim.return_value = None

        app_main.main([])

        self.Api.assert_not_called()
        self.FrontendServer.assert_not_called()
        self.webview.create_window.assert_not_called()

    def test_the_lock_is_claimed_with_the_launch_file(self):
        app_main.main(["a.pdf"])

        address, path, _handler = self.single_instance.claim.call_args[0]
        self.assertEqual(address, self.single_instance.default_address.return_value)
        self.assertEqual(path, os.path.abspath("a.pdf"))

    def test_the_lock_is_claimed_with_this_users_key(self):
        app_main.main([])

        self.single_instance.load_key.assert_called_once_with(app_main.settings.settings_dir())
        self.assertIs(
            self.single_instance.claim.call_args.kwargs["authkey"],
            self.single_instance.load_key.return_value,
        )

    def _handler(self):
        app_main.main([])
        return self.single_instance.claim.call_args[0][2]

    def test_a_handed_over_file_comes_to_the_front_and_opens(self):
        self._handler()(r"C:\docs\a.pdf")

        self.window_chrome.bring_to_front.assert_called_once_with("Lector")
        self.Api.return_value.open_file_from_launch.assert_called_once_with(r"C:\docs\a.pdf")

    def test_a_hand_over_with_no_file_only_comes_to_the_front(self):
        self._handler()(None)

        self.window_chrome.bring_to_front.assert_called_once_with("Lector")
        self.Api.return_value.open_file_from_launch.assert_not_called()

    def test_a_file_given_to_the_first_launch_opens_once_the_first_page_loads(self):
        app_main.main(["a.pdf"])
        api = self.Api.return_value
        api.open_file_from_launch.assert_not_called()

        self.window.events.loaded.fire()
        self.window.events.loaded.fire()

        api.open_file_from_launch.assert_called_once_with(os.path.abspath("a.pdf"))

    def test_the_lock_is_released_when_the_app_ends(self):
        app_main.main([])

        self.single_instance.claim.return_value.release.assert_called_once()

    def test_the_server_stops_when_the_app_ends(self):
        app_main.main([])

        self.FrontendServer.return_value.stop.assert_called_once()

    def test_a_second_launch_never_touches_the_log(self):
        self.single_instance.claim.return_value = None

        app_main.main([])

        self.app_log.setup.assert_not_called()
        self.app_log.install_exception_hooks.assert_not_called()

    def test_the_first_launch_logs_and_hands_the_log_folder_to_the_api(self):
        app_main.main([])

        log_dir = self.app_log.setup.call_args[0][0]
        self.assertEqual(log_dir.name, "logs")
        self.Api.assert_called_once_with(log_dir)
        self.app_log.install_exception_hooks.assert_called_once()

    def test_a_background_error_reaches_the_page_through_the_api(self):
        app_main.main([])
        on_background_error, on_fatal = self.app_log.install_exception_hooks.call_args[0]

        on_background_error()

        self.Api.return_value.notify_background_error.assert_called_once_with()
        self.assertIs(on_fatal, self.app_log.show_fatal_message)


if __name__ == "__main__":
    unittest.main()
