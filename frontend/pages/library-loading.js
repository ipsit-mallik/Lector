// What a library page (Recent, Favorites) shows while its list loads: a
// skeleton shaped like the view it is in, and a placeholder in each thumbnail
// slot until that thumbnail arrives. home.js owns when a load starts and what
// replaces the skeleton; this file only builds the placeholders and times them.
//
// The skeleton is shaped from what the page knows before any bridge call: the
// grid/list view and the last known file count, which the page server writes
// into <html> (shared/frontend_server.py). It appears only if the list is
// still missing after SKELETON_DELAY_MS, so a quick load goes straight to the
// content without a flash, and once up it stays SKELETON_MIN_VISIBLE_MS so it
// never blinks. It sits in its own layer over #recentArea, and fades out over
// the content drawn underneath it: row for row and card for card the same
// size, so nothing moves.
//
// The rows themselves don't wait for thumbnails (measured: rendering them was
// ~83% of a cold load), so each slot fills in on its own as get_thumbnails
// answers, a few files at a time and in the order on screen.
//
// The helpers above the DOM builders touch no DOM, so
// tests/test_library_loading_behavior.py can run them under Node.

const SKELETON_DELAY_MS = 150;
const SKELETON_MIN_VISIBLE_MS = 300;
// A list of unknown length gets this many placeholders; a known one gets one
// per file up to the cap (Recent's own cap; Favorites has none).
const SKELETON_FALLBACK_COUNT = 6;
const SKELETON_MAX_COUNT = 20;
const THUMBNAIL_BATCH_SIZE = 4;

// The server writes counts as attribute text; anything but a whole number
// (the page's empty default) means "not known".
function parseKnownCount(text) {
  if (typeof text !== "string" || !/^\d+$/.test(text)) return null;
  return Number.parseInt(text, 10);
}

function skeletonCount(lastKnown) {
  if (lastKnown === null || lastKnown === undefined) return SKELETON_FALLBACK_COUNT;
  return Math.min(lastKnown, SKELETON_MAX_COUNT);
}

// Times one layer's skeleton. arm(build) schedules build()'s skeleton (null
// for nothing to show) to come up after the delay; settle() resolves once the
// content may replace it: at once if it never came up (and now never will),
// or once it has been up its minimum time; release() then fades it out over
// the content drawn meanwhile. `onShow` runs as it comes up, for the page to
// clear what it was showing before.
function createSkeletonGate(layer, { onShow = () => {} } = {}) {
  let timer = null;
  let shownAt = null;

  function show(build) {
    timer = null;
    const skeleton = build();
    if (!skeleton) return;
    onShow();
    layer.replaceChildren(skeleton);
    layer.classList.remove("is-leaving");
    layer.classList.add("is-shown");
    layer.hidden = false;
    shownAt = performance.now();
  }

  return {
    arm(build) {
      clearTimeout(timer);
      if (shownAt !== null) {
        // Already up (a second load started before the first arrived):
        // reshape it in place, keeping the time it has already been shown.
        const skeleton = build();
        if (skeleton) layer.replaceChildren(skeleton);
        return;
      }
      timer = setTimeout(() => show(build), SKELETON_DELAY_MS);
    },

    settle() {
      clearTimeout(timer);
      timer = null;
      if (shownAt === null) return Promise.resolve();
      const wait = shownAt + SKELETON_MIN_VISIBLE_MS - performance.now();
      return wait > 0 ? new Promise((resolve) => setTimeout(resolve, wait)) : Promise.resolve();
    },

    release() {
      if (shownAt === null) return;
      shownAt = null;
      layer.classList.remove("is-shown");
      layer.classList.add("is-leaving");
      const done = (ev) => {
        // A placeholder's own shimmer ending (reduced motion) is not the fade.
        if (ev.target !== layer) {
          layer.addEventListener("animationend", done, { once: true });
          return;
        }
        if (shownAt !== null) return;
        layer.classList.remove("is-leaving");
        layer.hidden = true;
        layer.replaceChildren();
      };
      layer.addEventListener("animationend", done, { once: true });
    },
  };
}

