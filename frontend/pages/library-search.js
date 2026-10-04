// The header search on the library pages (Recent, Favorites; All PDFs once it
// exists). It filters the list that is already on screen by file name, live,
// in both views — no backend call, since home.js holds the whole list.
//
// The helpers at the top touch no DOM, so tests/test_library_search_behavior.py
// can run them under Node without a browser.

// Matches the file name (the grid card's label) and the PDF's own title (the
// list row's label), so whatever the reader is looking at can be found.
// Case-insensitive substring; surrounding whitespace is ignored. Returns the
// entries in their original order — sorting is the view's job.
function filterEntriesByQuery(entries, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  return entries.filter((entry) =>
    [entry.name, entry.title].some((label) => (label || "").toLowerCase().includes(needle))
  );
}

function searchPlaceholder(sectionTitle) {
  return `Search ${sectionTitle}`;
}

// The count pill while a search is narrowing the list: "3 of 12".
function filteredCountLabel(shown, total) {
  return `${shown} of ${total}`;
}

// Keys that should reach the search field from anywhere on the page. "/" is
// left alone while the reader is typing somewhere (it is a character there);
// Ctrl+F always is, since nothing else uses it on a library page.
function isSearchShortcut(ev) {
  if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey && !ev.altKey) return "slash";
  if ((ev.key === "f" || ev.key === "F") && (ev.ctrlKey || ev.metaKey) && !ev.altKey) return "find";
  return null;
}

function isTypingTarget(target) {
  if (!target || !target.tagName) return false;
  return target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;
}

// Wires the field, its clear (x) button, Esc, and the shortcuts. `onChange`
// receives the raw query on every edit; `canFocus` says whether the field is
// currently in play (it is hidden on an empty list), and `isBlocked` whether a
// dialog or panel is up that should keep the keyboard to itself.
function createLibrarySearch({ wrap, input, clearBtn, onChange, canFocus, isBlocked }) {
  function syncClear() {
    const hasText = input.value.length > 0;
    clearBtn.hidden = !hasText;
    wrap.classList.toggle("has-text", hasText);
  }

  function setQuery(value, notify = true) {
    if (input.value === value) return;
    input.value = value;
    syncClear();
    if (notify) onChange(value);
  }

  input.addEventListener("input", () => {
    syncClear();
    onChange(input.value);
  });

  clearBtn.addEventListener("click", () => {
    setQuery("");
    input.focus();
  });

  input.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    setQuery("");
    input.blur();
  });

  // At narrow widths the field is an icon until clicked (home.css); the whole
  // wrapper is the click target, not just the zero-width input inside it.
  wrap.addEventListener("click", (ev) => {
    if (ev.target === input || clearBtn.contains(ev.target)) return;
    input.focus();
  });

  document.addEventListener("keydown", (ev) => {
    const shortcut = isSearchShortcut(ev);
    if (!shortcut || !canFocus() || isBlocked()) return;
    if (shortcut === "slash" && isTypingTarget(ev.target)) return;
    ev.preventDefault();
    input.focus();
    input.select();
  });

  syncClear();
  return {
    getQuery: () => input.value,
    clear: () => setQuery(""),
    // For a caller that is about to render anyway.
    reset: () => setQuery("", false),
  };
}
