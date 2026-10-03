// Auto-hide logic for scrollbars — the only script that touches scrollbar
// state. The styling is shared/scrollbars.css; this file just toggles the
// `scrollbars-active` class on <html> so that *every* scrollbar in the app
// shows together on any activity and fades together once things go quiet.
//
// What counts as activity: mouse move, wheel, click, key press, touch, scroll
// (any element — scroll doesn't bubble, so it is observed in the capture
// phase) and focus changes, plus voice, but only real voice events:
//
//   * `lector:voice` with `wake` set — the wake phrase was detected;
//   * `lector:voice` with non-empty `text` — a transcript (partial or final)
//     arrived, which only happens during an actual capture;
//   * `lector:command` — a spoken command was executed.
//
// The push-to-talk key arrives as an ordinary keydown. Idle wake-phrase
// listening emits no events at all (engine.py only emits on a wake or a
// capture), and there is no mic-level event; neither may count, or scrollbars
// would never hide.
//
// Forced-colors (Windows High Contrast) never hides them: the thumb is kept
// visible there, see the matching block in shared/scrollbars.css.

const SCROLLBAR_ACTIVE_CLASS = "scrollbars-active";
const SCROLLBAR_HIDE_DELAY_MS = 1500;
const SCROLLBAR_ACTIVITY_EVENTS = [
  "mousemove",
  "wheel",
  "mousedown",
  "click",
  "keydown",
  "touchstart",
  "scroll",
  "focusin",
];

/**
 * @param {object} [env] Injectable for tests; defaults to the real browser.
 * @param {Window} [env.win]
 * @param {{classList: DOMTokenList}} [env.root] The <html> element.
 * @param {() => number} [env.now]
 * @param {(fn: () => void, ms: number) => any} [env.setTimer]
 * @param {number} [env.hideDelayMs]
 */
function initScrollbarAutoHide(env = {}) {
  const win = env.win || window;
  const root = env.root || document.documentElement;
  const now = env.now || (() => performance.now());
  const setTimer = env.setTimer || ((fn, ms) => setTimeout(fn, ms));
  const hideDelayMs = env.hideDelayMs ?? SCROLLBAR_HIDE_DELAY_MS;

  const forcedColors = win.matchMedia ? win.matchMedia("(forced-colors: active)") : null;
  const neverHide = () => Boolean(forcedColors && forcedColors.matches);

  let visible = false;
  let lastActivity = 0;
  let timer = null;

  const setVisible = (next) => {
    if (next === visible) return;
    visible = next;
    if (next) root.classList.add(SCROLLBAR_ACTIVE_CLASS);
    else root.classList.remove(SCROLLBAR_ACTIVE_CLASS);
  };

  // One timer at a time: rather than clearing and re-arming it on every
  // mousemove, record the time and let the pending timer re-check how much of
  // the delay is left when it fires.
  const checkIdle = () => {
    timer = null;
    if (neverHide()) return;
    const remaining = hideDelayMs - (now() - lastActivity);
    if (remaining > 0) {
      timer = setTimer(checkIdle, remaining);
      return;
    }
    setVisible(false);
  };

  const markActive = () => {
    lastActivity = now();
    setVisible(true);
    if (timer === null && !neverHide()) timer = setTimer(checkIdle, hideDelayMs);
  };

  for (const type of SCROLLBAR_ACTIVITY_EVENTS) {
    win.addEventListener(type, markActive, { capture: true, passive: true });
  }

  win.addEventListener("lector:voice", (ev) => {
    const detail = ev.detail || {};
    if (detail.wake || detail.text) markActive();
  });
  win.addEventListener("lector:command", markActive);

  if (forcedColors) {
    if (forcedColors.addEventListener) {
      forcedColors.addEventListener("change", () => {
        if (neverHide()) setVisible(true);
        else markActive();
      });
    }
    if (neverHide()) setVisible(true);
  }
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  initScrollbarAutoHide();
}
