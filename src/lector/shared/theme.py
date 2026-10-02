"""Theme names, plus read access to the design tokens for Python-side code.

The tokens themselves live in exactly one place: `frontend/shared/theme.css`
(documented in docs/DESIGN_SYSTEM.md). Python code that needs a colour — the
native title bar (`window_chrome.py`), the app-icon build script — reads it
from that file rather than keeping a second copy here that could drift.
"""

import re
from functools import lru_cache
from pathlib import Path

THEME_ORDER = ["light", "dark", "sepia"]

THEME_CSS = Path(__file__).resolve().parents[3] / "frontend" / "shared" / "theme.css"

_HEX = re.compile(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})\b")


@lru_cache(maxsize=None)
def _theme_blocks() -> dict[str, dict[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", THEME_CSS.read_text(encoding="utf-8"), flags=re.S)
    blocks: dict[str, dict[str, str]] = {}
    for name in THEME_ORDER:
        match = re.search(r'\[data-theme="%s"\]\s*\{([^}]*)\}' % name, css)
        if match is None:
            raise ValueError(f'theme.css has no [data-theme="{name}"] block')
        blocks[name] = dict(_HEX.findall(match.group(1)))
    return blocks


def token(theme: str, name: str) -> str:
    """A per-theme hex colour token from theme.css, e.g. token("dark", "surface-1").

    Unknown themes fall back to light, matching how the frontend treats a
    missing/invalid `data-theme`.
    """
    blocks = _theme_blocks()
    values = blocks.get(theme, blocks["light"])
    if name not in values:
        raise KeyError(f"theme.css defines no hex --{name} for {theme!r}")
    return values[name]
