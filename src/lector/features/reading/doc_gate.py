"""Keeps page changes and page reads of the open document apart.

pywebview runs every bridge call on its own thread, so the reader's page
images, the strip's look-ahead, the Organise grid's and the outline panel's
thumbnails and find can all be reading the open document at once. Done,
Undo page changes and Redo page changes rearrange that document or replace
it with another, so each one waits for every read already running, and a
read asked for meanwhile waits until the change is complete. Reads never
wait for each other.

A thread may read again inside its own read, and the thread making a change
may read while it does (both happen: a render calls the size helpers, a
change re-reads highlights). A thread must not start a change inside a read
of its own: it would wait for itself.

Lock order: the gate first, then `PdfDocument._save_lock`. Every holder of
the save lock (saves, open, page changes, history steps, the snapshot a file
tool takes) enters the gate before it, so the two can never wait on each
other the other way round.

Reads do run alongside each other on the one MuPDF document, as they did
before this gate existed; it only keeps changes apart from reads.
"""
import threading
from contextlib import contextmanager


class DocGate:
    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._changes_waiting = 0
        self._changer: int | None = None
        self._depth = threading.local()

    @contextmanager
    def reading(self):
        """Read the document; waits while a change is running or waiting."""
        depth = getattr(self._depth, "value", 0)
        outermost = depth == 0 and self._changer != threading.get_ident()
        if outermost:
            with self._cond:
                while self._changer is not None or self._changes_waiting:
                    self._cond.wait()
                self._readers += 1
        self._depth.value = depth + 1
        try:
            yield
        finally:
            self._depth.value = depth
            if outermost:
                with self._cond:
                    self._readers -= 1
                    if not self._readers:
                        self._cond.notify_all()

    @contextmanager
    def changing(self):
        """Change the document, once every read already running has ended."""
        with self._cond:
            self._changes_waiting += 1
            try:
                while self._changer is not None or self._readers:
                    self._cond.wait()
            finally:
                self._changes_waiting -= 1
                # Readers held back for this change may go if it never came.
                self._cond.notify_all()
            self._changer = threading.get_ident()
        try:
            yield
        finally:
            with self._cond:
                self._changer = None
                self._cond.notify_all()
