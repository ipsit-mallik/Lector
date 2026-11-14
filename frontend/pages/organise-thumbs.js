// Organise pages' thumbnails (docs/DESIGN_SYSTEM.md § "Organise pages"):
// drawn lazily, as cells come near the visible part of the grid, a few at a
// time, and kept per source page and turn, never per position: a move,
// delete, undo or redo draws none.
//
// A turn shows at once: the image already on screen is rotated by CSS (over
// --dur-base), and the page drawn at its new turn is fetched in the
// background, then put in its place once the rotation has finished, turned
// back to 0 in the same frame, so nothing visibly changes. The image on
// screen is never cleared while one is on its way.
"use strict";

// How many thumbnails are drawn at once; the rest wait their turn.
const ORGANISE_THUMB_CONCURRENCY = 3;
const ORGANISE_THUMB_BOX_PX = 120;
// The page's shape until its image arrives (portrait A4 in the 120px box).
const ORGANISE_THUMB_PLACEHOLDER = { width: 85, height: ORGANISE_THUMB_BOX_PX };

// `pageOf(cell)` is the page a cell shows now; `cellOf(key)` the cell of a
// page key, which is its source page ("source:index"); `turnMs()` how long
// a turn's rotation runs (0 under reduced motion).
function createOrganiseThumbs({ scroller, thumbnail, pageOf, cellOf, turnMs }) {
  const cache = new Map(); // "source:index" -> Map(turn -> {src, width, height})
  const inFlight = new Set(); // "source:index@turn"
  const queue = [];
  let drawing = 0;
  let frozen = false;

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      queue.push(entry.target);
    }
    drawQueued();
  }, { root: scroller, rootMargin: "400px 0px" });

  const sourceKey = (page) => `${page.source}:${page.index}`;
  const cached = (page, turn) => {
    const turns = cache.get(sourceKey(page));
    return (turns && turns.get(turn)) || null;
  };

  // ---- Fetching --------------------------------------------------------------

  function drawQueued() {
    while (!frozen && drawing < ORGANISE_THUMB_CONCURRENCY && queue.length) {
      const cell = queue.shift();
      const page = cell.isConnected ? pageOf(cell) : null;
      if (!page) continue;
      const wanted = `${sourceKey(page)}@${page.turn}`;
      if (inFlight.has(wanted)) continue; // painted when it arrives
      if (cached(page, page.turn)) {
        paint(cell, page);
        continue;
      }
      fetch(page, page.turn, wanted);
    }
  }

  function fetch(page, turn, wanted) {
    drawing += 1;
    inFlight.add(wanted);
    thumbnail(page, turn)
      .then((result) => {
        if (!result || !result.image) return;
        const turns = new Map(cache.get(sourceKey(page)) || []);
        turns.set(turn, { src: `data:image/png;base64,${result.image}`, width: result.width, height: result.height });
        cache.set(sourceKey(page), turns);
      })
      .catch((err) => console.error("organise: a thumbnail failed", err))
      .finally(() => {
        drawing -= 1;
        inFlight.delete(wanted);
        const cell = cellOf(sourceKey(page));
        const now = cell && cell.isConnected ? pageOf(cell) : null;
        if (now) paint(cell, now);
        drawQueued();
      });
  }

  // Rotate: the page's image at its new turn, in the background, for a cell
  // whose page has been drawn (one never drawn is drawn at its turn when it
  // comes into view). One request per page, however often it is asked for.
  function fetchTurned(cell, page) {
    if (cell.thumbState.imgTurn === null || cached(page, page.turn)) return;
    if (inFlight.has(`${sourceKey(page)}@${page.turn}`) || queue.includes(cell)) return;
    queue.unshift(cell);
    drawQueued();
  }

  // ---- Painting --------------------------------------------------------------

  // The rotation nearest `current` that turns an image drawn at `drawn` to
  // show the page at `wanted`: a turn always goes the short way round.
  function nearestTurn(current, wanted, drawn) {
    const step = (((wanted - drawn) % 360) + 360) % 360;
    const base = current - (((current % 360) + 360) % 360);
    const options = [base + step - 360, base + step, base + step + 360];
    return options.reduce((best, deg) => (Math.abs(deg - current) < Math.abs(best - current) ? deg : best));
  }

  // The best image there is for `page`: drawn at its turn, else the one on
  // screen, else any drawn turn of it, else none (null).
  function bestTurn(page, shown) {
    if (cached(page, page.turn)) return page.turn;
    if (shown !== null && cached(page, shown)) return shown;
    const turns = cache.get(sourceKey(page));
    return turns && turns.size ? turns.keys().next().value : null;
  }

  // The page fitted into the box, as drawn; the CSS rotation turns the box,
  // which fits either way since the box is square.
  function paperSize(thumb) {
    const { width, height } = thumb || ORGANISE_THUMB_PLACEHOLDER;
    const scale = ORGANISE_THUMB_BOX_PX / Math.max(width, height);
    return { width: Math.round(width * scale), height: Math.round(height * scale) };
  }

  function setTurn(cell, deg, instant) {
    const state = cell.thumbState;
    if (deg === state.css && state.cssSet) return;
    const paper = cell.paper;
    if (instant) paper.style.transition = "none";
    paper.style.setProperty("--turn", `${deg}deg`);
    if (instant) {
      void paper.offsetWidth; // the turn lands before transitions come back
      paper.style.transition = "";
    }
    state.css = deg;
    state.cssSet = true;
  }

  function showImage(cell, turn, thumb) {
    const { width, height } = paperSize(thumb);
    cell.paper.style.width = `${width}px`;
    cell.paper.style.height = `${height}px`;
    if (thumb && cell.img.src !== thumb.src) cell.img.src = thumb.src;
    cell.img.hidden = !thumb;
    cell.thumbState.imgTurn = thumb ? turn : null;
  }

  // Shows `page` in `cell`. A change of turn rotates the image on screen;
  // a better image takes its place only once that rotation has ended.
  function paint(cell, page) {
    const state = cell.thumbState;
    const turning = state.turn !== null && state.turn !== page.turn;
    state.turn = page.turn;
    if (turning) state.turnUntil = performance.now() + turnMs();
    const settling = performance.now() < state.turnUntil;
    const pick = settling && state.imgTurn !== null ? state.imgTurn : bestTurn(page, state.imgTurn);
    const swap = pick !== state.imgTurn || (pick === null && !state.cssSet);
    if (swap) showImage(cell, pick, pick === null ? null : cached(page, pick));
    setTurn(cell, nearestTurn(state.css, page.turn, pick ?? 0), swap || !turning);
    clearTimeout(state.settleTimer);
    if (settling && pick !== page.turn) {
      state.settleTimer = setTimeout(() => {
        const now = cell.isConnected ? pageOf(cell) : null;
        if (now) paint(cell, now);
      }, Math.max(0, state.turnUntil - performance.now()));
    }
  }

  return {
    observe: (cell) => observer.observe(cell),
    forget: (cell) => {
      observer.unobserve(cell);
      clearTimeout(cell.thumbState.settleTimer);
    },
    paint,
    fetchTurned,
    newState: () => ({ imgTurn: null, css: 0, cssSet: false, turn: null, turnUntil: 0, settleTimer: 0 }),
    // What the delete confirmation shows of a page: the image on screen (or
    // null) and the rotation it is shown at.
    view: (cell, page) => {
      const drawn = cell.thumbState.imgTurn;
      const thumb = drawn === null ? null : cached(page, drawn);
      return { thumb, turn: (((page.turn - (thumb ? drawn : 0)) % 360) + 360) % 360 };
    },
    freeze: () => { frozen = true; },
    thaw: () => {
      frozen = false;
      drawQueued();
    },
  };
}
