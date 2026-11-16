// Compress's insides for the tool dialog (tool-dialogs.js): the Quality and
// Size limit tabs, and the Done and "can't get under" results (docs/PRD.md §
// "Post-v1: file tools" → Compress; mockups
// docs/mockups/file-tools/compress-v3-quality.png, panels 1–2, and
// compress-v4-size-limit.png). The dialog keeps the frame (the header, the
// destination block, Working, keys and voice) and calls in here for what is
// Compress's own; `CompressPanel.init` hands over its helpers.
//
// Both tabs show what a choice will make before the reader picks it. Python
// compresses steps of one quality ladder in the background
// (features/file_tools/estimates.py; the ladder is compress_plan.LADDER),
// each result arriving as a `lector:compressestimate` event: Quality needs
// three steps, Size limit all of them. The results are shared by the tabs and
// kept for the dialog's life; pressing Compress moves a finished step's file
// into place. They are deleted when the dialog closes.
//
// Built from DOM calls with textContent: the file's name comes from disk.
"use strict";

// compress_plan.LADDER's length (tests/test_compress_panel_behavior.py pins it).
const COMPRESS_LADDER_STEPS = 10;
const COMPRESS_LEVELS = [
  { value: "light", step: 0, title: "Best quality", purpose: "For printing", tip: "Keeps images as they are and tidies the file." },
  { value: "balanced", step: 2, title: "Balanced", purpose: "For reading on screen", tip: "Reduces images to 150 ppi." },
  { value: "strong", step: 4, title: "Smallest file", purpose: "For email and uploads", tip: "Reduces images to 96 ppi." },
];
const COMPRESS_NO_CHANGE_TIP = "This file is already compressed to this level.";
// Once the estimates are in: Balanced if it helps, else Smallest file, else
// Best quality.
const COMPRESS_DEFAULT_ORDER = ["balanced", "strong", "light"];
// Sizes and limits count as upload forms do: 1 KB is 1,000 bytes. A step
// fits a limit at 97% of it or less (compress_plan.fits).
const COMPRESS_UNITS = { KB: 1000, MB: 1000 * 1000 };
const COMPRESS_MARGIN_PERCENT = 97;
const COMPRESS_LIMIT_DIGITS = 4; // 1 to 9999
const COMPRESS_PRESETS = [
  { value: 50, unit: "KB" },
  { value: 100, unit: "KB" },
  { value: 200, unit: "KB" },
  { value: 300, unit: "KB" },
  { value: 500, unit: "KB" },
  { value: 1, unit: "MB" },
  { value: 2, unit: "MB" },
];
// What a size-limit step does, in plain words (compress_plan.kind).
const COMPRESS_KINDS = {
  sharp: { icon: "compress_sharp", title: "Sharp", line: "Colour kept. Close to the original." },
  good: { icon: "compress_screen", title: "Good for reading on screen", line: "Colour kept. Images slightly softer." },
  readable: { icon: "compress_soft", title: "Readable", line: "Images are soft. Check small text after saving." },
  bw: {
    icon: "compress_bw",
    title: "Black and white",
    line: "Scanned pages become black and white. Text stays readable.",
  },
};
const COMPRESS_CHECKING_TEXT = "Checking which sizes this file can reach…";

// Sizes in this dialog: whole KB under 1 MB, one decimal for MB.
function compressSize(bytes) {
  const kb = Math.max(1, Math.round(bytes / COMPRESS_UNITS.KB));
  if (kb < 1000) return `${kb} KB`;
  return `${(bytes / COMPRESS_UNITS.MB).toFixed(1)} MB`;
}

// "75% smaller".
function compressSaving(before, after) {
  return `${Math.round(100 - (100 * after) / Math.max(1, before))}% smaller`;
}

function compressLimitBytes(value, unit) {
  return value * COMPRESS_UNITS[unit];
}

function compressFits(size, limit) {
  return size * 100 <= limit * COMPRESS_MARGIN_PERCENT;
}

