"""What Size limit decides, and which real step it runs next. Pure: nothing
read or written, no PyMuPDF. `estimates.py` runs it; `compress-panel.js`'s
`compressDecision` is its copy for the page, and a test runs both over the
same cases.

Two kinds of size are known about a ladder step (`compress_plan.LADDER`):

- real: the step compressed for real into a temporary file (`real`: step →
  size, or None for one that failed);
- estimated: a big file's sample of pages compressed at that step and scaled
  to the whole document (`estimates`: step → size, or None for one the
  sample couldn't make). Only files over `SAMPLE_MIN_BYTES` or
  `SAMPLE_MIN_PAGES` are sampled.

An estimate may decide a pill only with room to spare, so Lector never
offers a limit it can't meet, nor dims one it could: enabled when the step it
picks is estimated at `ENABLE_PERCENT` of the limit or less, dimmed when the
smallest steps are estimated at `DIM_PERCENT` or more. The same margin rules
out a better step: one above the chosen step is passed over only if it is
really over the limit or estimated at `DIM_PERCENT` or more; one estimated
nearer, or that the sample couldn't make, is in doubt (`_doubt`), and the
pill stays enabled but not final until it is made for real. Anything between
waits for a real compression. If a step enabled on an estimate turns out
over the limit after all, Compress takes the next step down that really fits
(`tools.compress_to_limit`).
"""
from lector.features.file_tools import compress_plan

ENABLE_PERCENT = 80
DIM_PERCENT = 130

# Big files: estimated from a sample before the real steps run.
SAMPLE_MIN_BYTES = 20 * compress_plan.MB
SAMPLE_MIN_PAGES = 30
# The sample: the pages with the most image bytes, and pages spread evenly
# through the rest.
SAMPLE_HEAVY = 2
SAMPLE_SPREAD = 2

# The smallest results: what "too small" is measured against
# (`compress_plan.choose`'s floor).
FLOOR_STEPS = (compress_plan.LAST_STEP, 0)

_STEPS = range(len(compress_plan.LADDER))
# What a step still to come could turn out to be: a size every limit takes,
# one none does, or a failure.
_HUGE = 1 << 62
_PROBES = (0, _HUGE, None)


def needs_sample(source_bytes: int, pages: int) -> bool:
    return source_bytes > SAMPLE_MIN_BYTES or pages > SAMPLE_MIN_PAGES


def merged(real: dict, estimates: dict | None) -> dict:
    """Each step's best-known size: the real one where there is one."""
    out = dict(estimates or {})
    out.update(real)
    return out


def decide(real: dict, estimates: dict | None, limit: int) -> tuple[int | None, bool]:
    """A limit's answer, in `compress_plan.choose`'s terms: (step, settled).
    Settled with a step: the result card is final and Compress can run.
    Settled with None: too small. Not settled with a step: a step is known
    to fit (really, or estimated with room to spare; the pill is enabled)
    but a better one might too. Not settled with None: still checking.

    The real results decide first. Otherwise, with estimates, the best step
    by every step's best-known size counts if it is real, or estimated at
    `ENABLE_PERCENT` of the limit or less; it is final once no better step
    is in doubt (`_doubt`). "Too small" if each floor step is real and over
    the limit, or estimated at `DIM_PERCENT` or more."""
    step, settled = compress_plan.choose(real, limit)
    if settled or not estimates:
        return step, settled
    best, known = compress_plan.choose(merged(real, estimates), limit)
    if best is None:
        if known and all(_over_floor(real, estimates, s, limit) for s in FLOOR_STEPS):
            return None, True
        return step, False
    if not known or (best not in real and estimates[best] * 100 > limit * ENABLE_PERCENT):
        return step, False
    return best, _doubt(real, estimates, best, limit) is None


def _doubt(real: dict, estimates: dict, best: int, limit: int) -> int | None:
    """The nearest step above `best` that might still fit `limit`, or None
    when every better step is ruled out. Sizes fall down the ladder, so the
    first step above that is really over (or estimated at `DIM_PERCENT` or
    more) rules out every step above it too; a step that really failed is
    passed over, as `compress_plan.choose` does."""
    for step in range(best - 1, -1, -1):
        if step in real:
            if real[step] is None:
                continue
            return None
        size = estimates.get(step)
        if size is not None and size * 100 >= limit * DIM_PERCENT:
            return None
        return step
    return None


def _over_floor(real: dict, estimates: dict, step: int, limit: int) -> bool:
    if real.get(step) is not None:
        return not compress_plan.fits(real[step], limit)
    size = estimates.get(step)
    return size is not None and size * 100 >= limit * DIM_PERCENT


