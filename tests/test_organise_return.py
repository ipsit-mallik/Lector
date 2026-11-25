"""Organise pages started from Home goes back there (reading-save.js):
Home's note is read once and dropped, Done's changes are applied and
"Save changes?" asked at once, and then Home opens on the section Organise
was started from, scrolled where it was; cancelling the question comes back
into Organise. Organise started in the reader stays in the reader.

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

  // Started from Tools, Done with changes, "Don't save": back to Tools.
  reset({ note: JSON.stringify({ section: "tools", top: 420 }), changed: true, dirty: true });
  run("takeOrganiseReturn(true)");
  out.taken = { back: run("organiseReturn"), left: store["lector-organise-return"] || null };
  await run("leaveOrganiseForHome()");
  out.doneDiscard = snapshot();

  // From Favorites, Done with changes, Save: saved, then back.
  reset({ note: JSON.stringify({ section: "favorites", top: 0 }), changed: true, dirty: true,
    reply: { action: "save", mode: "overwrite", remember: false } });
  run("takeOrganiseReturn(true)");
  await run("leaveOrganiseForHome()");
  out.doneSave = snapshot();

  // From Recent, the question cancelled after the changes were applied: the
  // reader reloads into Organise, still bound for Home.
  reset({ note: JSON.stringify({ section: "recent", top: 96 }), changed: true, dirty: true, reply: { action: "cancel" } });
  run("takeOrganiseReturn(true)");
  await run("leaveOrganiseForHome()");
  out.doneCancelled = snapshot();

  // Cancelled with nothing reloaded (no page change): Organise opens again.
  reset({ note: JSON.stringify({ section: "recent", top: 96 }), changed: false, dirty: true, reply: { action: "cancel" } });
  run("takeOrganiseReturn(true)");
  await run("leaveOrganiseForHome()");
  out.cancelledNoReload = snapshot();

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

    def test_done_applies_asks_at_once_then_goes_back_to_the_same_home_page(self):
        done = self.out["doneDiscard"]
        self.assertEqual(done["log"], ["apply", "get_save_behavior", "set_voice_context", "ask:true", "set_voice_context"])
        self.assertEqual(done["href"], "../index.html")
        self.assertEqual(done["store"], {"lector-home-section": "tools", "lector-home-scroll": "420"})

    def test_save_saves_then_goes_back(self):
        saved = self.out["doneSave"]
        self.assertIn("perform_save", saved["log"])
        self.assertEqual(saved["href"], "../index.html")
        # Scrolled to the top: no scroll note.
        self.assertEqual(saved["store"], {"lector-home-section": "favorites"})

    def test_cancelling_the_question_comes_back_into_organise(self):
        cancelled = self.out["doneCancelled"]
        self.assertEqual(cancelled["href"], "pages/reading.html")
        self.assertIn("reload", cancelled["log"])
        self.assertIn('notes:1|{"section":"recent","top":96}', cancelled["log"])
        quiet = self.out["cancelledNoReload"]
        self.assertEqual(quiet["href"], "pages/reading.html")
        self.assertEqual(quiet["log"][-1], "reopen")
        self.assertNotIn("reload", quiet["log"])

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
