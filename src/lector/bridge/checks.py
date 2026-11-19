"""Checks on values arriving across the bridge, shared by its controllers.

The bridge takes any JSON value, so a number from the page is checked before
it reaches a document: a bool is not a number, and neither is an integer too
large for a float.
"""
import base64
import math

# Bounds on a requested thumbnail's width in pixels: the Pages tab asks for its
# column width times the screen's pixel ratio, the progress bar's preview for a
# little more.
MIN_THUMBNAIL_PX = 16
MAX_THUMBNAIL_PX = 480


def pixmap_to_png_b64(pix) -> str:
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def finite(value) -> bool:
    """Whether `value` is a finite number (a bool is not; JSON `true` is no
    coordinate). An integer too large for a float is not finite enough."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def page_index(value) -> int | None:
    """`value` as a page index, or None if it is not a non-negative integer."""
    if not finite(value) or value != int(value) or value < 0:
        return None
    return int(value)
