const openPdfBtn = document.getElementById("openPdfBtn");
const settingsNav = document.getElementById("settingsNav");
const recentArea = document.getElementById("recentArea");
const recentCount = document.getElementById("recentCount");

async function openPath(path) {
  await callApi("open_pdf", path);
  window.location.href = "pages/reading.html";
}

openPdfBtn.addEventListener("click", async () => {
  const path = await callApi("open_pdf_dialog");
  if (path) await openPath(path);
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

function buildCard(entry) {
  const card = document.createElement("div");
  card.className = "recent-card";

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

  card.addEventListener("click", () => openPath(entry.path));
  return card;
}

// Populated by loadRecent(), read by the picker below to map a spoken
// index back to the entry it stands for.
let recentEntries = [];

async function loadRecent() {
  const entries = await callApi("get_recent_files");
  recentEntries = entries;
  recentCount.textContent = entries.length ? `${entries.length} of last 10 files` : "no files yet";
  recentArea.innerHTML = "";
  if (!entries.length) {
    recentArea.appendChild(buildEmptyState());
    return;
  }
  const grid = document.createElement("div");
  grid.className = "recent-grid";
  entries.forEach((entry) => grid.appendChild(buildCard(entry)));
  recentArea.appendChild(grid);
}

// --- Numbered-overlay picker (Milestone 8.6) ------------------------------ //
// docs/ARCHITECTURE.md's "Numbered-overlay picker": a card with no natural
// spoken label gets a number instead, and saying it does what clicking the
// card already does. The badges are purely decorative (see .picker-badge's
// `pointer-events: none` in home.css) — mouse/keyboard parity holds simply
// because clicking a card behaves identically whether or not this is
// showing, never a separate mode to click through.
let pickerActive = false;

function showPicker() {
  if (pickerActive || !recentEntries.length) return;
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
    hidePicker();
    if (entry) await openPath(entry.path);
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
  await loadRecent();
  voice = initVoice(renderVoiceBox);
})();

// --- Voice commands (Milestone 8.5, OPEN_PICKER added in 8.6) ------------- //
// Each intent calls the exact function its equivalent click handler already
// calls, mirroring reading.js's VOICE_ACTIONS. GO_HOME/UNDO/REDO/HELP are
// global (Milestone 8.4) but not wired here: "go home" is a no-op on the
// screen that already is Home, and undo/redo/help have nothing to act on
// with no document open and no command-reference button on this screen.
const VOICE_ACTIONS = {
  OPEN_SETTINGS: () => openSettings(),
  OPEN_RECENT: () => openMostRecent(),
  OPEN_PICKER: () => showPicker(),
};

window.addEventListener("lector:command", (ev) => {
  const { command } = ev.detail || {};
  if (!command) return;
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
