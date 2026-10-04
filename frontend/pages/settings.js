// Reads each theme's swatch colors straight from frontend/shared/theme.css's
// custom properties (via a throwaway probe element carrying data-theme),
// rather than keeping a second hardcoded copy of the palette here — theme.css
// stays the single source of truth and these preview cards can't drift from it.
const THEME_NAMES = ["light", "dark", "sepia"];
const THEME_LABELS = { light: "Light", dark: "Dark", sepia: "Sepia" };

function readThemeTokens(themeName) {
  const probe = document.createElement("div");
  probe.setAttribute("data-theme", themeName);
  probe.style.display = "none";
  document.body.appendChild(probe);
  const cs = getComputedStyle(probe);
  const tokens = {
    canvas: cs.getPropertyValue("--bg").trim(),
    surface: cs.getPropertyValue("--surface-2").trim(),
    border: cs.getPropertyValue("--border").trim(),
    text: cs.getPropertyValue("--text-primary").trim(),
    muted: cs.getPropertyValue("--border-strong").trim(),
    highlight: cs.getPropertyValue("--highlight").trim(),
  };
  probe.remove();
  return tokens;
}

const themeCardsEl = document.getElementById("themeCards");
const recentNav = document.getElementById("recentNav");
const copyRow = document.getElementById("copyRow");
const overwriteRow = document.getElementById("overwriteRow");
const rememberCheck = document.getElementById("rememberCheck");
const saveRows = [copyRow, overwriteRow];
const pushToTalkToggle = document.getElementById("pushToTalkToggle");
const wakePhraseToggle = document.getElementById("wakePhraseToggle");
const wakePhraseLabel = document.getElementById("wakePhraseLabel");
const continueRow = document.getElementById("continueRow");
const startRow = document.getElementById("startRow");
const reopenRows = [continueRow, startRow];

// Shared by the sidebar click and the voice GO_HOME command (Milestone 8.5).
function goHome() {
  window.location.href = "../index.html";
}

recentNav.addEventListener("click", goHome);

// The Open dialog only exists on Home, so "open a PDF" goes there and leaves a
// one-shot note asking Home to open it (the same localStorage route the
// Favorites hand-off below uses; home.js reads and clears the key).
const OPEN_DIALOG_HANDOFF_KEY = "lector-open-dialog";

function openPdfFromSettings() {
  localStorage.setItem(OPEN_DIALOG_HANDOFF_KEY, "1");
  goHome();
}

