"""Organise pages' draft: which pages, in which order, turned how far.

Organise never edits the open document while it is open. Every action makes
a new plan from the last one, and Done applies the latest plan to the
document as one undoable edit (docs/ARCHITECTURE.md § "Page changes
(Milestone F2)"). Plans are immutable tuples, so Organise's own Undo and
Redo are just a list of them.

No PyMuPDF here: a plan only names pages. `PdfDocument.apply_page_plan`
turns one into pages, through `file_tools.engine.apply_page_plan`.

Positions are 0-based places in the plan as it is now. A "gap" (where a
move or an insert lands) is 0..len(plan): before the page at that position,
or after the last page.
"""
from collections.abc import Iterable
from dataclasses import dataclass, replace

# A page of the open document itself, as opposed to one inserted from
# another file (whose source id `PdfDocument.add_page_source` hands out).
DOC = "doc"

QUARTER_TURN = 90
FULL_TURN = 360

# PlanError reasons.
EVERY_PAGE = "every_page"
BAD_POSITIONS = "bad_positions"
BAD_GAP = "bad_gap"
BAD_SOURCE = "bad_source"


class PlanError(ValueError):
    """An action the plan can't take, with a reason code (above)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PlanPage:
    """One page of the plan: page `index` of `source`, turned `turn`
    degrees clockwise beyond the rotation it already has."""

    source: str
    index: int
    turn: int = 0

    @property
    def is_new(self) -> bool:
        """Inserted in this Organise session: shown with a "New" pill."""
        return self.source != DOC

    @property
    def key(self) -> str:
        """Stable across moves, so the page can keep its selection and its
        thumbnail through any action."""
        return f"{self.source}:{self.index}"


Plan = tuple[PlanPage, ...]


def initial(page_count: int) -> Plan:
    """The open document as it is: every page, in order, unturned."""
    return tuple(PlanPage(DOC, i) for i in range(page_count))


def is_unchanged(plan: Plan, page_count: int) -> bool:
    """Whether applying `plan` would change nothing."""
    return plan == initial(page_count)


def checked_positions(plan: Plan, positions: Iterable[int]) -> tuple[int, ...]:
    """`positions` sorted, or PlanError unless they are distinct places in
    `plan` and there is at least one."""
    found = tuple(positions)
    if not all(isinstance(p, int) and not isinstance(p, bool) for p in found):
        raise PlanError(BAD_POSITIONS)
    unique = tuple(sorted(set(found)))
    if not unique or len(unique) != len(found) or unique[0] < 0 or unique[-1] >= len(plan):
        raise PlanError(BAD_POSITIONS)
    return unique


def _checked_gap(plan: Plan, gap: int) -> int:
    if not isinstance(gap, int) or isinstance(gap, bool) or not 0 <= gap <= len(plan):
        raise PlanError(BAD_GAP)
    return gap


def rotate(plan: Plan, positions: Iterable[int], degrees: int) -> Plan:
    """The pages at `positions` turned by `degrees` (a multiple of 90;
    negative is anticlockwise)."""
    chosen = set(checked_positions(plan, positions))
    if degrees % QUARTER_TURN:
        raise PlanError(BAD_POSITIONS)
    return tuple(
        replace(page, turn=(page.turn + degrees) % FULL_TURN) if i in chosen else page
        for i, page in enumerate(plan)
    )


def delete(plan: Plan, positions: Iterable[int]) -> Plan:
    """The plan without the pages at `positions`. A PDF needs at least one
    page, so deleting every page is refused (`EVERY_PAGE`)."""
    chosen = set(checked_positions(plan, positions))
    if len(chosen) == len(plan):
        raise PlanError(EVERY_PAGE)
    return tuple(page for i, page in enumerate(plan) if i not in chosen)


def move(plan: Plan, positions: Iterable[int], gap: int) -> tuple[Plan, tuple[int, ...]]:
    """The pages at `positions` taken out and put back together, in their
    current order, at `gap` (counted in the plan before the move). Returns
    the new plan and where the moved pages now are. Dropping a block onto
    itself changes nothing."""
    chosen = checked_positions(plan, positions)
    gap = _checked_gap(plan, gap)
    taken = set(chosen)
    block = [plan[i] for i in chosen]
    rest = [page for i, page in enumerate(plan) if i not in taken]
    at = gap - sum(1 for i in chosen if i < gap)
    moved = tuple(rest[:at] + block + rest[at:])
    return moved, tuple(range(at, at + len(block)))


def shift(plan: Plan, positions: Iterable[int], step: int) -> tuple[Plan, tuple[int, ...]]:
    """Ctrl+← / Ctrl+→: the selection moved one place left (`step` < 0) or
    right, as a block (a scattered selection is gathered where its first
    page goes). At either end it stays put."""
    chosen = checked_positions(plan, positions)
    gap = max(chosen[0] - 1, 0) if step < 0 else min(chosen[-1] + 2, len(plan))
    return move(plan, chosen, gap)


def insert(plan: Plan, gap: int, source: str, count: int) -> tuple[Plan, tuple[int, ...]]:
    """All `count` pages of `source` put in at `gap`, in their own order.
    Returns the new plan and where the new pages are."""
    gap = _checked_gap(plan, gap)
    if source == DOC or not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise PlanError(BAD_SOURCE)
    if any(page.source == source for page in plan):
        raise PlanError(BAD_SOURCE)  # each inserted file goes in once
    pages = tuple(PlanPage(source, i) for i in range(count))
    return plan[:gap] + pages + plan[gap:], tuple(range(gap, gap + count))


class PlanHistory:
    """Organise's own Undo and Redo: every plan since it opened, and which
    one is current."""

    def __init__(self, plan: Plan):
        self._plans: list[Plan] = [plan]
        self._at = 0

    @property
    def current(self) -> Plan:
        return self._plans[self._at]

    @property
    def can_undo(self) -> bool:
        return self._at > 0

    @property
    def can_redo(self) -> bool:
        return self._at < len(self._plans) - 1

    @property
    def steps(self) -> int:
        """How many actions are in effect: the footer's "N unsaved
        changes" while Organise is open."""
        return self._at

    def push(self, plan: Plan) -> bool:
        """Make `plan` current, dropping anything that could be redone.
        False (and nothing recorded) when it is the current plan already."""
        if plan == self.current:
            return False
        del self._plans[self._at + 1:]
        self._plans.append(plan)
        self._at += 1
        return True

    def undo(self) -> Plan | None:
        if not self.can_undo:
            return None
        self._at -= 1
        return self.current

    def redo(self) -> Plan | None:
        if not self.can_redo:
            return None
        self._at += 1
        return self.current
