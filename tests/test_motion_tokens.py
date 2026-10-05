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
