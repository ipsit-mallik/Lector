"""Build assets/lector.ico from the brand mark (frontend/shared/icons/logo.svg).

The icon is the same mark the Home sidebar shows: the microphone glyph on a
rounded accent-coloured square. Colours come from frontend/shared/theme.css
(light theme's --accent and --on-accent, i.e. --surface-2) via
lector.shared.theme, so the icon can't drift from the UI's palette.

Uses PyMuPDF (to rasterise the SVG) and Pillow (to compose and write the
multi-size .ico), both already project dependencies. Run after changing the
logo or the accent colour:

    python scripts/build_app_icon.py
"""

import io
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector.shared import theme  # noqa: E402

LOGO_SVG = ROOT / "frontend" / "shared" / "icons" / "logo.svg"
OUT = ROOT / "assets" / "lector.ico"

CANVAS = 256
CORNER_RADIUS = 56  # matches the sidebar mark's --radius-sm/26px ratio
GLYPH_FRACTION = 0.62  # sidebar: a 15px glyph in a 26px tile
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _render_glyph(color: str, size: int) -> Image.Image:
    svg = LOGO_SVG.read_text(encoding="utf-8").replace("currentColor", color)
    doc = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
    page = doc[0]
    zoom = size / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")


def build() -> Path:
    accent = theme.token("light", "accent")
    on_accent = theme.token("light", "surface-2")

    tile = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1), radius=CORNER_RADIUS, fill=accent
    )
    glyph = _render_glyph(on_accent, round(CANVAS * GLYPH_FRACTION))
    offset = ((CANVAS - glyph.width) // 2, (CANVAS - glyph.height) // 2)
    tile.alpha_composite(glyph, offset)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tile.save(OUT, format="ICO", sizes=ICO_SIZES)
    return OUT


if __name__ == "__main__":
    print(f"Wrote {build()}")
