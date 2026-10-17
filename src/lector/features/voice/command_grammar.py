"""The fixed voice vocabulary and the spoken-phrase → intent mapping.

Navigation (Milestone 6) and highlighting/save (Milestone 7) are both fixed,
synonym-mapped grammar per `docs/PRD.md` — this module owns intent parsing
for both. The wake phrase (Milestone 8) is deliberately absent.

For HIGHLIGHT and HIGHLIGHT_SENTENCE, `docs/ARCHITECTURE.md` still puts only
*interpretation* here: this module recognizes "highlight <phrase>" as an
intent carrying a `query`, but has no idea what a page's words are — matching
the query against visible text is `annotations/highlight_matcher.py`'s job,
and writing the annotation is `document.py`'s, both reached through `api.py`.

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
from lector.shared import theme

# Intent names cross the bridge to JS as plain strings, so they are part of
# the frontend contract (see `frontend/pages/reading.js`), not private
# implementation detail.
NEXT_PAGE = "NEXT_PAGE"
PREV_PAGE = "PREV_PAGE"
GOTO_PAGE = "GOTO_PAGE"
SCROLL_UP = "SCROLL_UP"
SCROLL_DOWN = "SCROLL_DOWN"
SAVE = "SAVE"
HIGHLIGHT = "HIGHLIGHT"
HIGHLIGHT_SENTENCE = "HIGHLIGHT_SENTENCE"
SET_HIGHLIGHT_COLOR = "SET_HIGHLIGHT_COLOR"
# The reader's side panel and progress bar. "show outline" and "show pages"
# open the panel on that tab and number its entries (the numbered-overlay
# picker), so a spoken number jumps to one.
SHOW_OUTLINE = "SHOW_OUTLINE"
SHOW_PAGES = "SHOW_PAGES"
HIDE_OUTLINE = "HIDE_OUTLINE"
TOGGLE_PROGRESS_BAR = "TOGGLE_PROGRESS_BAR"
# Focus mode: the reader's topbar, toolbar and footer hidden, and back.
FOCUS_MODE = "FOCUS_MODE"
EXIT_FOCUS_MODE = "EXIT_FOCUS_MODE"
# Zoom: the two fit modes, which keep fitting as the window changes, and 100%.
FIT_WIDTH = "FIT_WIDTH"
FIT_PAGE = "FIT_PAGE"
ACTUAL_SIZE = "ACTUAL_SIZE"

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
    SAVE: (
        "save the file",
        "save the document",
        "save the pdf",
        "save",
    ),
    # One phrasing each, deliberately. "show"/"open" against "hide"/"close"
    # outline score 0.67-0.69 to each other, close under the match threshold,
    # and opposite actions must never be one mishearing apart.
    SHOW_OUTLINE: ("show outline",),
    SHOW_PAGES: ("show pages",),
    HIDE_OUTLINE: ("hide outline",),
    TOGGLE_PROGRESS_BAR: ("toggle progress bar",),
    # One phrasing each, for the outline's reason: "enter focus mode" scores
    # 0.75 against "exit focus mode", over the match threshold. "focus mode"
    # against it is 0.67, and turning it on when it is already on does
    # nothing.
    FOCUS_MODE: ("focus mode",),
    EXIT_FOCUS_MODE: ("exit focus mode",),
    # "fit to width" and "fit to page" score 0.58 to each other, and "fit
    # page" 0.67 to "next page": both under the match threshold.
    FIT_WIDTH: ("fit width", "fit to width"),
    FIT_PAGE: ("fit page", "fit to page"),
    ACTUAL_SIZE: ("actual size",),
}

# Leading trigger phrases for HIGHLIGHT_SENTENCE, tried before HIGHLIGHT's —
# "highlight the sentence about foxes" must not be parsed as HIGHLIGHT with
# query "the sentence about foxes", so the longer, more specific trigger set
# always gets first refusal. Order is load-bearing for the same reason
# PHRASES' order is: the earliest trigger wins a tie.
HIGHLIGHT_SENTENCE_TRIGGERS: tuple[str, ...] = (
    "highlight the sentence",
    "highlight this sentence",
    "highlight sentence",
)
HIGHLIGHT_TRIGGERS: tuple[str, ...] = (
    # Deliberately a single trigger, unlike NAV's several synonyms: a
    # 3-word variant like "highlight the word" was tried and dropped, since
    # its fuzzy comparison window straddles the query itself — "highlight
    # the word quick" scored above threshold against "highlight the word"
    # with "quick" read as a mishearing of "word", silently eating the
    # query's first word. A single, short trigger has no such window.
    "highlight",
)

# The palette's colour names, said as themselves ("green"). Both colour
# commands end in one, the same way GOTO_PAGE ends in a number.
HIGHLIGHT_COLORS: tuple[str, ...] = theme.HIGHLIGHT_COLOR_NAMES

# What may precede a colour to make it the active one ("highlight color
# green"). Every phrasing contains "color", and `_parse_highlight_color`
# insists on hearing that word: without it, a highlight query that happens to
# end in a colour ("highlight cool in green" scores 0.76 against "highlight
# color") would change the colour instead of highlighting.
HIGHLIGHT_COLOR_PREFIXES: tuple[str, ...] = (
    "highlight color",
    "set highlight color to",
    "change highlight color to",
)
_COLOR_WORD = "color"

# "highlight <words> in <colour>": the word that splits a one-off colour off
# the end of a highlight query.
HIGHLIGHT_COLOR_JOINER = "in"

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


# (intent, trigger) pairs in priority order. HIGHLIGHT_SENTENCE's triggers
# come first because they are strict extensions of HIGHLIGHT's — "highlight
# the sentence" must not be cut short at "highlight" with "the sentence"
# left stranded in the query.
_HIGHLIGHT_TRIGGERS: tuple[tuple[str, str], ...] = tuple(
    (HIGHLIGHT_SENTENCE, trigger) for trigger in HIGHLIGHT_SENTENCE_TRIGGERS
) + tuple(
    (HIGHLIGHT, trigger) for trigger in HIGHLIGHT_TRIGGERS
)


def _parse_highlight(words: list[str]) -> dict | None:
    """Split a leading trigger phrase off the utterance; what remains is the
    query to match against the visible page (`annotations/highlight_matcher.py`
    does that matching — this function only recognizes that a highlight was
    asked for and what phrase to look for).

    Mirrors `_parse_goto`'s shape but for a *leading* trigger instead of a
    *trailing* number: each candidate trigger's word count fixes how many
    leading words to compare, since a spoken phrase carries no delimiter
    between the trigger and the free-form query that follows it.
    """
    for intent, trigger in _HIGHLIGHT_TRIGGERS:
        trigger_words = trigger.split()
        n = len(trigger_words)
        if len(words) <= n:
            continue  # nothing left over to be a query
        lead = " ".join(words[:n])
        score = fuzzy.similarity(lead, trigger)
        if score < fuzzy.DEFAULT_THRESHOLD:
            continue
        query_words, color = _split_color(words[n:])
        result = {
            "intent": intent,
            "query": " ".join(query_words),
            "matched": f"{trigger} <query>",
            "score": score,
        }
        if color is not None:
            result["color"] = color
            result["matched"] = f"{trigger} <query> {HIGHLIGHT_COLOR_JOINER} <color>"
        return result
    return None


def is_bare_highlight(text: str) -> bool:
    """True when `text` is a highlight trigger with nothing after it.

    `parse` gives that no intent — there is no query to look for. It is
    still worth recognizing, because it is what "highlight <words>" turns
    into when none of the words were in the recognizer's vocabulary (the
    engine drops `[unk]`), which is what happens on a scanned page whose
    text has not been read yet.
    """
    words = _normalize(text)
    return any(
        len(words) == len(trigger.split())
        and fuzzy.similarity(" ".join(words), trigger) >= fuzzy.DEFAULT_THRESHOLD
        for _, trigger in _HIGHLIGHT_TRIGGERS
    )


def _split_color(query_words: list[str]) -> tuple[list[str], str | None]:
    """Split a trailing "in <colour>" off a highlight query.

    Exact words, not fuzzy: the recognizer only ever returns words from its
    closed vocabulary, so a colour is either heard as one of the five or not
    at all. Only when words remain in front of it — "highlight in green"
    leaves nothing to look for, so it is a search for the words "in green".
    A page whose text really is "<words> in green" is therefore highlighted
    without those last two words, in green: a predictable rule beats
    guessing which of the two the reader meant.
    """
    if (
        len(query_words) >= 3
        and query_words[-2] == HIGHLIGHT_COLOR_JOINER
        and query_words[-1] in HIGHLIGHT_COLORS
    ):
        return query_words[:-2], query_words[-1]
    return query_words, None


def _parse_highlight_color(words: list[str]) -> dict | None:
    """"highlight color green" -> SET_HIGHLIGHT_COLOR green.

    Mirrors `_parse_goto`: a trailing argument (a colour, not a number) and a
    fuzzy-matched lead in front of it. The lead must contain the word "color"
    (see HIGHLIGHT_COLOR_PREFIXES), and "... in <colour>" is never this
    command — that ending belongs to a one-off highlight.
    """
    if len(words) < 2 or words[-1] not in HIGHLIGHT_COLORS:
        return None
    lead = words[:-1]
    if _COLOR_WORD not in lead or lead[-1] == HIGHLIGHT_COLOR_JOINER:
        return None
    match = fuzzy.best_match(" ".join(lead), HIGHLIGHT_COLOR_PREFIXES)
    if match is None:
        return None
    matched, score = match
    return {
        "intent": SET_HIGHLIGHT_COLOR,
        "color": words[-1],
        "matched": f"{matched} <color>",
        "score": score,
    }


def parse(text: str) -> dict | None:
    """Turn a recognized phrase into an intent, or `None`.

    `None` means "nothing was recognized as a command" and callers must
    treat it as a no-op: `docs/PRD.md` makes voice an accelerator over a
    fully working mouse/keyboard app, so the cost of ignoring a real command
    (say it again) is much lower than the cost of performing a command the
    reader never gave.

    The returned dict is `{"intent", "phrase", "matched", "score"}`, plus
    `"page"` for `GOTO_PAGE`, `"color"` for `SET_HIGHLIGHT_COLOR`, or
    `"query"` for `HIGHLIGHT`/`HIGHLIGHT_SENTENCE` (with `"color"` too when
    one was said after it).
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
    if match is not None:
        matched, score = match
        return {
            "intent": _PHRASE_TO_INTENT[matched],
            "phrase": phrase,
            "matched": matched,
            "score": score,
        }

    # Before the highlight triggers, which would otherwise take "highlight
    # color green" as a search for the words "color green".
    set_color = _parse_highlight_color(words)
    if set_color is not None:
        return {**set_color, "phrase": phrase}

    # Highlight triggers last: they are free-form (an open-ended query
    # follows the trigger), so a fixed phrase above should get first refusal
    # whenever an utterance could plausibly be either.
    highlight = _parse_highlight(words)
    if highlight is not None:
        return {**highlight, "phrase": phrase}

    return None


