const openPdfBtn = document.getElementById("openPdfBtn");
const settingsNav = document.getElementById("settingsNav");
const recentArea = document.getElementById("recentArea");
const recentCount = document.getElementById("recentCount");
const recentViewGridBtn = document.getElementById("recentViewGridBtn");
const recentViewListBtn = document.getElementById("recentViewListBtn");
const viewToggle = document.getElementById("viewToggle");
const headerSearch = document.getElementById("headerSearch");
const headerSearchInput = document.getElementById("headerSearchInput");

async function openPath(path) {
  try {
    await callApi("open_pdf", path);
  } catch (err) {
    // A dropped or listed file can be gone or damaged by now. Say so rather
    // than leave the reader on a screen where nothing happened.
    console.error("open_pdf failed:", err);
    showToast("Couldn't open that PDF. It may be damaged or no longer there.");
    return;
  }
  window.location.href = "pages/reading.html";
}

// In-app Open dialog (Milestone 8.7), replacing the native
// `create_file_dialog()` the voice router could not see or drive.
const openDialog = createFileBrowser({
  scrimId: "openDialogScrim",
  listId: "openDialogList",
  pathId: "openDialogPath",
  quickId: "openDialogQuick",
  treeId: "openDialogTree",
  searchId: "openDialogSearch",
  cancelBtnId: "openDialogCancelBtn",
  mode: "open",
  voiceContext: "open_dialog",
  restingContext: "home",
});

// One flow for every way to ask "open a PDF": the header button, the empty
// state's button, Ctrl+O, the voice command, and a hand-off from another screen.
async function openPdfFlow() {
  if (openDialog.isOpen()) return;
  const result = await openDialog.open({ dir: null });
  if (result && result.path) await openPath(result.path);
}

openPdfBtn.addEventListener("click", openPdfFlow);

