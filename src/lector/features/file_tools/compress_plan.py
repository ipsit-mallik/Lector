"""What Compress tries, worked out in the main process. Pure: no PyMuPDF,
nothing read or written. The engine (`engine.compress_images`) applies a
`Recipe` in the worker; `tools.py` and `estimates.py` run the attempts.

One ladder of recipes, best quality first (`LADDER`), shared by the dialog's
two tabs (docs/PRD.md § "Post-v1: file tools" → Compress):

- Quality: three of its steps (`LEVEL_STEPS`), compressed in the background
  as soon as the dialog opens, so each card shows the size it will make.
- Size limit: every step, compressed when the tab first opens, so each preset
  limit is known to be reachable or not before the reader picks it, and the
  step it would use is the best one under it (`choose`).

Pressing Compress moves a finished step's file into place: no compression
happens after the reader has chosen.
"""
from dataclasses import dataclass

from lector.features.file_tools.choices import BALANCED, LIGHT, STRONG

# How an image that covers most of a page (a scanned page) is treated.
COLOUR = "colour"  # like every other image
GREY = "grey"  # grey JPEG at `scan_ppi`, `scan_quality`
BITONAL = "bitonal"  # 1-bit (CCITT G4) at `scan_ppi`, if that is smaller than grey

# An image is resampled only when it is more than this much over the target,
# so one already about right is left alone (165 ppi for 150, 106 for 96).
THRESHOLD_MARGIN = 1.1

# A Quality level has to save at least this much to be offered.
MIN_SAVING = 0.05

# The Quality Done state's note about readability: shown when any image was
# brought below this.
LOW_PPI = 96

# Size limits count as upload forms do: 1 KB is 1,000 bytes. A step only
# counts as under a limit at 97% of it or less, so the file passes whichever
# way a portal counts (1,000 or 1,024) with room to spare.
KB = 1000
MB = 1000 * 1000
UNITS = {"KB": KB, "MB": MB}
LIMIT_MARGIN_PERCENT = 97
LIMIT_MIN = 1
LIMIT_MAX = 9999
DEFAULT_LIMIT = 200
DEFAULT_UNIT = "KB"
# The Size limit tab's pills, smallest first.
PRESETS = ((50, "KB"), (100, "KB"), (200, "KB"), (300, "KB"), (500, "KB"), (1, "MB"), (2, "MB"))

QUALITY = "quality"
SIZE_LIMIT = "limit"
MODES = (QUALITY, SIZE_LIMIT)
DEFAULT_MODE = QUALITY
# Not a mode the reader picks: "Save 164 KB file", the safety net's result
# when a size-limit step turns out not to fit after all.
SAVE_HELD = "save_held"

# What a step did to the file, in the words the Size limit tab shows.
SHARP = "sharp"  # colour, images at 120 ppi or more (or nothing resampled)
GOOD = "good"  # colour, images at 72 to 119 ppi
READABLE = "readable"  # colour under 72 ppi, or scanned pages made grey
BLACK_AND_WHITE = "bw"  # scanned pages made 1-bit
KINDS = (SHARP, GOOD, READABLE, BLACK_AND_WHITE)
SHARP_PPI = 120
GOOD_PPI = 72


@dataclass(frozen=True)
class Recipe:
    """One way to compress. `ppi`/`quality`: every image above `ppi` (with
    the margin) resampled to it and stored as JPEG at `quality`; None leaves
    images as they are (only the lossless tidy runs). `scans`: what happens
    to images that cover most of a page. Sent to the worker, so plain data."""

    ppi: int | None = None
    quality: int = 0
    scans: str = COLOUR
    scan_ppi: int | None = None
    scan_quality: int = 0

    @property
    def threshold(self) -> int | None:
        return None if self.ppi is None else round(self.ppi * THRESHOLD_MARGIN)

    @property
    def black_and_white(self) -> bool:
        return self.scans in (GREY, BITONAL)


# Best quality first, down to about 50 ppi at JPEG quality 30. Colour down to
# 72 ppi; then, at the low end, scanned pages go grey at that step's own
# settings, and then 1-bit, while the other images carry on down. A grey scan
# is never set above the other images' ppi (MuPDF's pass over every image
# would resample it again). A 1-bit page that would be bigger than grey at the
# step's own settings (a photo, a noisy scan) stays grey, so each step is no
# bigger than the one before. On a file with no scanned pages the last four
# steps are colour 72/50, 60/40, 50/30 and 50/30 (`same_result`).
LADDER = (
    Recipe(),
    Recipe(200, 85),
    Recipe(150, 80),
    Recipe(120, 70),
    Recipe(96, 65),
    Recipe(72, 50),
    Recipe(72, 50, GREY, 72, 50),
    Recipe(60, 40, BITONAL, 150),
    Recipe(50, 30, BITONAL, 120),
    Recipe(50, 30, BITONAL, 100),
)
LAST_STEP = len(LADDER) - 1