def resolve(text: str, alternatives: list[str] | None = None) -> dict:
    """Interpret a recognized phrase the way `parse` does, but never fall
    silent on a phrase that was close to a real command.

    `parse` alone returns `None` for anything under `fuzzy.DEFAULT_THRESHOLD`
    and callers have always treated that as "do nothing" — the right call
    for genuinely unrelated speech, since a wrong action costs the reader
    more than repeating themselves. But Milestone 8.3 adds a middle ground:
    Vosk's own best guess is not always its best *available* hypothesis, and
    a near-miss on the top one is not the same as noise. This tries, in
    order:

    1. `text` itself, exactly as `parse` would.
    2. Each of `alternatives` — Vosk's other n-best hypotheses for the same
       utterance — against the full grammar, so a command Vosk *did* hear
       correctly in its second guess is not thrown away because its first
       guess was a misfire.
    3. Only once none of those confidently match anything, the closest
       near-miss across `text` and every alternative, so the reader gets
       "did you mean '<candidate>'?" instead of silence.

    Returns `{"command": ..., "clarify": ...}`; at most one of the two is not
    `None`. `command` is `parse`'s own return shape. `clarify` is
    `{"intent", "matched", "score", "phrase"}` — `matched` is the phrasing to
    show in "did you mean", `phrase` is what was actually heard.

    Near-miss clarification is scoped to `_ORDERED_PHRASES` — the fixed,
    no-argument commands (next/previous page, scroll, save) — deliberately
    excluding GOTO_PAGE and the highlight triggers: both need a query or a
    number alongside the trigger word to mean anything, so "did you mean
    'go to page'?" with no number to jump to would be a question the reader
    cannot usefully answer "yes" to.
    """
    for candidate_text in (text, *(alternatives or ())):
        command = parse(candidate_text)
        if command is not None:
            return {"command": command, "clarify": None}

    best = None
    for candidate_text in (text, *(alternatives or ())):
        words = _normalize(candidate_text)
        if not words:
            continue
        near = fuzzy.best_near_miss(" ".join(words), _ORDERED_PHRASES)
        if near is not None and (best is None or near[1] > best[1]):
            best = near
    if best is None:
        return {"command": None, "clarify": None}

    matched, score = best
    return {
        "command": None,
        "clarify": {
            "intent": _PHRASE_TO_INTENT[matched],
            "matched": matched,
            "score": score,
            "phrase": " ".join(_normalize(text)),
        },
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
    for trigger in HIGHLIGHT_SENTENCE_TRIGGERS + HIGHLIGHT_TRIGGERS:
        words.update(trigger.split())
    for prefix in HIGHLIGHT_COLOR_PREFIXES:
        words.update(prefix.split())
    words.update(HIGHLIGHT_COLORS)
    words.add(HIGHLIGHT_COLOR_JOINER)
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
