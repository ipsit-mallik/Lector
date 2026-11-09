// The file tools' destination block (docs/PRD.md § "Post-v1: file tools" →
// "Rules every tool follows"; docs/DESIGN_SYSTEM.md § "Destination block"):
// a Name field with the extension fixed inside its right end, and a "Save in"
// button whose listbox offers Beside the original, RECENT, ON THIS PC and
// "Choose another folder…". One component for every tool that writes a file
// or a folder: Compress, Make searchable, Export as images and Convert to PDF
// now, Tier 2's Extract, Combine and Split later.
//
// A dialog makes one each time it opens (so it always starts on "Beside the
// original") and moves `element` back into its body on every re-render: the
// nodes persist, so the field keeps its text, caret and focus. Python checks
// everything again before a job runs (api.py's check_tool_destination and
// start_file_job): the page only ever sends a folder and a bare name.
//
// Keys: Enter, Space or ↑/↓ on "Save in" open the menu with focus on the
// chosen place; ↑/↓, Home and End move; Enter or Space picks; Esc and Tab
// close the menu only (never the dialog) and give focus back to the button.
// Voice: none of its own. The menu is mouse and keyboard; "Choose another
// folder…" opens the in-app folder picker, which is voice-driven.
//
// Built from DOM calls with textContent: folder names come from the disk.
"use strict";

