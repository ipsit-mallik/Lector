"""Spoken-phrase-to-page-text matching for voice highlighting (Milestone 7).

`docs/PRD.md` scopes matching to "the currently visible viewport" and settles
ties by "first occurrence in reading order" — both because searching the
whole document risks highlighting a word the reader cannot currently see (a
wrong, hard-to-notice action), and because a fixed, deterministic tie-break is
what makes a repeated command behave predictably. `voice/fuzzy.py` supplies
the edit-distance tolerance this needs; this module only adds the things that
are specific to *this* match — which words are in scope, and how a matched
span becomes an annotation-ready index range — per `docs/ARCHITECTURE.md`'s
split of matching logic into `annotations/`.
"""
from dataclasses import dataclass

from lector.features.annotations.highlighter import Word
from lector.features.voice import fuzzy

# A sentence's end, for the nearest-punctuation heuristic `extend_to_sentence`
# uses. Not full sentence-segmentation NLP — docs/PRD.md calls the
# sentence-level extension a heuristic, not a linguistic guarantee.
_SENTENCE_END = (".", "!", "?")


@dataclass(frozen=True)
class WordMatch:
    """A spoken phrase's best match against a page's visible words."""

    page_index: int
    start_idx: int
    end_idx: int
    score: float


def normalize_word(text: str) -> str:
    """Lowercase, alphanumeric-only form of a word.

    Matches `command_grammar._normalize`'s treatment of recognized speech, so
    a page word like "fox," compares equal to the "fox" Vosk actually
    returns — the recognizer's grammar never contains punctuation.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def visible_word_indices(words: list[Word], y0: float, y1: float) -> list[int]:
    """Indices into `words` whose box vertically overlaps `[y0, y1]`.

    `y0`/`y1` are PDF points, the same space `Word.rect` is already in — the
    caller (`api.py`) converts from on-screen pixels before calling this, so
    this function stays independent of zoom and rendering.
    """
    return [i for i, word in enumerate(words) if word.rect.y1 > y0 and word.rect.y0 < y1]


def _contiguous_runs(sorted_indices: list[int]) -> list[list[int]]:
    """Groups a sorted index list into runs of consecutive integers.

    A vertical band of visible text is not always literally contiguous in
    `words_on_page`'s reading-order list (multi-column layouts can interleave
    blocks), so a match window is only ever considered within one such run —
    never bridging a gap the reader cannot actually see as one line.
    """
    runs: list[list[int]] = []
    for idx in sorted_indices:
        if runs and runs[-1][-1] == idx - 1:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    return runs


def find_word_match(
    pages: list[tuple[int, list[Word], list[int]]],
    query: str,
    threshold: float = fuzzy.DEFAULT_THRESHOLD,
) -> WordMatch | None:
    """The best visible match for `query` across one or more viewport pages.

    `pages` is `(page_index, words_on_that_page, visible_indices)` in reading
    order (viewport-ascending page order, as the frontend supplies it); a
    match never spans two entries, so a highlight can never straddle a page
    boundary. Ties go to the earliest candidate encountered — `pages` order
    then window position — which is how "first occurrence in reading order
    wins" (docs/PRD.md) falls out of iteration order alone.
    """
    query_words = [w for w in query.split() if w]
    n = len(query_words)
    if n == 0:
        return None
    needle = " ".join(query_words)

    best: WordMatch | None = None
    for page_index, words, visible in pages:
        for run in _contiguous_runs(sorted(visible)):
            for start in range(len(run) - n + 1):
                idxs = run[start:start + n]
                candidate = " ".join(normalize_word(words[i].text) for i in idxs)
                score = fuzzy.similarity(needle, candidate)
                if best is None or score > best.score:
                    best = WordMatch(page_index, idxs[0], idxs[-1], score)

    if best is None or best.score < threshold:
        return None
    return best


def extend_to_sentence(words: list[Word], start_idx: int, end_idx: int) -> tuple[int, int]:
    """Widens a word-level match to the sentence it sits in.

    Walks outward from the match until it crosses a sentence boundary in
    either direction, stopping at the page's edges if none is found. Runs
    against the page's *full* word list, not the viewport-clipped one the
    match itself was found in: `docs/PRD.md` scopes *matching* to the visible
    viewport so a misheard phrase cannot highlight text the reader cannot
    see, but a sentence that starts just above or below the viewport edge is
    still the sentence the reader meant, and staying on the same page keeps
    this from becoming a whole-document search.
    """
    start = start_idx
    while start > 0 and not words[start - 1].text.rstrip().endswith(_SENTENCE_END):
        start -= 1
    end = end_idx
    while end < len(words) - 1 and not words[end].text.rstrip().endswith(_SENTENCE_END):
        end += 1
    return start, end
