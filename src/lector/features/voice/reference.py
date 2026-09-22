"""What the "What can I say?" panel shows (docs/PRD.md's discoverability
requirement, built in Milestone 8).

The point of this module is that the panel cannot lie. Every example phrase
below is taken from `command_grammar`'s own phrasings rather than retyped
beside them, so a command the reference offers is by construction a command
the grammar accepts — a list of suggestions that has quietly drifted out of
date is worse for a reader guessing at phrasing than no list at all.

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
"""
from lector.features.voice import command_grammar as grammar
from lector.features.voice import wake

# How many phrasings per command the panel offers. The grammar accepts more
# (several synonyms per intent, most-explicit first), but a reference that
# lists all of them reads as a specification to memorize, which is the exact
# failure the panel exists to avoid: the reader needs one phrase that works,
# plus enough of a second to see that the wording is forgiving.
_EXAMPLES_SHOWN = 2


def _examples(intent: str) -> list[str]:
    return list(grammar.PHRASES[intent][:_EXAMPLES_SHOWN])


def _command(examples: list[str], description: str, equivalent: str) -> dict:
    return {"examples": examples, "description": description, "equivalent": equivalent}


def categories() -> list[dict]:
    """The panel's contents: `[{"title", "commands": [...]}, ...]`.

    Built per call rather than frozen at import so a change to the grammar is
    reflected without a restart — this is read once when a panel opens, so
    the cost is irrelevant next to the drift it prevents.
    """
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


def panel() -> dict:
    """Everything the panel needs, including how to start talking at all.

    The activation line is part of the payload rather than hardcoded in the
    frontend because it names the wake phrase, and the phrase has exactly one
    home (`wake.py`).
    """
    return {
        "categories": categories(),
        "wake_phrase": wake.WAKE_PHRASE,
        "wake_phrase_display": wake.WAKE_PHRASE_DISPLAY,
        "push_to_talk_key": "Space",
    }
