// A menu that opens from a button: the Recent row's "⋯" menu (Home) and the
// reader's More menu. One open at a time, built fresh per open and appended
// to <body>, so nothing it sits over can clip it. Behaviour only: each menu
// styles its own classes.
//
// Keyboard: ArrowUp/ArrowDown move (wrapping), Home and End jump, Enter or
// Space activate (they are real buttons), Esc or Tab close and give focus
// back to the button. A press outside, the page's own scroller scrolling, or
// a resize also close it.
"use strict";

const POPUP_MENU_GAP_PX = 4;
const POPUP_MENU_EDGE_PX = 8;
let openPopupState = null;

// Close the open menu, if any. `restoreFocus` puts focus back on the button
// that opened it, which is what Esc, Tab and choosing an item do.
function closePopupMenu({ restoreFocus = false } = {}) {
  if (!openPopupState) return;
  const { menu, trigger, onClose } = openPopupState;
  openPopupState = null;
  menu.remove();
  trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus && trigger.isConnected) trigger.focus();
  if (onClose) onClose();
}

// Whether a menu is open, and (given a button) whether it is that button's.
function isPopupMenuOpen(trigger) {
  return Boolean(openPopupState) && (!trigger || openPopupState.trigger === trigger);
}

// One item: an icon, a label, and optionally a muted second line
// (`sublabel`) or a shortcut shown at the end. `onSelect` runs after the menu
// has closed and handed focus back.
function buildPopupMenuItem({ icon, label, sublabel, shortcut, onSelect, className }) {
  const item = document.createElement("button");
  item.type = "button";
  item.className = className;
  item.setAttribute("role", "menuitem");
  item.tabIndex = -1;
  if (icon) {
    const iconEl = document.createElement("span");
    iconEl.dataset.icon = icon;
    item.appendChild(iconEl);
  }
  if (sublabel === undefined && shortcut === undefined) {
    item.appendChild(document.createTextNode(label));
  } else {
    const text = document.createElement("span");
    text.className = `${className}-text`;
    const main = document.createElement("span");
    main.className = `${className}-label`;
    main.textContent = label;
    text.appendChild(main);
    if (sublabel !== undefined) {
      const sub = document.createElement("span");
      sub.className = `${className}-sub`;
      sub.textContent = sublabel;
      text.appendChild(sub);
    }
    item.appendChild(text);
    if (shortcut) {
      const keys = document.createElement("span");
      keys.className = `${className}-shortcut`;
      keys.textContent = shortcut;
      item.appendChild(keys);
      item.setAttribute("aria-keyshortcuts", shortcut.replace(/\s+/g, ""));
    }
  }
  item.addEventListener("click", () => {
    closePopupMenu({ restoreFocus: true });
    onSelect();
  });
  return item;
}

function buildPopupMenuSeparator(className) {
  const separator = document.createElement("div");
  separator.className = className;
  separator.setAttribute("role", "separator");
  return separator;
}

// Below the button (4px), right edges lined up; above it when there is no
// room below. `anchor` is what to line up with, the button by default.
function positionPopupMenu(menu, anchorEl, gap) {
  const anchor = anchorEl.getBoundingClientRect();
  const box = menu.getBoundingClientRect();
  let top = anchor.bottom + gap;
  if (top + box.height > window.innerHeight - POPUP_MENU_EDGE_PX) {
    top = anchor.top - box.height - gap;
  }
  const left = Math.min(
    Math.max(POPUP_MENU_EDGE_PX, anchor.right - box.width),
    window.innerWidth - box.width - POPUP_MENU_EDGE_PX
  );
  menu.style.top = `${Math.max(POPUP_MENU_EDGE_PX, top)}px`;
  menu.style.left = `${Math.max(POPUP_MENU_EDGE_PX, left)}px`;
}

function handlePopupMenuKeydown(ev) {
  const items = Array.from(ev.currentTarget.querySelectorAll("[role='menuitem']:not(:disabled)"));
  if (!items.length) return;
  const index = items.indexOf(document.activeElement);
  const moves = {
    ArrowDown: (index + 1) % items.length,
    ArrowUp: (index - 1 + items.length) % items.length,
    Home: 0,
    End: items.length - 1,
  };
  if (ev.key in moves) {
    ev.preventDefault();
    ev.stopPropagation();
    items[moves[ev.key]].focus();
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    ev.stopPropagation();
    closePopupMenu({ restoreFocus: true });
  } else if (ev.key === "Tab") {
    // Hand focus back to the button before the browser moves it, so Tab
    // carries on from there rather than from the end of <body>.
    closePopupMenu({ restoreFocus: true });
  }
}

// Open a menu under `trigger` holding `children` (items, separators, notes).
// `closesOnScroll(target)` says which scrolling closes it (the page's own
// scroller; a dialog's list scrolling leaves it open).
function openPopupMenu({
  trigger,
  children,
  className,
  label,
  anchor = trigger,
  gap = POPUP_MENU_GAP_PX,
  closesOnScroll = (target) => target === document,
  onClose = null,
}) {
  closePopupMenu();
  const menu = document.createElement("div");
  menu.className = className;
  menu.setAttribute("role", "menu");
  if (label) menu.setAttribute("aria-label", label);
  menu.append(...children);
  menu.addEventListener("keydown", handlePopupMenuKeydown);
  document.body.appendChild(menu);
  if (typeof mountIcons === "function") mountIcons(menu);
  positionPopupMenu(menu, anchor, gap);
  openPopupState = { menu, trigger, closesOnScroll, onClose };
  trigger.setAttribute("aria-expanded", "true");
  const first = menu.querySelector("[role='menuitem']:not(:disabled)");
  if (first) first.focus();
  return menu;
}

(function initPopupMenuDismissal() {
  if (typeof document === "undefined") return;
  document.addEventListener("pointerdown", (ev) => {
    if (!openPopupState) return;
    const { menu, trigger } = openPopupState;
    if (!menu.contains(ev.target) && !trigger.contains(ev.target)) closePopupMenu();
  });
  document.addEventListener(
    "scroll",
    (ev) => {
      if (openPopupState && openPopupState.closesOnScroll(ev.target)) closePopupMenu();
    },
    true
  );
  window.addEventListener("resize", () => closePopupMenu());
})();
