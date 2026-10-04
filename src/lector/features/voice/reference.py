"""What the "What can I say?" panel shows (docs/PRD.md's discoverability
requirement, built in Milestone 8).

The point of this module is that the panel cannot lie. Every example phrase
below is taken from `command_grammar`'s or `router`'s own phrasings rather
than retyped beside them, so a command the reference offers is by
construction a command the grammar accepts — a list of suggestions that has
quietly drifted out of date is worse for a reader guessing at phrasing than
no list at all.

Two things are deliberate about the contents:

* **Only commands that exist.** `docs/PRD.md` names four categories —
  Moving around / Highlighting / Finding words / The app itself — but
  in-document search has not been built, so there is nothing truthful to put
  under "Finding words" and the category is absent rather than present and
  empty. It returns when search does.

* **Keyboard and mouse equivalents sit beside each entry.** The panel's own
  promise in the mockup is that "every command here also has a button or
  shortcut", which is `docs/PRD.md`'s parity requirement restated as
  something the reader can check. Showing the equivalent is what makes it
  checkable, and it doubles as the answer for a reader who tried voice,
  found it unreliable, and wants to know what to press instead.

Milestone 8.10 scopes both `categories()` and `panel()` to whichever
`router` context is active, rather than always building the reading-view
grammar's categories regardless of where the reader actually is: `HOME` and
`SETTINGS` now have their own scoped command tables (Milestone 8.5), so a
panel that still only ever showed "Moving around"/"Highlighting" would be
advertising commands that context cannot act on. `READING`, and any context
without its own categories yet (the dialog/picker/dictation contexts, none
of which wire "help" to open this panel today), fall back to the reading
categories — the same content this module has always returned, kept as the
default so every existing caller's behavior is unchanged.
"""
from lector.features.voice import command_grammar as grammar
from lector.features.voice import router, wake

# How many phrasings per command the panel offers. The grammar accepts more
# (several synonyms per intent, most-explicit first), but a reference that
# lists all of them reads as a specification to memorize, which is the exact
# failure the panel exists to avoid: the reader needs one phrase that works,
# plus enough of a second to see that the wording is forgiving.
_EXAMPLES_SHOWN = 2


def _examples(intent: str) -> list[str]:
    return list(grammar.PHRASES[intent][:_EXAMPLES_SHOWN])


def _examples_from(phrases: dict[str, tuple[str, ...]], intent: str) -> list[str]:
    """Same as `_examples`, for a `router` phrase table instead of
    `command_grammar.PHRASES` — `HOME`/`SETTINGS`'s commands live there, not
    in the reading grammar."""
    return list(phrases[intent][:_EXAMPLES_SHOWN])


def _command(examples: list[str], description: str, equivalent: str) -> dict:
    return {"examples": examples, "description": description, "equivalent": equivalent}


def categories(context: str = router.READING) -> list[dict]:
    """The panel's contents for `context`: `[{"title", "commands": [...]}, ...]`.

    Built per call rather than frozen at import so a change to the grammar is
    reflected without a restart — this is read once when a panel opens, so
    the cost is irrelevant next to the drift it prevents.
    """
    if context == router.HOME:
        return _home_categories()
    if context == router.SETTINGS:
        return _settings_categories()
    return _reading_categories()


def _home_categories() -> list[dict]:
    return [
        {
            "title": "Recent files",
            "commands": [
                _command(
                    _examples_from(router.HOME_PHRASES, router.OPEN_RECENT),
                    "Opens the most recently opened file.",
                    "Click the first card in Recent",
                ),
                _command(
                    _examples_from(router.HOME_PHRASES, router.OPEN_PICKER),
                    "Numbers every card in Recent so you can say which one to open.",
                    "Click any card in Recent",
                ),
                _command(
                    _examples_from(router.HOME_PHRASES, router.REMOVE_RECENT),
                    "Removes the most recent file from Recent, after confirming — "
                    "the file on disk is never touched.",
                    "Hover the first card and click its remove icon",
                ),
                _command(
                    _examples_from(router.HOME_PHRASES, router.REMOVE_PICKER),
                    "Numbers every card in Recent so you can say which one to remove.",
                    "Hover a card and click its remove icon",
                ),
            ],
        },
        {
            "title": "Favorites",
            "commands": [
                _command(
                    _examples_from(router.HOME_PHRASES, router.OPEN_FAVORITES),
                    "Shows your favorite files.",
                    "Click Favorites in the sidebar",
                ),
                _command(
                    _examples_from(router.HOME_PHRASES, router.SHOW_RECENT),
                    "Goes back to your recent files.",
                    "Click Recent in the sidebar",
                ),
                _command(
                    _examples_from(router.HOME_PHRASES, router.FAVORITE_RECENT),
                    "Adds the most recent file to your favorites, or removes it if it "
                    "already is one.",
                    "Click the star on the first file in Recent",
                ),
                _command(
                    _examples_from(router.HOME_PHRASES, router.FAVORITE_PICKER),
                    "Numbers every file so you can say which one to add to or remove "
                    "from your favorites.",
                    "Click the star on any file",
                ),
            ],
        },
        {
            "title": "The app itself",
            "commands": [
                _command(
                    _examples_from(router.HOME_PHRASES, router.OPEN_SETTINGS),
                    "Opens Settings.",
                    "Click Settings in the sidebar",
                ),
            ],
        },
    ]