// Ctrl+O is the keyboard route on every page — including those where the
// header button is hidden (an empty list, Favorites). Not while a dialog or
// the commands panel is up: they own the keyboard.
document.addEventListener("keydown", (ev) => {
  if ((ev.key || "").toLowerCase() !== "o" || !(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
  ev.preventDefault();
  if (isModalOpen()) return;
  openPdfFlow();
});

function isModalOpen() {
  return Boolean(document.querySelector(".dialog-scrim:not([hidden])")) || !referenceDialog.hidden;
}

// Set by Settings or Reading when the reader says "open a PDF" there: the Open
// dialog only exists on Home, so they navigate here and leave this note (the
// same one-shot localStorage route the Favorites section already uses).
const OPEN_DIALOG_HANDOFF_KEY = "lector-open-dialog";

// A PDF dropped anywhere on the window (frontend/js/filedrop.js). Opens it
// unless a dialog is up: a file arriving mid-decision would navigate away from
// a prompt the reader is in the middle of answering.
onFileDrop((path) => {
  if (document.querySelector(".dialog-scrim:not([hidden])")) {
    showToast("Close this window first, then drop the file again.");
    return;
  }
  openPath(path);
});

// Shared by the sidebar click and the voice OPEN_SETTINGS command
// (Milestone 8.5) so both ways into Settings go through one function.
function openSettings() {
  window.location.href = "pages/settings.html";
}

settingsNav.addEventListener("click", openSettings);

// Opens the single most-recent entry — the only Recent card with a natural
// spoken label ("recent" already means "most recent" per docs/PRD.md). A
// specific other card is reached instead by the numbered-overlay picker
// below (Milestone 8.6).
async function openMostRecent() {
  const entries = await callApi("get_recent_files");
  if (entries.length) await openPath(entries[0].path);
}

const EMPTY_STATE_COPY = {
  recent: {
    heading: "Nothing open yet",
    body: "Open a PDF and Lector will keep it here. Everything stays on this computer.",
  },
  favorites: {
    heading: "No favorites yet",
    body: "Star a PDF in Recent and it will be kept here, even after it leaves the Recent list.",
  },
};

// The "TRY SAYING" chips. Not typed here: they come from the voice command
// registry (reference.try_saying, via get_command_reference) so a chip can only
// ever be a phrase the router accepts and this page handles. Filled in by
// init() before the first render; if the registry can't be reached the row is
// left out rather than guessed at.
let trySayingPhrases = [];

function buildEmptyIcon(name) {
  const circle = document.createElement("div");
  circle.className = "empty-state-icon";
  circle.dataset.icon = name;
  circle.setAttribute("aria-hidden", "true");
  mountIcon(circle, name);
  return circle;
}

function buildTrySaying() {
  if (!trySayingPhrases.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "empty-state-voice";

  const label = document.createElement("p");
  label.className = "empty-state-voice-label";
  const micIcon = document.createElement("span");
  micIcon.dataset.icon = "mic";
  micIcon.setAttribute("aria-hidden", "true");
  mountIcon(micIcon, "mic");
  label.append(micIcon, "Try saying");

  const chips = document.createElement("ul");
  chips.className = "empty-state-chips";
  trySayingPhrases.forEach((phrase) => {
    const chip = document.createElement("li");
    chip.textContent = `\u201C${phrase}\u201D`;
    chips.appendChild(chip);
  });
  wrap.append(label, chips);
  return wrap;
}

// Recent's empty state doubles as onboarding (docs/mockups/02): the card shows
// the three ways in — the button, a dropped file, and voice. Favorites keeps
// the plainer card, since "star something in Recent" has nothing to open.
function buildEmptyState(section) {
  const copy = EMPTY_STATE_COPY[section];
  const el = document.createElement("section");
  el.className = "empty-state";
  el.setAttribute("aria-labelledby", "emptyStateTitle");

  const heading = document.createElement("h2");
  heading.id = "emptyStateTitle";
  heading.textContent = copy.heading;
  const body = document.createElement("p");
  body.className = "empty-state-body";
  body.textContent = copy.body;
  el.append(buildEmptyIcon(section === "recent" ? "empty_doc" : "favorites"), heading, body);

  // Favorites' only way forward is back to Recent, where the stars are. The
  // empty state is a dead end without it (docs/DESIGN_SYSTEM.md: a message
  // *and* an action), and it is the mouse equivalent of "go to Recent".
  if (section !== "recent") {
    const toRecent = document.createElement("button");
    toRecent.className = "btn btn-secondary empty-state-action";
    toRecent.type = "button";
    toRecent.textContent = "Go to Recent";
    toRecent.addEventListener("click", () => setHomeSection("recent"));
    el.appendChild(toRecent);
    return el;
  }

  const openBtn = document.createElement("button");
  openBtn.className = "btn empty-state-open";
  openBtn.type = "button";
  const openIcon = document.createElement("span");
  openIcon.dataset.icon = "open_pdf";
  mountIcon(openIcon, "open_pdf");
  openBtn.append(openIcon, "Open PDF");
  openBtn.addEventListener("click", openPdfFlow);

  const dropHint = document.createElement("p");
  dropHint.className = "empty-state-drop";
  dropHint.textContent = "or drop a file anywhere in this window";

  el.append(openBtn, dropHint);
  const voice = buildTrySaying();
  if (voice) el.appendChild(voice);
  return el;
}

// A search that matches nothing. The same card as the other empty states, with
// the one way out: clear the search. The query goes in via textContent, so
// whatever was typed is shown as text and never as markup.
function buildNoMatchesState(query) {
  const el = document.createElement("section");
  el.className = "empty-state";
  el.setAttribute("aria-labelledby", "emptyStateTitle");

  const heading = document.createElement("h2");
  heading.id = "emptyStateTitle";
  heading.textContent = `No PDFs match “${query.trim()}”`;
  const body = document.createElement("p");
  body.className = "empty-state-body";
  body.textContent = `Nothing in ${SECTION_TITLES[homeSection]} has that in its name.`;

  const clearBtn = document.createElement("button");
  clearBtn.className = "btn btn-secondary empty-state-action";
  clearBtn.type = "button";
  clearBtn.textContent = "Clear";
  clearBtn.addEventListener("click", () => {
    librarySearch.clear();
    headerSearchInput.focus();
  });

  el.append(buildEmptyIcon("search"), heading, body, clearBtn);
  return el;
}

// Remove-from-Recent affordance (Milestone 8.9, docs/DESIGN_SYSTEM.md).
// A real <button>, not a click handler on a styled <div> — the
// accessibility baseline's "every interactive element is a real button"
// rule, and what gives it independent keyboard focus regardless of
// whether its container is ever made focusable. stopPropagation keeps
// this click from also bubbling into the card's own "open" handler. Grid
// only: the list view reaches the same removal through its row "⋯" menu
// (recent-list.js).
function buildRemoveBtn(entry) {
  const removeBtn = document.createElement("button");
  removeBtn.className = "icon-btn recent-remove-btn";
  removeBtn.dataset.icon = "remove";
  removeBtn.title = "Remove from Recent";
  removeBtn.setAttribute("aria-label", `Remove ${entry.name} from Recent`);
  removeBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    removeEntry(entry);
  });
  mountIcon(removeBtn, "remove");
  return removeBtn;
}
function buildCard(entry) {
  const card = document.createElement("div");
  // .recent-item: what the numbered-overlay picker below badges, in either
  // view (list rows carry it too).
  card.className = "recent-card recent-item card-lift";
  // docs/DESIGN_SYSTEM.md's accessibility baseline ("never a clickable
  // div") can't be met with a literal <button> here, since the card
  // also contains its own nested, independently-focusable remove
  // <button> below -- a <button> cannot contain another <button>. This
  // is the standard fallback for that exact composite-control shape:
  // role="button" + tabindex so it is reachable and announced like one,
  // plus an Enter/Space handler so keyboard activation matches click.
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.setAttribute("aria-label", `Open ${entry.name}`);

  const thumb = document.createElement("div");
  thumb.className = "recent-thumb";
  if (entry.thumbnail) {
    const img = document.createElement("img");
    img.src = `data:image/png;base64,${entry.thumbnail}`;
    thumb.appendChild(img);
  }
  card.appendChild(thumb);

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = entry.name;
  card.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "meta";
  const noun = entry.page_count === 1 ? "page" : "pages";
  meta.textContent = entry.page_count
    ? `${entry.relative_time} · ${entry.page_count} ${noun}`
    : entry.relative_time;
  card.appendChild(meta);

  const favBtn = buildFavoriteBtn(entry, entry.name, toggleFavorite);
  mountIcon(favBtn, favBtn.dataset.icon);
  card.appendChild(favBtn);
  // Removing from Recent only makes sense in Recent: a favorite may not be
  // listed there at all, and unstarring it is the star's job.
  if (homeSection === "recent") card.appendChild(buildRemoveBtn(entry));

  card.addEventListener("click", () => openPath(entry.path));
  card.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openPath(entry.path);
    }
  });
  return card;
}

