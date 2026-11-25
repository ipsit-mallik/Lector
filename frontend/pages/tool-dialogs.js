// The file tools' dialog: Compress, Make searchable and Export as images
// (docs/PRD.md § "Post-v1: file tools"), opened from the reader's More menu,
// Home's Tools page, a Recent entry's "⋯" menu, or by voice. One card, three
// states: Set up (the choices), Working (progress), Result. Only the body and
// footer change between them. From the Tools page it first asks which PDF,
// as a step of the same card (js/dialog-steps.js), with Back in Set up.
//
// The work itself is a job in Python (api.py's `start_file_job`), followed
// through `FileJobs` (js/file-jobs.js). In the reader, Keep reading closes
// the dialog while the job runs on; the footer pill (reading.js) reopens it.
//
// Built from DOM calls with `textContent`, never markup: file and folder
// names come from the reader's disk.
//
// Compress's own parts (its two modes, the estimates, its results) are in
// compress-panel.js; this file calls in for them.
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

const EXPORT_FORMATS = [
  { value: "png", label: "PNG", hint: "Sharp text, larger files", extension: "png" },
  { value: "jpeg", label: "JPEG", hint: "Smaller files, best for photos", extension: "jpg" },
];
const EXPORT_PPI = [96, 150, 300];

// How long after the last keystroke the Pages field is checked.
const RANGE_CHECK_DELAY_MS = 150;

const TOOL_READ_ERROR = "Couldn't read the PDF. It may be damaged or no longer there.";

// The choices a tool step starts from before Python has said what was used
// last (settings' file tool prefs); replaced by those once it has.
const DEFAULT_TOOL_PREFS = {
  compress_mode: "quality",
  compress_limit: null,
  compress_unit: "KB",
  export_format: "png",
  export_ppi: 150,
};

// A placeholder for a word or number still to come (components.css's
// `.skeleton-text`), `width` wide.
function skeletonText(width) {
  const span = el("span", "skeleton skeleton-text");
  span.style.width = width;
  span.setAttribute("aria-hidden", "true");
  return span;
}

