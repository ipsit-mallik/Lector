// Organise pages' two danger questions (danger-dialog.js):
//
// * "Discard page changes?" — Cancel (or Esc, or the spoken "cancel") while
//   the draft has changes. Keep editing or Discard changes.
// * "Delete 3 pages?" — Delete (the button, the Delete key, the spoken
//   "delete pages"). The pages it would delete as small thumbnails
//   with their numbers, Cancel or "Delete 3 pages".
//
// Both resolve true for the danger answer. "confirm" is the danger button,
// "cancel" the safe one.
"use strict";

const ORGANISE_DELETE_THUMBS = 6;

const OrganiseDiscardDialog = (() => {
  const dialog = createDangerDialog({
    id: "organiseDiscard",
    icon: "undo",
    safeLabel: "Keep editing",
    voiceHint: "Say “confirm” to discard, or “cancel” to keep editing.",
  });

  // `changes` is the number of actions in the draft.
  function open({ changes }) {
    return dialog.open({
      titleText: "Discard page changes?",
      descText: changes === 1
        ? "Your change to the pages won’t be applied. The document stays as it was."
        : `Your ${changes} changes to the pages won’t be applied. The document stays as it was.`,
      infoText: "To keep them, choose Keep editing, then Done.",
      dangerLabel: "Discard changes",
    });
  }

  return { open, isOpen: dialog.isOpen, handleCommand: dialog.handleCommand };
})();

// "pages 4–6", "page 4", or "5 pages" for a scattered selection: how the
// delete confirmation (and Extract's name) speak of a set of page numbers.
function describePageNumbers(numbers) {
  const sorted = [...numbers].sort((a, b) => a - b);
  if (sorted.length === 1) return `page ${sorted[0]}`;
  const run = sorted[sorted.length - 1] - sorted[0] + 1 === sorted.length;
  return run ? `pages ${sorted[0]}–${sorted[sorted.length - 1]}` : `${sorted.length} pages`;
}

const OrganiseDeleteDialog = (() => {
  const dialog = createDangerDialog({
    id: "organiseDelete",
    icon: "remove",
    safeLabel: "Cancel",
    voiceHint: "Say “confirm” to delete, or “cancel”.",
  });

  // One small page: the thumbnail fitted into 36×46 and turned as the grid
  // shows it, its number under it.
  function smallPage({ thumb, turn, number }) {
    const item = document.createElement("span");
    item.className = "organise-delete-page";
    const box = document.createElement("span");
    box.className = "organise-delete-box";
    const paper = document.createElement("span");
    paper.className = "organise-delete-paper";
    paper.style.setProperty("--turn", `${turn}deg`);
    if (thumb) {
      const scale = Math.min(36 / thumb.width, 46 / thumb.height);
      paper.style.width = `${Math.round(thumb.width * scale)}px`;
      paper.style.height = `${Math.round(thumb.height * scale)}px`;
      const img = document.createElement("img");
      img.alt = "";
      img.src = thumb.src;
      paper.appendChild(img);
    }
    box.appendChild(paper);
    const label = document.createElement("span");
    label.className = "organise-delete-num";
    label.textContent = String(number);
    item.append(box, label);
    return item;
  }

  // `pages` is what the grid knows of each selected page (`pageView`),
  // `docName` the document's title.
  function open({ pages, docName }) {
    const count = pages.length;
    const what = describePageNumbers(pages.map((page) => page.number));
    const strip = document.createElement("div");
    strip.className = "organise-delete-strip";
    strip.setAttribute("aria-hidden", "true");
    pages.slice(0, ORGANISE_DELETE_THUMBS).forEach((page) => strip.appendChild(smallPage(page)));
    if (count > ORGANISE_DELETE_THUMBS) {
      const more = document.createElement("span");
      more.className = "organise-delete-more";
      more.textContent = `+${count - ORGANISE_DELETE_THUMBS} more`;
      strip.appendChild(more);
    }
    const subject = what.charAt(0).toUpperCase() + what.slice(1);
    const pagesWord = count === 1 ? "1 page" : `${count} pages`;
    return dialog.open({
      titleText: `Delete ${pagesWord}?`,
      descText: `${subject} ${count === 1 ? "is" : "are"} removed from ${docName}.`,
      content: strip,
      infoText: "Undo brings them back. The PDF on disk doesn’t change until you save.",
      dangerLabel: `Delete ${pagesWord}`,
    });
  }

  return { open, isOpen: dialog.isOpen, handleCommand: dialog.handleCommand };
})();
