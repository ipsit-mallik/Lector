// First-run onboarding (docs/TASKS.md, Milestone 8), replacing the Milestone
// 4 stub whose two buttons did the same thing.
//
// Two steps, and the shape of the flow is the argument docs/PRD.md makes:
// voice is an addition, never a prerequisite. Every exit from this screen —
// allowing the microphone, being refused it, skipping it — lands in the same
// fully working app. The only thing the reader's answers change is which
// activation modes start out enabled.
//
// The microphone step deliberately asks Python to *use* the microphone
// briefly (`request_microphone`), because neither Windows nor macOS raises
// its permission prompt any other way. A refusal comes back as data rather
// than an exception, and is shown as a plain sentence, not an error.

const micStep = document.getElementById("micStep");
const modeStep = document.getElementById("modeStep");
const micResult = document.getElementById("micResult");
const allowMicBtn = document.getElementById("allowMicBtn");
const skipMicBtn = document.getElementById("skipMicBtn");
const pushToTalkCard = document.getElementById("pushToTalkCard");
const wakePhraseCard = document.getElementById("wakePhraseCard");
const wakePhraseName = document.getElementById("wakePhraseName");
const modeCards = [pushToTalkCard, wakePhraseCard];
const stepDotTwo = document.getElementById("stepDotTwo");
const stepLabel = document.getElementById("stepLabel");
const stepAside = document.getElementById("stepAside");
const backBtn = document.getElementById("backBtn");
const startBtn = document.getElementById("startBtn");

// Whether the microphone is actually usable. If it is not, the mode step
// still appears — the reader can see what voice would offer — but nothing is
// switched on at the end, because an indicator claiming to listen without a
// microphone is worse than one that says voice is off.
let micGranted = false;

function showStep(index) {
  micStep.hidden = index !== 0;
  modeStep.hidden = index !== 1;
  stepDotTwo.classList.toggle("filled", index === 1);
  stepLabel.textContent = `Step ${index + 1} of 2`;
  // The time estimate belongs to the step with an unknown wait in it (the OS
  // dialog). On the last step the footer carries the actions instead, and two
  // competing things on the right would read as clutter.
  stepAside.hidden = index !== 0;
  backBtn.hidden = index !== 1;
  startBtn.hidden = index !== 1;
}

function selectMode(card) {
  modeCards.forEach((c) => {
    const chosen = c === card;
    c.classList.toggle("selected", chosen);
    c.setAttribute("aria-pressed", String(chosen));
  });
}

async function requestMicrophone() {
  allowMicBtn.disabled = true;
  allowMicBtn.textContent = "Asking…";
  const result = await callApi("request_microphone").catch((err) => ({
    granted: false,
    error: String(err),
  }));

  micGranted = Boolean(result.granted);
  micResult.hidden = false;
  micResult.classList.toggle("denied", !micGranted);
  micResult.textContent = micGranted
    ? "Microphone ready."
    : "No microphone access — voice stays off, and everything else works as usual.";

  allowMicBtn.disabled = false;
  allowMicBtn.textContent = "Allow microphone";
  showStep(1);
}

// Skipping leaves the microphone unasked rather than refused. The reader
// still chooses a mode, so the choice is waiting for them if they turn voice
// on in Settings later.
function skipMicrophone() {
  micGranted = false;
  showStep(1);
}

async function finish() {
  const wake = wakePhraseCard.classList.contains("selected");
  // One mode at the end of onboarding, because the question asked was "how
  // should it start listening" — singular. Both can be on; Settings is where
  // that is offered, and the step above says so.
  await callApi("set_voice_activation", micGranted && !wake, micGranted && wake).catch(() => {});
  // Marked seen on every path: docs/PRD.md makes onboarding skippable, and a
  // screen that reappears after being dismissed is not skippable.
  await callApi("set_onboarding_seen").catch(() => {});
  window.location.href = "../index.html";
}

allowMicBtn.addEventListener("click", requestMicrophone);
skipMicBtn.addEventListener("click", skipMicrophone);
modeCards.forEach((card) => card.addEventListener("click", () => selectMode(card)));
backBtn.addEventListener("click", () => showStep(0));
startBtn.addEventListener("click", finish);

(async function init() {
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lector-theme", theme);

  // Asked for rather than written into the markup, so the phrase keeps one
  // home (src/lector/features/voice/wake.py) and this card cannot offer a
  // phrase the recognizer is not listening for.
  const reference = await callApi("get_command_reference").catch(() => null);
  wakePhraseName.textContent = reference ? `“${reference.wake_phrase_display}”` : "the wake phrase";

  showStep(0);
})();