// Characters Windows doesn't allow in a name (destinations.py's
// ILLEGAL_NAME_CHARS), and control characters.
const SAVE_DEST_ILLEGAL = /[\\/:*?"<>|\u0000-\u001f]/;

const SAVE_DEST_TEXT = {
  empty: "Type a name.",
  illegal: 'A name can\'t contain \\ / : * ? " < > |',
  unusable: "That name can't be used. Choose another.",
  folder: "Can't save in this folder. Choose another.",
};

// The sentence for a name that can't be used, or null: destinations.py's
// name_error, run as the reader types.
function saveDestNameError(stem) {
  const text = String(stem == null ? "" : stem);
  if (!text.trim()) return SAVE_DEST_TEXT.empty;
  if (SAVE_DEST_ILLEGAL.test(text)) return SAVE_DEST_TEXT.illegal;
  if (!text.trim().replace(/\./g, "")) return SAVE_DEST_TEXT.unusable;
  return null;
}

// Folder paths as Python sends them (OS-native): equal when only case
// (Windows) or a trailing separator differs.
function saveDestKey(path) {
  const text = String(path || "");
  const windows = text.includes("\\");
  const trimmed = /^[A-Za-z]:\\$/.test(text) || text === "/" ? text : text.replace(/[\\/]+$/, "");
  return windows ? trimmed.toLowerCase() : trimmed;
}

function saveDestSameFolder(a, b) {
  return Boolean(a) && Boolean(b) && saveDestKey(a) === saveDestKey(b);
}

// "D:\Reports" -> "Reports"; a drive's root is its own path.
function saveDestFolderName(path) {
  const text = String(path || "");
  const trimmed = /^[A-Za-z]:\\$/.test(text) || text === "/" ? text : text.replace(/[\\/]+$/, "");
  return trimmed.split(/[\\/]/).pop() || text;
}

let saveDestCount = 0;

// `kind` "file" (the extension shows at the field's end) or "folder" (Export,
// Split: labelled "Folder", no extension). `originLabel` is the first menu
// item ("Beside the original", or "Beside the first file" for Convert).
// `suggestName(folder)` resolves to the free name there, followed while the
// reader hasn't typed in the field; `pickFolder(start)` is the in-app folder
// picker, resolving to a folder or null; `onChange()` runs whenever the
// block's validity may have changed (the dialog then enables or disables its
// primary button).
function createSaveDestination({
  kind = "file",
  extension = ".pdf",
  stem = "",
  originFolder = "",
  originLabel = "Beside the original",
  hint = "",
  suggestName = null,
  pickFolder = null,
  onChange = () => {},
} = {}) {
  const id = `saveDest${++saveDestCount}`;
  const isFolderKind = kind === "folder";
  let origin = originFolder;
  let folder = originFolder;
  let atOrigin = true;
  let edited = false;
  let checking = 0; // writable checks in flight
  let folderError = null;
  let nameErrorFromCheck = null; // from Python at Start (e.g. "That's the original file")
  let menu = null;
  let menuRequest = 0;

  function node(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function icon(name, className) {
    const span = node("span", className);
    span.dataset.icon = name;
    span.setAttribute("aria-hidden", "true");
    return span;
  }

  // ---- The block ---------------------------------------------------------

  const root = node("div", "save-dest");
  const grid = node("div", "save-dest-grid");

  const nameLabel = node("label", "save-dest-label", isFolderKind ? "Folder" : "Name");
  nameLabel.htmlFor = `${id}Name`;
  const field = node("div", "save-dest-field");
  const input = node("input", "save-dest-input");
  input.type = "text";
  input.id = `${id}Name`;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.value = stem;
  field.appendChild(input);
  if (!isFolderKind && extension) {
    const ext = node("span", "save-dest-ext", extension);
    ext.id = `${id}Ext`;
    field.appendChild(ext);
  }
  const nameErrorEl = node("p", "dialog-field-error save-dest-error");
  nameErrorEl.id = `${id}NameError`;
  nameErrorEl.setAttribute("role", "alert");
  nameErrorEl.hidden = true;

  const placeLabel = node("span", "save-dest-label", "Save in");
  placeLabel.id = `${id}PlaceLabel`;
  const place = node("div", "save-dest-place");
  const button = node("button", "save-dest-button");
  button.type = "button";
  button.id = `${id}Button`;
  button.title = "Choose where to save";
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-labelledby", `${placeLabel.id} ${button.id}`);
  const folderNameEl = node("span", "save-dest-folder");
  const whereEl = node("span", "save-dest-where");
  button.append(icon("folder", "save-dest-icon"), folderNameEl, whereEl, icon("chevron_down", "save-dest-chevron"));
  place.appendChild(button);
  const folderErrorEl = node("p", "dialog-field-error save-dest-error");
  folderErrorEl.id = `${id}FolderError`;
  folderErrorEl.setAttribute("role", "alert");
  folderErrorEl.hidden = true;

  grid.append(nameLabel, field, nameErrorEl, placeLabel, place, folderErrorEl);
  const hintEl = node("p", "save-dest-hint");
  root.append(grid, hintEl);

  function nameError() {
    return nameErrorFromCheck || saveDestNameError(input.value);
  }

  function paint() {
    folderNameEl.textContent = saveDestFolderName(folder);
    whereEl.textContent = atOrigin ? originLabel.toLowerCase() : folder;
    button.title = atOrigin ? `${folder} (${originLabel.toLowerCase()})` : folder;
    const problem = nameError();
    nameErrorEl.textContent = problem || "";
    nameErrorEl.hidden = !problem;
    field.classList.toggle("is-invalid", Boolean(problem));
    if (problem) {
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", nameErrorEl.id);
    } else {
      input.removeAttribute("aria-invalid");
      if (!isFolderKind && extension) input.setAttribute("aria-describedby", `${id}Ext`);
      else input.removeAttribute("aria-describedby");
    }
    folderErrorEl.textContent = folderError || "";
    folderErrorEl.hidden = !folderError;
    button.classList.toggle("is-invalid", Boolean(folderError));
    if (folderError) button.setAttribute("aria-describedby", folderErrorEl.id);
    else button.removeAttribute("aria-describedby");
  }

  function setHint(text) {
    hintEl.textContent = text || "";
    hintEl.hidden = !text;
  }

  // ---- The chosen folder ---------------------------------------------------

  // Whether the chosen folder can be written to. One chosen from RECENT or
  // the folder picker goes to the top of RECENT once it passes: a folder
  // Lector can't save in takes no slot there.
  async function checkFolder({ remember = false } = {}) {
    const asked = folder;
    checking += 1;
    let usable = false;
    try {
      const answer = await callApi("check_output_folder", asked);
      usable = Boolean(answer && answer.ok);
    } catch (err) {
      console.error("save-destination: couldn't check the folder", err);
    } finally {
      checking -= 1;
    }
    if (saveDestSameFolder(asked, folder)) folderError = usable ? null : SAVE_DEST_TEXT.folder;
    if (usable && remember) {
      Promise.resolve(callApi("remember_output_folder", asked)).catch((err) =>
        console.error("save-destination: couldn't remember the folder", err)
      );
    }
    paint();
    onChange();
  }

  // The name follows the folder (the free name there) until the reader types.
  async function followName() {
    if (edited || !suggestName) return;
    const asked = folder;
    let suggested = null;
    try {
      suggested = await suggestName(asked);
    } catch (err) {
      console.error("save-destination: couldn't suggest a name", err);
    }
    if (edited || !suggested || !saveDestSameFolder(asked, folder)) return;
    input.value = suggested;
    nameErrorFromCheck = null;
    paint();
    onChange();
  }

  function choose(path, { remember = false } = {}) {
    folder = path;
    atOrigin = saveDestSameFolder(path, origin);
    folderError = null;
    nameErrorFromCheck = null;
    paint();
    onChange();
    checkFolder({ remember });
    followName();
  }

  // ---- The menu ------------------------------------------------------------

  function optionEl({ name, path, selected, iconName = null, onPick }) {
    const item = node("div", "save-dest-option");
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(Boolean(selected)));
    item.tabIndex = -1;
    const check = node("span", "save-dest-check");
    if (selected) check.appendChild(icon("check"));
    item.appendChild(check);
    if (iconName) item.appendChild(icon(iconName, "save-dest-option-icon"));
    const text = node("span", "save-dest-option-text");
    text.appendChild(node("span", "save-dest-option-name", name));
    if (path) text.appendChild(node("span", "save-dest-option-path", path));
    item.appendChild(text);
    item.title = path || name;
    item.addEventListener("click", onPick);
    return item;
  }

  function groupEl(key, title, entries) {
    const group = node("div", "save-dest-group");
    group.setAttribute("role", "group");
    const head = node("div", "save-dest-group-head", title);
    head.id = `${id}Group${key}`;
    head.setAttribute("role", "presentation");
    group.setAttribute("aria-labelledby", head.id);
    group.appendChild(head);
    for (const entry of entries) group.appendChild(entry);
    return group;
  }

  function buildMenu(places) {
    const list = node("div", "save-dest-menu");
    list.id = `${id}Menu`;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Save in");
    list.appendChild(
      optionEl({
        name: originLabel,
        path: origin,
        selected: atOrigin,
        onPick: () => pick(origin),
      })
    );
    const placeOption = (entry, remember) =>
      optionEl({
        name: entry.name,
        path: entry.path,
        selected: !atOrigin && saveDestSameFolder(entry.path, folder),
        onPick: () => pick(entry.path, { remember }),
      });
    if (places.recent && places.recent.length) {
      list.appendChild(groupEl("Recent", "Recent", places.recent.map((entry) => placeOption(entry, true))));
    }
    if (places.known && places.known.length) {
      list.appendChild(groupEl("Pc", "On this PC", places.known.map((entry) => placeOption(entry, false))));
    }
    const separator = node("div", "save-dest-separator");
    separator.setAttribute("role", "separator");
    list.appendChild(separator);
    list.appendChild(
      optionEl({ name: "Choose another folder…", path: "", selected: false, iconName: "folder", onPick: chooseOther })
    );
    list.addEventListener("keydown", onMenuKeydown);
    return list;
  }

  function options() {
    return menu ? Array.from(menu.querySelectorAll("[role='option']")) : [];
  }

  async function openMenu() {
    if (menu) return;
    const request = ++menuRequest;
    let places = { recent: [], known: [] };
    try {
      places = (await callApi("get_save_places", origin)) || places;
    } catch (err) {
      console.error("save-destination: couldn't list the places", err);
    }
    if (request !== menuRequest || menu || !root.isConnected) return;
    menu = buildMenu(places);
    place.appendChild(menu);
    if (typeof mountIcons === "function") mountIcons(menu);
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-controls", menu.id);
    fitMenu();
    document.addEventListener("pointerdown", onOutsidePointer, true);
    const items = options();
    const chosen = items.find((o) => o.getAttribute("aria-selected") === "true") || items[0];
    if (chosen) chosen.focus();
  }

  // Above the button there may be less room than the menu needs (a short
  // window): it then scrolls rather than leaving the screen.
  function fitMenu() {
    if (!menu || typeof button.getBoundingClientRect !== "function") return;
    const room = button.getBoundingClientRect().top - 6 - 8;
    if (room > 0 && menu.scrollHeight > room) menu.style.maxHeight = `${Math.floor(room)}px`;
  }

  function closeMenu({ focusButton = false } = {}) {
    menuRequest += 1;
    if (!menu) return;
    menu.remove();
    menu = null;
    button.setAttribute("aria-expanded", "false");
    button.removeAttribute("aria-controls");
    document.removeEventListener("pointerdown", onOutsidePointer, true);
    if (focusButton) button.focus();
  }

  function pick(path, { remember = false } = {}) {
    closeMenu({ focusButton: true });
    choose(path, { remember });
  }

  async function chooseOther() {
    closeMenu();
    if (!pickFolder) return;
    let picked = null;
    try {
      picked = await pickFolder(folder);
    } catch (err) {
      console.error("save-destination: the folder picker failed", err);
    }
    if (picked) choose(picked, { remember: true });
    if (root.isConnected) button.focus();
  }

  function onOutsidePointer(ev) {
    if (!place.contains(ev.target)) closeMenu();
  }

  function onMenuKeydown(ev) {
    const items = options();
    const at = items.indexOf(document.activeElement);
    let next = null;
    switch (ev.key) {
      case "ArrowDown":
        next = items[Math.min(items.length - 1, at + 1)];
        break;
      case "ArrowUp":
        next = items[Math.max(0, at - 1)];
        break;
      case "Home":
        next = items[0];
        break;
      case "End":
        next = items[items.length - 1];
        break;
      case "Enter":
      case " ":
        if (at >= 0) items[at].click();
        break;
      case "Escape":
      case "Tab":
        closeMenu({ focusButton: true });
        break;
      default:
        return;
    }
    // The dialog behind must not see these: Esc closes the menu only, and
    // Enter picks a place rather than pressing the dialog's primary button.
    ev.preventDefault();
    ev.stopPropagation();
    if (next) next.focus();
  }

  // ---- Wiring --------------------------------------------------------------

  button.addEventListener("click", () => {
    if (menu) closeMenu({ focusButton: true });
    else openMenu();
  });
  button.addEventListener("keydown", (ev) => {
    if (!["Enter", " ", "ArrowDown", "ArrowUp"].includes(ev.key)) return;
    ev.preventDefault();
    ev.stopPropagation();
    openMenu();
  });
  field.addEventListener("mousedown", (ev) => {
    if (ev.target !== input) {
      ev.preventDefault();
      input.focus();
    }
  });
  input.addEventListener("input", () => {
    edited = true;
    nameErrorFromCheck = null;
    paint();
    onChange();
  });

  setHint(hint);
  paint();
  checkFolder();

  return {
    element: root,
    // Whether the job may start: a usable name, and a folder known to be
    // writable (not while that is still being checked).
    isValid: () => !nameError() && !folderError && checking === 0,
    // What start_file_job and check_tool_destination are sent.
    value: () => ({ folder, name: input.value.trim() }),
    setHint,
    // Python's answer at Start: a sentence under the row it is about.
    showError(fieldName, message) {
      if (fieldName === "folder") folderError = message || SAVE_DEST_TEXT.folder;
      else nameErrorFromCheck = message || SAVE_DEST_TEXT.unusable;
      paint();
      onChange();
    },
    // Convert: the first file changed. "Beside the first file" follows it,
    // and so does the name until the reader has typed one.
    setOrigin(newOrigin) {
      const wasAtOrigin = atOrigin;
      origin = newOrigin;
      if (wasAtOrigin) {
        choose(newOrigin);
        return;
      }
      atOrigin = saveDestSameFolder(folder, origin);
      paint();
      followName();
    },
    isMenuOpen: () => Boolean(menu),
    closeMenu,
    focusButton: () => button.focus(),
  };
}
