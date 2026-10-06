"""What the "What can I say?" list shows (docs/PRD.md's discoverability
requirement, built in Milestone 8, made one shared list in Milestone 8.13).

The point of this module is that the list cannot lie. Voice recognition here is
a closed grammar, not language understanding: only the phrases in
`command_grammar`'s and `router`'s tables are recognized, so the list says so,
and it is built from those tables rather than retyped beside them.

* **Every phrase, every alternate.** A command's card shows every phrase the
  recognizer accepts for it, not a sample. A list that shows two of the six ways
  to say "next page" would make the other four look like they don't work.
* **Nothing that doesn't work.** Phrases are generated from the tables, never
  typed here, and `tests/test_command_reference.py` resolves each one through
  `router.resolve` in the contexts the card names. A phrase with a variable
  (a page number, a row number, the words to highlight) is a pattern with a
  placeholder (`{page}`, `{number}`, `{words}`) that the page fills with the real
  range when it knows it, so the list shows "open number 1–12", not one
  invented example.
* **One list for the whole app.** Every screen shows the same sections from the
  same payload; a screen differs only in which sections come first
  (`panel(context)` puts that screen's own first, then the rest in
  `_SECTION_ORDER`). A section that does not apply on the current screen says
  where it does, so the list stays complete without advertising a command the
  screen cannot act on.
* **Keyboard and mouse equivalents sit beside each entry.** The list's own
  promise is that "every command also has a button or shortcut", which is
  `docs/PRD.md`'s parity requirement restated as something the reader can check.

`tests/test_command_reference.py` also fails if a phrase in any router or
grammar table is missing from the list, so adding a command without listing it
is a test failure rather than a quiet gap.

`try_saying` (the "TRY SAYING" chips on Home's empty state) is built from the
same router tables.
"""
import re

from lector.features.voice import command_grammar as grammar
from lector.features.voice import router, wake

# Placeholders in a phrase. The page fills them in (command-reference.js):
# `{page}` and `{number}` with "1–N" (or the real range when the screen knows
# it), `{words}` with a short description of what to say.
PAGE = "{page}"
NUMBER = "{number}"
WORDS = "{words}"

_HOME = (router.HOME,)
_SETTINGS = (router.SETTINGS,)
_READING = (router.READING,)
_SCREENS = (router.HOME, router.SETTINGS, router.READING)
_PROMPTS = (
    router.PICKER, router.OPEN_DIALOG, router.SAVE_DIALOG,
    router.REMOVE_CONFIRM, router.SAVE_CONFIRM, router.OVERWRITE_CONFIRM,
)


def _display(phrase: str) -> str:
    """How a phrase is written for a reader. The grammar is lowercase because
    recognition is; "pdf" is an initialism and "i" is a pronoun, and
    `router.resolve` is case-insensitive, so the nicer form resolves exactly
    the same."""
    return re.sub(r"\bi\b", "I", re.sub(r"\bpdf\b", "PDF", phrase))


def _all(phrases: dict[str, tuple[str, ...]], intent: str) -> list[str]:
    """Every phrasing of `intent` in a `router`/`command_grammar` table."""
    return [_display(p) for p in phrases[intent]]


def _then(prefixes: tuple[str, ...], placeholder: str) -> list[str]:
    """A pattern per spoken lead: "go to page {page}", "page {page}", ..."""
    return [f"{_display(p)} {placeholder}" for p in prefixes]


def _command(
    phrases: list[str], description: str, equivalent: str, contexts: tuple[str, ...]
) -> dict:
    return {
        "phrases": phrases,
        "description": description,
        "equivalent": equivalent,
        "contexts": list(contexts),
    }


def _section(
    id_: str, title: str, scope_note: str, commands: list[dict], *, everywhere: bool = False
) -> dict:
    contexts = sorted({c for command in commands for c in command["contexts"]})
    return {
        "id": id_,
        "title": title,
        "contexts": contexts,
        # True for the commands that are the same on every screen they exist on:
        # they are not "this screen's own", so they never jump to the top.
        "everywhere": everywhere,
        "scope_note": scope_note,
        "commands": commands,
    }


