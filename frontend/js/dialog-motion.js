// How the file tools' dialogs move (docs/DESIGN_SYSTEM.md § "File tools" →
// "Dialog motion"): one smooth height change for anything that changes the
// card's height, the cross-fade between a stepped dialog's steps, and another
// dialog opened over one (the file browser, Save-As, "Replace?", a
// password) without the one under it going anywhere.
//
// Height. The card never jumps: its height is locked in px, the content
// changes, the new height is read once, and the card eases from the one to
// the other over --dur-base on --ease-size, its Web Animations clock
// started once the frame showing the change has painted (`afterPaint`: a
// requestAnimationFrame in the next frame's), so neither the change's own
// work nor that frame's drawing can eat into the motion. Afterwards it is released to `auto`. Content that arrives
// later (a preview, estimates, a list growing) goes through the same path:
// `changeDialogContent` when the dialog knows it is changing, and, for
// anything it doesn't (an icon, a font), a ResizeObserver on the card's
// parts (`smoothDialogHeight`). A change while the card is already moving
// retargets it from where it is. Under prefers-reduced-motion nothing
// animates.
//
// Steps. The old step's look is copied into an inert, aria-hidden overlay
// on the card's surface (`.dialog-step-ghost`), absolutely placed so it
// doesn't count in the card's height and can't take focus, and fades out
// over 120ms while the new step, already in place underneath, slides 12px
// in the direction of travel. Together they always add up to a full card:
// no frame is blank.
//
// Stacking. `stackDialog` runs another dialog over this one: this one stays
// mounted and visible, made inert, its dim the only one on screen (the
// dialog over it shows no dim of its own, so the dim's opacity never
// changes); closing that one gives focus back to the control that opened it.
"use strict";

const DIALOG_FADE_MS = 120;
const DIALOG_SHIFT_PX = 12;
const DIALOG_MOTION_DEFAULTS = { "--dur-base": 200 };
const DIALOG_EASE_SIZE_DEFAULT = "cubic-bezier(0.2, 0, 0, 1)";
const DIALOG_EASE_OUT_DEFAULT = "cubic-bezier(0.2, 0.8, 0.2, 1)";
// Height differences below this are layout rounding, not a change.
const DIALOG_HEIGHT_EPSILON_PX = 0.5;

function dialogMotionReduced() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function dialogToken(name) {
  const style = typeof getComputedStyle === "function" ? getComputedStyle(document.documentElement) : null;
  return style && typeof style.getPropertyValue === "function" ? style.getPropertyValue(name).trim() : "";
}

function dialogTokenMs(name) {
  const raw = dialogToken(name);
  const value = parseFloat(raw);
  if (!Number.isFinite(value)) return DIALOG_MOTION_DEFAULTS[name];
  return raw.endsWith("ms") ? value : value * 1000;
}

const dialogFrame = (fn) => (typeof requestAnimationFrame === "function" ? requestAnimationFrame(fn) : setTimeout(fn, 0));
const dialogCancelFrame = (id) =>
  typeof cancelAnimationFrame === "function" ? cancelAnimationFrame(id) : clearTimeout(id);

// `fn` in the frame after the next one has painted: the first frame with
// new content can take a long time to draw (measured: the first tool step
// after launch spent 225-350 ms rasterising on the GPU), and a motion whose
// clock started before it would be most of the way through by the time the
// next frame shows: one jump. Started after that frame, it runs whole.
// Returns a handle for `cancelAfterPaint`.
function afterPaint(fn) {
  const handle = { id: 0 };
  handle.id = dialogFrame(() => {
    handle.id = dialogFrame(fn);
  });
  return handle;
}

function cancelAfterPaint(handle) {
  if (handle) dialogCancelFrame(handle.id);
}

// ---- Height ---------------------------------------------------------------

// Per card: { anim, frame, locked, target, last, width, observer }.
const _dialogHeights = new WeakMap();

function dialogHeightState(card) {
  let state = _dialogHeights.get(card);
  if (!state) {
    state = { anim: null, frame: 0, locked: false, target: null, last: null, width: null, observer: null };
    _dialogHeights.set(card, state);
  }
  return state;
}

// The card's parts that are laid out in it (not the step's fading copy or
// the screen-reader status, which are placed absolutely).
function dialogParts(card) {
  return Array.from(card.children).filter(
    (child) => !child.classList.contains("dialog-step-ghost") && !child.classList.contains("dialog-step-status")
  );
}

function dialogShown(card) {
  return card.isConnected && card.getBoundingClientRect().height > 0;
}

// Stop the card's height motion where it is now, and let it size itself.
function releaseDialogHeight(card) {
  const state = dialogHeightState(card);
  cancelAfterPaint(state.frame);
  state.frame = 0;
  if (state.anim) state.anim.cancel();
  state.anim = null;
  state.locked = false;
  card.style.height = "";
  card.classList.remove("is-resizing");
}

