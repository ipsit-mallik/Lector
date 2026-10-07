import logging
import shutil
from pathlib import Path

import webview

from lector.api import Api
from lector.features.settings import store as settings
from lector.shared import theme, window_chrome

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Where WebView2 keeps what it cached from the local server, inside the folder
# `storage_path` points it at. Both hold copies of the app's own files.
_WEBVIEW_CACHE_DIRS = (Path("EBWebView", "Default", "Cache"), Path("EBWebView", "Default", "Code Cache"))


def clear_http_cache(webview_dir: Path) -> None:
    """Deletes the web view's cache from earlier launches, before it starts.

    pywebview's local server sends `Last-Modified` but no `Cache-Control` (it sets
    one and then returns a response that replaces it), so Chromium keeps a file
    fresh for a tenth of the time since it last changed, and `storage_path` below
    keeps that cache between launches. A page unedited for hours then kept being
    served stale after an edit or an update: the window showed the old Home while
    the server was serving the new one. `--disable-http-cache` does not help here
    (WebView2 ignores it), so the folders go instead. Everything is read from disk
    on this machine, so a cache saves nothing. Only the cache goes: the profile's
    `Local Storage` holds the saved theme. A folder that cannot be removed (another
    Lector is using the profile) is reported and left, never fatal.
    """
    for relative in _WEBVIEW_CACHE_DIRS:
        try:
            shutil.rmtree(webview_dir / relative, ignore_errors=False)
        except FileNotFoundError:
            continue
        except OSError as err:
            log.warning("Could not clear the web view cache %s: %s", relative, err)


def main():
    webview_dir = settings.settings_dir() / "webview"
    # Before the web view exists: it opens these files when it starts.
    clear_http_cache(webview_dir)
    api = Api()
    window_chrome.set_app_id()
    window = webview.create_window(
        window_chrome.WINDOW_TITLE,
        url=str(FRONTEND_DIR / "index.html"),
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
    # App icon + theme-matched title bar (Windows; no-op elsewhere). Re-applied
    # from Api.set_theme whenever the theme changes.
    window.events.shown += lambda: window_chrome.apply(
        window_chrome.WINDOW_TITLE, settings.get_theme()
    )
    # Persistent web storage, in the app's own folder: pywebview's default is a
    # private session, so `localStorage` would be empty on every launch and each
    # page's <head> script (which paints the saved theme before first paint, from
    # the "lector-theme" mirror) would always find nothing and paint Light.
    webview.start(private_mode=False, storage_path=str(webview_dir))


if __name__ == "__main__":
    main()
