// Recent list view: one table card (Name | Last active | Pages | Actions)
// instead of a card per PDF — docs/DESIGN_SYSTEM.md's "Recent list view".
// home.js owns the data and what each action does; this file only builds
// the table, sorts it, and runs the per-row "⋯" menu. The grid view is
// still home.js's own buildCard().
//
// The helpers at the top touch no DOM, so tests/test_recent_list_behavior.py
// can run them under Node without a browser.

// A name only gets the middle-ellipsis treatment once it is long enough to
// plausibly be cut off; anything shorter renders as one plain run of text.
const MIDDLE_ELLIPSIS_MIN_LENGTH = 30;
// The kept tail is the longest run of whole words, after a space, hyphen,
// underscore or dot, that fits in this many characters — long enough for
// "Mandate (highlighted)", short enough to leave the head room to show.
const MIDDLE_ELLIPSIS_MAX_TAIL = 24;
// When no word boundary falls inside that window (one long unbroken token),
// keep this many trailing characters instead.
const MIDDLE_ELLIPSIS_FALLBACK_TAIL = 12;
const NAME_SEPARATOR = /[\s\-_.]/;

// Splits a name into a head that CSS may truncate with an ellipsis and a
// tail that always stays visible, so long names that differ only at the end
// ("…Mandate (highlighted)" vs "…Mandate") can still be told apart.
function splitForMiddleEllipsis(text) {
  if (text.length <= MIDDLE_ELLIPSIS_MIN_LENGTH) return { head: text, tail: "" };
  const start = Math.max(1, text.length - MIDDLE_ELLIPSIS_MAX_TAIL - 1);
  for (let i = start; i < text.length - 1; i++) {
    if (NAME_SEPARATOR.test(text[i])) {
      return { head: text.slice(0, i + 1), tail: text.slice(i + 1) };
    }
  }
  const cut = text.length - MIDDLE_ELLIPSIS_FALLBACK_TAIL;
  return { head: text.slice(0, cut), tail: text.slice(cut) };
}

// `firstDirection` is what a column sorts by when its header is first
// clicked; clicking the active header again flips it. "Last active" starts
// newest first, since that is the question a Recent list answers.
const RECENT_SORTS = {
  name: {
    firstDirection: "asc",
    compare: (a, b) => a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" }),
  },
  lastActive: {
    firstDirection: "desc",
    // Naive ISO 8601 strings from store.py, so string order is time order.
    compare: (a, b) => (a.opened_at < b.opened_at ? -1 : a.opened_at > b.opened_at ? 1 : 0),
  },
  pages: {
    firstDirection: "asc",
    compare: (a, b) => a.page_count - b.page_count,
  },
};

const DEFAULT_RECENT_SORT = { key: "lastActive", direction: "desc" };

// Returns a new, sorted array; `entries` is left in the backend's order,
// which home.js still relies on for "the most recent file".
function sortRecentEntries(entries, sort) {
  const { compare } = RECENT_SORTS[sort.key];
  const sign = sort.direction === "asc" ? 1 : -1;
  return [...entries].sort((a, b) => sign * compare(a, b));
}

function nextRecentSort(current, key) {
  if (current.key === key) {
    return { key, direction: current.direction === "asc" ? "desc" : "asc" };
  }
  return { key, direction: RECENT_SORTS[key].firstDirection };
}

function formatPageCount(count) {
  if (!count) return "—";
  return `${count} ${count === 1 ? "page" : "pages"}`;
}

// --- Table ---------------------------------------------------------------- //

const RECENT_COLUMNS = [
  { key: "name", label: "Name", className: "recent-col-name" },
  { key: "lastActive", label: "Last active", className: "recent-col-when" },
  { key: "pages", label: "Pages", className: "recent-col-pages" },
];

function buildSortHeader(column, sort, onSort) {
  const cell = document.createElement("div");
  cell.className = `recent-th ${column.className}`;
  cell.setAttribute("role", "columnheader");
  const active = sort.key === column.key;
  cell.setAttribute(
    "aria-sort",
    active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"
  );

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "recent-sort-btn";
  btn.dataset.sortKey = column.key;
  btn.textContent = column.label;
  const icon = document.createElement("span");
  icon.className = "recent-sort-icon";
  icon.dataset.icon = active ? `sort_${sort.direction}` : "sort";
  btn.appendChild(icon);
  btn.addEventListener("click", () => onSort(column.key));
  cell.appendChild(btn);
  return cell;
}

function buildHeaderRow(sort, onSort) {
  const row = document.createElement("div");
  row.className = "recent-table-row recent-table-head";
  row.setAttribute("role", "row");
  RECENT_COLUMNS.forEach((column) => row.appendChild(buildSortHeader(column, sort, onSort)));
  const actions = document.createElement("div");
  actions.className = "recent-th recent-col-actions";
  actions.setAttribute("role", "columnheader");
  actions.setAttribute("aria-label", "Actions");
  row.appendChild(actions);
  return row;
}