// Populated by renderRecent(). recentEntries keeps the backend's order
// (most recent first, which "open recent"/"remove recent" rely on);
// displayedEntries is the order on screen — the list view may be sorted
// differently — and is what the picker below maps a spoken number onto.
let recentEntries = [];
let displayedEntries = [];

// Which list the content area shows: "recent" (the default) or "favorites",
// chosen by the sidebar, voice, or the Settings page's Favorites item. The
// two share everything — the grid/list toggle, sorting, the row menu, the
// numbered picker — so one render path serves both and `recentEntries` simply
// holds whichever list is on screen.
const sectionTitle = document.getElementById("sectionTitle");
const recentNav = document.getElementById("recentNav");
const favoritesNav = document.getElementById("favoritesNav");
const SECTION_TITLES = { recent: "Recent", favorites: "Favorites" };
const SECTION_LOADERS = { recent: "get_recent_files", favorites: "get_favorites" };
let homeSection = "recent";

// The header search belongs to the page it is on: it is named for it, and
// clears when the reader moves to another.
const headerSearchClear = document.getElementById("headerSearchClear");
const librarySearch = createLibrarySearch({
  wrap: headerSearch,
  input: headerSearchInput,
  clearBtn: headerSearchClear,
  onChange: () => {
    // The picker's numbers belong to the rows it was shown on.
    hidePicker();
    renderRecent(recentEntries);
  },
  canFocus: () => !headerSearch.classList.contains("is-absent"),
  isBlocked: () => isModalOpen() || pickerActive,
});

