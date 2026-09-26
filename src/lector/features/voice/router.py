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

`READING`'s scoped set is migrated from `command_grammar`, unchanged.
`HOME` and `SETTINGS` gain their own fixed-phrase tables here in Milestone
8.5 — `HOME_PHRASES`/`SETTINGS_PHRASES`, matched the same way
`GLOBAL_PHRASES` is (a single `fuzzy.best_match` against that context's own
ordered phrase list, confident-only — no near-miss tier, for the same
"not enough observed ambiguity to justify guessing" reason global commands
have none). Each intent calls the exact function its equivalent click
handler already calls (`home.js`/`settings.js`), so voice never grows a
second copy of what a click does. `OPEN_DIALOG`/`SAVE_DIALOG` gain theirs in
Milestone 8.7, once the native OS file choosers they replace are gone —
`DIALOG_PICK` reads a spoken row number the same way `PICKER`'s numbers do
(reusing 8.6's number-parsing mechanic), plus `DIALOG_UP`/`CANCEL` and, for
`SAVE_DIALOG` only, `DIALOG_CONFIRM`. Until a context has one, it hears
nothing but the global commands — a screen with no voice commands of its own
must not still be listening for a different screen's grammar, which is the
accidental behavior this router replaces (before it, every recognizer was
built from the reading grammar regardless of which screen was open, because
there was only ever one grammar to reach for).

`DICTATION` (Milestone 8.8) is the one exception to "global commands are
matched before a context is consulted at all": while it is active, `resolve`
skips global matching entirely and tries only `DICTATION_PHRASES` ("done" to
confirm, "cancel"/"never mind" to abandon), falling through to
`{"command": None, ...}` for anything else. Every other context needs global
commands to win first so a misheard "undo" is not swallowed by a context
grammar that happens to share a word; `DICTATION` needs the opposite,
because its "anything else" is not silence but arbitrary dictated text a
reader is actively speaking, via open-vocabulary recognition
(`VoiceEngine.set_vocabulary(None)`, reached through
`Api._refresh_voice_vocabulary`'s own special case for this context). A
dictated sentence that happens to contain "help" or "go home" must reach the
caller as plain text, not get hijacked into a global command partway through
being spoken.
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
# Milestone 8.8: the one way into dictation mode, reachable from wherever the
# "What can I say?" panel's search field can be reached (today, only reading,
# via HELP) — a global command rather than a `READING`-scoped one so it does
# not need duplicating into `command_grammar.py`, which `READING` delegates
# to unchanged. It is a silent no-op anywhere the panel is not wired to open,
# the same way `HELP` already is on Home (docs/TASKS.md's build order has not
# reached wiring Home's own reference panel yet).
START_DICTATION = "START_DICTATION"

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
    START_DICTATION: ("start search",),
}

# Home context (Milestone 8.5): sidebar navigation and opening a PDF from
# Recent. "Which specific card" has no natural spoken label and is 8.6's
# numbered-overlay picker to solve (see docs/ARCHITECTURE.md); "the most
# recent one" does have one — "recent" already means "most recent" in
# `docs/PRD.md`'s own Recent-list language — so only that single card is
# reachable by voice until 8.6 lands.
OPEN_SETTINGS = "OPEN_SETTINGS"
OPEN_RECENT = "OPEN_RECENT"

HOME_PHRASES: dict[str, tuple[str, ...]] = {
    OPEN_SETTINGS: ("open settings",),
    OPEN_RECENT: ("open recent", "resume reading"),
}

# Settings context (Milestone 8.5): theme switching and the existing
# save/reopen preference toggles, per docs/TASKS.md. Three single-phrase
# theme intents rather than one intent with a parsed "which theme" argument
# (the way GOTO_PAGE parses a number) — three fixed words don't earn a
# second parsing mechanism when `command_grammar`'s fixed-phrase pattern
# already covers it exactly.
THEME_LIGHT = "THEME_LIGHT"
THEME_DARK = "THEME_DARK"
THEME_SEPIA = "THEME_SEPIA"
SAVE_COPY = "SAVE_COPY"
SAVE_OVERWRITE = "SAVE_OVERWRITE"
REOPEN_CONTINUE = "REOPEN_CONTINUE"
REOPEN_START = "REOPEN_START"

SETTINGS_PHRASES: dict[str, tuple[str, ...]] = {
    THEME_LIGHT: ("light theme",),
    THEME_DARK: ("dark theme",),
    THEME_SEPIA: ("sepia theme",),
    SAVE_COPY: ("save a copy",),
    SAVE_OVERWRITE: ("overwrite the original",),
    REOPEN_CONTINUE: ("continue where i left off",),
    REOPEN_START: ("start at the beginning",),
}

# Home context, continued (Milestone 8.6): the way into the numbered-overlay
# picker itself. Kept in HOME_PHRASES rather than a new table, the same way
# OPEN_SETTINGS/OPEN_RECENT already live there — Home is what currently has a
# list worth picking from; a future screen with its own list gains its own
# equivalent entry rather than this being generalized ahead of a second
# caller existing.
OPEN_PICKER = "OPEN_PICKER"
HOME_PHRASES[OPEN_PICKER] = ("pick a file", "choose a file")

# Picker context (Milestone 8.6): docs/ARCHITECTURE.md's "Numbered-overlay
# picker" — a list item with no natural single-word voice label (a specific
# Recent card, say) gets a numbered badge instead, and saying the number
# performs the same action a click on it would. The number itself is not a
# fixed phrase — `_parse_picker_number` below reads it as a spoken number the
# same way `command_grammar._parse_goto` does for GOTO_PAGE — so this table
# only needs the one fixed phrase that isn't a number: a way out without
# picking anything.
PICK = "PICK"
CANCEL = "CANCEL"

PICKER_PHRASES: dict[str, tuple[str, ...]] = {
    CANCEL: ("cancel", "never mind"),
}

# Every word `_parse_picker_number` can read as part of a spoken number,
# reusing `command_grammar`'s own number vocabulary (`_UNITS`/`_TENS`/
# `_HUNDRED`) rather than a second copy of it — Recent is capped at 10
# (`docs/DESIGN_SYSTEM.md`'s Miller's Law note), but nothing here hardcodes
# that cap, so a future, larger list is not silently unreachable past ten.
# Also reused as-is by `OPEN_DIALOG`/`SAVE_DIALOG` below, whose rows are
# numbered the same way and are not capped at 10 either.
_PICKER_NUMBER_WORDS: tuple[str, ...] = tuple(sorted(
    set(command_grammar._UNITS) | set(command_grammar._TENS)
    | {command_grammar._HUNDRED, command_grammar._FILLER}
))

# Open/Save-As dialog contexts (Milestone 8.7): docs/ARCHITECTURE.md's
# replacement for the native OS file choosers, which the router could not
# see or drive. Each row (a folder or a PDF file) is numbered the same way a
# Recent card is in `PICKER`, and `DIALOG_PICK` reads the spoken number the
# same way `PICK` does — the frontend decides what picking a given row does
# (open a file, navigate into a folder) since that is a property of the row,
# not something the grammar needs to know. `DIALOG_UP` and `CANCEL` are
# shared by both dialogs; `DIALOG_CONFIRM` ("save here") only makes sense for
# `SAVE_DIALOG` — there is nothing to confirm in `OPEN_DIALOG`, where picking
# a file row already opens it.
DIALOG_UP = "DIALOG_UP"
DIALOG_PICK = "DIALOG_PICK"
DIALOG_CONFIRM = "DIALOG_CONFIRM"

OPEN_DIALOG_PHRASES: dict[str, tuple[str, ...]] = {
    DIALOG_UP: ("go up", "up a folder", "back a folder", "parent folder"),
    CANCEL: ("cancel", "never mind"),
}

SAVE_DIALOG_PHRASES: dict[str, tuple[str, ...]] = {
    DIALOG_UP: ("go up", "up a folder", "back a folder", "parent folder"),
    CANCEL: ("cancel", "never mind"),
    DIALOG_CONFIRM: ("save here", "save it", "confirm save"),
}

# Dictation context (Milestone 8.8): docs/ARCHITECTURE.md's "Dictation mode"
# — the one free-text field that exists today, the command-reference panel's
# live search. `START_DICTATION` (above, global) pushes this context;
# `STOP_DICTATION` ("done") pops it back to a resting context and keeps
# whatever was dictated, the same way `CANCEL` pops it back and discards.
# Reuses `CANCEL` rather than a second exit intent — "cancel" already means
# "leave without keeping this" in `PICKER`/the file dialogs, and dictation's
# cancel means exactly the same thing. Deliberately just these two phrases:
# resolve() skips global matching while this context is active (see the
# module docstring), so anything else falls through as dictated text instead
# of being matched against a table at all.
STOP_DICTATION = "STOP_DICTATION"

DICTATION_PHRASES: dict[str, tuple[str, ...]] = {
    STOP_DICTATION: ("done",),
    CANCEL: ("cancel", "never mind"),
}


def _flatten_phrases(phrases: dict[str, tuple[str, ...]]) -> tuple[tuple[str, ...], dict[str, str]]:
    """Shared by every fixed-phrase table here (global, home, settings):
    the ordered candidate list `fuzzy.best_match` compares against, and the
    matched-phrasing → intent lookup, both built once at import since
    `resolve` runs per utterance."""
    ordered = tuple(phrasing for phrasings in phrases.values() for phrasing in phrasings)
    mapping = {
        phrasing: intent for intent, phrasings in phrases.items() for phrasing in phrasings
    }
    return ordered, mapping


_GLOBAL_ORDERED_PHRASES, _GLOBAL_PHRASE_TO_INTENT = _flatten_phrases(GLOBAL_PHRASES)
_HOME_ORDERED_PHRASES, _HOME_PHRASE_TO_INTENT = _flatten_phrases(HOME_PHRASES)
_SETTINGS_ORDERED_PHRASES, _SETTINGS_PHRASE_TO_INTENT = _flatten_phrases(SETTINGS_PHRASES)
_PICKER_ORDERED_PHRASES, _PICKER_PHRASE_TO_INTENT = _flatten_phrases(PICKER_PHRASES)
_OPEN_DIALOG_ORDERED_PHRASES, _OPEN_DIALOG_PHRASE_TO_INTENT = _flatten_phrases(OPEN_DIALOG_PHRASES)
_SAVE_DIALOG_ORDERED_PHRASES, _SAVE_DIALOG_PHRASE_TO_INTENT = _flatten_phrases(SAVE_DIALOG_PHRASES)
_DICTATION_ORDERED_PHRASES, _DICTATION_PHRASE_TO_INTENT = _flatten_phrases(DICTATION_PHRASES)

# Per-context lookup for `resolve`'s fixed-phrase contexts — every context
# except `READING` (which delegates to `command_grammar` instead) and
# `DICTATION` (handled by its own dedicated branch at the top of `resolve`,
# since unlike every other context it must *not* fall back to this table
# after failing to match — see the module docstring). `PICKER`, `OPEN_DIALOG`
# and `SAVE_DIALOG`'s tables only ever match their non-numeric phrases here —
# their row numbers are read by `_parse_picker_number`/`_parse_dialog_number`
# instead, tried first in `resolve`.
_CONTEXT_PHRASE_TABLES: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    HOME: (_HOME_ORDERED_PHRASES, _HOME_PHRASE_TO_INTENT),
    SETTINGS: (_SETTINGS_ORDERED_PHRASES, _SETTINGS_PHRASE_TO_INTENT),
    PICKER: (_PICKER_ORDERED_PHRASES, _PICKER_PHRASE_TO_INTENT),
    OPEN_DIALOG: (_OPEN_DIALOG_ORDERED_PHRASES, _OPEN_DIALOG_PHRASE_TO_INTENT),
    SAVE_DIALOG: (_SAVE_DIALOG_ORDERED_PHRASES, _SAVE_DIALOG_PHRASE_TO_INTENT),
}


