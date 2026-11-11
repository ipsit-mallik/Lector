// Extract… in Organise pages (docs/DESIGN_SYSTEM.md § "Organise pages" →
// "Extract"): the selected pages of the draft, as the grid shows them, saved
// as a new PDF by a file job (api.py's `start_file_job("extract")`), while the
// draft stays as it is. The tool dialogs' card: the shared destination block
// (js/save-destination.js), "Also delete them from this PDF", Cancel and
// Extract; then progress, with Cancel, until the file is written.
//
// It stays in front while the job runs (no Keep reading): "Also delete" is
// a step of the draft once the file is there, so the draft mustn't change
// meanwhile. `open()` resolves `{ result, alsoDelete }` once the file is
// written (organise.js deletes and says so), or null.
//
// Keys: Esc is Cancel while setting up and does nothing while working (the
// Cancel button stops the job); Enter presses Extract unless a text field
// has focus. Voice (the file tools' grammar): "confirm" is Extract,
// "cancel" cancels or stops the job.
"use strict";

const OrganiseExtractDialog = (() => {
  let saveAs = null;

  let scrim = null;
  let card = null;
  let titleEl = null;
  let descEl = null;
  let bodyEl = null;
  let footerEl = null;
  let primaryBtn = null;
  let scope = null;
  let releaseTrap = null;
  // While open: { positions, setup, destination, alsoDelete, phase
  // ("setup" | "working"), starting, jobId, event, cancelling, error,
  // settle }.
  let s = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "organiseExtract";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "organiseExtractTitle");
    card.setAttribute("aria-describedby", "organiseExtractDesc");
    card.tabIndex = -1;
    const body = el("div", "dialog-body");
    const header = el("div", "dialog-header");
    const badge = el("div", "dialog-badge");
    badge.dataset.icon = "extract";
    badge.setAttribute("aria-hidden", "true");
    const text = el("div");
    titleEl = el("p", "dialog-title");
    titleEl.id = "organiseExtractTitle";
    descEl = el("p", "dialog-desc");
    descEl.id = "organiseExtractDesc";
    text.append(titleEl, descEl);
    header.append(badge, text);
    bodyEl = el("div", "tool-body");
    body.append(header, bodyEl);
    footerEl = el("div", "dialog-footer");
    card.append(body, footerEl);
    scrim.appendChild(card);
    document.body.appendChild(scrim);
    mountIcons(scrim);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown, true);
    FileJobs.subscribe(onJobEvent);
  }

  // ---- Showing and hiding ------------------------------------------------

  function show() {
    render();
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    // The buttons still work if this fails; only the spoken answers are lost.
    scope.enter();
    focusFirst();
  }

  function hide() {
    if (!scrim || scrim.hidden) return Promise.resolve();
    if (s && s.destination) s.destination.closeMenu();
    scrim.hidden = true;
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    return scope.leave();
  }

  // Another dialog in front (the folder picker, "Replace this file?"), this
  // one out of the way meanwhile and back after.
  async function aside(task) {
    await hide();
    try {
      return await task();
    } finally {
      if (s && scrim.hidden) show();
    }
  }

  function finish(answer) {
    if (!s) return;
    const settle = s.settle;
    hide();
    s = null;
    settle(answer);
  }

  // Extract when it can run; while the folder is still being checked it is
  // disabled for a moment, so the card holds focus and Extract takes it once
  // ready (updatePrimary), unless the reader has moved it meanwhile.
  function focusFirst() {
    if (primaryBtn && !primaryBtn.disabled) primaryBtn.focus();
    else if (s && s.phase === "working" && footerEl.children[0]) footerEl.children[0].focus();
    else card.focus();
  }

  // ---- Rendering -----------------------------------------------------------

  function footerButton(label, onClick, { primary = false, disabled = false } = {}) {
    const button = el("button", primary ? "btn" : "btn btn-secondary", label);
    button.type = "button";
    button.disabled = disabled;
    button.addEventListener("click", onClick);
    footerEl.appendChild(button);
    if (primary) primaryBtn = button;
    return button;
  }

  function render() {
    const { count, label } = s.setup;
    titleEl.textContent = `Extract ${plural(count, "page", "pages")}`;
    descEl.textContent = `Saves ${label} as a new PDF.`;
    bodyEl.replaceChildren();
    footerEl.replaceChildren();
    primaryBtn = null;
    if (s.phase === "working") renderWorking();
    else renderSetup();
    mountIcons(card);
  }

  function renderSetup() {
    bodyEl.appendChild(s.destination.element);
    // Deleting every page is refused, so with every page selected there is
    // nothing to offer.
    if (!s.setup.every_page) {
      const row = el("label", "checkbox-row");
      const box = el("input");
      box.type = "checkbox";
      box.id = "organiseExtractAlsoDelete";
      box.checked = s.alsoDelete;
      box.addEventListener("change", () => { s.alsoDelete = box.checked; });
      row.append(box, document.createTextNode(`Also delete ${s.setup.count === 1 ? "it" : "them"} from this PDF`));
      bodyEl.appendChild(row);
    }
    if (s.error) {
      const error = el("p", "dialog-field-error organise-extract-error", s.error);
      error.setAttribute("role", "alert");
      bodyEl.appendChild(error);
    }
    footerButton("Cancel", () => finish(null));
    footerButton("Extract", start, { primary: true, disabled: !ready() });
  }

  function renderWorking() {
    const wrap = el("div", "job-progress is-indeterminate");
    const row = el("div", "job-progress-row");
    row.append(el("span", "job-progress-phase"), el("span", "job-progress-pct"));
    const bar = el("div", "job-progress-bar");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Progress");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.appendChild(el("div", "job-progress-fill"));
    wrap.append(row, bar);
    bodyEl.append(wrap, el("p", "job-progress-note", "Cancelling writes nothing. Your page changes stay as they are."));
    footerButton(s.cancelling ? "Cancelling…" : "Cancel", cancelJob, { disabled: s.cancelling });
    paintProgress();
  }

  function paintProgress() {
    const wrap = bodyEl.querySelector(".job-progress");
    if (!wrap) return;
    const fraction = fileJobFraction(s.event);
    const bar = wrap.querySelector(".job-progress-bar");
    const fill = wrap.querySelector(".job-progress-fill");
    wrap.classList.toggle("is-indeterminate", fraction === null);
    if (fraction === null) {
      fill.style.removeProperty("--progress");
      bar.removeAttribute("aria-valuenow");
    } else {
      fill.style.setProperty("--progress", String(fraction));
      bar.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
    }
    wrap.querySelector(".job-progress-phase").textContent = fileJobPhaseText(s.event);
    wrap.querySelector(".job-progress-pct").textContent = fraction === null ? "" : `${Math.round(fraction * 100)}%`;
  }

  // ---- Running -------------------------------------------------------------

  function ready() {
    return Boolean(s) && s.phase === "setup" && !s.starting && s.destination.isValid();
  }

  function updatePrimary() {
    if (!s || s.phase !== "setup" || !primaryBtn) return;
    primaryBtn.disabled = !ready();
    if (!primaryBtn.disabled && document.activeElement === card) primaryBtn.focus();
  }

  // Before the job: the folder and name are usable and not the original, and
  // a file already there is only replaced after asking.
  async function confirmDestination() {
    const { folder, name } = s.destination.value();
    const target = await callApi("check_tool_destination", "extract", "open", folder, name);
    if (!s || s.phase !== "setup") return false;
    if (!target.ok) {
      s.destination.showError(target.field, target.error);
      return false;
    }
    if (!target.exists) return true;
    const replace = await aside(() => ReplaceDialog.open({ name: target.name, folder: target.folder }));
    return Boolean(replace) && Boolean(s) && s.phase === "setup";
  }

  async function start() {
    if (!ready()) return;
    s.starting = true;
    s.error = null;
    updatePrimary();
    let answer = null;
    try {
      if (await confirmDestination()) {
        answer = await callApi("start_file_job", "extract", "open", { ...s.destination.value(), positions: s.positions });
      }
    } catch (err) {
      console.error("organise-extract: starting the job", err);
    } finally {
      if (s) s.starting = false;
    }
    if (!s) return;
    if (!answer || !answer.ok) {
      if (answer) s.error = answer.busy ? "Another file job is running. Try again when it has finished." : answer.error;
      render();
      focusFirst();
      return;
    }
    s.jobId = answer.id;
    const current = FileJobs.current();
    s.event = current && current.id === answer.id ? current : null;
    s.phase = "working";
    s.cancelling = false;
    FileJobs.watch(answer.id);
    render();
    focusFirst();
  }

  async function cancelJob() {
    if (!s || s.phase !== "working" || s.cancelling) return;
    s.cancelling = true;
    render();
    focusFirst();
    await callApi("cancel_file_job");
  }

  function onJobEvent(event) {
    if (!s || event.id !== s.jobId) return;
    if (event.state === "working") {
      s.event = event;
      paintProgress();
      return;
    }
    s.jobId = null;
    s.phase = "setup";
    s.cancelling = false;
    s.event = null;
    if (event.state === "done") {
      finish({ result: event.result || {}, alsoDelete: s.alsoDelete });
      return;
    }
    // Failed (the sentence says the original is unchanged) or cancelled:
    // back to setting up, as it was.
    s.error = event.state === "failed" ? event.error : null;
    render();
    focusFirst();
  }

  // ---- Keys and voice ------------------------------------------------------

  function onKeydown(ev) {
    if (!s || !scrim || scrim.hidden) return;
    if (s.destination.isMenuOpen()) return; // the "Save in" menu takes its keys
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      if (s.phase === "setup") finish(null);
      return;
    }
    if (ev.key === "Enter" && !ev.shiftKey && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
      const active = document.activeElement;
      if (isTypingTarget(active) || (active && footerEl.contains(active)) || (active && active.type === "checkbox")) return;
      if (primaryBtn && !primaryBtn.disabled) {
        ev.preventDefault();
        primaryBtn.click();
      }
    }
  }

  function handleCommand(command) {
    if (!s) return;
    if (command.intent === "TOOL_CONFIRM") {
      if (primaryBtn && !primaryBtn.disabled) primaryBtn.click();
    } else if (command.intent === "CANCEL") {
      if (s.phase === "working") cancelJob();
      else if (s.destination.isMenuOpen()) s.destination.closeMenu({ focusButton: true });
      else finish(null);
    }
  }

  // ---- Opening ---------------------------------------------------------------

  // "Choose another folder…": the in-app folder picker, then back here; voice
  // goes back to Organise's context when it closes.
  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await aside(() =>
      saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder", restingContext: "organise" })
    );
    return picked && !picked.cancelled ? picked.path : null;
  }

  // The pages at `positions` (the draft's, now). Null straight away when
  // Python can't describe them.
  async function open({ positions }) {
    build();
    if (s) return null;
    const setup = await callApi("organise_extract_setup", positions);
    if (!setup.ok) return null;
    return new Promise((settle) => {
      s = {
        positions: [...positions],
        setup,
        alsoDelete: false,
        phase: "setup",
        starting: false,
        jobId: null,
        event: null,
        cancelling: false,
        error: null,
        settle,
      };
      s.destination = createSaveDestination({
        kind: "file",
        extension: setup.output.extension,
        stem: setup.output.stem,
        originFolder: setup.output.folder,
        originLabel: "Beside the original",
        suggestName: async (folder) => (await callApi("suggest_extract_name", positions, folder)).stem,
        pickFolder: chooseFolder,
        onChange: updatePrimary,
      });
      show();
    });
  }

  return {
    open,
    isOpen: () => Boolean(scrim) && !scrim.hidden,
    handleCommand,
    // The reader's Save-As dialog, whose folder-picking mode "Choose another
    // folder…" uses.
    configure(options) {
      saveAs = options.saveAs || saveAs;
    },
  };
})();