// Ctrl+O, the keyboard route to the same hand-off. Not while a confirmation or
// the commands panel is up: they own the keyboard.
document.addEventListener("keydown", (ev) => {
  if ((ev.key || "").toLowerCase() !== "o" || !(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
  ev.preventDefault();
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  openPdfFromSettings();
});

// A PDF dropped anywhere on the window (frontend/js/filedrop.js) opens in the
// reader, the same as picking it from Recent. Not while a dialog is up: that
// would navigate away from a prompt the reader is mid-way through answering.
onFileDrop(async (path) => {
  if (document.querySelector(".dialog-scrim:not([hidden])")) {
    showToast("Close this window first, then drop the file again.");
    return;
  }
  try {
    await callApi("open_pdf", path);
  } catch (err) {
    console.error("open_pdf failed:", err);
    showToast("Couldn't open that PDF. It may be damaged or no longer there.");
    return;
  }
  window.location.href = "reading.html";
});

// Home opens on Recent unless told otherwise; this hands it the section to
// open on (home.js reads and clears the key), the same localStorage route the
// theme already takes between pages.
document.getElementById("favoritesNav").addEventListener("click", () => {
  localStorage.setItem("lector-home-section", "favorites");
  goHome();
});

const voiceBox = createVoiceBox({
  box: document.getElementById("voiceBox"),
  state: document.getElementById("voiceState"),
  detail: document.getElementById("voiceDetail"),
  live: document.getElementById("voiceLive"),
});

// The mouse/keyboard way to the same panel "what can I say" opens.
document.getElementById("voiceHelpBtn").addEventListener("click", openCommandReference);

// The voice "close app"/"quit" command (Milestone 8.11) — see reading.js's
// identical closeApp() for why there is no separate click/keyboard wiring.
async function closeApp() {
  await callApi("close_app");
}

// Shared by a theme card's click and the voice THEME_* commands (Milestone
// 8.5). Reads the active theme from the DOM rather than taking it as a
// parameter, since the voice path has no closured `current` the way a card's
// own click handler does.
async function selectTheme(name) {
  if (document.documentElement.dataset.theme === name) return;
  document.documentElement.dataset.theme = name;
  localStorage.setItem("lector-theme", name);
  await callApi("set_theme", name);
  await renderThemeCards();
}

function buildThemeCard(name, current) {
  const t = readThemeTokens(name);
  const card = document.createElement("div");
  card.className = "theme-card" + (name === current ? " selected" : "");
  card.innerHTML = `
    <div class="theme-preview" style="background:${t.canvas}">
      <div class="theme-preview-mock" style="background:${t.surface};border:1px solid ${t.border}">
        <div class="theme-preview-line title" style="background:${t.text}"></div>
        <div class="theme-preview-line" style="background:${t.muted}"></div>
        <div class="theme-preview-line" style="width:88%;background:${t.muted}"></div>
        <div class="theme-preview-line accent" style="background:${t.highlight}"></div>
      </div>
    </div>
    <div class="theme-card-footer">
      <span class="theme-card-footer-label">${THEME_LABELS[name]}</span>
      ${name === current ? '<span class="theme-card-check" data-icon="check"></span>' : ""}
    </div>
  `;
  card.addEventListener("click", () => selectTheme(name));
  return card;
}

async function renderThemeCards() {
  const current = await callApi("get_theme");
  themeCardsEl.innerHTML = "";
  THEME_NAMES.forEach((name) => {
    themeCardsEl.appendChild(buildThemeCard(name, current));
  });
  await mountIcons(themeCardsEl);
}

function selectSaveRow(row) {
  saveRows.forEach((r) => r.classList.toggle("selected", r === row));
}

async function persistSaveBehavior() {
  if (rememberCheck.checked) {
    const mode = overwriteRow.classList.contains("selected") ? "overwrite" : "copy";
    await callApi("set_save_behavior", mode);
  } else {
    await callApi("set_save_behavior", "ask");
  }
}

// Shared by a save row's click and the voice SAVE_COPY/SAVE_OVERWRITE
// commands (Milestone 8.5).
function chooseSaveMode(mode) {
  selectSaveRow(mode === "overwrite" ? overwriteRow : copyRow);
  persistSaveBehavior();
}

saveRows.forEach((row) => {
  row.addEventListener("click", () => {
    chooseSaveMode(row === overwriteRow ? "overwrite" : "copy");
  });
});

// Saying "overwrite the original" asks first (Milestone 8.12): it makes every
// later save rewrite the reader's own PDF, and a misheard word is easy to make
// and hard to take back. Voice only — clicking the row still applies at once,
// because a click is already deliberate. Reuses the .dialog-scrim frame, and is
// a modal voice scope of its own so only "confirm overwrite" or "cancel" are
// heard while it is up.
const overwriteConfirmScrim = document.getElementById("overwriteConfirmScrim");
const overwriteConfirmBtn = document.getElementById("overwriteConfirmBtn");
const overwriteConfirmCancelBtn = document.getElementById("overwriteConfirmCancelBtn");
const overwriteConfirmCard = overwriteConfirmScrim.querySelector(".dialog-card");
let overwriteConfirmOpener = null;
// The confirmation's claim on the recognizer's grammar (voice.js
// createVoiceScope), serialized so a quick Cancel can't leave Settings' own
// commands dead.
const overwriteScope = createVoiceScope("overwrite_confirm");

function openOverwriteConfirm() {
  if (!overwriteConfirmScrim.hidden) return;
  if (overwriteRow.classList.contains("selected")) {
    showToast("“Overwrite the original” is already on.");
    return;
  }
  overwriteConfirmOpener = document.activeElement;
  overwriteConfirmScrim.hidden = false;
  // Focus lands on the dialog itself, not a button. Push-to-talk steps aside
  // for a keyboard-focused button (Space is how it is pressed), so a focused
  // Cancel would swallow the very "confirm overwrite" this dialog is waiting
  // for — and release Space would then press Cancel. With no button focused,
  // Enter does nothing, which is the safe default for this question; Tab
  // reaches the buttons, and Escape cancels.
  overwriteConfirmCard.focus();
  // The buttons still work if this fails; only the spoken confirmation is lost.
  overwriteScope.enter();
}

function closeOverwriteConfirm(confirmed) {
  if (overwriteConfirmScrim.hidden) return;
  overwriteConfirmScrim.hidden = true;
  overwriteScope.leave();
  if (confirmed) chooseSaveMode("overwrite");
  if (overwriteConfirmOpener && overwriteConfirmOpener.focus) overwriteConfirmOpener.focus();
  overwriteConfirmOpener = null;
}

overwriteConfirmBtn.addEventListener("click", () => closeOverwriteConfirm(true));
overwriteConfirmCancelBtn.addEventListener("click", () => closeOverwriteConfirm(false));
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !overwriteConfirmScrim.hidden) closeOverwriteConfirm(false);
});
rememberCheck.addEventListener("change", persistSaveBehavior);

