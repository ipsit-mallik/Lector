// Stub first-run onboarding (docs/TASKS.md, Milestone 4).
//
// Both buttons do the same thing today — mark onboarding as seen and go to
// Home. They are still two separate buttons because Milestone 8 gives them
// different meanings ("Skip" leaves voice off; "Start reading" proceeds
// through the mic permission request), and keeping the shape now means the
// real flow fills them in rather than redesigning the screen.

function finish() {
  // Marked seen on either path: docs/PRD.md makes onboarding skippable, and a
  // screen that reappears after being dismissed is not skippable.
  callApi("set_onboarding_seen")
    .catch(() => {})
    .finally(() => {
      window.location.href = "../index.html";
    });
}

document.getElementById("skipBtn").addEventListener("click", finish);
document.getElementById("startBtn").addEventListener("click", finish);

(async function init() {
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lector-theme", theme);
})();
