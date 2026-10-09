"""Serves frontend/ to the web view, from a port the OS picks for each launch.

pywebview's own server always takes port 42001 when web storage is kept, and on
Windows a second process can bind a port another already holds (Python's
`socketserver` sets `SO_REUSEADDR`, which there means "share"). So a second
Lector, or any other pywebview app, quietly had its pages served by the first.
This server binds 127.0.0.1 port 0 with `SO_EXCLUSIVEADDRUSE`, so every launch
gets a free port no one else can take.

A new port is a new origin, so web storage no longer survives a restart and
cannot carry the saved theme to the next launch's first paint. Instead each page
is sent with the saved theme already in its `<html data-theme>`, read from
settings.json as the page is requested, so the first frame is in the reader's
theme without any script or bridge call.

Every response is `Cache-Control: no-store`: the files are on this disk, so a
cache saves nothing and could only serve a stale page after an update.
"""

import functools
import http.server
import io
import logging
import re
import socket
import socketserver
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from lector.shared.theme import THEME_ORDER

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
FALLBACK_THEME = "light"

_HTML_THEME = re.compile(r'(<html\b[^>]*?\bdata-theme=")[^"]*(")')

# Set here rather than taken from the OS: on Windows `mimetypes` reads the
# registry, and a stylesheet sent as anything but text/css is not applied.
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def with_theme(html: str, theme: str) -> str:
    """`html` with its `<html>` element's `data-theme` set to `theme`.

    A name that is not a theme is served as light (the fallback everywhere
    else) and is never written into the page.
    """
    name = theme if theme in THEME_ORDER else FALLBACK_THEME
    return _HTML_THEME.sub(lambda m: f"{m.group(1)}{name}{m.group(2)}", html, count=1)


class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = _TYPES

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            # No folder listings, and no index pages: the window asks for files.
            self.send_error(404)
            return None
        if path.suffix == ".html" and path.is_file():
            return self._send_page(path)
        return super().send_head()

    def _send_page(self, path: Path) -> io.BytesIO:
        body = with_theme(path.read_text(encoding="utf-8"), self.server.theme()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", _TYPES[".html"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return io.BytesIO(body)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        log.debug("%s %s", self.address_string(), format % args)


class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, handler: Callable, current_theme: Callable[[], str]) -> None:
        self._current_theme = current_theme
        super().__init__((HOST, 0), handler)

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Not HTTPServer.server_bind: it looks up the host's full DNS name,
        # which can stall startup, for a value nothing here uses.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def theme(self) -> str:
        try:
            return self._current_theme()
        except Exception:
            log.exception("Could not read the saved theme; serving %s", FALLBACK_THEME)
            return FALLBACK_THEME


class FrontendServer:
    """frontend/ on 127.0.0.1, with each page sent in `current_theme()`."""

    def __init__(self, root: Path, current_theme: Callable[[], str]) -> None:
        self._root = Path(root)
        self._current_theme = current_theme
        self._httpd: _Server | None = None

    def start(self) -> None:
        handler = functools.partial(_Handler, directory=str(self._root))
        self._httpd = _Server(handler, self._current_theme)
        threading.Thread(
            target=self._httpd.serve_forever, name="frontend-server", daemon=True
        ).start()

    @property
    def address(self) -> tuple[str, int]:
        return self._httpd.server_address[:2]

    @property
    def port(self) -> int:
        return self.address[1]

    def url(self, page: str) -> str:
        return f"http://{HOST}:{self.port}/{page}"

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
