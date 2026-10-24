"""Behaviour of frontend/pages/replace-dialog.js (hardening H2).

"Save a copy" onto a name that is already taken asks "Replace this file?"
first. These scenarios hold its contract with `promptSaveIfDirty`: it resolves
true only for Replace (button or "confirm replace"), false for Cancel, Escape
or a spoken "cancel"; it takes its own voice scope while open and gives it
back on close; focus goes to the card, not a button, and back to where it was
afterwards; and the file name and folder are shown as text, never markup.

Run under Node like tests/test_voice_scope_behavior.py, with a small fake DOM.
Skipped when Node is not installed.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "replace-dialog.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(id) {
  const listeners = {};
  return {
    id, hidden: id === "replaceConfirmScrim", textContent: "", focused: 0,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    fire(type, ev = {}) { (listeners[type] || []).forEach((fn) => fn(ev)); },
    focus() { this.focused += 1; document.activeElement = this; },
    querySelector() { return card; },
  };
}

const card = element("card");
const ids = ["replaceConfirmScrim", "replaceConfirmTitle", "replaceConfirmDesc",
  "replaceConfirmBtn", "replaceConfirmCancelBtn"];
const els = Object.fromEntries(ids.map((id) => [id, element(id)]));
const docListeners = {};
const document = {
  activeElement: null,
  getElementById: (id) => els[id],
  addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
};
const scopeLog = [];
const context = vm.createContext({
  document,
  createVoiceScope: (name) => ({
    enter() { scopeLog.push(`enter:${name}`); return Promise.resolve(true); },
    leave() { scopeLog.push(`leave:${name}`); return Promise.resolve(); },
  }),
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + "\nthis.ReplaceDialog = ReplaceDialog;", context);
const dialog = context.ReplaceDialog;
const scrim = els.replaceConfirmScrim;
const pressEscape = () => (docListeners.keydown || []).forEach((fn) => fn({ key: "Escape" }));

const results = {};
const check = async (name, fn) => {
  try {
    results[name] = (await fn()) === true || "assertion false";
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
};
const opener = element("opener");

async function openAndAnswer(answer) {
  document.activeElement = opener;
  const pending = dialog.open({ name: "report (highlighted).pdf", folder: "D:\\Docs" });
  answer();
  return pending;
}

(async () => {
  await check("opening shows the name and folder and focuses the card, not a button", async () => {
    scopeLog.length = 0;
    document.activeElement = opener;
    const pending = dialog.open({ name: "a <b>.pdf", folder: "D:\\Docs" });
    const shown = !scrim.hidden && dialog.isOpen()
      && els.replaceConfirmTitle.textContent === "Replace “a <b>.pdf”?"
      && els.replaceConfirmDesc.textContent.includes("already exists in D:\\Docs")
      && document.activeElement === card
      && scopeLog.join() === "enter:replace_confirm";
    els.replaceConfirmCancelBtn.fire("click");
    await pending;
    return shown;
  });

  await check("Replace resolves true, closes, gives the scope back and restores focus", async () => {
    scopeLog.length = 0;
    const answer = await openAndAnswer(() => els.replaceConfirmBtn.fire("click"));
    return answer === true && scrim.hidden && !dialog.isOpen()
      && scopeLog.join() === "enter:replace_confirm,leave:replace_confirm"
      && document.activeElement === opener;
  });

  await check("Cancel resolves false", async () => {
    return (await openAndAnswer(() => els.replaceConfirmCancelBtn.fire("click"))) === false && scrim.hidden;
  });

  await check("Escape resolves false", async () => {
    return (await openAndAnswer(pressEscape)) === false && scrim.hidden;
  });

  await check("a spoken confirm replace resolves true", async () => {
    const answer = await openAndAnswer(() => dialog.handleCommand({ intent: "CONFIRM_REPLACE" }));
    return answer === true && scrim.hidden;
  });

  await check("a spoken cancel resolves false", async () => {
    const answer = await openAndAnswer(() => dialog.handleCommand({ intent: "CANCEL" }));
    return answer === false && scrim.hidden;
  });

  await check("any other command leaves the question open", async () => {
    document.activeElement = opener;
    const pending = dialog.open({ name: "x.pdf", folder: "D:\\" });
    dialog.handleCommand({ intent: "NEXT_PAGE" });
    const stillOpen = dialog.isOpen();
    els.replaceConfirmCancelBtn.fire("click");
    return stillOpen && (await pending) === false;
  });

  await check("Escape while closed does nothing", async () => {
    scopeLog.length = 0;
    pressEscape();
    return scrim.hidden && scopeLog.length === 0;
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class ReplaceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 8)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


class ReadingWiringTests(unittest.TestCase):
    """The reading page loads the dialog and routes its answers."""

    ROOT = Path(__file__).resolve().parents[1] / "frontend" / "pages"

    def test_the_page_loads_the_dialog_before_reading_js(self):
        html = (self.ROOT / "reading.html").read_text(encoding="utf-8")
        self.assertIn('id="replaceConfirmScrim"', html)
        self.assertLess(html.index('src="replace-dialog.js"'), html.index('src="reading.js"'))

    def test_a_copy_onto_a_taken_name_is_asked_about_and_failures_show_the_plain_message(self):
        js = (self.ROOT / "reading.js").read_text(encoding="utf-8")
        self.assertIn('callApi("describe_save_target"', js)
        self.assertIn("ReplaceDialog.open(", js)
        self.assertIn("ReplaceDialog.handleCommand(command)", js)
        self.assertIn("alert(outcome.error)", js)
        self.assertNotIn("Could not save the PDF:\\n", js)


if __name__ == "__main__":
    unittest.main()
