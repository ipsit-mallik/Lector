// Focus mode: the topbar, the toolbar and the footer (every `.focus-chrome`
// element, with their dividers) fade out together and give their room to the
// page; Esc, F11 or "exit focus mode" brings them back. F11, the topbar's
// button or "focus mode" turns it on. The progress bar's collapsed line and
// the outline panel are not chrome here: they stay as they are.
//
// While it is on, the pointer at the top edge of the window brings the topbar
// back down over the page (`.is-focus-peek`), with the button showing the way
// out; it goes back up once the pointer has left it.
//
// The mic pill goes with the footer, so while a command is being captured or
// worked out (the "listening" state, from key-down or wake to the final
// result) a copy of it floats at the bottom of the window instead.
//
// Nothing is remembered: the reader page loads afresh for every PDF opened,
// and this starts off every time.
//
// reading.js hands this file the scroller in use, so the text being read
// stays where it was on screen while the topbar's room is added or removed.

const readingView = document.querySelector(".reading-view");
const focusTopbar = document.querySelector(".topbar");
const focusModeBtn = document.getElementById("focusModeBtn");
const focusVoicePill = document.getElementById("focusVoicePill");
const focusVoiceLabel = document.getElementById("focusVoiceLabel");

// The fade, if the token can't be read.
const FOCUS_FADE_FALLBACK_MS = 120;
// How near the top of the window the pointer brings the topbar down, and how
// long it stays once the pointer has left it.
const FOCUS_PEEK_EDGE_PX = 8;
const FOCUS_PEEK_HIDE_MS = 300;
// Said on the way in, however it was turned on, so the way out is never a
// secret; taken back early if Focus mode ends first.
const FOCUS_MODE_TOAST = "Focus mode — press Esc or say “exit focus mode”";
const FOCUS_MODE_TOAST_MS = 3000;

let focusHooks = null;
let focusOn = false;
let focusFadeTimer = null;
let dismissFocusToast = null;
let focusPeekTimer = null;
let focusPeekHovered = false;

// hooks: {
//   scroller()   the page scroller showing now (book or strip)
//   holdsPeek()  whether something opened from the topbar (the zoom slider)
//                needs it to stay down
// }
function configureFocusMode(hooks) {
  focusHooks = hooks;
}

function isFocusMode() {
  return focusOn;
}

function focusFadeMs() {
  const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dur-fast"));
  return Number.isFinite(value) ? value : FOCUS_FADE_FALLBACK_MS;
}

// Adds or takes away the chrome's room while keeping what is on screen still:
// the scroller grows or shrinks at the top by the topbar's height, so its
// scroll offset moves by the same amount.
function changeFocusLayout(apply) {
  const scroller = focusHooks && focusHooks.scroller();
  const before = scroller ? scroller.getBoundingClientRect().top : 0;
  apply();
  if (!scroller) return;
  const shift = scroller.getBoundingClientRect().top - before;
  if (shift) scroller.scrollTop += shift;
}

// On: the chrome fades (`.is-focus-fading`), then leaves the layout
// (`.is-focus-mode`). Off: it comes back into the layout still transparent,
// then fades in. Switching again mid-fade picks up from wherever it is.
function setFocusMode(on) {
  if (on === focusOn) return;
  focusOn = on;
  clearTimeout(focusFadeTimer);
  setFocusPeek(false);
  renderFocusButton();
  if (dismissFocusToast) dismissFocusToast();
  dismissFocusToast = on ? showToast(FOCUS_MODE_TOAST, { durationMs: FOCUS_MODE_TOAST_MS }) : null;
  if (on) {
    readingView.classList.add("is-focus-fading");
    focusFadeTimer = setTimeout(() => {
      changeFocusLayout(() => readingView.classList.add("is-focus-mode"));
    }, focusFadeMs());
  } else {
    changeFocusLayout(() => readingView.classList.remove("is-focus-mode"));
    // The chrome has to be laid out at opacity 0 for one frame, or there is
    // nothing for the fade to start from.
    void readingView.offsetHeight;
    readingView.classList.remove("is-focus-fading");
  }
}

function toggleFocusMode() {
  setFocusMode(!focusOn);
}

// The button says which way it goes: into Focus mode, or, in the topbar
// brought back over the page, out of it.
function renderFocusButton() {
  const icon = focusOn ? "exit_focus_mode" : "focus_mode";
  focusModeBtn.classList.toggle("active", focusOn);
  focusModeBtn.setAttribute("aria-pressed", String(focusOn));
  focusModeBtn.title = focusOn ? "Exit focus mode  (Esc)" : "Focus mode  (F11)";
  if (focusModeBtn.dataset.icon === icon) return;
  focusModeBtn.dataset.icon = icon;
  mountIcon(focusModeBtn, icon);
}

function isFocusPeek() {
  return readingView.classList.contains("is-focus-peek");
}

// It comes down under the pointer, so it starts out hovered.
function setFocusPeek(on) {
  clearTimeout(focusPeekTimer);
  focusPeekHovered = on && focusOn;
  readingView.classList.toggle("is-focus-peek", on && focusOn);
}

// Takes the topbar back up after a moment, unless the pointer is on it or the
// zoom slider opened from it is still open. reading.js calls this again when
// the slider closes.
function settleFocusPeek() {
  clearTimeout(focusPeekTimer);
  if (!isFocusPeek() || focusPeekHovered) return;
  if (focusHooks && focusHooks.holdsPeek && focusHooks.holdsPeek()) return;
  focusPeekTimer = setTimeout(() => setFocusPeek(false), FOCUS_PEEK_HIDE_MS);
}

// By where the pointer is rather than enter/leave events: the bar slides in
// under a pointer that may not move again until it is well below it. Not
// with a button held, so a drag on the progress bar up to the top doesn't
// bring the bar down over it.
document.addEventListener("pointermove", (ev) => {
  if (!focusOn) return;
  const atEdge = ev.clientY < FOCUS_PEEK_EDGE_PX;
  if (!isFocusPeek()) {
    if (atEdge && ev.buttons === 0) setFocusPeek(true);
    return;
  }
  const wasHovered = focusPeekHovered;
  focusPeekHovered = atEdge || focusTopbar.contains(ev.target);
  if (focusPeekHovered) clearTimeout(focusPeekTimer);
  else if (wasHovered) settleFocusPeek();
});

// Called with every mic state (reading.js's renderMicState). Only shows in
// Focus mode (reading.css); outside it the footer's own pill says the same.
function showFocusVoice(micState, text) {
  const capturing = micState === "listening";
  if (capturing) focusVoiceLabel.textContent = text ? `"${text}"` : "Listening...";
  else if (micState === "heard" && text) focusVoiceLabel.textContent = `"${text}"`;
  focusVoicePill.classList.toggle("is-showing", capturing);
}
