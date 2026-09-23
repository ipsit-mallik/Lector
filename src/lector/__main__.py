from pathlib import Path

import webview

from lector.api import Api

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def main():
    api = Api()
    window = webview.create_window(
        "Lector",
        url=str(FRONTEND_DIR / "index.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(960, 640),
    )
    # Gatekeeper for the close button/Alt+F4: releases the microphone (the
    # push-to-talk engine holds an open PortAudio stream while a key is held,
    # which would otherwise stay claimed until the process is killed) and, if
    # a document has unsaved highlights, defers the close behind the same
    # save-or-discard prompt the Library back button uses. See
    # `Api.handle_window_closing` and docs/ARCHITECTURE.md's "Voice context
    # router" notes on why this can't just check-and-block synchronously.
    window.events.closing += api.handle_window_closing
    webview.start()


if __name__ == "__main__":
    main()