def _vocabulary_from_phrases(phrases: dict[str, tuple[str, ...]]) -> list[str]:
    words: set[str] = set()
    for phrasings in phrases.values():
        for phrasing in phrasings:
            words.update(phrasing.split())
    return sorted(words)


GLOBAL_VOCABULARY: list[str] = _vocabulary_from_phrases(GLOBAL_PHRASES)

# Per-context scoped vocabulary, on top of the globals every context gets.
# `DICTATION`'s entry here is never actually handed to the recognizer — Api.
# _refresh_voice_vocabulary special-cases this context to run Vosk
# open-vocabulary instead (`VoiceEngine.set_vocabulary(None)`) — but it is
# still filled in below rather than left absent, so `vocabulary_for` and
# `resolve` agree on what this context's fixed phrases are, and so a future
# caller of `vocabulary_for` that is not `Api` does not silently get an empty
# scoped set for a context that does, in fact, have one.
_CONTEXT_VOCABULARY: dict[str, list[str]] = {
    READING: command_grammar.VOCABULARY,
    HOME: _vocabulary_from_phrases(HOME_PHRASES),
    SETTINGS: _vocabulary_from_phrases(SETTINGS_PHRASES),
    PICKER: sorted(set(_vocabulary_from_phrases(PICKER_PHRASES)) | set(_PICKER_NUMBER_WORDS)),
    OPEN_DIALOG: sorted(set(_vocabulary_from_phrases(OPEN_DIALOG_PHRASES)) | set(_PICKER_NUMBER_WORDS)),
    SAVE_DIALOG: sorted(set(_vocabulary_from_phrases(SAVE_DIALOG_PHRASES)) | set(_PICKER_NUMBER_WORDS)),
    DICTATION: _vocabulary_from_phrases(DICTATION_PHRASES),
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


def _match_phrases(
    text: str, ordered_phrases: tuple[str, ...], phrase_to_intent: dict[str, str]
) -> dict | None:
    """`command_grammar.parse`'s shape, for any fixed-phrase table here."""
    # Reuses `command_grammar`'s own normalization rather than a second copy
    # of the same character-filtering rules, which could otherwise drift
    # from what the reading grammar treats as equivalent.
    words = command_grammar._normalize(text)
    if not words:
        return None
    phrase = " ".join(words)
    match = fuzzy.best_match(phrase, ordered_phrases)
    if match is None:
        return None
    matched, score = match
    return {
        "intent": phrase_to_intent[matched],
        "phrase": phrase,
        "matched": matched,
        "score": score,
    }


def _match_global(text: str) -> dict | None:
    return _match_phrases(text, _GLOBAL_ORDERED_PHRASES, _GLOBAL_PHRASE_TO_INTENT)


def _parse_spoken_number(text: str) -> tuple[int, str] | None:
    """`(index, normalized phrase)` if `text` is nothing but a bare spoken
    number ("three", "twenty one", "3") — the shared reading shared by
    `_parse_picker_number` and `_parse_dialog_number` below, since a numbered
    row means the same thing (badge/row `N`) whether it is `PICKER`'s or a
    file dialog's. Reuses `command_grammar`'s own normalization/number-
    reading helpers rather than a second copy of them.
    """
    words = command_grammar._normalize(text)
    # "and" is swept in alongside the digits, the same as `_parse_goto`: "one
    # hundred and five" is one spoken number, and rejecting it here over a
    # filler word would make a page count above a hundred unreachable.
    if not words or not all(
        command_grammar._is_number_word(w) or w == command_grammar._FILLER for w in words
    ):
        return None
    index = command_grammar._words_to_number(words)
    if index is None or index < 1:
        return None
    return index, " ".join(words)


def _parse_picker_number(text: str) -> dict | None:
    """A bare spoken number selects that `PICKER` badge.

    Unlike `command_grammar._parse_goto`, no lead phrase is required before
    the number: `GOTO_PAGE` treats a bare number cautiously because it could
    be heard anywhere, mid-conversation, but `picker` is only ever active
    while numbered badges are already on screen (`docs/ARCHITECTURE.md`'s
    "Numbered-overlay picker"), so a bare number here is unambiguous — it can
    only mean "pick that one."
    """
    parsed = _parse_spoken_number(text)
    if parsed is None:
        return None
    index, phrase = parsed
    return {"intent": PICK, "index": index, "phrase": phrase, "matched": "<number>", "score": 1.0}


def _parse_dialog_number(text: str) -> dict | None:
    """A bare spoken number selects that numbered row in `OPEN_DIALOG`/
    `SAVE_DIALOG` — the same reasoning as `_parse_picker_number`: the dialogs
    only number their rows while open, so a bare number is unambiguous."""
    parsed = _parse_spoken_number(text)
    if parsed is None:
        return None
    index, phrase = parsed
    return {
        "intent": DIALOG_PICK,
        "index": index,
        "phrase": phrase,
        "matched": "<number>",
        "score": 1.0,
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
    context with its own scoped grammar get a turn: `READING` delegates to
    unchanged, existing `command_grammar.resolve` (with its own near-miss
    clarification tier); `HOME` and `SETTINGS` (Milestone 8.5) try their own
    fixed-phrase table the same confident-only way global commands are
    matched — no clarification tier, for the reason `GLOBAL_PHRASES`
    doesn't have one either. `PICKER` (Milestone 8.6) additionally tries
    `_parse_picker_number` before its own fixed-phrase table, so a spoken
    number picks a badge and "cancel"/"never mind" still falls through to
    `PICKER_PHRASES`. `OPEN_DIALOG`/`SAVE_DIALOG` (Milestone 8.7) do the same
    with `_parse_dialog_number` before their own tables.

    `DICTATION` (Milestone 8.8) is handled before any of the above, and does
    not fall through to it: global matching is skipped entirely, only
    `DICTATION_PHRASES` is tried, and failing to match that returns
    `command: None` rather than continuing on to look for anything else —
    the caller (`Api._on_voice_result`) treats that `None` together with the
    original `text` as dictated content, not as "nothing was understood".
    """
    if context == DICTATION:
        for candidate in (text, *(alternatives or ())):
            command = _match_phrases(candidate, _DICTATION_ORDERED_PHRASES, _DICTATION_PHRASE_TO_INTENT)
            if command is not None:
                return {"command": command, "clarify": None}
        return {"command": None, "clarify": None}

    for candidate in (text, *(alternatives or ())):
        command = _match_global(candidate)
        if command is not None:
            return {"command": command, "clarify": None}

    if context == READING:
        return command_grammar.resolve(text, alternatives)

    if context == PICKER:
        for candidate in (text, *(alternatives or ())):
            command = _parse_picker_number(candidate)
            if command is not None:
                return {"command": command, "clarify": None}

    if context in (OPEN_DIALOG, SAVE_DIALOG):
        for candidate in (text, *(alternatives or ())):
            command = _parse_dialog_number(candidate)
            if command is not None:
                return {"command": command, "clarify": None}

    table = _CONTEXT_PHRASE_TABLES.get(context)
    if table is not None:
        ordered_phrases, phrase_to_intent = table
        for candidate in (text, *(alternatives or ())):
            command = _match_phrases(candidate, ordered_phrases, phrase_to_intent)
            if command is not None:
                return {"command": command, "clarify": None}

    return {"command": None, "clarify": None}