// Fetches the thumbnails of entries listed as `thumbnail_pending`, in the order
// asked, THUMBNAIL_BATCH_SIZE per bridge call and one call at a time, and hands
// each to onLoaded(path, png) — png null when there is none or the call failed,
// so its placeholder still clears. A path already queued is not asked for again.
//
// Batched because, measured in the app, a call per thumbnail cost about 39ms
// each against about 7ms of rendering, so 20 took twice as long as all of them
// inside the list call had. Small batches keep the first ones (the top of the
// screen) arriving quickly.
//
// What arrived is kept for the page's lifetime, so rows drawn after it (the
// list arrived while the skeleton was still holding, or a re-render for a sort,
// a search or the view toggle) are drawn with it: withLoaded(entries).
function createThumbnailLoader(onLoaded) {
  const queue = [];
  const queued = new Set();
  const loaded = new Map();
  let running = false;

  async function drain() {
    if (running) return;
    running = true;
    while (queue.length) {
      const batch = queue.splice(0, THUMBNAIL_BATCH_SIZE);
      let thumbnails = {};
      try {
        thumbnails = await callApi("get_thumbnails", batch);
      } catch (err) {
        console.error(`Couldn't load the thumbnails for ${batch.join(", ")}:`, err);
      }
      batch.forEach((path) => {
        const png = thumbnails[path] || null;
        queued.delete(path);
        loaded.set(path, png);
        onLoaded(path, png);
      });
    }
    running = false;
  }

  return {
    withLoaded(entries) {
      return entries.map((entry) =>
        entry.thumbnail_pending && loaded.has(entry.path)
          ? { ...entry, thumbnail: loaded.get(entry.path), thumbnail_pending: false }
          : entry
      );
    },

    request(entries) {
      entries.forEach((entry) => {
        if (!entry.thumbnail_pending || queued.has(entry.path) || loaded.has(entry.path)) return;
        queued.add(entry.path);
        queue.push(entry.path);
      });
      drain();
    },
  };
}

// --- Skeletons ----------------------------------------------------------- //

function skeletonBlock(className) {
  const el = document.createElement("div");
  el.className = `skeleton ${className}`;
  return el;
}

// A grid card's footprint: thumbnail, a two-line name (the commonest length),
// meta line (home.css's .recent-card--skeleton).
function buildSkeletonCard() {
  const card = document.createElement("div");
  card.className = "recent-card recent-card--skeleton";
  card.append(
    skeletonBlock("skeleton-thumb"),
    skeletonBlock("skeleton-line skeleton-title"),
    skeletonBlock("skeleton-line skeleton-title"),
    skeletonBlock("skeleton-line skeleton-meta")
  );
  return card;
}

function buildGridSkeleton(count) {
  const grid = document.createElement("div");
  grid.className = "recent-grid";
  for (let i = 0; i < count; i++) grid.appendChild(buildSkeletonCard());
  return grid;
}

function buildSkeletonCell(className, bar) {
  const cell = document.createElement("div");
  cell.className = className;
  cell.appendChild(skeletonBlock(`skeleton-line ${bar}`));
  return cell;
}

// One list row: the same grid columns and height as a real one
// (recent-list.js's buildBodyRow), a thumbnail block and a bar per column.
function buildSkeletonRow() {
  const row = document.createElement("div");
  row.className = "recent-table-row recent-row--skeleton";
  const name = document.createElement("div");
  name.className = "recent-col-name recent-name-cell";
  name.append(skeletonBlock("skeleton-row-thumb"), skeletonBlock("skeleton-line skeleton-name"));
  const actions = document.createElement("div");
  actions.className = "recent-col-actions";
  row.append(
    name,
    buildSkeletonCell("recent-col-when", "skeleton-when"),
    buildSkeletonCell("recent-col-pages", "skeleton-pages"),
    actions
  );
  return row;
}

// The table card with its real header row (recent-list.js's buildHeaderRow,
// in the order the list will be sorted), so the header is already in place
// when the rows arrive. The layer is inert, so its sort buttons can't be
// reached or clicked.
function buildListSkeleton(count, sort) {
  const table = document.createElement("div");
  table.className = "recent-table";
  table.appendChild(buildHeaderRow(sort, () => {}));
  for (let i = 0; i < count; i++) table.appendChild(buildSkeletonRow());
  mountIcons(table);
  return table;
}

// The one entry point for both pages and both views. Null when there is
// nothing to show: a list known to be empty gets its empty state, not
// placeholders for files that aren't there.
function buildLibrarySkeleton(mode, count, sort) {
  if (!count) return null;
  return mode === "list" ? buildListSkeleton(count, sort) : buildGridSkeleton(count);
}

// --- Thumbnail slots ----------------------------------------------------- //

function buildThumbnailImg(png) {
  const img = document.createElement("img");
  img.src = `data:image/png;base64,${png}`;
  img.alt = "";
  return img;
}

// Fills a grid card's or list row's thumbnail slot: the image when the entry
// has it, a placeholder (and the path to find the slot by later) when it is
// still to come, and nothing — the bare paper — when there is none to show.
function mountThumbnail(slot, entry) {
  if (entry.thumbnail) {
    slot.appendChild(buildThumbnailImg(entry.thumbnail));
    return;
  }
  if (!entry.thumbnail_pending) return;
  slot.dataset.thumbPath = entry.path;
  slot.appendChild(skeletonBlock("thumb-pending"));
}

// Puts an arrived thumbnail into every slot waiting for `path` under `root`.
// Matched by dataset rather than a selector, since a path can hold any
// character a selector would need escaped.
function showThumbnail(root, path, png) {
  root.querySelectorAll("[data-thumb-path]").forEach((slot) => {
    if (slot.dataset.thumbPath !== path) return;
    delete slot.dataset.thumbPath;
    const pending = slot.querySelector(".thumb-pending");
    if (pending) pending.remove();
    if (!png) return;
    const img = buildThumbnailImg(png);
    img.classList.add("thumb-arrived");
    slot.prepend(img);
  });
}
