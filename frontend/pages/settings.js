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
const continueRow = document.getElementById("continueRow");
const startRow = document.getElementById("startRow");
const reopenRows = [continueRow, startRow];

recentNav.addEventListener("click", () => {
  window.location.href = "../index.html";
});

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
  card.addEventListener("click", async () => {
    if (name === current) return;
    document.documentElement.dataset.theme = name;
    localStorage.setItem("lector-theme", name);
    await callApi("set_theme", name);
    await renderThemeCards();
  });
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

saveRows.forEach((row) => {
  row.addEventListener("click", () => {
    selectSaveRow(row);
    persistSaveBehavior();
  });
});
rememberCheck.addEventListener("change", persistSaveBehavior);

// Unlike the save-behavior rows above, these are a plain either/or with no
// "ask me" third state — the row's own data-mode is the stored value.
reopenRows.forEach((row) => {
  row.addEventListener("click", async () => {
    reopenRows.forEach((r) => r.classList.toggle("selected", r === row));
    await callApi("set_reopen_behavior", row.dataset.mode);
  });
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

  const reopen = await callApi("get_reopen_behavior");
  const activeReopenRow = reopen === "start" ? startRow : continueRow;
  reopenRows.forEach((r) => r.classList.toggle("selected", r === activeReopenRow));
})();
