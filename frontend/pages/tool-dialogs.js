// The file tools' dialog: Compress, Make searchable and Export as images
// (docs/PRD.md § "Post-v1: file tools"), opened from the reader's More menu,
// Home's Tools page, a Recent entry's "⋯" menu, or by voice. One card, three
// states: Set up (the choices), Working (progress), Result. Only the body and
// footer change between them.
//
// The work itself is a job in Python (api.py's `start_file_job`), followed
// through `FileJobs` (js/file-jobs.js). In the reader, Keep reading closes
// the dialog while the job runs on; the footer pill (reading.js) reopens it.
//
// Built from DOM calls with `textContent`, never markup: file and folder
// names come from the reader's disk.
//
// Where the output goes is the shared destination block (js/save-destination.js):
// a Name field and a "Save in" menu, always starting beside the original.
//
// Keys: Tab stays in the card, Esc closes it (while Working it means Keep
// reading, and on Home does nothing: a running job is only stopped by
// Cancel; with the "Save in" menu open it closes only the menu), Enter
// presses the primary button unless a text field has focus.
// Voice: "confirm" is the primary button, "cancel" closes or cancels, a
// number picks an option card (router.FILE_TOOL).
"use strict";

const TOOL_DIALOG_META = {
  compress: { title: "Compress PDF", icon: "compress" },
  searchable: { title: "Make searchable", icon: "searchable" },
  export: { title: "Export as images", icon: "export_images" },
};

const COMPRESS_LEVELS = [
  {
    value: "light",
    title: "Light",
    subtitle: "Lossless: tidies the file and subsets fonts. Images untouched, so the saving is small.",
  },
  {
    value: "balanced",
    title: "Balanced",
    subtitle: "Images at 150 ppi. Sharp on screen and in print at normal sizes.",
  },
  {
    value: "strong",
    title: "Strong",
    subtitle: "Images at 96 ppi. The smallest file, for email; photos look softer.",
  },
];

const EXPORT_FORMATS = [
  { value: "png", label: "PNG", hint: "Sharp text, larger files", extension: "png" },
  { value: "jpeg", label: "JPEG", hint: "Smaller files, best for photos", extension: "jpg" },
];
const EXPORT_PPI = [96, 150, 300];

// How long after the last keystroke the Pages field is checked.
const RANGE_CHECK_DELAY_MS = 150;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function iconEl(name, className = "") {
  const span = el("span", className);
  span.dataset.icon = name;
  span.setAttribute("aria-hidden", "true");
  return span;
}

