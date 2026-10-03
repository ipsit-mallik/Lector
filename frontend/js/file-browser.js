// Shared in-app file-browser overlay for the Open/Save-As dialogs (Milestone
// 8.7). docs/ARCHITECTURE.md: the native OS file choosers are replaced
// because the voice router can only see and drive the app's own UI, not OS
// chrome, so folders/PDFs are listed in Python (features/dialogs/browser.py)
// and drawn as regular numbered rows here — the same Milestone 8.6
// numbered-overlay picker mechanic, reused row-for-row rather than rebuilt.
//
// One implementation backs both Home's "Open PDF" and the reading view's
// "Save a copy" flow: both are "list folders + PDFs, number the rows, let a
// click or a spoken number act on one." Only the footer (a plain Open vs. a
// filename field + Save) and the starting directory differ, which is why
// `mode` ("open" | "save") is the one branch point below rather than two
// near-duplicate modules.
//
// Layout (docs/DESIGN_SYSTEM.md's tree-sidebar redesign): a left pane with
// Quick Access chips and a lazily-expanded directory tree, a breadcrumb above
// both panes, and the numbered folder contents on the right with a search
// filter. Quick Access entries have natural spoken labels, so they are reached
// by name ("documents") rather than number; only the right pane's rows are
// numbered, and filtering renumbers the visible rows so a spoken number always
// matches what is on screen.
const QUICK_ACCESS_INTENTS = {
  DIALOG_HOME: "Home",
  DIALOG_DESKTOP: "Desktop",
  DIALOG_DOCUMENTS: "Documents",
  DIALOG_DOWNLOADS: "Downloads",
};
const QUICK_ACCESS_ICONS = {
  Home: "home",
  Desktop: "desktop",
  Documents: "documents",
  Downloads: "downloads",
};

// `browse_directory`'s `path` is already an OS-native absolute path
// (`str(Path(...).resolve())` in browser.py) — Windows backslashes or POSIX
// forward slashes, never mixed — so these string helpers only need to match
// whichever separator a path already uses; no path library is needed.
function pathSeparator(path) {
  return path.includes("\\") ? "\\" : "/";
}

function joinPath(dir, name) {
  const sep = pathSeparator(dir);
  return dir.endsWith(sep) ? dir + name : dir + sep + name;
}

// A comparison key: Windows paths are case-insensitive, and a trailing
// separator is not significant except on a bare root ("C:\", "/").
function pathKey(path) {
  const sep = pathSeparator(path);
  const isRoot = path === "/" || /^[A-Za-z]:\\$/.test(path);
  const trimmed = !isRoot && path.endsWith(sep) ? path.slice(0, -1) : path;
  return sep === "\\" ? trimmed.toLowerCase() : trimmed;
}

function isSamePath(a, b) {
  return pathKey(a) === pathKey(b);
}

function isUnderPath(path, base) {
  if (isSamePath(path, base)) return true;
  const key = pathKey(base);
  const prefix = key.endsWith(pathSeparator(path)) ? key : key + pathSeparator(path);
  return pathKey(path).startsWith(prefix);
}

// The path segments strictly below `base` on the way to `path`, each with the
// full path built up to that point — `base` must be an ancestor of `path`.
function segmentsBelow(base, path) {
  const sep = pathSeparator(path);
  const baseWithSep = base.endsWith(sep) ? base : base + sep;
  const rest = isSamePath(path, base) ? "" : path.slice(baseWithSep.length);
  const segments = [];
  let built = base;
  for (const name of rest.split(sep).filter(Boolean)) {
    built = joinPath(built, name);
    segments.push({ name, path: built });
  }
  return segments;
}

// "C:\" -> "Local Disk (C:)"; a Mac volume or "/" is named by its last
// segment, since neither has a drive letter to show.
function driveLabel(path) {
  const letter = /^([A-Za-z]):\\$/.exec(path);
  if (letter) return `Local Disk (${letter[1].toUpperCase()}:)`;
  if (path === "/") return "Macintosh HD";
  return path.split("/").filter(Boolean).pop() || path;
}

// How far from the pane's edge a row is left when the tree has to scroll to it
// (so it doesn't sit flush against the edge, half under the focus ring).
const TREE_SCROLL_EDGE_PAD = 8;