def limits_of_interest(source_bytes: int, selected: int | None) -> list[int]:
    """The visible pills' limits (below the file's size), and the one the
    reader chose ("Other size…" may be none of them)."""
    out = [compress_plan.limit_bytes(v, u) for v, u in compress_plan.PRESETS]
    out = [limit for limit in out if limit < source_bytes]
    if selected is not None and selected < source_bytes and selected not in out:
        out.append(selected)
    return out


def could_change(real: dict, step: int, limit: int) -> bool:
    """Whether `step`'s real result, whatever it turns out to be, could
    change what `compress_plan.choose` answers for `limit`."""
    now = compress_plan.choose(real, limit)
    return any(compress_plan.choose({**real, step: probe}, limit) != now for probe in _PROBES)


def answers(real: dict, estimates: dict | None, limits: list[int]) -> set[int]:
    """The steps that are some limit's current answer: its decided step, or
    the best real one known to fit while it is still being checked. Their
    temporary files are kept; every other step's is deleted."""
    out = set()
    for limit in limits:
        step, _settled = decide(real, estimates, limit)
        if step is not None:
            out.add(step)
        best, _known = compress_plan.choose(real, limit)
        if best is not None:
            out.add(best)
    return out


def next_step(real: dict, estimates: dict | None, limits: list[int], selected: int | None) -> int | None:
    """The real step to run next for Size limit, or None when no step left
    could change any limit's answer (every pill is settled).

    Without estimates (a small file): `compress_plan.CHECK_ORDER`, skipping
    steps no unsettled limit needs. With them: the selected limit's step
    first, then the steps that decide the limits nearest a margin, then
    whatever an unsettled limit still needs, in `CHECK_ORDER`."""
    unknown = [s for s in _STEPS if s not in real]
    if not unknown:
        return None
    open_limits = [limit for limit in limits if not decide(real, estimates, limit)[1]]
    if estimates:
        candidates = []
        if selected is not None and selected in limits:
            candidates.append(selected)
        candidates += sorted(open_limits, key=lambda limit: _margin_distance(real, estimates, limit))
        for limit in candidates:
            step = _deciding_step(real, estimates, limit, unknown)
            if step is not None:
                return step
    for step in compress_plan.CHECK_ORDER:
        if step in unknown and any(could_change(real, step, limit) for limit in open_limits):
            return step
    return None


def _deciding_step(real: dict, estimates: dict, limit: int, unknown: list[int]) -> int | None:
    """The real step that would confirm or overturn `limit`'s answer: the
    step decided for it (on an estimate), or for one still open, the better
    step in doubt, else the best step by best-known sizes, or with none, the
    floor."""
    step, settled = decide(real, estimates, limit)
    if not settled:
        step, _known = compress_plan.choose(merged(real, estimates), limit)
        if step is None:
            return next((s for s in FLOOR_STEPS if s in unknown), None)
        doubt = _doubt(real, estimates, step, limit)
        if doubt is not None and doubt in unknown:
            return doubt
    return step if step in unknown else None


def _margin_distance(real: dict, estimates: dict, limit: int) -> float:
    """How near a still-open limit's estimate is to deciding it: its chosen
    step's estimate (or the floor's) as a share of the limit, against the
    nearer margin."""
    sizes = merged(real, estimates)
    step, _known = compress_plan.choose(sizes, limit)
    if step is None:
        known = [sizes[s] for s in FLOOR_STEPS if sizes.get(s) is not None]
        if not known:
            return float("inf")
        size = min(known)
    else:
        size = sizes[step]
    share = 100 * size / limit
    return min(abs(share - ENABLE_PERCENT), abs(share - DIM_PERCENT))


def scale_sample(setup: dict, measured: dict) -> int:
    """A whole-document size from a sample's compression (`sampling.py`).

    Images: the heavy pages' images are counted as compressed; the rest of
    the document's image bytes are scaled by how much the spread pages'
    images shrank. Everything else (text, fonts, structure): scaled by how
    much the sample's non-image bytes shrank. Scaling by bytes rather than
    by page count keeps a few image-heavy pages in the sample from inflating
    (or shrinking) the whole document's estimate."""
    heavy_out = measured["heavy_out"] * _ratio(setup["heavy_full"], setup["heavy_in"], 1.0)
    rest_in = max(0, setup["image_bytes"] - setup["heavy_full"])
    every_in = setup["heavy_in"] + setup["spread_in"]
    every_out = measured["heavy_out"] + measured["spread_out"]
    image_ratio = _ratio(measured["spread_out"], setup["spread_in"], _ratio(every_out, every_in, 1.0))
    other_in = max(0, setup["sample_bytes"] - every_in)
    other_out = max(0, measured["out"] - every_out)
    other_full = max(0, setup["source_bytes"] - setup["image_bytes"])
    return max(1, round(heavy_out + image_ratio * rest_in + _ratio(other_out, other_in, 1.0) * other_full))


def _ratio(top: float, bottom: float, fallback: float) -> float:
    return top / bottom if bottom > 0 else fallback
