"""Tests for the "What can I say?" list (Milestone 8, one shared list in 8.13).

The list's whole value is that a reader can trust it, so the central tests are
not about formatting. They hold three promises, from both directions:

* **Nothing listed that doesn't work.** Every phrase on every card is fed back
  through the router, in each context the card names, and must resolve.
* **Nothing that works is missing.** Every phrase in every router and grammar
  table must be on a card, with all its alternates. Add a command without listing
  it and the suite fails.
* **One list.** The component is built once and driven only by the registry:
  no page carries its own copy, and the component writes no command text itself.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lector.features.voice import command_grammar as grammar  # noqa: E402
from lector.features.voice import reference, router, wake  # noqa: E402

FRONTEND = ROOT / "frontend"
COMPONENT = FRONTEND / "pages" / "command-reference.js"
SCREENS = (router.HOME, router.SETTINGS, router.READING)
# What a variable is filled with to say a phrase for real.
SAMPLE = {"{page}": "12", "{number}": "3", "{words}": "the quick brown fox"}


def sayable(phrase: str) -> str:
    for placeholder, value in SAMPLE.items():
        phrase = phrase.replace(placeholder, value)
    return phrase


def all_commands(context: str = router.READING) -> list[dict]:
    return reference.commands(context)


def registry_tables() -> list[tuple[str, dict[str, tuple[str, ...]]]]:
    """Every fixed-phrase table the recognizer knows, found rather than listed,
    so a new `*_PHRASES` table is covered the moment it exists."""
    found = [
        (f"router.{name}", value)
        for name, value in vars(router).items()
        if name.endswith("_PHRASES") and isinstance(value, dict)
    ]
    found.append(("grammar.PHRASES", grammar.PHRASES))
    return found


def registry_patterns() -> list[tuple[str, tuple[str, ...], str]]:
    """The commands that take a variable: (name, spoken leads, placeholder)."""
    return [
        ("go to a page", grammar.GOTO_PREFIXES, reference.PAGE),
        ("highlight", grammar.HIGHLIGHT_TRIGGERS, reference.WORDS),
        ("highlight a sentence", grammar.HIGHLIGHT_SENTENCE_TRIGGERS, reference.WORDS),
        ("open a numbered file", router.OPEN_NUMBER_PREFIXES, reference.NUMBER),
    ]


def card_phrase_sets(context: str = router.READING) -> list[set[str]]:
    return [{p.lower() for p in command["phrases"]} for command in all_commands(context)]


class NothingListedThatDoesNotWorkTests(unittest.TestCase):
    def test_every_phrase_resolves_in_every_context_its_card_names(self):
        # Through the router, not `command_grammar.parse` alone: the router is
        # what actually runs.
        for command in all_commands():
            for phrase in command["phrases"]:
                for context in command["contexts"]:
                    with self.subTest(context=context, phrase=phrase):
                        self.assertIsNotNone(
                            router.resolve(context, sayable(phrase))["command"],
                            f"the list offers {phrase!r} in {context} but the router rejects it",
                        )

    def test_a_page_pattern_parses_as_a_jump(self):
        cards = [c for c in all_commands() if any(reference.PAGE in p for p in c["phrases"])]
        self.assertEqual(len(cards), 1)
        for phrase in cards[0]["phrases"]:
            with self.subTest(phrase=phrase):
                parsed = grammar.parse(sayable(phrase))
                self.assertEqual(parsed["intent"], grammar.GOTO_PAGE)
                self.assertEqual(parsed["page"], 12)

    def test_a_number_pattern_parses_as_that_row(self):
        cards = [c for c in all_commands() if any(reference.NUMBER in p for p in c["phrases"])]
        self.assertTrue(cards)
        for phrase in cards[0]["phrases"]:
            with self.subTest(phrase=phrase):
                resolved = router.resolve(router.HOME, sayable(phrase))["command"]
                self.assertEqual(resolved["intent"], router.OPEN_NUMBER)

    def test_the_highlight_patterns_carry_the_words_to_mark(self):
        for command in all_commands():
            for phrase in command["phrases"]:
                if reference.WORDS in phrase:
                    with self.subTest(phrase=phrase):
                        self.assertTrue(grammar.parse(sayable(phrase)).get("query"))

    def test_every_phrase_is_a_pattern_not_one_invented_example(self):
        # A variable is a placeholder the page fills with the real range. A
        # digit in a phrase would be a single made-up example presented as
        # the command.
        for command in all_commands():
            for phrase in command["phrases"]:
                with self.subTest(phrase=phrase):
                    self.assertNotRegex(phrase, r"\d")


class NothingThatWorksIsMissingTests(unittest.TestCase):
    def test_every_phrase_in_every_table_is_on_a_card(self):
        listed = set().union(*card_phrase_sets())
        for name, table in registry_tables():
            for intent, phrasings in table.items():
                for phrasing in phrasings:
                    with self.subTest(table=name, intent=intent, phrase=phrasing):
                        self.assertIn(
                            phrasing.lower(), listed,
                            f"{phrasing!r} ({name} {intent}) is recognized but not in the list",
                        )

    def test_every_intent_shows_all_its_alternates_on_one_card(self):
        # Not a sample: a card showing 2 of the 6 ways to say "next page" would
        # make the other four look like they do not work.
        cards = card_phrase_sets()
        for name, table in registry_tables():
            for intent, phrasings in table.items():
                wanted = {p.lower() for p in phrasings}
                with self.subTest(table=name, intent=intent):
                    self.assertTrue(
                        any(wanted <= card for card in cards),
                        f"no single card carries every phrasing of {intent}: {sorted(wanted)}",
                    )

    def test_every_command_that_takes_a_variable_is_listed_with_every_lead(self):
        cards = card_phrase_sets()
        for name, leads, placeholder in registry_patterns():
            wanted = {f"{lead} {placeholder}" for lead in leads}
            with self.subTest(command=name):
                self.assertTrue(any(wanted <= card for card in cards), sorted(wanted))

    def test_the_card_carries_as_many_phrases_as_the_grammar_has(self):
        nav = next(c for c in all_commands() if "next page" in {p.lower() for p in c["phrases"]})
        self.assertEqual(len(nav["phrases"]), len(grammar.PHRASES[grammar.NEXT_PAGE]))


class ListShapeTests(unittest.TestCase):
    def test_every_command_offers_a_keyboard_or_mouse_equivalent(self):
        # docs/PRD.md's parity requirement, restated as something the reader
        # can check: the list claims every voice command also has a button or
        # a shortcut, and this is what keeps that claim honest.
        for command in all_commands():
            with self.subTest(command=command["phrases"]):
                self.assertTrue(command["equivalent"].strip())

    def test_every_command_explains_what_it_does_and_where(self):
        for command in all_commands():
            with self.subTest(command=command["phrases"]):
                self.assertTrue(command["description"].strip())
                self.assertTrue(command["contexts"])
                self.assertTrue(command["phrases"])

    def test_no_section_is_empty_and_ids_are_unique(self):
        ids = [s["id"] for s in reference.sections()]
        self.assertEqual(len(ids), len(set(ids)))
        for section in reference.sections():
            with self.subTest(section=section["id"]):
                self.assertTrue(section["commands"])
                self.assertTrue(section["scope_note"].strip())

    def test_search_is_absent_until_it_exists(self):
        # docs/PRD.md names a "Finding words" category, but in-document search
        # has not been built. It belongs here when it is, and not before.
        self.assertNotIn("Finding words", [s["title"] for s in reference.sections()])

    def test_the_panel_names_both_ways_of_starting(self):
        panel = reference.panel()
        self.assertEqual(panel["wake_phrase"], wake.WAKE_PHRASE)
        self.assertTrue(panel["push_to_talk_key"])

    def test_the_displayed_phrase_is_the_recognized_one(self):
        # Settings and onboarding both print `wake_phrase_display`, so the
        # nicely-cased form has to stay the same words as the grammar's.
        panel = reference.panel()
        self.assertEqual(panel["wake_phrase_display"].lower(), panel["wake_phrase"])


class SameListOnEveryScreenTests(unittest.TestCase):
    ORDER = (
        "anywhere", "back_home", "recent", "favorites", "theme", "save", "reopen",
        "moving", "highlighting", "undo_redo", "app", "this_list", "prompts",
    )

    def test_every_screen_gets_every_section_and_every_command(self):
        reference_ids = {s["id"] for s in reference.sections(router.READING)}
        every_card = sorted(map(str, card_phrase_sets(router.READING)))
        for context in SCREENS:
            with self.subTest(context=context):
                self.assertEqual({s["id"] for s in reference.sections(context)}, reference_ids)
                self.assertEqual(sorted(map(str, card_phrase_sets(context))), every_card)

    def test_a_screens_own_sections_come_first_then_the_rest_in_the_fixed_order(self):
        for context in SCREENS:
            ordered = reference.sections(context)
            own = [s["id"] for s in ordered if s["available"] and not s["everywhere"]]
            rest = [s["id"] for s in ordered if s["id"] not in own]
            with self.subTest(context=context):
                self.assertEqual([s["id"] for s in ordered], own + rest)
                self.assertTrue(own)
                # Each group keeps the one fixed order.
                for group in (own, rest):
                    self.assertEqual(group, sorted(group, key=self.ORDER.index))

    def test_each_screen_leads_with_its_own_commands(self):
        self.assertEqual(reference.sections(router.HOME)[0]["id"], "recent")
        self.assertEqual(reference.sections(router.SETTINGS)[0]["id"], "theme")
        self.assertEqual(reference.sections(router.READING)[0]["id"], "moving")

    def test_a_section_the_screen_cannot_act_on_says_where_it_works(self):
        for context in SCREENS:
            for section in reference.sections(context):
                with self.subTest(context=context, section=section["id"]):
                    self.assertEqual(section["available"], context in section["contexts"])
                    if not section["available"]:
                        self.assertTrue(section["scope_note"].strip())

    def test_the_panel_threads_context_through_to_sections(self):
        for context in SCREENS:
            with self.subTest(context=context):
                self.assertEqual(
                    [s["id"] for s in reference.panel(context)["sections"]],
                    [s["id"] for s in reference.sections(context)],
                )

    def test_every_other_context_falls_back_to_the_reading_order(self):
        for context in router.CONTEXTS:
            if context in SCREENS:
                continue
            with self.subTest(context=context):
                self.assertEqual(
                    [s["id"] for s in reference.sections(context)],
                    [s["id"] for s in reference.sections(router.READING)],
                )


class TrySayingTests(unittest.TestCase):
    """The empty state's "TRY SAYING" chips come from the same registry as the
    list, so a chip can never be a command that does not work."""

    def test_home_suggests_the_three_mockup_phrases(self):
        self.assertEqual(
            reference.try_saying(router.HOME), ["open a PDF", "what can I say", "open settings"]
        )

    def test_every_suggestion_resolves_through_the_router(self):
        for context in SCREENS:
            for phrase in reference.try_saying(context):
                with self.subTest(context=context, phrase=phrase):
                    self.assertIsNotNone(router.resolve(context, phrase)["command"])

    def test_every_suggestion_is_also_listed(self):
        listed = set().union(*card_phrase_sets(router.HOME))
        for phrase in reference.try_saying(router.HOME):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), listed)

    def test_a_context_with_no_suggestions_returns_none_rather_than_guessing(self):
        self.assertEqual(reference.try_saying(router.SETTINGS), [])

    def test_the_panel_payload_carries_them(self):
        self.assertEqual(reference.panel(router.HOME)["try_saying"], reference.try_saying(router.HOME))

    def test_pdf_is_written_as_an_initialism(self):
        self.assertIn("open a PDF", reference.try_saying(router.HOME))


# ---------------------------------------------------------------------------
# The component: one, shared, and driven only by the registry.
# ---------------------------------------------------------------------------


def strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(?<![:\"'`])//.*$", "", text)


def all_commands_text() -> set[str]:
    """Every string the registry puts in front of a reader."""
    out = set()
    for context in SCREENS:
        for command in all_commands(context):
            out.update(p.lower() for p in command["phrases"])
            out.add(command["description"].lower())
            out.add(command["equivalent"].lower())
    return out


class OneSharedComponentTests(unittest.TestCase):
    PAGES = {
        "home": (FRONTEND / "index.html", FRONTEND / "pages" / "home.js"),
        "settings": (FRONTEND / "pages" / "settings.html", FRONTEND / "pages" / "settings.js"),
        "reading": (FRONTEND / "pages" / "reading.html", FRONTEND / "pages" / "reading.js"),
    }

    def test_every_screen_loads_the_one_component_and_stylesheet(self):
        for name, (html, _js) in self.PAGES.items():
            text = html.read_text(encoding="utf-8")
            with self.subTest(screen=name):
                self.assertEqual(text.count("command-reference.js"), 1)
        for css in ("home.css", "reading.css"):
            self.assertIn("reference.css", (FRONTEND / "pages" / css).read_text(encoding="utf-8"))

    def test_the_dialog_markup_exists_exactly_once_in_the_frontend(self):
        holders = [
            path.relative_to(FRONTEND).as_posix()
            for path in FRONTEND.rglob("*")
            if path.suffix in (".html", ".js", ".css")
            and 'id="referenceDialog"' in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(holders, ["pages/command-reference.js"])

    def test_no_page_builds_its_own_copy_of_the_list(self):
        for name, (html, js) in self.PAGES.items():
            for path in (html, js):
                text = path.read_text(encoding="utf-8")
                with self.subTest(screen=name, file=path.name):
                    for private in ("renderReference", "reference-card-row", "reference-section"):
                        self.assertNotIn(private, text)

    def test_every_entry_point_opens_the_same_component(self):
        home = self.PAGES["home"][1].read_text(encoding="utf-8")
        settings = self.PAGES["settings"][1].read_text(encoding="utf-8")
        reading = self.PAGES["reading"][1].read_text(encoding="utf-8")
        # The sidebar link, the reader's two buttons, and the spoken command.
        self.assertIn('getElementById("voiceHelpBtn").addEventListener("click", openCommandReference)', home)
        self.assertIn('getElementById("voiceHelpBtn").addEventListener("click", openCommandReference)', settings)
        self.assertIn('referenceRailBtn.addEventListener("click", openCommandReference)', reading)
        self.assertIn('referenceFooterBtn.addEventListener("click", openCommandReference)', reading)
        for js in (home, settings, reading):
            self.assertIn("HELP: () => openCommandReference()", js)
        # The "?" shortcut is the component's own, so it exists on every screen
        # and no page keeps a private one.
        component = COMPONENT.read_text(encoding="utf-8")
        self.assertIn('ev.key !== "?"', component)
        self.assertNotIn('case "?"', reading)

    def test_the_component_loads_the_list_from_the_registry_only(self):
        self.assertIn('callApi("get_command_reference")', COMPONENT.read_text(encoding="utf-8"))

    def test_the_component_writes_no_command_text_of_its_own(self):
        code = strip_js_comments(COMPONENT.read_text(encoding="utf-8"))
        # The dialog's own chrome (title, the "say these exactly" line) lives in
        # the markup; everything outside it is code that may only pass the
        # payload's strings through.
        code = code[: code.index("const REFERENCE_MARKUP")] + code[code.index("</div>`;") :]
        lowered = code.lower()
        for text in all_commands_text():
            if len(text.split()) >= 2 or len(text) > 12:
                with self.subTest(text=text):
                    self.assertNotIn(text, lowered)

    def test_no_screen_shows_a_spoken_phrase_the_registry_does_not_have(self):
        # UI copy outside the list ("or say “documents”", toasts, hints) quotes
        # phrases too. Each one must be a phrase the recognizer accepts.
        known = set(all_commands_text())
        known |= {wake.WAKE_PHRASE_DISPLAY.lower(), wake.WAKE_PHRASE.lower()}
        known |= {p.lower() for _name, table in registry_tables() for ps in table.values() for p in ps}
        # Only lines that tell the reader to "say" something. In a page's text
        # both quote styles mark a phrase; in script, straight quotes are code, so
        # only the curly ones the copy uses are read.
        curly = re.compile(r"“([^”\n]{2,40})”")
        either = re.compile(r"[“\"]([^”\"\n]{2,40})[”\"]")
        offenders = []
        for path in FRONTEND.rglob("*"):
            if path.suffix not in (".html", ".js") or path == COMPONENT:
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".js":
                text, pattern = strip_js_comments(text), curly
            else:
                text = re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", "", text, flags=re.S))
                pattern = either
            for line in text.splitlines():
                if not re.search(r"\bsay(?:ing)?\b", line, re.I):
                    continue
                for match in pattern.finditer(line):
                    phrase = match.group(1).strip().lower()
                    if phrase not in known and "${" not in phrase:
                        offenders.append((path.name, match.group(1)))
        self.assertEqual(offenders, [])

    def test_the_description_says_phrases_must_be_exact(self):
        # Recognition is a closed grammar, with no language understanding.
        flat = re.sub(r"\s+", " ", COMPONENT.read_text(encoding="utf-8"))
        self.assertIn("Say these phrases exactly.", flat)
        self.assertIn("Lector only recognizes the commands listed here.", flat)
        self.assertIn("Every command also has a button or shortcut.", flat)
        self.assertNotIn("magic words", flat)
        self.assertNotIn("however feels natural", flat)


if __name__ == "__main__":
    unittest.main()
