// Home's Tools page (docs/PRD.md § "Post-v1: file tools"): every file tool
// as a card, in sections: Tier 1's "Shrink and convert" and Tier 2's "Pages".
//
// Compress, Make searchable, Export, Organise pages and Split work on one
// PDF, so they first ask which ("<Tool> — choose a PDF": the five most
// recent, or Browse). For the tools with a dialog that is the dialog's first
// step (js/dialog-steps.js), so the dim never lifts between the two;
// Organise pages has a dialog of its own here, since its next step is the
// reader. Convert to PDF and Combine files open their own dialogs straight
// away. home.js owns what a card does (`buildToolsPage(onTool)`) and the
// Open dialog Browse uses.
//
// Built with DOM calls and textContent: file names come from the reader's
// disk.
"use strict";

const TOOL_SECTIONS = [
  {
    id: "shrink",
    title: "Shrink and convert",
    subtitle: "Each makes a new file beside the original. Nothing is overwritten.",
    tools: [
      { id: "compress", name: "Compress", icon: "compress", desc: "Make a PDF smaller, mostly by shrinking its images." },
      {
        id: "searchable",
        name: "Make searchable",
        icon: "searchable",
        desc: "Add text to scanned pages so they can be searched.",
      },
      { id: "export", name: "Export as images", icon: "export_images", desc: "Save pages as PNG or JPEG files." },
      {
        id: "convert",
        name: "Convert to PDF",
        icon: "convert",
        desc: "Turn images or an e-book (EPUB, XPS, CBZ) into a PDF.",
      },
    ],
  },
  {
    id: "pages",
    title: "Pages",
    subtitle: "Rearrange whole pages and files. What is on each page never changes.",
    tools: [
      {
        id: "organise",
        name: "Organise pages",
        icon: "organise",
        desc: "Rotate, delete, reorder, extract or insert pages.",
      },
      { id: "combine", name: "Combine files", icon: "combine", desc: "Join several PDFs and images into one PDF." },
      { id: "split", name: "Split", icon: "split", desc: "Break a PDF into several, by page ranges or every N pages." },
    ],
  },
];

const TOOL_NAMES = {
  compress: "Compress",
  searchable: "Make searchable",
  export: "Export as images",
  organise: "Organise pages",
  split: "Split",
};

// How many recent PDFs the "choose a PDF" step offers.
const PICK_RECENT_COUNT = 5;

function toolsEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toolsIcon(name, className) {
  const span = toolsEl("span", className);
  span.dataset.icon = name;
  span.setAttribute("aria-hidden", "true");
  return span;
}

// The page's content: a section per entry in TOOL_SECTIONS, its cards real
// buttons. `onTool(id)` runs when one is chosen.
function buildToolsPage(onTool) {
  const page = toolsEl("div", "tools-page");
  for (const section of TOOL_SECTIONS) {
    const wrap = toolsEl("section", "tools-section");
    wrap.setAttribute("aria-labelledby", `toolsSection-${section.id}`);
    const title = toolsEl("h2", "tools-section-title", section.title);
    title.id = `toolsSection-${section.id}`;
    const grid = toolsEl("div", "tool-cards");
    for (const tool of section.tools) {
      const card = toolsEl("button", "tool-card card-lift");
      card.type = "button";
      card.dataset.tool = tool.id;
      card.setAttribute("aria-describedby", `toolDesc-${tool.id}`);
      const desc = toolsEl("span", "tool-card-desc", tool.desc);
      desc.id = `toolDesc-${tool.id}`;
      card.append(toolsIcon(tool.icon, "tool-card-icon"), toolsEl("span", "tool-card-name", tool.name), desc);
      card.addEventListener("click", () => onTool(tool.id));
      grid.appendChild(card);
    }
    wrap.append(title, toolsEl("p", "tools-section-sub", section.subtitle), grid);
    page.appendChild(wrap);
  }
  return page;
}

