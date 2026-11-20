// What each spoken command does, in the reader and in Organise pages.

// --- Voice navigation ---------------------------------------------------- //
// Python recognizes the phrase and parses it into an intent
// (src/lector/features/voice/command_grammar.py); this end decides what that
// intent means for this view. Both halves of that split matter: the grammar
// has no idea there are two layouts, and this file has no idea which
// phrasings map to NEXT_PAGE.

// How far "scroll down" moves: most of a screen, but not all of it. Leaving
// an overlap means the line you were reading when you spoke is still on
// screen afterwards, so you never have to hunt for your place — the same
// reason Page Down in a text editor doesn't advance by a full viewport.
const VOICE_SCROLL_FRACTION = 0.8;

// Scrolling that turns the page when it runs out of room. In book layout a
// page that fits the window entirely cannot scroll at all, so without this
// fallback "scroll down" would be a command that visibly does nothing on the
// app's default layout — which reads as a broken microphone rather than as a
// document that has no more to scroll.
async function voiceScroll(delta) {
  const el = activeScrollEl();
  if (scrollElementBy(el, delta * el.clientHeight * VOICE_SCROLL_FRACTION)) return;
  if (layoutMode !== "book") return; // The strip is one continuous scroller.

  const page = state.page_index;
  await (delta > 0 ? goNext() : goPrev());
  if (state.page_index === page) return; // Already on the first or last page.
  // Land where reading continues from: the top of the next page going down,
  // the bottom of the previous one going up. Keeping the old offset would
  // drop the reader into the middle of a page they haven't read yet.
  scrollElementTo(el, delta > 0 ? 0 : el.scrollHeight, { animate: false });
}

// Matches and highlights `query` against the currently visible viewport
// (api.py's highlight_by_voice reuses the region frozen by the
// start_listening() call that began this utterance) and reports the outcome
// through the mic pill, the same way "heard" already does for navigation —
// a highlight that silently fails to match would otherwise look identical to
// a dropped command.
// What the mic pill says a voice markup did, by style.
const VOICE_MARKUP_VERBS = { highlight: "Highlighted", underline: "Underlined", strikethrough: "Struck through" };

async function voiceHighlight(query, sentence, color = null, style = "highlight") {
  adoptState(await callApi("highlight_by_voice", query, sentence, color, style));
  const result = state.voice_highlight || { matched: false, query };
  if (result.matched) {
    await afterAnnotationChange(result.affected_page);
    // The color is named only when it was a one-off, since that is the case
    // where it differs from what the preview under the tool shows.
    const verb = VOICE_MARKUP_VERBS[result.style] || VOICE_MARKUP_VERBS.highlight;
    setMicHint(color ? `${verb} “${result.text}” in ${result.color}` : `${verb} “${result.text}”`);
  } else if (result.ocr) {
    updateChrome();
    showVoiceOcr(result.ocr);
  } else {
    updateChrome();
    setMicHint(`Couldn't find “${result.query}” on this page`);
  }
}

// "thicker" / "thinner": the freehand thickness one preset along.
async function voiceThickness(direction) {
  const { width, moved } = await stepInkWidth(direction);
  if (moved) setMicHint(`Thickness ${width} pt`);
  else setMicHint(`Thickness ${width} pt — already the ${direction > 0 ? "thickest" : "thinnest"}`);
}

// "underline mode" and the other two: that style, with the tool on.
async function voiceStyleMode(style) {
  await chooseHighlightStyle(style);
  setMicHint(`${highlightStyleLabel(style)} mode`);
}