function baseName(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function stemOf(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

function dirName(path) {
  const text = String(path || "");
  const cut = Math.max(text.lastIndexOf("\\"), text.lastIndexOf("/"));
  return cut > 0 ? text.slice(0, cut) : text;
}

// A page at `ppi` in pixels, as MuPDF renders it (a part pixel rounds up).
function pixelSize(pageSize, ppi) {
  const [w, h] = pageSize;
  return [Math.ceil((w * ppi) / 72 - 1e-6), Math.ceil((h * ppi) / 72 - 1e-6)];
}

const ToolDialog = (() => {
  let where = "reader"; // or "home"
  let saveAs = null;
  let openOutput = null;
  let onBackground = () => {};
  let onMissing = null;

  let scrim = null;
  let card = null;
  let badge = null;
  let titleEl = null;
  let descEl = null;
  let bodyEl = null;
  let footerEl = null;
  let scope = null;
  let releaseTrap = null;
  let opener = null;
  let primaryBtn = null;
  let rangeTimer = 0;
  let rangeCheck = 0;

  // The dialog's state: what it is for, what is chosen, where it stands.
  let s = null;

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "toolDialog";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "toolDialogTitle");
    card.setAttribute("aria-describedby", "toolDialogDesc");
    card.tabIndex = -1;
    const body = el("div", "dialog-body");
    const header = el("div", "dialog-header");
    badge = el("div", "dialog-badge");
    const text = el("div");
    titleEl = el("p", "dialog-title");
    titleEl.id = "toolDialogTitle";
    descEl = el("p", "dialog-desc");
    descEl.id = "toolDialogDesc";
    bodyEl = el("div", "tool-body");
    text.append(titleEl, descEl);
    header.append(badge, text);
    body.append(header, bodyEl);
    footerEl = el("div", "dialog-footer");
    card.append(body, footerEl);
    scrim.appendChild(card);
    document.body.appendChild(scrim);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
    FileJobs.subscribe(onJobEvent);
  }

  // ---- Opening -------------------------------------------------------------

  // Ask Python what the dialog needs to know about the PDF. From Home a
  // protected PDF asks for its password first (password-dialog.js); it is
  // only ever held in Python's memory, for this file's job.
  async function fetchInfo(tool, source) {
    const info = await callApi("file_tool_info", tool, source);
    if (info.ok || (info.reason !== "password" && info.reason !== "wrong_password")) return info;
    const unlocked = await PasswordDialog.ask({
      name: info.name || baseName(source),
      attempt: async (password) => {
        const retry = await callApi("file_tool_info", tool, source, password);
        if (retry.ok) return { done: retry };
        if (retry.reason === "wrong_password") return { error: retry.error };
        return { done: retry };
      },
    });
    return unlocked || { ok: false, cancelled: true };
  }

  // Open `tool`'s dialog for `source`: "open" (the reader's PDF, as it is
  // on screen) or a path (Home).
  async function open(tool, source) {
    build();
    if (!TOOL_DIALOG_META[tool]) return;
    if (s && s.phase === "working") {
      // A job is running: its dialog comes back instead of a new one.
      reopen();
      return;
    }
    let info;
    try {
      info = await fetchInfo(tool, source);
    } catch (err) {
      console.error("tool-dialogs: couldn't read the PDF", err);
      info = { ok: false, error: "Couldn't read the PDF. It may be damaged or no longer there." };
    }
    if (!info.ok) {
      if (info.cancelled) return;
      if (info.reason === "missing" && onMissing) onMissing(info.error, source);
      else showToast(info.error);
      return;
    }
    s = {
      tool,
      source,
      info,
      phase: "setup",
      destination: makeDestination(tool, source, info),
      starting: false,
      level: info.prefs.compress_level,
      format: info.prefs.export_format,
      ppi: info.prefs.export_ppi,
      searchPages: "all",
      exportPages: "all",
      rangeText: "",
      rangePages: null,
      rangeError: null,
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
    releaseTrap = trapFocus(card);
    if (s.jobId) FileJobs.watch(s.jobId);
    scope.enter();
    focusFirst();
  }

  // The Name and "Save in" rows: beside the original every time the dialog
  // opens, whatever was chosen last time.
  function makeDestination(tool, source, info) {
    return createSaveDestination({
      kind: tool === "export" ? "folder" : "file",
      extension: info.output.extension,
      stem: info.output.stem,
      originFolder: info.output.folder,
      originLabel: "Beside the original",
      suggestName: async (folder) => (await callApi("suggest_tool_name", tool, source, folder)).stem,
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
  }

  // "Choose another folder…": the in-app file browser, picking a folder.
  // This card steps aside while it is up and comes back after.
  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await aside(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Run `task` (another dialog) with this card hidden, then show it again,
  // still answering to whoever opened it.
  async function aside(task) {
    const returnTo = opener;
    hide();
    try {
      return await task();
    } finally {
      if (s && scrim.hidden) {
        show();
        opener = returnTo;
      }
    }
  }

  // Hide the card without forgetting it (Keep reading, the folder picker).
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
    clearTimeout(rangeTimer);
    s = null;
    if (opener && opener.isConnected && opener.focus) opener.focus();
    opener = null;
  }

  // The pill's click, or opening a tool while its job runs.
  function reopen() {
    if (!s || !scrim.hidden) return;
    show();
  }

  function focusFirst() {
    const chosen = bodyEl.querySelector("[role='radio'][tabindex='0']");
    const target = (s.phase === "setup" && chosen) || primaryBtn || footerEl.querySelector("button");
    if (target) target.focus();
    else card.focus();
  }

  // ---- Rendering ---------------------------------------------------------

  function setupDescription() {
    if (s.tool === "compress") return `Makes a smaller copy. ${s.info.name} stays as it is.`;
    if (s.tool === "searchable") {
      return "Adds an invisible text layer to scanned pages, so they can be found, selected and highlighted in any PDF viewer.";
    }
    return "Saves pages as image files, with your highlights drawn in.";
  }

  function render() {
    badge.replaceChildren(iconEl(TOOL_DIALOG_META[s.tool].icon));
    titleEl.textContent = TOOL_DIALOG_META[s.tool].title;
    descEl.textContent = setupDescription();
    bodyEl.replaceChildren();
    footerEl.replaceChildren();
    primaryBtn = null;
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

  // Option cards (the shared .option-row radio card), numbered for voice.
  function optionCards(options, chosen, onPick, label) {
    const group = el("div", "tool-options");
    group.setAttribute("aria-label", label);
    for (const option of options) {
      const row = el("button", "option-row card-lift");
      row.type = "button";
      row.classList.toggle("selected", option.value === chosen);
      const text = el("span", "option-text");
      text.append(el("span", "option-title", option.title));
      if (option.subtitle) text.append(el("span", "option-subtitle", option.subtitle));
      row.append(el("span", "option-radio"), text);
      row.addEventListener("click", () => onPick(option.value));
      group.appendChild(row);
    }
    initRadioCards(group, { selectOnArrow: true });
    return group;
  }

  // The shared segmented control (file-tools.css).
  function segmented(options, chosen, onPick, label) {
    const group = el("div", "segmented");
    group.setAttribute("aria-label", label);
    for (const option of options) {
      const button = el("button", "segmented-btn", option.label);
      button.type = "button";
      button.classList.toggle("selected", option.value === chosen);
      button.addEventListener("click", () => onPick(option.value));
      group.appendChild(button);
    }
    initRadioCards(group, { selectOnArrow: true });
    return group;
  }

  // One-line option cards with a muted note at the end (Export's and Make
  // searchable's Pages): the same radio card, compact.
  function radios(options, chosen, onPick, label) {
    const group = el("div", "tool-options tool-options--compact");
    group.setAttribute("aria-label", label);
    for (const option of options) {
      const row = el("button", "option-row card-lift");
      row.type = "button";
      row.classList.toggle("selected", option.value === chosen);
      const text = el("span", "option-text");
      text.append(el("span", "option-title", option.label));
      row.append(el("span", "option-radio"), text);
      if (option.meta) row.append(el("span", "option-meta", option.meta));
      row.addEventListener("click", () => onPick(option.value));
      group.appendChild(row);
    }
    initRadioCards(group, { selectOnArrow: true });
    return group;
  }

  // Re-render, keeping focus on the control in the same place.
  function rerender() {
    const focused = document.activeElement;
    const groups = Array.from(card.querySelectorAll("[role='radiogroup']"));
    const groupIndex = groups.findIndex((g) => g.contains(focused));
    const itemIndex = groupIndex >= 0 ? Array.from(groups[groupIndex].children).indexOf(focused) : -1;
    const fieldFocused = Boolean(focused) && focused.id === "toolRangeInput";
    const caret = fieldFocused ? focused.selectionStart : 0;
    // The destination block is the same nodes after a render, moved back in:
    // whatever in it had focus gets it again, caret and all.
    const inDestination = Boolean(s.destination) && Boolean(focused) && s.destination.element.contains(focused);
    const selection = inDestination && focused.tagName === "INPUT" ? [focused.selectionStart, focused.selectionEnd] : null;
    render();
    if (inDestination) {
      focused.focus();
      if (selection) focused.setSelectionRange(selection[0], selection[1]);
    } else if (groupIndex >= 0) {
      const group = card.querySelectorAll("[role='radiogroup']")[groupIndex];
      const item = group && group.children[itemIndex];
      if (item) item.focus();
    } else if (fieldFocused) {
      const input = document.getElementById("toolRangeInput");
      if (input) {
        input.focus();
        input.setSelectionRange(caret, caret);
      }
    }
  }

  // The shared Name + "Save in" block; Export's hint says how the images
  // inside the folder are named.
  function destinationBlock() {
    let hint = "";
    if (s.tool === "export") {
      const ext = EXPORT_FORMATS.find((f) => f.value === s.format).extension;
      hint = `Files inside are named ${stemOf(s.info.name)}-p001.${ext}, -p002 and so on.`;
    }
    s.destination.setHint(hint);
    return s.destination.element;
  }

  // Whether Set up's primary button may run the tool now.
  function setupReady() {
    if (!s || s.phase !== "setup" || s.starting) return false;
    if (!s.destination.isValid()) return false;
    if (s.tool === "export") return exportCount() > 0;
    if (s.tool === "searchable") return s.info.textless > 0;
    return true;
  }

  // The destination block's validity changed (a name typed, a folder
  // checked): only the primary button follows; nothing is re-rendered.
  function updatePrimary() {
    if (!s || s.phase !== "setup" || !primaryBtn) return;
    if (s.tool === "searchable" && !s.info.textless) return; // its button is Done
    primaryBtn.disabled = !setupReady();
  }

  function notesBlock({ signed = true } = {}) {
    const notes = [];
    if (where === "reader" && s.info.dirty) {
      const n = s.info.unsaved;
      notes.push(
        n > 0
          ? `Includes your ${n === 1 ? "unsaved highlight" : `${n} unsaved highlights`}. ${
              n === 1 ? "It stays" : "They stay"
            } unsaved here until you save.`
          : "Includes your unsaved changes. They stay unsaved here until you save."
      );
    }
    if (signed && s.info.signed) {
      notes.push("This PDF is digitally signed. The new file won't carry the signature; the original keeps it.");
    }
    if (!notes.length) return null;
    const wrap = el("div", "tool-notes");
    for (const text of notes) {
      const note = el("p", "tool-note");
      note.append(iconEl("info"), el("span", "", text));
      wrap.appendChild(note);
    }
    return wrap;
  }

  function renderSetup() {
    if (s.tool === "compress") renderCompressSetup();
    else if (s.tool === "searchable") renderSearchableSetup();
    else renderExportSetup();
  }

  function renderCompressSetup() {
    const facts = el("p", "tool-facts");
    facts.append(
      document.createTextNode("Now "),
      el("b", "", formatFileSize(s.info.size)),
      document.createTextNode(
        ` · ${plural(s.info.pages, "page", "pages")} · ${plural(s.info.images, "image", "images")}`
      )
    );
    bodyEl.append(
      facts,
      el("span", "tool-section-label", "Level"),
      optionCards(COMPRESS_LEVELS, s.level, (value) => { s.level = value; rerender(); }, "Level"),
      destinationBlock()
    );
    const notes = notesBlock();
    if (notes) bodyEl.append(notes);
    footerButton("Cancel", close);
    footerButton("Compress", start, { primary: true, disabled: !setupReady() });
  }

  function renderSearchableSetup() {
    const { textless, pages } = s.info;
    if (!textless) {
      bodyEl.append(el("p", "tool-result", "Every page already has text. There's nothing to add."));
      footerButton("Done", close, { primary: true });
      return;
    }
    const others = pages - textless;
    const rest = others
      ? ` The other ${others} already ${others === 1 ? "has" : "have"} text and ${others === 1 ? "is" : "are"} left as ${
          others === 1 ? "it is" : "they are"
        }.`
      : "";
    const summary = el("div", "tool-summary");
    summary.append(
      el("span", "tool-summary-number", String(textless)),
      el(
        "p",
        "tool-summary-text",
        `of ${plural(pages, "page", "pages")} ${textless === 1 ? "is a picture" : "are pictures"} of text, so ${
          textless === 1 ? "it" : "they"
        } can't be searched or selected yet.${rest}`
      )
    );
    const options = [{ value: "all", label: `All ${plural(textless, "page", "pages")} without text` }];
    if (where === "reader" && s.info.current_textless) {
      options.push({ value: "this", label: "Only this page", meta: `Page ${s.info.current_page + 1}` });
    }
    const language = el("div", "tool-row-line");
    const value = el("span");
    value.append(el("b", "", "English"), document.createTextNode(" · read on this computer"));
    language.append(el("span", "", "Language"), value);
    bodyEl.append(
      summary,
      radios(options, s.searchPages, (choice) => { s.searchPages = choice; rerender(); }, "Pages"),
      language,
      destinationBlock()
    );
    const notes = notesBlock();
    if (notes) bodyEl.append(notes);
    footerButton("Cancel", close);
    footerButton("Make searchable", start, { primary: true, disabled: !setupReady() });
  }

  function exportCount() {
    if (s.exportPages === "all") return s.info.pages;
    if (s.exportPages === "this") return 1;
    return s.rangePages ? s.rangePages.length : 0;
  }

  function renderExportSetup() {
    const grid = el("div", "tool-grid");
    const format = EXPORT_FORMATS.find((f) => f.value === s.format);
    const formatControl = el("div", "tool-control");
    formatControl.append(
      segmented(
        EXPORT_FORMATS.map((f) => ({ value: f.value, label: f.label })),
        s.format,
        (value) => { s.format = value; rerender(); },
        "Format"
      ),
      el("span", "tool-hint", format.hint)
    );
    const [w, h] = pixelSize(s.info.page_size, s.ppi);
    const ppiControl = el("div", "tool-control");
    ppiControl.append(
      segmented(
        EXPORT_PPI.map((ppi) => ({ value: ppi, label: `${ppi} ppi` })),
        s.ppi,
        (value) => { s.ppi = value; rerender(); },
        "Resolution"
      ),
      el("span", "tool-hint", `${w} × ${h} px a page`)
    );
    const pageOptions = [{ value: "all", label: "All pages", meta: plural(s.info.pages, "image", "images") }];
    if (where === "reader") {
      pageOptions.push({ value: "this", label: "This page", meta: `Page ${s.info.current_page + 1}` });
    }
    const rangeMeta =
      s.exportPages === "range" && s.rangePages ? plural(s.rangePages.length, "image", "images") : "Choose a range";
    pageOptions.push({ value: "range", label: "Pages", meta: rangeMeta });
    const pages = radios(
      pageOptions,
      s.exportPages,
      (value) => {
        const wasRange = s.exportPages === "range";
        s.exportPages = value;
        rerender();
        if (value === "range" && !wasRange) {
          const input = document.getElementById("toolRangeInput");
          if (input) input.focus();
        }
      },
      "Pages"
    );
    const label = (text) => el("span", "tool-grid-label", text);
    grid.append(label("Format"), formatControl, label("Resolution"), ppiControl);
    bodyEl.append(grid, pages);
    if (s.exportPages === "range") bodyEl.append(rangeField());
    bodyEl.append(destinationBlock());
    const notes = notesBlock({ signed: false });
    if (notes) bodyEl.append(notes);
    const n = exportCount();
    footerButton("Cancel", close);
    footerButton(`Export ${plural(n, "image", "images")}`, start, { primary: true, disabled: !setupReady() });
  }

  function rangeField() {
    const wrap = el("div", "tool-range");
    const input = el("input");
    input.type = "text";
    input.id = "toolRangeInput";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "1–5, 8, 11–13";
    input.value = s.rangeText;
    input.setAttribute("aria-label", "Pages to export");
    const error = el("p", "dialog-field-error", s.rangeError || "");
    error.id = "toolRangeError";
    error.setAttribute("role", "alert");
    error.hidden = !s.rangeError;
    if (s.rangeError) {
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", "toolRangeError");
    }
    input.addEventListener("input", () => {
      s.rangeText = input.value;
      s.rangePages = null;
      clearTimeout(rangeTimer);
      rangeTimer = setTimeout(checkRange, RANGE_CHECK_DELAY_MS);
      if (primaryBtn) primaryBtn.disabled = true;
    });
    wrap.append(input, error);
    return wrap;
  }

  // The range is checked by the same parser the job uses (naming.py), so the
  // count on the button is the number of files that will be written.
  async function checkRange() {
    if (!s || s.tool !== "export") return;
    const id = ++rangeCheck;
    const text = s.rangeText;
    const answer = text.trim()
      ? await callApi("parse_export_range", text, s.info.pages)
      : { error: "Type the pages to export, such as 1–5, 8." };
    if (!s || id !== rangeCheck || text !== s.rangeText) return;
    s.rangePages = answer.pages || null;
    s.rangeError = answer.error || null;
    rerender();
  }

  function renderWorking() {
    const event = s.event || {};
    const fraction = fileJobFraction(event);
    const wrap = el("div", fraction === null ? "job-progress is-indeterminate" : "job-progress");
    const bar = el("div", "job-progress-bar");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Progress");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    const fill = el("div", "job-progress-fill");
    bar.appendChild(fill);
    const row = el("div", "job-progress-row");
    row.append(el("span", "job-progress-phase"), el("span", "job-progress-pct"));
    wrap.append(row, bar);
    const note =
      where === "reader"
        ? "You can keep reading while this runs. Cancelling writes nothing."
        : "Cancelling writes nothing, and the original stays as it is.";
    bodyEl.append(wrap, el("p", "job-progress-note", note));
    if (where === "reader") footerButton("Keep reading", keepReading);
    footerButton(s.cancelling ? "Cancelling…" : "Cancel", cancelJob, { disabled: s.cancelling });
    paintProgress(wrap);
  }

  function paintProgress(wrap) {
    const event = s.event || {};
    const fraction = fileJobFraction(event);
    wrap.classList.toggle("is-indeterminate", fraction === null);
    const bar = wrap.querySelector(".job-progress-bar");
    const fill = wrap.querySelector(".job-progress-fill");
    if (fraction === null) {
      fill.style.removeProperty("--progress");
      bar.removeAttribute("aria-valuenow");
    } else {
      fill.style.setProperty("--progress", String(fraction));
      bar.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
    }
    wrap.querySelector(".job-progress-phase").textContent = fileJobPhaseText(event);
    wrap.querySelector(".job-progress-pct").textContent = fraction === null ? "" : `${Math.round(fraction * 100)}%`;
  }

  // Only the progress line changes while Working; the rest stays put.
  function updateProgress() {
    if (!s || s.phase !== "working" || scrim.hidden) return;
    const wrap = bodyEl.querySelector(".job-progress");
    if (wrap) paintProgress(wrap);
  }

  function resultLine(bold, rest) {
    const line = el("p");
    line.append(el("b", "", bold), document.createTextNode(rest));
    return line;
  }

  function renderResult() {
    const outcome = s.outcome;
    descEl.textContent = "Done. The original is unchanged.";
    if (outcome.state === "failed") {
      descEl.textContent = "Nothing was written.";
      bodyEl.append(el("p", "tool-result", outcome.error));
      footerButton("Done", close, { primary: true });
      return;
    }
    const r = outcome.result || {};
    const result = el("div", "tool-result");
    let path = r.path || null;
    let canOpen = Boolean(path) && s.tool !== "export";
    if (s.tool === "compress" && r.compact) {
      descEl.textContent = "Nothing was written. The original is unchanged.";
      const level = COMPRESS_LEVELS.find((l) => l.value === r.level);
      result.append(
        el(
          "p",
          "",
          `This PDF is already compact: compressing at ${level ? level.title : r.level} would save less than 5%, so no file was written.`
        )
      );
      bodyEl.append(result);
      if (r.level !== "strong") footerButton("Try Strong", tryStrong);
      footerButton("Done", close, { primary: true });
      return;
    }
    if (s.tool === "compress") {
      const bars = el("div", "size-bars");
      const share = Math.max(2, Math.round((100 * r.after) / Math.max(1, r.before)));
      const barOf = (after, pct) => {
        const bar = el("div", after ? "size-bar is-after" : "size-bar");
        const fill = el("div", "size-bar-fill");
        fill.style.setProperty("--share", `${pct}%`);
        bar.appendChild(fill);
        return bar;
      };
      bars.append(
        el("span", "", "Before"),
        barOf(false, 100),
        el("span", "", formatFileSize(r.before)),
        el("span", "", "After"),
        barOf(true, share),
        el("b", "", formatFileSize(r.after))
      );
      const saved = Math.round(100 - (100 * r.after) / Math.max(1, r.before));
      result.append(resultLine(`${saved}% smaller.`, ` Saved as ${r.name}${savePlaceClause(r.where)}.`));
      bodyEl.append(bars, result);
    } else if (s.tool === "searchable") {
      if (r.written === false) {
        descEl.textContent = "Nothing was written. The original is unchanged.";
        result.append(
          el("p", "", "No readable text was found on these pages (they may be photos or blank), so no file was written.")
        );
        path = null;
        canOpen = false;
      } else {
        result.append(
          resultLine(`Text added to ${plural(r.added, "page", "pages")}.`, ` Saved as ${r.name}${savePlaceClause(r.where)}.`)
        );
        if (r.no_text && r.no_text.length) {
          const one = r.no_text.length === 1;
          const note = el("p", "tool-note");
          note.append(
            iconEl("info"),
            el(
              "span",
              "",
              `No readable text was found on ${one ? "page" : "pages"} ${listPages(r.no_text)} (${
                one ? "it may be a photo or blank" : "they may be photos or blank"
              }).`
            )
          );
          result.append(note);
        }
      }
      bodyEl.append(result);
    } else {
      result.append(
        resultLine(`${plural(r.count, "image", "images")} exported.`, ` In the folder “${r.name}”${savePlaceClause(r.where)}.`)
      );
      bodyEl.append(result);
    }
    if (path) footerButton("Show in folder", () => showFileJobOutput(path));
    if (canOpen && openOutput) {
      footerButton("Open", () => {
        close();
        openOutput(path);
      });
    }
    footerButton("Done", close, { primary: true });
  }

  // ---- Actions -------------------------------------------------------------

  function jobOptions() {
    const options = { ...s.destination.value() };
    if (s.tool === "compress") options.level = s.level;
    if (s.tool === "searchable") {
      options.pages = s.searchPages === "this" ? [s.info.current_page + 1] : s.info.textless_pages;
    }
    if (s.tool === "export") {
      options.format = s.format;
      options.ppi = s.ppi;
      if (s.exportPages === "all") options.pages = Array.from({ length: s.info.pages }, (_, i) => i + 1);
      else if (s.exportPages === "this") options.pages = [s.info.current_page + 1];
      else options.pages = s.rangePages || [];
    }
    return options;
  }

  // Before a job starts: the folder is there and writable, the name is
  // usable and isn't the original, and a file already there with that name
  // (in whichever folder was chosen) is only replaced after asking. A folder
  // export never asks: it takes the next free name.
  async function confirmDestination() {
    const { folder, name } = s.destination.value();
    const target = await callApi("check_tool_destination", s.tool, s.source, folder, name);
    if (!s || s.phase !== "setup") return false;
    if (!target.ok) {
      s.destination.showError(target.field, target.error);
      return false;
    }
    if (!target.exists || s.tool === "export") return true;
    const replace = await aside(() => ReplaceDialog.open({ name: target.name, folder: target.folder }));
    return Boolean(replace) && Boolean(s) && s.phase === "setup";
  }

  async function start() {
    if (!setupReady()) return;
    s.starting = true;
    updatePrimary();
    let answer = null;
    try {
      if (await confirmDestination()) answer = await callApi("start_file_job", s.tool, s.source, jobOptions());
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
    const current = FileJobs.current();
    s.event = current && current.id === answer.id ? current : null;
    s.phase = "working";
    s.cancelling = false;
    FileJobs.watch(answer.id);
    render();
    focusFirst();
  }

  function tryStrong() {
    s.level = "strong";
    s.phase = "setup";
    start();
  }

  async function cancelJob() {
    if (!s || s.phase !== "working" || s.cancelling) return;
    s.cancelling = true;
    render();
    focusFirst();
    await callApi("cancel_file_job");
  }

  function keepReading() {
    if (!s || s.phase !== "working" || where !== "reader") return;
    FileJobs.watch(null);
    hide();
    onBackground(s.event || { id: s.jobId, tool: s.tool, state: "working" });
    if (opener && opener.isConnected && opener.focus) opener.focus();
  }

  function onJobEvent(event) {
    if (!s || event.id !== s.jobId) return;
    if (event.state === "working") {
      s.event = event;
      updateProgress();
      return;
    }
    s.jobId = null;
    if (scrim.hidden) {
      // Ended in the background: FileJobs has said how in a toast.
      s = null;
      return;
    }
    if (event.state === "cancelled") {
      s.phase = "setup";
      s.cancelling = false;
      s.event = null;
      render();
      focusFirst();
      return;
    }
    s.phase = "result";
    s.outcome = event;
    render();
    focusFirst();
  }

  // ---- Keys and voice ------------------------------------------------------

  function onKeydown(ev) {
    if (!scrim || scrim.hidden || !s) return;
    // The Save-As or password dialog may be up over this one.
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#toolDialog)")) return;
    // The "Save in" menu takes its own keys (Esc closes only the menu).
    if (s.destination && s.destination.isMenuOpen()) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      if (s.phase === "working") keepReading();
      else close();
      return;
    }
    if (ev.key === "Enter" && !ev.shiftKey && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
      const active = document.activeElement;
      if (isTypingTarget(active)) return;
      // A focused footer button presses itself.
      if (active && footerEl.contains(active)) return;
      if (primaryBtn && !primaryBtn.disabled) {
        ev.preventDefault();
        primaryBtn.click();
      }
    }
  }

  // The option cards a spoken number picks: Compress's levels, Make
  // searchable's and Export's Pages.
  function pickNumbered(number) {
    const group = bodyEl.querySelector(".tool-options, .tool-radios");
    const item = group && group.children[number - 1];
    if (item) item.click();
  }

  function handleCommand(command) {
    if (!s) return;
    switch (command.intent) {
      case "TOOL_CONFIRM":
        if (primaryBtn && !primaryBtn.disabled) primaryBtn.click();
        break;
      case "CANCEL":
        if (s.phase === "working") cancelJob();
        else if (s.destination && s.destination.isMenuOpen()) s.destination.closeMenu({ focusButton: true });
        else close();
        break;
      case "DIALOG_PICK":
        if (s.phase === "setup") pickNumbered(command.index);
        break;
    }
  }

  return {
    open,
    reopen,
    isOpen: () => Boolean(scrim) && !scrim.hidden,
    // Whether a job this dialog started is running (shown or not).
    isWorking: () => Boolean(s) && s.phase === "working",
    handleCommand,
    // The page's own wiring: where it is, its Save-As dialog, how to open a
    // file a job wrote, what Keep reading hands the job to (the reader's
    // pill), and what a missing file shows (Home's Remove from Recent).
    configure(options) {
      where = options.where || where;
      saveAs = options.saveAs || saveAs;
      openOutput = options.openOutput || openOutput;
      onBackground = options.onBackground || onBackground;
      onMissing = options.onMissing || onMissing;
    },
  };
})();
