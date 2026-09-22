// Push-to-talk wiring, shared by the Home and Reading views.
//
// Hold a key, capture audio, get recognized text back. There is no wake
// phrase and no always-on listening yet (Milestone 8).
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
 * Wire push-to-talk to the document and report state changes.
 *
 * @param {(state: {state: string, text: string, error: ?string}) => void} onState
 *   Called with one of:
 *     {state: "unavailable", error}  — no model, or no microphone
 *     {state: "idle"}                — ready, not listening
 *     {state: "listening", text}     — key held; `text` firms up as you speak
 *     {state: "heard", text, command} — key released, final phrase (may be
 *                                       ""); `command` is the parsed intent,
 *                                       or null if nothing matched
 * @param {() => (Array<{page_index: number, y0: number, y1: number}> |
 *   Promise<Array<{page_index: number, y0: number, y1: number}>>)} [getViewport]
 *   Called on each key-down to get the currently visible page regions, in PDF
 *   points, so Python can widen the recognizer's vocabulary to what the
 *   reader can actually see (docs/PRD.md's viewport-scoped matching). Home
 *   has no document open and no viewport to report, so this defaults to an
 *   empty one rather than requiring every caller to supply it.
 */
function initPushToTalk(onState, getViewport = () => []) {
  let held = false;
  let available = false;

  const report = (state, text = "", error = null, command = null) =>
    onState({ state, text, error, command });

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
          report("idle");
        } else {
          report("unavailable", "", status.error);
        }
      })
      .catch((err) => report("unavailable", "", String(err)));
  };
  askStatus();

  // Partial and final results arriving from Python's worker thread.
  window.addEventListener("lector:voice", (ev) => {
    const { text, final, command } = ev.detail || {};
    if (final) {
      report("heard", text || "", null, command || null);
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
      report("listening", text || "");
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== PUSH_TO_TALK_KEY) return;
    if (!available || shouldIgnoreKey() || dialogIsOpen()) return;
    // Space scrolls the reading view by default; holding it to talk must not
    // also page the document down.
    ev.preventDefault();
    if (held) return; // Key auto-repeat, not a second press.
    held = true;
    report("listening", "");
    Promise.resolve(getViewport())
      .then((viewport) => callApi("start_listening", viewport || []))
      .catch((err) => {
        held = false;
        report("unavailable", "", String(err));
      });
  });

  document.addEventListener("keyup", (ev) => {
    if (ev.key !== PUSH_TO_TALK_KEY || !held) return;
    held = false;
    ev.preventDefault();
    callApi("stop_listening").catch((err) => report("unavailable", "", String(err)));
  });

  // Losing the window mid-hold never delivers the keyup, which would leave
  // the microphone open indefinitely.
  window.addEventListener("blur", () => {
    if (!held) return;
    held = false;
    callApi("stop_listening").catch(() => {});
  });
}