def _anywhere() -> dict:
    g = router.GLOBAL_PHRASES
    return _section(
        "anywhere",
        "Anywhere in the app",
        "Works on Recent, Settings and the reader.",
        [
            _command(_all(g, router.OPEN_PDF), "Opens the Open PDF dialog.",
                     "Press Ctrl+O, or click Open PDF on the Home screen", _SCREENS),
            _command(_all(g, router.GO_RECENT), "Goes to your recent files.",
                     "Click Recent in the sidebar", _SCREENS),
            _command(_all(g, router.OPEN_SETTINGS), "Opens Settings.",
                     "Click Settings in the sidebar", _SCREENS),
            _command(_all(g, router.HELP), "Shows this list.",
                     'Click "What can I say?"', _SCREENS),
            _command(_all(g, router.CLOSE_APP),
                     "Quits Lector, asking about unsaved highlights first.",
                     "Click the window's close button, or Alt+F4", _SCREENS),
        ],
        everywhere=True,
    )


def _back_home() -> dict:
    return _section(
        "back_home",
        "Back to Home",
        "Works in Settings and the reader.",
        [
            _command(_all(router.GLOBAL_PHRASES, router.GO_HOME),
                     "Goes back to your library from Settings or the reader, asking about "
                     "unsaved highlights first.",
                     "Click Recent in the sidebar, or Library in the reader",
                     (router.SETTINGS, router.READING)),
        ],
        everywhere=True,
    )


def _recent() -> dict:
    h = router.HOME_PHRASES
    picker = _display(h[router.OPEN_PICKER][0])
    return _section(
        "recent",
        "Recent files",
        "Works on Recent and Favorites.",
        [
            _command(_all(h, router.OPEN_RECENT), "Opens the most recently opened file.",
                     "Click the first card in Recent", _HOME),
            _command(_all(h, router.OPEN_PICKER),
                     "Numbers every card so you can say which one to open.",
                     "Click any card in Recent", _HOME),
            _command(_then(router.OPEN_NUMBER_PREFIXES, NUMBER),
                     "Opens that numbered file, counting in the order shown. Say "
                     f"\"{picker}\" first to see the numbers.",
                     "Click the file", _HOME),
            _command(_all(h, router.REMOVE_RECENT),
                     "Removes the most recent file from Recent, after confirming — "
                     "the file on disk is never touched.",
                     "Hover the first card and click its remove icon", _HOME),
            _command(_all(h, router.REMOVE_PICKER),
                     "Numbers every card so you can say which one to remove.",
                     "Hover a card and click its remove icon", _HOME),
        ],
    )


def _favorites() -> dict:
    h = router.HOME_PHRASES
    return _section(
        "favorites",
        "Favorites",
        "Works on Recent and Favorites.",
        [
            _command(_all(h, router.OPEN_FAVORITES), "Shows your favorite files.",
                     "Click Favorites in the sidebar", _HOME),
            _command(_all(h, router.FAVORITE_RECENT),
                     "Adds the most recent file to your favorites, or removes it if it "
                     "already is one.",
                     "Click the star on the first file in Recent", _HOME),
            _command(_all(h, router.FAVORITE_PICKER),
                     "Numbers every file so you can say which one to add to or remove "
                     "from your favorites.",
                     "Click the star on any file", _HOME),
        ],
    )


def _settings_sections() -> list[dict]:
    s = router.SETTINGS_PHRASES
    scope = "Works in Settings."
    return [
        _section("theme", "Theme", scope, [
            _command(_all(s, router.THEME_LIGHT), "Switches to the light theme.",
                     "Click the Light theme card", _SETTINGS),
            _command(_all(s, router.THEME_DARK), "Switches to the dark theme.",
                     "Click the Dark theme card", _SETTINGS),
            _command(_all(s, router.THEME_SEPIA), "Switches to the sepia theme.",
                     "Click the Sepia theme card", _SETTINGS),
        ]),
        _section("save", "When I save highlights", scope, [
            _command(_all(s, router.SAVE_COPY),
                     'Sets "save a copy" as the default for saving highlights.',
                     'Click "Save a copy"', _SETTINGS),
            _command(_all(s, router.SAVE_OVERWRITE),
                     'Sets "overwrite the original" as the default, after you say '
                     f'"{_display(router.OVERWRITE_CONFIRM_PHRASES[router.CONFIRM_OVERWRITE][0])}" '
                     "— it rewrites your own PDFs, so it asks first.",
                     'Click "Overwrite the original"', _SETTINGS),
        ]),
        _section("reopen", "When I reopen a PDF", scope, [
            _command(_all(s, router.REOPEN_CONTINUE),
                     "Sets reopening a PDF from Recent to continue where you left off.",
                     'Click "Continue where I left off"', _SETTINGS),
            _command(_all(s, router.REOPEN_START),
                     "Sets reopening a PDF from Recent to always start at page 1.",
                     'Click "Always start at the beginning"', _SETTINGS),
        ]),
    ]


