// The danger question (docs/DESIGN_SYSTEM.md § "Danger dialog"): a question
// whose answer loses something — "Discard page changes?", "Delete 3 pages?".
// A `--danger` badge, the title and description, an optional body, an info
// line, then the safe answer (secondary, focused, so Enter by reflex loses
// nothing; Esc means it too) and the danger button (`.btn--danger`).
// `open()` resolves true for the danger answer, false otherwise.
//
// A modal voice scope of the file tools' grammar: "confirm" is the danger
// button, "cancel" the safe one.
"use strict";

function createDangerDialog({ id, icon, safeLabel, voiceHint }) {
  const scrim = document.createElement("div");
  scrim.className = "dialog-scrim dialog-scrim--enter voice-dialog";
  scrim.id = id;
  scrim.hidden = true;

  const card = document.createElement("div");
  card.className = "dialog-card";
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", `${id}Title`);
  card.setAttribute("aria-describedby", `${id}Desc`);

  const body = document.createElement("div");
  body.className = "dialog-body";
  const header = document.createElement("div");
  header.className = "dialog-header";
  const badge = document.createElement("div");
  badge.className = "dialog-badge dialog-badge--danger";
  badge.dataset.icon = icon;
  badge.setAttribute("aria-hidden", "true");
  const text = document.createElement("div");
  const title = document.createElement("p");
  title.className = "dialog-title";
  title.id = `${id}Title`;
  const desc = document.createElement("p");
  desc.className = "dialog-desc";
  desc.id = `${id}Desc`;
  text.append(title, desc);
  header.append(badge, text);
  const extra = document.createElement("div");
  const info = document.createElement("p");
  info.className = "dialog-info";
  const infoIcon = document.createElement("span");
  infoIcon.dataset.icon = "info";
  infoIcon.setAttribute("aria-hidden", "true");
  const infoText = document.createElement("span");
  info.append(infoIcon, infoText);
  body.append(header, extra, info);

  const footer = document.createElement("div");
  footer.className = "dialog-footer";
  const hint = document.createElement("span");
  hint.className = "hint";
  hint.textContent = voiceHint;
  const safeBtn = document.createElement("button");
  safeBtn.type = "button";
  safeBtn.className = "btn btn-secondary";
  safeBtn.textContent = safeLabel;
  const dangerBtn = document.createElement("button");
  dangerBtn.type = "button";
  dangerBtn.className = "btn btn--danger";
  footer.append(hint, safeBtn, dangerBtn);

  card.append(body, footer);
  scrim.appendChild(card);
  document.body.appendChild(scrim);
  mountIcons(scrim);

  const scope = createVoiceScope("file_tool");
  let resolvePromise = null;
  let releaseTrap = null;

  function close(confirmed) {
    if (scrim.hidden) return;
    scrim.hidden = true;
    scope.leave();
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    const resolve = resolvePromise;
    resolvePromise = null;
    if (resolve) resolve(confirmed);
  }

  safeBtn.addEventListener("click", () => close(false));
  dangerBtn.addEventListener("click", () => close(true));
  // Esc means the safe answer, and goes no further (not to the view behind).
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || scrim.hidden) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    close(false);
  }, true);

  // Every string is set as text. `content` is an optional node for the body
  // (the delete confirmation's thumbnails).
  function open({ titleText, descText, content = null, infoText: infoLine, dangerLabel }) {
    title.textContent = titleText;
    desc.textContent = descText;
    extra.replaceChildren(...(content ? [content] : []));
    infoText.textContent = infoLine;
    dangerBtn.textContent = dangerLabel;
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    safeBtn.focus();
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
}
