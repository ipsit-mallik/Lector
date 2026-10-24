// "Replace this file?" — asked when "Save a copy" is given a name that is
// already taken (hardening H2). A copy used to replace whatever was there
// without a word. Resolves true for Replace, false for Cancel/Escape/"cancel".
//
// Built like Settings' overwrite confirmation (settings.js): focus goes to the
// card rather than a button, because push-to-talk steps aside for a focused
// button and would swallow the spoken "confirm replace"; with nothing focused
// Enter does nothing, the safe default. It is a modal voice scope of its own,
// so only "confirm replace" / "replace it" / "cancel" are heard while it is up.

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
