// Shared UI behaviour for the primitives in shared/components.css that need
// JavaScript: Tooltip and Toast. Loaded by every page, before the page's own
// script, the same way icons.js and bridge.js are.
//
// Tooltip — any element with `data-tooltip="Name"` (and optionally
// `data-shortcut="Ctrl K"`) gets a styled tooltip on hover (after a short
// delay) and immediately on keyboard focus. One shared floating element,
// not one per control, so nothing is added to the layout of the control
// itself. The element is linked with aria-describedby while it shows.
//
// Toast — showToast("Saved") or showToast("Highlight removed",
// { actionLabel: "Undo", onAction: () => ... }). Announced through a polite
// live region, so screen readers hear the same confirmation sighted users
// see.

const TOOLTIP_HOVER_DELAY_MS = 450;
const TOOLTIP_GAP_PX = 8;
const TOAST_DEFAULT_MS = 4000;
const TOAST_WITH_ACTION_MS = 6000;

let _tooltipEl = null;
let _tooltipTarget = null;
let _tooltipTimer = null;

function _ensureTooltip() {
  if (_tooltipEl) return _tooltipEl;
  _tooltipEl = document.createElement("div");
  _tooltipEl.className = "tooltip";
  _tooltipEl.id = "lector-tooltip";
  _tooltipEl.setAttribute("role", "tooltip");
  _tooltipEl.hidden = true;
  document.body.appendChild(_tooltipEl);
  return _tooltipEl;
}

function _renderTooltip(target) {
  const el = _ensureTooltip();
  el.replaceChildren(document.createTextNode(target.dataset.tooltip));
  if (target.dataset.shortcut) {
    const kbd = document.createElement("kbd");
    kbd.textContent = target.dataset.shortcut;
    el.appendChild(kbd);
  }
}

// Above the target by default; flips below when there is no room, and is
// clamped horizontally so it never runs off either window edge. Controls
// inside a `data-tooltip-side="right"` container (the Reader's icon rail,
// pinned to the window's left edge) get it beside them instead.
function _positionTooltip(target) {
  const el = _tooltipEl;
  const rect = target.getBoundingClientRect();
  const tip = el.getBoundingClientRect();
  if (target.closest("[data-tooltip-side='right']")) {
    el.style.left = `${rect.right + TOOLTIP_GAP_PX}px`;
    el.style.top = `${rect.top + (rect.height - tip.height) / 2}px`;
    return;
  }
  let top = rect.top - tip.height - TOOLTIP_GAP_PX;
  if (top < TOOLTIP_GAP_PX) top = rect.bottom + TOOLTIP_GAP_PX;
  let left = rect.left + (rect.width - tip.width) / 2;
  left = Math.max(TOOLTIP_GAP_PX, Math.min(left, window.innerWidth - tip.width - TOOLTIP_GAP_PX));
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

function showTooltip(target) {
  clearTimeout(_tooltipTimer);
  _tooltipTarget = target;
  _renderTooltip(target);
  const el = _tooltipEl;
  el.hidden = false;
  _positionTooltip(target);
  target.setAttribute("aria-describedby", el.id);
  requestAnimationFrame(() => el.classList.add("is-visible"));
}

function hideTooltip() {
  clearTimeout(_tooltipTimer);
  if (!_tooltipEl || !_tooltipTarget) return;
  _tooltipTarget.removeAttribute("aria-describedby");
  _tooltipTarget = null;
  _tooltipEl.classList.remove("is-visible");
  _tooltipEl.hidden = true;
}

function initTooltips(root = document) {
  root.addEventListener("pointerover", (e) => {
    const target = e.target.closest("[data-tooltip]");
    if (!target || target === _tooltipTarget) return;
    clearTimeout(_tooltipTimer);
    _tooltipTimer = setTimeout(() => showTooltip(target), TOOLTIP_HOVER_DELAY_MS);
  });
  root.addEventListener("pointerout", (e) => {
    const target = e.target.closest("[data-tooltip]");
    if (target && !target.contains(e.relatedTarget)) hideTooltip();
  });
  root.addEventListener("focusin", (e) => {
    const target = e.target.closest("[data-tooltip]");
    if (target && target.matches(":focus-visible")) showTooltip(target);
  });
  root.addEventListener("focusout", hideTooltip);
  root.addEventListener("pointerdown", hideTooltip);
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideTooltip();
  });
  window.addEventListener("scroll", hideTooltip, true);
}

let _toastRegion = null;

function _ensureToastRegion() {
  if (_toastRegion) return _toastRegion;
  _toastRegion = document.createElement("div");
  _toastRegion.className = "toast-region";
  _toastRegion.setAttribute("role", "status");
  _toastRegion.setAttribute("aria-live", "polite");
  document.body.appendChild(_toastRegion);
  return _toastRegion;
}

