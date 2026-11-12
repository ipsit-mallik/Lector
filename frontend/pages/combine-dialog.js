// Combine files (docs/PRD.md § "Post-v1: file tools" → Tier 2), from Home's
// Tools page: several PDFs (and images or e-books) into one new PDF, in the
// order listed, each file's bookmarks under one named after it. Set up →
// Working → Result in one card, like Convert to PDF, whose shape this keeps;
// the list and its drop zone are the shared js/file-list.js.
//
// Files come from "Add files…" (the in-app Open dialog, listing PDFs, images
// and e-books) or are dropped on the window while this is open. Python
// describes each first; a protected PDF asks for its password then (held in
// Python's memory only, until this closes), and one that can't be used is
// refused with a toast naming it.
//
// Built from DOM calls with textContent: names come from the reader's disk.
"use strict";

const CombineDialog = (() => {
  let addFile = null; // home.js: () => Promise<path | null>
  let saveAs = null;
  let openOutput = null;

  let scrim = null;
  let card = null;
  let descEl = null;
  let bodyEl = null;
  let footerEl = null;
  let primaryBtn = null;
  let scope = null;
  let releaseTrap = null;
  let opener = null;

  // { phase, files: [{path, name, type, size, pages, pdf, ebook, needs_pass,
  //   signed, thumbnail}], destination (made with the first file, gone with
  //   the last), adding, starting, jobId, event, outcome, cancelling }
  let s = null;

  const DESCRIPTION = "Joins the files below into one new PDF, in this order. The originals stay as they are.";

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "combineDialog";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "combineDialogTitle");
    card.setAttribute("aria-describedby", "combineDialogDesc");
    card.tabIndex = -1;
    const body = el("div", "dialog-body");
    const header = el("div", "dialog-header");
    const badge = el("div", "dialog-badge");
    badge.appendChild(iconEl("combine"));
    const text = el("div");
    const title = el("p", "dialog-title", "Combine files");
    title.id = "combineDialogTitle";
    descEl = el("p", "dialog-desc");
    descEl.id = "combineDialogDesc";
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
    onCombinableDrop((paths) => {
      if (!isOpen() || s.phase !== "setup") return false;
      addPaths(paths);
      return true;
    });
  }

  function isOpen() {
    return Boolean(scrim) && !scrim.hidden;
  }

  // ---- Opening and closing ---------------------------------------------------

  function open() {
    build();
    if (s && s.phase === "working") {
      show();
      return;
    }
    s = {
      phase: "setup",
      files: [],
      destination: null,
      adding: false,
      starting: false,
      jobId: null,
      event: null,
      outcome: null,
      cancelling: false,
    };
    show();
  }

  function show() {
    opener = document.activeElement;
    render();
    scrim.hidden = false;
    document.body.classList.add("is-combining");
    releaseTrap = trapFocus(card);
    if (s.jobId) FileJobs.watch(s.jobId);
    scope.enter();
    focusFirst();
  }

  function hide() {
    if (!scrim || scrim.hidden) return;
    if (s && s.destination) s.destination.closeMenu();
    scrim.hidden = true;
    document.body.classList.remove("is-combining");
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    scope.leave();
  }

  // The passwords Python held for this list go with it.
  function close() {
    hide();
    s = null;
    callApi("forget_combine_passwords").catch((err) => console.error("combine-dialog: forgetting passwords", err));
    if (opener && opener.isConnected && opener.focus) opener.focus();
    opener = null;
  }

  function focusFirst() {
    const add = bodyEl.querySelector(".drop-zone .btn");
    const target = (s.phase === "setup" && s.files.length < 2 && add) || primaryBtn || footerEl.querySelector("button");
    if (target) target.focus();
    else card.focus();
  }

  // ---- Files ----------------------------------------------------------------

  // A protected PDF: its password, asked for here; null when cancelled.
  function unlock(entry) {
    return PasswordDialog.ask({
      name: entry.name,
      attempt: async (password) => {
        const retry = await callApi("unlock_combine_input", entry.path, password);
        if (retry.reason === "wrong_password") return { error: retry.error };
        return { done: retry };
      },
    });
  }

  async function addPaths(paths) {
    if (!s || s.phase !== "setup" || !paths.length || s.adding) return;
    s.adding = true;
    try {
      let described;
      try {
        described = await callApi("describe_combine_inputs", paths);
      } catch (err) {
        console.error("combine-dialog: describe", err);
        showToast("Couldn't read those files.");
        return;
      }
      for (let entry of described) {
        if (!s) return;
        if (entry.reason === "password") {
          const unlocked = await aside(() => unlock(entry));
          if (!unlocked) continue;
          entry = unlocked;
        }
        if (entry.error) showToast(entry.error);
        else s.files.push(entry);
      }
      await suggestDest();
      renderKeepingFocus();
    } finally {
      if (s) s.adding = false;
    }
  }

  async function chooseFile() {
    if (!addFile) return;
    hide();
    const path = await addFile();
    if (!s) return;
    show();
    if (path) await addPaths([path]);
  }

  // The destination block follows the first file: "Beside the first file"
  // is its folder, and the name is "<its name> (combined)" until the reader
  // types one.
  async function suggestDest() {
    if (!s) return;
    if (!s.files.length) {
      if (s.destination) s.destination.closeMenu();
      s.destination = null;
      return;
    }
    const first = s.files[0].path;
    if (s.destination) {
      s.destination.setOrigin(dirName(first));
      return;
    }
    const answer = await callApi("suggest_tool_name", "combine", first, dirName(first));
    if (!s || s.destination || !s.files.length) return;
    s.destination = createSaveDestination({
      kind: "file",
      extension: ".pdf",
      stem: answer.stem,
      originFolder: dirName(first),
      originLabel: "Beside the first file",
      suggestName: async (folder) =>
        s && s.files.length ? (await callApi("suggest_tool_name", "combine", s.files[0].path, folder)).stem : null,
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
  }

  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await aside(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Another dialog in front (the folder picker, a password, "Replace?"),
  // this one out of the way meanwhile and back after.
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

  function setupReady() {
    return Boolean(s) && s.phase === "setup" && !s.starting && s.files.length >= 2 &&
      Boolean(s.destination) && s.destination.isValid();
  }

  function updatePrimary() {
    if (s && s.phase === "setup" && primaryBtn) primaryBtn.disabled = !setupReady();
  }

  const files = createFileList({
    label: "Files, in the order they are joined",
    meta: (file) => {
      if (!file.pdf) return `${file.type} · ${formatFileSize(file.size)}`;
      const pages = `PDF · ${plural(file.pages, "page", "pages")}`;
      return file.needs_pass ? `${pages} · password entered` : pages;
    },
    onMove: (from, to) => move(from, to),
    onRemove: (index) => remove(index),
  });

  async function move(from, to) {
    if (to < 0 || to >= s.files.length || from === to) return;
    s.files = moveFileListItem(s.files, from, to);
    await suggestDest();
    render();
    const row = files.row(to);
    if (row) row.focus();
  }

  async function remove(index) {
    s.files = s.files.filter((_, i) => i !== index);
    await suggestDest();
    render();
    const next = files.row(Math.min(index, s.files.length - 1));
    if (next) next.focus();
    else focusFirst();
  }

  function pageCount() {
    return s.files.reduce((sum, f) => sum + (f.pages || 0), 0);
  }

  // ---- Rendering ------------------------------------------------------------

  function render() {
    bodyEl.replaceChildren();
    footerEl.replaceChildren();
    primaryBtn = null;
    descEl.textContent = DESCRIPTION;
    if (s.phase === "setup") renderSetup();
    else if (s.phase === "working") renderWorking();
    else renderResult();
    mountIcons(card);
  }

  // Re-render, keeping focus on the control in the same place.
  function renderKeepingFocus() {
    if (!s || !isOpen()) return;
    const focused = document.activeElement;
    const keep = focused && focused.dataset ? focused.dataset.keep : null;
    const inDestination = Boolean(s.destination) && Boolean(focused) && s.destination.element.contains(focused);
    const selection = inDestination && focused.tagName === "INPUT" ? [focused.selectionStart, focused.selectionEnd] : null;
    render();
    if (inDestination) {
      focused.focus();
      if (selection) focused.setSelectionRange(selection[0], selection[1]);
    } else if (keep) {
      const again = card.querySelector(`[data-keep="${keep}"]`);
      if (again) again.focus();
    }
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

  function notes(lines) {
    if (!lines.length) return null;
    const wrap = el("div", "tool-notes");
    for (const text of lines) {
      const note = el("p", "tool-note");
      note.append(iconEl("info"), el("span", "", text));
      wrap.appendChild(note);
    }
    return wrap;
  }

  function setupNotes() {
    const lines = [];
    const locked = s.files.filter((f) => f.needs_pass).map((f) => f.name);
    if (locked.length) {
      lines.push(`${listPages(locked)} ${locked.length === 1 ? "is" : "are"} password-protected. The combined PDF won’t be.`);
    }
    if (s.files.some((f) => f.signed)) lines.push("The new file won’t carry signatures.");
    if (s.files.some((f) => f.ebook)) {
      lines.push("An e-book is laid out on A4 pages. It comes out plainer than in an e-book reader.");
    }
    return notes(lines);
  }

  function renderSetup() {
    bodyEl.appendChild(buildDropZone({ text: "Drop PDFs or images here", onAdd: chooseFile }));
    if (s.files.length) bodyEl.appendChild(files.build(s.files));
    const extra = setupNotes();
    if (extra) bodyEl.appendChild(extra);
    if (s.destination) bodyEl.appendChild(s.destination.element);

    // "3 files · 214 pages", at the footer's left.
    const pages = pageCount();
    const count = el("span", "tool-footer-count");
    count.setAttribute("aria-live", "polite");
    if (s.files.length) count.textContent = `${plural(s.files.length, "file", "files")} · ${plural(pages, "page", "pages")}`;
    footerEl.appendChild(count);
    footerButton("Cancel", close);
    const label = s.files.length >= 2 ? `Combine · ${plural(pages, "page", "pages")}` : "Combine";
    const primary = footerButton(label, start, { primary: true, disabled: !setupReady() });
    if (s.files.length < 2) primary.title = "Add at least two files";
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
    bodyEl.append(wrap, el("p", "job-progress-note", "Cancelling writes nothing, and the files you chose stay as they are."));
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
    descEl.textContent = "Done. The original files are unchanged.";
    const result = el("div", "tool-result");
    const line = el("p");
    line.append(
      el("b", "", `Created ${r.name}.`),
      document.createTextNode(
        ` ${plural(r.pages, "page", "pages")} from ${plural(r.files, "file", "files")}${savePlaceClause(r.where)}.`
      )
    );
    result.appendChild(line);
    bodyEl.appendChild(result);
    const lines = [];
    if (s.files.some((f) => f.needs_pass)) lines.push("The combined PDF isn’t password-protected.");
    if (r.clashes && r.clashes.length) {
      lines.push(`Form fields from ${listPages(r.clashes)} may not work in the combined PDF.`);
    }
    const extra = notes(lines);
    if (extra) result.appendChild(extra);
    footerButton("Show in folder", () => showFileJobOutput(r.path));
    if (openOutput) {
      footerButton("Open in Lector", () => {
        close();
        openOutput(r.path);
      });
    }
    footerButton("Done", close, { primary: true });
  }

  // ---- Actions --------------------------------------------------------------

  async function confirmDestination(inputs) {
    const { folder, name } = s.destination.value();
    const target = await callApi("check_tool_destination", "combine", inputs[0], folder, name, inputs);
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
    if (!setupReady()) return;
    s.starting = true;
    updatePrimary();
    const inputs = s.files.map((f) => f.path);
    let answer = null;
    try {
      if (await confirmDestination(inputs)) {
        answer = await callApi("start_file_job", "combine", inputs[0], { inputs, ...s.destination.value() });
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
    }
  }

  // ---- Keys and voice -------------------------------------------------------

  function onKeydown(ev) {
    if (!isOpen() || !s) return;
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#combineDialog)")) return;
    if (s.destination && s.destination.isMenuOpen()) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      // A running job is only stopped by Cancel.
      if (s.phase !== "working") close();
      return;
    }
    if (ev.key === "Enter" && !ev.altKey && !ev.ctrlKey && !ev.shiftKey && !ev.metaKey) {
      const active = document.activeElement;
      if (isTypingTarget(active)) return;
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
    // Home's wiring, as Convert's: how "Add files…" picks a file, the file
    // browser "Choose another folder…" uses, and how a new PDF is opened.
    configure(options) {
      addFile = options.addFile || addFile;
      saveAs = options.saveAs || saveAs;
      openOutput = options.openOutput || openOutput;
    },
    // What a drop of PDFs, images or e-books does while this is open.
    addPaths: (paths) => addPaths(paths),
  };
})();
