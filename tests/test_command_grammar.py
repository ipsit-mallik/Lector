"""Tests for navigation command parsing and its fuzzy tolerance (Milestone 6).

`unittest` rather than pytest, matching `test_voice_engine.py` and the short
dependency list docs/TECH_STACK.md commits to. Run with:

    python -m unittest discover -s tests

The through-line of these tests is that a *wrong* action is worse than *no*
action. docs/PRD.md makes voice an accelerator over an app that works fully
by mouse and keyboard, so a misheard phrase that does nothing costs the
reader one repetition, while a misheard phrase that jumps to page 5 costs
them their place. Most of what follows pins down the second case not
happening.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.voice import command_grammar as cg  # noqa: E402
from lector.features.voice import fuzzy  # noqa: E402


class LevenshteinTests(unittest.TestCase):
    def test_identical_strings_cost_nothing(self):
        self.assertEqual(fuzzy.levenshtein("next page", "next page"), 0)

    def test_counts_single_character_substitution(self):
        self.assertEqual(fuzzy.levenshtein("next", "text"), 1)

    def test_counts_insertion_and_deletion(self):
        self.assertEqual(fuzzy.levenshtein("page", "pages"), 1)
        self.assertEqual(fuzzy.levenshtein("pages", "page"), 1)

    def test_empty_string_costs_the_whole_other_string(self):
        self.assertEqual(fuzzy.levenshtein("", "page"), 4)
        self.assertEqual(fuzzy.levenshtein("page", ""), 4)

    def test_is_symmetric(self):
        self.assertEqual(
            fuzzy.levenshtein("previous page", "previous pages"),
            fuzzy.levenshtein("previous pages", "previous page"),
        )


class SimilarityTests(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertEqual(fuzzy.similarity("next page", "next page"), 1.0)

    def test_both_empty_is_one(self):
        self.assertEqual(fuzzy.similarity("", ""), 1.0)

    def test_nothing_in_common_is_zero(self):
        self.assertEqual(fuzzy.similarity("aaa", "bbb"), 0.0)

    def test_one_error_matters_less_in_a_longer_phrase(self):
        # The normalization is what makes a single threshold usable across
        # commands of very different lengths.
        one_error_in_a_short_word = fuzzy.similarity("up", "us")
        one_error_in_a_long_phrase = fuzzy.similarity("previous page", "previous pages")
        self.assertGreater(one_error_in_a_long_phrase, one_error_in_a_short_word)


class BestMatchTests(unittest.TestCase):
    def test_finds_the_closest_candidate(self):
        match = fuzzy.best_match("text page", ["previous page", "next page"])
        self.assertIsNotNone(match)
        self.assertEqual(match[0], "next page")

    def test_returns_none_when_nothing_is_close_enough(self):
        self.assertIsNone(fuzzy.best_match("bananas", ["next page", "scroll up"]))

    def test_ties_go_to_the_earliest_candidate(self):
        # Milestone 7 relies on this to make "first occurrence in reading
        # order wins" fall out of candidate ordering alone.
        match = fuzzy.best_match("page", ["page", "page"])
        self.assertEqual(match, ("page", 1.0))

    def test_empty_candidates_match_nothing(self):
        self.assertIsNone(fuzzy.best_match("next page", []))


class ExactPhraseTests(unittest.TestCase):
    """Every phrasing the grammar advertises has to actually work — a synonym
    listed in PHRASES but not parsing is a command the docs promise and the
    app ignores."""

    def test_every_listed_phrasing_parses_to_its_own_intent(self):
        for intent, phrasings in cg.PHRASES.items():
            for phrasing in phrasings:
                with self.subTest(intent=intent, phrasing=phrasing):
                    result = cg.parse(phrasing)
                    self.assertIsNotNone(result)
                    self.assertEqual(result["intent"], intent)

    def test_next_page_synonyms(self):
        for phrase in ("next page", "turn the page", "forward"):
            with self.subTest(phrase=phrase):
                self.assertEqual(cg.parse(phrase)["intent"], cg.NEXT_PAGE)

    def test_previous_page_synonyms(self):
        for phrase in ("previous page", "go back", "back"):
            with self.subTest(phrase=phrase):
                self.assertEqual(cg.parse(phrase)["intent"], cg.PREV_PAGE)

    def test_scroll_synonyms(self):
        self.assertEqual(cg.parse("scroll down")["intent"], cg.SCROLL_DOWN)
        self.assertEqual(cg.parse("scroll up")["intent"], cg.SCROLL_UP)


class NormalizationTests(unittest.TestCase):
    def test_is_case_insensitive(self):
        self.assertEqual(cg.parse("Next Page")["intent"], cg.NEXT_PAGE)

    def test_ignores_surrounding_punctuation_and_whitespace(self):
        self.assertEqual(cg.parse("  next page.  ")["intent"], cg.NEXT_PAGE)

    def test_empty_input_is_not_a_command(self):
        self.assertIsNone(cg.parse(""))
        self.assertIsNone(cg.parse("   "))
        self.assertIsNone(cg.parse(None))

    def test_reports_the_normalized_phrase_back(self):
        self.assertEqual(cg.parse("  Next  Page ")["phrase"], "next page")


class FuzzyToleranceTests(unittest.TestCase):
    def test_recovers_from_a_single_misheard_character(self):
        # The exact failure this milestone's fuzzy tolerance exists for.
        self.assertEqual(cg.parse("text page")["intent"], cg.NEXT_PAGE)

    def test_recovers_from_a_dropped_letter(self):
        self.assertEqual(cg.parse("prevous page")["intent"], cg.PREV_PAGE)

    def test_unrelated_speech_is_not_a_command(self):
        for phrase in ("hello there", "what time is it", "bananas"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(cg.parse(phrase))

    def test_near_misses_do_not_cross_intents(self):
        # "next" and "previous" are opposite actions; a tolerance loose
        # enough to confuse them would be worse than none at all.
        self.assertEqual(cg.parse("next page")["intent"], cg.NEXT_PAGE)
        self.assertEqual(cg.parse("previous page")["intent"], cg.PREV_PAGE)
        self.assertEqual(cg.parse("scroll down")["intent"], cg.SCROLL_DOWN)
        self.assertEqual(cg.parse("scroll up")["intent"], cg.SCROLL_UP)


class GotoPageTests(unittest.TestCase):
    def test_parses_a_spoken_digit(self):
        result = cg.parse("go to page five")
        self.assertEqual(result["intent"], cg.GOTO_PAGE)
        self.assertEqual(result["page"], 5)

    def test_parses_a_numeral(self):
        self.assertEqual(cg.parse("jump to page 7")["page"], 7)

    def test_parses_a_compound_number(self):
        self.assertEqual(cg.parse("go to page twenty three")["page"], 23)

    def test_parses_hundreds_with_filler(self):
        # "and" is how people actually say it, and dropping it would leave a
        # stray word wrecking the match against the "go to page" prefix.
        self.assertEqual(cg.parse("go to page one hundred and five")["page"], 105)

    def test_bare_page_prefix_is_enough(self):
        self.assertEqual(cg.parse("page twelve")["page"], 12)

    def test_a_bare_number_is_not_a_jump(self):
        # Far likelier a misfire than an intent, and a wrong jump loses the
        # reader's place.
        self.assertIsNone(cg.parse("five"))
        self.assertIsNone(cg.parse("twenty three"))

    def test_page_zero_is_rejected(self):
        # The reader sees 1-based page numbers; page 0 does not exist.
        self.assertIsNone(cg.parse("go to page zero"))

    def test_a_number_without_a_recognizable_prefix_is_not_a_jump(self):
        self.assertIsNone(cg.parse("bananas five"))

    def test_page_forward_is_not_read_as_a_jump(self):
        # "page" begins both a jump and a NEXT_PAGE synonym; the trailing
        # number is what distinguishes them.
        self.assertEqual(cg.parse("page forward")["intent"], cg.NEXT_PAGE)

    def test_filler_alone_after_the_prefix_is_not_a_jump(self):
        self.assertIsNone(cg.parse("go to page and"))


class VocabularyTests(unittest.TestCase):
    def test_covers_every_word_in_every_phrasing(self):
        # The engine pins its recognizer to this list, so any word missing
        # here is a phrasing that can never be heard.
        vocabulary = set(cg.VOCABULARY)
        for phrasings in cg.PHRASES.values():
            for phrasing in phrasings:
                for word in phrasing.split():
                    with self.subTest(word=word):
                        self.assertIn(word, vocabulary)

    def test_covers_every_goto_prefix_word(self):
        vocabulary = set(cg.VOCABULARY)
        for prefix in cg.GOTO_PREFIXES:
            for word in prefix.split():
                with self.subTest(word=word):
                    self.assertIn(word, vocabulary)

    def test_includes_number_words(self):
        for word in ("one", "nineteen", "twenty", "ninety", "hundred", "and"):
            with self.subTest(word=word):
                self.assertIn(word, cg.VOCABULARY)

    def test_is_sorted_and_deduplicated(self):
        self.assertEqual(cg.VOCABULARY, sorted(set(cg.VOCABULARY)))


class IntentContractTests(unittest.TestCase):
    """The intent strings cross the bridge into `frontend/pages/reading.js`'s
    VOICE_ACTIONS table, so they are a contract, not an internal detail."""

    def test_intent_names_are_stable(self):
        self.assertEqual(cg.NEXT_PAGE, "NEXT_PAGE")
        self.assertEqual(cg.PREV_PAGE, "PREV_PAGE")
        self.assertEqual(cg.GOTO_PAGE, "GOTO_PAGE")
        self.assertEqual(cg.SCROLL_UP, "SCROLL_UP")
        self.assertEqual(cg.SCROLL_DOWN, "SCROLL_DOWN")

    def test_every_phrase_intent_is_a_declared_constant(self):
        declared = {cg.NEXT_PAGE, cg.PREV_PAGE, cg.SCROLL_UP, cg.SCROLL_DOWN}
        self.assertEqual(set(cg.PHRASES), declared)

    def test_results_carry_the_matched_phrase_and_score(self):
        result = cg.parse("text page")
        self.assertEqual(result["matched"], "next page")
        self.assertLess(result["score"], 1.0)
        self.assertGreaterEqual(result["score"], fuzzy.DEFAULT_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
