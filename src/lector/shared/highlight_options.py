"""The highlight tool's options: style, opacity per style, freehand thickness.

One home for the values the settings file stores, the annotation writer
draws with, the bridge validates and the highlight tool shows (docs/DESIGN_SYSTEM.md,
"Highlight tool"), so none of them can drift from the others —
the same arrangement as `theme.HIGHLIGHT_COLOR_NAMES` for the colours.
"""

# Over text, what a drag (or "highlight"/"underline"/"strike through" by
# voice) writes: a PDF /Highlight, /Underline or /StrikeOut. Over anything
# else the tool always draws a freehand stroke, whichever is chosen.
HIGHLIGHT = "highlight"
UNDERLINE = "underline"
STRIKETHROUGH = "strikethrough"
STYLES: tuple[str, ...] = (HIGHLIGHT, UNDERLINE, STRIKETHROUGH)
DEFAULT_STYLE = HIGHLIGHT

# Opacity in whole percent, remembered per style. A highlight at 60% through
# Multiply reads as the colour with what is under it showing; a line is thin
# enough to be drawn solid. Freehand strokes use the Highlight value, so a
# stroke beside a text highlight is the same colour.
OPACITY_MIN_PCT = 10
OPACITY_MAX_PCT = 100
DEFAULT_OPACITY_PCT: dict[str, int] = {HIGHLIGHT: 60, UNDERLINE: 100, STRIKETHROUGH: 100}
# A drag on the slider settles on the style's default within this many
# percent of it; nowhere else.
OPACITY_SNAP_PCT = 3
# The slider's tick marks: every 10%.
OPACITY_TICK_STEP_PCT = 10

# Freehand stroke widths, in PDF points (so the same on paper at every zoom).
INK_WIDTH_PRESETS_PT: tuple[int, ...] = (4, 8, 14, 24, 36)
DEFAULT_INK_WIDTH_PT = 14


def is_style(value) -> bool:
    return isinstance(value, str) and value in STYLES


def is_opacity_pct(value) -> bool:
    """A whole percent in range. Booleans are refused: True is an int."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and OPACITY_MIN_PCT <= value <= OPACITY_MAX_PCT
    )


def is_ink_width(value) -> bool:
    """One of the presets: the popover offers nothing else, so a stored or
    bridged width that is not one is a hand-edited or hostile value."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value in INK_WIDTH_PRESETS_PT


def opacity_fraction(pct: int) -> float:
    return pct / 100
