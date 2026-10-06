// The "What can I say?" list (Milestone 8, made one shared list in 8.13).
//
// docs/PRD.md asks for a categorized command reference, and the reason it
// exists is recognition over recall: a reader should never have to remember
// a phrasing, only recognize one. Voice here is a closed grammar, not language
// understanding, so only the phrases in the grammar work and the list says so.
// That is the one opinion in this file:
//
//   * It writes no command text. Every phrase, description and shortcut it
//     shows comes from the payload (src/lector/features/voice/reference.py,
//     built from the grammar and router tables), so it cannot list a phrase the
//     recognizer would reject or leave one out. The only words here are the
//     list's own chrome (title, the "say these exactly" line, the empty state).
//   * It is the same list on every screen: the same markup, styling and
//     sections. A screen differs only in which sections the payload puts first,
//     and in what it tells this file about itself through
//     configureCommandReference() below.

// The list's markup lives here, once, rather than being copied into every page
// that offers "What can I say?" (Home, Settings and Reading). Static, with no
// interpolated or user-supplied data, so nothing here can be injected into.
const REFERENCE_MARKUP = `
  <div class="dialog-scrim reference-scrim voice-dialog" id="referenceDialog" hidden>
    <div class="dialog-card reference-card" id="referenceCard" role="dialog"
         aria-modal="true" aria-labelledby="referenceTitle" tabindex="-1">
      <div class="reference-band" id="referenceBand">
        <div class="dialog-header reference-header">
          <div class="dialog-badge" data-icon="logo"></div>
          <div>
            <p class="dialog-title" id="referenceTitle">What can I say?</p>
            <p class="dialog-desc">
              Say these phrases exactly. Lector only recognizes the commands
              listed here. Every command also has a button or shortcut.
            </p>
          </div>
          <button class="reference-close" id="referenceCloseBtn" title="Close  (Esc)"
                  aria-label="Close">&times;</button>
        </div>
        <div class="reference-search">
          <span class="reference-search-icon" data-icon="search"></span>
          <input type="search" id="referenceSearch" autocomplete="off"
                 placeholder="Search commands" aria-label="Search commands">
        </div>
      </div>

      <div class="reference-body" id="referenceBody">
        <div class="reference-sections" id="referenceSections"></div>
        <div class="reference-empty" id="referenceEmpty" hidden>
          <p class="reference-empty-text" id="referenceEmptyText"></p>
          <button class="btn btn-secondary" id="referenceClearBtn" type="button">Clear</button>
        </div>
      </div>

      <div class="dialog-footer reference-footer">
        <span class="hint" id="referenceActivation"></span>
        <button class="btn" id="referenceGotItBtn">Got it</button>
      </div>
    </div>
  </div>`;
if (!document.getElementById("referenceDialog")) {
  document.body.insertAdjacentHTML("beforeend", REFERENCE_MARKUP);
}

const referenceDialog = document.getElementById("referenceDialog");
const referenceCard = document.getElementById("referenceCard");
const referenceBand = document.getElementById("referenceBand");
const referenceBody = document.getElementById("referenceBody");
const referenceSections = document.getElementById("referenceSections");
const referenceEmpty = document.getElementById("referenceEmpty");
const referenceEmptyText = document.getElementById("referenceEmptyText");
const referenceClearBtn = document.getElementById("referenceClearBtn");
const referenceSearch = document.getElementById("referenceSearch");
// The `.dictating` cue paints the bordered wrapper around the input, the
// same element the focus ring styles — not the bare `<input>`, which carries
// no border of its own to color.
const referenceSearchWrap = referenceSearch.closest(".reference-search");
const referenceActivation = document.getElementById("referenceActivation");
const referenceCloseBtn = document.getElementById("referenceCloseBtn");
const referenceGotItBtn = document.getElementById("referenceGotItBtn");

// What a screen can tell the list about itself, set by the page with
// configureCommandReference(). Both are optional and both are read when the list
// opens, so they reflect the screen as it is then:
//   ranges()       -> { page, number }: the real upper bounds for "go to page
//                     {page}" and "open number {number}", when known. Without
//                     one the list shows "1–N".
//   focusSection() -> a section id to show first (Home's Favorites screen asks
//                     for "favorites"), on top of the order the payload already
//                     puts this screen's own sections in.
let referenceHooks = { ranges: () => ({}), focusSection: () => null };
function configureCommandReference(hooks) {
  referenceHooks = { ...referenceHooks, ...hooks };
}

