import logging
import os
import shutil
import sys
from pathlib import Path

import webview

from lector.api import Api
from lector.features.settings import store as settings
from lector.shared import single_instance, theme, window_chrome
from lector.shared.frontend_server import FrontendServer

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Where WebView2 keeps what it cached from the local server, inside the folder
# `storage_path` points it at. Both hold copies of the app's own files.
_WEBVIEW_CACHE_DIRS = (Path("EBWebView", "Default", "Cache"), Path("EBWebView", "Default", "Code Cache"))


def clear_http_cache(webview_dir: Path) -> None:
    """Deletes the web view's cache from earlier launches, before it starts.

    pywebview's server, which served the pages until `FrontendServer`, sent no
    working `Cache-Control`, and `storage_path` below keeps the cache between
    launches, so an edited or updated page kept being served stale.
    `--disable-http-cache` does not help (WebView2 ignores it). Lector's own
    server now sends `no-store` from a new port each launch, so nothing stale
    can be served; this still clears what older launches left and the
    compiled-script cache, which is kept per origin and would otherwise grow by
    one origin per launch. The rest of the profile is left alone. A folder that
    cannot be removed is reported and left, never fatal.
    """
    for relative in _WEBVIEW_CACHE_DIRS:
        try:
            shutil.rmtree(webview_dir / relative, ignore_errors=False)
        except FileNotFoundError:
            continue
        except OSError as err:
            log.warning("Could not clear the web view cache %s: %s", relative, err)


def launch_path(args: list[str]) -> str | None:
    """The file Lector was started with, made absolute: a second launch hands
    it to a Lector running from another working directory."""
    return os.path.abspath(args[0]) if args else None


def main(args: list[str] | None = None) -> None:
    path = launch_path(sys.argv[1:] if args is None else args)
    window = None
    api = None

    def on_handoff(handed_path: str | None) -> None:
        # Another launch, while this one runs: come forward, open its file.
        window_chrome.bring_to_front(window_chrome.WINDOW_TITLE)
        if handed_path and api is not None:
            api.open_file_from_launch(handed_path)

    # First, before anything is built: a second launch hands over and exits.
    # Lets this launch raise the running window if it turns out to be second.
    window_chrome.allow_foreground_handoff()
    lock = single_instance.claim(single_instance.default_address(), path, on_handoff)
    if lock is None:
        return

    webview_dir = settings.settings_dir() / "webview"
    # Before the web view exists: it opens these files when it starts.
    clear_http_cache(webview_dir)
    api = Api()
    # Lector's own server, on a port picked fresh for this launch, sends every
    # page with the saved theme already in <html data-theme>, so the first
    # frame is in it (see shared/frontend_server.py for why not pywebview's).
    server = FrontendServer(FRONTEND_DIR, settings.get_theme)
    server.start()
    window_chrome.set_app_id()
    window = webview.create_window(
        window_chrome.WINDOW_TITLE,
        url=server.url("index.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(880, 600),
        # The native surface behind the page until WebView2 has painted it:
        # the saved theme's canvas, so a dark launch doesn't open on white.
        background_color=theme.token(settings.get_theme(), "bg"),
    )
    # Gatekeeper for the close button/Alt+F4: releases the microphone (the
    # push-to-talk engine holds an open PortAudio stream while a key is held,
    # which would otherwise stay claimed until the process is killed) and, if
    # a document has unsaved highlights, defers the close behind the same
    # save-or-discard prompt the Library back button uses. See
    # `Api.handle_window_closing` and docs/ARCHITECTURE.md's "Voice context
    # router" notes on why this can't just check-and-block synchronously.
    window.events.closing += api.handle_window_closing
    # Dropping a PDF anywhere on the window opens it (Milestone 8.12). pywebview
    # forgets DOM bindings on every navigation, and Lector navigates between
    # Home, Settings and Reading, so this is rebound on each page load.
    window.events.loaded += lambda: api.bind_file_drop(window)
    if path:
        # A file to open at launch goes to the first page that loads, once.
        def open_launch_file() -> None:
            window.events.loaded -= open_launch_file
            api.open_file_from_launch(path)

        window.events.loaded += open_launch_file
    # App icon + theme-matched title bar (Windows; no-op elsewhere). Re-applied
    # from Api.set_theme whenever the theme changes.
    window.events.shown += lambda: window_chrome.apply(
        window_chrome.WINDOW_TITLE, settings.get_theme()
    )
    try:
        # A kept profile in the app's own folder, so WebView2 does not build a
        # fresh one on every launch. Nothing of Lector's lives in it: the pages'
        # origin changes every launch, and settings.json holds what persists.
        webview.start(private_mode=False, storage_path=str(webview_dir))
    finally:
        server.stop()
        lock.release()


if __name__ == "__main__":
    main()