def _reading_sections() -> list[dict]:
    p = grammar.PHRASES
    g = router.GLOBAL_PHRASES
    scope = "Works in the reader."
    return [
        _section("moving", "Moving around", scope, [
            _command(_all(p, grammar.NEXT_PAGE), "One page forward.", "→ or Page Down", _READING),
            _command(_all(p, grammar.PREV_PAGE), "One page back.", "← or Page Up", _READING),
            _command(_then(grammar.GOTO_PREFIXES, PAGE),
                     "Jumps straight to that page. Say the number however you like — "
                     "\"twelve\" and \"one hundred and five\" both work.",
                     "Type the page number in the toolbar", _READING),
            _command(_all(p, grammar.SCROLL_DOWN),
                     "Nudges down most of a screen, keeping the line you were on in view.",
                     "↓", _READING),
            _command(_all(p, grammar.SCROLL_UP), "The same, upwards.", "↑", _READING),
        ]),
        _section("highlighting", "Highlighting", scope, [
            _command(_then(grammar.HIGHLIGHT_TRIGGERS, WORDS),
                     "Say a few words you can see on screen and they are highlighted. "
                     "Only what is actually visible is searched, so the same phrase "
                     "elsewhere in the document is never what gets marked.",
                     "Press H, or drag across the text", _READING),
            _command(_then(grammar.HIGHLIGHT_SENTENCE_TRIGGERS, WORDS),
                     "The same, extended out to the sentence those words sit in.",
                     "Drag across the whole sentence", _READING),
        ]),
        _section("undo_redo", "Undo and redo", scope, [
            _command(_all(g, router.UNDO), "Takes back the last highlight change.",
                     "Press Ctrl+Z, or click Undo in the toolbar", _READING),
            _command(_all(g, router.REDO), "Puts back what you just took back.",
                     "Press Ctrl+Shift+Z or Ctrl+Y, or click Redo in the toolbar", _READING),
        ]),
        _section("app", "The app itself", scope, [
            _command(_all(p, grammar.SAVE),
                     "Asks how to save — a copy, or over the original. Nothing is "
                     "written to your file until you say so.",
                     "Ctrl+S", _READING),
        ]),
    ]


def _this_list() -> dict:
    g = router.GLOBAL_PHRASES
    d = router.DICTATION_PHRASES
    return _section(
        "this_list",
        "In this list",
        "Works while this list is open.",
        [
            _command(_all(router.REFERENCE_PHRASES, router.CANCEL),
                     "Closes this list.", "Click Got it or the ×, or press Esc",
                     (router.REFERENCE,)),
            _command(_all(g, router.START_DICTATION),
                     "Starts dictating into the search box: say the words to look for.",
                     "Click the search box and type", (router.REFERENCE,)),
            _command(_all(d, router.STOP_DICTATION),
                     "Stops dictating and keeps what was heard in the search box.",
                     "Keep typing in the search box", (router.DICTATION,)),
            _command(_all(d, router.CANCEL),
                     "Stops dictating and puts back what the search box held before.",
                     "Select the text and delete it", (router.DICTATION,)),
        ],
    )


