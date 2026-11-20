// What voice listens to and what the mic pill says: the visible regions sent
// for matching, the "listening here" outline, keeping the wake phrase's words
// current, and the pill itself.

// Draws the amber "listening here" outline over `rects` (screen-pixel boxes,
// {left, top, width, height}, relative to the viewport). One tag is enough
// even when strip layout hands back two partially-visible regions, since the
// point is "voice can hear all of this," not "here is region N."
function renderVoiceFocus(rects) {
  const areaRect = pageArea.getBoundingClientRect();
  const fragment = document.createDocumentFragment();
  rects.forEach((r, i) => {
    const div = document.createElement("div");
    div.className = "voice-focus-rect";
    div.style.left = `${r.left - areaRect.left}px`;
    div.style.top = `${r.top - areaRect.top}px`;
    div.style.width = `${r.width}px`;
    div.style.height = `${r.height}px`;
    if (i === 0) {
      const tag = document.createElement("span");
      tag.className = "voice-focus-tag";
      tag.textContent = "Listening here";
      div.appendChild(tag);
    }
    fragment.appendChild(div);
  });
  voiceFocusLayer.replaceChildren(fragment);
}

function clearVoiceFocus() {
  voiceFocusLayer.replaceChildren();
}

// The page regions actually on screen right now, in PDF points, for the
// backend to scope voice matching to (docs/PRD.md: matching only ever looks
// at "the currently visible viewport"). Reuses getPageChars()'s cached
// {width, height, chars} for each page's PDF-point size and pageScale() for
// the same pixels-per-point conversion the character-selection code already
// relies on, rather than introducing a second notion of page geometry.
// Resolves to the point-based regions (what start_listening() sends to
// Python) and, as a side effect, paints the same regions as the on-page
// "listening here" outline (docs/DESIGN_SYSTEM.md) — the two are measured in
// one pass so the outline can never drift from what is actually in scope for
// matching, which repainting them separately after a scroll could risk.
// --- Keeping the recognizer's vocabulary current -------------------------- //
// Push-to-talk can gather the visible words at the moment the key goes down,
// because there is a moment. A wake has none: the recognizer is built before
// the phrase is spoken, and a Vosk grammar can only ever return words it was
// built with, so words that reach the screen afterwards are words the reader
// cannot highlight by voice. This pushes them across as the reader moves
// instead.
//
// Only while the wake phrase is actually enabled: with push-to-talk alone it
// is pure overhead, since key-down already does the same work with better
// timing.
const VIEWPORT_PUSH_DEBOUNCE_MS = 400;
let viewportPushTimer = null;
let wakeListening = false;

function scheduleViewportPush() {
  if (!wakeListening) return;
  clearTimeout(viewportPushTimer);
  viewportPushTimer = setTimeout(async () => {
    if (!state.is_open) return;
    // Deliberately not computeVoiceViewport(): that paints the "listening
    // here" outline as a side effect, and nothing is being listened *to*
    // here — an outline drawn while the reader merely scrolls would claim a
    // capture that is not happening.
    const { regions } =
      layoutMode === "book" ? await computeBookViewport() : await computeStripViewport();
    callApi("update_voice_viewport", regions).catch(() => {
      // A vocabulary that could not be refreshed leaves the previous one in
      // place, which is worse aim rather than a broken feature.
    });
  }, VIEWPORT_PUSH_DEBOUNCE_MS);
}

async function computeVoiceViewport() {
  if (!state.is_open) {
    clearVoiceFocus();
    return [];
  }
  const { regions, rects } =
    layoutMode === "book" ? await computeBookViewport() : await computeStripViewport();
  renderVoiceFocus(rects);
  return regions;
}

async function computeBookViewport() {
  const empty = { regions: [], rects: [] };
  const pageIndex = state.page_index;
  const page = await getPageChars(pageIndex);
  if (!page || !page.width) return empty;
  const scale = pageScale(pageSurface, page);
  if (!scale) return empty;
  const pageRect = pageSurface.getBoundingClientRect();
  const scrollRect = bookScroll.getBoundingClientRect();
  const topPx = Math.max(pageRect.top, scrollRect.top);
  const bottomPx = Math.min(pageRect.bottom, scrollRect.bottom);
  if (bottomPx <= topPx) return empty;
  return {
    regions: [{
      page_index: pageIndex,
      y0: (topPx - pageRect.top) / scale,
      y1: (bottomPx - pageRect.top) / scale,
    }],
    rects: [{ left: pageRect.left, top: topPx, width: pageRect.width, height: bottomPx - topPx }],
  };
}

async function computeStripViewport() {
  const scrollRect = stripScroll.getBoundingClientRect();
  const regions = [];
  const rects = [];
  for (const el of stripContainer.querySelectorAll(".strip-page")) {
    const rect = el.getBoundingClientRect();
    if (rect.bottom <= scrollRect.top || rect.top >= scrollRect.bottom) continue;
    const pageIndex = Number(el.dataset.pageIndex);
    const page = await getPageChars(pageIndex);
    if (!page || !page.width) continue;
    const scale = pageScale(el, page);
    if (!scale) continue;
    const topPx = Math.max(rect.top, scrollRect.top);
    const bottomPx = Math.min(rect.bottom, scrollRect.bottom);
    if (bottomPx <= topPx) continue;
    regions.push({
      page_index: pageIndex,
      y0: (topPx - rect.top) / scale,
      y1: (bottomPx - rect.top) / scale,
    });
    rects.push({ left: rect.left, top: topPx, width: rect.width, height: bottomPx - topPx });
  }
  return { regions, rects };
}

