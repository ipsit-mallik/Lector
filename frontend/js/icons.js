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

async function loadIcon(name) {
  if (!_iconCache.has(name)) {
    const res = await fetch(new URL(`${name}.svg`, _iconsBaseUrl));
    _iconCache.set(name, await res.text());
  }
  return _iconCache.get(name);
}

async function mountIcon(el, name) {
  el.innerHTML = await loadIcon(name);
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
