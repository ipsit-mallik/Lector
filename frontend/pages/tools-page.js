// Home's Tools page (docs/PRD.md § "Post-v1: file tools"): every file tool
// as a card, in sections: Tier 1's "Shrink and convert" and Tier 2's "Pages".
//
// Compress, Make searchable, Export, Organise pages and Split work on one
// PDF, so they first ask which ("<Tool> — choose a PDF": the five most
// recent, or Browse); Convert to PDF and Combine files open their own
// dialogs straight away. home.js owns what a card does
// (`buildToolsPage(onTool)`) and the Open dialog Browse uses.
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
// Continue hands the chosen path back. The shared dialog frame, with a voice
// scope of its own ("confirm", "cancel", a number).
const PdfPicker = (() => {
  let scrim = null;
  let card = null;
  let titleEl = null;
  let listEl = null;
  let continueBtn = null;
  let browseBtn = null;
  let scope = null;
  let releaseTrap = null;
  let settle = null;
  let entries = [];
  let chosen = 0;
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
    badge.dataset.icon = "folder";
    const text = toolsEl("div");
    titleEl = toolsEl("p", "dialog-title");
    titleEl.id = "pdfPickerTitle";
    text.append(titleEl, toolsEl("p", "dialog-desc", "Pick one of your recent PDFs, or browse for another."));
    header.append(badge, text);
    const content = toolsEl("div", "tool-body");
    listEl = toolsEl("div", "pick-list");
    listEl.setAttribute("aria-label", "Recent PDFs");
    browseBtn = toolsEl("button", "btn btn-secondary pick-browse");
    browseBtn.type = "button";
    browseBtn.append(toolsIcon("folder"), document.createTextNode("Browse for a PDF…"));
    browseBtn.addEventListener("click", browseForPdf);
    content.append(toolsEl("span", "tool-section-label", "Recent"), listEl, browseBtn);
    body.append(header, content);
    const footer = toolsEl("div", "dialog-footer");
    const cancel = toolsEl("button", "btn btn-secondary", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => close(null));
    continueBtn = toolsEl("button", "btn", "Continue");
    continueBtn.type = "button";
    continueBtn.addEventListener("click", () => {
      if (entries[chosen]) close(entries[chosen].path);
    });
    footer.append(cancel, continueBtn);
    card.append(body, footer);
    scrim.appendChild(card);
    document.body.appendChild(scrim);
    mountIcons(scrim);
    scope = createVoiceScope("file_tool");
    document.addEventListener("keydown", onKeydown);
  }

  function onKeydown(ev) {
    if (!scrim || scrim.hidden) return;
    if (document.querySelector(".dialog-scrim:not([hidden]):not(#pdfPickerScrim)")) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      close(null);
      return;
    }
    if (ev.key !== "Enter" || isTypingTarget(document.activeElement)) return;
    // A focused button presses itself.
    if (card.querySelector(".dialog-footer").contains(document.activeElement)) return;
    if (document.activeElement === browseBtn) return;
    ev.preventDefault();
    continueBtn.click();
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
      const thumb = toolsEl("span", "pick-thumb");
      if (entry.thumbnail) {
        const img = document.createElement("img");
        img.alt = "";
        img.src = `data:image/png;base64,${entry.thumbnail}`;
        thumb.appendChild(img);
      }
      const pages = entry.page_count ? `${entry.page_count} ${entry.page_count === 1 ? "page" : "pages"}` : "";
      row.append(thumb, toolsEl("span", "pick-name", entry.name), toolsEl("span", "pick-meta", pages));
      row.addEventListener("click", () => {
        chosen = i;
        listEl.querySelectorAll(".pick-row").forEach((r, j) => r.classList.toggle("selected", j === chosen));
      });
      row.addEventListener("dblclick", () => close(entry.path));
      listEl.appendChild(row);
    });
    continueBtn.disabled = !entries.length;
    initRadioCards(listEl, { selectOnArrow: true });
  }

  // Thumbnails not rendered yet arrive after the rows, as on Recent.
  async function fillThumbnails() {
    const pending = entries.filter((e) => e.thumbnail_pending).map((e) => e.path);
    if (!pending.length) return;
    try {
      const pngs = await callApi("get_thumbnails", pending);
      const rows = Array.from(listEl.querySelectorAll(".pick-row"));
      entries.forEach((entry, i) => {
        if (!pngs[entry.path] || !rows[i]) return;
        entry.thumbnail = pngs[entry.path];
        const img = document.createElement("img");
        img.alt = "";
        img.src = `data:image/png;base64,${entry.thumbnail}`;
        rows[i].querySelector(".pick-thumb").replaceChildren(img);
      });
    } catch (err) {
      console.error("tools-page: thumbnails", err);
    }
  }

  async function browseForPdf() {
    if (!browse) return;
    hide();
    const path = await browse();
    if (path) close(path);
    else show();
  }

  function show() {
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    scope.enter();
    (listEl.querySelector(".pick-row.selected") || continueBtn).focus();
  }

  function hide() {
    if (scrim.hidden) return;
    scrim.hidden = true;
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

  // Ask which PDF `tool` is for. Resolves to a path, or null if cancelled.
  async function choose(tool) {
    build();
    titleEl.textContent = `${TOOL_NAMES[tool] || "Tool"} — choose a PDF`;
    let recent = [];
    try {
      recent = await callApi("get_recent_files");
    } catch (err) {
      console.error("tools-page: recent files", err);
    }
    entries = recent.slice(0, PICK_RECENT_COUNT).map((e) => ({ ...e }));
    chosen = 0;
    renderRows();
    show();
    fillThumbnails();
    return new Promise((resolve) => {
      settle = resolve;
    });
  }

  function handleCommand(command) {
    if (command.intent === "TOOL_CONFIRM") continueBtn.click();
    else if (command.intent === "CANCEL") close(null);
    else if (command.intent === "DIALOG_PICK") {
      const row = listEl.querySelectorAll(".pick-row")[command.index - 1];
      if (row) row.click();
    }
  }

  return {
    choose,
    handleCommand,
    isOpen: () => Boolean(scrim) && !scrim.hidden,
    configure({ browse: browseFn }) {
      browse = browseFn;
    },
  };
})();
