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
// focused row.
//
// Built from DOM calls with textContent: names come from the reader's disk.
"use strict";

const CONVERT_PAGE_SIZES = [
  { value: "image", label: "Same as image" },
  { value: "a4", label: "A4" },
  { value: "letter", label: "Letter" },
];

const ConvertDialog = (() => {
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
    card = el("div", "dialog-card dialog-card--tool");
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
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
    FileJobs.subscribe(onJobEvent);
    onConvertibleDrop((paths) => {
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
    scrim.hidden = true;
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

  async function addPaths(paths) {
    if (!s || s.phase !== "setup" || !paths.length) return;
    let described;
    try {
      described = await callApi("describe_convert_inputs", paths);
    } catch (err) {
      console.error("convert-dialog: describe", err);
      showToast("Couldn't read those files.");
      return;
    }
    if (!s) return;
    for (const entry of described) {
      if (entry.error) showToast(entry.error);
      else s.files.push(entry);
    }
    await suggestDest();
    renderKeepingFocus();
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
  // is its folder, and the name is its name until the reader types one.
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
    const answer = await callApi("suggest_tool_name", "convert", first, dirName(first));
    if (!s || s.destination || !s.files.length) return;
    s.destination = createSaveDestination({
      kind: "file",
      extension: ".pdf",
      stem: answer.stem,
      originFolder: dirName(first),
      originLabel: "Beside the first file",
      suggestName: async (folder) =>
        s && s.files.length ? (await callApi("suggest_tool_name", "convert", s.files[0].path, folder)).stem : null,
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
  }

  // "Choose another folder…": the in-app file browser, picking a folder,
  // with this card stepped aside meanwhile.
  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await aside(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

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
    return Boolean(s) && s.phase === "setup" && !s.starting && s.files.length > 0 &&
      Boolean(s.destination) && s.destination.isValid();
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
    descEl.textContent =
      "Turns images into one PDF, one image a page, in the order below. E-books (EPUB, XPS, CBZ, FB2) work too.";
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

  function renderSetup() {
    bodyEl.appendChild(buildDropZone({ text: "Drop images or an e-book here", onAdd: chooseFile }));
    if (s.files.length) bodyEl.appendChild(files.build(s.files));

    const grid = el("div", "tool-grid");
    const sizes = el("div", "segmented");
    sizes.setAttribute("aria-label", "Page size");
    for (const size of CONVERT_PAGE_SIZES) {
      const button = el("button", "segmented-btn", size.label);
      button.type = "button";
      button.dataset.keep = `size-${size.value}`;
      button.classList.toggle("selected", size.value === s.pageSize);
      button.addEventListener("click", () => {
        s.pageSize = size.value;
        renderKeepingFocus();
      });
      sizes.appendChild(button);
    }
    initRadioCards(sizes, { selectOnArrow: true });
    grid.append(el("span", "tool-grid-label", "Page size"), sizes);
    bodyEl.appendChild(grid);

    if (s.files.some((f) => f.ebook)) {
      const notes = el("div", "tool-notes");
      const note = el("p", "tool-note");
      note.append(
        iconEl("info"),
        el("span", "", "An e-book is laid out on A4 pages. It comes out plainer than in an e-book reader.")
      );
      notes.appendChild(note);
      bodyEl.appendChild(notes);
    }
    if (s.destination) bodyEl.appendChild(s.destination.element);

    footerButton("Cancel", close);
    const pages = pageCount();
    const label = s.files.length && pages ? `Create PDF · ${plural(pages, "page", "pages")}` : "Create PDF";
    footerButton(label, start, { primary: true, disabled: !setupReady() });
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
    if (!isOpen() || !s) return;
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
      addFile = options.addFile || addFile;
      saveAs = options.saveAs || saveAs;
      openOutput = options.openOutput || openOutput;
    },
    // What a drop of images or e-books does while this is open.
    addPaths: (paths) => addPaths(paths),
  };
})();