function syncSectionChrome() {
  sectionTitle.textContent = SECTION_TITLES[homeSection];
  const placeholder = searchPlaceholder(SECTION_TITLES[homeSection]);
  headerSearchInput.placeholder = placeholder;
  headerSearchInput.setAttribute("aria-label", placeholder);
  [[recentNav, "recent"], [favoritesNav, "favorites"]].forEach(([nav, section]) => {
    const active = homeSection === section;
    nav.classList.toggle("active", active);
    if (active) nav.setAttribute("aria-current", "page");
    else nav.removeAttribute("aria-current");
  });
}

async function setHomeSection(section) {
  if (section === homeSection) return;
  hidePicker();
  librarySearch.reset();
  homeSection = section;
  syncSectionChrome();
  await loadSection();
}

recentNav.addEventListener("click", () => setHomeSection("recent"));
favoritesNav.addEventListener("click", () => setHomeSection("favorites"));

// Stars or unstars `entry`, then reloads whichever list is showing. Focus
// follows the reader: if it was inside the list, it goes back to the same
// star (the re-render replaced it), or — when unstarring made the row leave
// the Favorites list — to the row that took its place. A voice-triggered
// toggle (`announce`) has no pointer or focus to show it worked, so it says so
// in a toast instead, and never moves focus.
async function toggleFavorite(entry, { announce = false } = {}) {
  const hadFocus = recentArea.contains(document.activeElement);
  const rowIndex = displayedEntries.findIndex((e) => e.path === entry.path);
  const { favorite, error } = await callApi("set_favorite", entry.path, !entry.favorite);
  if (error) {
    showToast(error);
    return;
  }
  await loadSection();
  if (announce) showToast(favorite ? "Added to Favorites." : "Removed from Favorites.");
  if (!hadFocus) return;
  const star = Array.from(recentArea.querySelectorAll(".recent-fav-btn"))
    .find((btn) => btn.dataset.favPath === entry.path);
  if (star) {
    star.focus();
    return;
  }
  const items = recentArea.querySelectorAll(".recent-item");
  if (items.length) items[Math.min(Math.max(rowIndex, 0), items.length - 1)].focus();
  else favoritesNav.focus();
}

// List view's sort (recent-list.js). Session-only: every visit to Home
// starts back at "Last active, newest first".
let recentSort = DEFAULT_RECENT_SORT;

// Below this width the table hides Last active and Pages (home.css), and
// their sort buttons with them. A sort on either would keep ordering the
// rows with nothing on screen to show or change it, so narrowing the window
// puts the order back to the default. Keep in step with the media query.
const NARROW_RECENT_QUERY = "(max-width: 860px)";
window.matchMedia(NARROW_RECENT_QUERY).addEventListener("change", (ev) => {
  if (ev.matches && recentSort.key !== "name") {
    recentSort = DEFAULT_RECENT_SORT;
    renderRecent(recentEntries);
  }
});

// Grid/list toggle (Recent screen, Motion & elevation foundation pass):
// a persisted preference (settings.json via get_recent_view/
// set_recent_view), not per-session UI state -- see docs/DESIGN_SYSTEM.md.
let recentViewMode = "grid";

function setRecentView(mode) {
  if (mode === recentViewMode) return;
  recentViewMode = mode;
  recentViewGridBtn.setAttribute("aria-pressed", String(mode === "grid"));
  recentViewListBtn.setAttribute("aria-pressed", String(mode === "list"));
  callApi("set_recent_view", mode);
  renderRecent(recentEntries);
}
recentViewGridBtn.addEventListener("click", () => setRecentView("grid"));
recentViewListBtn.addEventListener("click", () => setRecentView("list"));

