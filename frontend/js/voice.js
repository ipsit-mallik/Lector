// Voice activation wiring, shared by the Home and Reading views.
//
// Two ways in, per docs/PRD.md's "dual voice activation":
//
//   * push-to-talk — hold Space, and the microphone is open only while it is
//     held (Milestone 5);
//   * wake phrase — say "Hey Lector" and a short command window opens
//     (Milestone 8).
//
// They are independent: either, both, or neither may be enabled, and the
// reader's choice lives in settings.json. With neither enabled the app is
// still completely usable, which is the point of the parity requirement.
// Python owns the microphone in both cases, so this file never decides
// *whether* a wake happened — it is told.
//
// Nothing here interprets what was said: Python parses the phrase into an
// intent (src/lector/features/voice/command_grammar.py) and sends it along
// with the text. This file only re-broadcasts it as a `lector:command`
// event that each view subscribes to — the reading view acts on navigation
// and highlight intents alike, without either needing to know about the
// other.
//
// Python pushes results the other way, as a `lector:voice` CustomEvent
// dispatched from src/lector/api.py's `_on_voice_result`.

const PUSH_TO_TALK_KEY = " "; // Space, per docs/PRD.md's stated default.

// Holding Space while a button or a text field has focus must not start
// listening: Space is that control's own activation/typing key, and
// docs/PRD.md's mouse/keyboard-parity requirement means the traditional
// control has to keep working exactly as it did.
const TYPING_TAGS = ["INPUT", "TEXTAREA", "SELECT"];

// How long to wait before re-asking whether the speech model has finished
// loading. Short enough that the indicator settles while the reader is still
// looking at the page, long enough not to spin the bridge.
const STATUS_RETRY_MS = 300;

function shouldIgnoreKey() {
  const el = document.activeElement;
  if (!el) return false;
  if (TYPING_TAGS.includes(el.tagName)) return true;
  if (el.isContentEditable) return true;
  if (el.tagName === "BUTTON" || el.tagName === "A") return true;
  return false;
}

// Whether a modal is on screen. A dialog owns the keyboard while it is open,
// so push-to-talk stands down rather than competing with it.
function dialogIsOpen() {
  return Boolean(document.querySelector(".dialog-scrim:not([hidden])"));
}

/**
 * Wire voice activation to the document and report state changes.
 *
 * @param {(state: {state: string, text: string, error: ?string,
 *   command: ?object, clarify: ?object, wake: boolean, pushToTalk: boolean,
 *   viaWake: boolean}) => void} onState
 *   Called with one of:
 *     {state: "unavailable", error}   — no model, or no microphone
 *     {state: "off"}                  — voice works, but the reader has both
 *                                       activation modes switched off
 *     {state: "idle", wake, pushToTalk} — ready; `wake` says whether the
 *                                       microphone is listening for the
 *                                       phrase right now
 *     {state: "listening", text, viaWake} — capturing a command; `text` firms
 *                                       up as you speak, and `viaWake` says
 *                                       whether a wake opened the window (no
 *                                       key is being held) or Space did
 *     {state: "heard", text, command, clarify} — final phrase (may be "");
 *                                       `command` is the parsed intent, or
 *                                       null if nothing matched; `clarify`,
 *                                       when non-null, is a near-miss worth
 *                                       asking "did you mean '<candidate>'?"
 *                                       about instead (Milestone 8.3)
 * @param {() => (Array<{page_index: number, y0: number, y1: number}> |
 *   Promise<Array<{page_index: number, y0: number, y1: number}>>)} [getViewport]
 *   Called on each key-down to get the currently visible page regions, in PDF
 *   points, so Python can widen the recognizer's vocabulary to what the
 *   reader can actually see (docs/PRD.md's viewport-scoped matching). Home
 *   has no document open and no viewport to report, so this defaults to an
 *   empty one rather than requiring every caller to supply it.
 * @returns {{rest: () => void}} `rest()` settles the indicator back to its
 *   resting state without the caller having to know which of "idle", "off"
 *   or "unavailable" currently applies.
 */
