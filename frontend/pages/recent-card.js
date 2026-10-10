// The grid view's card: thumbnail, name, when and how long, and the two actions
// in the thumbnail's top-right corner — the favorite star and, in Recent,
// Remove. home.js owns the data and what each action does; the list view's rows
// are recent-list.js's. Loaded before home.js, whose globals (homeSection,
// toggleFavorite, removeEntry, openPath) it reads only when a card is built.

// Remove-from-Recent (Milestone 8.9, docs/DESIGN_SYSTEM.md). A real <button>,
// not a click handler on a styled <div>: the accessibility baseline's "every
// interactive element is a real button" rule, and what gives it independent
// keyboard focus. stopPropagation keeps this click from also bubbling into the
// card's own "open" handler. Grid only: the list view reaches the same removal
// through its row "⋯" menu (recent-list.js). Removal always goes through
// removeEntry(), so the confirmation and the Undo toast are the same as by voice.
function buildRemoveBtn(entry) {
  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "card-action-btn recent-remove-btn";
  removeBtn.dataset.icon = "remove";
  removeBtn.dataset.tooltip = "Remove from Recent";
  removeBtn.setAttribute("aria-label", `Remove ${entry.name} from Recent`);
  removeBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    removeEntry(entry);
  });
  mountIcon(removeBtn, "remove");
  return removeBtn;
}

// The chips over the thumbnail's corner: Remove (Recent only — a favorite may
// not be listed there at all, and unstarring is the star's job), then the star
// at the very corner, where a favorited one stays visible at rest.
function buildCardActions(entry) {
  const actions = document.createElement("div");
  actions.className = "card-actions";
  if (homeSection === "recent") actions.appendChild(buildRemoveBtn(entry));
  const favBtn = buildFavoriteBtn(entry, entry.name, toggleFavorite);
  favBtn.classList.add("card-action-btn");
  mountIcon(favBtn, favBtn.dataset.icon);
  actions.appendChild(favBtn);
  return actions;
}

function buildCard(entry) {
  const card = document.createElement("div");
  // .recent-item: what the numbered-overlay picker badges, in either view
  // (list rows carry it too).
  card.className = "recent-card recent-item card-lift";
  // docs/DESIGN_SYSTEM.md's accessibility baseline ("never a clickable
  // div") can't be met with a literal <button> here, since the card
  // also contains its own nested, independently-focusable buttons -- a
  // <button> cannot contain another <button>. This is the standard fallback
  // for that exact composite-control shape: role="button" + tabindex so it is
  // reachable and announced like one, plus an Enter/Space handler so keyboard
  // activation matches click.
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.setAttribute("aria-label", `Open ${entry.name}`);

  const thumb = document.createElement("div");
  thumb.className = "recent-thumb";
  mountThumbnail(thumb, entry);
  thumb.appendChild(buildCardActions(entry));
  card.appendChild(thumb);

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = entry.name;
  card.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "meta";
  const noun = entry.page_count === 1 ? "page" : "pages";
  meta.textContent = entry.page_count
    ? `${entry.relative_time} · ${entry.page_count} ${noun}`
    : entry.relative_time;
  card.appendChild(meta);

  card.addEventListener("click", () => openPath(entry.path));
  card.addEventListener("keydown", (ev) => {
    // Enter or Space on a nested button is that button's, not "open".
    if (ev.target !== card) return;
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openPath(entry.path);
    }
  });
  return card;
}
