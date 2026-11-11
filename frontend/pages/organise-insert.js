// Insert… in Organise pages (docs/DESIGN_SYSTEM.md § "Organise pages" →
// "Insert"): where the pages of the file just chosen go. The source row
// (its first page, its name, how many pages) with Choose… to pick another
// file, the three places as radio cards (only "At the end" when nothing is
// selected), and "Insert N pages".
//
// It doesn't choose files itself: `open()` is handed the file already
// chosen and a `choose()` that asks for another (the Open dialog, and a
// protected PDF's password; organise.js), plus `forget()` for a file that
// was chosen and then not used. Resolves `{ source, name, pages, gap }`, or
// null.
//
// A modal voice scope of the file tools' grammar: "confirm" inserts,
// "cancel" cancels, and a number picks that place.
"use strict";

const OrganiseInsertDialog = (() => {
  let scrim = null;
  let card = null;
  let thumbEl = null;
  let nameEl = null;
  let metaEl = null;
  let chooseBtn = null;
  let placesEl = null;
  let insertBtn = null;
  let scope = null;
  let releaseTrap = null;
  let settle = null;
  // { source, positions, total, place, choose, forget } while open.
  let s = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function build() {
    if (scrim) return;
    scrim = el("div", "dialog-scrim dialog-scrim--enter voice-dialog");
    scrim.id = "organiseInsert";
    scrim.hidden = true;
    card = el("div", "dialog-card dialog-card--tool");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "organiseInsertTitle");
    card.setAttribute("aria-describedby", "organiseInsertDesc");

    const body = el("div", "dialog-body");
    const header = el("div", "dialog-header");
    const badge = el("div", "dialog-badge");
    badge.dataset.icon = "insert";
    badge.setAttribute("aria-hidden", "true");
    const text = el("div");
    const title = el("p", "dialog-title", "Insert pages");
    title.id = "organiseInsertTitle";
    const desc = el("p", "dialog-desc", "Adds pages from another PDF or from images.");
    desc.id = "organiseInsertDesc";
    text.append(title, desc);
    header.append(badge, text);

    const source = el("div", "organise-insert-source");
    thumbEl = el("span", "organise-insert-thumb");
    const label = el("span", "organise-insert-label");
    nameEl = el("span", "organise-insert-name");
    metaEl = el("span", "organise-insert-meta");
    label.append(nameEl, metaEl);
    chooseBtn = el("button", "btn btn-secondary organise-insert-choose", "Choose…");
    chooseBtn.type = "button";
    chooseBtn.title = "Insert pages from a different file";
    chooseBtn.addEventListener("click", () => chooseAnother());
    source.append(thumbEl, label, chooseBtn);

    placesEl = el("div", "tool-options tool-options--compact organise-insert-places");
    placesEl.setAttribute("aria-label", "Where");
    initRadioCards(placesEl, { selectOnArrow: true });
    body.append(header, source, placesEl);

    const footer = el("div", "dialog-footer");
    const cancelBtn = el("button", "btn btn-secondary", "Cancel");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", () => close(null));
    insertBtn = el("button", "btn");
    insertBtn.type = "button";
    insertBtn.addEventListener("click", () => confirm());
    footer.append(cancelBtn, insertBtn);

    card.append(body, footer);
    scrim.appendChild(card);
    document.body.appendChild(scrim);
    mountIcons(scrim);
    scope = createVoiceScope("file_tool");
    // Esc is Cancel, and goes no further (not to Organise behind).
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape" || !isOpen()) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      close(null);
    }, true);
  }

  // The places this selection allows, each with where that is now.
  function places() {
    const { positions, total } = s;
    const list = [];
    if (positions.length) {
      const first = positions[0];
      const last = positions[positions.length - 1];
      list.push({ value: "before", title: "Before the selection", meta: `before page ${first + 1}`, gap: first });
      list.push({ value: "after", title: "After the selection", meta: `after page ${last + 1}`, gap: last + 1 });
    }
    list.push({ value: "end", title: "At the end", meta: `after page ${total}`, gap: total });
    return list;
  }

  function renderPlaces() {
    placesEl.replaceChildren();
    for (const place of places()) {
      const row = el("button", "option-row card-lift");
      row.type = "button";
      row.classList.toggle("selected", place.value === s.place);
      const text = el("span", "option-text");
      text.append(el("span", "option-title", place.title));
      row.append(el("span", "option-radio"), text, el("span", "option-meta", place.meta));
      row.addEventListener("click", () => {
        s.place = place.value;
        for (const other of placesEl.children) other.classList.toggle("selected", other === row);
      });
      placesEl.appendChild(row);
    }
  }

  function renderSource() {
    const { pages, name } = s.source;
    nameEl.textContent = name;
    nameEl.title = name;
    metaEl.textContent = `· ${pages} ${pages === 1 ? "page" : "pages"}`;
    insertBtn.textContent = `Insert ${pages} ${pages === 1 ? "page" : "pages"}`;
    thumbEl.replaceChildren();
    const wanted = s.source.source;
    callApi("organise_thumbnail", wanted, 0, Math.round(34 * devicePixelRatio))
      .then((thumb) => {
        if (!s || s.source.source !== wanted || !thumb.image) return;
        const img = document.createElement("img");
        img.alt = "";
        img.src = `data:image/png;base64,${thumb.image}`;
        thumbEl.replaceChildren(img);
      })
      .catch((err) => console.error("organise-insert: thumbnail", err));
  }

  function show(focus) {
    scrim.hidden = false;
    releaseTrap = trapFocus(card);
    (focus || insertBtn).focus();
    // The buttons still work if this fails; only the spoken answer is lost.
    scope.enter();
  }

  function hide() {
    scrim.hidden = true;
    if (releaseTrap) releaseTrap();
    releaseTrap = null;
    return scope.leave();
  }

  // Choose…: the Open dialog in front, this one out of the way meanwhile.
  // A new file replaces the one shown, which is let go.
  async function chooseAnother() {
    if (!s) return;
    await hide();
    let next = null;
    try {
      next = await s.choose();
    } catch (err) {
      console.error("organise-insert: choosing a file", err);
    }
    if (!s) return;
    if (next) {
      s.forget(s.source.source);
      s.source = next;
      renderSource();
    }
    show(chooseBtn);
  }

  function confirm() {
    const place = places().find((p) => p.value === s.place);
    const { source, name, pages } = s.source;
    close({ source, name, pages, gap: place.gap });
  }

  function close(result) {
    if (!s) return;
    if (!result) s.forget(s.source.source);
    hide();
    s = null;
    const resolve = settle;
    settle = null;
    if (resolve) resolve(result);
  }

  // `source` is `Api.organise_add_source`'s answer for the file chosen;
  // `positions` the selection, `total` the pages in the draft. After the
  // selection is the place when there is one, else the end.
  function open({ source, positions, total, choose, forget }) {
    build();
    s = { source, positions, total, place: positions.length ? "after" : "end", choose, forget };
    renderSource();
    renderPlaces();
    show();
    return new Promise((resolve) => {
      settle = resolve;
    });
  }

  function isOpen() {
    return Boolean(scrim) && !scrim.hidden;
  }

  // TOOL_CONFIRM/CANCEL/DIALOG_PICK arrive while this is the active voice
  // context.
  function handleCommand(command) {
    if (!isOpen()) return;
    if (command.intent === "TOOL_CONFIRM") confirm();
    else if (command.intent === "CANCEL") close(null);
    else if (command.intent === "DIALOG_PICK") {
      const row = placesEl.children[command.index - 1];
      if (row) row.click();
    }
  }

  return { open, isOpen, handleCommand };
})();