// A phrase is a pattern when it has a variable: "{page}" and "{number}" are
// numbers (shown as a range), "{words}" is whatever is on the page.
const REFERENCE_WORDS_LABEL = "the words you see";
const REFERENCE_UNKNOWN_MAX = "N";

function placeholderText(name, ranges) {
  if (name === "words") return REFERENCE_WORDS_LABEL;
  const max = Number(ranges[name]);
  return `1–${max > 0 ? max : REFERENCE_UNKNOWN_MAX}`;
}

// The payload's phrases are data, so they go in as text nodes (never markup).
function phraseElement(phrase, ranges) {
  const el = document.createElement("span");
  el.className = "reference-phrase";
  el.append("“");
  phrase.split(/(\{\w+\})/).forEach((part) => {
    const variable = part.match(/^\{(\w+)\}$/);
    if (!variable) {
      if (part) el.append(part);
      return;
    }
    const slot = document.createElement("span");
    slot.className = "reference-var";
    slot.textContent = placeholderText(variable[1], ranges);
    el.append(slot);
  });
  el.append("”");
  return el;
}

function plainPhrase(phrase, ranges) {
  return phrase.replace(/\{(\w+)\}/g, (_, name) => placeholderText(name, ranges));
}

// Fetched once per session, not once per open: the grammar cannot change
// while the app is running, and re-asking would put a bridge round-trip
// between the reader's click and the list appearing. What the screen knows
// (ranges, focus) is applied at render time, so it is not stale.
let referencePanel = null;
// What had focus before the list took it, so it can be handed back — a
// reader who opened this from the rail button should land back on the rail
// button, not at the top of the document.
let referenceOpener = null;

function commandCard(command, section, ranges) {
  const card = document.createElement("div");
  card.className = "reference-card-row";

  const phrases = document.createElement("p");
  phrases.className = "reference-phrases";
  // Every phrasing the recognizer accepts, joined with "/" so several phrasings
  // read as alternatives rather than as a sequence to say in order.
  command.phrases.forEach((phrase, i) => {
    if (i > 0) phrases.append(" / ");
    phrases.append(phraseElement(phrase, ranges));
  });

  const description = document.createElement("p");
  description.className = "reference-desc";
  description.textContent = command.description;

  const equivalent = document.createElement("p");
  equivalent.className = "reference-equivalent";
  equivalent.textContent = command.equivalent;

  card.append(phrases, description, equivalent);
  // Everything the filter should match — the phrases, what the command does, its
  // button or shortcut, and its section's name — lowercased once here rather than
  // on every keystroke.
  card.dataset.haystack = [
    ...command.phrases.map((phrase) => plainPhrase(phrase, ranges)),
    command.description,
    command.equivalent,
    section.title,
  ].join(" ").toLowerCase();
  return card;
}

function sectionElement(section, ranges) {
  const el = document.createElement("section");
  el.className = "reference-section";
  el.dataset.sectionId = section.id;

  const title = document.createElement("h3");
  title.className = "reference-section-title";
  title.textContent = section.title;
  el.append(title);

  // Said once under the heading when this screen cannot act on the section, so
  // the list stays complete without promising a command that does nothing here.
  if (!section.available) {
    const note = document.createElement("p");
    note.className = "reference-section-note";
    note.textContent = section.scope_note;
    el.append(note);
  }

  const list = document.createElement("div");
  list.className = "reference-list";
  section.commands.forEach((command) => list.append(commandCard(command, section, ranges)));
  el.append(list);
  return el;
}

// The payload's order puts this screen's own sections first; a page may ask for
// one of them to lead (Favorites on Home).
function orderedSections(panel) {
  const focus = referenceHooks.focusSection();
  const lead = panel.sections.find((section) => section.id === focus && section.available);
  return lead ? [lead, ...panel.sections.filter((section) => section !== lead)] : panel.sections;
}

function renderReference(panel) {
  const ranges = referenceHooks.ranges() || {};
  referenceSections.replaceChildren(
    ...orderedSections(panel).map((section) => sectionElement(section, ranges)),
  );

  // The footer names both ways of starting. The phrase itself comes from the
  // payload rather than being typed here, so that it keeps exactly one home
  // (src/lector/features/voice/wake.py).
  referenceActivation.replaceChildren();
  const hold = document.createElement("span");
  hold.textContent = `Hold ${panel.push_to_talk_key} to talk, or say `;
  const phrase = document.createElement("strong");
  phrase.textContent = `“${panel.wake_phrase_display}”`;
  const first = document.createElement("span");
  first.textContent = " first.";
  referenceActivation.append(hold, phrase, first);
}

