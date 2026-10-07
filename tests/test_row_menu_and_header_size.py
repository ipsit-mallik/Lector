"""Two small Home refinements, checked from the source files.

* The list row already carries a favorite star, so its "⋯" menu does not repeat
  it as "Add to / Remove from Favorites". The menu keeps Open, Show in folder and
  (Recent only) Remove from Recent. The star, and the voice commands, are what
  favorite a file.
* Clicking a card's star leaves focus on it; the card's other chip (Remove) must
  not stay visible because of that. It shows on hover and on *keyboard* focus
  (`:focus-visible`), never on plain `:focus-within`.
* The header's search and grid/list toggle were too big beside the title; the
  row's one control height is now 40px, not 48px.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.find("\nfunction ", start + 1)
    return source[start:end if end != -1 else len(source)]


class RowMenuTest(unittest.TestCase):
    def test_the_menu_does_not_repeat_the_favorite_star(self):
        body = _function_body(_read("pages", "recent-list.js"), "openRowMenu")
        self.assertNotIn("Favorites", body)
        self.assertNotIn("onToggleFavorite", body)

    def test_the_menu_keeps_open_show_in_folder_and_remove(self):
        source = _read("pages", "recent-list.js")
        body = _function_body(source, "openRowMenu")
        self.assertIn('"Open"', body)
        self.assertIn('"Show in folder"', body)
        self.assertIn("buildRemoveItems(", body)

    def test_the_row_still_has_its_star(self):
        source = _read("pages", "recent-list.js")
        self.assertIn("buildFavoriteBtn(entry, entry.title, handlers.onToggleFavorite)", source)


class CardChipsTest(unittest.TestCase):
    def test_chips_do_not_stay_up_because_a_click_left_focus_in_the_card(self):
        css = _read("pages", "home.css")
        self.assertNotRegex(css, r"\.recent-card:is\([^)]*:focus-within")

    def test_chips_still_show_for_keyboard_focus_anywhere_in_the_card(self):
        css = _read("pages", "home.css")
        self.assertRegex(css, r"\.recent-card:is\(:hover, :has\(:focus-visible\)\) \.card-actions")


class HeaderControlSizeTest(unittest.TestCase):
    def test_the_shared_control_height_is_40px(self):
        match = re.search(r"--control-h:\s*(\d+)px", _read("shared", "theme.css"))
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 40)

    def test_toggle_buttons_fit_inside_the_control_with_their_padding(self):
        css = _read("shared", "library-header.css")
        button = re.search(r"\.view-toggle-btn\s*\{[^}]*?width:\s*(\d+)px;\s*height:\s*(\d+)px", css)
        self.assertIsNotNone(button)
        width, height = (int(v) for v in button.groups())
        self.assertEqual(width, height)
        # 4px of padding either side (--space-1) inside the 40px control.
        self.assertLessEqual(height + 2 * 4, 40)

    def test_the_search_icon_is_smaller_than_the_control(self):
        css = _read("shared", "library-header.css")
        icon = re.search(r"\.header-search-toggle \[data-icon\]\s*\{[^}]*width:\s*(\d+)px", css)
        self.assertIsNotNone(icon)
        self.assertLessEqual(int(icon.group(1)), 18)


if __name__ == "__main__":
    unittest.main()
