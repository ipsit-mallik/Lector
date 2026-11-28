"""The window's title, as the taskbar and Alt+Tab show it.

"Lector" off the reader (Home, Settings, onboarding), the document's name
and the app's in the reader ("Report – Lector"), with a leading "*" while
it has unsaved changes ("*Report – Lector"), the convention Windows apps
use. Read from the one unsaved flag (docs/DESIGN_SYSTEM.md § "Unsaved
changes"); set by `BridgeCore.sync_window_title`.
"""

APP_TITLE = "Lector"
# An en dash with a space either side, as Windows apps write it.
SEPARATOR = " – "
UNSAVED_MARK = "*"


def compose(name: str | None, dirty: bool) -> str:
    """The title for a document called `name` (None or empty: no document
    on screen), unsaved when `dirty`."""
    if not name:
        return APP_TITLE
    return f"{UNSAVED_MARK if dirty else ''}{name}{SEPARATOR}{APP_TITLE}"


def is_lector_title(text: str) -> bool:
    """Whether `text` is a title `compose` gives: how the native window is
    found when its handle isn't known yet (`window_chrome`)."""
    return text == APP_TITLE or text.endswith(SEPARATOR + APP_TITLE)
