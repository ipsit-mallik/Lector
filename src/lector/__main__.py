from pathlib import Path

import webview

from lector.api import Api

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def main():
    api = Api()
    webview.create_window(
        "Lector",
        url=str(FRONTEND_DIR / "index.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(960, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
