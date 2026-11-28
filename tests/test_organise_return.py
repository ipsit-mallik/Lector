"""Organise pages started from Home goes back there on Cancel
(reading-save.js): Home's note is read once and dropped, and Home opens on
the section Organise was started from, scrolled where it was. Done never
goes there (organise.js keeps it in the reader; tests/test_organise_behavior.py).
If the PDF has unsaved changes after all, Cancel asks the usual question
first, and cancelling it stays in the reader. Organise started in the
reader stays in the reader.

reading-save.js runs under Node with the reader's other parts stubbed
(tests/dom_standin.py). Skipped when Node is not installed.
"""
import shutil
import unittest
from pathlib import Path

from dom_standin import run_node

READING_SAVE_JS = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "reading-save.js"

HARNESS = r"""
const store = {};
const sessionStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
const log = [];
let organiseOpen = false;
let applied = { state: null, changed: false };
let answer = { action: "discard" };
let refuse = false;
const state = { is_dirty: false };

const ctx = context({
  sessionStorage, state,
  callApi: async (method) => {
    log.push(method);
    if (method === "get_save_behavior") return "ask";
    if (method === "perform_save") return { ok: true, state: { is_dirty: false } };
    return {};
  },
  isOrganiseOpen: () => organiseOpen,
  applyOrganiseBeforeLeaving: async () => { log.push("apply"); organiseOpen = false; return applied; },
  SaveDialog: { open: async (options) => { log.push(`ask:${options.allowDiscard}`); return answer; } },
  runPageChange: async () => {
    log.push("reload");
    log.push(`notes:${sessionStorage.getItem("lector-open-organise")}|${sessionStorage.getItem("lector-organise-return")}`);
  },
  adoptState: (s) => s,
  isPageChangeBusy: () => false,
  openOrganise: async () => { log.push("reopen"); if (refuse) return false; organiseOpen = true; return true; },
  clearOrganisePlaceholder: () => log.push("placeholder-gone"),
  alert: (t) => log.push(`alert:${t}`),
});
load(ctx, process.argv.slice(2));
const run = (expr) => vm.runInContext(expr, ctx);

function reset({ note, open = true, changed = false, dirty = false, reply = { action: "discard" } }) {
  for (const k of Object.keys(store)) delete store[k];
  if (note !== undefined) store["lector-organise-return"] = note;
  log.length = 0;
  window.location.href = "pages/reading.html";
  organiseOpen = open;
  applied = { state: { is_dirty: dirty }, changed };
  state.is_dirty = dirty;
  answer = reply;
}

const snapshot = () => ({ href: window.location.href, store: { ...store }, log: log.slice() });

(async () => {
  const out = {};

  // Started from Tools, Cancel with the PDF somehow unsaved, "Don't save":
  // back to Tools.
  reset({ note: JSON.stringify({ section: "tools", top: 420 }), open: false, dirty: true });
  run("takeOrganiseReturn(true)");
  out.taken = { back: run("organiseReturn"), left: store["lector-organise-return"] || null };
  await run("leaveOrganiseForHome()");
  out.cancelDiscard = snapshot();

  // From Favorites, the same with Save: saved, then back.
  reset({ note: JSON.stringify({ section: "favorites", top: 0 }), open: false, dirty: true,
    reply: { action: "save", mode: "overwrite", remember: false } });
  run("takeOrganiseReturn(true)");
  await run("leaveOrganiseForHome()");
  out.cancelSave = snapshot();

  // The question cancelled: the reader stays, and forgets the way home.
  reset({ note: JSON.stringify({ section: "recent", top: 96 }), open: false, dirty: true, reply: { action: "cancel" } });
  run("takeOrganiseReturn(true)");
  await run("leaveOrganiseForHome()");
  out.cancelStays = { ...snapshot(), back: run("organiseReturn") };

  // Leaving another way with Organise open and changes in it, the question
  // cancelled: the reader reloads onto the changed pages, not into Organise.
  reset({ note: JSON.stringify({ section: "recent", top: 96 }), changed: true, dirty: true, reply: { action: "cancel" } });
  run("takeOrganiseReturn(true)");
  out.reloadStays = { stayed: await run("promptSaveIfDirty(true)"), ...snapshot(), back: run("organiseReturn") };

  // Cancel (the draft already dropped): nothing to save, straight home.
  reset({ note: JSON.stringify({ section: "recent", top: 96 }), open: false });
  run("takeOrganiseReturn(true)");
  await run("leaveOrganiseForHome()");
  out.cancel = snapshot();

  // Started from Home, then left another way (Ctrl+O, the window closing)
  // and the question cancelled: Organise has closed, and a later Organise
  // from the ⋯ menu stays in the reader.
  reset({ note: JSON.stringify({ section: "tools", top: 5 }), dirty: true, reply: { action: "cancel" } });
  run("takeOrganiseReturn(true)");
  out.otherWay = { stayed: await run("promptSaveIfDirty(true)"), back: run("organiseReturn") };

  // Organise wasn't asked for (the reader's own ⋯ menu later): an old note
  // is dropped and nothing goes home.
  reset({ note: JSON.stringify({ section: "tools", top: 5 }) });
  run("takeOrganiseReturn(false)");
  out.stale = { back: run("organiseReturn"), left: store["lector-organise-return"] || null };
  reset({ note: "not json" });
  run("takeOrganiseReturn(true)");
  out.unreadable = run("organiseReturn");
  reset({ note: JSON.stringify({ section: "settings", top: 5 }) });
  run("takeOrganiseReturn(true)");
  out.unknownSection = run("organiseReturn");

  // Sent from Home, but Organise couldn't open (refused, or the PDF's owner
  // forbids changes): the way back to Home goes with it.
  reset({ note: JSON.stringify({ section: "tools", top: 5 }), open: false });
  run("takeOrganiseReturn(true)");
  refuse = true;
  await run("startOrganise()");
  refuse = false;
  out.refused = run("organiseReturn");
  reset({ note: JSON.stringify({ section: "tools", top: 5 }), open: false });
  run("takeOrganiseReturn(true)");
  state.organise_blocked = "The owner of this PDF doesn't allow changes.";
  await run("startOrganise()");
  delete state.organise_blocked;
  out.blocked = run("organiseReturn");
  reset({ note: JSON.stringify({ section: "tools", top: 5 }), open: false });
  run("takeOrganiseReturn(true)");
  await run("startOrganise()");
  out.opened = run("organiseReturn");

  process.stdout.write(JSON.stringify(out));
})().catch((err) => { console.error(err.stack); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OrganiseReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(HARNESS, [READING_SAVE_JS])

    def test_an_organise_that_couldnt_open_no_longer_goes_home(self):
        self.assertIsNone(self.out["refused"])
        self.assertIsNone(self.out["blocked"])
        self.assertEqual(self.out["opened"], {"section": "tools", "top": 5})

    def test_homes_note_is_read_once(self):
        self.assertEqual(self.out["taken"], {"back": {"section": "tools", "top": 420}, "left": None})

    def test_cancel_with_unsaved_changes_asks_then_goes_back_to_the_same_home_page(self):
        left = self.out["cancelDiscard"]
        self.assertEqual(left["log"], ["get_save_behavior", "set_voice_context", "ask:true", "set_voice_context"])
        self.assertEqual(left["href"], "../index.html")
        # Back on the Tools page with the focus on the Organise pages card.
        self.assertEqual(left["store"], {"lector-home-section": "tools", "lector-home-scroll": "420",
                                         "lector-home-focus": "organise"})

    def test_save_saves_then_goes_back(self):
        saved = self.out["cancelSave"]
        self.assertIn("perform_save", saved["log"])
        self.assertEqual(saved["href"], "../index.html")
        # Scrolled to the top: no scroll note.
        self.assertEqual(saved["store"], {"lector-home-section": "favorites"})

    def test_cancelling_the_question_stays_in_the_reader_and_forgets_home(self):
        stays = self.out["cancelStays"]
        self.assertEqual(stays["href"], "pages/reading.html")
        self.assertIsNone(stays["back"])
        self.assertNotIn("reopen", stays["log"])
        self.assertNotIn("reload", stays["log"])

    def test_staying_with_changed_pages_reloads_onto_them_not_into_organise(self):
        stays = self.out["reloadStays"]
        self.assertFalse(stays["stayed"])
        self.assertIn("reload", stays["log"])
        self.assertIn("notes:null|null", stays["log"])
        self.assertIsNone(stays["back"])

    def test_cancel_goes_straight_home_to_recent_with_its_scroll(self):
        cancel = self.out["cancel"]
        self.assertEqual(cancel["log"], [])
        self.assertEqual(cancel["href"], "../index.html")
        # Recent is Home's own start: only the scroll travels.
        self.assertEqual(cancel["store"], {"lector-home-scroll": "96"})

    def test_staying_after_leaving_another_way_drops_the_way_home(self):
        self.assertEqual(self.out["otherWay"], {"stayed": False, "back": None})

    def test_a_note_nobody_asked_for_or_cant_read_goes_nowhere(self):
        self.assertEqual(self.out["stale"], {"back": None, "left": None})
        self.assertIsNone(self.out["unreadable"])
        self.assertIsNone(self.out["unknownSection"])


class ClosingAfterDontSaveTests(unittest.TestCase):
    """After "Don't save", Home (or Settings) is shown while Python still holds
    the document with its changes, so closing the window asks the page
    `promptSaveIfDirty(true)` (bridge/window.py). The reader asked already:
    these pages must answer "close", or the call fails and the window can
    never be closed (pywebview drops the callback of a script that throws)."""

    def test_home_and_settings_answer_close(self):
        pages = Path(__file__).resolve().parents[1] / "frontend" / "pages"
        for name in ("home.js", "settings.js"):
            with self.subTest(page=name):
                source = (pages / name).read_text(encoding="utf-8")
                self.assertRegex(source, r"async function promptSaveIfDirty\(\) \{\s*return true;\s*\}")


if __name__ == "__main__":
    unittest.main()