// The list view's row handlers. Removing from the "⋯" menu goes through the
// same confirmation dialog as the grid's remove button and voice (docs/PRD.md
// gates every removal behind it). Afterwards focus moves to the row that
// took the removed one's place — or back to the same row if the reader
// cancelled — so a keyboard user isn't dropped back to the top of the page.
const recentListHandlers = {
  onSort: (key) => {
    recentSort = nextRecentSort(recentSort, key);
    renderRecent(recentEntries);
    recentArea.querySelector(`[data-sort-key="${key}"]`).focus();
  },
  onOpen: (entry) => openPath(entry.path),
  onToggleFavorite: toggleFavorite,
  onShowInFolder: async (entry) => {
    const { error } = await callApi("show_in_folder", entry.path);
    if (error) showToast(error);
  },
  onRemove: async (entry, rowIndex) => {
    await removeEntry(entry);
    const rows = recentArea.querySelectorAll(".recent-table .recent-item");
    if (rows.length) rows[Math.min(rowIndex, rows.length - 1)].focus();
    else recentViewListBtn.focus();
  },
};

// Which header controls apply right now. They are hidden, never removed, so
// the row keeps its shape (.is-absent in home.css). Nothing to search or toggle
// on an empty list, and Open PDF is Recent's only when there is a list: on an
// empty one the card's own button is the single call to action, and Favorites
// has nothing to open from.
function syncHeaderControls(hasItems) {
  headerSearch.classList.toggle("is-absent", !hasItems);
  viewToggle.classList.toggle("is-absent", !hasItems);
  openPdfBtn.classList.toggle("is-absent", !(hasItems && homeSection === "recent"));
}

// Shows how many cards are actually on screen, not the cap -- see .count-pill
// in home.css -- and "N of M" while a search is narrowing the list.
function renderCount(shown, total, filtering) {
  if (!total) {
    recentCount.className = "count-empty";
    recentCount.textContent = "no files yet";
    return;
  }
  recentCount.className = "count-pill";
  recentCount.textContent = filtering ? filteredCountLabel(shown, total) : String(total);
}

function renderRecent(entries) {
  closeRowMenu();
  recentEntries = entries;
  // A search over an empty list has nothing to apply to, and its field is
  // hidden: drop it so it can't reappear as a stale filter.
  if (!entries.length) librarySearch.reset();
  const query = librarySearch.getQuery();
  const visible = filterEntriesByQuery(entries, query);
  renderCount(visible.length, entries.length, Boolean(query.trim()));
  syncHeaderControls(entries.length > 0);
  recentArea.innerHTML = "";
  if (!entries.length) {
    displayedEntries = [];
    recentArea.appendChild(buildEmptyState(homeSection));
    return;
  }
  if (!visible.length) {
    displayedEntries = [];
    recentArea.appendChild(buildNoMatchesState(query));
    return;
  }
  if (recentViewMode === "list") {
    displayedEntries = sortRecentEntries(visible, recentSort);
    recentArea.appendChild(
      buildRecentTable(displayedEntries, recentSort, recentListHandlers, homeSection)
    );
    return;
  }
  displayedEntries = visible;
  const grid = document.createElement("div");
  grid.className = "recent-grid";
  visible.forEach((entry) => grid.appendChild(buildCard(entry)));
  recentArea.appendChild(grid);
}

async function loadSection() {
  renderRecent(await callApi(SECTION_LOADERS[homeSection]));
}

// --- Remove-from-Recent confirmation (Milestone 8.9) --------------------- //
// Reuses the .dialog-scrim/.dialog-card frame verbatim, the same way
// SaveDialog (frontend/pages/save-dialog.js) does — no new dialog chrome.
const removeConfirmScrim = document.getElementById("removeConfirmScrim");
const removeConfirmDesc = document.getElementById("removeConfirmDesc");
const removeConfirmCancelBtn = document.getElementById("removeConfirmCancelBtn");
const removeConfirmRemoveBtn = document.getElementById("removeConfirmRemoveBtn");

let removeConfirmResolve = null;

// Voice-drivable regardless of how the dialog was opened (index.html marks
// its scrim `.voice-dialog` so push-to-talk keeps working while it's shown,
// the same way the Open dialog's scrim already does) — restores the `home`
// context on close since this dialog is only ever reached from Home.
function closeRemoveConfirm(confirmed) {
  removeConfirmScrim.hidden = true;
  callApi("set_voice_context", "home");
  if (removeConfirmResolve) {
    removeConfirmResolve(confirmed);
    removeConfirmResolve = null;
  }
}