// Ease the card from `from` to `to` (px). With `now`, the motion starts in
// this frame (a retarget mid-motion); otherwise once the frame showing the
// change has painted (`afterPaint`), the card held at `from` until then.
function animateDialogHeight(card, from, to, { now = false } = {}) {
  const state = dialogHeightState(card);
  cancelAfterPaint(state.frame);
  state.frame = 0;
  if (state.anim) state.anim.cancel();
  state.anim = null;
  state.target = to;
  if (dialogMotionReduced() || typeof card.animate !== "function" || Math.abs(to - from) < DIALOG_HEIGHT_EPSILON_PX) {
    state.locked = false;
    state.last = to;
    card.style.height = "";
    card.classList.remove("is-resizing");
    return;
  }
  state.locked = true;
  card.style.height = `${from}px`;
  card.classList.add("is-resizing");
  const start = () => {
    state.frame = 0;
    const anim = card.animate([{ height: `${from}px` }, { height: `${to}px` }], {
      duration: dialogTokenMs("--dur-base"),
      easing: dialogToken("--ease-size") || DIALOG_EASE_SIZE_DEFAULT,
    });
    state.anim = anim;
    anim.finished.then(
      () => {
        if (state.anim !== anim) return;
        state.anim = null;
        state.locked = false;
        state.last = to;
        card.style.height = "";
        card.classList.remove("is-resizing");
      },
      () => {}
    );
  };
  if (now) start();
  else state.frame = afterPaint(start);
}

// The card's content changes (`apply`): one smooth height change from its
// height now (mid-motion, wherever it has got to) to the content's. Reads
// layout twice: the height before, and the height after.
function changeDialogContent(card, apply) {
  if (dialogMotionReduced() || !dialogShown(card)) {
    releaseDialogHeight(card);
    apply();
    dialogHeightState(card).last = null;
    return;
  }
  const from = card.getBoundingClientRect().height;
  releaseDialogHeight(card);
  apply();
  const to = card.getBoundingClientRect().height;
  animateDialogHeight(card, from, to);
}

// The bottom of the card's laid-out parts: its height as its content would
// have it, read while the card itself is held at another height (a block
// card's parts don't depend on its height).
function dialogContentHeight(card) {
  let bottom = 0;
  for (const part of dialogParts(card)) bottom = Math.max(bottom, part.offsetTop + part.offsetHeight);
  return bottom;
}

// Watch the card for height changes nobody announced (an icon, a font, a
// late line of text) and smooth them the same way. Once per card.
function smoothDialogHeight(card) {
  const state = dialogHeightState(card);
  if (state.observer || typeof ResizeObserver !== "function") return;
  const flex = getComputedStyle(card).display === "flex";
  state.observer = new ResizeObserver(() => {
    if (!dialogShown(card)) {
      state.last = null;
      return;
    }
    const rect = card.getBoundingClientRect();
    if (state.last === null || state.width !== rect.width) {
      // Just shown, or the window changed width: nothing to move from.
      state.last = state.locked ? state.target : rect.height;
      state.width = rect.width;
      return;
    }
    if (dialogMotionReduced()) {
      state.last = rect.height;
      return;
    }
    if (state.locked) {
      // Moving already. A flex card's parts follow its held height, so
      // what they'd want is only known once it is released (then the
      // observer runs again, at rest); a block card's is known now.
      if (flex) return;
      const natural = dialogContentHeight(card);
      if (Math.abs(natural - state.target) > DIALOG_HEIGHT_EPSILON_PX) {
        animateDialogHeight(card, rect.height, natural, { now: true });
      }
      return;
    }
    // At rest: the card has just taken its new height, not painted yet.
    // Hold it at the old one and ease across.
    if (Math.abs(rect.height - state.last) > DIALOG_HEIGHT_EPSILON_PX) animateDialogHeight(card, state.last, rect.height);
  });
  for (const part of dialogParts(card)) state.observer.observe(part);
}

// ---- Steps --------------------------------------------------------------

// The cross-fade a card's last swap started, so the next can finish it.
const _stepSwaps = new WeakMap();

function finishStepSwap(card) {
  const running = _stepSwaps.get(card);
  _stepSwaps.delete(card);
  if (!running) return;
  cancelAfterPaint(running.frame);
  for (const animation of running.animations) animation.finish();
  running.cleanup();
}

// A copy of what the card shows now, to fade out over the new step. Ids go
// (the copy is decoration, and ids must stay unique); it takes no clicks,
// no focus, and isn't read out.
function stepGhost(card, height) {
  const ghost = document.createElement("div");
  ghost.className = "dialog-step-ghost";
  ghost.setAttribute("aria-hidden", "true");
  ghost.inert = true;
  ghost.style.height = `${height}px`;
  for (const part of dialogParts(card)) {
    const copy = part.cloneNode(true);
    copy.removeAttribute("id");
    for (const node of copy.querySelectorAll("[id]")) node.removeAttribute("id");
    ghost.appendChild(copy);
  }
  return ghost;
}

