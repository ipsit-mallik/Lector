"""The wake phrase ("Hey Lector") and how it is recognized.

Milestone 8 adds the second half of `docs/PRD.md`'s "dual voice activation":
push-to-talk was Milestone 5, and this is the hands-free path. The audio
plumbing stays in `engine.py`; what lives here is the phrase itself, the
grammar the idle recognizer is pinned to, and the rule for deciding that the
phrase was actually said.

Two deliberate shapes:

* **The idle grammar is as small as it can possibly be.** `docs/TASKS.md`
  asks for "idle low-cost grammar listening", and the cost that matters is
  not really CPU — it is false positives. A recognizer that is always on
  while the reader reads aloud, talks to someone, or plays music will decode
  *something*; pinning it to one phrase means almost all of that decodes to
  `[unk]` instead of to a command. The full command vocabulary is only
  loaded once the phrase has been heard, which is why `engine.py` swaps
  recognizers rather than listening for everything all the time.

* **Both words, adjacent, or it does not count.** Measured against this
  project's own Vosk model with synthesized speech: "hey lector" decodes to
  exactly `hey lector`, but "the lecture was long" decodes to a bare
  `lector` with no `hey` in front of it, and "hey there how are you" decodes
  to `hey [unk] [unk]`. So either word *alone* is a realistic mishearing of
  ordinary speech, while the adjacent pair was not produced by anything
  except the phrase itself. Requiring the pair is what makes the idle
  listener safe to leave running — and a wake the reader did not ask for is
  worse than one that needs saying twice, for the same reason
  `command_grammar.parse` returns None rather than guessing.

* **Adjacency alone stopped being enough once Milestone 8.12 swapped in a
  larger Vosk model.** The bigger model decodes "hey there, how are you
  today" as `hey lector hey [unk]` — the adjacent pair now appears in
  ordinary conversation too, not just in the phrase itself, so
  `contains_wake_phrase` can no longer be the whole rule.
  `wake_phrase_confidence` closes that gap using Vosk's per-word confidence
  (`SetWords(True)`, not the whole-utterance `confidence` field
  `SetMaxAlternatives` returns — see that function's docstring for why the
  two are not interchangeable): genuine "hey lector" audio scores 1.0 on
  both words, while "hey there..." scores 0.68-0.79 on the "lector" it was
  forced into, because the decoder had nothing else in its one-phrase
  grammar to spend that sound on. `engine.py` requires
  `WAKE_WORD_CONFIDENCE_THRESHOLD` before acting on the adjacency match this
  module reports.
"""

WAKE_PHRASE = "hey lector"
WAKE_WORDS: tuple[str, ...] = tuple(WAKE_PHRASE.split())

# The same phrase as the reader should see it written. Recognition works in
# lowercase, but "hey lector" set in quotation marks in the UI reads as a
# transcript of something already said rather than as an instruction. Derived
# rather than typed out, so it cannot come to name a different phrase than the
# one the recognizer is listening for.
WAKE_PHRASE_DISPLAY = WAKE_PHRASE.title()

# What the idle recognizer may return. Passed to Vosk as its whole grammar,
# alongside the unknown-word token `engine.py` appends — everything that is
# not this phrase is meant to land on that token and be discarded.
WAKE_GRAMMAR: list[str] = [WAKE_PHRASE]

# How long the microphone stays open for a command after the phrase is heard.
# Long enough to say "highlight the quick brown fox" without rushing, short
# enough that a wake triggered by accident closes again on its own rather
# than leaving a microphone open the reader never asked to open.
COMMAND_WINDOW_SECONDS = 6.0

# How well Vosk must have heard each word of the phrase — see
# `wake_phrase_confidence` — before the idle listener trusts an adjacency
# match. Measured against this project's own model and fixtures: genuine
# "hey lector" audio scores 1.0 on both words every time, while every
# measured false-adjacency case ("hey there, how are you today" and several
# synthesized variants on it) scored between 0.68 and 0.79 on "lector". 0.9
# sits in the gap between those two clusters with margin on both sides,
# rather than splitting a narrow one.
WAKE_WORD_CONFIDENCE_THRESHOLD = 0.9


def contains_wake_phrase(text: str) -> bool:
    """Whether `text` contains the wake phrase as adjacent whole words.

    Substring matching would not do: "collector" contains neither word as a
    word, and a bare "lector" — which ordinary speech really does produce
    (see the module docstring) — must not wake anything.
    """
    words = (text or "").lower().split()
    span = len(WAKE_WORDS)
    return any(
        tuple(words[i:i + span]) == WAKE_WORDS
        for i in range(len(words) - span + 1)
    )


def wake_phrase_confidence(words: list[dict]) -> float | None:
    """The weakest per-word confidence across the wake phrase's last adjacent
    occurrence in `words`, or `None` if the phrase is not there at all.

    `words` is Vosk's `SetWords(True)` result list — each entry a
    `{"word": ..., "conf": ..., ...}` mapping in decode order, `[unk]`
    entries included — not the cleaned text `contains_wake_phrase` checks.
    That matters here: this function requires the phrase to be adjacent in
    the *raw* decode, filler words and all, since a low-confidence "lector"
    the decoder only guessed at is exactly the case
    `WAKE_WORD_CONFIDENCE_THRESHOLD` exists to catch (see the module
    docstring). Mirrors `strip_wake_phrase`'s "last occurrence" rule, and
    takes the minimum of the pair rather than the average, so a word the
    decoder was confident about cannot mask one it was only guessing at.

    Deliberately not Vosk's whole-utterance `confidence` field (available
    from `SetMaxAlternatives` instead of `SetWords`): that number is an
    accumulated log-likelihood, so it grows with utterance length rather
    than reflecting how sure the decoder was about any one word — measured
    against this project's own fixtures, "hey there, how are you today"
    scored *higher* on that field than genuine "hey lector" audio did,
    simply for being the longer utterance. `fuzzy.py` made the same call
    for the same reason when Milestone 8.3 built its near-miss matching.
    """
    tokens = [w.get("word", "") for w in words]
    span = len(WAKE_WORDS)
    for i in range(len(tokens) - span, -1, -1):
        if tuple(tokens[i:i + span]) == WAKE_WORDS:
            return min(words[i + j].get("conf", 0.0) for j in range(span))
    return None


def strip_wake_phrase(text: str) -> str:
    """Whatever followed the wake phrase in `text`, or "".

    The idle grammar cannot hear a command — every word outside the phrase
    decodes to the unknown token — so in practice this returns "" and the
    command arrives in the window that follows. It exists because the phrase
    and anything said after it are not the same thing, and a caller echoing
    the utterance back to the reader should not show them "hey lector".
    """
    words = (text or "").lower().split()
    span = len(WAKE_WORDS)
    for i in range(len(words) - span, -1, -1):
        if tuple(words[i:i + span]) == WAKE_WORDS:
            return " ".join(words[i + span:])
    return ""
