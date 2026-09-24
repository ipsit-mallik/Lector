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
    canvas: cs.getPropertyValue("--color-canvas").trim(),
    surface: cs.getPropertyValue("--color-surface").trim(),
    border: cs.getPropertyValue("--color-border-light").trim(),
    text: cs.getPropertyValue("--color-text-primary").trim(),
    muted: cs.getPropertyValue("--color-border-muted").trim(),
    highlight: cs.getPropertyValue("--color-highlight").trim(),
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

  // Narrow the recognizer to Settings' context (Milestone 8.4), which now
  // (Milestone 8.5) carries its own scoped commands — see VOICE_ACTIONS
  // below — on top of the always-on global ones (undo/redo/help/go home).
  await callApi("set_voice_context", "settings");
  // No mic-status element exists in this screen's design (docs/mockups/06
  // Settings.png), unlike Home's voiceBox, so there is nothing for onState
  // to render here — the mockup gives push-to-talk/wake feedback nowhere on
  // this page, and 8.5 doesn't add one.
  initVoice(() => {});
})();

// --- Voice commands (Milestone 8.5) --------------------------------------- //
// Each intent calls the exact function its equivalent click handler already
// calls, mirroring reading.js's and home.js's VOICE_ACTIONS. Unlike home.js,
// GO_HOME is wired here: Settings already has a natural action for it
// (return to the Home screen), where Home itself does not.
const VOICE_ACTIONS = {
  GO_HOME: () => goHome(),
  THEME_LIGHT: () => selectTheme("light"),
  THEME_DARK: () => selectTheme("dark"),
  THEME_SEPIA: () => selectTheme("sepia"),
  SAVE_COPY: () => chooseSaveMode("copy"),
  SAVE_OVERWRITE: () => chooseSaveMode("overwrite"),
  REOPEN_CONTINUE: () => chooseReopenMode("continue"),
  REOPEN_START: () => chooseReopenMode("start"),
};

window.addEventListener("lector:command", (ev) => {
  const { command } = ev.detail || {};
  if (!command) return;
  const action = VOICE_ACTIONS[command.intent];
  if (action) action();
});