// Where to scroll the tree's own scroll container so the active row is on
// screen, or null when nothing needs to move. All inputs are in the tree's
// scroll-content coordinates. Pure so the rules can be tested under Node.
//
//   * `center` (the dialog just opened): put the row in the middle of the pane.
//     Always returns a position, even if the row is already visible.
//   * otherwise: leave it alone when the row is already fully visible, else
//     the *minimum* movement that brings it into view with a small pad, so the
//     tree does not jump while someone is clicking around in it.
//
// The result is clamped to the scrollable range. In a pane barely taller than
// a row the pad shrinks rather than pushing the row out of view, and a row
// taller than the pane is aligned by its top.
function treeScrollTarget({
  nodeTop,
  nodeHeight,
  viewTop,
  viewHeight,
  contentHeight,
  center,
  edgePad = TREE_SCROLL_EDGE_PAD,
}) {
  const maxScroll = Math.max(0, contentHeight - viewHeight);
  const clamp = (value) => Math.min(maxScroll, Math.max(0, value));
  if (center) return clamp(nodeTop - (viewHeight - nodeHeight) / 2);

  const nodeBottom = nodeTop + nodeHeight;
  if (nodeTop >= viewTop && nodeBottom <= viewTop + viewHeight) return null;

  const pad = Math.max(0, Math.min(edgePad, (viewHeight - nodeHeight) / 2));
  if (nodeTop < viewTop || nodeHeight >= viewHeight) return clamp(nodeTop - pad);
  return clamp(nodeBottom - viewHeight + pad);
}

