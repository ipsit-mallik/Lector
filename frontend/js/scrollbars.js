// Lector's scrollbars — the only script that touches them. The styling is
// shared/scrollbars.css. Two parts:
//
// 1. The overlay scrollbar (initOverlayScrollbars). The browser's own
//    scrollbars are switched off in scrollbars.css: they take layout space and
//    their thickness can't be animated. In their place every scroller gets a
//    rail per axis, an absolutely positioned sibling laid over the scroller's
//    edge, with a thumb that is thin at rest and thickens while pointed at or
//    dragged, like Windows 11's. It takes no layout space, so nothing shifts
//    when it appears. Scrollers are found from the stylesheets (every rule that
//    sets overflow to auto or scroll) plus anything that fires a scroll event,
//    so a new scroll container needs nothing added. The rails take no pointer
//    events, so the wheel over a scrollbar is the scroller's own native scroll;
//    hover, press and drag are worked out from the pointer's position.
//
// 2. Auto-hide (initScrollbarAutoHide). It toggles the `scrollbars-active`
//    class on <html> so that *every* thumb shows together on any activity and
//    fades together once things go quiet.
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
 * @param {() => void} [env.onShow] Called each time the scrollbars appear.
 */
function initScrollbarAutoHide(env = {}) {
  const win = env.win || window;
  const root = env.root || document.documentElement;
  const now = env.now || (() => performance.now());
  const setTimer = env.setTimer || ((fn, ms) => setTimeout(fn, ms));
  const hideDelayMs = env.hideDelayMs ?? SCROLLBAR_HIDE_DELAY_MS;
  const onShow = env.onShow || (() => {});

  const forcedColors = win.matchMedia ? win.matchMedia("(forced-colors: active)") : null;
  const neverHide = () => Boolean(forcedColors && forcedColors.matches);

  let visible = false;
  let lastActivity = 0;
  let timer = null;

  const setVisible = (next) => {
    if (next === visible) return;
    visible = next;
    if (next) {
      root.classList.add(SCROLLBAR_ACTIVE_CLASS);
      onShow();
    } else {
      root.classList.remove(SCROLLBAR_ACTIVE_CLASS);
    }
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

// ---------------------------------------------------------------------------
// Overlay scrollbar
// ---------------------------------------------------------------------------

// Fallbacks for the sizes scrollbars.css owns (--scrollbar-hit, -inset and
// -min-thumb); the CSS values are read at start-up and win.
const SCROLLBAR_DEFAULT_HIT_PX = 12;
const SCROLLBAR_DEFAULT_INSET_PX = 8;
const SCROLLBAR_DEFAULT_MIN_THUMB_PX = 32;
// A press on the track beside the thumb scrolls this much of a viewport toward
// the press, the same step as the browser's own scrollbar.
const SCROLLBAR_TRACK_PAGE_FRACTION = 0.875;
// Set on a scroller while its scrollbar is pointed at or dragged.
const SCROLLBAR_HOVER_ATTRIBUTE = "data-scrollbar-hover";
const SCROLLABLE_OVERFLOW = /^(auto|scroll)$/;

const SCROLLBAR_AXES = {
  y: {
    viewport: "clientHeight",
    content: "scrollHeight",
    position: "scrollTop",
    overflow: "overflowY",
    pointer: "clientY",
    start: "top",
    length: "height",
    translate: "translateY",
  },
  x: {
    viewport: "clientWidth",
    content: "scrollWidth",
    position: "scrollLeft",
    overflow: "overflowX",
    pointer: "clientX",
    start: "left",
    length: "width",
    translate: "translateX",
  },
};

/**
 * Where the thumb sits in its track. Pure, so it is tested under Node.
 * Returns null when there is nothing to scroll.
 *
 * @param {{viewport: number, content: number, position: number, track: number, minThumb: number}} m
 * @returns {{length: number, offset: number, travel: number, range: number} | null}
 */
function scrollbarThumb({ viewport, content, position, track, minThumb }) {
  const range = content - viewport;
  if (range < 1 || track <= 0) return null;
  const length = Math.min(track, Math.max(minThumb, (track * viewport) / content));
  const travel = track - length;
  const offset = travel * Math.min(Math.max(position / range, 0), 1);
  return { length, offset, travel, range };
}

/** Whether a pointer at (x, y) is over a rail's grab area ({left, top, right, bottom}). */
function scrollbarHitTest(area, x, y) {
  return Boolean(area) && x >= area.left && x < area.right && y >= area.top && y < area.bottom;
}

/** Where a thumb dragged `moved` px from where it was grabbed puts the scroller. */
function scrollbarDragPosition({ startPosition, moved, travel, range }) {
  if (travel <= 0) return startPosition;
  return Math.min(Math.max(startPosition + (moved * range) / travel, 0), range);
}

function initOverlayScrollbars(doc = document) {
  const win = doc.defaultView;
  const rootStyle = win.getComputedStyle(doc.documentElement);
  const cssPx = (name, fallback) => parseFloat(rootStyle.getPropertyValue(name)) || fallback;
  const HIT = cssPx("--scrollbar-hit", SCROLLBAR_DEFAULT_HIT_PX);
  const INSET = cssPx("--scrollbar-inset", SCROLLBAR_DEFAULT_INSET_PX);
  const MIN_THUMB = cssPx("--scrollbar-min-thumb", SCROLLBAR_DEFAULT_MIN_THUMB_PX);

  /** @type {Map<Element, {y: object, x: object}>} */
  const scrollers = new Map();
  let selector = "";
  let frame = 0;

  // Every selector whose rule makes an element scroll, from all the page's
  // stylesheets and what they @import.
  const collectSelectors = () => {
    const found = new Set();
    const walk = (rules) => {
      for (const rule of rules) {
        if (rule.styleSheet) {
          try { walk(rule.styleSheet.cssRules); } catch { /* unreadable sheet */ }
        } else if (rule.style) {
          const { overflowX, overflowY } = rule.style;
          if (SCROLLABLE_OVERFLOW.test(overflowY) || SCROLLABLE_OVERFLOW.test(overflowX)) {
            found.add(rule.selectorText);
          }
        } else if (rule.cssRules) {
          walk(rule.cssRules); // @media, @supports, @layer
        }
      }
    };
    for (const sheet of doc.styleSheets) {
      try { walk(sheet.cssRules); } catch { /* unreadable sheet */ }
    }
    selector = [...found].join(",");
  };

  const scrolls = (el, axis) =>
    SCROLLABLE_OVERFLOW.test(win.getComputedStyle(el)[SCROLLBAR_AXES[axis].overflow]);

  const makeRail = (scroller, axis) => {
    const rail = doc.createElement("div");
    rail.className = `scrollbar-rail scrollbar-rail--${axis}`;
    rail.setAttribute("aria-hidden", "true");
    rail.style.left = "0px";
    rail.style.top = "0px";
    const thumb = doc.createElement("div");
    thumb.className = "scrollbar-thumb";
    rail.appendChild(thumb);
    return { axis, rail, thumb, metrics: null, shown: false, area: null, scale: 1 };
  };

  const attach = (scroller) => {
    if (scrollers.has(scroller) || !scroller.parentElement || scroller === doc.body) return;
    const bars = { y: makeRail(scroller, "y"), x: makeRail(scroller, "x") };
    scroller.after(bars.y.rail, bars.x.rail);
    scrollers.set(scroller, bars);
    scroller.addEventListener("scroll", () => update(scroller), { passive: true });
    resizeObserver.observe(scroller);
  };

  const detach = (scroller) => {
    const bars = scrollers.get(scroller);
    bars.y.rail.remove();
    bars.x.rail.remove();
    resizeObserver.unobserve(scroller);
    scrollers.delete(scroller);
  };

  const placeThumb = (scroller, bar, track) => {
    const axis = SCROLLBAR_AXES[bar.axis];
    bar.metrics = scrollbarThumb({
      viewport: scroller[axis.viewport],
      content: scroller[axis.content],
      position: scroller[axis.position],
      track,
      minThumb: MIN_THUMB,
    });
    if (!bar.metrics) return;
    bar.thumb.style[axis.length] = `${bar.metrics.length}px`;
    bar.thumb.style.transform = `${axis.translate}(${bar.metrics.offset}px)`;
  };

  // Lays one scroller's rails over its padding box: along the far edge, the
  // track stopping --scrollbar-inset short of both ends (and short of the other
  // rail's corner when both show). Positions are measured against the rail's
  // own box, so they hold whatever its containing block is, and divided by the
  // rendered scale, so they hold inside a dialog mid-way through its grow-in.
  const update = (scroller) => {
    const bars = scrollers.get(scroller);
    if (!bars) return;
    const displayed = scroller.isConnected && scroller.getClientRects().length > 0;
    const { y, x } = bars;
    for (const bar of [y, x]) {
      const axis = SCROLLBAR_AXES[bar.axis];
      bar.shown = displayed && scrolls(scroller, bar.axis) && scroller[axis.content] - scroller[axis.viewport] >= 1;
      bar.rail.classList.toggle("is-scrollable", bar.shown);
      if (!bar.shown) bar.area = null;
    }
    if (!y.shown && !x.shown) {
      if (hovered && hovered.scroller === scroller) setHovered(null);
      return;
    }

    const box = scroller.getBoundingClientRect();
    const scale = scroller.offsetWidth ? box.width / scroller.offsetWidth : 1;
    const padLeft = box.left + scroller.clientLeft * scale;
    const padTop = box.top + scroller.clientTop * scale;

    const place = (bar, left, top, length) => {
      const own = bar.rail.getBoundingClientRect();
      const ownLeft = parseFloat(bar.rail.style.left) || 0;
      const ownTop = parseFloat(bar.rail.style.top) || 0;
      bar.rail.style.left = `${ownLeft + (left - own.left) / scale}px`;
      bar.rail.style.top = `${ownTop + (top - own.top) / scale}px`;
      bar.rail.style[SCROLLBAR_AXES[bar.axis].length] = `${Math.max(length, 0)}px`;
      // The grab area in window coordinates, for the pointer handling below.
      const across = HIT * scale;
      const along = Math.max(length, 0) * scale;
      bar.area = bar.axis === "y"
        ? { left, top, right: left + across, bottom: top + along }
        : { left, top, right: left + along, bottom: top + across };
      bar.scale = scale;
      placeThumb(scroller, bar, length);
    };

    if (y.shown) {
      place(
        y,
        padLeft + (scroller.clientWidth - HIT) * scale,
        padTop + INSET * scale,
        scroller.clientHeight - 2 * INSET - (x.shown ? HIT : 0),
      );
    }
    if (x.shown) {
      place(
        x,
        padLeft + INSET * scale,
        padTop + (scroller.clientHeight - HIT) * scale,
        scroller.clientWidth - 2 * INSET - (y.shown ? HIT : 0),
      );
    }
  };

  // Pointing, pressing and dragging. The rails take no pointer events
  // (scrollbars.css), so a wheel or touchpad turned over a scrollbar lands on
  // the scroller underneath and scrolls it natively, exactly as it does
  // anywhere else on it: smooth, at the system's step, on the compositor. The
  // rails are found by position instead. `hovered` is the rail under the
  // pointer, or the one being dragged.
  let hovered = null; // { scroller, bar }
  let drag = null;
  let swallowClick = false;

  // Over a rail's grab area, and over its own scroller rather than something
  // laid on top of it (a dialog, a popover).
  const railAt = (ev) => {
    for (const [scroller, bars] of scrollers) {
      if (ev.target !== scroller && !scroller.contains(ev.target)) continue;
      for (const bar of [bars.y, bars.x]) {
        if (bar.metrics && scrollbarHitTest(bar.area, ev.clientX, ev.clientY)) return { scroller, bar };
      }
    }
    return null;
  };

  // While a scrollbar is pointed at, the scroller's contents ignore the
  // pointer (scrollbars.css): nothing under the rail lights up or takes the
  // press, and the wheel goes to the scroller itself.
  function setHovered(next) {
    if (hovered && next && hovered.bar === next.bar) return;
    if (hovered) {
      hovered.bar.rail.classList.remove("is-hovered");
      hovered.scroller.removeAttribute(SCROLLBAR_HOVER_ATTRIBUTE);
    }
    hovered = next;
    if (hovered) {
      hovered.bar.rail.classList.add("is-hovered");
      hovered.scroller.setAttribute(SCROLLBAR_HOVER_ATTRIBUTE, "");
    }
  }

  // A running scrollElementTo() tween (ui.js) gives way to the reader's own
  // hand on the scrollbar, as it does to the wheel. Asking it to go where the
  // scroller already is cancels the tween without moving anything.
  const stopTween = (scroller) => {
    if (typeof scrollElementTo === "function") scrollElementTo(scroller, scroller.scrollTop, { animate: false });
  };

  const onPointerDown = (ev) => {
    if (ev.pointerType === "touch" || ev.button !== 0) return; // touch pans the content
    const hit = railAt(ev);
    if (!hit) return;
    // The press is the scrollbar's: no focus change, no text selection, and
    // neither it nor the click it ends in reaches the page.
    ev.preventDefault();
    ev.stopPropagation();
    swallowClick = true;
    const { scroller, bar } = hit;
    const axis = SCROLLBAR_AXES[bar.axis];
    setHovered(hit);
    stopTween(scroller);
    const thumbBox = bar.thumb.getBoundingClientRect();
    const at = ev[axis.pointer];
    if (at >= thumbBox[axis.start] && at <= thumbBox[axis.start] + thumbBox[axis.length]) {
      drag = { ...hit, pointerId: ev.pointerId, from: at, startPosition: scroller[axis.position] };
      scroller.setPointerCapture(ev.pointerId);
      bar.rail.classList.add("is-dragging");
      return;
    }
    // The track: a page toward the press, eased like the app's other jumps.
    const direction = at < thumbBox[axis.start] ? -1 : 1;
    const step = direction * scroller[axis.viewport] * SCROLLBAR_TRACK_PAGE_FRACTION;
    if (bar.axis === "y" && typeof scrollElementBy === "function") scrollElementBy(scroller, step);
    else scroller[axis.position] += step;
  };

  const onPointerMove = (ev) => {
    if (drag) {
      if (ev.pointerId !== drag.pointerId || !drag.bar.metrics) return;
      const axis = SCROLLBAR_AXES[drag.bar.axis];
      drag.scroller[axis.position] = scrollbarDragPosition({
        startPosition: drag.startPosition,
        moved: (ev[axis.pointer] - drag.from) / drag.bar.scale,
        travel: drag.bar.metrics.travel,
        range: drag.bar.metrics.range,
      });
      return;
    }
    if (ev.pointerType !== "touch") setHovered(railAt(ev));
  };

  const endDrag = (ev) => {
    if (drag && ev.pointerId === drag.pointerId) {
      drag.bar.rail.classList.remove("is-dragging");
      drag = null;
      if (ev.type !== "lostpointercapture") setHovered(railAt(ev));
    }
    // The click (and a double-click) that ends a press on a scrollbar is
    // dispatched straight after the release; anything later is someone else's.
    if (swallowClick) win.setTimeout(() => { swallowClick = false; }, 0);
  };

  const onClick = (ev) => {
    if (!swallowClick) return;
    ev.preventDefault();
    ev.stopPropagation();
  };

  win.addEventListener("pointerdown", onPointerDown, { capture: true });
  win.addEventListener("pointermove", onPointerMove, { capture: true, passive: true });
  win.addEventListener("pointerup", endDrag, { capture: true, passive: true });
  win.addEventListener("pointercancel", endDrag, { capture: true, passive: true });
  win.addEventListener("lostpointercapture", endDrag, { capture: true, passive: true });
  win.addEventListener("click", onClick, { capture: true });
  win.addEventListener("dblclick", onClick, { capture: true });
  // The pointer left the window.
  doc.documentElement.addEventListener("pointerleave", () => { if (!drag) setHovered(null); });

  const discover = () => {
    for (const scroller of [...scrollers.keys()]) {
      if (!scroller.isConnected) detach(scroller);
    }
    if (selector) {
      for (const el of doc.querySelectorAll(selector)) attach(el);
    }
  };

  const refresh = () => {
    frame = 0;
    discover();
    for (const scroller of scrollers.keys()) update(scroller);
  };
  const scheduleRefresh = () => {
    if (!frame) frame = win.requestAnimationFrame(refresh);
  };

  const resizeObserver = new win.ResizeObserver(scheduleRefresh);

  // Content growing (rows drawn, pages rendered, zoom) or a scroller being
  // shown or hidden. The rails' own changes are ignored, or each update would
  // schedule the next.
  const ownNode = (node) => node.nodeType === 1 && Boolean(node.closest(".scrollbar-rail"));
  const ownMutation = (record) =>
    (record.target.nodeType === 1 && Boolean(record.target.closest(".scrollbar-rail"))) ||
    (record.type === "childList" && [...record.addedNodes, ...record.removedNodes].every(ownNode));
  new win.MutationObserver((records) => {
    if (!records.every(ownMutation)) scheduleRefresh();
  }).observe(doc.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });

  // A scroller no stylesheet rule announced (an inline overflow, say) is picked
  // up the first time it scrolls.
  win.addEventListener("scroll", (ev) => {
    const el = ev.target;
    if (el.nodeType === 1 && !scrollers.has(el) && (scrolls(el, "y") || scrolls(el, "x"))) {
      attach(el);
      update(el);
    }
  }, { capture: true, passive: true });

  // A scroller that slid or grew in (Settings' entrance rise, a dialog's
  // grow-in) is measured again where it came to rest.
  win.addEventListener("animationend", scheduleRefresh, true);
  win.addEventListener("transitionend", scheduleRefresh, true);
  win.addEventListener("resize", scheduleRefresh);
  win.addEventListener("load", () => {
    collectSelectors();
    scheduleRefresh();
  });
  collectSelectors();
  refresh();
  return { refresh: scheduleRefresh };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  const overlay = initOverlayScrollbars();
  initScrollbarAutoHide({ onShow: overlay.refresh });
}
