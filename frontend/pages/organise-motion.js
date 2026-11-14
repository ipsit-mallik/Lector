// Organise pages' motion (docs/DESIGN_SYSTEM.md § "Organise pages" →
// Motion): pages slide from where they were to where they are (FLIP: First,
// Last, Invert, Play), deleted pages fade and shrink away before the rest
// close the gap, inserted pages fade in. Only transform and opacity, through
// the Web Animations API, so nothing here waits on layout or blocks input:
// `finish()` ends whatever is running at once, and the grid calls it before
// every change. Under prefers-reduced-motion nothing moves; the result is
// just as immediate.
"use strict";

// The defaults when the tokens can't be read (theme.css holds the real ones).
const ORGANISE_MOTION_DEFAULTS = {
  "--dur-fast": 120, "--dur-quick": 140, "--dur-base": 200, "--dur-reorder": 180, "--dur-return": 150,
};
const ORGANISE_EASE_DEFAULT = "cubic-bezier(0.2, 0.8, 0.2, 1)";
const ORGANISE_LEAVE_SCALE = 0.92;
const ORGANISE_ENTER_SCALE = 0.92;

function createOrganiseMotion({ grid, scroller, onSettled }) {
  let running = new Set();
  let leaving = null; // { after, fades }

  function reduced() {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function canAnimate(cell) {
    return !reduced() && typeof cell.animate === "function";
  }

  function token(name) {
    const style = typeof getComputedStyle === "function" ? getComputedStyle(document.documentElement) : null;
    return style && typeof style.getPropertyValue === "function" ? style.getPropertyValue(name).trim() : "";
  }

  function tokenMs(name) {
    const raw = token(name);
    const value = parseFloat(raw);
    if (!Number.isFinite(value)) return ORGANISE_MOTION_DEFAULTS[name];
    return raw.endsWith("ms") ? value : value * 1000;
  }

  const easing = () => token("--ease-out") || ORGANISE_EASE_DEFAULT;

  // How long a page's turn runs (the paper's own CSS transition).
  function turnMs() {
    return reduced() ? 0 : tokenMs("--dur-base");
  }

  function settledIfIdle() {
    if (!running.size && !leaving) onSettled();
  }

  function track(animation) {
    running.add(animation);
    const done = () => {
      if (!running.delete(animation)) return;
      settledIfIdle();
    };
    animation.finished.then(done, done);
    return animation;
  }

  // Where each cell is, in the grid's own coordinates (so a scroll between
  // two snapshots doesn't read as movement), and whether it is on screen
  // or near it.
  function snapshot(cells) {
    const gridRect = grid.getBoundingClientRect();
    const view = scroller.getBoundingClientRect();
    const margin = view.height / 2;
    const out = new Map();
    for (const cell of cells) {
      const rect = cell.getBoundingClientRect();
      out.set(cell, {
        left: rect.left - gridRect.left,
        top: rect.top - gridRect.top,
        near: rect.bottom > view.top - margin && rect.top < view.bottom + margin,
      });
    }
    return out;
  }

  function fadeIn(cell, duration) {
    track(cell.animate(
      [{ opacity: 0, transform: `scale(${ORGANISE_ENTER_SCALE})` }, { opacity: 1, transform: "none" }],
      { duration, easing: easing() },
    ));
  }

  // Each of `cells` from where `first` had it to where it is now. `lead`
  // (the moved pages) lands first; `entering` (new pages) fades in. A page
  // that comes from off screen fades in where it lands rather than flying
  // across the grid; nothing off screen both before and after moves.
  function slide(first, cells, { lead = new Set(), entering = new Set() } = {}) {
    const animatable = cells.filter(canAnimate);
    if (!animatable.length) return;
    const last = snapshot(animatable);
    const base = tokenMs("--dur-reorder");
    const quick = tokenMs("--dur-quick");
    for (const cell of animatable) {
      const to = last.get(cell);
      const from = first.get(cell);
      const duration = lead.has(cell) ? quick : base;
      if (entering.has(cell) || !from) {
        if (to.near) fadeIn(cell, base);
        continue;
      }
      const dx = from.left - to.left;
      const dy = from.top - to.top;
      if ((!dx && !dy) || (!from.near && !to.near)) continue;
      if (!from.near) {
        fadeIn(cell, duration);
        continue;
      }
      track(cell.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration, easing: easing() },
      ));
    }
  }

  // Deleted pages fade and shrink where they are, then `after(true)` takes
  // them out and the rest slide into the gap. Finished early (`finish`),
  // `after(false)`: out at once, no slide.
  function leave(cells, after) {
    const shown = cells.filter(canAnimate);
    if (!shown.length) {
      after(false);
      return;
    }
    const fades = shown.map((cell) => cell.animate(
      [{ opacity: 1, transform: "none" }, { opacity: 0, transform: `scale(${ORGANISE_LEAVE_SCALE})` }],
      { duration: tokenMs("--dur-fast"), easing: easing(), fill: "forwards" },
    ));
    const entry = { after, fades };
    leaving = entry;
    Promise.all(fades.map((fade) => fade.finished)).then(() => {
      if (leaving !== entry) return;
      leaving = null;
      endLeaving(entry, true);
      settledIfIdle();
    }, () => {});
  }

  // The faded cells out of the grid, then their fades let go (they held the
  // cells at 0 opacity; a page that comes back, by Undo, reuses its cell).
  function endLeaving({ after, fades }, slide) {
    after(slide);
    for (const fade of fades) fade.cancel();
  }

  // Ends everything running, at its end state: a second action never waits
  // for the first one's motion.
  function finish() {
    if (leaving) {
      const entry = leaving;
      leaving = null;
      endLeaving(entry, false);
    }
    const now = [...running];
    running = new Set();
    for (const animation of now) animation.finish();
    if (now.length) onSettled();
  }

  return { snapshot, slide, leave, finish, turnMs, reduced, easing, tokenMs };
}
