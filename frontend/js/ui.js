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

initTooltips();