function showReferenceEmpty(message, { clearable }) {
  referenceEmptyText.textContent = message;
  referenceClearBtn.hidden = !clearable;
  referenceEmpty.hidden = false;
}

function applyReferenceFilter() {
  const query = referenceSearch.value.trim();
  const needle = query.toLowerCase();
  let shown = 0;

  referenceSections.querySelectorAll(".reference-section").forEach((section) => {
    let visible = 0;
    section.querySelectorAll(".reference-card-row").forEach((card) => {
      const matches = !needle || card.dataset.haystack.includes(needle);
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    // A heading with nothing under it reads as a section that has been
    // emptied out rather than one that simply has no match here.
    section.hidden = visible === 0;
    shown += visible;
  });

  if (shown > 0) {
    referenceEmpty.hidden = true;
  } else if (referencePanel) {
    // The query goes in via textContent, so whatever was typed is shown as text.
    showReferenceEmpty(`No commands match “${query}”.`, { clearable: true });
  }
  // A new set of results starts from the top.
  referenceBody.scrollTop = 0;
}

// A divider under the header band appears once the list has scrolled beneath it
// (and not before, so an unscrolled list has no stray line).
function updateReferenceDivider() {
  referenceBand.classList.toggle("is-scrolled", referenceBody.scrollTop > 0);
}

// The panel's claim on the recognizer's grammar (voice.js createVoiceScope):
// entered on open, left on close, serialized so a quick close can never leave
// the screen's own commands dead.
const referenceScope = createVoiceScope("reference");
// True from the moment an open starts until the dialog is on screen. The first
// open awaits the command list, during which `referenceDialog.hidden` is still
// true, so "what can I say" heard (or the button clicked) a second time in that
// gap would otherwise start a second open.
let referenceOpening = false;

// The one way in, whatever the entry point: the sidebar link, the reader's
// buttons and the spoken "what can I say" all call this.
async function openCommandReference() {
  // "What can I say?" is also a global voice command, so it is heard while the
  // list is already open or opening.
  if (!referenceDialog.hidden || referenceOpening) return;
  referenceOpening = true;
  referenceOpener = document.activeElement;
  let failure = null;
  if (!referencePanel) {
    try {
      referencePanel = await callApi("get_command_reference");
    } catch (err) {
      // Failing to open a help list must not take the reader's place in the
      // document with it, so this says so plainly and stops.
      failure = `Couldn't load the command list: ${err}`;
    }
  }
  referenceSearch.value = "";
  if (referencePanel) {
    renderReference(referencePanel);
    applyReferenceFilter();
  } else {
    referenceSections.replaceChildren();
    showReferenceEmpty(failure, { clearable: false });
  }
  updateReferenceDivider();
  referenceDialog.hidden = false;
  // A modal scope: while the list is up the recognizer hears only the list's
  // own phrases (close it, start a search), not the screen beneath — "next
  // page" must not turn pages behind it. Closing restores whatever was
  // listening before, which is why this is pushed rather than set.
  // The list still works by mouse and keyboard if this fails; only voice
  // scoping is lost (and createVoiceScope reports it).
  referenceScope.enter();
  referenceOpening = false;
  await mountIcons(referenceDialog);
  // Focus lands on the dialog itself, not the search box: a text field always
  // matches :focus-visible, so focusing it would draw its ring just because the
  // list opened. Tab reaches the search box, and "/" or Ctrl+F jumps to it.
  referenceCard.focus();
}

function closeCommandReference() {
  if (referenceDialog.hidden) return;
  // Leaving the panel with dictation still active must not leave the voice
  // context stuck on `dictation` — nothing after this point would ever pop
  // it back, and every subsequent command anywhere in the app would be
  // silently swallowed as "just dictated text" (see the module docstring in
  // src/lector/features/voice/router.py).
  if (dictationActive) endDictation({ restore: false });
  referenceDialog.hidden = true;
  referenceScope.leave();
  if (referenceOpener && referenceOpener.focus) referenceOpener.focus();
  referenceOpener = null;
}

referenceCloseBtn.addEventListener("click", closeCommandReference);
referenceGotItBtn.addEventListener("click", closeCommandReference);
referenceSearch.addEventListener("input", applyReferenceFilter);
referenceBody.addEventListener("scroll", updateReferenceDivider, { passive: true });
referenceClearBtn.addEventListener("click", () => {
  referenceSearch.value = "";
  applyReferenceFilter();
  referenceSearch.focus();
});

// Clicking the dimmed area dismisses, matching the save dialog: the list is
// a reference, so leaving it can never cost the reader anything.
referenceDialog.addEventListener("click", (ev) => {
  if (ev.target === referenceDialog) closeCommandReference();
});

function isTypingTarget(el) {
  return Boolean(el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)));
}

