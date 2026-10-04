from pathlib import Path

import webview

from lector.api import Api
from lector.features.settings import store as settings
from lector.shared import window_chrome

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def main():
    api = Api()
    window_chrome.set_app_id()
    window = webview.create_window(
        window_chrome.WINDOW_TITLE,
        url=str(FRONTEND_DIR / "index.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(880, 600),
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
    webview.start()


if __name__ == "__main__":
    main()
