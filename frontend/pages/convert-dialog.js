// Convert to PDF (docs/PRD.md § "Post-v1: file tools"), from Home's Tools
// page: images and e-books into one new PDF, one image a page, in the order
// listed. Set up → Working → Result in one card, like the other tools
// (tool-dialogs.js), whose helpers this shares.
//
// Files come from "Add files…" (the in-app Open dialog, listing images and
// e-books instead of PDFs) or are dropped on the window while this is open
// (js/filedrop.js hands them over). Each is described by Python first (type,
// size, pages, a thumbnail); one it can't read is refused with a toast naming
// it. The list (js/file-list.js, shared with Combine files) reorders by
// dragging a row, by its Move up / Move down buttons, or with Alt+↑/↓ on a
// focused row, and takes the card's spare height (the card grows to the
// window's height less 48px). The Name is "<the file's name>" for one file and
// "Images <DD-MM-YYYY>" for several, following the list until the reader
// types one.
//
// Built from DOM calls with textContent: names come from the reader's disk.
"use strict";

const CONVERT_PAGE_SIZES = [
  { value: "image", label: "Same as image" },
  { value: "a4", label: "A4" },
  { value: "letter", label: "Letter" },
];

const ConvertDialog = (() => {
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
  // Set up's nodes kept between renders (`setupParts`), made again for a
  // new dialog or after Working.
  let setupUi = null;

  // { phase, files: [{path, name, type, size, pages, ebook, thumbnail}],
  //   pageSize, destination (the shared Name + "Save in" block, made with
  //   the first file, gone with the last), starting, jobId, event, outcome,
  //   cancelling }
  let s = null;

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "convertDialog";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool dialog-card--file-list");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "convertDialogTitle");
    card.setAttribute("aria-describedby", "convertDialogDesc");
    card.tabIndex = -1;
    const body = el("div", "dialog-body");
    const header = el("div", "dialog-header");
    const badge = el("div", "dialog-badge");
    badge.appendChild(iconEl("convert"));
    const text = el("div");
    const title = el("p", "dialog-title", "Convert to PDF");
    title.id = "convertDialogTitle";
    descEl = el("p", "dialog-desc");
    descEl.id = "convertDialogDesc";
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
    onConvertibleDrop((paths) => {
      // Not while another dialog (the Open dialog) is over it.
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
      pageSize: "image",
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
    document.body.classList.add("is-converting");
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
    document.body.classList.remove("is-converting");
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    scope.leave();
  }

  function close() {
    hide();
    s = null;
    if (opener && opener.isConnected && opener.focus) opener.focus();
    opener = null;
  }

  function focusFirst() {
    const add = bodyEl.querySelector(".drop-zone .btn");
    const target = (s.phase === "setup" && !s.files.length && add) || primaryBtn || footerEl.querySelector("button");
    if (target) target.focus();
    else card.focus();
  }

  // ---- Files ----------------------------------------------------------------

  // The files join the list at once, as rows with their names and
  // placeholders for the rest, which Python then describes in place. One
  // that can't be used leaves the list again, with a toast naming it.
  async function addPaths(paths) {
    if (!s || s.phase !== "setup" || !paths.length) return;
    // One already listed (or twice in what came) is skipped, with a toast,
    // whether it came from Add files… or a drop: each file has one row.
    paths = skipListedFiles(s.files, paths);
    if (!paths.length) return;
    const state = s;
    const keys = new Set(paths.map(fileListPathKey));
    state.files = [...state.files, ...paths.map((path) => ({ path, name: fileListName(path), pending: true }))];
    renderKeepingFocus();
    let described;
    try {
      described = await callApi("describe_convert_inputs", paths);
    } catch (err) {
      console.error("convert-dialog: describe", err);
      showToast("Couldn't read those files.");
      described = [];
    }
    if (s !== state) return;
    for (const entry of described) {
      if (entry.error) showToast(entry.error);
      settleFile(entry.path, entry.error ? null : entry);
      keys.delete(fileListPathKey(entry.path));
    }
    for (const key of keys) settleFile(key, null);
    try {
      await suggestDest();
    } catch (err) {
      console.error("convert-dialog: suggesting a name", err);
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
  // first file's folder, and the name is made from the whole list (Python's
  // destinations.list_stem) until the reader types one. With a block already
  // there nothing is awaited here: the name catches up in the background, so
  // a move or a removal is drawn at once.
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
    const answer = await callApi("suggest_tool_name", "convert", first, dirName(first), inputPaths());
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
          ? (await callApi("suggest_tool_name", "convert", s.files[0].path, folder, inputPaths())).stem
          : null,
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
    // The list may have changed while the first name was being asked for.
    s.destination.refreshName();
  }

  // "Choose another folder…": the in-app file browser, picking a folder,
  // with this card stepped aside meanwhile.
  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await stacked(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Another dialog (the Open dialog, the folder picker, "Replace?") over
  // this one, which stays as it is underneath (js/dialog-motion.js).
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
    return Boolean(s) && s.phase === "setup" && !s.starting && s.files.length > 0 &&
      !s.files.some((f) => f.pending) && Boolean(s.destination) && s.destination.isValid();
  }

  function updatePrimary() {
    if (s && s.phase === "setup" && primaryBtn) primaryBtn.disabled = !setupReady();
  }

  // The shared list (js/file-list.js): what it was asked to do.
  const files = createFileList({
    label: "Files, in the order they become pages",
    meta: (file) => `${file.type} · ${formatFileSize(file.size)}`,
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
    descEl.textContent =
      "Turns images into one PDF, one image a page, in the order below. E-books (EPUB, XPS, CBZ, FB2) work too.";
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
    // The destination block is the same nodes after a render, moved back in.
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

  // Set up's parts, made once per dialog: the Page size row, the e-book
  // note and the footer's buttons.
  function setupParts() {
    const grid = el("div", "tool-grid");
    const sizes = el("div", "segmented");
    sizes.setAttribute("aria-label", "Page size");
    const sizeButtons = CONVERT_PAGE_SIZES.map((size) => {
      const button = el("button", "segmented-btn", size.label);
      button.type = "button";
      button.dataset.keep = `size-${size.value}`;
      button.dataset.value = size.value;
      button.addEventListener("click", () => {
        s.pageSize = size.value;
        renderKeepingFocus();
      });
      sizes.appendChild(button);
      return button;
    });
    initRadioCards(sizes, { selectOnArrow: true });
    grid.append(el("span", "tool-grid-label", "Page size"), sizes);
    const ebookNote = el("div", "tool-notes");
    const note = el("p", "tool-note");
    note.append(
      iconEl("info"),
      el("span", "", "An e-book is laid out on A4 pages. It comes out plainer than in an e-book reader.")
    );
    ebookNote.appendChild(note);
    footerEl.replaceChildren();
    const cancel = footerButton("Cancel", close);
    const primary = footerButton("Create PDF", start, { primary: true });
    return { state: s, grid, sizeButtons, ebookNote, cancel, primary };
  }

  // Set up, updated in place: the list keeps its rows (js/file-list.js), and
  // the Page size row, the note, the Name block and the footer's buttons
  // stay the same nodes; only what changed is touched.
  function renderSetup() {
    // "4 files · 4 pages", at the top of the list.
    const pages = pageCount();
    const summary = `${plural(s.files.length, "file", "files")} · ${plural(pages, "page", "pages")}`;
    const area = files.build(s.files, { emptyText: "Drop images or an e-book here", onAdd: chooseFile, summary });
    if (!setupUi || setupUi.state !== s) setupUi = setupParts();
    for (const button of setupUi.sizeButtons) button.classList.toggle("selected", button.dataset.value === s.pageSize);
    const parts = [area, setupUi.grid];
    if (s.files.some((f) => f.ebook)) parts.push(setupUi.ebookNote);
    if (s.destination) parts.push(s.destination.element);
    syncChildren(bodyEl, parts);
    syncChildren(footerEl, [setupUi.cancel, setupUi.primary]);
    primaryBtn = setupUi.primary;
    primaryBtn.textContent = s.files.length && pages ? `Create PDF · ${plural(pages, "page", "pages")}` : "Create PDF";
    primaryBtn.disabled = !setupReady();
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
      document.createTextNode(` ${plural(r.pages, "page", "pages")}${savePlaceClause(r.where)}.`)
    );
    result.appendChild(line);
    bodyEl.appendChild(result);
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

  // Before the job: the folder is writable, the name usable and none of the
  // files given, and a PDF already there with that name (in whichever folder
  // was chosen) is only replaced after asking.
  async function confirmDestination(inputs) {
    const { folder, name } = s.destination.value();
    const target = await callApi("check_tool_destination", "convert", inputs[0], folder, name, inputs);
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
        const options = { inputs, page_size: s.pageSize, ...s.destination.value() };
        answer = await callApi("start_file_job", "convert", inputs[0], options);
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
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#convertDialog)")) return;
    // The "Save in" menu takes its own keys (Esc closes only the menu).
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
      // A focused button presses itself.
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
    } else if (command.intent === "DIALOG_PICK" && s.phase === "setup") {
      const size = card.querySelectorAll(".segmented-btn")[command.index - 1];
      if (size) size.click();
    }
  }

  return {
    open,
    isOpen,
    handleCommand,
    // Home's wiring: how "Add files…" picks a file, the file browser that
    // "Choose another folder…" picks a folder with, and how a new PDF is
    // opened.
    configure(options) {
      addFiles = options.addFiles || addFiles;
      saveAs = options.saveAs || saveAs;
      openOutput = options.openOutput || openOutput;
    },
    // What a drop of images or e-books does while this is open.
    addPaths: (paths) => addPaths(paths),
  };
})();