// "<Tool> — choose a PDF": the five most recent PDFs as one-choice rows (the
// first chosen), and Browse for a PDF…, which opens the in-app Open dialog.
// Drawn into a dialog's card by whoever holds it: the tool dialogs' first
// step (js/dialog-steps.js), or Organise pages' picker below. A PDF browsed
// for is kept as an extra first row, so coming Back to this step shows it
// chosen.
// `onSelect(path)` hears every PDF the reader chooses in the list (the first
// row, chosen from the start, included), so the tool can start reading it
// before Continue.
function createPdfChooser({ toolName, browse, onSelect = () => {} }) {
  let entries = [];
  let chosen = 0;
  let busy = false;
  let thumbnailsAsked = false;
  let handlers = { onContinue: () => {}, onCancel: () => {} };
  let continueBtn = null;
  const listEl = toolsEl("div", "pick-list");
  listEl.setAttribute("aria-label", "Recent PDFs");
  // Once, for the list's whole life: it is drawn again on every Back and
  // Browse, and a second call would add a second arrow-key handler (each
  // press then moved two rows). Its observer keeps the radios in step with
  // the rows drawn later.
  initRadioCards(listEl, { selectOnArrow: true });
  const browseBtn = toolsEl("button", "btn btn-secondary pick-browse");
  browseBtn.type = "button";
  browseBtn.append(toolsIcon("folder"), document.createTextNode("Browse for a PDF…"));
  browseBtn.addEventListener("click", browseForPdf);

  async function load() {
    let recent = [];
    try {
      recent = await callApi("get_recent_files");
    } catch (err) {
      console.error("tools-page: recent files", err);
    }
    entries = recent.slice(0, PICK_RECENT_COUNT).map((e) => ({ ...e }));
    chosen = 0;
    thumbnailsAsked = false;
    if (entries[chosen]) onSelect(entries[chosen].path);
  }

  function samePath(a, b) {
    return String(a || "").toLowerCase() === String(b || "").toLowerCase();
  }

  // `path` chosen, added as the first row when it isn't one of them.
  function select(path) {
    if (!path) return;
    let index = entries.findIndex((e) => samePath(e.path, path));
    if (index < 0) {
      const file = String(path).split(/[\\/]/).pop();
      entries.unshift({ path, name: file.replace(/\.pdf$/i, ""), page_count: null, thumbnail: null });
      index = 0;
    }
    chosen = index;
    if (listEl.isConnected) renderRows();
  }

  function render({ badge, titleEl, descEl, bodyEl, footerEl, onContinue, onCancel }) {
    handlers = { onContinue, onCancel };
    badge.replaceChildren(toolsIcon("folder"));
    titleEl.textContent = `${toolName} — choose a PDF`;
    descEl.textContent = "Pick one of your recent PDFs, or browse for another.";
    bodyEl.replaceChildren(toolsEl("span", "tool-section-label", "Recent"), listEl, browseBtn);
    renderRows();
    footerEl.replaceChildren();
    const cancel = toolsEl("button", "btn btn-secondary", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => handlers.onCancel());
    continueBtn = toolsEl("button", "btn", "Continue");
    continueBtn.type = "button";
    continueBtn.addEventListener("click", continueWithChosen);
    footerEl.append(cancel, continueBtn);
    setBusy(busy);
    mountIcons(badge);
    mountIcons(bodyEl);
    if (!thumbnailsAsked) {
      thumbnailsAsked = true;
      fillThumbnails();
    }
  }

  function continueWithChosen() {
    if (busy || !entries[chosen]) return;
    handlers.onContinue(entries[chosen].path);
  }

  function rowThumb(entry) {
    const thumb = toolsEl("span", "pick-thumb");
    if (entry.thumbnail) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = `data:image/png;base64,${entry.thumbnail}`;
      thumb.appendChild(img);
    }
    return thumb;
  }

  function renderRows() {
    listEl.replaceChildren();
    if (!entries.length) {
      listEl.appendChild(toolsEl("p", "pick-empty", "No recent PDFs yet. Browse for one instead."));
    }
    entries.forEach((entry, i) => {
      const row = toolsEl("button", "pick-row");
      row.type = "button";
      row.classList.toggle("selected", i === chosen);
      const pages = entry.page_count ? `${entry.page_count} ${entry.page_count === 1 ? "page" : "pages"}` : "";
      row.append(rowThumb(entry), toolsEl("span", "pick-name", entry.name), toolsEl("span", "pick-meta", pages));
      row.addEventListener("click", () => {
        chosen = i;
        listEl.querySelectorAll(".pick-row").forEach((r, j) => r.classList.toggle("selected", j === chosen));
        onSelect(entry.path);
      });
      row.addEventListener("dblclick", () => {
        chosen = i;
        continueWithChosen();
      });
      listEl.appendChild(row);
    });
    if (continueBtn) continueBtn.disabled = busy || !entries.length;
  }

  // Thumbnails not rendered yet arrive after the rows, as on Recent.
  async function fillThumbnails() {
    const pending = entries.filter((e) => e.thumbnail_pending).map((e) => e.path);
    if (!pending.length) return;
    try {
      const pngs = await callApi("get_thumbnails", pending);
      const rows = Array.from(listEl.querySelectorAll(".pick-row"));
      entries.forEach((entry, i) => {
        if (!pngs[entry.path]) return;
        entry.thumbnail = pngs[entry.path];
        if (rows[i]) rows[i].querySelector(".pick-thumb").replaceWith(rowThumb(entry));
      });
    } catch (err) {
      console.error("tools-page: thumbnails", err);
    }
  }

  async function browseForPdf() {
    if (!browse || busy) return;
    const path = await browse();
    if (!path) return;
    select(path);
    handlers.onContinue(path);
  }

  // Continue says what it is doing while the tool reads the PDF.
  function setBusy(on) {
    busy = on;
    if (!continueBtn) return;
    continueBtn.disabled = on || !entries.length;
    continueBtn.textContent = on ? "Opening…" : "Continue";
  }

  // The chosen row (scrolled into view), else Continue.
  function focus() {
    const row = listEl.querySelector(".pick-row.selected");
    if (row) {
      row.focus();
      if (typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
    } else if (continueBtn && !continueBtn.disabled) {
      continueBtn.focus();
    } else {
      browseBtn.focus();
    }
  }

  // Enter is Continue, unless a button in the footer (or Browse) has focus:
  // a focused button presses itself.
  function handleKey(ev) {
    if (ev.key !== "Enter" || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return false;
    const active = document.activeElement;
    if (isTypingTarget(active) || active === browseBtn) return false;
    if (continueBtn && continueBtn.parentElement && continueBtn.parentElement.contains(active)) return false;
    ev.preventDefault();
    continueWithChosen();
    return true;
  }

  function handleCommand(command) {
    if (command.intent === "TOOL_CONFIRM") continueWithChosen();
    else if (command.intent === "CANCEL") handlers.onCancel();
    else if (command.intent === "DIALOG_PICK") {
      const row = listEl.querySelectorAll(".pick-row")[command.index - 1];
      if (row) row.click();
    }
  }

  return {
    load,
    render,
    select,
    focus,
    setBusy,
    handleKey,
    handleCommand,
    chosenPath: () => (entries[chosen] ? entries[chosen].path : null),
    // What the list knows about `path` (its page count), or null.
    entryFor: (path) => entries.find((e) => samePath(e.path, path)) || null,
  };
}

// Organise pages from the Tools page: the chooser in a dialog of its own,
// since its next step is the reader, not a dialog. Continue hands the path
// to `accept` (which opens it: a protected PDF asks for its password); once
// it is open the dialog fades out as a whole, so the dim doesn't drop out
// from under it, and `choose` resolves to the path. A PDF that couldn't be
// opened keeps the dialog. The shared dialog frame, with a voice scope of its
// own ("confirm", "cancel", a number).
const PdfPicker = (() => {
  let scrim = null;
  let card = null;
  let parts = null;
  let scope = null;
  let releaseTrap = null;
  let settle = null;
  let chooser = null;
  let accept = null;
  let browse = null; // home.js: () => Promise<path | null>

  function build() {
    if (scrim) return;
    scrim = toolsEl("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "pdfPickerScrim";
    scrim.hidden = true;
    card = toolsEl("div", "dialog-card dialog-card--tool");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "pdfPickerTitle");
    card.tabIndex = -1;
    const body = toolsEl("div", "dialog-body");
    const header = toolsEl("div", "dialog-header");
    const badge = toolsEl("div", "dialog-badge");
    const text = toolsEl("div");
    const titleEl = toolsEl("p", "dialog-title");
    titleEl.id = "pdfPickerTitle";
    const descEl = toolsEl("p", "dialog-desc");
    text.append(titleEl, descEl);
    header.append(badge, text);
    const bodyEl = toolsEl("div", "tool-body");
    body.append(header, bodyEl);
    const footerEl = toolsEl("div", "dialog-footer");
    card.append(body, footerEl);
    scrim.appendChild(card);
    document.body.appendChild(scrim);
    parts = { badge, titleEl, descEl, bodyEl, footerEl };
    smoothDialogHeight(card);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
  }

  function onKeydown(ev) {
    if (!scrim || scrim.hidden || scrim.inert || !chooser) return;
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#pdfPickerScrim)")) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      cancel();
      return;
    }
    chooser.handleKey(ev);
  }

  // The Open dialog (or a password) over this one, which stays where it is
  // (js/dialog-motion.js's `stackDialog`).
  function stacked(task) {
    return stackDialog(
      { scrim, onLeave: () => scope.leave(), onReturn: () => scope.enter(), refocus: () => chooser.focus() },
      task
    );
  }

  // While the PDF is being opened Cancel and Esc wait: it is the reader's
  // once open (with Home's notes left for it), so the dialog can't close
  // out from under it.
  let opening = false;

  function cancel() {
    if (!opening) close(null);
  }

  // Continue is one-shot: busy from the first click. A PDF that opened is
  // the reader's from then on: the dialog stays as it is ("Opening…", the
  // dim behind it) until the reader's first frame, which is Organise's
  // already, so there is never a bare or stale screen between them; Cancel
  // and Esc stay ignored. Only a PDF that couldn't be opened gives
  // Continue back. Its password, if it asks for one, comes over this one.
  async function onContinue(path) {
    if (opening) return;
    opening = true;
    chooser.setBusy(true);
    let opened = false;
    try {
      opened = await stacked(() => accept(path));
    } catch (err) {
      console.error("tools-page: couldn't open the PDF for Organise", err);
    }
    if (!opened || !settle) {
      opening = false;
      if (settle) chooser.setBusy(false);
      return;
    }
    const resolve = settle;
    settle = null;
    resolve(path);
  }

  function show() {
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    scope.enter();
    chooser.focus();
  }

  function hide() {
    if (scrim.hidden) return;
    hideDialogScrim(scrim);
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    scope.leave();
  }

  function close(path) {
    hide();
    const resolve = settle;
    settle = null;
    if (resolve) resolve(path);
  }

  // Ask which PDF `tool` is for and hand it to `accept` (Promise<bool>).
  // Resolves to the path once accepted, or null if cancelled.
  async function choose(tool, { accept: acceptFn } = {}) {
    build();
    // Still up from a PDF that opened (the reader hadn't taken over): put
    // it away first, so its focus trap and voice scope aren't doubled.
    if (!scrim.hidden) hide();
    accept = acceptFn || (async () => true);
    opening = false;
    chooser = createPdfChooser({ toolName: TOOL_NAMES[tool] || "Tool", browse: () => stacked(browse) });
    await chooser.load();
    chooser.render({ ...parts, onContinue, onCancel: cancel });
    show();
    return new Promise((resolve) => {
      settle = resolve;
    });
  }

  return {
    choose,
    handleCommand: (command) => {
      if (chooser) chooser.handleCommand(command);
    },
    isOpen: () => Boolean(scrim) && !scrim.hidden,
    configure({ browse: browseFn }) {
      browse = browseFn;
    },
  };
})();
