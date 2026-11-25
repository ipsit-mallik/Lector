// Combine files (docs/PRD.md § "Post-v1: file tools" → Tier 2), from Home's
// Tools page: several PDFs (and images or e-books) into one new PDF, in the
// order listed, each file's bookmarks under one named after it. Set up →
// Working → Result in one card, like Convert to PDF, whose shape this keeps;
// the list and its drop zone are the shared js/file-list.js, which takes the
// card's spare height (the card grows to the window's height less 48px).
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
  let addFiles = null; // home.js: () => Promise<path[]>, several at a time
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
  // Set up's nodes kept between renders: { state, cancel, primary, notes,
  // notesKey }, made again for a new dialog or after Working.
  let setupUi = null;

  // { phase, files: [{path, name, type, size, pages, pdf, ebook, needs_pass,
  //   signed, thumbnail}], destination (made with the first file, gone with
  //   the last), starting, jobId, event, outcome, cancelling }
  let s = null;

  const DESCRIPTION = "Joins the files below into one new PDF, in this order. The originals stay as they are.";

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "combineDialog";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool dialog-card--file-list");
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
    smoothDialogHeight(card);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
    FileJobs.subscribe(onJobEvent);
    onCombinableDrop((paths) => {
      // Not while another dialog (the Open dialog, a password) is over it.
      if (!isOpen() || s.phase !== "setup" || scrim.inert) return false;
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
    files.cancelDrag();
    hideDialogScrim(scrim);
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

  // The files join the list at once, as rows with their names and
  // placeholders for the rest, which Python then describes in place (a
  // protected PDF asks for its password over this dialog). One that can't
  // be used leaves the list again, with a toast naming it. Files added while
  // earlier ones are still being described join the list at once too, and
  // are described after them, in order (one password box at a time).
  let describing = Promise.resolve();

  function addPaths(paths) {
    if (!s || s.phase !== "setup" || !paths.length) return Promise.resolve();
    // One already listed (or twice in what came) is skipped, with a toast,
    // whether it came from Add files… or a drop: each file has one row.
    paths = skipListedFiles(s.files, paths);
    if (!paths.length) return Promise.resolve();
    const state = s;
    state.files = [...state.files, ...paths.map((path) => ({ path, name: fileListName(path), pending: true }))];
    renderKeepingFocus();
    const run = describing.then(() => describeAdded(state, paths));
    describing = run.catch((err) => console.error("combine-dialog: adding files", err));
    return run;
  }

  async function describeAdded(state, paths) {
    if (s !== state) return;
    const keys = new Set(paths.map(fileListPathKey));
    let described;
    try {
      described = await callApi("describe_combine_inputs", paths);
    } catch (err) {
      console.error("combine-dialog: describe", err);
      showToast("Couldn't read those files.");
      described = [];
    }
    for (let entry of described) {
      if (s !== state) return;
      // Removed while earlier files were being described: nothing to ask.
      if (!hasPendingFile(s.files, entry.path)) {
        keys.delete(fileListPathKey(entry.path));
        continue;
      }
      if (entry.reason === "password") {
        const unlocked = await stacked(() => unlock(entry));
        if (s !== state) return;
        entry = unlocked || { ...entry, cancelled: true };
      }
      if (entry.error && !entry.cancelled) showToast(entry.error);
      settleFile(entry.path, entry.error || entry.cancelled ? null : entry);
      keys.delete(fileListPathKey(entry.path));
    }
    // Anything Python didn't answer for goes too.
    for (const key of keys) settleFile(key, null);
    try {
      await suggestDest();
    } finally {
      // The rows are settled whatever the name's answer: never left as placeholders.
      if (s === state) renderKeepingFocus();
    }
  }

  // A pending row's file described (`entry`), or gone (null); one the
  // reader removed meanwhile stays removed.
  function settleFile(path, entry) {
    s.files = settlePendingFile(s.files, path, entry);
  }

  // Add files…: the Open dialog, several at a time, over this one. They go
  // after the files already listed, in the folder's order; one already
  // listed is skipped.
  async function chooseFile() {
    if (!addFiles) return;
    const paths = await stacked(addFiles);
    if (!s) return;
    await addPaths(paths || []);
  }

  function inputPaths() {
    return s.files.filter((f) => !f.pending).map((f) => f.path);
  }

  // The destination block follows the list: "Beside the first file" is the
  // first file's folder, and the name is "<its name> (combined)" when that is
  // a PDF, "Combined <DD-MM-YYYY>" otherwise (Python's
  // destinations.list_stem), until the reader types one. With a block
  // already there nothing is awaited here: the name catches up in the
  // background, so a move or a removal is drawn at once.
  async function suggestDest() {
    if (!s) return;
    if (!s.files.length) {
      if (s.destination) s.destination.closeMenu();
      s.destination = null;
      return;
    }
    const first = s.files[0].path;
    if (s.destination) {
      if (dirName(first) !== s.originFolder) {
        s.originFolder = dirName(first);
        s.destination.setOrigin(s.originFolder);
      }
      s.destination.refreshName();
      return;
    }
    const answer = await callApi("suggest_tool_name", "combine", first, dirName(first), inputPaths());
    if (!s || s.destination || !s.files.length) return;
    s.originFolder = dirName(s.files[0].path);
    s.destination = createSaveDestination({
      kind: "file",
      extension: ".pdf",
      stem: answer.stem,
      originFolder: s.originFolder,
      originLabel: "Beside the first file",
      suggestName: async (folder) =>
        s && s.files.length
          ? (await callApi("suggest_tool_name", "combine", s.files[0].path, folder, inputPaths())).stem
          : null,
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
    // The list may have changed while the first name was being asked for.
    s.destination.refreshName();
  }

  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await stacked(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Another dialog (the Open dialog, the folder picker, a password,
  // "Replace?") over this one, which stays as it is underneath
  // (js/dialog-motion.js's `stackDialog`).
  function stacked(task) {
    return stackDialog(
      {
        scrim,
        onLeave: () => {
          if (s && s.destination) s.destination.closeMenu();
          files.cancelDrag();
          scope.leave();
        },
        onReturn: () => scope.enter(),
        refocus: focusFirst,
      },
      task
    );
  }

  function setupReady() {
    return Boolean(s) && s.phase === "setup" && !s.starting && s.files.length >= 2 &&
      !s.files.some((f) => f.pending) && Boolean(s.destination) && s.destination.isValid();
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

  // Drawn in the same task as the drop, key or click; the name follows.
  function move(from, to) {
    if (to < 0 || to >= s.files.length || from === to) return;
    s.files = moveFileListItem(s.files, from, to);
    suggestDest();
    render();
    const row = files.row(to);
    if (row) row.focus();
  }

  function remove(index) {
    s.files = s.files.filter((_, i) => i !== index);
    suggestDest();
    render();
    const next = files.row(Math.min(index, s.files.length - 1));
    if (next) next.focus();
    else focusFirst();
  }

  function pageCount() {
    return s.files.reduce((sum, f) => sum + (f.pages || 0), 0);
  }

  // ---- Rendering ------------------------------------------------------------

  // Set up is updated in place (`renderSetup`); Working and Result are
  // drawn afresh. Only icons not drawn yet are mounted.
  function render() {
    descEl.textContent = DESCRIPTION;
    if (s.phase === "setup") {
      renderSetup();
    } else {
      setupUi = null;
      bodyEl.replaceChildren();
      footerEl.replaceChildren();
      primaryBtn = null;
      if (s.phase === "working") renderWorking();
      else renderResult();
    }
    mountMissingIcons(card);
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

  function setupNoteLines() {
    const lines = [];
    const locked = s.files.filter((f) => f.needs_pass).map((f) => f.name);
    if (locked.length) {
      lines.push(`${listPages(locked)} ${locked.length === 1 ? "is" : "are"} password-protected. The combined PDF won’t be.`);
    }
    if (s.files.some((f) => f.signed)) lines.push("The new file won’t carry signatures.");
    if (s.files.some((f) => f.ebook)) {
      lines.push("An e-book is laid out on A4 pages. It comes out plainer than in an e-book reader.");
    }
    return lines;
  }

  // Set up, updated in place: the list keeps its rows (js/file-list.js),
  // and the notes, the Name block and the footer's buttons stay the same
  // nodes; only what changed is touched, so a file added (or removed, or
  // moved) never redraws what was there.
  function renderSetup() {
    // "3 files · 214 pages", at the top of the list.
    const pages = pageCount();
    const summary = `${plural(s.files.length, "file", "files")} · ${plural(pages, "page", "pages")}`;
    const area = files.build(s.files, { emptyText: "Drop PDFs or images here", onAdd: chooseFile, summary });
    if (!setupUi || setupUi.state !== s) {
      footerEl.replaceChildren();
      const cancel = footerButton("Cancel", close);
      const primary = footerButton("Combine", start, { primary: true });
      setupUi = { state: s, cancel, primary, notesKey: null, notes: null };
    }
    const lines = setupNoteLines();
    const key = lines.join("\n");
    if (setupUi.notesKey !== key) {
      setupUi.notesKey = key;
      setupUi.notes = notes(lines);
    }
    syncChildren(bodyEl, [area, setupUi.notes, s.destination ? s.destination.element : null].filter(Boolean));
    syncChildren(footerEl, [setupUi.cancel, setupUi.primary]);
    primaryBtn = setupUi.primary;
    primaryBtn.textContent = s.files.length >= 2 ? `Combine · ${plural(pages, "page", "pages")}` : "Combine";
    primaryBtn.disabled = !setupReady();
    primaryBtn.title = s.files.length < 2 ? "Add at least two files" : "";
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
    const replace = await stacked(() => ReplaceDialog.open({ name: target.name, folder: target.folder }));
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
    if (!isOpen() || scrim.inert || !s) return;
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
      addFiles = options.addFiles || addFiles;
      saveAs = options.saveAs || saveAs;
      openOutput = options.openOutput || openOutput;
    },
    // What a drop of PDFs, images or e-books does while this is open.
    addPaths: (paths) => addPaths(paths),
  };
})();
