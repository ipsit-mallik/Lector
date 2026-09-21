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

settingsNav.addEventListener("click", () => {
  window.location.href = "pages/settings.html";
});

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

async function loadRecent() {
  const entries = await callApi("get_recent_files");
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

// --- Push-to-talk indicator (Milestone 5) -------------------------------- //
// The sidebar's voice box was static copy until now; it reports the real
// engine state here. Home has nothing to *do* with a recognized phrase yet —
// it echoes it so push-to-talk can be verified without opening a PDF first.

const voiceBox = document.getElementById("voiceBox");
const voiceState = document.getElementById("voiceState");
const voiceDetail = document.getElementById("voiceDetail");

const VOICE_IDLE_DETAIL =
  "Hold Space and say what you want. Offline — nothing leaves this machine.";
const HEARD_LINGER_MS = 2500;
let heardTimer = null;

function renderVoiceBox({ state, text, error }) {
  clearTimeout(heardTimer);
  voiceBox.classList.toggle("listening", state === "listening");
  voiceBox.classList.toggle("unavailable", state === "unavailable");

  switch (state) {
    case "unavailable":
      voiceState.textContent = "VOICE UNAVAILABLE";
      // Naming the fix in place of the generic copy: this is the one screen
      // where the reader can act on it before opening anything.
      voiceDetail.textContent = error || "No speech model installed.";
      break;
    case "listening":
      voiceState.textContent = "LISTENING";
      voiceDetail.textContent = text ? `"${text}"` : "Go ahead — release Space when you're done.";
      break;
    case "heard":
      voiceState.textContent = "HEARD";
      voiceDetail.textContent = text ? `"${text}"` : "Didn't catch that.";
      heardTimer = setTimeout(() => renderVoiceBox({ state: "idle" }), HEARD_LINGER_MS);
      break;
    default:
      voiceState.textContent = "VOICE READY";
      voiceDetail.textContent = VOICE_IDLE_DETAIL;
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
  await loadRecent();
  initPushToTalk(renderVoiceBox);
})();