function buildNameCell(entry) {
  const cell = document.createElement("div");
  cell.className = "recent-col-name recent-name-cell";
  cell.setAttribute("role", "gridcell");

  // .picker-anchor: where home.js's numbered-overlay picker pins this row's
  // badge — on the thumbnail's corner, the same spot it takes on a grid card.
  const thumb = document.createElement("div");
  thumb.className = "recent-table-thumb picker-anchor";
  if (entry.thumbnail) {
    const img = document.createElement("img");
    img.src = `data:image/png;base64,${entry.thumbnail}`;
    img.alt = "";
    thumb.appendChild(img);
  }
  cell.appendChild(thumb);

  const { head, tail } = splitForMiddleEllipsis(entry.title);
  const name = document.createElement("span");
  name.className = "recent-name";
  const headEl = document.createElement("span");
  headEl.className = "recent-name-head";
  headEl.textContent = head;
  name.appendChild(headEl);
  if (tail) {
    const tailEl = document.createElement("span");
    tailEl.className = "recent-name-tail";
    tailEl.textContent = tail;
    name.appendChild(tailEl);
  }
  cell.appendChild(name);
  return cell;
}

function buildTextCell(className, text) {
  const cell = document.createElement("div");
  cell.className = `recent-cell ${className}`;
  cell.setAttribute("role", "gridcell");
  cell.textContent = text;
  return cell;
}

// The full title as a tooltip, but only while the name is really cut off:
// that depends on the window width, so it is decided as the pointer arrives
// (this runs before ui.js's own delegated listener reads data-tooltip)
// rather than once at build time.
function syncNameTooltip(row, entry) {
  const name = row.querySelector(".recent-name");
  const head = name.querySelector(".recent-name-head");
  if (head.scrollWidth > head.clientWidth) name.dataset.tooltip = entry.title;
  else delete name.dataset.tooltip;
}

// One tab stop for the whole list instead of two per row: only the current
// row (and its "⋯") is tabbable, and the arrow keys move between rows.
function setActiveRow(table, row) {
  table.querySelectorAll(".recent-item").forEach((item) => {
    const isActive = item === row;
    item.tabIndex = isActive ? 0 : -1;
    item.querySelector(".recent-more-btn").tabIndex = isActive ? 0 : -1;
  });
}

function focusSiblingRow(row, key) {
  const rows = Array.from(row.parentElement.querySelectorAll(".recent-table-row.recent-item"));
  const index = rows.indexOf(row);
  const targets = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: rows.length - 1 };
  const target = rows[targets[key]];
  if (target) target.focus();
}

// Same contract as a grid card: the whole row opens the PDF (click, Enter
// or Space), and its one nested control — the "⋯" button — never also
// triggers that. A focusable role="row" inside role="grid" rather than
// role="button", since this is a table: screen readers keep the cells.
function buildBodyRow(entry, handlers) {
  const row = document.createElement("div");
  row.className = "recent-table-row recent-item";
  row.setAttribute("role", "row");
  row.tabIndex = -1;
  // What a screen reader announces on landing here; Enter then opens it.
  const pageLabel = formatPageCount(entry.page_count);
  row.setAttribute("aria-label", `Open ${entry.title}, ${entry.relative_time}, ${pageLabel}`);

  row.appendChild(buildNameCell(entry));
  row.appendChild(buildTextCell("recent-col-when", entry.relative_time));
  row.appendChild(buildTextCell("recent-col-pages", formatPageCount(entry.page_count)));

  const actions = document.createElement("div");
  actions.className = "recent-col-actions";
  actions.setAttribute("role", "gridcell");
  const more = document.createElement("button");
  more.type = "button";
  more.className = "icon-btn icon-btn--sm recent-more-btn";
  more.dataset.icon = "more";
  more.setAttribute("aria-label", `More actions for ${entry.title}`);
  more.setAttribute("aria-haspopup", "menu");
  more.setAttribute("aria-expanded", "false");
  more.tabIndex = -1;
  more.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (openRowMenuState && openRowMenuState.trigger === more) closeRowMenu();
    else openRowMenu(more, row, entry, handlers);
  });
  actions.appendChild(more);
  row.appendChild(actions);

  row.addEventListener("pointerover", () => syncNameTooltip(row, entry));
  row.addEventListener("focusin", () => setActiveRow(row.parentElement, row));
  row.addEventListener("click", () => handlers.onOpen(entry));
  row.addEventListener("keydown", (ev) => {
    if (ev.target !== row) return;
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      handlers.onOpen(entry);
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(ev.key)) {
      ev.preventDefault();
      focusSiblingRow(row, ev.key);
    }
  });
  return row;
}

// `entries` arrive already sorted (sortRecentEntries); `handlers` are
// home.js's onSort(key), onOpen(entry), onShowInFolder(entry) and
// onRemove(entry, rowIndex).
function buildRecentTable(entries, sort, handlers) {
  const table = document.createElement("div");
  table.className = "recent-table";
  table.setAttribute("role", "grid");
  table.setAttribute("aria-label", "Recent documents");
  table.appendChild(buildHeaderRow(sort, handlers.onSort));
  entries.forEach((entry) => table.appendChild(buildBodyRow(entry, handlers)));
  const firstRow = table.querySelector(".recent-item");
  if (firstRow) setActiveRow(table, firstRow);
  mountIcons(table);
  return table;
}

