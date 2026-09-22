// The "What can I say?" panel (Milestone 8).
//
// docs/PRD.md asks for a categorized command reference, and the reason it
// exists is recognition over recall: a reader should never have to remember
// a phrasing, only recognize one. Three things follow from that, and they
// are the only opinions in this file:
//
//   * The contents come from Python (reference.panel()), which derives them
//     from the grammar. Retyping the phrases here would let the panel drift
//     into advertising commands the recognizer does not accept.
//   * Every entry shows its keyboard or mouse equivalent, because the panel
//     claims in its own subtitle that each command has one, and a claim the
//     reader cannot check is not worth making.
//   * It filters as you type. The list is short today and will not stay
//     short, and a list you can narrow never has to be memorized.

const referenceDialog = document.getElementById("referenceDialog");
const referenceGrid = document.getElementById("referenceGrid");
const referenceEmpty = document.getElementById("referenceEmpty");
const referenceSearch = document.getElementById("referenceSearch");
const referenceActivation = document.getElementById("referenceActivation");
const referenceCloseBtn = document.getElementById("referenceCloseBtn");
const referenceGotItBtn = document.getElementById("referenceGotItBtn");

// Fetched once per session, not once per open: the grammar cannot change
// while the app is running, and re-asking would put a bridge round-trip
// between the reader's click and the panel appearing.
let referenceLoaded = false;
// What had focus before the panel took it, so it can be handed back — a
// reader who opened this from the rail button should land back on the rail
// button, not at the top of the document.
let referenceOpener = null;

function commandCard(command) {
  const card = document.createElement("div");
  card.className = "reference-card-row";

  const phrases = document.createElement("p");
  phrases.className = "reference-phrases";
  // Quoted and joined with "/" so several phrasings read as alternatives
  // rather than as a sequence to say in order.
  phrases.textContent = command.examples.map((ex) => `“${ex}”`).join(" / ");

  const description = document.createElement("p");
  description.className = "reference-desc";
  description.textContent = command.description;

  const equivalent = document.createElement("p");
  equivalent.className = "reference-equivalent";
  equivalent.textContent = command.equivalent;

  card.append(phrases, description, equivalent);
  // Everything the filter should match, lowercased once here rather than on
  // every keystroke.
  card.dataset.haystack =
    `${command.examples.join(" ")} ${command.description} ${command.equivalent}`.toLowerCase();
  return card;
}

function categoryColumn(category) {
  const column = document.createElement("section");
  column.className = "reference-category";

  const title = document.createElement("h3");
  title.className = "reference-category-title";
  title.textContent = category.title;

  const list = document.createElement("div");
  list.className = "reference-list";
  category.commands.forEach((command) => list.append(commandCard(command)));

  column.append(title, list);
  return column;
}

function renderReference(panel) {
  referenceGrid.replaceChildren(...panel.categories.map(categoryColumn));

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

function applyReferenceFilter() {
  const needle = referenceSearch.value.trim().toLowerCase();
  let shown = 0;

  referenceGrid.querySelectorAll(".reference-category").forEach((column) => {
    let visibleInColumn = 0;
    column.querySelectorAll(".reference-card-row").forEach((card) => {
      const matches = !needle || card.dataset.haystack.includes(needle);
      card.hidden = !matches;
      if (matches) visibleInColumn += 1;
    });
    // A heading with nothing under it reads as a category that has been
    // emptied out rather than one that simply has no match here.
    column.hidden = visibleInColumn === 0;
    shown += visibleInColumn;
  });

  referenceEmpty.hidden = shown > 0;
}

async function openCommandReference() {
  referenceOpener = document.activeElement;
  if (!referenceLoaded) {
    try {
      renderReference(await callApi("get_command_reference"));
      referenceLoaded = true;
    } catch (err) {
      // Failing to open a help panel must not take the reader's place in the
      // document with it, so this says so plainly and stops.
      referenceGrid.replaceChildren();
      referenceEmpty.hidden = false;
      referenceEmpty.textContent = `Couldn't load the command list: ${err}`;
    }
  }
  referenceSearch.value = "";
  applyReferenceFilter();
  referenceDialog.hidden = false;
  await mountIcons(referenceDialog);
  referenceSearch.focus();
}

function closeCommandReference() {
  if (referenceDialog.hidden) return;
  referenceDialog.hidden = true;
  if (referenceOpener && referenceOpener.focus) referenceOpener.focus();
  referenceOpener = null;
}

referenceCloseBtn.addEventListener("click", closeCommandReference);
referenceGotItBtn.addEventListener("click", closeCommandReference);
referenceSearch.addEventListener("input", applyReferenceFilter);

// Clicking the dimmed area dismisses, matching the save dialog: the panel is
// a reference, so leaving it can never cost the reader anything.
referenceDialog.addEventListener("click", (ev) => {
  if (ev.target === referenceDialog) closeCommandReference();
});

// Esc closes it here rather than in the reading view's key handler, so the
// panel owns its own dismissal wherever it is used. Capture phase, because
// the search field would otherwise swallow Esc to clear itself first.
document.addEventListener(
  "keydown",
  (ev) => {
    if (ev.key !== "Escape" || referenceDialog.hidden) return;
    ev.stopPropagation();
    ev.preventDefault();
    closeCommandReference();
  },
  true,
);
