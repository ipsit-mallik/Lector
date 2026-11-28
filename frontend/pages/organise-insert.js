// Insert… in Organise pages (docs/DESIGN_SYSTEM.md § "Organise pages" →
// "Insert"): where the pages of the file just chosen go. The source row
// (its first page, its name, how many pages) with Choose… to pick another
// file; the places as one segmented control (At the start · Before page X ·
// After page Y · At the end; only the start and the end when nothing is
// selected), with a preview strip and a caption saying where the pages land
// (organise-insert-places.js works them out); and "Insert N pages".
//
// It doesn't choose files itself: `open()` is handed the file already
// chosen and a `choose()` that asks for another (the Open dialog, and a
// protected PDF's password; organise.js), plus `forget()` for a file that
// was chosen and then not used. Resolves `{ source, name, pages, gap }`, or
// null.
//
// Its own modal voice scope, `organise_insert`: the file tools' grammar
// ("confirm" inserts, "cancel" cancels, a number picks that place) plus the
// four places by name.
"use strict";

const OrganiseInsertDialog = (() => {
  const Places = OrganiseInsertPlaces;
  // Each spoken place, and the place it picks.
  const SPOKEN_PLACES = {
    INSERT_AT_START: "start",
    INSERT_BEFORE: "before",
    INSERT_AFTER: "after",
    INSERT_AT_END: "end",
  };
  const NO_SELECTION = "No pages are selected, so the new pages can go at the start or the end.";

  let scrim = null;
  let card = null;
  let thumbEl = null;
  let nameEl = null;
  let metaEl = null;
  let chooseBtn = null;
  let placesEl = null;
  let previewEl = null;
  let captionEl = null;
  let insertBtn = null;
  let scope = null;
  let releaseTrap = null;
  let settle = null;
  let resizes = null;
  let columnsShown = null;
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

    // The shared segmented control's look (file-tools.css), laid out as a
    // grid of equal columns by organise.css so it can fall back to 2×2.
    placesEl = el("div", "segmented organise-insert-places");
    placesEl.setAttribute("aria-label", "Where the pages go");
    placesEl.setAttribute("aria-describedby", "organiseInsertCaption");
    initRadioCards(placesEl, { selectOnArrow: true });
    previewEl = el("div", "organise-insert-preview");
    previewEl.setAttribute("aria-hidden", "true");
    captionEl = el("p", "organise-insert-caption");
    captionEl.id = "organiseInsertCaption";
    captionEl.setAttribute("aria-live", "polite");
    body.append(header, source, placesEl, previewEl, captionEl);

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
    scope = createVoiceScope("organise_insert");
    // The track and its labels: a narrower dialog, display scaling or a
    // larger font each change one of them, and the columns are worked out
    // again.
    resizes = new ResizeObserver(() => fitColumns());
    // Esc is Cancel, and goes no further (not to Organise behind).
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape" || !isOpen()) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      close(null);
    }, true);
  }

  function places() {
    return Places.places(s.positions, s.total);
  }

  function chosenPlace() {
    return places().find((p) => p.value === s.place);
  }

  function renderPlaces() {
    resizes.disconnect();
    placesEl.replaceChildren();
    for (const place of places()) {
      const button = el("button", "segmented-btn");
      button.type = "button";
      button.dataset.place = place.value;
      button.classList.toggle("selected", place.value === s.place);
      // data-label: organise.css reserves the label's selected (600) width
      // with it, so choosing a place never changes what fits.
      const label = el("span", "organise-insert-place-label", place.label);
      label.dataset.label = place.label;
      button.appendChild(label);
      button.addEventListener("click", () => pick(place.value));
      placesEl.appendChild(button);
    }
    resizes.observe(placesEl);
    for (const button of placesEl.children) resizes.observe(button.children[0]);
  }

  function pick(value) {
    s.place = value;
    for (const button of placesEl.children) button.classList.toggle("selected", button.dataset.place === value);
    renderPreview();
  }

  // The strip and its caption, for the place chosen.
  function renderPreview() {
    const { gap } = chosenPlace();
    const count = s.source.pages;
    const items = Places.preview(s.total, gap, count, new Set(s.positions));
    previewEl.replaceChildren(...items.map((item) => {
      if (item.kind === "gap") return el("span", "organise-insert-pv organise-insert-pv--gap", "…");
      if (item.kind === "insert") return el("span", "organise-insert-pv organise-insert-pv--new", `+${item.count}`);
      const page = el("span", "organise-insert-pv organise-insert-pv--page", String(item.page));
      page.classList.toggle("is-selected", item.selected);
      return page;
    }));
    captionEl.textContent = Places.caption(gap, count, s.total);
  }

  // One row while every label fits on one line at full size; else 2×2
  // (else one column). Labels are measured at their selected weight
  // (organise.css), with offsetWidth, which the dialog's pop-in scale
  // doesn't affect.
  function fitColumns() {
    if (!isOpen() || !placesEl.children.length) return;
    const track = getComputedStyle(placesEl);
    const first = getComputedStyle(placesEl.children[0]);
    const px = (value) => parseFloat(value) || 0;
    const inner = placesEl.clientWidth - px(track.paddingLeft) - px(track.paddingRight);
    const padX = px(first.paddingLeft) + px(first.paddingRight) + px(first.borderLeftWidth) + px(first.borderRightWidth);
    // offsetWidth is rounded to a whole pixel: one more keeps a label that
    // rounds down from being squeezed.
    const widths = Array.from(placesEl.children, (button) => button.children[0].offsetWidth + 1);
    const columns = Places.columns(widths, inner, px(track.columnGap), padX);
    if (columns === columnsShown) return;
    columnsShown = columns;
    placesEl.style.setProperty("--places-cols", String(columns));
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
    fitColumns();
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
      renderPreview();
    }
    show(chooseBtn);
  }

  function confirm() {
    const { source, name, pages } = s.source;
    close({ source, name, pages, gap: chosenPlace().gap });
  }

  function close(result) {
    if (!s) return;
    if (!result) s.forget(s.source.source);
    hide();
    resizes.disconnect();
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
    s = { source, positions, total, place: Places.defaultPlace(positions), choose, forget };
    renderSource();
    renderPlaces();
    renderPreview();
    show();
    return new Promise((resolve) => {
      settle = resolve;
    });
  }

  function isOpen() {
    return Boolean(scrim) && !scrim.hidden;
  }

  // A place said by name. Before and after need a selection: without one
  // the reason is said and nothing changes.
  function pickSpoken(value) {
    if (places().some((p) => p.value === value)) pick(value);
    else showToast(NO_SELECTION);
  }

  // TOOL_CONFIRM/CANCEL/DIALOG_PICK and the INSERT_* places arrive while
  // this is the active voice context. A number counts the places from the
  // left (the top-left in 2×2), as they are read.
  function handleCommand(command) {
    if (!isOpen()) return;
    if (command.intent === "TOOL_CONFIRM") confirm();
    else if (command.intent === "CANCEL") close(null);
    else if (command.intent === "DIALOG_PICK") {
      const button = placesEl.children[command.index - 1];
      if (button) pick(button.dataset.place);
    } else if (command.intent in SPOKEN_PLACES) pickSpoken(SPOKEN_PLACES[command.intent]);
  }

  return { open, isOpen, handleCommand };
})();