const VOICE_ACTIONS = {
  NEXT_PAGE: () => goNext(),
  PREV_PAGE: () => goPrev(),
  GOTO_PAGE: (command) => goToPage(command.page),
  SCROLL_DOWN: () => voiceScroll(1),
  SCROLL_UP: () => voiceScroll(-1),
  HIGHLIGHT: (command) => voiceHighlight(command.query, false, command.color),
  HIGHLIGHT_SENTENCE: (command) => voiceHighlight(command.query, true, command.color),
  // The same matching, written as the other two text styles whichever one
  // the highlight tool has chosen (the command names its own).
  UNDERLINE: (command) => voiceHighlight(command.query, false, command.color, "underline"),
  UNDERLINE_SENTENCE: (command) => voiceHighlight(command.query, true, command.color, "underline"),
  STRIKETHROUGH: (command) => voiceHighlight(command.query, false, command.color, "strikethrough"),
  STRIKETHROUGH_SENTENCE: (command) => voiceHighlight(command.query, true, command.color, "strikethrough"),
  // The outline panel (outline-panel.js). "show outline" and "show pages"
  // number the entries, so the next thing said can be a number.
  SHOW_OUTLINE: () => startOutlinePicker("outline"),
  SHOW_PAGES: () => startOutlinePicker("pages"),
  HIDE_OUTLINE: () => closeOutlinePanel(),
  TOGGLE_PROGRESS_BAR: () => toggleProgressBar(),
  FOCUS_MODE: () => enterFocusMode(),
  EXIT_FOCUS_MODE: () => setFocusMode(false),
  FIT_WIDTH: () => chooseZoomFit(ZOOM_FIT_WIDTH),
  FIT_PAGE: () => chooseZoomFit(ZOOM_FIT_PAGE),
  ACTUAL_SIZE: () => chooseActualSize(),
  SET_HIGHLIGHT_COLOR: async (command) => {
    await setActiveHighlightColor(command.color);
    setMicHint(`Highlight color: ${command.color}`);
  },
  // The highlight tool's settings (highlight-options.js), confirmed in the
  // mic pill since the cards that show them are usually closed.
  SET_OPACITY: async (command) => {
    await setHighlightOpacityNow(command.opacity);
    setMicHint(`Opacity ${command.opacity}%`);
  },
  THICKER: () => voiceThickness(1),
  THINNER: () => voiceThickness(-1),
  HIGHLIGHT_MODE: () => voiceStyleMode("highlight"),
  UNDERLINE_MODE: () => voiceStyleMode("underline"),
  STRIKETHROUGH_MODE: () => voiceStyleMode("strikethrough"),
  // Voice must never save as a side effect of anything else (docs/PRD.md):
  // this reaches the exact same prompt-or-save flow Ctrl+S does, rather than
  // a shortcut that skips the dialog.
  SAVE: () => promptSaveIfDirty(false),
  // The file tools (Milestone F1): each opens its dialog in Set up; inside
  // it, "confirm" and "cancel" and the numbered options take over.
  COMPRESS_FILE: () => openTool("compress"),
  MAKE_SEARCHABLE: () => openTool("searchable"),
  EXPORT_IMAGES: () => openTool("export"),
  // Organise pages, as the ⋯ menu's item and Ctrl+Shift+O open it.
  ORGANISE_PAGES: () => startOrganise(),
  // Global commands (Milestone 8.4) — available from every context, so they
  // reach the same functions their toolbar buttons do.
  UNDO: () => undo(),
  REDO: () => redo(),
  HELP: () => openCommandReference(),
  GO_HOME: () => goHome(),
  CLOSE_APP: () => closeApp(),
  // Global navigation (Milestone 8.12). Each leaves the document, so each goes
  // through the same unsaved-highlights check the back button does.
  GO_RECENT: () => goHome(),
  OPEN_SETTINGS: () => leaveReadingTo("settings.html"),
  OPEN_PDF: () => leaveReadingTo("../index.html", { openDialog: true }),
};

// While Organise pages is open: "done" and "cancel" are its two buttons, the
// selection and its action bar's Rotate and Delete are spoken as they are
// clicked, Undo and Redo are its own, and leaving goes through the same
// apply-then-ask route as the back button.
const ORGANISE_VOICE_ACTIONS = {
  ORGANISE_DONE: () => finishOrganise(),
  CANCEL: () => cancelOrganise(),
  SELECT_ALL: () => organiseVoiceSelect(true),
  SELECT_NONE: () => organiseVoiceSelect(false),
  ROTATE_LEFT: () => organiseVoiceOnSelection(() => organiseRotate(-90)),
  ROTATE_RIGHT: () => organiseVoiceOnSelection(() => organiseRotate(90)),
  DELETE_PAGES: () => organiseVoiceOnSelection(() => organiseDelete()),
  UNDO: () => organiseUndo(),
  REDO: () => organiseRedo(),
  HELP: () => openCommandReference(),
  GO_HOME: () => goHome(),
  GO_RECENT: () => goHome(),
  CLOSE_APP: () => closeApp(),
};

