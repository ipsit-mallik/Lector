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
    # The push-to-talk engine holds an open PortAudio stream while a key is
    # held; closing the window mid-hold would otherwise leave the microphone
    # claimed until the process is killed.
    window.events.closing += api.shutdown_voice
    webview.start()


if __name__ == "__main__":
    main()
