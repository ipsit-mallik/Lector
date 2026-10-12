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

# The fixed highlight palette (docs/PRD.md: five colours, no free picker), in
# the order the toolbar and the "What can I say?" list show them. Each name is
# a `--highlight-<name>` token below; the names are what the settings file
# stores, what crosses the bridge and what voice says.
HIGHLIGHT_COLOR_NAMES: tuple[str, ...] = ("yellow", "green", "blue", "pink", "orange")
DEFAULT_HIGHLIGHT_COLOR = "yellow"

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


@lru_cache(maxsize=None)
def _shared_block() -> dict[str, str]:
    """The theme-independent `:root { ... }` block: the paper and what is drawn
    on it, which look the same whichever theme the chrome is in."""
    css = re.sub(r"/\*.*?\*/", "", THEME_CSS.read_text(encoding="utf-8"), flags=re.S)
    # The first bare `:root {` (the per-theme blocks are `:root, [data-theme=...]`).
    match = re.search(r"^:root\s*\{([^}]*)\}", css, flags=re.M)
    if match is None:
        raise ValueError("theme.css has no theme-independent :root block")
    return dict(_HEX.findall(match.group(1)))


def shared_token(name: str) -> str:
    """A theme-independent hex colour token from theme.css, e.g.
    shared_token("highlight-yellow")."""
    values = _shared_block()
    if name not in values:
        raise KeyError(f"theme.css defines no theme-independent hex --{name}")
    return values[name]


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