# Quality's three cards (Best quality, Balanced, Smallest file) are steps on
# the ladder. The level names are the ones settings and voice already know.
LEVEL_STEPS = {LIGHT: 0, BALANCED: 2, STRONG: 4}
LEVEL_RECIPES = {level: LADDER[step] for level, step in LEVEL_STEPS.items()}
# The order the dialog lists them, and the order they are estimated in:
# Balanced first, since it is selected until the estimates are in.
LEVEL_ORDER = (LIGHT, BALANCED, STRONG)
QUALITY_ORDER = tuple(LEVEL_STEPS[level] for level in (BALANCED, STRONG, LIGHT))

# Size limit's checking order: the smallest result first, so the floor (what
# no limit below can reach) is known first; then the lossless step, which on a
# file with nothing to resample can be the smallest; then up the ladder from
# the bottom, so each limit's best step is settled as soon as the step above
# it is known not to fit.
CHECK_ORDER = (LAST_STEP, 0) + tuple(range(LAST_STEP - 1, 0, -1))


def helps(before: int, after: int) -> bool:
    """Whether a Quality level is worth writing: at least `MIN_SAVING` smaller."""
    return after <= before * (1 - MIN_SAVING)


def limit_bytes(value, unit) -> int | None:
    """The size limit in bytes (1 KB = 1,000), or None for a number or unit
    that isn't on offer."""
    if isinstance(value, bool) or not isinstance(value, int) or not isinstance(unit, str) or unit not in UNITS:
        return None
    if not LIMIT_MIN <= value <= LIMIT_MAX:
        return None
    return value * UNITS[unit]


def fits(size: int, limit: int) -> bool:
    """Under the limit with the safety margin: at most 97% of it."""
    return size * 100 <= limit * LIMIT_MARGIN_PERCENT


def kind(recipe: Recipe, outcome: dict) -> str:
    """What a step did, from what the engine reports it changed."""
    if outcome.get("bitonal"):
        return BLACK_AND_WHITE
    if outcome.get("grey"):
        return READABLE
    if not outcome.get("rewritten") or recipe.ppi is None or recipe.ppi >= SHARP_PPI:
        return SHARP
    return GOOD if recipe.ppi >= GOOD_PPI else READABLE


def needs_reading_check(step_kind: str) -> bool:
    """Whether the Done state asks the reader to check the file reads well."""
    return step_kind in (READABLE, BLACK_AND_WHITE)


def same_result(step: int, facts: dict) -> list[int]:
    """The other steps whose result is the same file as `step`'s, given what
    an attempt found in this PDF (`facts`: `images`, any image drawn;
    `scans`, how many scanned pages). With no image every step is only the
    lossless tidy; with no scanned page a black-and-white step is the colour
    step at the same settings."""
    if facts.get("images") is False:
        return [n for n in range(len(LADDER)) if n != step]
    if facts.get("scans") == 0:
        recipe = LADDER[step]
        return [
            n
            for n, other in enumerate(LADDER)
            if n != step and (other.ppi, other.quality) == (recipe.ppi, recipe.quality)
        ]
    return []


def choose(sizes: dict[int, int | None], limit: int) -> tuple[int | None, bool]:
    """The best-quality step under `limit`, from the steps known so far
    (`sizes`: step → size, or None for one that failed; a step not there is
    still to come). Returns (step, settled): the step is None while none is
    known to fit; settled says no later result will change the answer.

    Sizes fall down the ladder, so a step's answer is settled once the
    nearest known step above it doesn't fit; "too small" (None, settled) once
    the smallest step and the lossless one are known and neither fits. The
    dialog's copy of this (compress-panel.js, `compressChoice`) must agree."""
    done = {step: size for step, size in sizes.items() if size is not None}
    every = all(step in sizes for step in range(len(LADDER)))
    fitting = [step for step, size in done.items() if fits(size, limit)]
    if not fitting:
        floor_known = (LAST_STEP in done and 0 in sizes) or every
        return None, floor_known
    best = min(fitting)
    for step in range(best - 1, -1, -1):
        if step not in sizes:
            return best, every
        if step in done:
            return best, True
    return best, True


def floor(sizes: dict[int, int | None]) -> int | None:
    """The smallest result known so far."""
    done = [size for size in sizes.values() if size is not None]
    return min(done) if done else None
