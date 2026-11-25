"""Organise pages' picker from the Tools page (pages/tools-page.js's
`PdfPicker`): Continue is one-shot, and a PDF that has opened is never
cancelled after the fact.

- Continue is busy from the first click: a second click doesn't open the PDF
  again.
- Esc and Cancel while the PDF opens, and once it has (the picker stays up
  until the reader replaces the page), do nothing: the PDF is the reader's
  from then on.
- A PDF that couldn't be opened gives Continue back.
- Hiding the picker cancels any animation still holding its scrim, so it is
  visible the next time it opens (an earlier fade-out held it at opacity 0).

The real scripts run under Node with the stand-in DOM (tests/dom_standin.py).
Skipped when Node is not installed.
"""
import shutil
import unittest
from pathlib import Path

from dom_standin import run_node

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SCRIPTS = [FRONTEND / "js" / "dialog-motion.js", FRONTEND / "js" / "dialog-steps.js", FRONTEND / "pages" / "tools-page.js"]

HARNESS = r"""
const recent = ["C:/Docs/Report.pdf", "C:/Docs/Notes.pdf"].map((path) => ({
  path, name: path.split("/").pop().replace(".pdf", ""), page_count: 4, thumbnail: null,
}));
const ctx = context({
  callApi: async (method) => (method === "get_recent_files" ? recent : {}),
});
load(ctx, process.argv.slice(2));
const PdfPicker = vm.runInContext("PdfPicker", ctx);
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

(async () => {
  const out = {};
  const pid = "pdfPickerScrim";
  const scrim = () => document.getElementById(pid);
  const button = (label) => scrim().querySelector(".dialog-footer").children.find((b) => b.textContent === label);
  const accepted = [];
  let answer = null;
  const choose = () => PdfPicker.choose("organise", {
    accept: (path) => { accepted.push(path); return new Promise((resolve) => { answer = resolve; }); },
  });

  // A PDF that couldn't be opened: Continue comes back.
  const first = choose();
  await settle();
  button("Continue").click();
  button("Opening…").click();
  out.oneShot = { accepted: accepted.length, busy: button("Opening…").disabled };
  answer(false);
  await settle();
  out.refused = { open: !scrim().hidden, label: Boolean(button("Continue")), enabled: !button("Continue").disabled };

  // One that opens: Esc while it opens, and while the dialog fades, do nothing.
  button("Continue").click();
  keydown("Escape");
  button("Opening…").click();
  out.whileOpening = { open: !scrim().hidden, accepted: accepted.length };
  answer(true);
  out.chosen = await first;
  keydown("Escape");
  out.afterOpen = { open: !scrim().hidden, fades: scrim().getAnimations().length,
    busy: Boolean(button("Opening…")) };

  // Something holding the scrim at opacity 0 (as the old fade-out did) is
  // cancelled with the dialog, so it opens visible again.
  scrim().animate([{ opacity: 1 }, { opacity: 0 }], { duration: 1, fill: "forwards" });
  await settle();
  const second = choose();
  await settle();
  out.reopened = { open: !scrim().hidden, held: scrim().getAnimations().length };
  keydown("Escape");
  out.cancelled = await second;

  process.stdout.write(JSON.stringify(out));
})().catch((err) => { console.error(err.stack); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OrganisePickerContinueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(HARNESS, SCRIPTS)

    def test_continue_is_one_shot(self):
        self.assertEqual(self.out["oneShot"], {"accepted": 1, "busy": True})

    def test_a_pdf_that_couldnt_open_gives_continue_back(self):
        self.assertEqual(self.out["refused"], {"open": True, "label": True, "enabled": True})

    def test_esc_never_cancels_a_pdf_that_is_opening_or_has_opened(self):
        self.assertEqual(self.out["whileOpening"], {"open": True, "accepted": 2})
        self.assertEqual(self.out["chosen"], "C:/Docs/Report.pdf")
        # Up until the reader takes over: no fade to a bare Home.
        self.assertEqual(self.out["afterOpen"], {"open": True, "fades": 0, "busy": True})

    def test_an_animation_holding_the_scrim_is_cancelled_with_the_dialog(self):
        self.assertEqual(self.out["reopened"], {"open": True, "held": 0})
        self.assertIsNone(self.out["cancelled"])


if __name__ == "__main__":
    unittest.main()
