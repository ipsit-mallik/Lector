"""Keeps Lector to one running copy per user, and hands a second launch's file to it.

The lock is a named pipe on Windows (a Unix socket elsewhere) with a per-user
name. Only one process can create the first instance of a pipe name, so whoever
creates it holds the lock; the operating system frees it when that process
ends, even if it crashes. A later launch connects to it instead, sends the file
it was started with (or none), waits for the running copy to confirm it heard,
and exits. The running copy comes to the front and opens the file the same way
it opens a dropped PDF.

Messages are small JSON objects (`{"path": str | null}`) read with a size cap.
Never `Connection.recv()`: it unpickles, and any local process can connect.
"""

import getpass
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

log = logging.getLogger(__name__)

FAMILY = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
MAX_MESSAGE = 32 * 1024
_ACK = b"ok"
_ACK_TIMEOUT_S = 3
# A running copy that is just starting, or closing, can hold the name without
# answering; ask a few times. One that never answers is hung, and waiting
# longer would not help, so this launch gives up within about ten seconds.
_ATTEMPTS = 3
_RETRY_DELAY_S = 0.3

_INVALID = object()


def default_address() -> str:
    """This user's lock: one Lector per user, not per machine."""
    try:
        user = getpass.getuser()
    except OSError:
        user = "user"
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    if sys.platform == "win32":
        return rf"\\.\pipe\Lector-{user}"
    return str(Path(tempfile.gettempdir()) / f"lector-{user}.sock")


def _parse(data: bytes):
    """The path in a hand-off message (`None` for "just come forward"), or
    `_INVALID` for anything else."""
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _INVALID
    if not isinstance(message, dict) or "path" not in message:
        return _INVALID
    path = message["path"]
    return path if path is None or isinstance(path, str) else _INVALID


class InstanceLock:
    """Held by the running Lector. Hears later launches on its own thread."""

    def __init__(self, listener: Listener, address: str, on_handoff: Callable[[str | None], None]):
        self._listener = listener
        self.address = address
        self._on_handoff = on_handoff
        self._released = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="single-instance", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        # Only this thread touches the listener, closing included: closed from
        # another thread, `accept()` can create a fresh pipe instance after the
        # close, which nothing then closes, and the name stays taken.
        try:
            while not self._released.is_set():
                try:
                    conn = self._listener.accept()
                except OSError:
                    return
                with conn:
                    if self._released.is_set():
                        return
                    self._receive(conn)
        finally:
            self._listener.close()

    def _receive(self, conn: Connection) -> None:
        try:
            path = _parse(conn.recv_bytes(MAX_MESSAGE))
        except (OSError, EOFError) as err:
            log.warning("Ignored a hand-off that could not be read: %s", err)
            return
        if path is _INVALID:
            log.warning("Ignored a hand-off that was not a Lector message")
            return
        try:
            conn.send_bytes(_ACK)
        except OSError:
            pass  # the sender gave up waiting; the file is still worth opening
        try:
            self._on_handoff(path)
        except Exception:
            log.exception("Could not act on a hand-off from another launch")

    def release(self) -> None:
        """Free the name for the next launch: wake the listening thread with a
        connection of our own, and wait for it to close the listener."""
        self._released.set()
        try:
            Client(self.address, family=FAMILY).close()
        except OSError:
            pass  # the thread has already stopped
        self._thread.join(_ACK_TIMEOUT_S)


def hand_off(address: str, path: str | None) -> bool:
    """Give `path` to the running Lector. True once it has confirmed."""
    try:
        with Client(address, family=FAMILY) as conn:
            conn.send_bytes(json.dumps({"path": path}).encode("utf-8"))
            return conn.poll(_ACK_TIMEOUT_S) and conn.recv_bytes(len(_ACK)) == _ACK
    except (OSError, EOFError):
        return False


def _listen(address: str, on_handoff: Callable[[str | None], None]) -> InstanceLock | None:
    try:
        listener = Listener(address, family=FAMILY)
    except OSError:
        return None  # held: access denied for a pipe, address in use for a socket
    return InstanceLock(listener, address, on_handoff)


def claim(
    address: str, path: str | None, on_handoff: Callable[[str | None], None]
) -> InstanceLock | None:
    """The lock if this is the only Lector, else `None` once `path` has been
    handed to the one that is running (this launch should then exit)."""
    for _ in range(_ATTEMPTS):
        lock = _listen(address, on_handoff)
        if lock is not None:
            return lock
        if hand_off(address, path):
            return None
        if FAMILY == "AF_UNIX":
            # A socket file left by a copy that crashed: no one answers it.
            try:
                os.unlink(address)
            except FileNotFoundError:
                pass
        time.sleep(_RETRY_DELAY_S)
    log.error("Another Lector holds %s but is not answering; not starting a second one", address)
    return None