// The voice "toggle progress bar"; Settings > Reading is its mouse and
// keyboard route. The same stored setting either way.
async function toggleProgressBar() {
  const on = !isProgressBarVisible();
  setProgressBarVisible(on);
  setMicHint(on ? "Progress bar on" : "Progress bar off");
  try {
    await callApi("set_progress_bar", on);
  } catch (err) {
    console.error("set_progress_bar failed:", err);
  }
}

// A recognized command, routed to whatever is listening (registered in
// reading.js).
function onVoiceCommand(ev) {
  const { command } = ev.detail || {};
  // No command means the phrase matched nothing in the grammar. That is a
  // deliberate no-op: acting on a guess would move the reader somewhere they
  // didn't ask to go, and the mic pill already shows what was heard so they
  // can see why nothing happened.
  if (!command) return;
  // Nothing happens behind "Applying page changes…".
  if (isPageChangeBusy()) return;
  // DIALOG_PICK/DIALOG_UP/CANCEL/DIALOG_CONFIRM only ever arrive while the
  // Save-As dialog is the active voice context (router.py scopes them
  // there), so it is checked first — neither VOICE_ACTIONS nor the dialog
  // guard below know about it.
  if (saveAsDialog.isOpen()) {
    saveAsDialog.handleCommand(command);
    return;
  }
  // The Open dialog Organise's Insert… chooses a file in (OPEN_DIALOG).
  if (openFileDialog.isOpen()) {
    openFileDialog.handleCommand(command);
    return;
  }
  // CONFIRM_REPLACE/CANCEL: the "Replace this file?" question the Save-As
  // screen leads to (router.py's REPLACE_CONFIRM scope).
  if (ReplaceDialog.isOpen()) {
    ReplaceDialog.handleCommand(command);
    return;
  }
  // CANCEL is all the password box hears (router.py's PASSWORD_PROMPT).
  if (PasswordDialog.isOpen()) {
    PasswordDialog.handleCommand(command);
    return;
  }
  // TOOL_CONFIRM/CANCEL/DIALOG_PICK: the close gatekeeper's question while a
  // file job runs, then a tool's own dialog (router.py's FILE_TOOL).
  if (StopJobDialog.isOpen()) {
    StopJobDialog.handleCommand(command);
    return;
  }
  if (ToolDialog.isOpen()) {
    ToolDialog.handleCommand(command);
    return;
  }
  // Organise's dialogs. The danger questions: "confirm" is the danger
  // answer (discard, delete), "cancel" the safe one. Insert: "confirm"
  // inserts, a number picks the place. Extract: "confirm" extracts, "cancel"
  // cancels or stops its job.
  for (const question of [OrganiseDiscardDialog, OrganiseDeleteDialog, OrganiseInsertDialog, OrganiseExtractDialog]) {
    if (question.isOpen()) {
      question.handleCommand(command);
      return;
    }
  }
  // SAVE_COPY/SAVE_OVERWRITE/DONT_SAVE/CANCEL only ever arrive while the
  // Save-changes confirmation is the active voice context (router.py scopes
  // them there via SAVE_CONFIRM, Milestone 8.9) — checked next for the same
  // reason saveAsDialog is checked above it.
  if (SaveDialog.isOpen()) {
    SaveDialog.handleCommand(command);
    return;
  }
  // A spoken number or "cancel" while the outline or pages are numbered.
  if (handleOutlinePickerCommand(command)) return;
  // In Organise pages, its own commands and the globals that still make
  // sense there (router.py's ORGANISE context).
  const action = isOrganiseOpen() ? ORGANISE_VOICE_ACTIONS[command.intent] : VOICE_ACTIONS[command.intent];
  if (!action) return;
  // A dialog owns the view while it is open; a page turn behind it would be
  // invisible and would apply to a document the reader is mid-decision about.
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  action(command);
}
