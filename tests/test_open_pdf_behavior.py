"""Behaviour of frontend/pages/password-dialog.js and open-pdf.js (hardening H5).

`openPdf(path)` is how every screen opens a PDF. A PDF that can't be opened
shows `Api.open_pdf`'s own sentence and the screen stays put; a missing one
goes to the caller's `onMissing` (Home offers "Remove from Recent"); a
password-protected one opens the password box, which keeps asking until the
password is right or the reader cancels.

The password box: focus starts in the field and Enter submits it (it is a
form, not a yes/no question); Open is disabled while the field is empty or a
password is being tried; a wrong password puts a line under the field and
selects what was typed; Cancel, Escape and a spoken "cancel" close it; the
field is emptied and hidden again whenever it closes; the file name is set
as text, never markup; it takes its own voice scope while open.

Run under Node like tests/test_replace_dialog_behavior.py, with a small fake
DOM. Skipped when Node is not installed.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "frontend" / "pages"
DIALOG = PAGES / "password-dialog.js"
OPEN_PDF = PAGES / "open-pdf.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(id, extra = {}) {
  const listeners = {};
  const attrs = {};
  return Object.assign({
    id, hidden: false, textContent: "", value: "", type: "", checked: false,
    disabled: false, readOnly: false, selected: false,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    fire(type, ev = {}) { (listeners[type] || []).forEach((fn) => fn(ev)); },
    focus() { document.activeElement = this; },
    select() { this.selected = true; },
    setAttribute(name, value) { attrs[name] = String(value); },
    removeAttribute(name) { delete attrs[name]; },
    getAttribute(name) { return name in attrs ? attrs[name] : null; },
  }, extra);
}

const els = {
  passwordDialog: element("passwordDialog", { hidden: true }),
  passwordCard: element("passwordCard"),
  passwordDesc: element("passwordDesc"),
  passwordInput: element("passwordInput", { type: "password" }),
  passwordError: element("passwordError", { hidden: true }),
  passwordShow: element("passwordShow"),
  passwordCancelBtn: element("passwordCancelBtn"),
  passwordOpenBtn: element("passwordOpenBtn", { disabled: true, textContent: "Open" }),
};
let injected = 0;
const docListeners = {};
const document = {
  activeElement: null,
  body: { insertAdjacentHTML() { injected += 1; } },
  getElementById: (id) => els[id] || null,
  addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
};
const scopeLog = [];
const toasts = [];
let answers = [];
const calls = [];
const context = vm.createContext({
  document,
  console: { error() {} },
  mountIcons: () => Promise.resolve(),
  createVoiceScope: (name) => ({
    enter() { scopeLog.push(`enter:${name}`); return Promise.resolve(true); },
    leave() { scopeLog.push(`leave:${name}`); return Promise.resolve(); },
  }),
  showToast: (message, options) => { toasts.push({ message, options }); },
  callApi: (method, ...args) => {
    calls.push([method, ...args]);
    const next = answers.shift();
    return typeof next === "function" ? next(...args) : Promise.resolve(next);
  },
});
const [dialogPath, openPath] = process.argv.slice(1);
vm.runInContext(fs.readFileSync(dialogPath, "utf8") + "\nthis.PasswordDialog = PasswordDialog;", context);
vm.runInContext(fs.readFileSync(openPath, "utf8") + "\nthis.openPdf = openPdf; this.OPEN_FAILED = OPEN_FAILED;", context);
const dialog = context.PasswordDialog;
const { passwordDialog: scrim, passwordInput: input, passwordError: error,
        passwordOpenBtn: openBtn, passwordCancelBtn: cancelBtn, passwordShow: show,
        passwordCard: card } = els;

const tick = () => new Promise((resolve) => setImmediate(resolve));
const type = (text) => { input.value = text; input.fire("input"); };
const submit = () => {
  let prevented = false;
  card.fire("submit", { preventDefault() { prevented = true; } });
  return prevented;
};
const pressEscape = () => (docListeners.keydown || []).forEach((fn) => fn({ key: "Escape" }));
const opener = element("opener");
const reset = () => { scopeLog.length = 0; toasts.length = 0; calls.length = 0; answers = []; };

function ask(attempt = async () => ({ done: "opened" })) {
  document.activeElement = opener;
  return dialog.ask({ name: "Tax <b>2025</b>.pdf", attempt });
}

const results = {};
const check = async (name, fn) => {
  reset();
  try {
    results[name] = (await fn()) === true || "assertion false";
  } catch (err) {
    results[name] = `threw: ${err.message}`;
  }
  if (dialog.isOpen()) cancelBtn.fire("click");
};

(async () => {
  await check("the markup is not added again to a page that already has it", async () => injected === 0);

  await check("asking shows the name as text, focuses the field and takes the voice scope", async () => {
    const pending = ask();
    const shown = !scrim.hidden && dialog.isOpen()
      && els.passwordDesc.textContent === "“Tax <b>2025</b>.pdf” is protected by a password."
      && document.activeElement === input
      && openBtn.disabled && error.hidden
      && scopeLog.join() === "enter:password_prompt";
    cancelBtn.fire("click");
    return shown && (await pending) === null;
  });

  await check("Open is enabled only once something is typed", async () => {
    ask();
    const before = openBtn.disabled;
    type("x");
    const typed = !openBtn.disabled;
    type("");
    return before && typed && openBtn.disabled;
  });

  await check("Enter with an empty field tries nothing", async () => {
    let tried = 0;
    ask(async () => { tried += 1; return { done: 1 }; });
    const prevented = submit();
    await tick();
    return prevented && tried === 0 && dialog.isOpen();
  });

  await check("a wrong password shows the line under the field and selects the text", async () => {
    let tried = null;
    ask(async (password) => { tried = password; return { error: "The password isn't right. Try again." }; });
    type("guess");
    submit();
    await tick();
    return tried === "guess" && dialog.isOpen()
      && !error.hidden && error.textContent === "The password isn't right. Try again."
      && input.getAttribute("aria-invalid") === "true"
      && input.getAttribute("aria-describedby") === "passwordError"
      && input.selected && document.activeElement === input
      && !openBtn.disabled && openBtn.textContent === "Open" && !input.readOnly;
  });

  await check("typing again clears the error", async () => {
    ask(async () => ({ error: "The password isn't right. Try again." }));
    type("guess");
    submit();
    await tick();
    type("guess2");
    return error.hidden && error.textContent === "" && input.getAttribute("aria-invalid") === null
      && input.getAttribute("aria-describedby") === null;
  });

  await check("the right password resolves with the result, empties the field and gives everything back", async () => {
    const pending = ask(async () => ({ done: { is_open: true } }));
    type("right one");
    submit();
    const result = await pending;
    return result && result.is_open === true && scrim.hidden && input.value === ""
      && scopeLog.join() === "enter:password_prompt,leave:password_prompt"
      && document.activeElement === opener;
  });

  await check("while a password is being tried, Open is busy and a second Enter is ignored", async () => {
    let tried = 0;
    let finish;
    ask(() => { tried += 1; return new Promise((resolve) => { finish = resolve; }); });
    type("pw");
    submit();
    const busy = openBtn.disabled && openBtn.textContent === "Opening…" && input.readOnly;
    submit();
    finish({ error: "The password isn't right. Try again." });
    await tick();
    return busy && tried === 1 && !openBtn.disabled && !input.readOnly;
  });

  await check("while a password is being tried it can't be cancelled, and its answer still applies", async () => {
    // A right password opens the PDF: cancelling then would leave the screen
    // on the old one while Lector holds the new one.
    let finish;
    const pending = ask(() => new Promise((resolve) => { finish = resolve; }));
    type("pw");
    submit();
    const cancelDisabled = cancelBtn.disabled;
    cancelBtn.fire("click");
    pressEscape();
    dialog.handleCommand({ intent: "CANCEL" });
    const stillOpen = dialog.isOpen();
    finish({ done: { is_open: true } });
    const result = await pending;
    return cancelDisabled && stillOpen && result && result.is_open === true
      && scrim.hidden && !cancelBtn.disabled;
  });

  await check("once a wrong password is answered, Cancel works again", async () => {
    let finish;
    const pending = ask(() => new Promise((resolve) => { finish = resolve; }));
    type("pw");
    submit();
    finish({ error: "The password isn't right. Try again." });
    await tick();
    const enabled = !cancelBtn.disabled;
    cancelBtn.fire("click");
    return enabled && (await pending) === null;
  });

  await check("Cancel, Escape and a spoken cancel all resolve null and empty the field", async () => {
    const outcomes = [];
    for (const close of [() => cancelBtn.fire("click"), pressEscape,
                         () => dialog.handleCommand({ intent: "CANCEL" })]) {
      const pending = ask();
      type("half typed");
      close();
      outcomes.push((await pending) === null && scrim.hidden && input.value === "");
    }
    return outcomes.every(Boolean);
  });

  await check("any other spoken command leaves it open", async () => {
    ask();
    dialog.handleCommand({ intent: "NEXT_PAGE" });
    return dialog.isOpen();
  });

  await check("Show password reveals the text, and closing hides it again", async () => {
    ask();
    show.checked = true;
    show.fire("change");
    const shown = input.type === "text";
    cancelBtn.fire("click");
    return shown && input.type === "password" && show.checked === false;
  });

  await check("Escape while closed does nothing", async () => {
    pressEscape();
    return scrim.hidden && scopeLog.length === 0;
  });

  // --- openPdf ---------------------------------------------------------- //

  await check("a PDF that opens resolves true with no toast", async () => {
    answers = [{ is_open: true }];
    return (await context.openPdf("D:\\a.pdf")) === true && toasts.length === 0
      && JSON.stringify(calls) === JSON.stringify([["open_pdf", "D:\\a.pdf"]]);
  });

  await check("one that can't be opened shows the backend's sentence and resolves false", async () => {
    answers = [{ error: "Couldn't open the file. It's damaged or isn't a PDF.", reason: "not_a_pdf" }];
    const opened = await context.openPdf("D:\\a.pdf");
    return opened === false && toasts.length === 1
      && toasts[0].message === "Couldn't open the file. It's damaged or isn't a PDF.";
  });

  await check("a missing one goes to onMissing when given, else to a toast", async () => {
    const missing = { error: "The PDF couldn't be found. It may have been moved or deleted.", reason: "missing" };
    const told = [];
    answers = [missing, missing];
    const first = await context.openPdf("D:\\gone.pdf", { onMissing: (m) => told.push(m) });
    const second = await context.openPdf("D:\\gone.pdf");
    return first === false && second === false && told.join() === missing.error
      && toasts.length === 1 && toasts[0].message === missing.error;
  });

  await check("a rejected call shows the general sentence, never its own text", async () => {
    answers = [() => Promise.reject(new Error("code=2: cannot open file 'D:\\\\a.pdf'"))];
    const opened = await context.openPdf("D:\\a.pdf");
    return opened === false && toasts.length === 1 && toasts[0].message === context.OPEN_FAILED
      && context.OPEN_FAILED === "Couldn't open the PDF. It may be damaged or no longer there.";
  });

  await check("a protected PDF asks, says when the password is wrong, and opens with the right one", async () => {
    answers = [
      { error: "The PDF is protected by a password.", reason: "password" },
      { error: "The password isn't right. Try again.", reason: "wrong_password" },
      { is_open: true },
    ];
    const pending = context.openPdf("D:\\Docs\\locked.pdf");
    await tick();
    const asked = dialog.isOpen()
      && els.passwordDesc.textContent === "“locked.pdf” is protected by a password.";
    type("wrong");
    submit();
    await tick();
    const toldWrong = dialog.isOpen() && error.textContent === "The password isn't right. Try again.";
    type("right");
    submit();
    const opened = await pending;
    return asked && toldWrong && opened === true && !dialog.isOpen() && toasts.length === 0
      && JSON.stringify(calls) === JSON.stringify([
        ["open_pdf", "D:\\Docs\\locked.pdf"],
        ["open_pdf", "D:\\Docs\\locked.pdf", "wrong"],
        ["open_pdf", "D:\\Docs\\locked.pdf", "right"],
      ]);
  });

  await check("cancelling the password box resolves false with no toast", async () => {
    answers = [{ error: "The PDF is protected by a password.", reason: "password" }];
    const pending = context.openPdf("D:\\locked.pdf");
    await tick();
    pressEscape();
    return (await pending) === false && toasts.length === 0 && calls.length === 1;
  });

  await check("a protected PDF that fails for another reason once unlocked shows that reason", async () => {
    answers = [
      { error: "The PDF is protected by a password.", reason: "password" },
      { error: "The PDF has no pages to show.", reason: "no_pages" },
    ];
    const pending = context.openPdf("D:\\locked.pdf");
    await tick();
    type("right");
    submit();
    return (await pending) === false && !dialog.isOpen()
      && toasts.length === 1 && toasts[0].message === "The PDF has no pages to show.";
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class OpenPdfBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", HARNESS, str(DIALOG), str(OPEN_PDF)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(f"harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_scenario_passes(self):
        self.assertGreaterEqual(len(self.results), 21)
        for name, outcome in self.results.items():
            with self.subTest(scenario=name):
                self.assertIs(outcome, True, outcome)


class PageWiringTests(unittest.TestCase):
    """Every screen that opens a PDF does it through openPdf, loads the
    password box first, and lets it hear "cancel"."""

    PAGES = {
        ROOT / "frontend" / "index.html": ("pages/password-dialog.js", "pages/open-pdf.js", "pages/home.js"),
        PAGES / "reading.html": ("password-dialog.js", "open-pdf.js", "reading.js"),
        PAGES / "settings.html": ("password-dialog.js", "open-pdf.js", "settings.js"),
    }

    def test_each_page_loads_the_dialog_and_open_pdf_before_its_own_script(self):
        for html_path, names in self.PAGES.items():
            with self.subTest(page=html_path.name):
                html = html_path.read_text(encoding="utf-8")
                positions = [html.index(f'src="{name}"') for name in names]
                self.assertEqual(positions, sorted(positions))

    def test_no_page_calls_open_pdf_itself_or_keeps_the_old_toast(self):
        for script in ("home.js", "reading.js", "settings.js"):
            with self.subTest(script=script):
                js = (PAGES / script).read_text(encoding="utf-8")
                self.assertNotIn('callApi("open_pdf"', js)
                self.assertIn("openPdf(path", js)
                self.assertNotIn("that PDF", js)
                self.assertIn("PasswordDialog.handleCommand(command)", js)

    def test_home_offers_to_remove_a_missing_recent_file_through_the_usual_confirmation(self):
        js = (PAGES / "home.js").read_text(encoding="utf-8")
        self.assertIn('actionLabel: "Remove from Recent", onAction: () => removeEntry(entry)', js)
        self.assertIn('homeSection === "recent"', js)

    def test_the_dialog_uses_the_shared_frame_and_a_real_password_field(self):
        js = DIALOG.read_text(encoding="utf-8")
        for needle in ('class="dialog-scrim dialog-scrim--enter voice-dialog"', 'role="dialog"',
                       'aria-modal="true"', 'data-icon="lock"', 'class="dialog-title"',
                       'type="password"', 'autocomplete="off"', 'class="dialog-field-error"',
                       'class="btn btn-secondary"', 'createVoiceScope("password_prompt")'):
            with self.subTest(needle=needle):
                self.assertIn(needle, js)
        self.assertTrue((ROOT / "frontend" / "shared" / "icons" / "lock.svg").is_file())
        # The file name is only ever set as text.
        self.assertNotIn("innerHTML", js)
        self.assertIsNone(re.search(r"\$\{name\}`;\s*$", js.split("PASSWORD_MARKUP")[1].split("</div>`")[0]))

    def test_the_password_is_not_kept_anywhere_on_the_page(self):
        for script in (DIALOG, OPEN_PDF):
            with self.subTest(script=script.name):
                js = script.read_text(encoding="utf-8")
                self.assertNotIn("sessionStorage", js)
                self.assertNotIn("localStorage", js)
                self.assertNotRegex(js, r"console\.\w+\([^)]*password")


if __name__ == "__main__":
    unittest.main()
