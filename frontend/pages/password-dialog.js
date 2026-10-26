// "Enter the password" — asked when a PDF can't be opened without one
// (hardening H5). Home, Reading and Settings all open PDFs, so the markup
// lives here once, like the "What can I say?" list's.
//
// Built like the Replace confirmation (replace-dialog.js): the shared
// .dialog-* frame, a modal voice scope of its own, Escape cancels. Two things
// differ on purpose, because this is a form rather than a yes/no question:
// focus starts in the field, and Enter submits it. A password is typed, never
// spoken (a spoken one would pass through the recognizer), so "cancel" is the
// only thing voice can do here; with the field focused, Space types a space.
//
// The password is only ever in the field and in the one call that tries it.
// The field is emptied whenever the dialog closes.

// Static, with no interpolated or user-supplied data (the file name is set
// later with textContent), so nothing here can be injected into.
const PASSWORD_MARKUP = `
  <div class="dialog-scrim dialog-scrim--enter voice-dialog" id="passwordDialog" hidden>
    <form class="dialog-card" id="passwordCard" role="dialog" aria-modal="true"
          aria-labelledby="passwordTitle" aria-describedby="passwordDesc" novalidate>
      <div class="dialog-body">
        <div class="dialog-header">
          <div class="dialog-badge" data-icon="lock"></div>
          <div>
            <p class="dialog-title" id="passwordTitle">Enter the password</p>
            <p class="dialog-desc" id="passwordDesc"></p>
            <div class="dialog-field">
              <label for="passwordInput">Password</label>
              <input type="password" id="passwordInput" autocomplete="off"
                     spellcheck="false" autocapitalize="off">
              <p class="dialog-field-error" id="passwordError" role="alert" hidden></p>
            </div>
            <label class="checkbox-row">
              <input type="checkbox" id="passwordShow">
              Show password
            </label>
          </div>
        </div>
      </div>
      <div class="dialog-footer">
        <span class="hint">Type the password and press Enter, or say “cancel”.</span>
        <button class="btn btn-secondary" type="button" id="passwordCancelBtn">Cancel</button>
        <button class="btn" type="submit" id="passwordOpenBtn" disabled>Open</button>
      </div>
    </form>
  </div>`;
if (!document.getElementById("passwordDialog")) {
  document.body.insertAdjacentHTML("beforeend", PASSWORD_MARKUP);
}

const PasswordDialog = (() => {
  const scrim = document.getElementById("passwordDialog");
  const card = document.getElementById("passwordCard");
  const desc = document.getElementById("passwordDesc");
  const input = document.getElementById("passwordInput");
  const error = document.getElementById("passwordError");
  const show = document.getElementById("passwordShow");
  const cancelBtn = document.getElementById("passwordCancelBtn");
  const openBtn = document.getElementById("passwordOpenBtn");
  const scope = createVoiceScope("password_prompt");

  let tryPassword = null;
  let resolvePromise = null;
  let opener = null;
  let busy = false;
  let iconMounted = false;

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", "passwordError");
  }

  function clearError() {
    error.textContent = "";
    error.hidden = true;
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
  }

  // While a password is being tried the dialog can't be cancelled: the try
  // opens the PDF if the password is right, and a cancel then would leave the
  // screen on the old PDF while Lector holds the new one.
  function setBusy(value) {
    busy = value;
    input.readOnly = value;
    cancelBtn.disabled = value;
    openBtn.textContent = value ? "Opening…" : "Open";
    openBtn.disabled = value || !input.value;
  }

  function cancel() {
    if (!busy) close(null);
  }

  function close(result) {
    if (scrim.hidden) return;
    scrim.hidden = true;
    input.value = "";
    input.type = "password";
    show.checked = false;
    clearError();
    setBusy(false);
    tryPassword = null;
    scope.leave();
    if (opener && opener.focus) opener.focus();
    opener = null;
    const resolve = resolvePromise;
    resolvePromise = null;
    if (resolve) resolve(result);
  }

  async function submit() {
    if (busy || !input.value || !tryPassword) return;
    const attempt = tryPassword;
    setBusy(true);
    let outcome;
    try {
      outcome = await attempt(input.value);
    } catch (err) {
      // open-pdf.js's attempt never rejects; one that did would otherwise
      // leave the dialog busy for good. Closed as if cancelled.
      console.error("The unlock attempt failed:", err);
      outcome = null;
    }
    // Asked again for another PDF meanwhile (only `ask` can close it while
    // busy): this answer belongs to a dialog that is gone.
    if (scrim.hidden || attempt !== tryPassword) return;
    setBusy(false);
    if (outcome && outcome.error) {
      showError(outcome.error);
      input.focus();
      input.select();
      return;
    }
    close(outcome ? outcome.done : null);
  }

  card.addEventListener("submit", (ev) => {
    ev.preventDefault();
    submit();
  });
  input.addEventListener("input", () => {
    clearError();
    openBtn.disabled = busy || !input.value;
  });
  show.addEventListener("change", () => {
    input.type = show.checked ? "text" : "password";
  });
  cancelBtn.addEventListener("click", cancel);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !scrim.hidden) cancel();
  });

  // Asks for the password of the PDF called `name` (set as text: it comes
  // from the reader's file system). `attempt(password)` resolves to
  // `{ error }` to keep asking with that line under the field, or to
  // `{ done }` to close; `ask` then resolves to `done`, or to null when the
  // reader cancels.
  function ask({ name, attempt }) {
    if (!scrim.hidden) close(null);
    desc.textContent = `“${name}” is protected by a password.`;
    tryPassword = attempt;
    opener = document.activeElement;
    scrim.hidden = false;
    input.focus();
    if (!iconMounted) {
      iconMounted = true;
      mountIcons(scrim);
    }
    // The buttons and keys still work if this fails; only "cancel" is lost.
    scope.enter();
    return new Promise((resolve) => {
      resolvePromise = resolve;
    });
  }

  function isOpen() {
    return !scrim.hidden;
  }

  // CANCEL only ever arrives while this is the active voice context
  // (router.py scopes it to PASSWORD_PROMPT).
  function handleCommand(command) {
    if (command.intent === "CANCEL") cancel();
  }

  return { ask, isOpen, handleCommand };
})();
