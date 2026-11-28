// Fetches an SVG from frontend/shared/icons/ and inlines it (instead of
// <img src="...">) so its stroke/fill can use currentColor and follow
// whatever CSS color the containing button applies for its state.
//
// The icons directory is resolved relative to this script's own URL
// (frontend/js/icons.js), not the loading page's URL — otherwise
// frontend/index.html (one level up from frontend/pages/*.html) would
// resolve a page-relative "../shared/icons/" outside frontend/ entirely.
const _iconsBaseUrl = new URL("../shared/icons/", document.currentScript.src);
const _iconCache = new Map();

// A fetch that "succeeds" with an error page (a 404 or 500 body, or HTML in place
// of the file) used to be inlined as the icon, so it rendered as nothing, and was
// then cached for the life of the page, so it never recovered: every icon that
// asked during a bad moment stayed blank while the rest of the page worked. An
// icon is now only ever real SVG text; anything else is retried, and a failure is
// never cached.
const ICON_FETCH_ATTEMPTS = 3;
const ICON_RETRY_DELAY_MS = 200;

async function fetchIconText(name) {
  let failure;
  for (let attempt = 0; attempt < ICON_FETCH_ATTEMPTS; attempt++) {
    if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, ICON_RETRY_DELAY_MS * attempt));
    try {
      const res = await fetch(new URL(`${name}.svg`, _iconsBaseUrl));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      if (!text.trimStart().startsWith("<svg")) throw new Error("not an SVG");
      rememberIcon(name, text);
      return text;
    } catch (err) {
      failure = err;
    }
  }
  throw new Error(`icon "${name}" could not be loaded: ${failure.message}`);
}

// The cache holds the in-flight promise, so many elements asking for one icon
// share a single request, and drops it again if that request failed.
function loadIcon(name) {
  if (!_iconCache.has(name)) {
    const pending = fetchIconText(name);
    _iconCache.set(name, pending);
    pending.catch(() => _iconCache.delete(name));
  }
  return _iconCache.get(name);
}

// One icon failing leaves that one slot empty and says so; it must not take the
// rest of the page's start-up down with it (a rejected mountIcons() used to
// abort a page's whole init).
async function mountIcon(el, name) {
  try {
    el.innerHTML = await loadIcon(name);
  } catch (err) {
    console.error(err.message);
  }
}

// Every icon fetched is kept for the session (sessionStorage), so a page
// that must be whole on its first paint (Home's Tools page, coming back from
// Organise pages) can draw the icons it has seen before at once, without
// waiting for a fetch. Best effort: a full or unavailable storage only means
// the icon is fetched as before.
const ICON_SESSION_PREFIX = "lector-icon:";

function rememberIcon(name, text) {
  try {
    sessionStorage.setItem(ICON_SESSION_PREFIX + name, text);
  } catch {
    // Not kept: the next page fetches it.
  }
}

// Draws, at once, every empty icon under `root` this session has already
// fetched; the rest are left for mountIcons/mountMissingIcons.
function mountIconsFromCache(root) {
  for (const el of root.querySelectorAll("[data-icon]")) {
    if (el.firstChild || (el.parentElement && el.parentElement.closest("[data-icon]"))) continue;
    let text = null;
    try {
      text = sessionStorage.getItem(ICON_SESSION_PREFIX + el.dataset.icon);
    } catch {
      text = null;
    }
    if (text && text.trimStart().startsWith("<svg")) el.innerHTML = text;
  }
}

// Only the icons under `root` not drawn yet (empty): a dialog that updates
// its content in place mounts what it has just added, and leaves the icons
// already there alone rather than writing every one of them again.
function mountMissingIcons(root) {
  const targets = Array.from(root.querySelectorAll("[data-icon]")).filter(
    (el) => !el.firstChild && (!el.parentElement || !el.parentElement.closest("[data-icon]"))
  );
  return Promise.all(targets.map((el) => mountIcon(el, el.dataset.icon)));
}

async function mountIcons(root = document) {
  // Skip any element whose ancestor also carries data-icon: mounting the
  // ancestor first would overwrite its innerHTML (and this descendant) anyway,
  // so nesting two data-icon elements is always a markup mistake, not a valid
  // "icon inside an icon" case.
  const targets = Array.from(root.querySelectorAll("[data-icon]")).filter(
    (el) => !el.parentElement || !el.parentElement.closest("[data-icon]")
  );
  await Promise.all(targets.map((el) => mountIcon(el, el.dataset.icon)));
}
