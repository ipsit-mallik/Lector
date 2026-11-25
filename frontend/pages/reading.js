// The reading view's start-up. The reader's code is split by job into the
// reading-*.js modules loaded just before this file (docs/ARCHITECTURE.md §
// "Reader page layout"); they only declare. This file wires them to the page
// and starts the reader, top to bottom, in the order it has always started
// in. Page rendering and annotation logic live in Python (src/lector/api.py
// and the bridge controllers behind it); this side only drives the DOM.

watchPixelRatio();

stripScroll.addEventListener("scroll", onStripScroll);

// Tracked on the document, not on the page image: a drag that runs off the
// edge of the page (or off the window) should still select up to where it
// left and still commit on release, the same way it does in a text editor.
document.addEventListener("mousemove", onSelectionMove);
document.addEventListener("mouseup", endSelection);

bookScroll.addEventListener("scroll", repaintSelectionOnScroll);
stripScroll.addEventListener("scroll", repaintSelectionOnScroll);
bookScroll.addEventListener("scroll", scheduleViewportPush);
stripScroll.addEventListener("scroll", scheduleViewportPush);
bookScroll.addEventListener("scroll", paintFindMarks);
stripScroll.addEventListener("scroll", paintFindMarks);
bookScroll.addEventListener("scroll", paintHighlightTargets);
stripScroll.addEventListener("scroll", paintHighlightTargets);
bookScroll.addEventListener("scroll", scheduleOcrPrefetch);
stripScroll.addEventListener("scroll", scheduleOcrPrefetch);
// Scrolling brings the progress bar back to full strength for a while.
bookScroll.addEventListener("scroll", wakeProgressBar, { passive: true });
stripScroll.addEventListener("scroll", wakeProgressBar, { passive: true });
bookScroll.addEventListener("scroll", scheduleProgressScroll, { passive: true });
stripScroll.addEventListener("scroll", scheduleProgressScroll, { passive: true });

// A scanned page read (reading-ocr.js).
window.addEventListener("lector:ocr", onOcrEvent);
// Book layout centres its page, so a resize moves the highlights under the
// targets laid over them. The page area also changes size when the outline
// panel opens or closes, with no window resize, so it is the page area that is
// watched.
// The page area resizing (the window, the outline panel, Focus mode) or the
// page itself (zoom, a new page, the strip being built) can each move the
// page: it is centred on the window again (page-centering.js) and everything
// drawn over it follows.
const pageResizeObserver = new ResizeObserver(() => {
  // It also fires once on load, before there is a document to paint over.
  if (!state.is_open) return;
  centerActivePage();
  paintHighlightTargets();
  paintFindMarks();
  if (selection && selection.range) paintSelection(selection, false);
  repaintInkStroke();
  updateProgressScroll(currentPageReadFraction());
});
[pageArea, pageCard, stripContainer].forEach((el) => pageResizeObserver.observe(el));

// In-app Save-As dialog (Milestone 8.7), replacing the native
// `create_file_dialog()` the voice router could not see or drive — the same
// file-browser.js module Home's Open dialog uses, in its "save" mode. The
// file tools' Change… uses it too.
const saveAsDialog = ensureSaveAsDialog("reading");
// Organise's Insert… picks its file here (organise.js); voice goes back to
// Organise's context when it closes.
const openFileDialog = ensureOpenFileDialog("organise");
// Extract…'s "Choose another folder…" (organise-extract.js).
OrganiseExtractDialog.configure({ saveAs: saveAsDialog });

configureOrganise({
  title: () => state.title || "",
  openOutput: (path) => openToolOutput(path),
  returnsHome: () => Boolean(organiseReturn),
  leaveForHome: () => leaveOrganiseForHome(),
  onOpen: () => {
    setHighlightMode(false);
    closeHighlightEditor();
    closeHighlightOptions();
    closeZoomPopover();
    if (isFindBarOpen()) closeFindBar();
    if (isOutlinePickerActive()) endOutlinePicker();
    if (isFocusMode()) setFocusMode(false);
    // The footer's hint is cut short at narrow widths: the whole of it here.
    micHint.title = ORGANISE_HINT;
    voice.rest();
  },
  onClose: () => {
    micHint.title = "";
    voice.rest();
    pageArea.focus({ preventScroll: true });
  },
});

// ------------------------------------------------------------------ //
// Wiring                                                               //
// ------------------------------------------------------------------ //

backBtn.addEventListener("click", goHome);

prevBtn.addEventListener("click", goPrev);
nextBtn.addEventListener("click", goNext);