// Returns a dismiss function, so a caller can retract the toast early (e.g.
// when the action it offers to undo has itself been reverted).
function showToast(message, { actionLabel, onAction, durationMs } = {}) {
  const region = _ensureToastRegion();
  const toast = document.createElement("div");
  toast.className = "toast";
  const text = document.createElement("span");
  text.textContent = message;
  toast.appendChild(text);

  const ms = durationMs ?? (actionLabel ? TOAST_WITH_ACTION_MS : TOAST_DEFAULT_MS);
  let timer = null;
  const dismiss = () => {
    clearTimeout(timer);
    toast.classList.remove("is-visible");
    // Removed after the exit transition; the fallback covers reduced motion,
    // where the transition is collapsed and `transitionend` may not fire.
    toast.addEventListener("transitionend", () => toast.remove(), { once: true });
    setTimeout(() => toast.remove(), 400);
  };

  if (actionLabel && onAction) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-action";
    btn.textContent = actionLabel;
    btn.addEventListener("click", () => {
      dismiss();
      onAction();
    });
    toast.appendChild(btn);
  }

  region.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  timer = setTimeout(dismiss, ms);
  // Hovering or focusing the toast pauses its timeout, so an Undo is never
  // pulled out from under the pointer.
  toast.addEventListener("pointerenter", () => clearTimeout(timer));
  toast.addEventListener("pointerleave", () => { timer = setTimeout(dismiss, ms); });
  toast.addEventListener("focusin", () => clearTimeout(timer));
  return dismiss;
}

// Theme switch: one crossfade of the whole window, for a switch the reader
// makes (a page restoring its saved theme on load sets data-theme directly,
// with no fade).
//
// Preferred path is a View Transition: the browser snapshots the old frame,
// the change is made inside the callback, and the two frames dissolve over
// --dur-base (theme.css), so every surface, shadow, scrollbar thumb and the
// rendered PDF page fade together by construction, with no per-element
// transitions to start late or be cut short. Reduced motion skips it. Without
// the API, .theme-fading puts a transition on every element for the change and
// comes off when the root's own transition actually ends, not on a timer: a
// timer measured from the click can fire before a slow first frame has even
// started the fade, which turns it into a snap.
//
// Everything that must change with the theme and fade with it (Settings' check
// badge and selection ring) listens for `themechange`, fired inside the same
// step, rather than being updated beside this call.
const THEME_FADE_SAFETY_MS = 1000;
let _targetTheme = null;
let _endThemeFade = null;
let _themeSwitchesInFlight = 0;

// The theme being switched to if a switch is in flight, else the one on the
// page: a View Transition applies the change a frame after it is requested.
function currentTheme() {
  return _targetTheme || document.documentElement.dataset.theme;
}

function commitTheme(name) {
  document.documentElement.dataset.theme = name;
  _targetTheme = null;
  document.dispatchEvent(new CustomEvent("themechange", { detail: { name } }));
}

function fadeThemeWithTransitions(name) {
  const root = document.documentElement;
  if (_endThemeFade) _endThemeFade();
  const onEnd = (e) => {
    if (e.target === root && e.propertyName === "background-color") end();
  };
  const timer = setTimeout(() => end(), THEME_FADE_SAFETY_MS);
  function end() {
    clearTimeout(timer);
    root.removeEventListener("transitionend", onEnd);
    root.classList.remove("theme-fading");
    _endThemeFade = null;
  }
  _endThemeFade = end;
  root.addEventListener("transitionend", onEnd);
  root.classList.add("theme-fading");
  commitTheme(name);
}

function applyTheme(name) {
  if (currentTheme() === name) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    commitTheme(name);
    return;
  }
  if (typeof document.startViewTransition !== "function") {
    fadeThemeWithTransitions(name);
    return;
  }
  _targetTheme = name;
  const root = document.documentElement;
  const endSwitch = () => {
    if (--_themeSwitchesInFlight === 0) root.classList.remove("theme-switching");
  };
  const transition = document.startViewTransition(() => {
    // The new frame must be the final state, not components still animating
    // towards it on their own 120/200ms transitions underneath the dissolve.
    _themeSwitchesInFlight++;
    root.classList.add("theme-switching");
    commitTheme(name);
  });
  // `finished` settles either way; a transition skipped by a newer one, or by a
  // hidden window, rejects `ready`, and the change is made regardless.
  transition.finished.then(endSwitch, endSwitch);
  transition.ready.catch(() => {});
  transition.updateCallbackDone.catch(() => {});
}