function initVoice(onState, getViewport = () => []) {
  let held = false;
  let available = false;
  let viaWake = false;
  // Assumed until settings answer. Optimistic on purpose: the alternative is
  // a moment in which a held Space does nothing, which reads as a broken
  // microphone rather than as a value still loading.
  let activation = { push_to_talk: true, wake_phrase: false };

  const report = (state, extra = {}) =>
    onState({
      state,
      text: "",
      error: null,
      command: null,
      clarify: null,
      wake: activation.wake_phrase,
      pushToTalk: activation.push_to_talk,
      viaWake: false,
      ...extra,
    });

  // Ready, off, or unavailable — the resting state, which depends both on
  // whether voice *can* work and on whether the reader wants it to.
  const reportResting = () => {
    if (!available) return;
    if (!activation.push_to_talk && !activation.wake_phrase) {
      report("off");
      return;
    }
    report("idle");
  };

  // The speech model takes a few seconds to load and does so in the
  // background (see `VoiceEngine.warm_up`), so the first answer can be
  // provisional. Ask again rather than committing the indicator to "ready"
  // or "unavailable" on it — a load that fails late would otherwise leave
  // the pill claiming voice works right up until the reader tries it.
  const askStatus = () => {
    callApi("get_voice_status")
      .then((status) => {
        if (status.loading) {
          setTimeout(askStatus, STATUS_RETRY_MS);
          return;
        }
        available = status.available;
        if (available) {
          reportResting();
        } else {
          report("unavailable", { error: status.error });
        }
      })
      .catch((err) => report("unavailable", { error: String(err) }));
  };

  // Which modes the reader has enabled. Asked for before the status so that
  // the first resting state is already worded correctly, rather than
  // announcing "Hold Space to talk" to someone who turned that off.
  callApi("get_voice_activation")
    .then((modes) => {
      activation = modes;
    })
    .catch(() => {
      // Keep the optimistic default: a settings read that failed is no
      // reason to take voice away from someone who has it.
    })
    .finally(askStatus);

  // Partial and final results arriving from Python's worker thread.
  window.addEventListener("lector:voice", (ev) => {
    const { text, final, command, clarify, wake } = ev.detail || {};
    // A wake carries no text — it is the moment the phrase was recognized
    // and the command window opened. Everything after it, up to the final,
    // belongs to that window.
    if (wake) viaWake = true;
    if (final) {
      report("heard", { text: text || "", command: command || null, clarify: clarify || null, viaWake });
      viaWake = false;
      // Re-broadcast for feature code. Deliberately a separate event from
      // `lector:voice`: that one is the raw transport, this one is the
      // "a command was spoken" signal features act on, and only final
      // results qualify.
      //
      // Dispatched even when `command` is null, so that a view can tell
      // "heard something, understood nothing" apart from "heard nothing"
      // and say so, rather than leaving the reader wondering whether the
      // microphone worked.
      if (text) {
        window.dispatchEvent(
          new CustomEvent("lector:command", { detail: { text, command: command || null } }),
        );
      }
    } else {
      report("listening", { text: text || "", viaWake });
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== PUSH_TO_TALK_KEY) return;
    if (!available || !activation.push_to_talk) return;
    if (shouldIgnoreKey() || dialogIsOpen()) return;
    // Space scrolls the reading view by default; holding it to talk must not
    // also page the document down.
    ev.preventDefault();
    if (held) return; // Key auto-repeat, not a second press.
    held = true;
    viaWake = false; // A key in hand outranks any window a wake left open.
    report("listening", { text: "" });
    Promise.resolve(getViewport())
      .then((viewport) => callApi("start_listening", viewport || []))
      .catch((err) => {
        held = false;
        report("unavailable", { error: String(err) });
      });
  });

  document.addEventListener("keyup", (ev) => {
    if (ev.key !== PUSH_TO_TALK_KEY || !held) return;
    held = false;
    ev.preventDefault();
    callApi("stop_listening").catch((err) => report("unavailable", { error: String(err) }));
  });

  // Losing the window mid-hold never delivers the keyup, which would leave
  // the microphone open indefinitely.
  window.addEventListener("blur", () => {
    if (!held) return;
    held = false;
    callApi("stop_listening").catch(() => {});
  });

  return { rest: reportResting };
}