let pageInputSelectOnClick = false;
pageInput.addEventListener("focus", () => {
  pageInput.select();
  pageInputSelectOnClick = true;
});
// The click that focused the box would otherwise put a caret where it landed,
// undoing the select-all above. Only that first click: a second one is the
// reader placing the caret on purpose.
pageInput.addEventListener("mouseup", (ev) => {
  if (pageInputSelectOnClick) ev.preventDefault();
  pageInputSelectOnClick = false;
});
pageInput.addEventListener("input", () => {
  const digits = pageInput.value.replace(/\D/g, "");
  if (digits !== pageInput.value) pageInput.value = digits;
});
pageInput.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    commitPageInput();
    pageInput.blur();
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    revertPageInput();
    pageInput.blur();
  }
});
pageInput.addEventListener("blur", revertPageInput);
zoomInBtn.addEventListener("click", zoomIn);
zoomOutBtn.addEventListener("click", zoomOut);
window.addEventListener("wheel", onWheelZoom, { passive: false });
layoutBtn.addEventListener("click", toggleLayout);
themeBtn.addEventListener("click", cycleTheme);
undoBtn.addEventListener("click", undo);
redoBtn.addEventListener("click", redo);
highlightBtn.addEventListener("click", () => setHighlightMode(!highlightMode));
// What the style card, H/U/S and "… mode" turn on and off (highlight-options.js).
configureHighlightTool({ isOn: () => highlightMode, setOn: setHighlightMode });
// The footer button is the reader's one button for the list: it sits next to
// the mic pill, which is where the question "what can I say?" occurs, and it
// says what it is. "?" and the spoken "what can I say" reach the same panel.
// What the shared list needs to know about this screen (command-reference.js):
// the real range for "go to page N".
configureCommandReference({
  ranges: () => ({ page: state && state.page_count }),
  // Opened from Organise pages, its own commands come first.
  focusSection: () => (isOrganiseOpen() ? "organising" : null),
});
referenceFooterBtn.addEventListener("click", openCommandReference);

// The topbar's button. Going in hides it, so the page takes the keyboard.
focusModeBtn.addEventListener("click", () => {
  if (isFocusMode()) {
    setFocusMode(false);
    return;
  }
  enterFocusMode();
  pageArea.focus({ preventScroll: true });
});

// The reader's keyboard (reading-keys.js).
document.addEventListener("keydown", noteDialogAtKey, true);

document.addEventListener("keydown", onReaderKeydown);

bindSelection(pageSurface, () => state.page_index);

// A truncated title shows in full on hover; one that fits needs no tooltip
// repeating it.
docTitle.addEventListener("pointerenter", () => {
  docTitle.title = docTitle.scrollWidth > docTitle.clientWidth ? docTitle.textContent : "";
});

// What the find bar (find-bar.js) needs to know about this view's layouts.
configureFindBar({
  pageElement: (index) =>
    layoutMode === "book"
      ? (index === state.page_index ? pageSurface : null)
      : stripPageEls[index] || null,
  showPage: async (index) => {
    if (layoutMode === "book" && index !== state.page_index) await goToPage(index + 1);
  },
  scroller: () => activeScrollEl(),
  currentPage: () => state.page_index,
  returnFocus: () => pageArea.focus({ preventScroll: true }),
});

configureProgressBar({
  jumpTo: (index) => jumpToPageIndex(index),
  panelOpen: () => isOutlinePanelOpen(),
  preview: (index) => previewOutlinePage(index),
});
configureFocusMode({
  scroller: () => activeScrollEl(),
  // The header stays down for the zoom popover and the More menu, both of
  // which hang from it.
  holdsPeek: () => isZoomPopoverOpen() || isFileMenuOpen(),
});
// The More menu and the footer's job pill (file-menu.js), and the tools'
// dialog (tool-dialogs.js).
configureFileMenu({
  save: () => saveFromMenu(),
  saveCopy: () => saveCopy(),
  openTool: (tool) => openTool(tool),
  organise: () => startOrganise(),
  organiseBlocked: () => state.organise_blocked || null,
  onClose: () => settleFocusPeek(),
});
ToolDialog.configure({
  where: "reader",
  saveAs: saveAsDialog,
  openOutput: (path) => openToolOutput(path),
  onBackground: (event) => showFileJobPill(event),
});
FileJobs.configure({ open: (path) => openToolOutput(path) });
configureZoomControl({
  setZoomPct: (pct) => setZoomPct(pct),
  fitGeometry: () => zoomFitGeometry(),
  // In Focus mode the topbar was held down for the popover; it may go now.
  onClose: () => settleFocusPeek(),
});
// A fit mode fits again whenever the page area changes size: the window, the
// outline panel, Focus mode. Once a frame at most, while a window is dragged.
let refitFrame = 0;
new ResizeObserver(() => {
  if (refitFrame || !state.is_open) return;
  refitFrame = requestAnimationFrame(() => {
    refitFrame = 0;
    refitZoom();
  });
}).observe(pageArea);
configureOutlinePanel({
  jumpTo: (index) => goToPage(index + 1),
  say: (text) => setMicHint(text),
  onToggle: (open) => progressPanelToggled(open),
});

