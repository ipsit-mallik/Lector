const openPdfBtn = document.getElementById("openPdfBtn");
const settingsNav = document.getElementById("settingsNav");
const recentArea = document.getElementById("recentArea");
const recentCount = document.getElementById("recentCount");
const recentViewGridBtn = document.getElementById("recentViewGridBtn");
const recentViewListBtn = document.getElementById("recentViewListBtn");

async function openPath(path) {
  await callApi("open_pdf", path);
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

openPdfBtn.addEventListener("click", async () => {
  const result = await openDialog.open({ dir: null });
  if (result && result.path) await openPath(result.path);
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

function buildEmptyState() {
  const el = document.createElement("div");
  el.className = "empty-state";
  el.innerHTML = `
    <h2>Nothing open yet</h2>
    <p>Open a PDF and Lector will keep it here. Everything stays on this computer.</p>
  `;
  return el;
}

// Remove-from-Recent affordance (Milestone 8.9, docs/DESIGN_SYSTEM.md).
// A real <button>, not a click handler on a styled <div> — the
// accessibility baseline's "every interactive element is a real button"
// rule, and what gives it independent keyboard focus regardless of
// whether its container is ever made focusable. stopPropagation keeps
// this click from also bubbling into the card/row's own "open" handler.
// Shared by both buildCard() (grid) and buildListRow() (list) — same
// control, just laid out differently by its container's CSS.
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
  card.className = "recent-card";
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

  card.appendChild(buildRemoveBtn(entry));

  card.addEventListener("click", () => openPath(entry.path));
  card.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openPath(entry.path);
    }
  });
  return card;
}

// List view's row -- same interactive-surface contract as buildCard()
// (role=button/tabindex/keydown for the same nested-button reason, same
// shared buildRemoveBtn(), and deliberately keeps the "recent-card" class
// alongside its own "recent-row" one so the numbered-overlay picker below
// (which queries ".recent-card") keeps working in either view without
// its own view-aware branch.
function buildListRow(entry) {
  const row = document.createElement("div");
  row.className = "recent-card recent-row";
  row.setAttribute("role", "button");
  row.tabIndex = 0;
  row.setAttribute("aria-label", `Open ${entry.name}`);

  const thumb = document.createElement("div");
  thumb.className = "recent-thumb";
  if (entry.thumbnail) {
    const img = document.createElement("img");
    img.src = `data:image/png;base64,${entry.thumbnail}`;
    thumb.appendChild(img);
  }
  row.appendChild(thumb);

  const title = document.createElement("div");
  title.className = "title row-title";
  title.textContent = entry.name;
  row.appendChild(title);

  const lastOpened = document.createElement("div");
  lastOpened.className = "row-col row-last-opened";
  lastOpened.textContent = entry.relative_time;
  row.appendChild(lastOpened);

  const pages = document.createElement("div");
  pages.className = "row-col row-pages";
  const noun = entry.page_count === 1 ? "page" : "pages";
  pages.textContent = entry.page_count ? `${entry.page_count} ${noun}` : "—";
  row.appendChild(pages);

  // progress_percent is null when the file has never been opened far
  // enough to record a position (src/lector/features/home/recent.py) --
  // shown as "Not started" text rather than a 0%-filled bar, so a reader
  // cannot mistake "no data yet" for "read almost nothing of it".
  const progress = document.createElement("div");
  progress.className = "row-col row-progress";
  if (entry.progress_percent === null || entry.progress_percent === undefined) {
    const label = document.createElement("span");
    label.className = "progress-label";
    label.textContent = "Not started";
    progress.appendChild(label);
  } else {
    const bar = document.createElement("div");
    bar.className = "progress-bar";
    const fill = document.createElement("div");
    fill.className = "progress-bar-fill";
    fill.style.width = `${entry.progress_percent}%`;
    bar.appendChild(fill);
    const label = document.createElement("span");
    label.className = "progress-label";
    label.textContent = `${entry.progress_percent}%`;
    progress.appendChild(bar);
    progress.appendChild(label);
  }
  row.appendChild(progress);

  row.appendChild(buildRemoveBtn(entry));

  row.addEventListener("click", () => openPath(entry.path));
  row.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openPath(entry.path);
    }
  });
  return row;
}

// Populated by renderRecent(), read by the picker below to map a spoken
// index back to the entry it stands for.
let recentEntries = [];

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

function renderRecent(entries) {
  recentEntries = entries;
  // Shows how many cards are actually on screen, not the cap -- see
  // .count-pill in home.css.
  if (entries.length) {
    recentCount.className = "count-pill";
    recentCount.textContent = String(entries.length);
  } else {
    recentCount.className = "count-empty";
    recentCount.textContent = "no files yet";
  }
  recentArea.innerHTML = "";
  if (!entries.length) {
    recentArea.appendChild(buildEmptyState());
    return;
  }
  const container = document.createElement("div");
  if (recentViewMode === "list") {
    container.className = "recent-list";
    entries.forEach((entry) => container.appendChild(buildListRow(entry)));
  } else {
    container.className = "recent-grid";
    entries.forEach((entry) => container.appendChild(buildCard(entry)));
  }
  recentArea.appendChild(container);
}

