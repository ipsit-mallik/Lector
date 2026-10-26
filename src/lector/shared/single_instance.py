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

The name is easy to guess and, on Windows, open to the whole machine, so
another program (another account's, say) can create it before Lector does.
Before a path is sent, both ends prove they hold this user's key
(`load_key`, kept in the settings folder): the launch first, then the copy it
reached. Whatever holds the name without the key never learns which file is
being opened, and a program without it can't have Lector open a file. It can
still keep Lector from starting by holding the name; nothing here prevents that.
"""

import getpass
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Connection, Listener, answer_challenge, deliver_challenge
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
# A whole hand-off, handshake included. The handshake reads with no time limit
# of its own, so without this whatever holds the name could keep a launch
# waiting forever.
_HANDOFF_TIMEOUT_S = _ACK_TIMEOUT_S

KEY_FILE = "instance.key"
KEY_BYTES = 32

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


def load_key(folder: Path) -> bytes:
    """This user's hand-off key, kept in `folder` (the settings folder) and made
    there by the first launch that finds none.

    Only this user can read it: on Windows the settings folder is already
    theirs alone, and elsewhere the file is created 0600. A folder that can't
    be used gives this launch a key of its own, so Lector still starts; it just
    can't hand a file to, or take one from, another launch.
    """
    path = folder / KEY_FILE
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for _ in range(_ATTEMPTS):
            key = _create_key(path) or path.read_bytes()
            if len(key) == KEY_BYTES:
                return key
            # Short: another launch made the file a moment ago and is still
            # writing it. Still short after a few looks: damaged, made anew.
            time.sleep(_RETRY_DELAY_S)
        replacement = path.with_suffix(".tmp")
        replacement.unlink(missing_ok=True)
        key = _create_key(replacement) or replacement.read_bytes()
        os.replace(replacement, path)
        return key
    except OSError as err:
        log.warning("Could not read or make the hand-off key; this launch keeps its own: %s", err)
        return secrets.token_bytes(KEY_BYTES)


def _create_key(path: Path) -> bytes | None:
    """A new key, written to `path`; `None` if `path` already exists."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        return None
    key = secrets.token_bytes(KEY_BYTES)
    with os.fdopen(fd, "wb") as file:
        file.write(key)
    return key


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

    def __init__(
        self,
        listener: Listener,
        address: str,
        on_handoff: Callable[[str | None], None],
        authkey: bytes,
    ):
        self._listener = listener
        self.address = address
        self._on_handoff = on_handoff
        self._authkey = authkey
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
            # The launch proves it holds the key, then this copy does.
            deliver_challenge(conn, self._authkey)
            answer_challenge(conn, self._authkey)
            path = _parse(conn.recv_bytes(MAX_MESSAGE))
        except AuthenticationError:
            log.warning("Ignored a hand-off from a program without this user's key")
            return
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


def hand_off(address: str, path: str | None, authkey: bytes) -> bool:
    """Give `path` to the running Lector. True once it has confirmed.

    The path is sent only once whatever answers has proved it holds `authkey`.
    The exchange runs on a thread of its own, waited on for at most
    `_HANDOFF_TIMEOUT_S`; one that has not finished by then counts as no answer.
    """
    confirmed = threading.Event()

    def send() -> None:
        try:
            with Client(address, family=FAMILY) as conn:
                answer_challenge(conn, authkey)
                deliver_challenge(conn, authkey)
                conn.send_bytes(json.dumps({"path": path}).encode("utf-8"))
                if conn.poll(_ACK_TIMEOUT_S) and conn.recv_bytes(len(_ACK)) == _ACK:
                    confirmed.set()
        except (OSError, EOFError, AuthenticationError):
            pass

    sender = threading.Thread(target=send, name="single-instance-handoff", daemon=True)
    sender.start()
    sender.join(_HANDOFF_TIMEOUT_S)
    return confirmed.is_set()


def _listen(
    address: str, on_handoff: Callable[[str | None], None], authkey: bytes
) -> InstanceLock | None:
    try:
        listener = Listener(address, family=FAMILY)
    except OSError:
        return None  # held: access denied for a pipe, address in use for a socket
    return InstanceLock(listener, address, on_handoff, authkey)


def claim(
    address: str, path: str | None, on_handoff: Callable[[str | None], None], *, authkey: bytes
) -> InstanceLock | None:
    """The lock if this is the only Lector, else `None` once `path` has been
    handed to the one that is running (this launch should then exit)."""
    for _ in range(_ATTEMPTS):
        lock = _listen(address, on_handoff, authkey)
        if lock is not None:
            return lock
        if hand_off(address, path, authkey):
            return None
        if FAMILY == "AF_UNIX":
            # A socket file left by a copy that crashed: no one answers it.
            # One this user may not remove (another user's) stays, and this
            # launch gives up below.
            try:
                os.unlink(address)
            except OSError:
                pass
        time.sleep(_RETRY_DELAY_S)
    log.error("Another Lector holds %s but is not answering; not starting a second one", address)
    return None
