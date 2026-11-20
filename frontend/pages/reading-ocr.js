// Scanned pages: reading their text when they are pressed on, for a voice
// highlight, and ahead of time while voice is on.

// --- Scanned pages (OCR) --------------------------------------------------- //
// A scanned page is a picture with no text layer, so it has nothing to select
// or match until Python has read its text (features/reading/ocr.py). That
// happens one page at a time and never on open: when the reader presses on
// the page to highlight, when a voice highlight there comes up empty, or —
// with voice on — once the page has stayed on screen for OCR_DWELL_MS, so the
// words are usually already in the recognizer's vocabulary by the time the
// reader speaks. Every outcome is said out loud, in a toast for the mouse and
// in the mic pill for voice: a press that silently did nothing would look
// exactly like a broken highlighter.

const OCR_MESSAGES = {
  running: "Reading text on this page…",
  no_text: "No text found on this page",
  unavailable: "Text recognition isn't installed",
  error: "Couldn't read the text on this page",
};
const OCR_READ_FOR_MOUSE = "Text read — drag to highlight";
const OCR_READ_FOR_VOICE = "Text read — say it again";
// How long a scanned page must stay on screen, unscrolled, before it is read
// ahead of a voice highlight: long enough that paging past it costs nothing.
const OCR_DWELL_MS = 1000;
// The "Reading…" toast outlives the backend's own OCR timeout (ocr.py's
// OCR_TIMEOUT_S), so it is always the lector:ocr outcome that takes it down.
const OCR_READING_TOAST_MS = 150000;

// Page index -> dismiss() of the "Reading…" toast a press on it put up.
const mouseOcrWaits = new Map();
// The page the mic pill is showing "Reading…" for, if any.
let voiceOcrWait = null;
// The page being read ahead for voice — one at a time.
let ocrPrefetchPage = null;
let ocrDwellTimer = null;
// Whether voice can be used at all right now (mic state not off/unavailable).
let voiceEnabled = false;

function ocrMessage(status) {
  return OCR_MESSAGES[status] || OCR_MESSAGES.error;
}

// Only a page not read yet gets here (freehand.js: once read, a press is a
// text highlight or a drawing like on any page).
async function readPageForSelection(pageIndex) {
  if (mouseOcrWaits.has(pageIndex)) return; // already reading; its toast is up
  let result;
  try {
    result = await callApi("ocr_page", pageIndex);
  } catch (err) {
    console.error("ocr_page failed:", err);
    showToast(OCR_MESSAGES.error);
    return;
  }
  if (result.status === "running") {
    mouseOcrWaits.set(pageIndex, showToast(OCR_MESSAGES.running, { durationMs: OCR_READING_TOAST_MS }));
    return;
  }
  // Finished between the press and now (a read ahead for voice, say).
  forgetPageChars(pageIndex);
  showToast(result.status === "done" ? OCR_READ_FOR_MOUSE : ocrMessage(result.status));
}

// The mic pill's half of the story: `ocr` is {page_index, status} from a
// voice highlight that could not have matched because the page's text was
// not ready when the reader started speaking.
function showVoiceOcr({ page_index: pageIndex, status }) {
  if (status === "running") {
    // Held, not lingered: it stays until the page has been read.
    clearTimeout(heardTimer);
    micHint.textContent = OCR_MESSAGES.running;
    voiceOcrWait = pageIndex;
    return;
  }
  voiceOcrWait = null;
  setMicHint(status === "done" ? OCR_READ_FOR_VOICE : ocrMessage(status));
}

// Python's answer for a page being read (registered in reading.js).
function onOcrEvent(ev) {
  const { page_index: pageIndex, status } = ev.detail || {};
  // The boxes cached for this page were the empty pre-OCR ones.
  forgetPageChars(pageIndex);
  const dismiss = mouseOcrWaits.get(pageIndex);
  if (dismiss) {
    mouseOcrWaits.delete(pageIndex);
    dismiss();
    showToast(status === "done" ? OCR_READ_FOR_MOUSE : ocrMessage(status));
  }
  // Not over a new utterance the reader has already started.
  if (voiceOcrWait === pageIndex && !micPill.classList.contains("listening")) {
    voiceOcrWait = null;
    setMicHint(status === "done" ? OCR_READ_FOR_VOICE : ocrMessage(status));
  }
  if (ocrPrefetchPage === pageIndex) ocrPrefetchPage = null;
  scheduleOcrPrefetch(); // another scanned page may be on screen too
}

// Restarted on every scroll and page change, so only a page the reader has
// actually stopped on is read ahead.
function scheduleOcrPrefetch() {
  clearTimeout(ocrDwellTimer);
  if (!voiceEnabled || ocrPrefetchPage !== null || !state.is_open) return;
  ocrDwellTimer = setTimeout(prefetchVisiblePage, OCR_DWELL_MS);
}

async function prefetchVisiblePage() {
  if (!voiceEnabled || ocrPrefetchPage !== null || !state.is_open) return;
  // Not computeVoiceViewport(): that paints the "listening here" outline.
  const { regions } =
    layoutMode === "book" ? await computeBookViewport() : await computeStripViewport();
  for (const { page_index: pageIndex } of regions) {
    const page = await getPageChars(pageIndex);
    if (page.ocr !== "needed") continue;
    ocrPrefetchPage = pageIndex;
    try {
      const result = await callApi("ocr_page", pageIndex);
      if (result.status !== "running") {
        ocrPrefetchPage = null;
        forgetPageChars(pageIndex);
      }
    } catch (err) {
      // Nobody asked for this read, so nothing to tell the reader; a
      // highlight attempt on the page will ask again and report properly.
      ocrPrefetchPage = null;
      console.error("OCR prefetch failed:", err);
    }
    return;
  }
}

// The mic pill's state, as it bears on scanned pages (renderMicState,
// reading-mic.js): a new utterance drops the wait it was showing, and voice
// coming on or going off starts or stops reading ahead.
function ocrFollowsMic(micState) {
  // A new utterance replaces whatever the pill was waiting to report.
  if (micState === "listening") voiceOcrWait = null;
  const enabled = micState !== "off" && micState !== "unavailable";
  if (enabled !== voiceEnabled) {
    voiceEnabled = enabled;
    scheduleOcrPrefetch();
  }
}
