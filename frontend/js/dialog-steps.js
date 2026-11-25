// One dialog in steps (docs/DESIGN_SYSTEM.md § "File tools" → "Stepped
// dialog"): Home's Tools page asks which PDF and then shows the tool in the
// same card, so the dim behind it never lifts between the two. How the card
// moves between them is js/dialog-motion.js's.
//
// The step changes the moment Continue is pressed: nothing waits for Python.
// What the tool needs to know about the PDF starts being read when it is
// *selected* in the chooser (`prefetch`), and whatever hasn't arrived by
// Continue shows as a placeholder the size of what it stands for, filled in
// where it is. A PDF that turns out to be unreadable (or whose password is
// cancelled) takes the dialog back to the chooser, saying why.
//
// `createToolSteps` is the part ToolDialog and SplitDialog share: the
// "choose a PDF" step (tools-page.js's `createPdfChooser`), Continue, Back
// (the footer's left end, and Alt+←) and what is announced. Each dialog
// keeps its own state and rendering.
"use strict";

// Alt+← is Back, wherever the focus is. On a Mac, Option+← in a field being
// typed in is "a word left", so there it stays the caret's (an option card or
// a checkbox still goes Back); Windows fields do nothing with Alt+←.
const STEP_BACK_MAC = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform || "");
const TEXT_INPUT_TYPES = new Set(["text", "search", "number", "email", "url", "tel", "password", ""]);

function isTextEntry(el) {
  if (!el || !el.tagName) return false;
  if (el.isContentEditable || el.tagName === "TEXTAREA") return true;
  return el.tagName === "INPUT" && TEXT_INPUT_TYPES.has(String(el.type || "").toLowerCase());
}

function isStepBackKey(ev, mac = STEP_BACK_MAC) {
  if (ev.key !== "ArrowLeft" || !ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return false;
  return !(mac && isTextEntry(ev.target));
}

// The two steps of a tool dialog opened from the Tools page. The dialog
// gives its card's parts and these:
//   toolName              "Compress", for the chooser's title
//   browse()              the Open dialog: Promise<path | null>
//   stacked(task)         run another dialog (`task`) over this one
//   prefetch(path)        a PDF was chosen in the list: start reading it
//   enterTool(path, entry) make the tool's state for `path` (at once; what
//                         is still to come shows as placeholders). `entry`
//                         is what the chooser knows (its page count)
//   renderTool()          draw the tool step into the card
//   entered()             after the tool step is drawn: fill it in
//   leaveTool()           Back: stop what nobody waits for any more
//   focusTool()           focus the tool step's first control
//   toolTitle()           what is announced on arriving there
//   currentSource()       the PDF the tool step has, or null
//   close()               the whole dialog
function createToolSteps(options) {
  const { card, titleEl } = options;
  const chooser = createPdfChooser({
    toolName: options.toolName,
    browse: () => options.stacked(options.browse),
    onSelect: (path) => options.prefetch(path),
  });
  let step = "choose";

  async function begin() {
    step = "choose";
    await chooser.load();
  }

  function cancel() {
    options.close();
  }

  function render() {
    if (step === "choose") {
      const { badge, titleEl: title, descEl, bodyEl, footerEl } = options;
      chooser.render({ badge, titleEl: title, descEl, bodyEl, footerEl, onContinue: go, onCancel: cancel });
    } else {
      options.renderTool();
    }
  }

  // Continue: the tool, at once, in the same card. Pressing it again (a
  // double click) finds the step already changed.
  function go(path) {
    if (step !== "choose" || !path) return;
    step = "tool";
    options.enterTool(path, chooser.entryFor(path));
    swapDialogStep(card, render, { direction: 1 });
    options.focusTool();
    announceDialogStep(card, options.toolTitle());
    options.entered();
  }

  function back() {
    if (step !== "tool") return;
    step = "choose";
    options.leaveTool();
    chooser.select(options.currentSource());
    swapDialogStep(card, render, { direction: -1 });
    chooser.focus();
    announceDialogStep(card, titleEl.textContent);
  }

  // The tool step's Back: secondary, at the footer's left end.
  function backButton() {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary dialog-footer-back";
    button.textContent = "Back";
    button.title = "Back to choosing a PDF  (Alt+←)";
    button.addEventListener("click", back);
    return button;
  }

  return {
    begin,
    render,
    back,
    backButton,
    isChoosing: () => step === "choose",
    cancel,
    focusChooser: () => chooser.focus(),
    // Keys while the chooser is shown: Enter is Continue.
    handleChooserKey: (ev) => chooser.handleKey(ev),
    handleChooserCommand: (command) => chooser.handleCommand(command),
  };
}
