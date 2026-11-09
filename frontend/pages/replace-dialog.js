// "Replace this file?" — asked when "Save a copy" is given a name that is
// already taken (hardening H2). A copy used to replace whatever was there
// without a word. Resolves true for Replace, false for Cancel/Escape/"cancel".
//
// Built like Settings' overwrite confirmation (settings.js): focus goes to the
// card rather than a button, because push-to-talk steps aside for a focused
// button and would swallow the spoken "confirm replace"; with nothing focused
// Enter does nothing, the safe default. It is a modal voice scope of its own,
// so only "confirm replace" / "replace it" / "cancel" are heard while it is up.

// The reader's Save a copy and the file tools' Change… (reader and Home) ask
// this, so the markup lives here once, like the password dialog's. Static:
// the name and folder are set later as text.
const REPLACE_CONFIRM_MARKUP = `
  <div class="dialog-scrim dialog-scrim--enter voice-dialog" id="replaceConfirmScrim" hidden>
    <div class="dialog-card" role="dialog" aria-modal="true" aria-labelledby="replaceConfirmTitle" tabindex="-1">
      <div class="dialog-body">
        <div class="dialog-header">
          <div class="dialog-badge" data-icon="folder"></div>
          <div>
            <p class="dialog-title" id="replaceConfirmTitle">Replace this file?</p>
            <p class="dialog-desc" id="replaceConfirmDesc"></p>
          </div>
        </div>
      </div>
      <div class="dialog-footer">
        <span class="hint">Say “confirm replace”, or “cancel” to pick another name.</span>
        <button class="btn btn-secondary" id="replaceConfirmCancelBtn">Cancel</button>
        <button class="btn" id="replaceConfirmBtn">Replace</button>
      </div>
    </div>
  </div>`;
if (!document.getElementById("replaceConfirmScrim")) {
  document.body.insertAdjacentHTML("beforeend", REPLACE_CONFIRM_MARKUP);
  if (typeof mountIcons === "function") mountIcons(document.getElementById("replaceConfirmScrim"));
}

const ReplaceDialog = (() => {
  const scrim = document.getElementById("replaceConfirmScrim");
  const card = scrim.querySelector(".dialog-card");
  const title = document.getElementById("replaceConfirmTitle");
  const desc = document.getElementById("replaceConfirmDesc");
  const replaceBtn = document.getElementById("replaceConfirmBtn");
  const cancelBtn = document.getElementById("replaceConfirmCancelBtn");
  const scope = createVoiceScope("replace_confirm");

  let resolvePromise = null;
  let opener = null;

  function close(confirmed) {
    if (scrim.hidden) return;
    scrim.hidden = true;
    scope.leave();
    if (opener && opener.focus) opener.focus();
    opener = null;
    const resolve = resolvePromise;
    resolvePromise = null;
    if (resolve) resolve(confirmed);
  }

  replaceBtn.addEventListener("click", () => close(true));
  cancelBtn.addEventListener("click", () => close(false));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !scrim.hidden) close(false);
  });

  // `name` and `folder` come from the reader's own file system, so they are
  // set as text, never as markup.
  function open({ name, folder }) {
    title.textContent = `Replace “${name}”?`;
    desc.textContent =
      `A file with this name already exists in ${folder}. Replacing it overwrites that file.`;
    opener = document.activeElement;
    scrim.hidden = false;
    card.focus();
    // The buttons still work if this fails; only the spoken answer is lost.
    scope.enter();
    return new Promise((resolve) => {
      resolvePromise = resolve;
    });
  }

  function isOpen() {
    return !scrim.hidden;
  }

  // CONFIRM_REPLACE/CANCEL only ever arrive while this is the active voice
  // context (router.py scopes them to REPLACE_CONFIRM).
  function handleCommand(command) {
    if (command.intent === "CONFIRM_REPLACE") close(true);
    else if (command.intent === "CANCEL") close(false);
  }

  return { open, isOpen, handleCommand };
})();
