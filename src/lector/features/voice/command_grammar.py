"""The fixed navigation vocabulary and the spoken-phrase → intent mapping.

Milestone 6 scope (see `docs/TASKS.md`): navigation only. These are the
lowest-risk voice commands — there is no ambiguity about *what* to act on
and no matching against document text, just intent → action. Highlighting
(Milestone 7) and the wake phrase (Milestone 8) are deliberately absent.

Three things live here, and they are connected:

* **The intents.** `docs/ARCHITECTURE.md` puts interpretation in `voice/`
  and the acting-on-it in the frontend: this module emits `NEXT_PAGE`, it
  does not know that a page is an image in a scroll container. The set is
  exactly `docs/PRD.md`'s stated navigation scope — next/previous page,
  jump to page N, scroll up/down. "First/last page" is *not* here: it is
  not in the PRD's enumeration, and adding commands the product doc does
  not list is a scope decision rather than an implementation one.

* **The synonyms.** `docs/PRD.md` rules out general-purpose NLU in favour
  of "a fixed, synonym-mapped grammar (several known phrasings per
  intent)". A reader should not have to remember that it is "next page"
  and not "turn the page", so each intent lists the phrasings people
  actually reach for.

* **The vocabulary.** `VOCABULARY` is derived from the phrasings rather
  than maintained beside them, so a synonym can never be added without the
  recognizer also being able to hear its words. `engine.py` pins its
  `KaldiRecognizer` to this list, which is what makes recognition a
  closed-set problem instead of open transcription.
"""
from lector.features.voice import fuzzy

# Intent names cross the bridge to JS as plain strings, so they are part of
# the frontend contract (see `frontend/pages/reading.js`), not private
# implementation detail.
NEXT_PAGE = "NEXT_PAGE"
PREV_PAGE = "PREV_PAGE"
GOTO_PAGE = "GOTO_PAGE"
SCROLL_UP = "SCROLL_UP"
SCROLL_DOWN = "SCROLL_DOWN"

# Phrasings per intent, each ordered most-explicit first. Order is load
# bearing: `fuzzy.best_match` breaks ties by taking the earliest candidate,
# so an utterance that sits equally close to two phrasings resolves to the
# more explicit one.
PHRASES: dict[str, tuple[str, ...]] = {
    NEXT_PAGE: (
        "next page",
        "turn the page",
        "page forward",
        "go forward",
        "forward",
        "next",
    ),
    PREV_PAGE: (
        "previous page",
        "go back a page",
        "page back",
        "go back",
        "previous",
        "back",
    ),
    SCROLL_DOWN: (
        "scroll down",
        "move down",
        "down",
    ),
    SCROLL_UP: (
        "scroll up",
        "move up",
        "up",
    ),
}

# What may precede a page number. A bare number is deliberately not a jump
# command: "five" alone is far more likely to be a misfire than an intent,
# and a wrong jump loses the reader's place.
GOTO_PREFIXES: tuple[str, ...] = (
    "go to page",
    "jump to page",
    "turn to page",
    "open page",
    "go to",
    "page",
)

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_HUNDRED = "hundred"
# Spoken as "one hundred and five"; carries no numeric value of its own but
# has to be in the vocabulary or the recognizer cannot hear the phrase.
_FILLER = "and"

# Only characters the recognizer can actually produce, plus the ones a test
# or a typed command might carry. Everything else is stripped rather than
# rejected, so a trailing "." never costs a match.
_KEPT = set("abcdefghijklmnopqrstuvwxyz0123456789 ")


def _normalize(text: str) -> list[str]:
    lowered = (text or "").lower()
    cleaned = "".join(ch if ch in _KEPT else " " for ch in lowered)
    return cleaned.split()


def _is_number_word(word: str) -> bool:
    return word.isdigit() or word in _UNITS or word in _TENS or word == _HUNDRED


def _words_to_number(words: list[str]) -> int | None:
    """"twenty three" → 23, "one hundred and five" → 105, "7" → 7.

    Returns None for anything it cannot read as a single whole number, so a
    half-heard phrase produces no jump rather than a jump to the wrong page.
    """
    if not words:
        return None
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])

    total = 0
    current = 0
    seen = False
    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == _HUNDRED:
            # "hundred" with nothing before it means "one hundred".
            current = (current or 1) * 100
        elif word == _FILLER:
            continue
        elif word.isdigit():
            # Mixing digits into a spoken number ("twenty 3") is not a
            # phrasing worth guessing at.
            return None
        else:
            return None
        seen = True
    return total + current if seen else None


def _parse_goto(words: list[str]) -> dict | None:
    """Split a trailing number off the utterance and check what precedes it."""
    split = len(words)
    # "and" is swept up with the digits because "one hundred and five" is one
    # number spoken naturally; stopping the scan at it would leave "and" in
    # the lead, where it would wreck the match against "go to page".
    while split > 0 and (_is_number_word(words[split - 1]) or words[split - 1] == _FILLER):
        split -= 1
    number_words = words[split:]
    lead = words[:split]
    # A run of pure filler is not a number, and a number with nothing in
    # front of it is not a jump command.
    if not lead or not any(_is_number_word(w) for w in number_words):
        return None

    page = _words_to_number(number_words)
    # Page 0 does not exist in the 1-based numbering the reader sees.
    if page is None or page < 1:
        return None

    match = fuzzy.best_match(" ".join(lead), GOTO_PREFIXES)
    if match is None:
        return None
    matched, score = match
    return {
        "intent": GOTO_PAGE,
        "page": page,
        "matched": f"{matched} <number>",
        "score": score,
    }


def parse(text: str) -> dict | None:
    """Turn a recognized phrase into a navigation intent, or `None`.

    `None` means "nothing was recognized as a command" and callers must
    treat it as a no-op: `docs/PRD.md` makes voice an accelerator over a
    fully working mouse/keyboard app, so the cost of ignoring a real command
    (say it again) is much lower than the cost of performing a command the
    reader never gave.

    The returned dict is `{"intent", "phrase", "matched", "score"}`, plus
    `"page"` for `GOTO_PAGE`.
    """
    words = _normalize(text)
    if not words:
        return None
    phrase = " ".join(words)

    # Numbers first: "page five" must be a jump, not a fuzzy near-miss on
    # the "page forward" phrasing.
    goto = _parse_goto(words)
    if goto is not None:
        return {**goto, "phrase": phrase}

    match = fuzzy.best_match(phrase, _ORDERED_PHRASES)
    if match is None:
        return None
    matched, score = match
    return {
        "intent": _PHRASE_TO_INTENT[matched],
        "phrase": phrase,
        "matched": matched,
        "score": score,
    }


def vocabulary() -> list[str]:
    """Every word the recognizer needs to hear this grammar, de-duplicated
    and sorted. `engine.py` pins its recognizer to exactly this list."""
    words: set[str] = set()
    for phrasings in PHRASES.values():
        for phrasing in phrasings:
            words.update(phrasing.split())
    for prefix in GOTO_PREFIXES:
        words.update(prefix.split())
    words.update(_UNITS)
    words.update(_TENS)
    words.add(_HUNDRED)
    words.add(_FILLER)
    return sorted(words)


# Flattened once at import: `parse` runs per utterance and rebuilding the
# candidate list each time would be wasted work.
_ORDERED_PHRASES: tuple[str, ...] = tuple(
    phrasing for phrasings in PHRASES.values() for phrasing in phrasings
)
_PHRASE_TO_INTENT: dict[str, str] = {
    phrasing: intent
    for intent, phrasings in PHRASES.items()
    for phrasing in phrasings
}

VOCABULARY: list[str] = vocabulary()
