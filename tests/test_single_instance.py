"""Only one Lector runs at a time.

A second launch hands its file (if it was given one) to the running Lector,
which comes to the front and opens it the way it opens a dropped PDF, and the
second launch then exits. The lock is a per-user named pipe on Windows (a Unix
socket elsewhere): only one process can create the first instance of a pipe
name, so the claim is atomic.

The message is JSON read with a size cap, never `Connection.recv()`: that
unpickles, and any local process can connect.
"""

import json
import os
import pickle
import sys
import threading
import unittest
import uuid
from multiprocessing.connection import Client
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import __main__ as app_main  # noqa: E402
from lector.shared import single_instance  # noqa: E402

WAIT = 5


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
        self.lock = single_instance.claim(self.address, None, self.received)
        self.assertIsNotNone(self.lock)
        self.addCleanup(self.lock.release)

    def test_a_second_launch_hands_its_file_over_and_does_not_claim(self):
        self.assertIsNone(single_instance.claim(self.address, r"C:\docs\a.pdf", lambda p: None))
        self.assertEqual(self.received.wait(), r"C:\docs\a.pdf")

    def test_a_second_launch_with_no_file_still_brings_lector_forward(self):
        self.assertIsNone(single_instance.claim(self.address, None, lambda p: None))
        self.assertIsNone(self.received.wait())

    def test_every_later_launch_is_heard_not_just_the_first(self):
        for path in ("one.pdf", "two.pdf", "three.pdf"):
            single_instance.claim(self.address, path, lambda p: None)
            self.assertEqual(self.received.wait(), path)

    def test_once_released_the_next_launch_claims(self):
        self.lock.release()
        again = single_instance.claim(self.address, None, lambda p: None)
        self.assertIsNotNone(again)
        again.release()

    def _send_raw(self, payload: bytes):
        with Client(self.address, family=single_instance.FAMILY) as conn:
            try:
                conn.send_bytes(payload)
            except BrokenPipeError:
                pass  # an oversized message is cut off mid-write; that is the point

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
        # Still listening, and none of those reached the handler.
        single_instance.claim(self.address, "real.pdf", lambda p: None)
        self.assertEqual(self.received.wait(), "real.pdf")
        self.assertEqual(self.received.items, ["real.pdf"])

    def test_a_handler_that_fails_does_not_stop_the_lock_listening(self):
        self.received.items.clear()
        failing = single_instance.claim(_address(), None, mock.Mock(side_effect=RuntimeError("boom")))
        self.addCleanup(failing.release)
        with self.assertLogs(single_instance.log, level="ERROR"):
            single_instance.claim(failing.address, "a.pdf", lambda p: None)
            single_instance.claim(failing.address, "b.pdf", lambda p: None)


class HandOffTest(unittest.TestCase):
    def test_handing_off_to_nobody_reports_failure(self):
        self.assertFalse(single_instance.hand_off(_address(), "a.pdf"))


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


if __name__ == "__main__":
    unittest.main()