// A Quality card whose estimate is finished and doesn't save 5%: "No change".
function compressNoChange(row) {
  return Boolean(row) && row.state === "done" && !row.helps;
}

function compressLevelRow(steps, level) {
  return steps[COMPRESS_LEVELS.find((l) => l.value === level).step];
}

function compressLevelsSettled(steps) {
  return COMPRESS_LEVELS.every((l) => steps[l.step] && ["done", "failed"].includes(steps[l.step].state));
}

function compressAllNoChange(steps) {
  return COMPRESS_LEVELS.every((l) => compressNoChange(steps[l.step]));
}

// The level to select once the estimates are in, or null when none helps.
// A level whose estimate failed still counts: its size is just unknown.
function compressDefaultLevel(steps) {
  return COMPRESS_DEFAULT_ORDER.find((level) => !compressNoChange(compressLevelRow(steps, level))) || null;
}

// The best-quality step under `limit` from the steps known so far, and
// whether a later result could still change that: compress_plan.choose,
// which the job checks again, so the two must agree. `step` null and
// `settled` true is "too small for this file".
function compressChoice(steps, limit) {
  const last = COMPRESS_LADDER_STEPS - 1;
  const known = (i) => Boolean(steps[i]) && (steps[i].state === "done" || steps[i].state === "failed");
  const done = (i) => Boolean(steps[i]) && steps[i].state === "done";
  let every = true;
  for (let i = 0; i <= last; i++) if (!known(i)) every = false;
  let best = null;
  for (let i = 0; i <= last; i++) {
    if (done(i) && compressFits(steps[i].size, limit)) {
      best = i;
      break;
    }
  }
  if (best === null) return { step: null, settled: (done(last) && known(0)) || every };
  for (let i = best - 1; i >= 0; i--) {
    if (!known(i)) return { step: best, settled: every };
    if (done(i)) return { step: best, settled: true };
  }
  return { step: best, settled: true };
}

// The smallest result known so far, or null.
function compressFloor(steps) {
  const sizes = Object.values(steps)
    .filter((row) => row && row.state === "done")
    .map((row) => row.size);
  return sizes.length ? Math.min(...sizes) : null;
}

// A limit's state for this file: "hidden" (at or above its size: it would
// do nothing), "reachable", "too_small", or "checking".
function compressLimitState(limit, fileSize, steps) {
  if (limit >= fileSize) return "hidden";
  const choice = compressChoice(steps, limit);
  if (choice.step !== null) return "reachable";
  return choice.settled ? "too_small" : "checking";
}

// The "Other size…" field's number, or null for anything but a whole number
// 1–9999.
function compressLimitValue(text) {
  if (!/^\d{1,4}$/.test(String(text))) return null;
  const value = Number(text);
  return value >= 1 ? value : null;
}

function compressSameLimit(a, b) {
  return Boolean(a) && Boolean(b) && a.value === b.value && a.unit === b.unit;
}