removeConfirmCancelBtn.addEventListener("click", () => closeRemoveConfirm(false));
removeConfirmRemoveBtn.addEventListener("click", () => closeRemoveConfirm(true));

// Names the file the way the current view does: the filename on a grid card,
// the PDF's own title in the list view -- whichever way removal was started
// (button, "⋯" menu or voice), so the dialog always matches what is on screen.
function removalLabel(entry) {
  return recentViewMode === "list" ? entry.title : entry.name;
}

function openRemoveConfirm(entry) {
  removeConfirmDesc.textContent =
    `"${removalLabel(entry)}" will no longer appear in Recent.`;
  removeConfirmScrim.hidden = false;
  callApi("set_voice_context", "remove_confirm");
  return new Promise((resolve) => {
    removeConfirmResolve = resolve;
  });
}

// Voice never skips this confirmation either (docs/DESIGN_SYSTEM.md) — the
// same function backs both the icon-button click and the voice-triggered
// path (REMOVE_RECENT/REMOVE_PICKER in VOICE_ACTIONS below), and the list
// view's "⋯" menu (recentListHandlers).
//
// A removal that actually happened ends on a toast with Undo. The backend
// keeps only the last removal, so a new one retracts the previous toast
// rather than leaving an Undo on screen that would restore the wrong file.
// One that didn't (a stale row, already gone) shows nothing and leaves the
// previous toast alone, since its Undo still works.
let dismissRemoveToast = () => {};

const UNDO_FAILURE_MESSAGES = {
  full: "Recent is full, so that file couldn't be put back.",
};

async function undoRemoval() {
  const { status } = await callApi("undo_remove_recent_file");
  // Reload what is on screen rather than render the returned Recent list: the
  // reader may have switched to Favorites since the toast appeared.
  await loadSection();
  if (UNDO_FAILURE_MESSAGES[status]) showToast(UNDO_FAILURE_MESSAGES[status]);
}

async function removeEntry(entry) {
  if (!entry) return;
  if (!(await openRemoveConfirm(entry))) return;
  const { entries, removed } = await callApi("remove_recent_file", entry.path);
  renderRecent(entries);
  if (!removed) return;
  dismissRemoveToast();
  dismissRemoveToast = showToast("Removed from Recent. The file stays on disk.", {
    actionLabel: "Undo",
    onAction: undoRemoval,
  });
}

// Mirrors openMostRecent() — "remove recent" acts on the single most-recent
// card, the only one with a natural spoken label.
async function removeMostRecent() {
  if (recentEntries.length) await removeEntry(recentEntries[0]);
}

// "remove recent" / "remove a file" mean Recent. While Favorites is showing
// they would be ambiguous (the file may not even be in Recent), so they ask
// for Recent first instead of guessing.
function requireRecentSection() {
  if (homeSection === "recent") return true;
  showToast("Say “show recent files” first, then remove it from Recent.");
  return false;
}

// "favorite recent": the single most recently opened file, whichever list is
// showing — the same natural spoken label "open recent" uses.
async function favoriteMostRecent() {
  const [latest] = await callApi("get_recent_files");
  if (latest) await toggleFavorite(latest, { announce: true });
}

// --- Numbered-overlay picker (Milestone 8.6) ------------------------------ //
// docs/ARCHITECTURE.md's "Numbered-overlay picker": a card with no natural
// spoken label gets a number instead, and saying it does what clicking the
// card already does. The badges are purely decorative (see .picker-badge's
// `pointer-events: none` in home.css) — mouse/keyboard parity holds simply
// because clicking a card behaves identically whether or not this is
// showing, never a separate mode to click through.
let pickerActive = false;
// "open" (default, Milestone 8.6) picks a card the way clicking it does;
// "remove" (Milestone 8.9, entered via REMOVE_PICKER) routes the picked
// card through removeEntry()'s confirmation instead — the picker itself
// doesn't grow a second grammar for this, only what PICK does once said
// changes, the same way DIALOG_PICK's meaning is a property of the
// frontend's dialog mode rather than something router.py needs to know.
let pickerMode = "open";

