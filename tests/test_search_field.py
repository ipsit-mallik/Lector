"""The one search field (docs/DESIGN_SYSTEM.md, "Search field").

Every search in the app (the library header, the Open / Save As folder search,
"What can I say?" and find in document) is the shared `.search-field` from
frontend/shared/search-field.css, with its behaviour in
frontend/js/search-field.js. It has no border in any state: it rests recessed,
lifts on hover, and adds a soft accent halo on focus instead of the 2px outline.
A field voice is typing into pulses a voice-coloured halo and shows a mic.

The behaviour checks run search-field.js under Node against a minimal DOM stub,
the same way tests/test_library_search_behavior.py runs its script. Skipped when
Node is not installed.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SCRIPT = FRONTEND / "js" / "search-field.js"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", _strip_comments(css))
    assert match, f"{selector} has no rule"
    return match.group(1)


def _theme_block(theme: str) -> str:
    css = _read("shared", "theme.css")
    if theme == "light":
        match = re.search(r':root,\s*\[data-theme="light"\]\s*\{([^}]*)\}', css)
    else:
        match = re.search(rf'\[data-theme="{theme}"\]\s*\{{([^}}]*)\}}', css)
    assert match, theme
    return match.group(1)


# Every search in the app: (the file its markup is in, the id of its input).
FIELDS = (
    (("index.html",), "headerSearchInput"),
    (("index.html",), "openDialogSearch"),
    (("pages", "reading.html"), "saveAsDialogSearch"),
    (("pages", "reading.html"), "findInput"),
    (("pages", "command-reference.js"), "referenceSearch"),
)


def _field_markup(parts, input_id) -> str:
    """The markup of the field holding `input_id`, from its .search-field
    opening tag to a little past the input."""
    source = _read(*parts)
    at = source.index(f'id="{input_id}"')
    return source[source.rindex('<div class="search-field', 0, at):at + 1200]


class SharedComponentTests(unittest.TestCase):
    def test_every_search_is_the_shared_field(self):
        for parts, input_id in FIELDS:
            with self.subTest(input_id):
                markup = _field_markup(parts, input_id)
                self.assertRegex(markup, r'^<div class="search-field\b')
                self.assertRegex(markup, rf'<input class="search-field-input[^"]*"[^>]*id="{input_id}"')
                self.assertIn('class="search-field-icon', markup)
                self.assertIn('class="search-field-clear', markup)

    def test_the_clear_button_is_a_labelled_button(self):
        for parts, input_id in FIELDS:
            with self.subTest(input_id):
                clear = re.search(r'<button[^>]*class="search-field-clear[^>]*>',
                                  _field_markup(parts, input_id))
                self.assertIsNotNone(clear)
                self.assertIn('type="button"', clear.group(0))
                self.assertIn('aria-label="Clear search"', clear.group(0))

    def test_the_component_is_one_shared_stylesheet(self):
        self.assertIn('@import "search-field.css"', _read("shared", "components.css"))
        for path in FRONTEND.rglob("*.css"):
            if path.name == "search-field.css":
                continue
            with self.subTest(path.name):
                self.assertNotRegex(_strip_comments(path.read_text(encoding="utf-8")),
                                    r"\.search-field[-a-z]*:(hover|focus)")

    def test_the_script_loads_on_every_page_with_a_search_before_its_users(self):
        for parts, src, first_user in (
            (("index.html",), "js/search-field.js", "js/file-browser.js"),
            (("pages", "reading.html"), "../js/search-field.js", "../js/file-browser.js"),
            (("pages", "settings.html"), "../js/search-field.js", "command-reference.js"),
        ):
            with self.subTest(parts[-1]):
                html = _read(*parts)
                self.assertIn(f'<script src="{src}"></script>', html)
                self.assertLess(html.index(f'src="{src}"'), html.index(f'src="{first_user}"'))

    def test_no_field_keeps_its_old_border_or_outline(self):
        retired = {
            ("shared", "library-header.css"): (".header-search:has(:focus-visible)",),
            ("shared", "reference.css"): (".reference-search:has(input:focus-visible)",
                                          ".reference-search.dictating"),
            ("shared", "file-browser.css"): (".browser-search input:focus-visible",),
            ("pages", "reading.css"): (".find-field:hover", ".find-field:focus-within",
                                       ".find-field:has(:focus-visible)"),
        }
        for parts, selectors in retired.items():
            css = _strip_comments(_read(*parts))
            for selector in selectors:
                with self.subTest(selector):
                    self.assertNotIn(selector, css)
        for parts, selector in (
            (("shared", "library-header.css"), ".header-search"),
            (("shared", "reference.css"), ".reference-search"),
            (("shared", "file-browser.css"), ".browser-search"),
            (("pages", "reading.css"), ".find-field"),
        ):
            with self.subTest(selector):
                self.assertNotRegex(_rule(_read(*parts), selector), r"(?<![-\w])border\s*:")


class StateTests(unittest.TestCase):
    css = _strip_comments(_read("shared", "search-field.css")) if (
        FRONTEND / "shared" / "search-field.css").exists() else ""

    def test_rest_is_borderless_and_recessed_with_a_muted_icon(self):
        rest = _rule(self.css, ".search-field")
        self.assertRegex(rest, r"border:\s*none")
        # One step below the --surface-2 every field sits on.
        self.assertRegex(rest, r"background-color:\s*var\(--surface-1\)")
        self.assertRegex(_rule(self.css, ".search-field-icon"), r"color:\s*var\(--text-muted\)")

    def test_hover_lifts_one_step_lighter_with_the_soft_shadow(self):
        hover = _rule(self.css, ".search-field:hover")
        self.assertRegex(hover, r"background-color:\s*var\(--surface-input\)")
        self.assertRegex(hover, r"box-shadow:\s*var\(--field-lift\);")

    def test_focus_is_the_lift_plus_a_halo_and_an_accent_icon(self):
        focus = _rule(self.css, ".search-field:has(:focus-visible)")
        self.assertRegex(focus, r"background-color:\s*var\(--surface-input\)")
        self.assertRegex(
            focus,
            r"box-shadow:\s*var\(--field-lift\),\s*0 0 0 var\(--field-halo-width\) var\(--field-halo\)",
        )
        self.assertRegex(_rule(self.css, ".search-field:has(:focus-visible) .search-field-icon"),
                         r"color:\s*var\(--accent\)")
        # The halo takes the outline's place: no part of the field draws its own ring.
        self.assertRegex(_rule(self.css, ".search-field :focus-visible"), r"outline:\s*none")

    def test_hover_and_focus_ease_on_the_fast_duration(self):
        rest = _rule(self.css, ".search-field")
        for prop in ("background-color", "box-shadow"):
            self.assertRegex(rest, rf"{prop} var\(--dur-fast\) var\(--ease-out\)")

    def test_clear_shows_only_while_the_field_has_text(self):
        self.assertRegex(_rule(self.css, ".search-field-clear"), r"display:\s*none")
        self.assertRegex(
            _rule(self.css,
                  ".search-field:has(.search-field-input:not(:placeholder-shown)) .search-field-clear"),
            r"display:\s*flex",
        )

    def test_listening_pulses_a_voice_halo_and_shows_the_mic(self):
        listening = _rule(self.css, ".search-field.is-listening")
        self.assertIn("var(--field-halo-voice)", listening)
        self.assertRegex(listening,
                         r"animation:\s*search-field-listening var\(--dur-pulse\)[^;]*infinite")
        self.assertRegex(_rule(self.css, ".search-field.is-listening .search-field-mic"),
                         r"display:\s*flex")
        mic = _rule(self.css, ".search-field-mic")
        self.assertRegex(mic, r"color:\s*var\(--voice\)")
        self.assertRegex(mic, r"display:\s*none")

    def test_listening_rests_on_a_steady_halo_so_reduced_motion_has_no_pulse(self):
        # theme.css's reduced-motion rule plays every animation once, instantly.
        # The pulse only changes the halo mid-cycle, so what is left is the
        # steady halo of the rule above, with nothing moving.
        keyframes = re.search(r"@keyframes search-field-listening\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(keyframes)
        self.assertNotRegex(keyframes.group(1), r"(?m)^\s*(from|to|0%|100%)\b")


class TokenTests(unittest.TestCase):
    def test_halo_is_a_soft_three_pixel_ring(self):
        theme = _read("shared", "theme.css")
        self.assertRegex(theme, r"--field-halo-width:\s*3px;")
        self.assertRegex(theme, r"--field-halo:\s*color-mix\(in srgb, var\(--accent\) "
                                r"var\(--field-halo-mix\), transparent\);")
        self.assertRegex(theme, r"--field-halo-voice:\s*color-mix\(in srgb, var\(--voice\) "
                                r"var\(--field-halo-mix\), transparent\);")

    def test_dark_halo_is_stronger(self):
        for theme, mix in (("light", "20%"), ("sepia", "20%"), ("dark", "30%")):
            with self.subTest(theme):
                self.assertRegex(_theme_block(theme), rf"--field-halo-mix:\s*{mix};")

    def test_dark_lifts_with_a_top_highlight_not_a_drop_shadow(self):
        lift = re.search(r"--field-lift:\s*([^;]+);", _theme_block("dark")).group(1)
        self.assertTrue(lift.startswith("inset 0 1px 0"), lift)
        self.assertNotIn(",", lift.replace("in srgb,", "").split("color-mix")[0])

    def test_light_and_sepia_lift_with_their_own_soft_shadow(self):
        for theme in ("light", "sepia"):
            with self.subTest(theme):
                self.assertRegex(_theme_block(theme), r"--field-lift:\s*var\(--shadow-1\);")
        # Sepia's --shadow-1 is tinted warm (its text colour), not grey.
        self.assertRegex(_theme_block("sepia"), r"--shadow-1:[^;]*rgba\(59, 47, 32")

    def test_pulse_is_slow(self):
        self.assertRegex(_read("shared", "theme.css"), r"--dur-pulse:\s*1\.8s;")


class ShortcutTests(unittest.TestCase):
    def test_ctrl_f_focuses_the_folder_search_in_the_open_and_save_dialogs(self):
        js = _read("js", "file-browser.js")
        handler = re.search(r'document\.addEventListener\("keydown", \(ev\) => \{(.*?)\n  \}\);', js, re.S)
        self.assertIsNotNone(handler)
        body = handler.group(1)
        self.assertIn("ctrlKey", body)
        self.assertIn('"f"', body)
        self.assertIn("searchEl.focus()", body)

    def test_no_shortcut_hint_inside_any_field(self):
        for parts, input_id in FIELDS:
            with self.subTest(input_id):
                markup = _field_markup(parts, input_id)
                field = markup[:markup.index("</div>")]
                self.assertNotRegex(field, r"<kbd|Ctrl\+F|⌘F")

    def test_dictation_uses_the_shared_listening_state(self):
        js = _read("pages", "command-reference.js")
        self.assertIn("setSearchFieldListening(referenceSearch, true)", js)
        self.assertIn("setSearchFieldListening(referenceSearch, false)", js)
        self.assertNotIn('"dictating"', js)
        markup = _field_markup(("pages", "command-reference.js"), "referenceSearch")
        self.assertRegex(markup, r'class="search-field-mic"[^>]*data-icon="mic"')


HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

class Event { constructor(type, init = {}) { this.type = type; this.bubbles = Boolean(init.bubbles); } }

function element(cls, parent) {
  const el = {
    classList: {
      set: new Set(cls ? [cls] : []),
      add(c) { this.set.add(c); },
      remove(c) { this.set.delete(c); },
      contains(c) { return this.set.has(c); },
      toggle(c, on) { (on === undefined ? !this.set.has(c) : on) ? this.set.add(c) : this.set.delete(c); },
    },
    parent, children: [], dataset: {}, events: [], focused: false,
    closest(sel) {
      for (let n = this; n; n = n.parent) if (n.classList.contains(sel.slice(1))) return n;
      return null;
    },
    querySelector(sel) {
      for (const child of this.children) {
        if (child.classList.contains(sel.slice(1))) return child;
        const deeper = child.querySelector(sel);
        if (deeper) return deeper;
      }
      return null;
    },
    dispatchEvent(ev) { this.events.push(ev.type); },
    focus() { this.focused = true; },
  };
  if (parent) parent.children.push(el);
  return el;
}

const listeners = {};
const document = { addEventListener: (type, fn) => { listeners[type] = fn; } };
const source = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({ document, Event });
vm.runInContext(source + "\nthis.api = { setSearchFieldListening, SEARCH_FIELD_LISTENING_PLACEHOLDER };", context);
const api = context.api;

function field(value, placeholder = "Search commands") {
  const wrap = element("search-field");
  const input = element("search-field-input", wrap);
  input.value = value;
  input.placeholder = placeholder;
  const clear = element("search-field-clear", wrap);
  const glyph = element(null, clear);
  return { wrap, input, clear, glyph };
}

const results = {};
const check = (name, fn) => {
  try { results[name] = fn() === true; } catch (err) { results[name] = `threw: ${err.message}`; }
};

check("the clear button empties the field, tells its owner and refocuses it", () => {
  const f = field("annual");
  listeners.click({ target: f.clear });
  return f.input.value === "" && f.input.events.join() === "input" && f.input.focused;
});

check("a click on the clear button's icon counts as the button", () => {
  const f = field("annual");
  listeners.click({ target: f.glyph });
  return f.input.value === "";
});

check("a click anywhere else leaves every field alone", () => {
  const f = field("annual");
  listeners.click({ target: f.input });
  listeners.click({ target: element("other") });
  return f.input.value === "annual" && f.input.events.length === 0;
});

check("listening marks the field and says so in the placeholder", () => {
  const f = field("");
  api.setSearchFieldListening(f.input, true);
  return f.wrap.classList.contains("is-listening") && f.input.placeholder === "Listening…"
    && api.SEARCH_FIELD_LISTENING_PLACEHOLDER === "Listening…";
});

check("leaving listening puts the field's own placeholder back", () => {
  const f = field("", "Search commands");
  api.setSearchFieldListening(f.input, true);
  api.setSearchFieldListening(f.input, false);
  return !f.wrap.classList.contains("is-listening") && f.input.placeholder === "Search commands";
});

check("starting to listen twice still restores the original placeholder", () => {
  const f = field("", "Search commands");
  api.setSearchFieldListening(f.input, true);
  api.setSearchFieldListening(f.input, true);
  api.setSearchFieldListening(f.input, false);
  return f.input.placeholder === "Search commands";
});

check("stopping a field that was not listening changes nothing", () => {
  const f = field("", "Search commands");
  api.setSearchFieldListening(f.input, false);
  return f.input.placeholder === "Search commands" && !f.wrap.classList.contains("is-listening");
});

console.log(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class SearchFieldBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Node harness failed:\n{proc.stderr}")
        cls.results = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_every_check_passes(self):
        failures = {name: res for name, res in self.results.items() if res is not True}
        self.assertEqual(failures, {})
        self.assertEqual(len(self.results), 7)


if __name__ == "__main__":
    unittest.main()
