// The header search on the library pages (Recent, Favorites; All PDFs once it
// exists). It filters the list that is already on screen by file name, in both
// views — no backend call, since home.js holds the whole list.
//
// It is collapsed to a search icon until opened, and typing is debounced: the
// list is filtered once the reader pauses, not on every key.
//
// The helpers at the top touch no DOM, so tests/test_library_search_behavior.py
// can run them under Node without a browser.

// How long typing must pause before the list is filtered.
const SEARCH_DEBOUNCE_MS = 200;

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

// The count beside a page title: "12 files", "1 file", or "3 of 12 files" while
// a search is narrowing the list. Plain text, so it reads the same in every theme.
function formatFileCount(shown, total, filtering) {
  if (!total) return "no files yet";
  const noun = total === 1 ? "file" : "files";
  return filtering ? `${shown} of ${total} ${noun}` : `${total} ${noun}`;
}

// Calls `fn` with the latest value once `schedule` has not been called for
// `delayMs`. `cancel` drops a pending call, so a value that was typed but is
// no longer wanted (the field was cleared or closed) can never arrive late.
function createDebouncer(fn, delayMs) {
  let timer = null;
  return {
    schedule(value) {
      clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        fn(value);
      }, delayMs);
    },
    cancel() {
      clearTimeout(timer);
      timer = null;
    },
  };
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

// Wires the icon button, field, its clear (x) button, Esc and the shortcuts.
// `onChange` receives the query once typing has paused (and at once for a
// clear, close or reset); `canFocus` says whether the search is currently in
// play (it is hidden on an empty list), and `isBlocked` whether a dialog or
// panel is up that should keep the keyboard to itself.
//
// Opening focuses the field. Closing — the icon again, Esc, or focus leaving
// an empty field — clears the query and hands focus to the icon if it was
// inside. A field with text stays open when focus leaves, so a filter in
// effect is never hidden.
function createLibrarySearch({ wrap, input, toggleBtn, clearBtn, onChange, canFocus, isBlocked }) {
  // What the list is filtered by: the query as of the last pause in typing.
  let committed = "";
  // A press that started inside the search. WebKit does not focus a button on
  // click, so without this the field would see focus leave, close, and then
  // have the same click open it again.
  let pressInside = false;

  function commit(value, notify = true) {
    debouncer.cancel();
    if (committed === value) return;
    committed = value;
    if (notify) onChange(value);
  }
  const debouncer = createDebouncer(commit, SEARCH_DEBOUNCE_MS);

  const isOpen = () => wrap.classList.contains("is-open");

  function syncClear() {
    const hasText = input.value.length > 0;
    clearBtn.hidden = !hasText;
    wrap.classList.toggle("has-text", hasText);
  }

  function setOpen(open) {
    wrap.classList.toggle("is-open", open);
    toggleBtn.setAttribute("aria-expanded", String(open));
  }

  function open() {
    if (!isOpen()) setOpen(true);
    input.focus();
    input.select();
  }

  function close(notify = true) {
    const hadFocus = wrap.contains(document.activeElement);
    input.value = "";
    syncClear();
    commit("", notify);
    setOpen(false);
    if (hadFocus) toggleBtn.focus();
  }

  input.addEventListener("input", () => {
    syncClear();
    debouncer.schedule(input.value);
  });

  clearBtn.addEventListener("click", () => {
    input.value = "";
    syncClear();
    commit("");
    input.focus();
  });

  // On the whole search, so Esc works whichever part has focus (field, clear
  // button or icon).
  wrap.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !isOpen()) return;
    ev.preventDefault();
    close();
  });

  toggleBtn.addEventListener("click", () => {
    if (isOpen()) close();
    else open();
  });

  // Only a press on one of the buttons needs the guard: a press on the field
  // keeps focus, and one on the padding or border should leave like any other.
  wrap.addEventListener("pointerdown", (ev) => {
    pressInside = Boolean(ev.target.closest("button"));
  });
  const endPress = () => setTimeout(() => (pressInside = false), 0);
  document.addEventListener("pointerup", endPress);
  document.addEventListener("pointercancel", endPress);
  window.addEventListener("blur", () => (pressInside = false));

  wrap.addEventListener("focusout", (ev) => {
    if (pressInside || wrap.contains(ev.relatedTarget)) return;
    // Switching to another window also blurs the field; that is not leaving it.
    if (!document.hasFocus()) return;
    if (isOpen() && input.value === "") close();
  });

  document.addEventListener("keydown", (ev) => {
    const shortcut = isSearchShortcut(ev);
    if (!shortcut || !canFocus() || isBlocked()) return;
    if (shortcut === "slash" && isTypingTarget(ev.target)) return;
    ev.preventDefault();
    open();
  });

  syncClear();
  return {
    getQuery: () => committed,
    // Empties the query but keeps the field open (the "no matches" card's Clear).
    clear: () => {
      input.value = "";
      syncClear();
      commit("");
    },
    // For a caller that is about to render anyway: empty and closed, no notice.
    reset: () => close(false),
    // The page's name, for the field and the icon that opens it.
    setPageName: (name) => {
      const placeholder = searchPlaceholder(name);
      input.placeholder = placeholder;
      input.setAttribute("aria-label", placeholder);
      toggleBtn.setAttribute("aria-label", placeholder);
    },
  };
}
