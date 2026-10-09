"""Native window chrome on Windows: the app icon and a theme-matched title bar.

pywebview 6's `icon=` argument only applies on GTK/Qt, and it has no API for
the caption colour, so on Windows both are set directly through Win32/DWM:

* `WM_SETICON` with `assets/lector.ico` (built by scripts/build_app_icon.py),
  plus an explicit AppUserModelID so the taskbar groups Lector as its own app
  instead of under python.exe's icon.
* `DwmSetWindowAttribute(DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR)` with the
  theme's `--surface-1` / `--text-primary` from frontend/shared/theme.css, so
  the title bar reads as part of the window rather than a white strip above
  a dark app. Windows 11 only; on Windows 10 the call fails and the system
  caption is left as-is, which is harmless.

Every function is a no-op off Windows, and every Win32 failure is logged
and swallowed: window chrome is cosmetic and must never stop the app opening.
"""

import ctypes
import logging
import os
import sys
from pathlib import Path

from lector.shared import theme

log = logging.getLogger(__name__)

APP_ID = "Lector.Reader"
WINDOW_TITLE = "Lector"
ICON_PATH = Path(__file__).resolve().parents[3] / "assets" / "lector.ico"

_WM_SETICON = 0x0080
_ICON_SMALL, _ICON_BIG = 0, 1
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x0010
_SM_CXICON, _SM_CXSMICON = 11, 49
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_SW_RESTORE = 9
_ASFW_ANY = -1

_IS_WINDOWS = sys.platform == "win32"


def set_app_id() -> None:
    """Call once, before the window is created."""
    if not _IS_WINDOWS:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except OSError:
        log.warning("Could not set AppUserModelID", exc_info=True)


def _colorref(hex_color: str) -> ctypes.c_uint:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return ctypes.c_uint(r | (g << 8) | (b << 16))


def _find_window(title: str) -> int | None:
    """This process's visible top-level window with the given title."""
    user32 = ctypes.windll.user32
    pid = os.getpid()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _lparam):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
            if buf.value == title:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, None)
    return found[0] if found else None


def _set_icon(hwnd: int) -> None:
    if not ICON_PATH.exists():
        log.warning("App icon missing at %s; run scripts/build_app_icon.py", ICON_PATH)
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for which, metric in ((_ICON_BIG, _SM_CXICON), (_ICON_SMALL, _SM_CXSMICON)):
        size = user32.GetSystemMetrics(metric)
        hicon = user32.LoadImageW(None, str(ICON_PATH), _IMAGE_ICON, size, size, _LR_LOADFROMFILE)
        if hicon:
            user32.SendMessageW(hwnd, _WM_SETICON, which, hicon)


def _set_caption(hwnd: int, theme_name: str) -> None:
    dwm = ctypes.windll.dwmapi
    dark = ctypes.c_int(1 if theme_name == "dark" else 0)
    attrs = (
        (_DWMWA_USE_IMMERSIVE_DARK_MODE, dark),
        (_DWMWA_CAPTION_COLOR, _colorref(theme.token(theme_name, "surface-1"))),
        (_DWMWA_TEXT_COLOR, _colorref(theme.token(theme_name, "text-primary"))),
    )
    for attr, value in attrs:
        result = dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), attr, ctypes.byref(value), ctypes.sizeof(value)
        )
        if result != 0:
            log.info("DwmSetWindowAttribute(%s) unsupported here (0x%08X)", attr, result & 0xFFFFFFFF)


def allow_foreground_handoff() -> None:
    """Let the running Lector take the foreground when this launch hands over.

    Windows only lets the process the user just interacted with raise a
    window, and that is the launch the user just started, not the running
    Lector. Granting it before handing over lets the running copy come forward
    instead of only flashing on the taskbar.
    """
    if not _IS_WINDOWS:
        return
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(_ASFW_ANY)
    except OSError:
        log.warning("Could not allow the running Lector to come forward", exc_info=True)


def bring_to_front(title: str) -> None:
    """Un-minimise the window titled `title` and give it focus. Leaves a
    maximised window maximised."""
    if not _IS_WINDOWS:
        return
    try:
        hwnd = _find_window(title)
        if hwnd is None:
            log.warning("Window %r not found; could not bring it forward", title)
            return
        user32 = ctypes.windll.user32
        if user32.IsIconic(ctypes.c_void_p(hwnd)):
            user32.ShowWindow(ctypes.c_void_p(hwnd), _SW_RESTORE)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    except OSError:
        log.warning("Could not bring the window forward", exc_info=True)


def apply(title: str, theme_name: str, *, icon: bool = True) -> None:
    """Style the window titled `title`. Safe to call repeatedly (theme changes)."""
    if not _IS_WINDOWS:
        return
    try:
        hwnd = _find_window(title)
        if hwnd is None:
            log.warning("Window %r not found; chrome left at system defaults", title)
            return
        if icon:
            _set_icon(hwnd)
        _set_caption(hwnd, theme_name)
    except (OSError, KeyError, ValueError):
        log.warning("Could not apply window chrome", exc_info=True)
