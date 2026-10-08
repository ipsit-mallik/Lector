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
  const previous = currentTheme();
  if (previous === name) return;
  applyTheme(name);
  localStorage.setItem("lector-theme", name);
  try {
    await callApi("set_theme", name);
  } catch (err) {
    // Not saved, so show what is saved rather than a theme the next launch
    // won't have.
    console.error("set_theme failed:", err);
    applyTheme(previous);
    localStorage.setItem("lector-theme", previous);
    showToast("Couldn't change the theme. Try again.");
  }
}

// The cards are marked in place rather than rebuilt, so the check badge
// animates in and the page doesn't re-render under the reader's pointer. Done
// on `themechange` (ui.js), which fires inside the theme's own crossfade, so the
// ring and badge fade with the rest of the window instead of before or after.
function markCurrentThemeCard(name) {
  themeCardsEl.querySelectorAll(".theme-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.themeName === name);
  });
}

document.addEventListener("themechange", (e) => markCurrentThemeCard(e.detail.name));

// The cards are in settings.html, already marked from the <head>'s theme mirror,
// so they are on the first frame; init() re-marks them from the saved setting.
themeCardsEl.querySelectorAll(".theme-card").forEach((card) => {
  card.addEventListener("click", () => selectTheme(card.dataset.themeName));
});

// Radio semantics, a single Tab stop and arrow keys for each card group; the
// clicks themselves are wired below, as before.
initRadioCards(themeCardsEl);
// Arrows only move focus here: choosing "Overwrite the original" is confirmed
// when spoken, so one arrow press (which wraps) must not apply it.
initRadioCards(document.getElementById("saveOptions"), { selectOnArrow: false });
initRadioCards(document.getElementById("reopenOptions"));

// Lifts the guard that keeps the saved settings from animating in as they are
// restored (see theme.css .is-restoring). Two frames, so the restored state has
// been styled once with transitions off before they come back on.
function endRestoring() {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.body.classList.remove("is-restoring");
  }));
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
  markCurrentThemeCard(theme);

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
// Also lifted if a bridge call above failed, so a failed restore can't leave
// every transition on the page switched off.
})().finally(endRestoring);

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