def _settings_categories() -> list[dict]:
    return [
        {
            "title": "Theme",
            "commands": [
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.THEME_LIGHT),
                    "Switches to the light theme.",
                    "Click the Light theme card",
                ),
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.THEME_DARK),
                    "Switches to the dark theme.",
                    "Click the Dark theme card",
                ),
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.THEME_SEPIA),
                    "Switches to the sepia theme.",
                    "Click the Sepia theme card",
                ),
            ],
        },
        {
            "title": "When I save highlights",
            "commands": [
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.SAVE_COPY),
                    'Sets "save a copy" as the default for saving highlights.',
                    'Click "Save a copy"',
                ),
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.SAVE_OVERWRITE),
                    'Sets "overwrite the original" as the default.',
                    'Click "Overwrite the original"',
                ),
            ],
        },
        {
            "title": "When I reopen a PDF",
            "commands": [
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.REOPEN_CONTINUE),
                    "Sets reopening a PDF from Recent to continue where you left off.",
                    'Click "Continue where I left off"',
                ),
                _command(
                    _examples_from(router.SETTINGS_PHRASES, router.REOPEN_START),
                    "Sets reopening a PDF from Recent to always start at page 1.",
                    'Click "Always start at the beginning"',
                ),
            ],
        },
    ]


def _reading_categories() -> list[dict]:
    return [
        {
            "title": "Moving around",
            "commands": [
                _command(
                    _examples(grammar.NEXT_PAGE),
                    "One page forward.",
                    "→ or Page Down",
                ),
                _command(
                    _examples(grammar.PREV_PAGE),
                    "One page back.",
                    "← or Page Up",
                ),
                _command(
                    [f"{grammar.GOTO_PREFIXES[0]} 12", f"{grammar.GOTO_PREFIXES[1]} 12"],
                    "Jumps straight to that page. Say the number however you like — "
                    "\"twelve\" and \"one hundred and five\" both work.",
                    "Type the page number in the toolbar",
                ),
                _command(
                    _examples(grammar.SCROLL_DOWN),
                    "Nudges down most of a screen, keeping the line you were on in view.",
                    "↓",
                ),
                _command(
                    _examples(grammar.SCROLL_UP),
                    "The same, upwards.",
                    "↑",
                ),
            ],
        },
        {
            "title": "Highlighting",
            "commands": [
                _command(
                    [f"{trigger} the quick brown fox"
                     for trigger in grammar.HIGHLIGHT_TRIGGERS],
                    "Say a few words you can see on screen and they are highlighted. "
                    "Only what is actually visible is searched, so the same phrase "
                    "elsewhere in the document is never what gets marked.",
                    "Press H, or drag across the text",
                ),
                _command(
                    [f"{grammar.HIGHLIGHT_SENTENCE_TRIGGERS[0]} about foxes"],
                    "The same, extended out to the sentence those words sit in.",
                    "Drag across the whole sentence",
                ),
            ],
        },
        {
            "title": "The app itself",
            "commands": [
                _command(
                    _examples(grammar.SAVE),
                    "Asks how to save — a copy, or over the original. Nothing is "
                    "written to your file until you say so.",
                    "Ctrl+S",
                ),
            ],
        },
    ]


def panel(context: str = router.READING) -> dict:
    """Everything the panel needs, including how to start talking at all.

    `context` is whichever `router` context is active when the panel opens
    (Milestone 8.10) — `Api.get_command_reference` passes its own
    `self._voice_context` — so the categories shown match what that context
    can actually act on rather than always the reading grammar's.

    The activation line is part of the payload rather than hardcoded in the
    frontend because it names the wake phrase, and the phrase has exactly one
    home (`wake.py`).
    """
    return {
        "categories": categories(context),
        "wake_phrase": wake.WAKE_PHRASE,
        "wake_phrase_display": wake.WAKE_PHRASE_DISPLAY,
        "push_to_talk_key": "Space",
    }