const CompressPanel = (() => {
  let ui = null;
  // Starting and stopping the background work go to Python one after
  // another, in the order the dialog asked: each bridge call runs on its own
  // thread, so a stop sent after a start could otherwise arrive before it.
  let control = Promise.resolve();

  function inOrder(method, ...args) {
    const call = control.then(() => callApi(method, ...args));
    control = call.catch(() => null);
    return call;
  }

  function init(hooks) {
    ui = hooks;
    window.addEventListener("lector:compressestimate", (ev) => onEstimate(ev.detail || {}));
  }

  function freshState(info) {
    const unit = info.prefs.compress_unit === "MB" ? "MB" : "KB";
    return {
      mode: info.prefs.compress_mode === "limit" ? "limit" : "quality",
      level: "balanced",
      picked: false,
      steps: {},
      session: null,
      // Size limit: the pill selected (a preset), whether the reader picked
      // it, the last limit used (selected by default once it is reachable).
      limit: null,
      limitPicked: false,
      remembered: { value: info.prefs.compress_limit, unit },
      // "Other size…".
      other: false,
      otherText: "",
      otherUnit: unit,
      saveHeld: false,
    };
  }

  // The header's one line of facts, the same in every state.
  function description(s) {
    return `${s.info.name} · ${compressSize(s.info.size)} · ${plural(s.info.pages, "page", "pages")}`;
  }

  // ---- The background work -------------------------------------------------

  // Ask Python for the steps this tab needs (finished ones are kept).
  async function startWork(s) {
    const cs = s.compress;
    const folder = s.destination.value().folder;
    const answer = await inOrder("start_compress_estimates", s.source, folder, cs.session, cs.mode);
    // The dialog closed while they were starting: its close already queued
    // the stop, in order (a second one here could land after a newer
    // dialog's start and stop that one instead).
    if (ui.state() !== s) return;
    if (!answer || !answer.ok) {
      cs.unchecked = true;
      if (s.phase === "setup") ui.rerender();
      return;
    }
    cs.unchecked = false;
    cs.session = answer.session;
    const known = await callApi("get_compress_estimates", cs.session);
    if (ui.state() !== s) return;
    // An event may have brought a newer row while this was on its way.
    for (const [step, row] of Object.entries(known || {})) {
      const now = cs.steps[step];
      if (!now || now.state === "working") cs.steps[step] = row;
    }
    settle(s);
    if (s.phase === "setup") ui.rerender();
  }

  function opened(s) {
    return startWork(s);
  }

  function closed(s) {
    if (s && s.compress) inOrder("stop_compress_estimates", true);
  }

  // Default selections, as results arrive: Quality's once every level is in
  // (unless the reader picked a card and it can still be picked); Size
  // limit's, the last limit used, once it is known to be reachable.
  function settle(s) {
    const cs = s.compress;
    const keep = cs.picked && !compressNoChange(compressLevelRow(cs.steps, cs.level));
    if (!keep && (compressLevelsSettled(cs.steps) || compressNoChange(compressLevelRow(cs.steps, cs.level)))) {
      cs.picked = false;
      cs.level = compressDefaultLevel(cs.steps) || "balanced";
    }
    const remembered = COMPRESS_PRESETS.find((p) => compressSameLimit(p, cs.remembered));
    if (!cs.limit && !cs.limitPicked && remembered) {
      const limit = compressLimitBytes(remembered.value, remembered.unit);
      if (compressLimitState(limit, s.info.size, cs.steps) === "reachable") cs.limit = remembered;
    }
  }

  function onEstimate(event) {
    const s = ui && ui.state();
    if (!s || !s.compress || event.session !== s.compress.session || typeof event.step !== "number") return;
    const cs = s.compress;
    if (event.state === "stopped") delete cs.steps[event.step];
    else cs.steps[event.step] = { state: event.state, size: event.size, helps: event.helps, kind: event.kind };
    settle(s);
    if (s.phase !== "setup" || ui.hidden()) return;
    // A redraw would rebuild the KB/MB button under its open menu: wait for
    // the menu to close.
    const unit = document.getElementById("compressUnitBtn");
    if (unit && isPopupMenuOpen(unit)) {
      cs.renderAfterMenu = true;
      return;
    }
    ui.rerender();
  }

  // ---- Set up ------------------------------------------------------------

  function pickMode(s, mode) {
    const cs = s.compress;
    if (cs.mode === mode) return;
    cs.mode = mode;
    callApi("set_compress_mode", mode);
    startWork(s);
    ui.rerender();
  }

  function renderSetup(s) {
    const cs = s.compress;
    const modes = ui.segmented(
      [
        { value: "quality", label: "Quality" },
        { value: "limit", label: "Size limit" },
      ],
      cs.mode,
      (mode) => pickMode(s, mode),
      "How to compress"
    );
    modes.classList.add("segmented--full", "compress-modes");
    ui.body.append(modes);
    if (cs.mode === "quality") qualityBlock(s);
    else limitBlock(s);
    ui.body.append(ui.destinationBlock());
    const notes = ui.notesBlock();
    if (notes) ui.body.append(notes);
    ui.footerButton("Cancel", ui.close);
    ui.footerButton("Compress", ui.start, { primary: true, disabled: !ui.setupReady() });
  }

  // ---- Quality: three option cards ------------------------------------------

  // The right side of a card: "About 6.2 MB" over "75% smaller".
  function sizeCell(s, row) {
    const cell = el("span", "compress-size");
    if (!row || row.state === "working") {
      const busy = el("span", "compress-estimating", "Estimating");
      busy.setAttribute("aria-label", "Estimating the size");
      cell.append(busy);
      return cell;
    }
    if (row.state === "failed") {
      cell.append(el("span", "compress-size-note", "Size unknown"));
      return cell;
    }
    if (!row.helps) {
      cell.append(el("span", "compress-size-main", compressSize(s.info.size)), el("span", "compress-size-note", "No change"));
      return cell;
    }
    cell.append(
      el("span", "compress-size-main", `About ${compressSize(row.size)}`),
      el("span", "compress-size-note", compressSaving(s.info.size, row.size))
    );
    return cell;
  }

  function qualityBlock(s) {
    const cs = s.compress;
    const list = el("div", "tool-options compress-levels");
    list.setAttribute("aria-label", "Quality");
    for (const level of COMPRESS_LEVELS) {
      const row = cs.steps[level.step];
      const noChange = compressNoChange(row);
      const card = el("button", "option-row card-lift compress-level");
      card.type = "button";
      card.classList.toggle("selected", level.value === cs.level && !noChange);
      card.dataset.tooltip = noChange ? COMPRESS_NO_CHANGE_TIP : level.tip;
      if (noChange) card.setAttribute("aria-disabled", "true");
      const text = el("span", "option-text");
      text.append(el("span", "option-title", level.title), el("span", "option-subtitle", level.purpose));
      card.append(el("span", "option-radio"), text, sizeCell(s, row));
      card.addEventListener("click", () => {
        if (card.getAttribute("aria-disabled") === "true") return;
        cs.level = level.value;
        cs.picked = true;
        ui.rerender();
      });
      list.appendChild(card);
    }
    initRadioCards(list, { selectOnArrow: true });
    ui.body.append(list);
    if (compressAllNoChange(cs.steps)) {
      ui.body.append(
        el("p", "compress-hint", "This file is already as small as these levels can make it. Try a size limit.")
      );
    }
  }

  // ---- Size limit: preset pills, checked in advance --------------------------

  function presetLabel(preset) {
    return `${preset.value} ${preset.unit}`;
  }

  // The limit Compress would use now: "Other size…"'s, or the pill's.
  function activeLimit(cs) {
    if (cs.other) {
      const value = compressLimitValue(cs.otherText);
      return value === null ? null : { value, unit: cs.otherUnit };
    }
    return cs.limit;
  }

  function activeBytes(cs) {
    const limit = activeLimit(cs);
    return limit ? compressLimitBytes(limit.value, limit.unit) : null;
  }

  function floorText(cs) {
    const floor = compressFloor(cs.steps);
    return floor === null ? "" : compressSize(floor);
  }

  function pills(s) {
    const cs = s.compress;
    const group = el("div", "compress-pills");
    group.setAttribute("aria-labelledby", "compressPillsLabel");
    COMPRESS_PRESETS.forEach((preset, n) => {
      const bytes = compressLimitBytes(preset.value, preset.unit);
      const state = compressLimitState(bytes, s.info.size, cs.steps);
      if (state === "hidden") return;
      const pill = el("button", "compress-pill", presetLabel(preset));
      pill.type = "button";
      pill.id = `compressPill${n}`;
      pill.classList.toggle("selected", !cs.other && compressSameLimit(cs.limit, preset));
      if (state !== "reachable") {
        pill.setAttribute("aria-disabled", "true");
        pill.classList.add(state === "too_small" ? "is-too-small" : "is-checking");
      }
      if (state === "too_small") {
        pill.dataset.tooltip = `Too small for this file. The smallest it can go is about ${floorText(cs)}.`;
      }
      pill.addEventListener("click", () => {
        if (pill.getAttribute("aria-disabled") === "true") return;
        cs.limit = preset;
        cs.limitPicked = true;
        cs.other = false;
        ui.rerender();
      });
      group.appendChild(pill);
    });
    if (!group.children.length) return null;
    initRadioCards(group, { selectOnArrow: true });
    return group;
  }

  // The card under the pills: what the chosen limit will make, or that the
  // file is still being checked.
  function outcomeCard(cs, step) {
    const card = el("div", "compress-outcome");
    card.setAttribute("role", "status");
    if (step === null) {
      card.classList.add("is-checking");
      const text = el("span", "compress-outcome-text");
      text.append(el("span", "compress-outcome-line", COMPRESS_CHECKING_TEXT), el("span", "compress-shimmer"));
      card.append(el("span", "compress-outcome-tile"), text);
      return card;
    }
    const row = cs.steps[step];
    const words = COMPRESS_KINDS[row.kind] || COMPRESS_KINDS.sharp;
    const text = el("span", "compress-outcome-text");
    text.append(el("span", "compress-outcome-title", words.title), el("span", "compress-outcome-line", words.line));
    card.append(
      iconEl(words.icon, "compress-outcome-tile"),
      text,
      el("span", "compress-outcome-size", `About ${compressSize(row.size)}`)
    );
    return card;
  }

  // What shows under the pills (and the "Other size…" row): a result card,
  // the checking card, a line, or nothing.
  function limitOutcome(s) {
    const cs = s.compress;
    if (cs.unchecked) {
      const note = el("p", "compress-hint", "Couldn't check this file's sizes. Quality still works.");
      note.setAttribute("role", "status");
      return note;
    }
    const limit = activeBytes(cs);
    if (limit === null) {
      // Nothing picked yet: while pills are still undecided, say so.
      const checking =
        !cs.other &&
        COMPRESS_PRESETS.some(
          (p) => compressLimitState(compressLimitBytes(p.value, p.unit), s.info.size, cs.steps) === "checking"
        );
      return checking ? outcomeCard(cs, null) : null;
    }
    const state = compressLimitState(limit, s.info.size, cs.steps);
    if (state === "hidden" || state === "too_small") return null; // the field says why
    const choice = compressChoice(cs.steps, limit);
    return outcomeCard(cs, choice.settled ? choice.step : null);
  }

  function chooseUnit(s, button) {
    const cs = s.compress;
    const items = Object.keys(COMPRESS_UNITS).map((unit) =>
      buildPopupMenuItem({
        label: unit,
        className: "compress-unit-item",
        onSelect: () => {
          cs.otherUnit = unit;
          ui.rerender();
          const again = document.getElementById("compressUnitBtn");
          if (again) again.focus();
        },
      })
    );
    openPopupMenu({
      trigger: button,
      children: items,
      className: "compress-unit-menu",
      label: "Unit",
      anchor: button,
      closesOnScroll: () => false,
      onClose: () => {
        if (!cs.renderAfterMenu) return;
        cs.renderAfterMenu = false;
        // After the menu has handed focus back to its button.
        setTimeout(() => {
          if (ui.state() && ui.state().compress === cs && ui.state().phase === "setup") ui.rerender();
        }, 0);
      },
    });
  }

  function closeOther(s) {
    s.compress.other = false;
    ui.rerender();
    const link = document.getElementById("compressOtherLink");
    if (link) link.focus();
  }

  // "Other size…": [number] [KB v], checked against the same results.
  function otherRow(s) {
    const cs = s.compress;
    const row = el("div", "compress-other");
    const input = el("input", "compress-limit-input");
    input.id = "compressOtherInput";
    input.type = "text";
    input.inputMode = "numeric";
    input.maxLength = COMPRESS_LIMIT_DIGITS;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = cs.otherText;
    input.setAttribute("aria-label", "Size limit");
    input.addEventListener("input", () => {
      const digits = input.value.replace(/\D+/g, "").slice(0, COMPRESS_LIMIT_DIGITS);
      if (digits !== input.value) input.value = digits;
      cs.otherText = digits;
      ui.rerender();
    });
    const unit = el("button", "compress-unit");
    unit.type = "button";
    unit.id = "compressUnitBtn";
    unit.setAttribute("aria-haspopup", "menu");
    unit.setAttribute("aria-expanded", "false");
    unit.setAttribute("aria-label", `Unit, ${cs.otherUnit}`);
    unit.append(el("span", "", cs.otherUnit), iconEl("chevron_down", "compress-unit-chevron"));
    unit.addEventListener("click", () => {
      if (isPopupMenuOpen(unit)) closePopupMenu({ restoreFocus: true });
      else chooseUnit(s, unit);
    });
    // Esc in the row closes the row, not the dialog.
    row.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      ev.preventDefault();
      ev.stopPropagation();
      closeOther(s);
    });
    row.append(input, unit);

    const out = [row];
    const limit = activeBytes(cs);
    if (limit !== null) {
      const state = compressLimitState(limit, s.info.size, cs.steps);
      const label = `${compressLimitValue(cs.otherText)} ${cs.otherUnit}`;
      if (state === "too_small") {
        input.classList.add("is-error");
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", "compressOtherMessage");
        const message = el("p", "compress-field-error", `Too small. The smallest this file can go is about ${floorText(cs)}.`);
        message.id = "compressOtherMessage";
        message.prepend(iconEl("info"));
        out.push(message);
      } else if (state === "hidden") {
        const message = el("p", "compress-hint", `This file is already under ${label}.`);
        message.setAttribute("role", "status");
        out.push(message);
      }
    }
    return out;
  }

  function limitBlock(s) {
    const cs = s.compress;
    const label = el("p", "compress-label", "Upload limit");
    label.id = "compressPillsLabel";
    ui.body.append(label);
    const group = pills(s);
    if (group) ui.body.append(group);
    else ui.body.append(el("p", "compress-hint", `This file is already under ${presetLabel(COMPRESS_PRESETS[0])}.`));
    if (cs.other) ui.body.append(...otherRow(s));
    const card = limitOutcome(s);
    if (card) ui.body.append(card);
    const link = el("button", "compress-other-link", cs.other ? "Use a preset" : "Other size…");
    link.type = "button";
    link.id = "compressOtherLink";
    link.setAttribute("aria-expanded", String(cs.other));
    link.addEventListener("click", () => {
      if (cs.other) {
        closeOther(s);
        return;
      }
      cs.other = true;
      ui.rerender();
      const input = document.getElementById("compressOtherInput");
      if (input) input.focus();
    });
    ui.body.append(link);
  }

  function ready(s) {
    const cs = s.compress;
    if (cs.saveHeld) return true;
    if (cs.mode === "limit") {
      const limit = activeBytes(cs);
      if (limit === null || !cs.session || limit >= s.info.size) return false;
      const choice = compressChoice(cs.steps, limit);
      return choice.step !== null && choice.settled;
    }
    return !compressAllNoChange(cs.steps) && !compressNoChange(compressLevelRow(cs.steps, cs.level));
  }

  function jobOptions(s) {
    const cs = s.compress;
    const limit = activeLimit(cs);
    if (cs.saveHeld) return { mode: "save_held", limit: limit.value, unit: limit.unit, session: cs.session };
    if (cs.mode === "limit") return { mode: "limit", limit: limit.value, unit: limit.unit, session: cs.session };
    return { mode: "quality", level: cs.level, session: cs.session };
  }

  // A job this dialog started was cancelled: back to Set up, and the steps
  // the job stopped (or took) are made again.
  function afterCancel(s) {
    s.compress.saveHeld = false;
    startWork(s);
  }

  // ---- Results -------------------------------------------------------------

  function sizeBars(before, after) {
    const bars = el("div", "size-bars");
    const share = Math.max(2, Math.round((100 * after) / Math.max(1, before)));
    const barOf = (isAfter, pct) => {
      const bar = el("div", isAfter ? "size-bar is-after" : "size-bar");
      const fill = el("div", "size-bar-fill");
      fill.style.setProperty("--share", `${pct}%`);
      bar.appendChild(fill);
      return bar;
    };
    bars.append(
      el("span", "", "Before"),
      barOf(false, 100),
      el("span", "", compressSize(before)),
      el("span", "", "After"),
      barOf(true, share),
      el("b", "", compressSize(after))
    );
    return bars;
  }

  // The Done state's readability note, or "" for none: a Quality level that
  // brought images low, or a size limit that made them soft or black and
  // white.
  function readingNote(r) {
    let bw = r.black_and_white;
    let low = r.lowered;
    if (r.mode === "limit") {
      bw = r.kind === "bw";
      low = r.kind === "readable";
    }
    if (!bw && !low) return "";
    const what = bw ? "black and white at a lower resolution" : "at a lower resolution";
    return `Pages are now ${what}. Open the file to check it's easy to read.`;
  }

  function renderDone(s, r) {
    const wrap = el("div", "compress-result");
    const saved = `Saved to ${r.folder_label} as ${r.name}.`;
    const line =
      r.mode === "limit" && !r.over_limit
        ? `That's within your ${r.label} limit. ${saved}`
        : `That's ${compressSaving(r.before, r.after)}. ${saved}`;
    wrap.append(
      el("p", "compress-headline", `Done. The new file is ${compressSize(r.after)}.`),
      el("p", "compress-line", line),
      sizeBars(r.before, r.after)
    );
    const note = readingNote(r);
    if (note) wrap.append(el("p", "compress-hint", note));
    ui.body.append(wrap);
    ui.footerButton("Show in folder", () => showFileJobOutput(r.path));
    ui.footerButton("Open file", () => ui.openOutput(r.path));
    ui.footerButton("Done", ui.close, { primary: true });
  }

  // The safety net: a step that turned out not to fit (it shouldn't happen;
  // Python logs it).
  function renderUnreachable(s, r) {
    const wrap = el("div", "compress-result");
    wrap.append(
      el("p", "compress-headline", `This file can't get under ${r.label}.`),
      el("p", "compress-line", `The smallest Lector can make is ${compressSize(r.smallest)}. Nothing has been saved yet.`),
      el(
        "p",
        "compress-hint compress-advice",
        "At that size, text may be hard to read. Splitting the PDF into smaller parts usually helps."
      )
    );
    ui.body.append(wrap);
    ui.footerButton("Change limit", () => changeLimit(s));
    ui.footerButton(`Save ${compressSize(r.smallest)} file`, () => saveHeld(s), { primary: true });
  }

  function renderResult(s) {
    const r = (s.outcome && s.outcome.result) || {};
    if (r.unreachable) {
      renderUnreachable(s, r);
      return;
    }
    // Saved: the other steps' temporary files have no use now, and
    // shouldn't sit beside the new file in "Show in folder".
    if (!s.compress.cleared) {
      s.compress.cleared = true;
      inOrder("stop_compress_estimates", true);
    }
    renderDone(s, r);
  }

  function changeLimit(s) {
    s.phase = "setup";
    s.compress.mode = "limit";
    s.outcome = null;
    startWork(s);
    ui.rerender();
  }

  // "Save 164 KB file": the safety net's result, moved into place by a job
  // like any other (the name and folder are checked first).
  async function saveHeld(s) {
    s.phase = "setup";
    s.compress.saveHeld = true;
    await ui.start();
    if (ui.state() === s && s.phase === "setup") {
      // Not started (a name to fix, a busy job): Set up shows why.
      s.compress.saveHeld = false;
      ui.rerender();
    }
  }

  return {
    init,
    freshState,
    description,
    opened,
    closed,
    renderSetup,
    renderResult,
    ready,
    jobOptions,
    afterCancel,
  };
})();
