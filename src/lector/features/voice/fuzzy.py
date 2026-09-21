"""Edit-distance tolerance for imperfect speech recognition.

Recognition is never exact. A grammar-constrained Vosk recognizer will not
invent words outside its vocabulary, but it will still return the *wrong*
word from that vocabulary ("next page" heard as "text page") or drop one
("go to page" as "to page"). Comparing recognized text to command phrases
with `==` therefore fails often enough to make voice feel unreliable, which
`docs/PRD.md` treats as worse than not having it.

This module is deliberately separate from `command_grammar.py` because
`docs/TASKS.md` calls for the tolerance to be built once and reused: it is
Milestone 6's command matching *and* Milestone 7's spoken-phrase-to-text
highlight matching. Nothing here knows what a command is, so the highlight
matcher can compare a spoken phrase against a page's words with the same
primitives. (`docs/ARCHITECTURE.md` keeps the *matching logic* — viewport
scoping, tie-breaking, span selection — in `annotations/`; only this
string-level primitive is shared.)
"""

# Below this, two strings are different phrases rather than two hearings of
# the same phrase. Tuned against the navigation grammar's short commands,
# where a single wrong character in a 9-character phrase ("next page")
# scores ~0.89 and a genuinely different command ("previous page") scores
# well under 0.5 — so the gap is wide and the exact cut-off is not delicate.
DEFAULT_THRESHOLD = 0.72


def levenshtein(a: str, b: str) -> int:
    """Minimum single-character insertions, deletions, and substitutions
    turning `a` into `b`.

    Two rolling rows rather than a full matrix: the inputs here are short
    phrases, but Milestone 7 will call this across every word on a page, and
    an O(len(b)) footprint keeps that affordable.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            cost = 0 if ch_a == ch_b else 1
            current.append(min(
                previous[j] + 1,         # deletion
                current[j - 1] + 1,      # insertion
                previous[j - 1] + cost,  # substitution
            ))
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """How alike two strings are, from 0.0 (nothing in common) to 1.0
    (identical), normalized by the longer string so that a one-character
    error counts for less in a long phrase than in a short one."""
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - (levenshtein(a, b) / longest)


def best_match(needle: str, candidates, threshold: float = DEFAULT_THRESHOLD):
    """The closest candidate to `needle`, or `None` if nothing is close enough.

    Returns `(candidate, score)`. Ties go to the earliest candidate, so
    callers control precedence by ordering — which is how Milestone 7's
    "first occurrence in reading order wins" rule falls out for free.

    Returning `None` rather than the least-bad candidate is the important
    part: a misheard phrase that matches nothing should do nothing, because
    a wrong action the reader has to notice and undo is worse than no action
    at all.
    """
    best = None
    best_score = 0.0
    for candidate in candidates:
        score = similarity(needle, candidate)
        if score > best_score:
            best = candidate
            best_score = score
    if best is None or best_score < threshold:
        return None
    return best, best_score
