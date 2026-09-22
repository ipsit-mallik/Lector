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
