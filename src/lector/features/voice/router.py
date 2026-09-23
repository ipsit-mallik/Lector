"""The contextual voice command router (Milestone 8.4).

`docs/ARCHITECTURE.md`'s "Voice context router" section frames the shape:
the recognizer has always run one active grammar at a time — `wake.py`
proves the pattern by swapping between the idle wake grammar and the full
command grammar — and this module generalizes that from two grammars to one
per screen or dialog. A **context** is whichever screen or dialog currently
has the reader's attention (`HOME`, `READING`, `SETTINGS`, and the
dialog/picker/dictation contexts Milestones 8.6-8.8 add); the active grammar
is always the fixed **global** command set — available no matter what is on
screen — unioned with that context's own scoped set.

Two things are deliberately kept apart:

* **`command_grammar.py` still owns the reading grammar entirely** — its
  navigation/highlight phrasings, its numeric `GOTO_PAGE` parsing, its
  highlight-trigger parsing, and Milestone 8.3's near-miss clarification are
  all untouched by this module. Migrating the reading grammar into the
  `READING` context means routing to it, not rewriting it: `resolve()` below
  delegates to `command_grammar.resolve()` unchanged whenever `READING` is
  active, which is what keeps this migration behavior-preserving rather than
  a rewrite in disguise.

* **Global commands are matched before a context is consulted at all**, via
  their own small fixed-phrase table (`GLOBAL_PHRASES`) and the same
  `fuzzy.best_match` primitive `command_grammar` uses. `docs/TASKS.md` names
  the set this milestone builds: "undo"/"redo" (wired to the existing
  `undo_highlight`/`redo_highlight`), "help"/"what can I say", and "go home".
  "close app" is deliberately absent — `docs/TASKS.md`'s 8.11 adds it once
  Milestone 8.1's close gatekeeper is what it wires into, as its own task
  rather than bundled in here. Global matching is confident-only: unlike
  `command_grammar.resolve`, there is no near-miss clarification tier for
  these, for the same reason 8.3 excluded `GOTO_PAGE` and the highlight
  triggers from its own — "did you mean 'go home'?" is a fair question only
  once there is a real ambiguity to have observed in practice, and these are
  short, low-collision phrases where guessing at one has not been shown to
  help.

Only `READING` has a scoped command set today (migrated from
`command_grammar`). `HOME` and `SETTINGS` gain their own in Milestone 8.5;
the dialog/picker/dictation contexts in 8.6-8.8. Until a context has one, it
hears nothing but the global commands — a screen with no voice commands of
its own must not still be listening for a different screen's grammar, which
is the accidental behavior this router replaces (before it, every recognizer
was built from the reading grammar regardless of which screen was open,
because there was only ever one grammar to reach for).
"""
from lector.features.voice import command_grammar, fuzzy

HOME = "home"
READING = "reading"
SETTINGS = "settings"
SAVE_DIALOG = "save_dialog"
OPEN_DIALOG = "open_dialog"
PICKER = "picker"
DICTATION = "dictation"

# Every context a page or dialog may declare itself as, via `Api.set_voice_context`.
# Extendable: 8.6-8.8 add screens that need no new entry here beyond what is
# already listed, since `docs/ARCHITECTURE.md` names the full set up front.
CONTEXTS: tuple[str, ...] = (
    HOME, READING, SETTINGS, SAVE_DIALOG, OPEN_DIALOG, PICKER, DICTATION,
)

# What a fresh `VoiceEngine`/`Api` should behave as before any page has made
# its own `set_voice_context` call — which includes every test and script
# that talks to `Api` without a frontend attached. `READING` rather than
# `HOME` on purpose: before this router existed, every screen got the full
# reading grammar because there was only one, so defaulting here to anything
# narrower would silently change what a caller who has not opted into
# contexts yet can say, which is exactly the "without changing its behavior"
# migration this milestone promises. `home.js` and (from Milestone 8.5)
# `settings.js` narrow themselves explicitly on load; nothing needs to narrow
# by default.
DEFAULT_CONTEXT = READING

# Intent names, exactly like `command_grammar`'s: they cross the bridge to JS
# as plain strings and are part of the frontend contract.
UNDO = "UNDO"
REDO = "REDO"
HELP = "HELP"
GO_HOME = "GO_HOME"

