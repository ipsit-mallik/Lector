// Split (docs/PRD.md § "Post-v1: file tools" → Tier 2), from Home: one PDF
// into several new ones, "Every N pages" or by page ranges, written into a
// new folder ("<name> (split)") as "<name> (part 1).pdf" and so on. The
// original is never touched. Set up → Working → Result in one card, like the
// other tools; the Tools page's card asks which PDF first, a Recent row's
// "Split…" opens this straight away.
//
// The two ways are a radiogroup of option cards, each with its own field:
// the radio is the card's own button, the field sits beside it, and focusing
// the field chooses its way. What the choice makes (the preview, the error
// under the field, the button's count) comes from Python's parser, the one
// the job uses (naming.py), so the count shown is the count written.
//
// Built from DOM calls with textContent: names come from the reader's disk.
"use strict";

const SplitDialog = (() => {
  let saveAs = null;
  let onMissing = null; // home.js: (message, path) => offer Remove from Recent

  let scrim = null;
  let card = null;
  let descEl = null;
  let bodyEl = null;
  let footerEl = null;
  let primaryBtn = null;
  let scope = null;
  let releaseTrap = null;
  let opener = null;
  let previewTimer = 0;
  let previewAsk = 0;

  // Set up's controls, kept between preview updates so typing never loses
  // its place: { cards, picks, fields (each by mode), error, preview }.
  let ui = null;

  // { phase, source, info, mode, every, ranges, preview, pending,
  //   destination, starting, jobId, event, outcome, cancelling }
  let s = null;

  // How long typing settles before the preview is asked for again.
  const PREVIEW_DELAY_MS = 150;

  const WAYS = [
    { mode: "every", label: "Every", field: "Pages per file", unit: "pages" },
    { mode: "ranges", label: "Page ranges", field: "Page ranges" },
  ];

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "splitDialog";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "splitDialogTitle");
    card.setAttribute("aria-describedby", "splitDialogDesc");
    card.tabIndex = -1;
    const body = el("div", "dialog-body");
    const header = el("div", "dialog-header");
    const badge = el("div", "dialog-badge");
    badge.appendChild(iconEl("split"));
    const text = el("div");
    const title = el("p", "dialog-title", "Split PDF");
    title.id = "splitDialogTitle";
    descEl = el("p", "dialog-desc");
    descEl.id = "splitDialogDesc";
    text.append(title, descEl);
    header.append(badge, text);
    bodyEl = el("div", "tool-body");
    body.append(header, bodyEl);
    footerEl = el("div", "dialog-footer");
    card.append(body, footerEl);
    scrim.appendChild(card);
    document.body.appendChild(scrim);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
    FileJobs.subscribe(onJobEvent);
  }

  function isOpen() {
    return Boolean(scrim) && !scrim.hidden;
  }

  // ---- Opening and closing ---------------------------------------------------

  // What the dialog needs to know about the PDF; a protected one asks for
  // its password first (held in Python's memory only, for this file's job).
  async function fetchInfo(path) {
    const info = await callApi("file_tool_info", "split", path);
    if (info.ok || (info.reason !== "password" && info.reason !== "wrong_password")) return info;
    const unlocked = await PasswordDialog.ask({
      name: info.name || baseName(path),
      attempt: async (password) => {
        const retry = await callApi("file_tool_info", "split", path, password);
        if (retry.reason === "wrong_password") return { error: retry.error };
        return { done: retry };
      },
    });
    return unlocked || { ok: false, cancelled: true };
  }

  async function open(path) {
    build();
    if (s && s.phase === "working") {
      show();
      return;
    }
    let info;
    try {
      info = await fetchInfo(path);
    } catch (err) {
      console.error("split-dialog: couldn't read the PDF", err);
      info = { ok: false, error: "Couldn't read the PDF. It may be damaged or no longer there." };
    }
    if (!info.ok) {
      if (info.cancelled) return;
      if (info.reason === "missing" && onMissing) onMissing(info.error, path);
      else showToast(info.error);
      return;
    }
    s = {
      phase: "setup",
      source: path,
      info,
      mode: "every",
      every: String(info.split_every),
      ranges: "",
      preview: null,
      pending: true,
      destination: makeDestination(path, info),
      starting: false,
      jobId: null,
      event: null,
      outcome: null,
      cancelling: false,
    };
    show();
    askPreview();
  }

  function makeDestination(path, info) {
    return createSaveDestination({
      kind: "folder",
      extension: info.output.extension,
      stem: info.output.stem,
      originFolder: info.output.folder,
      originLabel: "Beside the original",
      hint: `Files inside are named ${stemOf(info.name)} (part 1).pdf and so on.`,
      suggestName: async (folder) => (await callApi("suggest_tool_name", "split", path, folder)).stem,
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
  }

  function show() {
    opener = document.activeElement;
    render();
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    if (s.jobId) FileJobs.watch(s.jobId);
    scope.enter();
    focusFirst();
  }

  function hide() {
    if (!scrim || scrim.hidden) return;
    if (s && s.destination) s.destination.closeMenu();
    scrim.hidden = true;
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    scope.leave();
  }

  function close() {
    hide();
    clearTimeout(previewTimer);
    s = null;
    ui = null;
    if (opener && opener.isConnected && opener.focus) opener.focus();
    opener = null;
  }

  function focusFirst() {
    const target = (s.phase === "setup" && ui && ui.picks[s.mode]) || primaryBtn || footerEl.querySelector("button");
    if (target) target.focus();
    else card.focus();
  }

  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await aside(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Another dialog in front (the folder picker), this one out of the way
  // meanwhile and back after.
  async function aside(task) {
    const returnTo = opener;
    hide();
    try {
      return await task();
    } finally {
      if (s && !isOpen()) {
        show();
        opener = returnTo;
      }
    }
  }

  // ---- The choice and its preview -------------------------------------------

  function value() {
    return s.mode === "every" ? s.every : s.ranges;
  }

  function choose(mode) {
    if (!s || s.phase !== "setup" || s.mode === mode) return;
    s.mode = mode;
    syncChoice();
    askPreview();
  }

  // Asked of Python for what is chosen now; an answer to an older question
  // than the latest is dropped.
  function askPreview({ delay = 0 } = {}) {
    clearTimeout(previewTimer);
    s.pending = true;
    updatePrimary();
    previewTimer = setTimeout(async () => {
      if (!s || s.phase !== "setup") return;
      const ask = ++previewAsk;
      const mode = s.mode;
      const asked = value();
      let answer;
      try {
        answer = await callApi("split_preview", s.source, mode, asked, s.info.pages);
      } catch (err) {
        console.error("split-dialog: preview", err);
        answer = { error: "Couldn't work out the files. Try again.", count: 0 };
      }
      if (!s || ask !== previewAsk || mode !== s.mode || asked !== value()) return;
      s.preview = answer;
      s.pending = false;
      updatePreview();
    }, delay);
  }

  function setupReady() {
    return Boolean(s) && s.phase === "setup" && !s.starting && !s.pending && Boolean(s.preview) &&
      !s.preview.error && s.preview.count >= 2 && s.destination.isValid();
  }

  function primaryLabel() {
    const p = s.preview;
    return p && !p.error && p.count ? `Split into ${plural(p.count, "file", "files")}` : "Split";
  }

  function updatePrimary() {
    if (!s || s.phase !== "setup" || !primaryBtn) return;
    primaryBtn.disabled = !setupReady();
    primaryBtn.textContent = primaryLabel();
  }

  // ---- Rendering ------------------------------------------------------------

  function render() {
    bodyEl.replaceChildren();
    footerEl.replaceChildren();
    primaryBtn = null;
    ui = null;
    descEl.textContent = `${s.info.name} · ${plural(s.info.pages, "page", "pages")}. Makes new files; the original stays as it is.`;
    if (s.phase === "setup") renderSetup();
    else if (s.phase === "working") renderWorking();
    else renderResult();
    mountIcons(card);
  }

  function footerButton(label, onClick, { primary = false, disabled = false } = {}) {
    const button = el("button", primary ? "btn" : "btn btn-secondary", label);
    button.type = "button";
    button.disabled = disabled;
    button.addEventListener("click", onClick);
    footerEl.appendChild(button);
    if (primary) primaryBtn = button;
    return button;
  }

  // An example that fits this PDF: "1–10, 11–120".
  function rangesExample() {
    const pages = s.info.pages;
    const cut = Math.max(1, Math.min(10, pages - 1));
    return pages > 1 ? `1–${cut}, ${cut + 1}–${pages}` : "1";
  }

  function wayCard(way) {
    const row = el("div", "option-row split-option");
    const pick = el("button", "split-option-pick");
    pick.type = "button";
    pick.setAttribute("role", "radio");
    pick.dataset.mode = way.mode;
    pick.append(el("span", "option-radio"), el("span", "option-title", way.label));
    pick.addEventListener("click", () => choose(way.mode));
    const field = el("input", `split-field split-field--${way.mode}`);
    field.type = "text";
    field.autocomplete = "off";
    field.spellcheck = false;
    field.setAttribute("aria-label", way.field);
    if (way.mode === "every") {
      field.inputMode = "numeric";
      field.value = s.every;
    } else {
      field.placeholder = rangesExample();
      field.value = s.ranges;
    }
    field.addEventListener("focus", () => choose(way.mode));
    field.addEventListener("input", () => {
      if (way.mode === "every") s.every = field.value;
      else s.ranges = field.value;
      choose(way.mode);
      askPreview({ delay: PREVIEW_DELAY_MS });
    });
    // A click on the card's empty space chooses it too.
    row.addEventListener("click", (ev) => {
      if (ev.target === row) choose(way.mode);
    });
    row.append(pick, field);
    if (way.unit) row.appendChild(el("span", "split-unit", way.unit));
    ui.cards[way.mode] = row;
    ui.picks[way.mode] = pick;
    ui.fields[way.mode] = field;
    return row;
  }

  // The arrows move between the two radios and choose, as in any radiogroup.
  function onRadioKeydown(ev) {
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[ev.key];
    const index = WAYS.findIndex((w) => ui && ui.picks[w.mode] === document.activeElement);
    if (!step || index < 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const next = WAYS[(index + step + WAYS.length) % WAYS.length].mode;
    ui.picks[next].focus();
    choose(next);
  }

  // The chosen card and its radio, without rebuilding anything (a field
  // being typed in keeps its caret).
  function syncChoice() {
    if (!ui) return;
    for (const way of WAYS) {
      const on = way.mode === s.mode;
      ui.cards[way.mode].classList.toggle("selected", on);
      ui.picks[way.mode].setAttribute("aria-checked", String(on));
      ui.picks[way.mode].tabIndex = on ? 0 : -1;
    }
    updatePreview();
  }

  function renderSetup() {
    ui = { cards: {}, picks: {}, fields: {}, error: null, preview: null };
    const group = el("div", "tool-options split-options");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", "Split by");
    for (const way of WAYS) group.appendChild(wayCard(way));
    group.addEventListener("keydown", onRadioKeydown);
    ui.error = el("p", "dialog-field-error split-error");
    ui.error.id = "splitFieldError";
    ui.error.setAttribute("role", "alert");
    ui.error.hidden = true;
    ui.preview = el("div", "split-preview");
    ui.preview.setAttribute("aria-live", "polite");
    bodyEl.append(group, ui.error, ui.preview, s.destination.element);
    footerButton("Cancel", close);
    footerButton(primaryLabel(), start, { primary: true, disabled: !setupReady() });
    syncChoice();
  }

  // The preview box, the error under the chosen field, and the button.
  function updatePreview() {
    if (!ui || !s || s.phase !== "setup") return;
    const p = s.preview;
    const error = p && p.error ? p.error : "";
    ui.error.textContent = error;
    ui.error.hidden = !error;
    for (const way of WAYS) {
      const field = ui.fields[way.mode];
      if (error && way.mode === s.mode) {
        field.setAttribute("aria-invalid", "true");
        field.setAttribute("aria-describedby", "splitFieldError");
      } else {
        field.removeAttribute("aria-invalid");
        field.removeAttribute("aria-describedby");
      }
    }
    const box = ui.preview;
    box.replaceChildren();
    const count = p && !p.error ? plural(p.count, "file", "files") : "No files yet";
    box.appendChild(el("span", "split-preview-count", count));
    if (p && !p.error) {
      for (const part of p.named) {
        const row = el("div", "split-preview-row");
        row.append(el("span", "split-preview-name", part.name), el("span", "split-preview-pages", part.pages));
        box.appendChild(row);
      }
      if (p.more) box.appendChild(el("span", "split-preview-more", `and ${p.more} more`));
      if (p.gaps) {
        const note = el("p", "tool-note split-preview-gaps");
        note.append(iconEl("info"), el("span", "", p.gaps));
        box.appendChild(note);
        mountIcons(note);
      }
    }
    updatePrimary();
  }

  function renderWorking() {
    const event = s.event || {};
    const fraction = fileJobFraction(event);
    const wrap = el("div", fraction === null ? "job-progress is-indeterminate" : "job-progress");
    const row = el("div", "job-progress-row");
    row.append(
      el("span", "job-progress-phase", fileJobPhaseText(event)),
      el("span", "job-progress-pct", fraction === null ? "" : `${Math.round(fraction * 100)}%`)
    );
    const bar = el("div", "job-progress-bar");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Progress");
    const fill = el("div", "job-progress-fill");
    if (fraction !== null) fill.style.setProperty("--progress", String(fraction));
    bar.appendChild(fill);
    wrap.append(row, bar);
    bodyEl.append(wrap, el("p", "job-progress-note", "Cancelling writes nothing, and the original stays as it is."));
    footerButton(s.cancelling ? "Cancelling…" : "Cancel", cancelJob, { disabled: s.cancelling });
  }

  function renderResult() {
    const outcome = s.outcome;
    if (outcome.state === "failed") {
      descEl.textContent = "Nothing was written.";
      bodyEl.append(el("p", "tool-result", outcome.error));
      footerButton("Done", close, { primary: true });
      return;
    }
    const r = outcome.result || {};
    descEl.textContent = "Done. The original is unchanged.";
    const result = el("div", "tool-result");
    const line = el("p");
    line.append(
      el("b", "", `Made ${plural(r.files, "file", "files")}.`),
      document.createTextNode(` In the folder “${r.name}”${savePlaceClause(r.where)}.`)
    );
    result.appendChild(line);
    bodyEl.appendChild(result);
    footerButton("Show in folder", () => showFileJobOutput(r.path));
    footerButton("Done", close, { primary: true });
  }

  // ---- Actions --------------------------------------------------------------

  // A folder already there is never written into: the job takes the next
  // free name, as Export does, so only a wrong name or folder stops here.
  async function confirmDestination() {
    const { folder, name } = s.destination.value();
    const target = await callApi("check_tool_destination", "split", s.source, folder, name);
    if (!s || s.phase !== "setup") return false;
    if (!target.ok) {
      s.destination.showError(target.field, target.error);
      return false;
    }
    return true;
  }

  async function start() {
    if (!setupReady()) return;
    s.starting = true;
    updatePrimary();
    let answer = null;
    try {
      if (await confirmDestination()) {
        answer = await callApi("start_file_job", "split", s.source, {
          mode: s.mode,
          value: value(),
          ...s.destination.value(),
        });
      }
    } finally {
      if (s) s.starting = false;
    }
    if (!s) return;
    if (!answer || !answer.ok) {
      updatePrimary();
      if (answer) showToast(answer.busy ? "Another file job is running" : answer.error);
      return;
    }
    s.jobId = answer.id;
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
    await callApi("cancel_file_job");
  }

  function onJobEvent(event) {
    if (!s || event.id !== s.jobId) return;
    if (event.state === "working") {
      s.event = event;
      if (isOpen()) render();
      return;
    }
    s.jobId = null;
    if (event.state === "cancelled") {
      s.phase = "setup";
      s.cancelling = false;
      s.event = null;
    } else {
      s.phase = "result";
      s.outcome = event;
    }
    if (isOpen()) {
      render();
      focusFirst();
      if (s.phase === "setup") askPreview();
    }
  }

  // ---- Keys and voice -------------------------------------------------------

  function onKeydown(ev) {
    if (!isOpen() || !s) return;
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#splitDialog)")) return;
    if (s.destination && s.destination.isMenuOpen()) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      // A running job is only stopped by Cancel.
      if (s.phase !== "working") close();
      return;
    }
    // Enter splits from a field too (the button is the form's one action);
    // a focused button presses itself.
    if (ev.key === "Enter" && !ev.altKey && !ev.ctrlKey && !ev.shiftKey && !ev.metaKey) {
      const active = document.activeElement;
      if (active && active.tagName === "BUTTON") return;
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
      else if (s.destination && s.destination.isMenuOpen()) s.destination.closeMenu({ focusButton: true });
      else close();
    }
  }

  return {
    open,
    isOpen,
    handleCommand,
    // Home's wiring: the file browser "Choose another folder…" uses, and
    // what a Recent PDF that has gone offers.
    configure(options) {
      saveAs = options.saveAs || saveAs;
      onMissing = options.onMissing || onMissing;
    },
  };
})();
