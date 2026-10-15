// Focus mode: the topbar, the toolbar and the footer (every `.focus-chrome`
// element, with their dividers) fade out together and give their room to the
// page; Esc, F11 or "exit focus mode" brings them back. F11 or "focus mode"
// turns it on. The progress bar's collapsed line and the outline panel are not
// chrome here: they stay as they are.
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
const focusVoicePill = document.getElementById("focusVoicePill");
const focusVoiceLabel = document.getElementById("focusVoiceLabel");

// The fade, if the token can't be read.
const FOCUS_FADE_FALLBACK_MS = 120;

let focusHooks = null;
let focusOn = false;
let focusFadeTimer = null;

// hooks: {
//   scroller()  the page scroller showing now (book or strip)
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

// Called with every mic state (reading.js's renderMicState). Only shows in
// Focus mode (reading.css); outside it the footer's own pill says the same.
function showFocusVoice(micState, text) {
  const capturing = micState === "listening";
  if (capturing) focusVoiceLabel.textContent = text ? `"${text}"` : "Listening...";
  else if (micState === "heard" && text) focusVoiceLabel.textContent = `"${text}"`;
  focusVoicePill.classList.toggle("is-showing", capturing);
}