# Phrasings per global intent — the exact wording `docs/TASKS.md` and
# `docs/ARCHITECTURE.md` name for 8.4. Unlike `command_grammar.PHRASES`,
# these are deliberately single-phrase where the docs only ever name one
# wording: adding synonyms nobody asked for is scope the docs did not
# authorize, the same restraint `command_grammar.HIGHLIGHT_TRIGGERS` already
# applies to its own single trigger.
GLOBAL_PHRASES: dict[str, tuple[str, ...]] = {
    UNDO: ("undo",),
    REDO: ("redo",),
    HELP: ("help", "what can i say"),
    GO_HOME: ("go home",),
}

# Flattened once at import, mirroring `command_grammar`'s own
# `_ORDERED_PHRASES`/`_PHRASE_TO_INTENT` — built once since `resolve` runs
# per utterance.
_GLOBAL_ORDERED_PHRASES: tuple[str, ...] = tuple(
    phrasing for phrasings in GLOBAL_PHRASES.values() for phrasing in phrasings
)
_GLOBAL_PHRASE_TO_INTENT: dict[str, str] = {
    phrasing: intent
    for intent, phrasings in GLOBAL_PHRASES.items()
    for phrasing in phrasings
}


def _global_vocabulary() -> list[str]:
    words: set[str] = set()
    for phrasings in GLOBAL_PHRASES.values():
        for phrasing in phrasings:
            words.update(phrasing.split())
    return sorted(words)


GLOBAL_VOCABULARY: list[str] = _global_vocabulary()

# Per-context scoped vocabulary, on top of the globals every context gets.
# Only `READING` has one so far — see the module docstring for why the
# others are deliberately empty until 8.5-8.8 give them their own.
_CONTEXT_VOCABULARY: dict[str, list[str]] = {
    READING: command_grammar.VOCABULARY,
}


def vocabulary_for(context: str) -> list[str]:
    """Every word the recognizer needs to hear while `context` is active.

    `engine.py` pins its recognizer to exactly this list, the same way it
    already does with `command_grammar.VOCABULARY` — this is what makes a
    context change take effect: `Api.set_voice_context` calls this and hands
    the result to `VoiceEngine.set_vocabulary`.
    """
    scoped = _CONTEXT_VOCABULARY.get(context, [])
    return sorted(set(GLOBAL_VOCABULARY) | set(scoped))


def _match_global(text: str) -> dict | None:
    """`command_grammar.parse`'s shape, for the global fixed-phrase table."""
    # Reuses `command_grammar`'s own normalization rather than a second copy
    # of the same character-filtering rules, which could otherwise drift
    # from what the reading grammar treats as equivalent.
    words = command_grammar._normalize(text)
    if not words:
        return None
    phrase = " ".join(words)
    match = fuzzy.best_match(phrase, _GLOBAL_ORDERED_PHRASES)
    if match is None:
        return None
    matched, score = match
    return {
        "intent": _GLOBAL_PHRASE_TO_INTENT[matched],
        "phrase": phrase,
        "matched": matched,
        "score": score,
    }


def resolve(context: str, text: str, alternatives: list[str] | None = None) -> dict:
    """Interpret a recognized phrase for whichever `context` is active.

    Returns `{"command": ..., "clarify": ...}` — the same shape
    `command_grammar.resolve` returns, so `Api._on_voice_result` does not
    need to know which of the two produced it.

    Global commands are tried first, across `text` and every alternative,
    the same "give Vosk's other n-best hypotheses a chance" reasoning
    Milestone 8.3 already applies within `command_grammar.resolve` — a
    global command misheard as the top guess should not be lost because a
    context grammar happened to have its own opinion about the same
    alternative. Only once no global command confidently matches does a
    context with its own scoped grammar get a turn; today that is `READING`
    alone, delegated to unchanged, existing `command_grammar.resolve`.
    """
    for candidate in (text, *(alternatives or ())):
        command = _match_global(candidate)
        if command is not None:
            return {"command": command, "clarify": None}

    if context == READING:
        return command_grammar.resolve(text, alternatives)

    return {"command": None, "clarify": None}
