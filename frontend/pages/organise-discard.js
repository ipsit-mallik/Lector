// "Discard page changes?" — asked when Organise pages is left with Cancel
// (or Esc, or the spoken "cancel") while its draft has changes. The danger
// question pattern (docs/DESIGN_SYSTEM.md § "Danger dialog"), shared with the
// delete confirmation: a danger badge, an info line, Keep editing (secondary,
// focused, so Enter by reflex keeps the work) and a red Discard changes.
// Resolves true for Discard, false for Keep editing, Esc and "cancel".
//
// A modal voice scope of the file tools' grammar: "confirm" discards,
// "cancel" keeps editing.
"use strict";

const DISCARD_DIALOG_MARKUP = `
  <div class="dialog-scrim dialog-scrim--enter voice-dialog" id="organiseDiscardScrim" hidden>
    <div class="dialog-card" role="alertdialog" aria-modal="true"
         aria-labelledby="organiseDiscardTitle" aria-describedby="organiseDiscardDesc">
      <div class="dialog-body">
        <div class="dialog-header">
          <div class="dialog-badge dialog-badge--danger" data-icon="undo" aria-hidden="true"></div>
          <div>
            <p class="dialog-title" id="organiseDiscardTitle">Discard page changes?</p>
            <p class="dialog-desc" id="organiseDiscardDesc"></p>
          </div>
        </div>
        <p class="dialog-info">
          <span data-icon="info" aria-hidden="true"></span>
          <span>To keep them, choose Keep editing, then Done.</span>
        </p>
      </div>
      <div class="dialog-footer">
        <span class="hint">Say “confirm” to discard, or “cancel” to keep editing.</span>
        <button class="btn btn-secondary" id="organiseKeepEditingBtn" type="button">Keep editing</button>
        <button class="btn btn--danger" id="organiseDiscardBtn" type="button">Discard changes</button>
      </div>
    </div>
  </div>`;

const OrganiseDiscardDialog = (() => {
  document.body.insertAdjacentHTML("beforeend", DISCARD_DIALOG_MARKUP);
  const scrim = document.getElementById("organiseDiscardScrim");
  const card = scrim.querySelector(".dialog-card");
  const desc = document.getElementById("organiseDiscardDesc");
  const keepBtn = document.getElementById("organiseKeepEditingBtn");
  const discardBtn = document.getElementById("organiseDiscardBtn");
  const scope = createVoiceScope("file_tool");
  mountIcons(scrim);

  let resolvePromise = null;
  let releaseTrap = null;

  function close(discard) {
    if (scrim.hidden) return;
    scrim.hidden = true;
    scope.leave();
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    const resolve = resolvePromise;
    resolvePromise = null;
    if (resolve) resolve(discard);
  }

  keepBtn.addEventListener("click", () => close(false));
  discardBtn.addEventListener("click", () => close(true));
  // Esc means Keep editing, and goes no further: it must not also reach
  // Organise's own Esc.
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || scrim.hidden) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    close(false);
  }, true);

  // `changes` is the number of actions in the draft.
  function open({ changes }) {
    desc.textContent = changes === 1
      ? "Your change to the pages won’t be applied. The document stays as it was."
      : `Your ${changes} changes to the pages won’t be applied. The document stays as it was.`;
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    keepBtn.focus();
    // The buttons still work if this fails; only the spoken answer is lost.
    scope.enter();
    return new Promise((resolve) => {
      resolvePromise = resolve;
    });
  }

  function isOpen() {
    return !scrim.hidden;
  }

  // TOOL_CONFIRM/CANCEL arrive while this is the active voice context.
  function handleCommand(command) {
    if (command.intent === "TOOL_CONFIRM") close(true);
    else if (command.intent === "CANCEL") close(false);
  }

  return { open, isOpen, handleCommand };
})();