// What freehand.js needs: this page's text, the selection to hand a text
// press to, and the commit.
configureFreehand({
  loadedPage: (i) => charData.get(i) || null,
  pageData: getPageChars,
  isOnText: isPointOnText,
  beginText: beginSelection,
  moveText: onSelectionMove,
  endText: endSelection,
  commit: commitInkStroke,
  commitFailed: () => showToast(INK_NOT_DRAWN),
  clickAt: (i, x, y) => {
    const at = highlightAtPoint(i, x, y);
    if (at) openHighlightEditor(i, at);
  },
  activeColor: () => activeHighlightColor,
  colorValue: (name) => getComputedStyle(document.documentElement).getPropertyValue(`--highlight-${name}`).trim(),
  toolOn: () => highlightMode,
  textDragging: () => selection !== null,
  reducedMotion: prefersReducedMotion,
});

// What highlight-colors.js needs to know about this view's layouts.
configureHighlightColors({
  visiblePages: () => {
    if (layoutMode === "book") return [{ index: state.page_index, el: pageSurface }];
    const view = stripScroll.getBoundingClientRect();
    return stripPageEls
      .map((el, index) => ({ index, el }))
      .filter(({ el }) => {
        const rect = el.getBoundingClientRect();
        return rect.bottom > view.top && rect.top < view.bottom;
      });
  },
  afterChange: async (next) => {
    adoptState(next);
    await afterAnnotationChange(state.affected_page);
  },
  returnFocus: () => pageArea.focus({ preventScroll: true }),
});

onFileDrop(onPdfDropped);

// A recognized command (reading-voice-commands.js).
window.addEventListener("lector:command", onVoiceCommand);

const voice = initVoice(renderMicState, computeVoiceViewport);

// Whatever init() does (draws the document, finds none and goes home, or
// fails), a page change's busy card comes down after it: it must never stay
// up taking every key and click.
async function init() {
  await mountIcons();
  const theme = await callApi("get_theme");
  document.documentElement.dataset.theme = theme;
  adoptState(await callApi("get_state"));
  if (!state.is_open) {
    window.location.href = "../index.html";
    return;
  }
  // Claim the reading context (Milestone 8.4) so the recognizer's grammar
  // includes this page's navigation/highlight commands. Nothing changes
  // here in practice yet, since `reading` is also the router's default —
  // but this is what stops that from being an accident once Home (or any
  // future screen) narrows itself to a different context and later
  // navigates back here.
  await callApi("set_voice_context", "reading");
  // The backend has already resolved the reopen preference by this point, so
  // state.page_index/layout_mode are either the restored ones or the
  // start-at-the-beginning defaults; this view just renders what it is given.
  startInSavedLayout();
  // The left edge: the progress bar (unless switched off in Settings) and the
  // outline panel, open again if it was left open.
  const [prefs, outline, sizes] = await Promise.all([
    callApi("get_reading_preferences"),
    callApi("get_outline"),
    callApi("get_page_sizes"),
  ]);
  initProgressBar({ pageCount: state.page_count, chapters: outline.chapters, visible: prefs.progress_bar });
  initOutlinePanel({
    entries: outline.entries,
    pageCount: state.page_count,
    pageSizes: sizes,
    currentPage: state.page_index,
    open: prefs.outline_open,
    width: prefs.outline_width,
    minWidth: prefs.outline_width_min,
    maxWidth: prefs.outline_width_max,
  });
  // The palette first: the highlight tool's settings card builds its swatches from it.
  await initHighlightColors();
  await initHighlightOptions(await callApi("get_highlight_options"), state.highlight_color);
  await refresh();
}

// Sent here from Home to organise: Organise's frame and a placeholder tile
// per page from the very first paint (organise.js), not the reader's empty
// page while the document and the draft load.
{
  const raw = sessionStorage.getItem(OPEN_ORGANISE_NOTE);
  if (raw !== null) {
    let note = {};
    try {
      note = JSON.parse(raw) || {};
    } catch {
      note = {};
    }
    showOrganisePlaceholder({ pages: Number(note.pages) || 0, name: typeof note.name === "string" ? note.name : "" });
  }
}

(async () => {
  try {
    await init();
  } catch (err) {
    console.error("The reader couldn't start:", err);
  } finally {
    // Back from a page change: the busy card comes down now the document is
    // drawn (or the reader gave up drawing it).
    await finishPageChangeAfterReload();
  }
  // Sent here to organise (Home's Tools page and Recent menus), once the
  // card is down so Organise isn't refused as busy.
  // Its Cancel and Done then go back to where on Home it was started.
  takeOrganiseReturn(Boolean(state.is_open && sessionStorage.getItem(OPEN_ORGANISE_NOTE)));
  if (state.is_open && sessionStorage.getItem(OPEN_ORGANISE_NOTE)) {
    sessionStorage.removeItem(OPEN_ORGANISE_NOTE);
    startOrganise();
  } else {
    // Nothing to organise after all (no document open): the note goes, so
    // it can't send a later visit to the reader into Organise.
    sessionStorage.removeItem(OPEN_ORGANISE_NOTE);
    clearOrganisePlaceholder({ hide: !isOrganiseOpen() });
  }
})();