function showPicker(mode = "open") {
  if (pickerActive || !displayedEntries.length) return;
  pickerMode = mode;
  // A list row pins its badge to its thumbnail (.picker-anchor) instead of
  // its own corner, which the table card's rounded edge would clip.
  const items = recentArea.querySelectorAll(".recent-item");
  items.forEach((item, i) => {
    const badge = document.createElement("div");
    badge.className = "picker-badge";
    badge.textContent = String(i + 1);
    (item.querySelector(".picker-anchor") || item).appendChild(badge);
  });
  pickerActive = true;
  callApi("set_voice_context", "picker");
}

function hidePicker() {
  if (!pickerActive) return;
  recentArea.querySelectorAll(".picker-badge").forEach((badge) => badge.remove());
  pickerActive = false;
  callApi("set_voice_context", "home");
}

// PICK/CANCEL only ever arrive while the picker is active (router.py scopes
// them to the `picker` context), so this is tried before VOICE_ACTIONS
// rather than added to it — that table is Home's own commands, and these
// two are not among them.
async function handlePickerCommand(command) {
  if (command.intent === "PICK") {
    const entry = displayedEntries[command.index - 1];
    const mode = pickerMode;
    hidePicker();
    if (!entry) return;
    if (mode === "remove") await removeEntry(entry);
    else if (mode === "favorite") await toggleFavorite(entry, { announce: true });
    else await openPath(entry.path);
    return;
  }
  if (command.intent === "CANCEL") {
    hidePicker();
  }
}

// Keyboard equivalent for CANCEL, the same "Escape backs out of an
// in-progress mode" convention reading.js's cancelSelection() already uses.
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && pickerActive) hidePicker();
});

// --- Voice status ---------------------------------------------------------- //
// The sidebar box reports the real engine state (frontend/js/voice-box.js,
// shared with Settings) and starts as "checking" rather than "ready".
let voice = { rest: () => {} };

const voiceBox = createVoiceBox({
  box: document.getElementById("voiceBox"),
  state: document.getElementById("voiceState"),
  detail: document.getElementById("voiceDetail"),
  live: document.getElementById("voiceLive"),
});

// The mouse/keyboard way to the same panel "what can I say" opens.
document.getElementById("voiceHelpBtn").addEventListener("click", openCommandReference);

// What the shared list needs to know about this screen (command-reference.js):
// how many rows "open number N" can reach right now, and that the Favorites
// screen leads with the Favorites section.
configureCommandReference({
  ranges: () => ({ number: displayedEntries.length }),
  focusSection: () => (homeSection === "favorites" ? "favorites" : null),
});

(async function init() {
  // First run goes to the onboarding stub before anything else renders, so
  // the reader doesn't see Home flash past underneath it.
  if (!(await callApi("get_onboarding_seen"))) {
    window.location.href = "pages/onboarding.html";
    return;
  }
  // A hand-off from Settings/Reading ("open a PDF" said there): consumed at
  // once, so a later visit can never open the dialog by surprise if this init
  // fails or redirects before reaching the end.
  const openDialogRequested = localStorage.getItem(OPEN_DIALOG_HANDOFF_KEY) === "1";
  localStorage.removeItem(OPEN_DIALOG_HANDOFF_KEY);
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lector-theme", theme);
  // Narrow the recognizer to Home's context (Milestone 8.4), which carries its
  // own scoped commands — see VOICE_ACTIONS below — on top of the global ones.
  // If that fails the box must say so below, not claim voice is ready.
  let voiceScopeError = null;
  try {
    await callApi("set_voice_context", "home");
  } catch (err) {
    voiceScopeError = `Voice commands couldn't start on this screen. ${err}`;
    console.error(voiceScopeError);
  }
  try {
    trySayingPhrases = (await callApi("get_command_reference")).try_saying || [];
  } catch (err) {
    console.error("Couldn't load the suggested phrases:", err);
  }
  recentViewMode = await callApi("get_recent_view");
  recentViewGridBtn.setAttribute("aria-pressed", String(recentViewMode === "grid"));
  recentViewListBtn.setAttribute("aria-pressed", String(recentViewMode === "list"));
  // Settings' Favorites item sends the reader here already pointed at that
  // list; the hand-off is one-shot, so a later plain visit starts on Recent.
  if (localStorage.getItem("lector-home-section") === "favorites") homeSection = "favorites";
  localStorage.removeItem("lector-home-section");
  syncSectionChrome();
  await loadSection();
  if (voiceScopeError) {
    voiceBox.showUnavailable(voiceScopeError);
  } else {
    voice = initVoice(voiceBox.render);
    voiceBox.attach(voice);
  }
  // "Open a PDF" said on Settings or Reading lands here with the dialog due.
  if (openDialogRequested) openPdfFlow();
})();