// --- Row "⋯" menu ------------------------------------------------------------ //
// One menu open at a time, built fresh per open and appended to <body> so
// the table card's rounded-corner clipping can't cut it off.

const MENU_GAP_PX = 4;
const MENU_EDGE_PX = 8;
let openRowMenuState = null;

function closeRowMenu({ restoreFocus = false } = {}) {
  if (!openRowMenuState) return;
  const { menu, trigger, row } = openRowMenuState;
  openRowMenuState = null;
  menu.remove();
  row.classList.remove("is-menu-open");
  trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus && trigger.isConnected) trigger.focus();
}

function buildMenuItem(icon, label, onSelect) {
  const item = document.createElement("button");
  item.type = "button";
  item.className = "recent-menu-item";
  item.setAttribute("role", "menuitem");
  item.tabIndex = -1;
  const iconEl = document.createElement("span");
  iconEl.dataset.icon = icon;
  item.appendChild(iconEl);
  item.appendChild(document.createTextNode(label));
  item.addEventListener("click", onSelect);
  return item;
}

function positionRowMenu(menu, trigger) {
  const anchor = trigger.getBoundingClientRect();
  const box = menu.getBoundingClientRect();
  let top = anchor.bottom + MENU_GAP_PX;
  if (top + box.height > window.innerHeight - MENU_EDGE_PX) {
    top = anchor.top - box.height - MENU_GAP_PX;
  }
  const left = Math.max(MENU_EDGE_PX, anchor.right - box.width);
  menu.style.top = `${Math.max(MENU_EDGE_PX, top)}px`;
  menu.style.left = `${left}px`;
}

function handleMenuKeydown(ev) {
  const items = Array.from(ev.currentTarget.querySelectorAll("[role='menuitem']"));
  const index = items.indexOf(document.activeElement);
  const moves = {
    ArrowDown: (index + 1) % items.length,
    ArrowUp: (index - 1 + items.length) % items.length,
    Home: 0,
    End: items.length - 1,
  };
  if (ev.key in moves) {
    ev.preventDefault();
    items[moves[ev.key]].focus();
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    ev.stopPropagation();
    closeRowMenu({ restoreFocus: true });
  } else if (ev.key === "Tab") {
    // Hand focus back to the "⋯" button before the browser moves it, so
    // Tab carries on from the row rather than from the end of <body>.
    closeRowMenu({ restoreFocus: true });
  }
}

function openRowMenu(trigger, row, entry, handlers) {
  closeRowMenu();
  const menu = document.createElement("div");
  menu.className = "recent-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", `Actions for ${entry.title}`);

  const select = (action) => () => {
    closeRowMenu({ restoreFocus: true });
    action();
  };
  const rowIndex = Array.from(row.parentElement.querySelectorAll(".recent-item")).indexOf(row);
  const note = document.createElement("p");
  note.className = "recent-menu-note";
  note.id = "recentMenuNote";
  // Not a menu item: exposed through the Remove item's aria-describedby only.
  note.setAttribute("role", "none");
  note.textContent = "Only removes it from this list. The file stays on disk.";
  const remove = buildMenuItem("remove", "Remove from Recent", select(() => handlers.onRemove(entry, rowIndex)));
  remove.setAttribute("aria-describedby", note.id);
  const separator = document.createElement("div");
  separator.className = "recent-menu-separator";
  separator.setAttribute("role", "separator");

  menu.append(
    buildMenuItem("open_pdf", "Open", select(() => handlers.onOpen(entry))),
    buildMenuItem("folder", "Show in folder", select(() => handlers.onShowInFolder(entry))),
    separator,
    remove,
    note
  );
  menu.addEventListener("keydown", handleMenuKeydown);
  document.body.appendChild(menu);
  mountIcons(menu);
  positionRowMenu(menu, trigger);

  openRowMenuState = { menu, trigger, row };
  row.classList.add("is-menu-open");
  trigger.setAttribute("aria-expanded", "true");
  menu.querySelector("[role='menuitem']").focus();
}

// Any press outside the menu, scrolling the list under it, or resizing
// closes it — it is anchored to a row that would otherwise move out from
// under it. Only the page's own scroller counts (the document or Home's
// .content): other scrolling, such as a dialog's list, leaves it open. Losing
// window focus doesn't close it either, so switching apps and back is safe.
function initRowMenuDismissal() {
  if (typeof document === "undefined") return;
  document.addEventListener("pointerdown", (ev) => {
    if (!openRowMenuState) return;
    const { menu, trigger } = openRowMenuState;
    if (!menu.contains(ev.target) && !trigger.contains(ev.target)) closeRowMenu();
  });
  document.addEventListener(
    "scroll",
    (ev) => {
      if (!openRowMenuState) return;
      const scroller = ev.target === document || ev.target.classList.contains("content");
      if (scroller) closeRowMenu();
    },
    true
  );
  window.addEventListener("resize", () => closeRowMenu());
}

initRowMenuDismissal();
