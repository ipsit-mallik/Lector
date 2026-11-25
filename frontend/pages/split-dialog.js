// Split (docs/PRD.md § "Post-v1: file tools" → Tier 2), from Home: one PDF
// into several new ones, "Every N pages" or by page ranges, written into a
// new folder ("<name> (split)") as "<name> (part 1).pdf" and so on. One
// range makes one new PDF instead ("<name> (pages 1–6).pdf", saved as a
// file, not a folder). The original is left as it is, unless the reader
// ticks "Move the original to the Recycle Bin afterwards": Python then moves
// it there (never a permanent delete) once every new file is written and
// checked. Set up → Working → Result in one card, like the other tools; from
// the Tools page "choose a PDF" is the card's first step (js/dialog-steps.js),
// with Back in Set up; a Recent row's "Split…" opens Set up straight away.
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
  let browse = null; // home.js: the Open dialog, () => Promise<path | null>
  let openOutput = null; // home.js: open a PDF the job wrote
  let onOriginalMoved = () => {}; // home.js: the Recent list lost an entry
  // Opened from the Tools page: "choose a PDF" and Split as two steps of
  // this one card (js/dialog-steps.js); null otherwise.
  let steps = null;
  let titleEl = null;
  let badgeEl = null;

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
  // Which entry into the Split step answers are for (`fill`).
  let entryToken = 0;
  // What Python last said about the preview and the bin, for a step drawn
  // before it has said so about this PDF: how many files the preview names
  // (naming.py's SPLIT_PREVIEW_NAMED) and what the bin is called.
  let lastPreviewNamed = 3;
  let lastTrashName =
    typeof navigator !== "undefined" && /Mac/i.test(navigator.platform || "") ? "Trash" : "Recycle Bin";

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
    badgeEl = el("div", "dialog-badge");
    const text = el("div");
    titleEl = el("p", "dialog-title", "Split PDF");
    titleEl.id = "splitDialogTitle";
    descEl = el("p", "dialog-desc");
    descEl.id = "splitDialogDesc";
    text.append(titleEl, descEl);
    header.append(badgeEl, text);
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
  }

  function isOpen() {
    return Boolean(scrim) && !scrim.hidden;
  }

  // ---- Opening and closing ---------------------------------------------------

  // What the dialog needs to know about the PDF; a protected one asks for
  // its password first (held in Python's memory only, for this file's job).
  // What Python has been asked about each PDF since the dialog opened (a
  // quick look, a few milliseconds, never behind the worker), by path:
  // asked as soon as a PDF is chosen in the "choose a PDF" list.
  let infos = new Map();
  // The answers already in, so a step for a PDF whose look has arrived is
  // drawn complete at once.
  let known = new Map();

  function requestInfo(path) {
    if (!infos.has(path)) {
      const asked = Promise.resolve(callApi("file_tool_info", "split", path)).catch((err) => {
        console.error("split-dialog: couldn't read the PDF", err);
        return { ok: false, error: "Couldn't read the PDF. It may be damaged or no longer there." };
      });
      infos.set(path, asked);
      asked.then((info) => {
        if (infos.get(path) !== asked) return;
        if (info.ok) known.set(path, info);
        else if (info.reason !== "password") infos.delete(path);
      });
    }
    return infos.get(path);
  }

  function forgetAnswers() {
    infos = new Map();
    known = new Map();
  }

  // A protected PDF's password is asked over this dialog when it is open.
  function askOver(task) {
    return scrim && !scrim.hidden ? stacked(task) : task();
  }

  async function fetchInfo(path, wanted) {
    const info = await requestInfo(path);
    if (wanted && !wanted()) return { ok: false, cancelled: true };
    if (info.ok || (info.reason !== "password" && info.reason !== "wrong_password")) return info;
    const unlocked = await askOver(() => PasswordDialog.ask({
      name: info.name || baseName(path),
      attempt: async (password) => {
        const retry = await callApi("file_tool_info", "split", path, password);
        if (retry.reason === "wrong_password") return { error: retry.error };
        return { done: retry };
      },
    }));
    if (unlocked && unlocked.ok) {
      infos.set(path, Promise.resolve(unlocked));
      known.set(path, unlocked);
    }
    return unlocked || { ok: false, cancelled: true };
  }

  // What the dialog needs to know about `path`, or null once the reason it
  // can't be had has been said.
  // `wanted()`, when given: whether anyone still waits for the answer; if
  // not, no password is asked and nothing is said.
  async function readInfo(path, { wanted = null } = {}) {
    let info;
    try {
      info = await fetchInfo(path, wanted);
    } catch (err) {
      console.error("split-dialog: couldn't read the PDF", err);
      info = { ok: false, error: "Couldn't read the PDF. It may be damaged or no longer there." };
    }
    if (info.ok) return info;
    if (info.cancelled) return null;
    if (info.reason === "missing" && onMissing) onMissing(info.error, path);
    else showToast(info.error);
    return null;
  }

  // The dialog's state for `path`. Coming Back and choosing another PDF
  // keeps how it splits (Every or Page ranges) and starts the rest afresh:
  // the numbers, the name, the Recycle Bin box (never remembered).
  function freshState(path, info, previous) {
    const folderDest = makeDestination(path, info);
    if (info.pending) folderDest.setPending(true);
    return {
      phase: "setup",
      source: path,
      info,
      mode: previous ? previous.mode : "every",
      every: info.split_every ? String(info.split_every) : "",
      ranges: "",
      preview: null,
      pending: true,
      destination: folderDest,
      folderDest,
      fileDest: null,
      trash: false,
      starting: false,
      jobId: null,
      event: null,
      outcome: null,
      cancelling: false,
    };
  }

  // Split `path`; with `choose` (Home's Tools page) it first asks which PDF,
  // in the same card.
  // A second open while the first is still reading (a double click on a
  // Tools card, before the dialog shows) is ignored: it would show the dialog
  // twice, with two focus traps.
  let opening = false;
  async function open(path, options = {}) {
    if (opening) return;
    opening = true;
    try {
      await openNow(path, options);
    } finally {
      opening = false;
    }
  }

  async function openNow(path, { choose = false } = {}) {
    build();
    if (s && s.phase === "working") {
      show();
      return;
    }
    if (choose) {
      await openAtChooser();
      return;
    }
    forgetAnswers();
    const info = await readInfo(path);
    if (!info) return;
    steps = null;
    s = freshState(path, info, null);
    show();
    askPreview();
  }

  // The step for a PDF chosen in the list, before Python has said anything
  // about it: its name and (from the list) page count; the rest are
  // placeholders (`pending`) until the quick look lands (`fill`).
  function provisionalInfo(path, entry) {
    const pages = entry && entry.page_count ? entry.page_count : null;
    return {
      ok: true,
      pending: true,
      name: baseName(path),
      pages,
      split_every: null,
      preview_named: lastPreviewNamed,
      trash_name: lastTrashName,
      output: { folder: dirName(path), stem: "", extension: "" },
    };
  }

  // Fill the step in once the quick look has come: the fields, the header
  // and the name in place, then the preview (into the room kept for it).
  // Each entry into the step has its own answers: one that lands after Back
  // (or after another entry) is kept for next time but draws nothing, and
  // asks no password.
  async function fill(state) {
    const token = ++entryToken;
    const here = () => s === state && token === entryToken && !choosing() && isOpen();
    if (!state.info.pending) {
      if (state.pending) askPreview();
      return;
    }
    const info = await readInfo(state.source, { wanted: here });
    if (!here()) return;
    if (!info) {
      s = null;
      if (steps && !steps.isChoosing()) steps.back();
      return;
    }
    lastPreviewNamed = info.preview_named || lastPreviewNamed;
    lastTrashName = info.trash_name || lastTrashName;
    state.info = info;
    if (!state.every) state.every = String(info.split_every);
    state.folderDest.setSuggestedStem(info.output.stem);
    state.folderDest.setPending(false);
    const hadFocus = card.contains(document.activeElement);
    changeDialogContent(card, renderToolStep);
    if (hadFocus) focusFirst();
    askPreview();
  }

  async function openAtChooser() {
    s = null;
    forgetAnswers();
    steps = createToolSteps({
      card,
      badge: badgeEl,
      titleEl,
      descEl,
      bodyEl,
      footerEl,
      toolName: "Split",
      browse: () => (browse ? browse() : Promise.resolve(null)),
      stacked,
      prefetch: (path) => requestInfo(path),
      // At once: the PDF chosen before keeps everything; another starts
      // from what the list knows (its page count), the rest on its way.
      enterTool: (path, entry) => {
        if (s && s.source === path) return;
        clearTimeout(previewTimer);
        requestInfo(path);
        s = freshState(path, known.get(path) || provisionalInfo(path, entry), s);
      },
      renderTool: renderToolStep,
      entered: () => {
        if (s) fill(s);
      },
      // Nothing of Split's runs in the worker: Back has nothing to stop.
      leaveTool: () => {
        entryToken += 1;
      },
      focusTool: focusFirst,
      toolTitle: () => "Split PDF",
      currentSource: () => (s ? s.source : null),
      close,
    });
    await steps.begin();
    show();
  }

  function choosing() {
    return Boolean(steps) && steps.isChoosing();
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

  // One range makes one PDF: a Name with ".pdf", following the range
  // ("<name> (pages 1–6)") until the reader types.
  function makeFileDestination(single) {
    const source = s.source;
    return createSaveDestination({
      kind: "file",
      extension: ".pdf",
      // Python's name (an earlier "(pages …)" replaced, "(2)" if taken).
      stem: single.stem || `${stemOf(s.info.name)} (${single.label})`,
      originFolder: s.info.output.folder,
      originLabel: "Beside the original",
      suggestName: async (folder) => {
        const one = s && s.preview && s.preview.single;
        const range = one ? [one.first, one.last] : [single.first, single.last];
        return (await callApi("suggest_tool_name", "split_one", source, folder, range)).stem;
      },
      pickFolder: chooseFolder,
      onChange: updatePrimary,
    });
  }

  // The destination block for what the entry makes now: a folder for two
  // files or more, a file for one. Each keeps what was typed in it.
  function syncDestination() {
    const single = s.preview && !s.preview.error ? s.preview.single : null;
    let next = s.folderDest;
    if (single) {
      if (!s.fileDest) s.fileDest = makeFileDestination(single);
      else s.fileDest.refreshName();
      next = s.fileDest;
    }
    if (next === s.destination) return;
    const previous = s.destination;
    previous.closeMenu();
    s.destination = next;
    if (previous.element.isConnected) {
      previous.element.replaceWith(next.element);
      // A block made since the card was drawn has its icons still to mount.
      mountIcons(next.element);
    }
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
    clearTimeout(previewTimer);
    forgetAnswers();
    // A protected PDF's password was held for this dialog only.
    Promise.resolve(callApi("forget_file_tool_passwords")).catch((err) =>
      console.error("split-dialog: couldn't forget the passwords", err)
    );
    s = null;
    ui = null;
    steps = null;
    if (opener && opener.isConnected && opener.focus) opener.focus();
    opener = null;
  }

  function focusFirst() {
    if (choosing()) {
      steps.focusChooser();
      return;
    }
    const target = (s.phase === "setup" && ui && ui.picks[s.mode]) || primaryBtn || footerEl.querySelector("button");
    if (target) target.focus();
    else card.focus();
  }

  async function chooseFolder(start) {
    if (!saveAs) return null;
    const picked = await stacked(() => saveAs.open({ dir: start, pickFolder: true, title: "Choose a folder" }));
    return picked && !picked.cancelled ? picked.path : null;
  }

  // Another dialog (the folder picker, "Replace?", a password) over this
  // one, which stays as it is underneath (js/dialog-motion.js).
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
      // Asked once the page count is known (`fill`).
      if (!s || s.phase !== "setup" || s.info.pending) return;
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
      syncDestination();
      updatePreview();
    }, delay);
  }

  function setupReady() {
    return Boolean(s) && s.phase === "setup" && !s.starting && !s.pending && Boolean(s.preview) &&
      !s.preview.error && s.preview.count >= 1 && s.destination.isValid();
  }

  // "Save pages 1–6 as a new PDF" for one range; "Split into 3 files" for more.
  function primaryLabel() {
    const p = s.preview;
    if (!p || p.error || !p.count) return "Split";
    if (p.single) return `Save ${p.single.label} as a new PDF`;
    return `Split into ${plural(p.count, "file", "files")}`;
  }

  function isSingle() {
    return Boolean(s && s.preview && !s.preview.error && s.preview.single);
  }

  // The muted line under the Recycle Bin box: what will happen, either way.
  function trashOutcome() {
    if (!s.trash) return "The original stays as it is.";
    const saved = isSingle() ? "the new file is saved" : "the new files are saved";
    return `${s.info.name} will be moved to the ${s.info.trash_name} once ${saved}. You can restore it from there.`;
  }

  function headerText() {
    const facts = `${s.info.name} · ${plural(s.info.pages, "page", "pages")}.`;
    return s.trash ? `${facts} Makes new files.` : `${facts} Makes new files; the original stays as it is.`;
  }

  // The header, with a placeholder for the page count while it isn't known.
  function paintHeader() {
    if (s.info.pages) {
      descEl.textContent = headerText();
      return;
    }
    const tail = s.trash ? " Makes new files." : " Makes new files; the original stays as it is.";
    descEl.replaceChildren(
      document.createTextNode(`${s.info.name} · `),
      skeletonText("4em"),
      document.createTextNode(`.${tail}`)
    );
  }

  function updatePrimary() {
    if (!s || s.phase !== "setup" || !primaryBtn) return;
    primaryBtn.disabled = !setupReady();
    primaryBtn.textContent = primaryLabel();
  }

  // ---- Rendering ------------------------------------------------------------

  function render() {
    if (choosing()) steps.render();
    else renderToolStep();
  }

  function renderToolStep() {
    badgeEl.replaceChildren(iconEl("split"));
    titleEl.textContent = "Split PDF";
    bodyEl.replaceChildren();
    footerEl.replaceChildren();
    primaryBtn = null;
    ui = null;
    paintHeader();
    if (s.phase === "setup") renderSetup();
    else if (s.phase === "working") renderWorking();
    else renderResult();
    if (backShown()) footerEl.prepend(steps.backButton());
    mountIcons(card);
  }

  function backShown() {
    return Boolean(steps) && !steps.isChoosing() && Boolean(s) && s.phase === "setup";
  }

  // "Move the original to the Recycle Bin afterwards", unticked every time,
  // and the line under it saying what will happen.
  function trashBlock() {
    const row = el("label", "checkbox-row split-trash");
    const box = el("input");
    box.type = "checkbox";
    box.id = "splitTrashOriginal";
    box.checked = s.trash;
    box.setAttribute("aria-describedby", "splitTrashOutcome");
    row.append(box, document.createTextNode(`Move the original to the ${s.info.trash_name} afterwards`));
    const outcome = el("p", "split-trash-outcome", trashOutcome());
    outcome.id = "splitTrashOutcome";
    outcome.setAttribute("aria-live", "polite");
    box.addEventListener("change", () => {
      s.trash = box.checked;
      outcome.textContent = trashOutcome();
      paintHeader();
    });
    ui.trashOutcome = outcome;
    return [row, outcome];
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
    // Alt+← is Back, not "the other way".
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
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
    ui = { cards: {}, picks: {}, fields: {}, error: null, preview: null, trashOutcome: null };
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
    bodyEl.append(group, ui.error, ui.preview, s.destination.element, ...trashBlock());
    footerButton("Cancel", close);
    footerButton(primaryLabel(), start, { primary: true, disabled: !setupReady() });
    syncChoice();
  }

  // The preview's first answer still to come: its lines as placeholders,
  // as many as it will have. "Every N pages" with the page count known
  // makes ceil(pages / N) files, of which the preview names the first few
  // (Python's SPLIT_PREVIEW_NAMED, `preview_named`) and says how many more;
  // before the count is known, the most it can name, and the "more" line.
  function previewPlaceholder(box) {
    box.setAttribute("aria-busy", "true");
    const named = s.info.preview_named || lastPreviewNamed;
    const every = Number(s.every);
    let rows = named;
    let more = true;
    if (s.info.pages && s.mode === "every" && Number.isInteger(every) && every >= 1 && every < s.info.pages) {
      const count = Math.ceil(s.info.pages / every);
      rows = Math.min(count, named);
      more = count > named;
    }
    const count = el("span", "split-preview-count");
    count.append(skeletonText("3.5em"));
    box.appendChild(count);
    for (let i = 0; i < rows; i++) {
      const row = el("div", "split-preview-row");
      const name = el("span", "split-preview-name");
      name.append(skeletonText("11em"));
      const pages = el("span", "split-preview-pages");
      pages.append(skeletonText("4.5em"));
      row.append(name, pages);
      box.appendChild(row);
    }
    if (more) {
      const line = el("span", "split-preview-more");
      line.append(skeletonText("5em"));
      box.appendChild(line);
    }
  }

  // The preview box, the error under the chosen field, and the button.
  function updatePreview() {
    if (ui && ui.preview) ui.preview.removeAttribute("aria-busy");
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
    if (ui.trashOutcome) ui.trashOutcome.textContent = trashOutcome();
    const box = ui.preview;
    box.replaceChildren();
    if (!p) {
      previewPlaceholder(box);
      updatePrimary();
      return;
    }
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
    const original = r.original || null;
    descEl.textContent = original && original.moved ? "Done." : "Done. The original is unchanged.";
    const result = el("div", "tool-result");
    const line = el("p");
    if (r.single) {
      line.append(el("b", "", `Saved ${r.name}.`), document.createTextNode(` ${placeSentence(r.where)}`));
    } else {
      line.append(
        el("b", "", `Made ${plural(r.files, "file", "files")}.`),
        document.createTextNode(` In the folder “${r.name}”${savePlaceClause(r.where)}.`)
      );
    }
    result.appendChild(line);
    if (original) result.appendChild(el("p", "split-result-original", originalSentence(original, r.single)));
    bodyEl.appendChild(result);
    footerButton("Show in folder", () => showFileJobOutput(r.path));
    if (r.single && openOutput) {
      footerButton("Open", () => {
        close();
        openOutput(r.path);
      });
    }
    footerButton("Done", close, { primary: true });
  }

  // "Beside the original." / "In Documents." / "On the Desktop."
  function placeSentence(where) {
    const text = where || "beside the original";
    return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
  }

  function originalSentence(original, single) {
    if (original.moved) return `The original was moved to the ${original.trash_name}.`;
    const saved = single ? "Your new file is saved." : "Your new files are saved.";
    return `${saved} The original couldn't be moved to the ${original.trash_name}: ${original.error}`;
  }

  // ---- Actions --------------------------------------------------------------

  // A folder already there is never written into: the job takes the next
  // free name, as Export does, so only a wrong name or folder stops here.
  async function confirmDestination() {
    const { folder, name } = s.destination.value();
    const single = isSingle();
    const target = await callApi("check_tool_destination", single ? "split_one" : "split", s.source, folder, name);
    if (!s || s.phase !== "setup") return false;
    if (!target.ok) {
      s.destination.showError(target.field, target.error);
      return false;
    }
    // One new PDF over a file already there: asked first, as every tool does.
    if (!single || !target.exists) return true;
    const replace = await stacked(() => ReplaceDialog.open({ name: target.name, folder: target.folder }));
    return Boolean(replace) && Boolean(s) && s.phase === "setup";
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
          trash: s.trash,
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
      const result = event.result || {};
      if (result.original && result.original.moved) onOriginalMoved(s.source);
    }
    if (isOpen()) {
      render();
      focusFirst();
      if (s.phase === "setup") askPreview();
    }
  }

  // ---- Keys and voice -------------------------------------------------------

  function onKeydown(ev) {
    if (!isOpen() || scrim.inert || !(s || steps)) return;
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#splitDialog)")) return;
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
    if (s.destination && s.destination.isMenuOpen()) return;
    if (isStepBackKey(ev) && backShown()) {
      ev.preventDefault();
      steps.back();
      return;
    }
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
    if (choosing()) {
      steps.handleChooserCommand(command);
      return;
    }
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
    isChoosing: choosing,
    handleCommand,
    // Home's wiring: the file browser "Choose another folder…" uses, and
    // what a Recent PDF that has gone offers.
    configure(options) {
      saveAs = options.saveAs || saveAs;
      onMissing = options.onMissing || onMissing;
      browse = options.browse || browse;
      openOutput = options.openOutput || openOutput;
      onOriginalMoved = options.onOriginalMoved || onOriginalMoved;
    },
  };
})();