// Programmatic scrolling: the one place the app animates a scroll it starts
// itself (a page jump in the reader, the Open dialog's tree following the active
// folder, arrow keys and "scroll down" by voice). The wheel, touchpad, scrollbar
// drag and keyboard-on-a-focused-scroller are never touched: those stay with the
// browser's own scrolling.
//
// This is a short tween, not `scrollTo({ behavior: "smooth" })`, because the
// browser's own duration can't be set and is not short: measured in Chromium it
// is about 330ms for 300px, 520ms for one viewport and 730ms for anything
// longer. This one takes --dur-base on an ease-out cubic whatever the distance,
// and gives way at once to the reader taking the scroller over.
//
// Not animated, by design: a jump of more than SCROLL_ANIMATION_MAX_VIEWPORTS
// viewports (page 3 to page 300 lands, it does not sweep through 297 pages);
// anything the caller passes `animate: false` for (the position a view or dialog
// opens at, zoom re-anchoring, which must be exact); and all of it under
// prefers-reduced-motion.
const SCROLL_ANIMATION_MAX_VIEWPORTS = 3;
const SCROLL_ANIMATION_FALLBACK_MS = 200;
const _scrollTweens = new WeakMap();
const _scrollTakeoverBound = new WeakSet();

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function _cancelScrollTween(el) {
  const tween = _scrollTweens.get(el);
  if (!tween) return;
  cancelAnimationFrame(tween.frame);
  _scrollTweens.delete(el);
}

// Whoever scrolls the scroller themselves takes over from a running tween.
function _bindScrollTakeover(el) {
  if (_scrollTakeoverBound.has(el)) return;
  _scrollTakeoverBound.add(el);
  for (const type of ["wheel", "pointerdown", "touchstart"]) {
    el.addEventListener(type, () => _cancelScrollTween(el), { passive: true });
  }
}

// Where the scroller is heading: the running tween's end, else where it is. A
// caller judging "is this already in view" must ask this, not scrollTop, or a
// row the tween merely passes through counts as visible and the tween carries on.
function scrollDestination(el) {
  const tween = _scrollTweens.get(el);
  return tween ? tween.target : el.scrollTop;
}

// Returns whether it moved (false: already there, or at the end it was asked to
// go past), which "scroll down" by voice uses to decide to turn the page instead.
function scrollElementTo(el, top, { animate = true } = {}) {
  _cancelScrollTween(el);
  const target = Math.min(Math.max(top, 0), Math.max(el.scrollHeight - el.clientHeight, 0));
  const from = el.scrollTop;
  if (Math.abs(target - from) < 1) return false;

  const tooFar = Math.abs(target - from) > el.clientHeight * SCROLL_ANIMATION_MAX_VIEWPORTS;
  if (!animate || tooFar || prefersReducedMotion()) {
    el.scrollTop = target;
    return true;
  }

  const duration =
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dur-base")) ||
    SCROLL_ANIMATION_FALLBACK_MS;
  const tween = { target, frame: 0 };
  let startedAt = null;
  const step = (now) => {
    if (startedAt === null) startedAt = now;
    const progress = Math.min((now - startedAt) / duration, 1);
    el.scrollTop = from + (target - from) * (1 - Math.pow(1 - progress, 3));
    if (progress < 1) tween.frame = requestAnimationFrame(step);
    else _scrollTweens.delete(el);
  };
  _scrollTweens.set(el, tween);
  _bindScrollTakeover(el);
  tween.frame = requestAnimationFrame(step);
  return true;
}

function scrollElementBy(el, delta, options) {
  return scrollElementTo(el, scrollDestination(el) + delta, options);
}

// Radio semantics for a group of <button> cards (.option-row, .theme-card) that
// choose one of several. Each card's own click handler still does the choosing;
// this only mirrors the .selected class into role/aria-checked, keeps the
// selected card as the group's single Tab stop, and lets the arrow keys move
// to (and choose) the neighbour, as a native radio group does. Re-syncs itself
// when the page changes .selected or rebuilds the cards.
//
// `selectOnArrow: false` makes the arrows move focus only (Space/Enter then
// choose): for a group with a choice that shouldn't be one stray keypress away,
// like Settings' "Overwrite the original".
function initRadioCards(group, { selectOnArrow = true } = {}) {
  group.setAttribute("role", "radiogroup");
  const cards = () => Array.from(group.children).filter((el) => el.matches("button"));

  const sync = () => {
    const all = cards();
    const selected = all.find((card) => card.classList.contains("selected"));
    all.forEach((card) => {
      card.setAttribute("role", "radio");
      card.setAttribute("aria-checked", String(card === selected));
      card.tabIndex = card === (selected || all[0]) ? 0 : -1;
    });
  };

  const ARROW_STEP = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
  group.addEventListener("keydown", (e) => {
    const step = ARROW_STEP[e.key];
    const all = cards();
    const index = all.indexOf(document.activeElement);
    if (!step || index < 0) return;
    e.preventDefault();
    // The arrows belong to the group: a page-level handler (the reader turns
    // pages on them) must not also act while a card has focus.
    e.stopPropagation();
    const next = all[(index + step + all.length) % all.length];
    next.focus();
    if (selectOnArrow) next.click();
  });

  new MutationObserver(sync).observe(group, {
    attributes: true,
    attributeFilter: ["class"],
    childList: true,
    subtree: true,
  });
  sync();
}

initTooltips();