// Move `card` to its next step: `apply()` puts the new step's content in
// place (the real nodes; nothing is cloned but the old step's look), then
// the card eases to it. `direction` is 1 going forward, -1 going back.
function swapDialogStep(card, apply, { direction = 1 } = {}) {
  finishStepSwap(card);
  if (dialogMotionReduced() || typeof card.animate !== "function" || !dialogShown(card)) {
    releaseDialogHeight(card);
    apply();
    return;
  }
  const from = card.getBoundingClientRect().height;
  releaseDialogHeight(card);
  const ghost = stepGhost(card, from);
  apply();
  card.appendChild(ghost);
  // The one read of the new layout (the copy is placed absolutely, so it
  // doesn't count).
  const to = card.getBoundingClientRect().height;
  animateDialogHeight(card, from, to);
  const fresh = dialogParts(card);
  const easing = dialogToken("--ease-out") || DIALOG_EASE_OUT_DEFAULT;
  const running = { animations: [], frame: 0, cleanup: () => ghost.remove() };
  _stepSwaps.set(card, running);
  // Started with the height, once the frame showing the new step has
  // painted (`afterPaint`); the old step's copy covers it until then.
  running.frame = afterPaint(() => {
    running.frame = 0;
    running.animations = [
      ghost.animate(
        [{ opacity: 1, transform: "none" }, { opacity: 0, transform: `translateX(${-DIALOG_SHIFT_PX * direction}px)` }],
        { duration: DIALOG_FADE_MS, easing, fill: "forwards" }
      ),
      ...fresh.map((part) =>
        part.animate([{ transform: `translateX(${DIALOG_SHIFT_PX * direction}px)` }, { transform: "none" }], {
          duration: DIALOG_FADE_MS,
          easing,
        })
      ),
    ];
    Promise.all(running.animations.map((a) => a.finished)).then(
      () => {
        if (_stepSwaps.get(card) !== running) return;
        _stepSwaps.delete(card);
        running.cleanup();
      },
      () => {}
    );
  });
}

// Say which step the dialog is on, for a screen reader (the focus moves
// too, but a heading isn't focused).
function announceDialogStep(card, text) {
  let status = card.querySelector(":scope > .dialog-step-status");
  if (!status) {
    status = document.createElement("span");
    status.className = "sr-only dialog-step-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    card.appendChild(status);
  }
  status.textContent = text;
}

// Hide a dialog. A fade still holding its scrim at opacity 0 is cancelled,
// so it is visible the next time it opens; the card's motion ends.
function hideDialogScrim(scrim) {
  scrim.hidden = true;
  if (typeof scrim.getAnimations === "function") {
    for (const animation of scrim.getAnimations()) animation.cancel();
  }
  const card = scrim.querySelector(".dialog-card");
  if (card) {
    finishStepSwap(card);
    releaseDialogHeight(card);
    dialogHeightState(card).last = null;
  }
}

// ---- Stacking -----------------------------------------------------------

let _dialogStackDepth = 0;
// How many dialogs are over each one: it is live again only when the last
// of them has closed.
const _dialogsOver = new WeakMap();

// Run `task` (another dialog: Promise) over the dialog in `scrim`, which
// stays where it is, visible and inert: `onLeave` and `onReturn` put away
// and bring back what it does while another is in front (its voice scope,
// an open menu, a drag), once for the first over it and the last to close.
// Afterwards focus goes back to the control that opened the other one, if
// the dialog is still open (`refocus` otherwise chooses). Resolves to the
// task's answer.
async function stackDialog({ scrim, onLeave = () => {}, onReturn = () => {}, refocus = null }, task) {
  const opener = document.activeElement;
  const over = (_dialogsOver.get(scrim) || 0) + 1;
  _dialogsOver.set(scrim, over);
  _dialogStackDepth += 1;
  document.body.classList.add("has-stacked-dialog");
  if (over === 1) {
    scrim.inert = true;
    scrim.classList.add("dialog-scrim--under");
    onLeave();
  }
  try {
    return await task();
  } finally {
    _dialogStackDepth = Math.max(0, _dialogStackDepth - 1);
    if (!_dialogStackDepth) document.body.classList.remove("has-stacked-dialog");
    const left = Math.max(0, (_dialogsOver.get(scrim) || 1) - 1);
    _dialogsOver.set(scrim, left);
    if (!left) {
      scrim.inert = false;
      scrim.classList.remove("dialog-scrim--under");
      if (!scrim.hidden) {
        onReturn();
        if (opener && opener.isConnected && scrim.contains(opener) && typeof opener.focus === "function") opener.focus();
        else if (refocus) refocus();
      }
    }
  }
}