// --- Voice commands (Milestone 8.5, OPEN_PICKER added in 8.6) ------------- //
// Each intent calls the exact function its equivalent click handler already
// calls, mirroring reading.js's VOICE_ACTIONS. GO_HOME/UNDO/REDO/HELP are
// global (Milestone 8.4) but not wired here: "go home" is a no-op on the
// screen that already is Home, and undo/redo/help have nothing to act on
// with no document open and no command-reference button on this screen.
// CLOSE_APP (Milestone 8.11) is wired, unlike those — quitting is never a
// no-op regardless of which screen is active.
async function closeApp() {
  await callApi("close_app");
}

// "open number N": the Nth row of what is on screen, in the order shown — the
// same numbering the picker's badges use, so the two can never disagree.
function openNumbered(index) {
  const entry = displayedEntries[index - 1];
  if (!entry) {
    showToast(`There's no number ${index} on screen.`);
    return;
  }
  openPath(entry.path);
}

const VOICE_ACTIONS = {
  // Global commands (Milestone 8.12), reaching the same functions their
  // buttons do.
  OPEN_PDF: () => openPdfFlow(),
  OPEN_SETTINGS: () => openSettings(),
  GO_RECENT: () => setHomeSection("recent"),
  HELP: () => openCommandReference(),
  OPEN_NUMBER: (command) => openNumbered(command.index),
  OPEN_RECENT: () => openMostRecent(),
  OPEN_PICKER: () => showPicker("open"),
  // Milestone 8.9: same OPEN_RECENT/OPEN_PICKER split, for removal instead.
  REMOVE_RECENT: () => requireRecentSection() && removeMostRecent(),
  REMOVE_PICKER: () => requireRecentSection() && showPicker("remove"),
  // Favorites: navigation between the two lists, and starring by voice,
  // split into "the most recent" and "pick one" like open/remove above.
  OPEN_FAVORITES: () => setHomeSection("favorites"),
  FAVORITE_RECENT: () => favoriteMostRecent(),
  FAVORITE_PICKER: () => showPicker("favorite"),
  CLOSE_APP: () => closeApp(),
};

window.addEventListener("lector:command", (ev) => {
  const { command } = ev.detail || {};
  if (!command) return;
  // The "What can I say?" panel is a modal scope with its own listener
  // (command-reference.js), so nothing here should act behind it.
  if (!referenceDialog.hidden) return;
  // CONFIRM_REMOVE/CANCEL only ever arrive while the remove-confirmation
  // dialog is the active voice context (router.py scopes them there), so
  // it is checked first, the same way openDialog/pickerActive already are
  // below — and mirrors exactly what its Cancel/Remove buttons already do.
  if (!removeConfirmScrim.hidden) {
    if (command.intent === "CONFIRM_REMOVE") closeRemoveConfirm(true);
    else if (command.intent === "CANCEL") closeRemoveConfirm(false);
    return;
  }
  // DIALOG_PICK/DIALOG_UP/CANCEL/DIALOG_CONFIRM only ever arrive while the
  // Open dialog is the active voice context (router.py scopes them there),
  // so it is checked first, the same way pickerActive already is below.
  if (openDialog.isOpen()) {
    openDialog.handleCommand(command);
    return;
  }
  // Picker commands (PICK/CANCEL) are not in VOICE_ACTIONS above — they are
  // scoped to the `picker` context rather than Home's, so they only ever
  // arrive while pickerActive is true, and are handled separately from it.
  if (pickerActive) {
    handlePickerCommand(command);
    return;
  }
  const action = VOICE_ACTIONS[command.intent];
  if (action) action();
});