function createFileBrowser({
  scrimId,
  listId,
  pathId,
  quickId,
  treeId,
  searchId,
  cancelBtnId,
  confirmBtnId,
  filenameId,
  mode,
  voiceContext,
  restingContext,
}) {
  const scrim = document.getElementById(scrimId);
  const listEl = document.getElementById(listId);
  const crumbsEl = document.getElementById(pathId);
  const quickEl = document.getElementById(quickId);
  const treeEl = document.getElementById(treeId);
  const searchEl = document.getElementById(searchId);
  const cancelBtn = document.getElementById(cancelBtnId);
  const confirmBtn = confirmBtnId ? document.getElementById(confirmBtnId) : null;
  const filenameInput = filenameId ? document.getElementById(filenameId) : null;

  let entries = [];
  let visibleEntries = [];
  let currentDir = null;
  let parentDir = null;
  let listError = null;
  let settleOpen = null;
  let navigationId = 0;
  // True while the right pane lists the drives themselves ("This PC") rather
  // than a folder — reached by "go up" from a drive root, the one place a
  // voice-only user can pick a different drive by number.
  let showingDrives = false;

  // Fetched once per `open()`.
  let quickAccess = [];
  let homePath = null;
  let drives = [];
  let profileFolderPath = null;
  const treeNodes = new Map();

  function findDrive(path) {
    let best = null;
    for (const drive of drives) {
      if (!isUnderPath(path, drive.path)) continue;
      if (!best || pathKey(drive.path).length > pathKey(best.path).length) best = drive;
    }
    return best;
  }

  // The OS profile folder (`C:\Users`, `/Users`) is never shown as a tree
  // node — Quick Access is how a user reaches anything under their home.
  function findProfileFolder() {
    const drive = homePath && findDrive(homePath);
    if (!drive) return null;
    const [first] = segmentsBelow(drive.path, homePath);
    return first ? first.path : null;
  }

  // ---- Breadcrumb -------------------------------------------------------

  function crumbPlan(dir) {
    const drive = findDrive(dir);
    if (!drive) return [{ name: dir, path: dir }];
    const crumbs = [{ name: driveLabel(drive.path), path: drive.path }];
    const underHome = homePath && !isSamePath(homePath, drive.path) && isUnderPath(dir, homePath);
    if (!underHome) return crumbs.concat(segmentsBelow(drive.path, dir));
    const below = segmentsBelow(homePath, dir);
    return crumbs.concat(below.length ? below : [{ name: "Home", path: homePath }]);
  }

  function appendCrumbText(text, className) {
    const el = document.createElement("span");
    el.className = className;
    el.textContent = text;
    crumbsEl.appendChild(el);
    return el;
  }

  function renderBreadcrumb() {
    crumbsEl.innerHTML = "";
    if (showingDrives) {
      appendCrumbText("This PC", "browser-crumb browser-crumb--current");
      return;
    }
    appendCrumbText("This PC", "browser-crumb");
    const crumbs = crumbPlan(currentDir);
    crumbs.forEach((crumb, i) => {
      appendCrumbText("›", "browser-crumb-sep");
      const isLast = i === crumbs.length - 1;
      const el = appendCrumbText(
        crumb.name,
        isLast ? "browser-crumb browser-crumb--current" : "browser-crumb browser-crumb--link"
      );
      if (!isLast) el.addEventListener("click", () => navigateTo(crumb.path));
    });
    if (listError) appendCrumbText("— couldn't be read", "browser-crumb-error");
  }

  // ---- Right pane: numbered rows ---------------------------------------

  function renderRows() {
    const query = searchEl.value.trim().toLowerCase();
    visibleEntries = query
      ? entries.filter((entry) => entry.name.toLowerCase().includes(query))
      : entries;
    listEl.innerHTML = "";
    if (!visibleEntries.length) {
      const empty = document.createElement("div");
      empty.className = "browser-empty";
      empty.textContent = query ? "No matches in this folder." : "No folders or PDFs here.";
      listEl.appendChild(empty);
      return;
    }
    visibleEntries.forEach((entry, i) => {
      const row = document.createElement("div");
      row.className = "browser-row";

      const icon = document.createElement("span");
      icon.className = "browser-row-icon";
      icon.dataset.icon = entry.is_dir ? "folder" : "empty_doc";
      row.appendChild(icon);

      const name = document.createElement("span");
      name.className = "browser-row-name";
      name.textContent = entry.name;
      row.appendChild(name);

      const badge = document.createElement("span");
      badge.className = "browser-row-badge";
      badge.textContent = String(i + 1);
      row.appendChild(badge);

      row.addEventListener("click", () => activateRow(i));
      listEl.appendChild(row);
    });
    mountIcons(listEl);
  }

  // ---- Left pane: Quick Access -----------------------------------------

  function renderQuickAccess() {
    quickEl.innerHTML = "";
    for (const entry of quickAccess) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "browser-quick-chip";
      chip.dataset.path = entry.path;

      const icon = document.createElement("span");
      icon.className = "browser-quick-icon";
      icon.dataset.icon = QUICK_ACCESS_ICONS[entry.name] || "folder";
      chip.appendChild(icon);
      chip.appendChild(document.createTextNode(entry.name));

      chip.addEventListener("click", () => navigateTo(entry.path));
      quickEl.appendChild(chip);
    }
    mountIcons(quickEl);
  }

  function goQuickAccess(name) {
    const entry = quickAccess.find((e) => e.name === name);
    if (entry) navigateTo(entry.path);
  }

  // ---- Left pane: directory tree ---------------------------------------

  function createTreeNode(entry, depth, label, iconName) {
    const wrap = document.createElement("div");
    wrap.className = "tree-node";

    const row = document.createElement("div");
    row.className = "tree-row";
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-expanded", "false");
    row.tabIndex = 0;

    const chevron = document.createElement("span");
    chevron.className = "tree-chevron";
    chevron.dataset.icon = "chevron_right";
    row.appendChild(chevron);
    if (iconName) {
      const icon = document.createElement("span");
      icon.className = "tree-icon";
      icon.dataset.icon = iconName;
      row.appendChild(icon);
    }
    const text = document.createElement("span");
    text.className = "tree-label";
    text.textContent = label;
    row.appendChild(text);

    // Children wrapper: the grid-rows 0fr -> 1fr trick animates to the
    // content's real height with no measured pixel value; the connector
    // line and its accent overlay live inside it.
    const children = document.createElement("div");
    children.className = "tree-children";
    const inner = document.createElement("div");
    inner.className = "tree-children-inner";
    const branch = document.createElement("div");
    branch.className = "tree-branch";
    const line = document.createElement("span");
    line.className = "tree-line";
    line.style.setProperty("--depth", String(depth));
    const fill = document.createElement("span");
    fill.className = "tree-line-fill";
    line.appendChild(fill);
    branch.appendChild(line);
    inner.appendChild(branch);
    children.appendChild(inner);

    wrap.appendChild(row);
    wrap.appendChild(children);

    const node = {
      path: entry.path,
      depth,
      row,
      chevron,
      childrenEl: children,
      branchEl: branch,
      lineEl: line,
      expanded: false,
      loadPromise: null,
    };
    treeNodes.set(pathKey(entry.path), node);
    chevron.addEventListener("click", (ev) => {
      ev.stopPropagation();
      toggleNode(node);
    });
    row.addEventListener("click", () => activateTreeNode(node));
    row.addEventListener("keydown", (ev) => onTreeKey(ev, node));
    return wrap;
  }

  // Children are fetched the first time a node is expanded and then kept —
  // re-expanding never re-fetches. A caller that already holds `node`'s
  // directory listing (navigating into a folder lists it for the right pane)
  // passes it as `knownListing` so the same folder is not listed twice.
  function loadChildren(node, knownListing) {
    if (!node.loadPromise) {
      node.loadPromise = (async () => {
        const listing = knownListing || (await callApi("browse_directory", node.path));
        const folders = listing.entries.filter(
          (e) => e.is_dir && !(profileFolderPath && isSamePath(e.path, profileFolderPath))
        );
        for (const folder of folders) {
          node.branchEl.appendChild(createTreeNode(folder, node.depth + 1, folder.name, null));
        }
        node.chevron.classList.toggle("is-leaf", folders.length === 0);
        mountIcons(node.branchEl);
      })().catch((err) => {
        node.loadPromise = null;
        throw err;
      });
    }
    return node.loadPromise;
  }

  function setExpanded(node, expanded) {
    node.expanded = expanded;
    node.childrenEl.classList.toggle("is-expanded", expanded);
    node.chevron.classList.toggle("is-expanded", expanded);
    node.row.setAttribute("aria-expanded", String(expanded));
  }

  async function expandNode(node, knownListing) {
    if (node.expanded) return;
    await loadChildren(node, knownListing);
    setExpanded(node, true);
  }

  async function toggleNode(node) {
    if (node.expanded) setExpanded(node, false);
    else await expandNode(node);
  }

  // Clicking a folder navigates to it (which also expands it, see
  // `revealCurrent`); clicking the already-current folder toggles it open or
  // closed instead, so a folder behaves like an accordion header.
  function activateTreeNode(node) {
    if (currentDir && isSamePath(node.path, currentDir)) {
      toggleNode(node);
      return;
    }
    navigateTo(node.path);
  }

  function onTreeKey(ev, node) {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      activateTreeNode(node);
    } else if (ev.key === "ArrowRight") {
      ev.preventDefault();
      expandNode(node);
    } else if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      setExpanded(node, false);
    }
  }

  function buildTree() {
    treeEl.innerHTML = "";
    treeEl.scrollTop = 0;
    pendingTreeScrollTop = null;
    treeNodes.clear();
    for (const drive of drives) {
      treeEl.appendChild(createTreeNode(drive, 0, driveLabel(drive.path), "drive"));
    }
    mountIcons(treeEl);
  }

  // Expands the current folder's node and each tree ancestor above it that
  // exists as a node (a folder under the hidden profile folder has none, so
  // the walk simply stops there), so its immediate subfolders are visible.
  // Stops early if a newer navigation has superseded this one. `listing` is
  // the current folder's own listing, reused for its node instead of a second
  // `browse_directory` call.
  async function revealCurrent(id, listing) {
    for (const path of pathChainToCurrent()) {
      const node = treeNodes.get(pathKey(path));
      if (!node) return;
      await expandNode(node, isSamePath(path, currentDir) ? listing : undefined);
      if (id !== navigationId) return;
    }
  }

  // ---- Tree follows the active folder -----------------------------------
  //
  // After every folder change, however it was made (tree click, breadcrumb,
  // file list, Quick Access, a spoken number or name), the tree scrolls its own
  // container so the active folder's row is on screen: centred when the dialog
  // has just opened, otherwise only if it isn't already fully visible and by
  // the minimum amount. Quick Access sits outside the scrolling tree, so this
  // never moves it, and only `treeEl` is ever scrolled — not the modal, the
  // page or the file list.

  // The folder path from its drive down to the current folder, inclusive.
  function pathChainToCurrent() {
    const drive = findDrive(currentDir);
    if (!drive) return [];
    return [drive.path, ...segmentsBelow(drive.path, currentDir).map((s) => s.path)];
  }

  // The row to bring into view: the current folder's own, or — when it has no
  // node (a folder under the hidden OS profile folder) — its nearest ancestor
  // that does, so at least the drive that contains it is on screen.
  function activeTreeNode() {
    const chain = pathChainToCurrent();
    for (let i = chain.length - 1; i >= 0; i--) {
      const node = treeNodes.get(pathKey(chain[i]));
      if (node) return node;
    }
    return null;
  }

  // `el`'s top within the tree's scroll content, or null if `el` is not inside
  // it (or not laid out, e.g. the dialog is hidden). Summed from layout
  // offsets up the offsetParent chain rather than read from
  // getBoundingClientRect: the dialog card can be mid-transform, which would
  // skew a client rect, and layout offsets also ignore the expand animation's
  // clipping. `treeEl` is `position: relative` so it terminates the chain.
  function offsetWithinTree(el) {
    let top = 0;
    let cur = el;
    while (cur && cur !== treeEl) {
      top += cur.offsetTop;
      cur = cur.offsetParent;
    }
    return cur === treeEl ? top : null;
  }

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Resolves once the tree has stopped changing height. Expanding or
  // collapsing a node runs a grid-rows transition, and until it ends a row's
  // offset — and the tree's scrollHeight, which caps how far it can scroll — is
  // mid-way to its final value, so a target measured or applied then lands in
  // the wrong place. That includes a transition an *earlier* folder change
  // started, which a navigation that expanded nothing itself would otherwise
  // not know about. `getAnimations()` flushes pending style first, so a
  // transition begun a moment ago is included; under reduced motion there are
  // none and this resolves at once. The cap is only a backstop.
  const TREE_LAYOUT_SETTLE_CAP_MS = 1500;

  function layoutTransitionsSettled() {
    const running = treeEl
      .getAnimations({ subtree: true })
      .filter((animation) => animation.transitionProperty === "grid-template-rows");
    if (!running.length) return Promise.resolve();
    return Promise.race([
      Promise.allSettled(running.map((animation) => animation.finished)),
      new Promise((resolve) => setTimeout(resolve, TREE_LAYOUT_SETTLE_CAP_MS)),
    ]);
  }

  // Where a smooth scroll that is still running is heading. A second folder
  // change arriving mid-animation is judged against that destination, not the
  // position the animation happens to be passing through: a row momentarily in
  // view as it passes would otherwise count as "already visible", no new scroll
  // would start, and the running one would carry on past it.
  let pendingTreeScrollTop = null;
  treeEl.addEventListener("scrollend", () => {
    pendingTreeScrollTop = null;
  });
  // Someone scrolling the tree themselves takes over from any running scroll.
  treeEl.addEventListener("wheel", () => {
    pendingTreeScrollTop = null;
  }, { passive: true });
  treeEl.addEventListener("pointerdown", () => {
    pendingTreeScrollTop = null;
  });

  async function scrollTreeToCurrent(id, { initial }) {
    await layoutTransitionsSettled();
    if (id !== navigationId) return;
    const node = activeTreeNode();
    if (!node) return;
    const top = offsetWithinTree(node.row);
    if (top === null) return;
    const target = treeScrollTarget({
      nodeTop: top,
      nodeHeight: node.row.offsetHeight,
      viewTop: pendingTreeScrollTop ?? treeEl.scrollTop,
      viewHeight: treeEl.clientHeight,
      contentHeight: treeEl.scrollHeight,
      center: initial,
    });
    if (target === null) return;
    const smooth = !initial && !prefersReducedMotion();
    pendingTreeScrollTop = smooth ? target : null;
    treeEl.scrollTo({ top: target, behavior: smooth ? "smooth" : "instant" });
  }

  // Highlights the current node and Quick Access chip, and lights exactly the
  // connector lines on the current folder's ancestor chain (each line's
  // `--depth` delay makes the accent visibly travel down the path).
  function updateSelection() {
    for (const node of treeNodes.values()) {
      const isCurrent = isSamePath(node.path, currentDir);
      node.row.classList.toggle("is-current", isCurrent);
      node.lineEl.classList.toggle("is-active-path", !isCurrent && isUnderPath(currentDir, node.path));
    }
    for (const chip of quickEl.children) {
      chip.classList.toggle("is-active", isSamePath(chip.dataset.path, currentDir));
    }
  }

  // ---- Navigation and actions ------------------------------------------

  // The one place the current folder changes, whatever caused it. `initial` is
  // set only by `open()`: the tree is then centred on the folder instead of
  // scrolled the minimum amount.
  async function navigateTo(dir, { initial = false } = {}) {
    const id = ++navigationId;
    const listing = await callApi("browse_directory", dir);
    if (id !== navigationId) return;
    showingDrives = false;
    currentDir = listing.path;
    parentDir = listing.parent;
    entries = listing.entries;
    listError = listing.error;
    searchEl.value = "";
    renderBreadcrumb();
    renderRows();
    updateSelection();
    // The right pane is already correct at this point; a failure expanding the
    // tree must not turn a successful navigation into an unhandled rejection.
    try {
      await revealCurrent(id, listing);
    } catch (err) {
      console.error("file-browser: could not reveal folder in tree", err);
    }
    if (id !== navigationId) return;
    updateSelection();
    try {
      await scrollTreeToCurrent(id, { initial });
    } catch (err) {
      console.error("file-browser: could not scroll the tree to the folder", err);
    }
  }

  // A folder row navigates into it, in both modes. A PDF file row opens it
  // directly in `open` mode (the same "a number does what a click already
  // does" contract Milestone 8.6 established) — there is nothing to open in
  // `save` mode, so there it pre-fills the filename field instead, the same
  // convenience a native Save-As dialog offers when you click an existing
  // file. `index` is into the *visible* (filtered) rows, so a spoken number
  // always matches the badge on screen.
  function activateRow(index) {
    const entry = visibleEntries[index];
    if (!entry) return;
    if (entry.is_dir) {
      navigateTo(entry.path);
      return;
    }
    if (mode === "open") {
      closeWith({ path: entry.path });
    } else if (filenameInput) {
      filenameInput.value = entry.name;
    }
  }

  // No button calls this any more — the breadcrumb and tree replaced Up —
  // but "go up" (DIALOG_UP) is still voice-reachable. From a drive root there
  // is no parent folder, so it lists the drives instead; that is the only
  // voice route to another drive, since tree nodes have no spoken label.
  function goUp() {
    if (parentDir) navigateTo(parentDir);
    else if (drives.length > 1) showDrives();
  }

  function showDrives() {
    navigationId += 1; // supersede any navigation still in flight
    showingDrives = true;
    entries = drives.map((drive) => ({ name: driveLabel(drive.path), path: drive.path, is_dir: true }));
    listError = null;
    searchEl.value = "";
    renderBreadcrumb();
    renderRows();
  }

  function confirmSave() {
    // Nothing to save into while the pane lists drives rather than a folder.
    if (!filenameInput || showingDrives) return;
    let name = filenameInput.value.trim();
    if (!name) return;
    if (!name.toLowerCase().endsWith(".pdf")) name += ".pdf";
    closeWith({ path: joinPath(currentDir, name) });
  }

  function closeWith(result) {
    scrim.hidden = true;
    callApi("set_voice_context", restingContext);
    const resolve = settleOpen;
    settleOpen = null;
    if (resolve) resolve(result);
  }

  function isOpen() {
    return !scrim.hidden;
  }

  async function loadSidebar() {
    const [quick, driveListing] = await Promise.all([
      callApi("list_quick_access"),
      callApi("list_drives"),
    ]);
    quickAccess = quick.entries;
    homePath = quickAccess.length ? quickAccess[0].path : null;
    drives = driveListing.entries;
    profileFolderPath = findProfileFolder();
    renderQuickAccess();
    buildTree();
  }

  function open({ dir, filename }) {
    return new Promise((resolve) => {
      settleOpen = resolve;
      if (filenameInput) filenameInput.value = filename || "";
      scrim.hidden = false;
      callApi("set_voice_context", voiceContext);
      // The sidebar is a convenience: if it fails to load the folder list must
      // still appear, so its failure is swallowed rather than blocking navigation.
      loadSidebar()
        .catch((err) => console.error("file-browser: sidebar failed to load", err))
        .then(() => navigateTo(dir, { initial: true }));
    });
  }

  // DIALOG_PICK/DIALOG_UP/DIALOG_HOME…DIALOG_DOWNLOADS/CANCEL/DIALOG_CONFIRM
  // only ever arrive while this dialog is the active voice context (router.py
  // scopes them there), so the caller only needs to route to this while
  // `isOpen()` is true.
  function handleCommand(command) {
    switch (command.intent) {
      case "DIALOG_PICK":
        activateRow(command.index - 1);
        break;
      case "DIALOG_UP":
        goUp();
        break;
      case "CANCEL":
        closeWith({ cancelled: true });
        break;
      case "DIALOG_CONFIRM":
        confirmSave();
        break;
      default:
        if (QUICK_ACCESS_INTENTS[command.intent]) goQuickAccess(QUICK_ACCESS_INTENTS[command.intent]);
    }
  }

  searchEl.addEventListener("input", renderRows);
  cancelBtn.addEventListener("click", () => closeWith({ cancelled: true }));
  if (confirmBtn) confirmBtn.addEventListener("click", confirmSave);

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && isOpen()) closeWith({ cancelled: true });
  });

  return { open, handleCommand, isOpen };
}