async function loadRecent() {
  renderRecent(await callApi("get_recent_files"));
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

function openRemoveConfirm(entry) {
  removeConfirmDesc.textContent =
    `"${entry.name}" will no longer appear in Recent.`;
  removeConfirmScrim.hidden = false;
  callApi("set_voice_context", "remove_confirm");
  return new Promise((resolve) => {
    removeConfirmResolve = resolve;
  });
}

// Voice never skips this confirmation either (docs/DESIGN_SYSTEM.md) — the
// same function backs both the icon-button click and the voice-triggered
// path (REMOVE_RECENT/REMOVE_PICKER in VOICE_ACTIONS below).
async function removeEntry(entry) {
  if (!entry) return;
  const confirmed = await openRemoveConfirm(entry);
  if (!confirmed) return;
  renderRecent(await callApi("remove_recent_file", entry.path));
}

// Mirrors openMostRecent() — "remove recent" acts on the single most-recent
// card, the only one with a natural spoken label.
async function removeMostRecent() {
  if (recentEntries.length) await removeEntry(recentEntries[0]);
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
  if (pickerActive || !recentEntries.length) return;
  pickerMode = mode;
  const cards = recentArea.querySelectorAll(".recent-card");
  cards.forEach((card, i) => {
    const badge = document.createElement("div");
    badge.className = "picker-badge";
    badge.textContent = String(i + 1);
    card.appendChild(badge);
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
    const entry = recentEntries[command.index - 1];
    const mode = pickerMode;
    hidePicker();
    if (!entry) return;
    if (mode === "remove") await removeEntry(entry);
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

// --- Push-to-talk indicator (Milestone 5) -------------------------------- //
// The sidebar's voice box was static copy until now; it reports the real
// engine state here. Home has nothing to *do* with a recognized phrase yet —
// it echoes it so push-to-talk can be verified without opening a PDF first.

// Set once voice is wired, below; `rest()` is how a lingering
// "heard" message hands the indicator back.
let voice = { rest: () => {} };

const voiceBox = document.getElementById("voiceBox");
const voiceState = document.getElementById("voiceState");
const voiceDetail = document.getElementById("voiceDetail");

const VOICE_IDLE_DETAIL =
  "Hold Space and say what you want. Offline — nothing leaves this machine.";

// The resting copy has to name the way in that the reader actually has: a
// reader who turned push-to-talk off and the wake phrase on is told to hold
// Space by the old wording, which is advice that does nothing.
function idleDetail(wake, pushToTalk) {
  if (wake && pushToTalk) {
    return "Hold Space, or say “Hey Lector”. Offline — nothing leaves this machine.";
  }
  if (wake) {
    return "Say “Hey Lector”, then your command. Offline — nothing leaves this machine.";
  }
  return VOICE_IDLE_DETAIL;
}
const HEARD_LINGER_MS = 2500;
let heardTimer = null;

function renderVoiceBox({ state, text, error, wake, pushToTalk, viaWake }) {
  clearTimeout(heardTimer);
  voiceBox.classList.toggle("listening", state === "listening");
  voiceBox.classList.toggle("unavailable", state === "unavailable" || state === "off");

  switch (state) {
    case "unavailable":
      voiceState.textContent = "VOICE UNAVAILABLE";
      // Naming the fix in place of the generic copy: this is the one screen
      // where the reader can act on it before opening anything.
      voiceDetail.textContent = error || "No speech model installed.";
      break;
    case "listening":
      voiceState.textContent = "LISTENING";
      voiceDetail.textContent = text
        ? `"${text}"`
        : viaWake
          ? "Go ahead — say your command."
          : "Go ahead — release Space when you're done.";
      break;
    case "heard":
      voiceState.textContent = "HEARD";
      voiceDetail.textContent = text ? `"${text}"` : "Didn't catch that.";
      // Back to rest through the engine rather than by rendering "idle"
      // directly: only it knows whether resting means ready, off, or
      // unavailable, and which activation modes to word the copy for.
      heardTimer = setTimeout(() => voice.rest(), HEARD_LINGER_MS);
      break;
    case "off":
      // Not a failure: docs/PRD.md makes voice an accelerator, and switching
      // it off is a supported choice rather than something to nag about.
      voiceState.textContent = "VOICE OFF";
      voiceDetail.textContent = "Turn on push-to-talk or the wake phrase in Settings.";
      break;
    default:
      voiceState.textContent = "VOICE READY";
      voiceDetail.textContent = idleDetail(wake, pushToTalk);
  }
}

(async function init() {
  // First run goes to the onboarding stub before anything else renders, so
  // the reader doesn't see Home flash past underneath it.
  if (!(await callApi("get_onboarding_seen"))) {
    window.location.href = "pages/onboarding.html";
    return;
  }
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lector-theme", theme);
  // Narrow the recognizer to Home's context (Milestone 8.4), which now
  // (Milestone 8.5) carries its own scoped commands — see VOICE_ACTIONS
  // below — on top of the always-on global ones (undo/redo/help/go home).
  await callApi("set_voice_context", "home");
  recentViewMode = await callApi("get_recent_view");
  recentViewGridBtn.setAttribute("aria-pressed", String(recentViewMode === "grid"));
  recentViewListBtn.setAttribute("aria-pressed", String(recentViewMode === "list"));
  await loadRecent();
  voice = initVoice(renderVoiceBox);
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

const VOICE_ACTIONS = {
  OPEN_SETTINGS: () => openSettings(),
  OPEN_RECENT: () => openMostRecent(),
  OPEN_PICKER: () => showPicker("open"),
  // Milestone 8.9: same OPEN_RECENT/OPEN_PICKER split, for removal instead.
  REMOVE_RECENT: () => removeMostRecent(),
  REMOVE_PICKER: () => showPicker("remove"),
  CLOSE_APP: () => closeApp(),
};

window.addEventListener("lector:command", (ev) => {
  const { command } = ev.detail || {};
  if (!command) return;
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