// "?" is the conventional key for "what are my options", and the list's own
// promise of a keyboard equivalent for everything applies to opening it too. It
// lives here, not in each page's key handler, so every screen has it (and the
// reader's help button's "(?)" tooltip is true everywhere). Never while typing,
// and never on top of another dialog.
function openOnQuestionMark(ev) {
  if (ev.key !== "?" || ev.ctrlKey || ev.metaKey || ev.altKey || isTypingTarget(ev.target)) return;
  if (document.querySelector(".dialog-scrim:not([hidden])")) return;
  ev.preventDefault();
  openCommandReference();
}

// Esc closes it here rather than in the reading view's key handler, so the
// list owns its own dismissal wherever it is used. Capture phase, because
// the search field would otherwise swallow Esc to clear itself first.
document.addEventListener(
  "keydown",
  (ev) => {
    if (referenceDialog.hidden) {
      openOnQuestionMark(ev);
      return;
    }
    if (ev.key === "Escape") {
      ev.stopPropagation();
      ev.preventDefault();
      closeCommandReference();
      return;
    }
    // The same two keys that jump to the search field in the library.
    const toSearch = ev.key === "/" || ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "f");
    if (toSearch && ev.target !== referenceSearch) {
      ev.stopPropagation();
      ev.preventDefault();
      referenceSearch.focus();
    }
  },
  true,
);

// --- Dictation mode (Milestone 8.8) ---------------------------------------
//
// docs/ARCHITECTURE.md's "Dictation mode": the one free-text field that
// exists today is this panel's own search box, and voice fills it the same
// way a keyboard does — by typing into it, not by matching a fixed phrase.
// "start search" pushes a `dictation` router context (open-vocabulary
// recognition, src/lector/features/voice/engine.py); "done" keeps whatever
// was dictated and pops back; "cancel"/"never mind" discards it and pops
// back to what the field held before. Only *final* recognition results are
// ever applied to the field — this deliberately does not stream partial
// results into it character-by-character, because a partial result cannot
// yet be told apart from the start of "done"/"cancel", and writing a
// half-formed guess into the reader's search query would be worse than
// waiting the extra moment for the final one.
//
// Dictation is a second modal scope pushed on top of the panel's own
// (Milestone 8.12), so ending it pops back to the panel and closing the panel
// pops back to whichever screen opened it — Home, Settings or Reading — rather
// than a context hardcoded here.
let dictationActive = false;
let dictationPreviousValue = "";
const dictationScope = createVoiceScope("dictation");

function startDictation() {
  if (dictationActive) return;
  dictationActive = true;
  dictationPreviousValue = referenceSearch.value;
  referenceSearchWrap.classList.add("dictating");
  dictationScope.enter().then((taken) => {
    if (taken) return;
    // A failed context switch must not leave the field looking like it is
    // listening when it is not.
    dictationActive = false;
    referenceSearchWrap.classList.remove("dictating");
  });
}

function endDictation({ restore }) {
  if (!dictationActive) return;
  dictationActive = false;
  referenceSearchWrap.classList.remove("dictating");
  if (restore) {
    referenceSearch.value = dictationPreviousValue;
    applyReferenceFilter();
  }
  dictationScope.leave();
}

// A separate `lector:command` listener from reading.js's own: that one bails
// out immediately on `!command` (it has nothing to do with plain text), but
// dictated text arrives as exactly that — a final result with `command:
// null` — so this panel needs its own subscriber to see the raw `text`
// field voice.js's `lector:voice` handler re-broadcasts alongside it.
window.addEventListener("lector:command", (ev) => {
  if (referenceDialog.hidden) return;
  const { text, command } = ev.detail || {};
  if (!dictationActive) {
    if (command && command.intent === "START_DICTATION") startDictation();
    // The panel's own voice scope ends with "got it" / "go back" / "cancel".
    else if (command && command.intent === "CANCEL") closeCommandReference();
    return;
  }
  if (command && command.intent === "STOP_DICTATION") {
    endDictation({ restore: false });
    return;
  }
  if (command && command.intent === "CANCEL") {
    endDictation({ restore: true });
    return;
  }
  // Anything else heard while dictating is the dictated text itself —
  // router.py's DICTATION branch returns `command: null` for it precisely so
  // it reaches here as plain text rather than being matched against any
  // fixed-phrase table.
  if (text) {
    referenceSearch.value = text;
    applyReferenceFilter();
  }
});