// --- Voice indicator ------------------------------------------------------ //
// Reflects the engine's state in the footer's mic pill, for both ways of
// starting: a held key (Milestone 5) and the wake phrase (Milestone 8).

const micPill = document.getElementById("micPill");
const micLabel = document.getElementById("micLabel");
const micHint = document.getElementById("micHint");

// How long a recognized phrase stays on screen before the pill falls back to
// idle. Long enough to read a short command, short enough not to look stuck.
const HEARD_LINGER_MS = 2500;
let heardTimer = null;

// Overrides the mic pill's hint text once an async voice action (currently
// only highlighting) resolves, restarting the same linger timeout
// renderMicState's "heard" case uses — without this, a highlight's outcome
// would have to wait for the *next* utterance to ever be shown.
function setMicHint(text) {
  clearTimeout(heardTimer);
  micHint.textContent = text;
  heardTimer = setTimeout(() => voice.rest(), HEARD_LINGER_MS);
}

// What to tell the reader when nothing is happening. It has to name a way in
// they actually have: "Hold Space to talk" is useless advice for someone who
// switched push-to-talk off in favour of the wake phrase.
function idleHint(wake, pushToTalk) {
  // Organise pages' footer says how its grid works instead.
  if (isOrganiseOpen()) return ORGANISE_HINT;
  if (wake && pushToTalk) return "Hold Space, or say “Hey Lector”";
  if (wake) return "Say “Hey Lector” to start";
  return "Hold Space to talk";
}

function renderMicState({ state: micState, text, error, command, clarify, ocr, wake, pushToTalk, viaWake }) {
  clearTimeout(heardTimer);
  ocrFollowsMic(micState);
  micPill.classList.toggle("listening", micState === "listening");
  micPill.classList.toggle("unavailable", micState === "unavailable" || micState === "off");
  // Idle listening for the phrase is its own look: the microphone is open,
  // but nothing is being captured as a command, and showing the two the same
  // way would misrepresent one of them.
  micPill.classList.toggle("waking", micState === "idle" && Boolean(wake));
  if (Boolean(wake) !== wakeListening) {
    wakeListening = Boolean(wake);
    // Turning the wake phrase on mid-session leaves the recognizer holding
    // whatever vocabulary it was built with, so seed it with what is on
    // screen now rather than waiting for the reader to scroll.
    scheduleViewportPush();
  }
  // The outline is only ever painted by computeVoiceViewport() at the start
  // of a hold; every other state (heard, unavailable, idle) means the hold
  // is over, so this is the single place that takes it back down again.
  if (micState !== "listening") clearVoiceFocus();
  showFocusVoice(micState, text);

  switch (micState) {
    case "unavailable":
      micLabel.textContent = "Voice unavailable";
      // The reason belongs in the tooltip, not the footer: docs/PRD.md makes
      // voice an accelerator, so its absence is a quiet fact, not an alert.
      micPill.title = error || "Speech model not installed";
      micHint.textContent = "Mouse and keyboard work as usual";
      break;
    case "listening":
      micLabel.textContent = text ? `"${text}"` : "Listening...";
      micPill.title = "";
      micHint.textContent = viaWake
        ? "Say your command"
        : "Release Space when you're done";
      break;
    case "heard":
      micLabel.textContent = text ? `"${text}"` : "Didn't catch that";
      micPill.title = "";
      // A bare "highlight" over a scanned page not yet read: the words after
      // it were dropped because they could not have been heard, which is a
      // different story from "not a command I know".
      if (ocr) {
        showVoiceOcr(ocr);
        break;
      }
      // A near-miss (Milestone 8.3) gets its own hint, ahead of the generic
      // "not a command I know" — a phrase close enough to guess at deserves
      // a named guess, not the same catch-all a totally unrelated one gets.
      // It is informational only: saying the phrase again is what confirms
      // it, through the same recognition path as any other command, rather
      // than this reaching for a dedicated yes/no voice reply here.
      if (clarify) {
        micHint.textContent = `Did you mean “${clarify.matched}”? Say it again to confirm.`;
      } else if (text && !command) {
        // A phrase heard clearly but matching no command is worth naming out
        // loud: without it, "next pages" looks identical to a dead
        // microphone, and the reader has no way to tell that rephrasing is
        // what's needed.
        micHint.textContent = "Not a command I know — try “next page”";
      } else {
        micHint.textContent = idleHint(wake, pushToTalk);
      }
      // Back to rest through the engine rather than by rendering "idle"
      // directly: only it knows whether resting means ready, off, or
      // unavailable, and which activation modes to word the hint for.
      heardTimer = setTimeout(() => voice.rest(), HEARD_LINGER_MS);
      break;
    case "off":
      // Not a failure: docs/PRD.md makes voice an accelerator, and turning it
      // off is a supported choice, so this states the fact and stops there.
      micLabel.textContent = "Voice off";
      micPill.title = "Turn on push-to-talk or the wake phrase in Settings";
      micHint.textContent = "Mouse and keyboard work as usual";
      break;
    default:
      micLabel.textContent = wake ? "Listening for “Hey Lector”" : "Mic idle";
      micPill.title = "";
      micHint.textContent = idleHint(wake, pushToTalk);
  }
}
