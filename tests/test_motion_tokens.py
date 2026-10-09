"""The motion contract (docs/DESIGN_SYSTEM.md, "Motion").

One duration pair and one easing for the whole app, no overshoot curve, only
compositor/paint properties animated by the shared components, and reduced
motion meaning "no movement". Plus the Settings rule that voice can never be
the thing that switches voice activation off.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SRC_VOICE = Path(__file__).resolve().parents[1] / "src" / "lector" / "features" / "voice"

THEME_CSS = (FRONTEND / "shared" / "theme.css").read_text(encoding="utf-8")
COMPONENTS_CSS = (FRONTEND / "shared" / "components.css").read_text(encoding="utf-8")
SETTINGS_CSS = (FRONTEND / "pages" / "settings.css").read_text(encoding="utf-8")
SETTINGS_JS = (FRONTEND / "pages" / "settings.js").read_text(encoding="utf-8")

# Properties the shared components and Settings may animate: transform,
# opacity, colour, background, border and shadow -- never layout.
ALLOWED_TRANSITION_PROPERTIES = {
    "transform", "opacity", "color", "background-color", "border-color",
    "box-shadow", "text-decoration-color", "fill", "stroke",
}


def declared(name: str, css: str = THEME_CSS) -> str:
    match = re.search(rf"{re.escape(name)}:\s*([^;]+);", css)
    assert match, f"{name} is not declared"
    return match.group(1).strip()


def transition_properties(css: str) -> set:
    properties = set()
    for value in re.findall(r"(?<![-\w])transition:\s*([^;]+);", css):
        for part in value.split(","):
            properties.add(part.split()[0])
    return properties


class MotionTokenTests(unittest.TestCase):
    def test_one_fast_and_one_base_duration(self):
        self.assertEqual(declared("--dur-fast"), "120ms")
        self.assertEqual(declared("--dur-base"), "200ms")

    def test_one_easing_and_no_overshoot_curve(self):
        self.assertEqual(declared("--ease-out"), "cubic-bezier(0.2, 0.8, 0.2, 1)")
        for path in FRONTEND.rglob("*.css"):
            text = path.read_text(encoding="utf-8")
            for retired in ("--ease-spring", "--dur-med", "--dur-slow"):
                self.assertNotIn(retired, text, f"{retired} still used in {path.name}")

    def test_no_css_easing_overshoots(self):
        # A cubic-bezier with a y value above 1 (or below 0) overshoots.
        for path in FRONTEND.rglob("*.css"):
            for curve in re.findall(r"cubic-bezier\(([^)]*)\)", path.read_text(encoding="utf-8")):
                y_values = [float(v) for v in curve.split(",")[1::2]]
                self.assertTrue(all(0 <= y <= 1 for y in y_values), f"{path.name}: {curve}")

    def test_shared_components_animate_only_allowed_properties(self):
        for name, css in (("components.css", COMPONENTS_CSS), ("settings.css", SETTINGS_CSS)):
            extra = transition_properties(css) - ALLOWED_TRANSITION_PROPERTIES
            self.assertFalse(extra, f"{name} transitions layout/other properties: {extra}")

    def test_reduced_motion_removes_movement_not_just_speed(self):
        block = THEME_CSS[THEME_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertRegex(block, r"--lift:\s*0px")
        self.assertRegex(block, r"--enter-rise:\s*0px")
        self.assertIn("transition-duration: 0.01ms !important", block)
        self.assertIn("animation-duration: 0.01ms !important", block)

    def test_theme_switch_crossfades_for_one_base_duration(self):
        self.assertRegex(THEME_CSS, r"\.theme-fading[^{]*\{[^}]*var\(--dur-base\)")


    def test_reduced_motion_also_clears_animation_delay_and_the_theme_fade(self):
        block = THEME_CSS[THEME_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("animation-delay: 0ms !important", block)
        self.assertRegex(block, r"\.theme-fading[^{]*\{[^}]*transition-duration: 0\.01ms")

    def test_hover_lifts_and_entrance_distances_use_the_zeroable_tokens(self):
        home_css = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")
        for css in (home_css, COMPONENTS_CSS):
            self.assertNotRegex(css, r"translateY\(-?\d+px\)")


class SharedCardHoverTests(unittest.TestCase):
    """One hover recipe for every card (Recent's grid cards and all of Settings')."""

    HOME_CSS = (FRONTEND / "pages" / "home.css").read_text(encoding="utf-8")
    CARD_JS = (FRONTEND / "pages" / "recent-card.js").read_text(encoding="utf-8")
    SETTINGS_HTML = (FRONTEND / "pages" / "settings.html").read_text(encoding="utf-8")
    READING_HTML = (FRONTEND / "pages" / "reading.html").read_text(encoding="utf-8")

    def test_card_lift_is_the_recent_card_recipe(self):
        rule = COMPONENTS_CSS[COMPONENTS_CSS.index(".card-lift:hover,"):]
        rule = rule[:rule.index("}")]
        self.assertIn("border-color: var(--accent)", rule)
        self.assertIn("box-shadow: var(--shadow-2)", rule)
        self.assertIn("translateY(var(--lift))", rule)
        self.assertIn(".card-lift:focus-visible", COMPONENTS_CSS)

    def test_no_card_writes_its_own_hover_css(self):
        for selector in (r"\.recent-card:hover\s*[,{]", r"\.theme-card:hover\s*[,{]",
                         r"\.option-row:hover\s*[,{]", r"\.card:hover\s*[,{]",
                         r"\.voice-card[^{]*:hover"):
            for name, css in (("components.css", COMPONENTS_CSS), ("home.css", self.HOME_CSS),
                              ("settings.css", SETTINGS_CSS)):
                self.assertNotRegex(css, selector, f"{name}: {selector}")

    def test_every_card_opts_in_to_the_shared_class(self):
        self.assertIn("recent-card recent-item card-lift", self.CARD_JS)
        self.assertEqual(self.SETTINGS_HTML.count('class="theme-card card-lift"'), 3)
        for html in (self.SETTINGS_HTML, self.READING_HTML):
            for tag in re.findall(r'<button[^>]*class="option-row[^"]*"', html):
                self.assertIn("card-lift", tag)
        self.assertEqual(self.SETTINGS_HTML.count("card voice-card card-lift"), 2)

    def test_selected_stays_distinct_from_hover(self):
        self.assertRegex(COMPONENTS_CSS, r"\.card-lift\.selected:hover[^{]*\{[^}]*0 0 0 1px var\(--accent\)")

    def test_selected_option_is_distinguishable_from_a_hovered_neighbour(self):
        # Hover and selected share the accent border, so selected also has a
        # fill that hover never sets, and the stacked rows keep a real gap.
        self.assertRegex(COMPONENTS_CSS, r"\.option-row\.selected\s*\{\s*background-color: color-mix")
        self.assertNotRegex(COMPONENTS_CSS, r"\.card-lift:hover[^{]*\{[^}]*background")
        self.assertRegex(COMPONENTS_CSS, r"\.option-row \+ \.option-row\s*\{\s*margin-top: var\(--space-3\)")

    def test_voice_cards_keep_the_option_card_fill_in_both_states(self):
        self.assertNotIn("--surface-on", THEME_CSS + SETTINGS_CSS + COMPONENTS_CSS)
        card_rule = r"^\.voice-card(?::has\([^)]*\)(?::hover)?)?\s*\{[^}]*(background|border|box-shadow)"
        self.assertNotRegex(SETTINGS_CSS, re.compile(card_rule, re.MULTILINE))


class ThemeSwitchTests(unittest.TestCase):
    """One crossfade for the whole window, and no flash on launch."""

    UI_JS = (FRONTEND / "js" / "ui.js").read_text(encoding="utf-8")
    MAIN_PY = (FRONTEND.parent / "src" / "lector" / "__main__.py").read_text(encoding="utf-8")

    def test_switch_runs_as_a_view_transition_with_a_fallback(self):
        body = self.UI_JS[self.UI_JS.index("function applyTheme"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("document.startViewTransition(", body)
        self.assertIn("fadeThemeWithTransitions(name)", body)

    def test_reduced_motion_switches_instantly(self):
        body = self.UI_JS[self.UI_JS.index("function applyTheme"):]
        body = body[:body.index("startViewTransition")]
        self.assertIn("(prefers-reduced-motion: reduce)", body)
        self.assertIn("commitTheme(name)", body)

    def test_the_fade_class_is_not_removed_on_a_timer_from_the_click(self):
        # A timer from the click fires before a slow first frame has started
        # the fade (measured), turning it into a snap or cutting it short.
        self.assertNotIn("THEME_FADE_PADDING_MS", self.UI_JS)
        self.assertIn('"transitionend"', self.UI_JS)

    def test_components_cannot_start_a_second_fade_inside_the_new_frame(self):
        rule = THEME_CSS[THEME_CSS.index(".theme-switching,"):]
        rule = rule[:rule.index("}")]
        self.assertIn("transition: none !important", rule)
        body = self.UI_JS[self.UI_JS.index("function applyTheme"):]
        self.assertRegex(body, r'startViewTransition\(\(\) => \{[^}]*classList\.add\("theme-switching"\)[^}]*commitTheme\(name\)')
        self.assertIn('classList.remove("theme-switching")', body)

    def test_view_transition_uses_the_base_duration_and_the_one_easing(self):
        rule = THEME_CSS[THEME_CSS.index("::view-transition-group(root)"):]
        rule = rule[:rule.index("}")]
        self.assertIn("animation-duration: var(--dur-base)", rule)
        self.assertIn("animation-timing-function: var(--ease-out)", rule)

    def test_reduced_motion_also_collapses_any_view_transition(self):
        block = THEME_CSS[THEME_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("::view-transition-old(*)", block)
        self.assertRegex(block, r"::view-transition-new\(\*\)[^{]*\{[^}]*0\.01ms")

    def test_the_theme_cards_change_inside_the_same_step_as_the_theme(self):
        self.assertRegex(self.UI_JS, r'dispatchEvent\(new CustomEvent\("themechange"')
        self.assertIn('document.addEventListener("themechange"', SETTINGS_JS)
        body = SETTINGS_JS[SETTINGS_JS.index("async function selectTheme"):]
        body = body[:body.index("function markCurrentThemeCard")]
        self.assertNotIn("markCurrentThemeCard(", body)

    def test_the_reader_saves_the_theme_it_cycles_to(self):
        # The next page is served in whatever settings.json holds.
        reading_js = (FRONTEND / "pages" / "reading.js").read_text(encoding="utf-8")
        body = reading_js[reading_js.index("async function cycleTheme"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('callApi("set_theme", next)', body)

    def test_every_page_arrives_in_its_theme_before_the_first_stylesheet(self):
        # The server writes the saved theme into <html data-theme> as it sends
        # the page (tests/test_frontend_server.py), so there is no script for it.
        for name in ("index.html", "pages/settings.html", "pages/reading.html", "pages/onboarding.html"):
            html = (FRONTEND / name).read_text(encoding="utf-8")
            self.assertLess(html.index('data-theme="light"'), html.index('rel="stylesheet"'), name)
            self.assertNotIn("dataset.theme = localStorage", html, name)

    def test_launch_paints_the_native_window_in_the_theme(self):
        self.assertIn("background_color=theme.token(settings.get_theme(), \"bg\")", self.MAIN_PY)


class RadioCardKeyboardTests(unittest.TestCase):
    UI_JS = (FRONTEND / "js" / "ui.js").read_text(encoding="utf-8")

    def test_arrow_keys_do_not_reach_page_level_handlers(self):
        # The reader turns pages on the arrows; a focused card must keep them.
        self.assertIn("e.stopPropagation()", self.UI_JS)

    def test_settings_save_group_does_not_select_overwrite_on_arrow(self):
        self.assertRegex(SETTINGS_JS, r'getElementById\("saveOptions"\),\s*\{\s*selectOnArrow:\s*false')

    def test_save_dialog_does_not_select_overwrite_on_arrow(self):
        source = (FRONTEND / "pages" / "save-dialog.js").read_text(encoding="utf-8")
        self.assertRegex(source, r'saveDialogOptions"\),\s*\{\s*selectOnArrow:\s*false')

    def test_settings_restore_guard_is_always_lifted(self):
        self.assertIn(".finally(endRestoring)", SETTINGS_JS)
        self.assertEqual(SETTINGS_JS.count("endRestoring()"), 1)  # only the definition

    def test_a_failed_theme_save_is_reverted(self):
        body = SETTINGS_JS[SETTINGS_JS.index("async function selectTheme"):]
        body = body[:body.index("function markCurrentThemeCard")]
        self.assertIn("catch (err)", body)
        self.assertIn("applyTheme(previous)", body)

    def test_theme_crossfade_keeps_opacity_and_transform_transitions(self):
        rule = THEME_CSS[THEME_CSS.index(".theme-fading,"):]
        rule = rule[:rule.index("}")]
        self.assertIn("opacity var(--dur-base)", rule)
        self.assertIn("transform var(--dur-base)", rule)


class VoiceCannotDisableVoiceTests(unittest.TestCase):
    def test_no_settings_voice_action_touches_voice_activation(self):
        actions = SETTINGS_JS[SETTINGS_JS.index("const VOICE_ACTIONS"):]
        actions = actions[:actions.index("};")]
        for forbidden in ("Toggle", "set_voice_activation", "persistVoiceActivation", "checked"):
            self.assertNotIn(forbidden, actions)

    def test_the_voice_package_never_sets_voice_activation(self):
        for path in SRC_VOICE.glob("*.py"):
            self.assertNotIn("set_voice_activation", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