def _prompts() -> dict:
    o = router.OPEN_DIALOG_PHRASES
    dialogs = (router.OPEN_DIALOG, router.SAVE_DIALOG)
    quick = [
        _command(_all(o, intent), f"Goes to your {label} folder.", f"Click {label}", dialogs)
        for intent, label in (
            (router.DIALOG_HOME, "Home"), (router.DIALOG_DESKTOP, "Desktop"),
            (router.DIALOG_DOCUMENTS, "Documents"), (router.DIALOG_DOWNLOADS, "Downloads"),
        )
    ]
    return _section(
        "prompts",
        "In dialogs and numbered lists",
        "Works only while a dialog or a numbered list is showing.",
        [
            _command([NUMBER],
                     "Picks that numbered item — a card after \"pick a file\", or a row in the "
                     "Open PDF and Save a copy dialogs.",
                     "Click the item", (router.PICKER, *dialogs)),
            _command(_all(o, router.CANCEL),
                     "Leaves the dialog or numbered list without doing anything.",
                     "Click Cancel, or press Esc", _PROMPTS),
            _command(_all(o, router.DIALOG_UP), "Goes up to the parent folder.",
                     "Click the parent folder in the path above the list", dialogs),
            *quick,
            _command(_all(router.SAVE_DIALOG_PHRASES, router.DIALOG_CONFIRM),
                     "Saves the copy in the folder you are looking at.",
                     "Click Save", (router.SAVE_DIALOG,)),
            _command(_all(router.REMOVE_CONFIRM_PHRASES, router.CONFIRM_REMOVE),
                     "Confirms removing the file from Recent.", "Click Remove",
                     (router.REMOVE_CONFIRM,)),
            _command([*_all(router.SAVE_CONFIRM_PHRASES, router.SAVE_COPY),
                      *_all(router.SAVE_CONFIRM_PHRASES, router.SAVE_OVERWRITE)],
                     "In the Save changes dialog, picks that way of saving.",
                     "Click the option, then Save", (router.SAVE_CONFIRM,)),
            _command(_all(router.SAVE_CONFIRM_PHRASES, router.DONT_SAVE),
                     "In the Save changes dialog, leaves without saving.",
                     "Click Don't Save", (router.SAVE_CONFIRM,)),
            _command(_all(router.OVERWRITE_CONFIRM_PHRASES, router.CONFIRM_OVERWRITE),
                     "Confirms making \"overwrite the original\" the default.",
                     "Click Overwrite the original", (router.OVERWRITE_CONFIRM,)),
        ],
    )


# The one fixed order, everywhere. A screen's own sections are moved to the
# front of it (see `sections`); the rest keep this order.
_SECTION_ORDER: tuple[str, ...] = (
    "anywhere", "back_home", "recent", "favorites", "theme", "save", "reopen",
    "moving", "highlighting", "undo_redo", "app", "this_list", "prompts",
)


def _all_sections() -> list[dict]:
    built = [
        _anywhere(), _back_home(), _recent(), _favorites(),
        *_settings_sections(), *_reading_sections(), _this_list(), _prompts(),
    ]
    by_id = {section["id"]: section for section in built}
    assert set(by_id) == set(_SECTION_ORDER), "a section is missing from _SECTION_ORDER"
    return [by_id[id_] for id_ in _SECTION_ORDER]


def sections(context: str = router.READING) -> list[dict]:
    """Every section, for `context`'s screen: its own first, then the rest in the
    one fixed order. Each carries `available` (does this section's commands work
    on this screen) so the page can say where the others do.

    Built per call rather than frozen at import so a change to the grammar is
    reflected without a restart — this is read once when the list opens, so the
    cost is irrelevant next to the drift it prevents.
    """
    screen = context if context in _SCREENS else router.READING
    built = _all_sections()
    for section in built:
        section["available"] = screen in section["contexts"]
    own = [s for s in built if s["available"] and not s["everywhere"]]
    rest = [s for s in built if s not in own]
    return [*own, *rest]


def commands(context: str = router.READING) -> list[dict]:
    """Every command card in `sections(context)`, flattened."""
    return [command for section in sections(context) for command in section["commands"]]


# Which global commands the empty-state "TRY SAYING" chips suggest, per
# context, in display order. Only the first phrasing of each is shown.
_TRY_SAYING: dict[str, tuple[str, ...]] = {
    router.HOME: (router.OPEN_PDF, router.HELP, router.OPEN_SETTINGS),
}


def try_saying(context: str) -> list[str]:
    """Phrases worth suggesting to a reader who has nothing open yet.

    Read from `router.GLOBAL_PHRASES` rather than typed beside the UI, so a
    chip can only ever be a phrase the router accepts. `[]` for a context with
    no suggestions, and the frontend then shows no "TRY SAYING" row at all.
    """
    return [_display(router.GLOBAL_PHRASES[intent][0]) for intent in _TRY_SAYING.get(context, ())]


def panel(context: str = router.READING) -> dict:
    """Everything the list needs, including how to start talking at all.

    `context` is whichever `router` context is active when the list opens
    (`Api.get_command_reference` passes its own `self._voice_context`), which
    decides only the order and which sections say "works elsewhere".

    The activation line is part of the payload rather than hardcoded in the
    frontend because it names the wake phrase, and the phrase has exactly one
    home (`wake.py`).
    """
    return {
        "context": context if context in _SCREENS else router.READING,
        "sections": sections(context),
        "try_saying": try_saying(context),
        "wake_phrase": wake.WAKE_PHRASE,
        "wake_phrase_display": wake.WAKE_PHRASE_DISPLAY,
        "push_to_talk_key": "Space",
    }