// Voice activation. Both flags go over on every change rather than one at a
// time, because `set_voice_activation` takes the pair — the engine has to be
// told the whole state to decide whether a microphone should stay open.
async function persistVoiceActivation() {
  await callApi("set_voice_activation", pushToTalkToggle.checked, wakePhraseToggle.checked);
}

[pushToTalkToggle, wakePhraseToggle].forEach((toggle) => {
  toggle.addEventListener("change", persistVoiceActivation);
});

// Unlike the save-behavior rows above, these are a plain either/or with no
// "ask me" third state — the row's own data-mode is the stored value.
// Shared by a reopen row's click and the voice REOPEN_CONTINUE/REOPEN_START
// commands (Milestone 8.5).
async function chooseReopenMode(mode) {
  const row = mode === "start" ? startRow : continueRow;
  reopenRows.forEach((r) => r.classList.toggle("selected", r === row));
  await callApi("set_reopen_behavior", mode);
}

reopenRows.forEach((row) => {
  row.addEventListener("click", () => chooseReopenMode(row.dataset.mode));
});

(async function init() {
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lector-theme", theme);
  await renderThemeCards();

  const behavior = await callApi("get_save_behavior");
  selectSaveRow(behavior === "overwrite" ? overwriteRow : copyRow);
  rememberCheck.checked = behavior !== "ask";

  const activation = await callApi("get_voice_activation");
  pushToTalkToggle.checked = activation.push_to_talk;
  wakePhraseToggle.checked = activation.wake_phrase;
  // Asked for rather than typed here, so the phrase has exactly one home
  // (src/lector/features/voice/wake.py) and this card cannot advertise a
  // phrase the recognizer is not listening for.
  const reference = await callApi("get_command_reference");
  wakePhraseLabel.textContent = `“${reference.wake_phrase_display}”`;

  const reopen = await callApi("get_reopen_behavior");
  const activeReopenRow = reopen === "start" ? startRow : continueRow;
  reopenRows.forEach((r) => r.classList.toggle("selected", r === activeReopenRow));

  // Narrow the recognizer to Settings' context (Milestone 8.4), which carries
  // its own scoped commands — see VOICE_ACTIONS below — on top of the global
  // ones. If that fails the box must say so, not claim voice is ready.
  try {
    await callApi("set_voice_context", "settings");
  } catch (err) {
    console.error("set_voice_context failed:", err);
    voiceBox.showUnavailable(`Voice commands couldn't start on this screen. ${err}`);
    return;
  }
  // Milestone 8.12 adds the same status box Home has (it was absent here
  // before, so a reader had no way to tell whether voice was listening).
  voiceBox.attach(initVoice(voiceBox.render));
})();

// --- Voice commands (Milestone 8.5) --------------------------------------- //
// Each intent calls the exact function its equivalent click handler already
// calls, mirroring reading.js's and home.js's VOICE_ACTIONS. Unlike home.js,
// GO_HOME is wired here: Settings already has a natural action for it
// (return to the Home screen), where Home itself does not.
const VOICE_ACTIONS = {
  // Global commands (Milestone 8.12), reaching the same functions their
  // buttons do. "Go to Recent" is the Recent sidebar item; "open settings" is
  // already satisfied here, and says so rather than appearing to do nothing.
  OPEN_PDF: () => openPdfFromSettings(),
  GO_RECENT: () => goHome(),
  OPEN_SETTINGS: () => showToast("You're already in Settings."),
  HELP: () => openCommandReference(),
  GO_HOME: () => goHome(),
  THEME_LIGHT: () => selectTheme("light"),
  THEME_DARK: () => selectTheme("dark"),
  THEME_SEPIA: () => selectTheme("sepia"),
  SAVE_COPY: () => chooseSaveMode("copy"),
  SAVE_OVERWRITE: () => openOverwriteConfirm(),
  REOPEN_CONTINUE: () => chooseReopenMode("continue"),
  REOPEN_START: () => chooseReopenMode("start"),
  CLOSE_APP: () => closeApp(),
};

window.addEventListener("lector:command", (ev) => {
  const { command } = ev.detail || {};
  if (!command) return;
  // The "What can I say?" panel is a modal scope with its own listener
  // (command-reference.js), so nothing here should act behind it.
  if (!referenceDialog.hidden) return;
  // CONFIRM_OVERWRITE/CANCEL only ever arrive while the confirmation is the
  // active voice context (router.py scopes them there).
  if (!overwriteConfirmScrim.hidden) {
    if (command.intent === "CONFIRM_OVERWRITE") closeOverwriteConfirm(true);
    else if (command.intent === "CANCEL") closeOverwriteConfirm(false);
    return;
  }
  const action = VOICE_ACTIONS[command.intent];
  if (action) action();
});