// A line of facts with some still to come: each part a string, or
// `{ pending: "4em" }` (a placeholder that wide) for one not known yet.
function factsLine(parts) {
  const nodes = [];
  parts.forEach((part, i) => {
    if (i) nodes.push(document.createTextNode(" · "));
    nodes.push(typeof part === "string" ? document.createTextNode(part) : skeletonText(part.pending));
  });
  return nodes;
}

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
  // Opened from the Tools page: "choose a PDF" and the tool as two steps of
  // this one card (js/dialog-steps.js); null otherwise.
  let steps = null;
  let browse = null; // home.js: the Open dialog, () => Promise<path | null>

  // The dialog's state: what it is for, what is chosen, where it stands.
  let s = null;
  // The tool prefs Python last said (the next PDF's placeholders start
  // from them).
  let lastPrefs = null;
  // Which entry into the tool step answers are for (`fill`).
  let entryToken = 0;
  // How often a page check stopped by an older cancel is asked again.
  const ANALYSIS_RETRIES = 2;

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
    smoothDialogHeight(card);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
    FileJobs.subscribe(onJobEvent);
    CompressPanel.init({
      state: () => s,
      // On the "choose a PDF" step the tool isn't drawn: estimates that
      // arrive meanwhile wait in its state for the way back.
      hidden: () => scrim.hidden || choosing(),
      body: bodyEl,
      rerender,
      segmented,
      footerButton,
      destinationBlock,
      notesBlock,
      setupReady,
      updatePrimary,
      start,
      close,
      openOutput: (path) => {
        close();
        if (openOutput) openOutput(path);
      },
    });
  }

  // ---- Opening -------------------------------------------------------------

  // What Python has been asked about each PDF since the dialog opened, by
  // path: a quick look (`file_tool_info`, a few milliseconds, never behind
  // the worker), and for Make searchable its pages with no text
  // (`file_tool_analysis`, in the worker). Asked as soon as a PDF is chosen
  // in the "choose a PDF" list, so they are usually in by Continue.
  let infos = new Map();
  let analyses = new Map();
  // The answers already in, by path, so a step for a PDF whose look has
  // arrived is drawn complete at once, with no placeholders to replace.
  let known = new Map();
  let knownAnalyses = new Map();

  function requestInfo(tool, source) {
    if (!infos.has(source)) {
      const asked = Promise.resolve(callApi("file_tool_info", tool, source)).catch((err) => {
        console.error("tool-dialogs: couldn't read the PDF", err);
        return { ok: false, error: TOOL_READ_ERROR };
      });
      infos.set(source, asked);
      asked.then((info) => {
        if (infos.get(source) !== asked) return;
        if (info.ok) known.set(source, info);
        // A failure is asked again next time (the file may be back).
        else if (info.reason !== "password") infos.delete(source);
      });
    }
    return infos.get(source);
  }

  // The newest request wins in Python (an older file's check is stopped),
  // so a waiting answer for another file is forgotten rather than reused.
  function requestAnalysis(source) {
    for (const other of Array.from(analyses.keys())) if (other !== source) analyses.delete(other);
    if (!analyses.has(source)) {
      const asked = Promise.resolve(callApi("file_tool_analysis", source)).catch((err) => {
        console.error("tool-dialogs: couldn't look at the pages", err);
        return { ok: false, error: TOOL_READ_ERROR };
      });
      analyses.set(source, asked);
      asked.then((answer) => {
        if (analyses.get(source) !== asked) return;
        if (answer.ok) knownAnalyses.set(source, answer);
        else analyses.delete(source);
      });
    }
    return analyses.get(source);
  }

  // What is known about `source` now, without waiting: the quick look (and
  // Make searchable's pages, if they are in too), or null.
  function knownInfo(source) {
    const info = known.get(source);
    if (!info) return null;
    const pages = knownAnalyses.get(source);
    if (!info.analysis_pending || !pages) return info;
    return { ...info, textless: pages.textless, textless_pages: pages.textless_pages, analysis_pending: false };
  }

  function forgetAnswers() {
    infos = new Map();
    analyses = new Map();
    known = new Map();
    knownAnalyses = new Map();
  }

  // Make searchable's look at every page is dropped when nobody waits for it.
  function cancelAnalysis() {
    analyses = new Map();
    knownAnalyses = new Map();
    Promise.resolve(callApi("cancel_file_tool_analysis")).catch((err) =>
      console.error("tool-dialogs: couldn't stop the page check", err)
    );
  }

  // A PDF chosen in the list: start reading it.
  function prefetch(tool, source) {
    requestInfo(tool, source);
    if (tool === "searchable") requestAnalysis(source);
  }

  // What the dialog needs to know about `source`, or null once the reason it
  // can't be had has been said (a toast; Home's Remove from Recent for a
  // file that has gone; nothing when the password was cancelled). A
  // protected PDF from Home asks for its password, over this dialog
  // (password-dialog.js); it is only ever held in Python's memory, for this
  // file's job.
  // `wanted()`, when given, says whether anyone still waits for the answer
  // (the tool step it is for is still showing): if not, nothing is asked
  // or said.
  async function readInfo(tool, source, { wanted = null } = {}) {
    let info = await requestInfo(tool, source);
    if (wanted && !wanted()) return null;
    if (!info.ok && (info.reason === "password" || info.reason === "wrong_password")) {
      const ask = () =>
        PasswordDialog.ask({
          name: info.name || baseName(source),
          attempt: async (password) => {
            const retry = await callApi("file_tool_info", tool, source, password);
            if (retry.reason === "wrong_password") return { error: retry.error };
            return { done: retry };
          },
        });
      const unlocked = scrim.hidden ? await ask() : await stacked(ask);
      info = unlocked || { ok: false, cancelled: true };
      if (info.ok) {
        infos.set(source, Promise.resolve(info));
        known.set(source, info);
      }
      if (wanted && !wanted()) return null;
    }
    if (info.ok) return info;
    if (info.cancelled) return null;
    if (info.reason === "missing" && onMissing) onMissing(info.error, source);
    else showToast(info.error || TOOL_READ_ERROR);
    return null;
  }

  // The tool step for a PDF chosen in the list, before Python has said
  // anything about it: what the list knows (its name and page count) and
  // the last choices used, everything else a placeholder (`pending`).
  function provisionalInfo(tool, source, entry) {
    return {
      ok: true,
      pending: true,
      name: baseName(source),
      pages: entry && entry.page_count ? entry.page_count : null,
      size: null,
      page_size: null,
      signed: false,
      dirty: false,
      unsaved: 0,
      textless: null,
      textless_pages: null,
      current_page: null,
      current_textless: false,
      analysis_pending: tool === "searchable",
      prefs: lastPrefs || DEFAULT_TOOL_PREFS,
      output: { folder: dirName(source), stem: "", extension: tool === "export" ? "" : ".pdf" },
    };
  }

  // Fill the tool step in as what it waits for arrives: the quick look,
  // then (Make searchable) the pages with no text, and Compress's
  // estimates. Each lands where its placeholder is; the card's height, if
  // it changes at all, eases (js/dialog-motion.js).
  // Each time the tool step is entered (Continue, or opening from a menu)
  // its answers are for that entry only: Back, or another entry, makes them
  // stale, and a stale one lands in nothing.
  async function fill(state) {
    const token = ++entryToken;
    const here = () => s === state && token === entryToken && !choosing() && !scrim.hidden;
    if (state.info.pending) {
      const info = await readInfo(state.tool, state.source, { wanted: here });
      if (!here()) return;
      if (!info) {
        infoFailed();
        return;
      }
      adoptInfo(state, info);
    }
    if (state.compress) CompressPanel.opened(state);
    if (state.tool === "searchable" && state.info.analysis_pending) analyse(state, here);
  }

  function adoptInfo(state, info) {
    lastPrefs = info.prefs;
    state.info = info;
    if (state.compress) CompressPanel.adoptInfo(state);
    if (state.tool === "export" && !state.formatPicked) {
      state.format = info.prefs.export_format;
      state.ppi = info.prefs.export_ppi;
    }
    state.destination.setSuggestedStem(info.output.stem);
    state.destination.setPending(false);
    redraw();
    if (state.tool === "export" && state.exportPages === "range") checkRange();
  }

  // A check stopped by an older request's cancel (Back and Continue again
  // quickly, or another row chosen and this one again) is asked again,
  // while its step is still showing.
  async function analyse(state, here) {
    let answer = await requestAnalysis(state.source);
    for (let again = 0; answer.cancelled && again < ANALYSIS_RETRIES && here(); again++) {
      analyses.delete(state.source);
      answer = await requestAnalysis(state.source);
    }
    if (!here() || answer.cancelled) return;
    if (!answer.ok) {
      showToast(answer.error || TOOL_READ_ERROR);
      infoFailed();
      return;
    }
    state.info = { ...state.info, textless: answer.textless, textless_pages: answer.textless_pages, analysis_pending: false };
    redraw();
  }

  // The PDF couldn't be read (the reason has been said): back to the list,
  // or, opened straight from a menu, closed.
  function infoFailed() {
    if (steps && !steps.isChoosing()) {
      if (s && s.compress) CompressPanel.closed(s);
      s = null;
      steps.back();
    } else if (!steps) {
      close();
    }
  }

  // Draw the tool step again for something that arrived; the card's height
  // eases to what it now needs.
  function redraw() {
    if (!s || choosing() || scrim.hidden) return;
    changeDialogContent(card, rerender);
  }

  // The dialog's state for `source`. Coming Back and choosing another PDF
  // keeps the choices that aren't about the file (Compress's mode, Export's
  // format and resolution) and starts the rest afresh.
  function freshState(tool, source, info, previous) {
    const destination = makeDestination(tool, source, info);
    if (info.pending) destination.setPending(true);
    const next = {
      tool,
      source,
      info,
      phase: "setup",
      destination,
      formatPicked: false,
      starting: false,
      compress: tool === "compress" ? CompressPanel.freshState(info) : null,
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
    if (previous && previous.tool === tool) {
      next.format = previous.format;
      next.ppi = previous.ppi;
      next.formatPicked = previous.formatPicked;
      if (next.compress && previous.compress && "mode" in previous.compress) {
        next.compress.mode = previous.compress.mode;
        next.compress.modePicked = true;
      }
    }
    return next;
  }

  // Open `tool`'s dialog for `source`: "open" (the reader's PDF, as it is
  // on screen) or a path (Home). With `choose` (Home's Tools page) it starts
  // at "choose a PDF", in the same card (js/dialog-steps.js).
  // A second open while the first is still reading (a double click on a
  // Tools card, before the dialog shows) is ignored: it would show the dialog
  // twice, with two focus traps.
  let opening = false;
  async function open(tool, source, options = {}) {
    if (opening) return;
    opening = true;
    try {
      await openNow(tool, source, options);
    } finally {
      opening = false;
    }
  }

  async function openNow(tool, source, { choose = false } = {}) {
    build();
    if (!TOOL_DIALOG_META[tool]) return;
    if (s && s.phase === "working") {
      // A job is running: its dialog comes back instead of a new one.
      reopen();
      return;
    }
    if (choose) {
      await openAtChooser(tool);
      return;
    }
    // From a menu: the quick look (a few milliseconds) is there before the
    // dialog shows; what reads every page comes after, into placeholders.
    forgetAnswers();
    const info = await readInfo(tool, source);
    if (!info) return;
    steps = null;
    s = freshState(tool, source, info, null);
    lastPrefs = info.prefs;
    show();
    fill(s);
  }

  async function openAtChooser(tool) {
    s = null;
    forgetAnswers();
    steps = createToolSteps({
      card,
      badge,
      titleEl,
      descEl,
      bodyEl,
      footerEl,
      toolName: TOOL_NAMES[tool],
      browse: () => (browse ? browse() : Promise.resolve(null)),
      stacked,
      prefetch: (path) => prefetch(tool, path),
      // At once: the PDF chosen before keeps everything; another starts
      // from what the list knows, the rest still on its way.
      enterTool: (path, entry) => {
        if (s && s.source === path) return;
        const previous = s;
        if (previous) CompressPanel.closed(previous);
        requestInfo(tool, path);
        const ready = knownInfo(path);
        if (ready) lastPrefs = ready.prefs;
        s = freshState(tool, path, ready || provisionalInfo(tool, path, entry), previous);
      },
      renderTool: renderToolStep,
      entered: () => {
        if (s) fill(s);
      },
      // Back: what was being worked out for the tool stops (Compress keeps
      // what it has finished, for coming back to the same PDF).
      leaveTool: () => {
        entryToken += 1;
        if (s && s.compress) CompressPanel.paused(s);
        if (tool === "searchable") cancelAnalysis();
      },
      focusTool: focusFirst,
      toolTitle: () => TOOL_DIALOG_META[tool].title,
      currentSource: () => (s ? s.source : null),
      close,
    });
    await steps.begin();
    show();
  }

  // Whether the dialog is on its "choose a PDF" step.
  function choosing() {
    return Boolean(steps) && steps.isChoosing();
  }

  function show() {
    if (!scrim.hidden) return;
    opener = document.activeElement;
    render();
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    if (s && s.jobId) FileJobs.watch(s.jobId);
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
      onChange: () => {
        updatePrimary();
        // Compress retries its checking in a new folder after "not enough
        // free disk space".
        if (s && s.compress) CompressPanel.destinationChanged(s);
      },
    });
  }

  // "Choose another folder…": the in-app file browser, picking a folder,
  // over this card.
  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await stacked(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Run `task` (another dialog) over this one, which stays as it is
  // underneath (js/dialog-motion.js's `stackDialog`); focus comes back to
  // whatever opened it.
  function stacked(task) {
    return stackDialog(
      {
        scrim,
        onLeave: () => {
          if (s && s.destination) s.destination.closeMenu();
          scope.leave();
        },
        onReturn: () => scope.enter(),
        refocus: focusFirst,
      },
      task
    );
  }

  // Hide the card without forgetting it (Keep reading, the folder picker).
  function hide() {
    if (!scrim || scrim.hidden) return;
    if (s && s.destination) s.destination.closeMenu();
    hideDialogScrim(scrim);
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    scope.leave();
  }

  function close() {
    hide();
    clearTimeout(rangeTimer);
    // Compress's estimates and held result are deleted with the dialog;
    // Make searchable's page check, if it is still going, stops.
    CompressPanel.closed(s);
    if (analyses.size) cancelAnalysis();
    forgetAnswers();
    // A protected PDF's password was held for this dialog only.
    Promise.resolve(callApi("forget_file_tool_passwords")).catch((err) =>
      console.error("tool-dialogs: couldn't forget the passwords", err)
    );
    s = null;
    steps = null;
    if (opener && opener.isConnected && opener.focus) opener.focus();
    opener = null;
  }

  // The pill's click, or opening a tool while its job runs.
  function reopen() {
    if (!s || !scrim.hidden) return;
    show();
  }

  function focusFirst() {
    if (choosing()) {
      steps.focusChooser();
      return;
    }
    const chosen =
      bodyEl.querySelector(".compress-levels [role='radio'][tabindex='0'], .compress-pills [role='radio'][tabindex='0']") ||
      bodyEl.querySelector("[role='radio'][tabindex='0']");
    const target = (s.phase === "setup" && chosen) || primaryBtn || footerEl.querySelector("button");
    if (target) target.focus();
    else card.focus();
  }

  // ---- Rendering ---------------------------------------------------------

  function setupDescription() {
    if (s.tool === "searchable") {
      return "Adds an invisible text layer to scanned pages, so they can be found, selected and highlighted in any PDF viewer.";
    }
    return "Saves pages as image files, with your highlights drawn in.";
  }

  function render() {
    if (choosing()) steps.render();
    else renderToolStep();
  }

  // The tool itself. Opened from the Tools page, its Set up has Back at the
  // footer's left end (not on Working or Done).
  function renderToolStep() {
    badge.replaceChildren(iconEl(TOOL_DIALOG_META[s.tool].icon));
    titleEl.textContent = TOOL_DIALOG_META[s.tool].title;
    // Compress's header is one line of facts, the same in every state (a
    // placeholder for any not known yet).
    if (s.compress && s.info.pending) {
      descEl.replaceChildren(
        ...factsLine([
          s.info.name,
          { pending: "3.6em" },
          s.info.pages ? plural(s.info.pages, "page", "pages") : { pending: "4em" },
        ])
      );
    } else {
      descEl.textContent = s.compress ? CompressPanel.description(s) : setupDescription();
    }
    bodyEl.replaceChildren();
    footerEl.replaceChildren();
    primaryBtn = null;
    if (s.phase === "setup") renderSetup();
    else if (s.phase === "working") renderWorking();
    else renderResult();
    if (backShown()) footerEl.prepend(steps.backButton());
    mountIcons(card);
  }

  function backShown() {
    return Boolean(steps) && !steps.isChoosing() && Boolean(s) && s.phase === "setup";
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
      else if (option.metaPending) {
        const meta = el("span", "option-meta");
        meta.append(skeletonText(option.metaPending));
        row.append(meta);
      }
      row.addEventListener("click", () => onPick(option.value));
      group.appendChild(row);
    }
    initRadioCards(group, { selectOnArrow: true });
    return group;
  }

  // Re-render, keeping focus on the control in the same place.
  function rerender() {
    if (!s || choosing()) return;
    const focused = document.activeElement;
    const groups = Array.from(card.querySelectorAll("[role='radiogroup']"));
    const groupIndex = groups.findIndex((g) => g.contains(focused));
    const itemIndex = groupIndex >= 0 ? Array.from(groups[groupIndex].children).indexOf(focused) : -1;
    // A control with an id (Export's range field, Compress's "Other size…"
    // field, unit button and link) gets focus back by its id.
    const focusId = Boolean(focused) && card.contains(focused) && focused.id ? focused.id : null;
    const caret = focusId && focused.type === "text" ? focused.selectionStart : null;
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
      // A Compress card or pill that has just turned unavailable can't keep focus:
      // the group's own Tab stop takes it, or the dialog's first control.
      if (item && item.getAttribute("aria-disabled") !== "true") item.focus();
      else if (group && group.querySelector("[tabindex='0']")) group.querySelector("[tabindex='0']").focus();
      else focusFirst();
    } else if (focusId) {
      const again = document.getElementById(focusId);
      if (again) {
        again.focus();
        if (caret !== null && again.type === "text") again.setSelectionRange(caret, caret);
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
    if (s.info.pending || s.info.analysis_pending) return false;
    if (!s.destination.isValid()) return false;
    if (s.compress) return CompressPanel.ready(s);
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
    if (s.compress) CompressPanel.renderSetup(s);
    else if (s.tool === "searchable") renderSearchableSetup();
    else renderExportSetup();
  }

  // While its pages are still being looked at (in the worker): the same
  // parts in the same places, the counts as placeholders.
  function renderSearchablePending() {
    const summary = el("div", "tool-summary");
    summary.setAttribute("aria-busy", "true");
    const number = el("span", "tool-summary-number");
    number.append(skeletonText("0.9em"));
    const text = el("p", "tool-summary-text");
    text.append(skeletonText("94%"), document.createElement("br"), skeletonText("58%"));
    summary.append(number, text);
    const language = el("div", "tool-row-line");
    const value = el("span");
    value.append(el("b", "", "English"), document.createTextNode(" · read on this computer"));
    language.append(el("span", "", "Language"), value);
    bodyEl.append(
      summary,
      radios([{ value: "all", label: "All pages without text", metaPending: "4em" }], "all", () => {}, "Pages"),
      language,
      destinationBlock()
    );
    const notes = notesBlock();
    if (notes) bodyEl.append(notes);
    footerButton("Cancel", close);
    footerButton("Make searchable", start, { primary: true, disabled: true });
  }

  function renderSearchableSetup() {
    const { textless, pages } = s.info;
    if (textless === null || textless === undefined) {
      renderSearchablePending();
      return;
    }
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
        (value) => { s.format = value; s.formatPicked = true; rerender(); },
        "Format"
      ),
      el("span", "tool-hint", format.hint)
    );
    const ppiHint = el("span", "tool-hint");
    if (s.info.page_size) {
      const [w, h] = pixelSize(s.info.page_size, s.ppi);
      ppiHint.textContent = `${w} × ${h} px a page`;
    } else {
      ppiHint.append(skeletonText("8em"));
    }
    const ppiControl = el("div", "tool-control");
    ppiControl.append(
      segmented(
        EXPORT_PPI.map((ppi) => ({ value: ppi, label: `${ppi} ppi` })),
        s.ppi,
        (value) => { s.ppi = value; s.formatPicked = true; rerender(); },
        "Resolution"
      ),
      ppiHint
    );
    const pageOptions = [
      s.info.pages
        ? { value: "all", label: "All pages", meta: plural(s.info.pages, "image", "images") }
        : { value: "all", label: "All pages", metaPending: "4.5em" },
    ];
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
    const primaryLabel = s.info.pending ? "Export images" : `Export ${plural(n, "image", "images")}`;
    footerButton(primaryLabel, start, { primary: true, disabled: !setupReady() });
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
    // Checked against the page count, so once it is known (`adoptInfo`).
    if (!s || s.tool !== "export" || s.info.pending) return;
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
    wrap.querySelector(".job-progress-pct").textContent = fileJobProgressLabel(event, fraction);
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
    if (!s.compress) descEl.textContent = "Done. The original is unchanged.";
    if (outcome.state === "failed") {
      if (!s.compress) descEl.textContent = "Nothing was written.";
      bodyEl.append(el("p", "tool-result", outcome.error));
      footerButton("Done", close, { primary: true });
      return;
    }
    if (s.compress) {
      CompressPanel.renderResult(s);
      return;
    }
    const r = outcome.result || {};
    const result = el("div", "tool-result");
    let path = r.path || null;
    let canOpen = Boolean(path) && s.tool !== "export";
    if (s.tool === "searchable") {
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
    if (s.compress) Object.assign(options, CompressPanel.jobOptions(s));
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
    const replace = await stacked(() => ReplaceDialog.open({ name: target.name, folder: target.folder }));
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
      CompressPanel.closed(s);
      s = null;
      return;
    }
    if (event.state === "cancelled") {
      s.phase = "setup";
      s.cancelling = false;
      s.event = null;
      if (s.compress) CompressPanel.afterCancel(s);
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
    if (!scrim || scrim.hidden || scrim.inert || !(s || steps)) return;
    // The Save-As or password dialog may be up over this one.
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#toolDialog)")) return;
    if (choosing()) {
      // Esc on any step closes the whole dialog (once a PDF being opened has
      // landed); Enter is Continue.
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        steps.cancel();
      } else {
        steps.handleChooserKey(ev);
      }
      return;
    }
    // The "Save in" menu takes its own keys (Esc closes only the menu).
    if (s.destination && s.destination.isMenuOpen()) return;
    // Alt+← is Back, where Back is shown.
    if (isStepBackKey(ev) && backShown()) {
      ev.preventDefault();
      steps.back();
      return;
    }
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

  // The option cards a spoken number picks: Compress's Quality cards, Make
  // searchable's and Export's Pages. (Nothing in Compress's Size limit: its
  // pills are named by size, not numbered.)
  function pickNumbered(number) {
    const group = bodyEl.querySelector(".compress-levels, .tool-options, .tool-radios");
    const item = group && group.children[number - 1];
    if (item) item.click();
  }

  function handleCommand(command) {
    if (choosing()) {
      steps.handleChooserCommand(command);
      return;
    }
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
    // On its "choose a PDF" step (tests, and Home's voice routing).
    isChoosing: choosing,
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
      browse = options.browse || browse;
    },
  };
})();
