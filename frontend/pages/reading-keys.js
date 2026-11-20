// The reader's keyboard: every shortcut, and Focus mode's way in.

// Focus mode's own popovers live in the chrome it hides, so they close first:
// left open, the next Esc would go on closing something no longer visible.
function enterFocusMode() {
  if (isFocusMode()) return;
  closeHighlightOptions();
  closeZoomPopover();
  setFocusMode(true);
}

// Whether a dialog was up when the key went down. Captured before any other
// listener runs, since a dialog's own Esc hides it before the handler below
// gets the key, and one Esc must not both cancel it and leave Focus mode.
let dialogOpenAtKey = false;

const ZOOM_SHORTCUTS = {
  0: () => chooseZoomFit(ZOOM_FIT_PAGE),
  1: () => chooseActualSize(),
  2: () => chooseZoomFit(ZOOM_FIT_WIDTH),
};

function noteDialogAtKey() {
  dialogOpenAtKey = Boolean(document.querySelector(".dialog-scrim:not([hidden])"));
}

// Registered in reading.js after noteDialogAtKey.
function onReaderKeydown(ev) {
  // Organise pages has keys of its own (organise.js); none of the reader's
  // reach the page behind it — H, U and S included.
  if (isOrganiseOpen()) return;
  // Ctrl+Shift+O: Organise pages (the More menu shows it). With Shift held,
  // `ev.key` is "O", so the Open shortcut's lower-case "o" below never sees it.
  if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "o") {
    ev.preventDefault();
    if (!ev.repeat && !dialogOpenAtKey) startOrganise();
    return;
  }
  // Focus mode's key works from anywhere in the reader, the find box included,
  // but not behind a dialog.
  if (ev.key === "F11") {
    ev.preventDefault();
    if (ev.repeat || dialogOpenAtKey) return;
    if (isFocusMode()) setFocusMode(false);
    else enterFocusMode();
    return;
  }
  // Ctrl+= and Ctrl+- are the reader's zoom, never the web view's own, from
  // the page box and the find box too.
  if ((ev.ctrlKey || ev.metaKey) && (ev.key === "=" || ev.key === "+" || ev.key === "-")) {
    ev.preventDefault();
    ev.key === "-" ? zoomOut() : zoomIn();
    return;
  }
  // Ctrl+0 Fit page, Ctrl+1 100%, Ctrl+2 Fit width, as in Acrobat; from the
  // inputs too, like the zoom steps above.
  const zoomShortcut = (ev.ctrlKey || ev.metaKey) && !ev.altKey && ZOOM_SHORTCUTS[ev.key];
  if (zoomShortcut) {
    ev.preventDefault();
    zoomShortcut();
    return;
  }
  // Ctrl+Shift+S is Save a copy (the More menu shows it), from the inputs
  // too, as Ctrl+S's own Save is. With Shift held, `ev.key` is "S", so the
  // lower-case "s" below never sees it.
  if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "s") {
    ev.preventDefault();
    if (!ev.repeat && !dialogOpenAtKey) saveCopy();
    return;
  }
  // The zoom slider is an input itself, so its Esc is taken before the guard
  // below; and it comes ahead of everything else open, Focus mode included.
  if (ev.key === "Escape" && isZoomPopoverOpen() && !dialogOpenAtKey) {
    closeZoomPopover({ returnFocus: true });
    return;
  }
  // H, U and S: the highlight tool in that style, or off when it already is
  // (highlight-options.js). Never while typing (command-reference.js's
  // isTypingTarget: the page number, the find box, any dialog's field), behind
  // a dialog, or with a modifier — Ctrl+S is Save.
  const toolStyle = !ev.ctrlKey && !ev.metaKey && !ev.altKey && highlightStyleForKey(ev.key);
  if (toolStyle) {
    if (ev.repeat || dialogOpenAtKey || isTypingTarget(ev.target)) return;
    ev.preventDefault();
    pressHighlightStyleKey(toolStyle);
    return;
  }
  if (ev.target.tagName === "INPUT") return;
  switch (ev.key) {
    case "ArrowRight":
    case "PageDown":
      goNext();
      break;
    case "ArrowLeft":
    case "PageUp":
      goPrev();
      break;
    case "ArrowDown":
      if (layoutMode === "strip") scrollElementBy(stripScroll, 60);
      else goNext();
      break;
    case "ArrowUp":
      if (layoutMode === "strip") scrollElementBy(stripScroll, -60);
      else goPrev();
      break;
    case "=":
    case "+":
      zoomIn();
      break;
    case "-":
      zoomOut();
      break;
    case "Escape":
      // Abandons a selection mid-drag: the wash disappears and the release
      // that follows writes nothing, so a drag started by mistake costs
      // nothing to back out of. With no drag on, Esc closes the find bar
      // even once focus has moved to the page. A stroke mid-drag is the same.
      if (selection) cancelSelection();
      else if (isDrawingInk()) cancelInkStroke();
      else if (isOutlinePickerActive()) endOutlinePicker();
      else if (isHighlightEditorOpen()) closeHighlightEditor({ returnFocus: true });
      else if (isHighlightOptionsOpen()) closeHighlightOptions({ returnFocus: true });
      else if (isFindBarOpen()) closeFindBar();
      else if (isFocusMode() && !dialogOpenAtKey) setFocusMode(false);
      break;
    case "z":
      if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
      break;
    case "y":
      if (ev.ctrlKey) { ev.preventDefault(); redo(); }
      break;
    case "s":
      if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); promptSaveIfDirty(false); }
      break;
    case "o":
      // The same route as the voice command: leave for Home's Open dialog,
      // after the unsaved-highlights check.
      if ((ev.ctrlKey || ev.metaKey) && !document.querySelector(".dialog-scrim:not([hidden])")) {
        ev.preventDefault();
        leaveReadingTo("../index.html", { openDialog: true });
      }
      break;
  }
}
