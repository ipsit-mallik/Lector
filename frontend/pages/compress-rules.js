// Compress's rules for its dialog (compress-panel.js), with no DOM: its
// sizes, presets and words, which step a limit gets, and whether a result is
// newer than the one shown. `compressChoice` is compress_plan.choose and
// `compressDecision` is limit_plan.decide, for the page: the job checks again
// what the page offered, so each pair must agree, and
// tests/test_compress_panel_behavior.py runs both over the same cases.
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
// A big file's estimates decide a pill only with room to spare
// (limit_plan.ENABLE_PERCENT, DIM_PERCENT, FLOOR_STEPS).
const COMPRESS_ENABLE_PERCENT = 80;
const COMPRESS_DIM_PERCENT = 130;
const COMPRESS_FLOOR_STEPS = [COMPRESS_LADDER_STEPS - 1, 0];
const COMPRESS_NO_SPACE_TEXT = "Not enough free disk space to check sizes. Free up space or choose another Save in folder.";
const COMPRESS_DROPPED = {
  bw: "To fit, Lector used black and white.",
  quality: "To fit, Lector lowered the image quality.",
};

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

// A big file's estimates (step → { size, kind }, or null for one the sample
// couldn't make) as rows, with the real results over them.
function compressMerged(steps, estimates) {
  const out = {};
  for (const [step, row] of Object.entries(estimates || {})) {
    out[step] = row ? { state: "done", size: row.size, kind: row.kind } : { state: "failed" };
  }
  for (const [step, row] of Object.entries(steps)) {
    if (row && (row.state === "done" || row.state === "failed")) out[step] = row;
  }
  return out;
}

function compressHasEstimates(estimates) {
  return Boolean(estimates) && Object.keys(estimates).length > 0;
}

// A floor step known to be over the limit: really, or estimated at
// COMPRESS_DIM_PERCENT of it or more.
function compressOverFloor(steps, estimates, step, limit) {
  const row = steps[step];
  if (row && row.state === "done") return !compressFits(row.size, limit);
  const estimate = estimates[step];
  return Boolean(estimate) && estimate.size * 100 >= limit * COMPRESS_DIM_PERCENT;
}

// The nearest step above `best` that might still fit, or null when every
// better step is ruled out: really over, or estimated at
// COMPRESS_DIM_PERCENT or more (sizes fall down the ladder, so that rules
// out the ones above it too). A step that really failed is passed over.
// limit_plan._doubt.
function compressDoubt(steps, estimates, best, limit) {
  for (let i = best - 1; i >= 0; i--) {
    const row = steps[i];
    if (row && row.state === "failed") continue;
    if (row && row.state === "done") return null;
    const estimate = estimates[i];
    if (estimate && estimate.size * 100 >= limit * COMPRESS_DIM_PERCENT) return null;
    return i;
  }
  return null;
}

// A limit's answer from the real results and a big file's estimates:
// limit_plan.decide, which the job checks again, so the two must agree.
// An estimate decides only with room to spare: enabled at
// COMPRESS_ENABLE_PERCENT of the limit or less, too small at
// COMPRESS_DIM_PERCENT or more; anything between waits for a real step.
// A step with room but a better one in doubt is reachable, not final.
function compressDecision(steps, estimates, limit) {
  const real = compressChoice(steps, limit);
  if (real.settled || !compressHasEstimates(estimates)) return real;
  const best = compressChoice(compressMerged(steps, estimates), limit);
  if (best.step === null) {
    const tooSmall = best.settled && COMPRESS_FLOOR_STEPS.every((step) => compressOverFloor(steps, estimates, step, limit));
    return tooSmall ? best : real;
  }
  const isReal = Boolean(steps[best.step]) && steps[best.step].state === "done";
  if (!best.settled || (!isReal && estimates[best.step].size * 100 > limit * COMPRESS_ENABLE_PERCENT)) return real;
  return { step: best.step, settled: compressDoubt(steps, estimates, best.step, limit) === null };
}

// Whether Compress can run for a limit: its answer is final, and after "not
// enough free disk space" only on a step really made (its size is known
// for certain; one decided on an estimate would still have to be made).
function compressLimitReady(steps, estimates, limit, nospace) {
  const choice = compressDecision(steps, estimates, limit);
  if (choice.step === null || !choice.settled) return false;
  return !nospace || (Boolean(steps[choice.step]) && steps[choice.step].state === "done");
}

// The smallest result known so far (real, or estimated), or null.
function compressFloor(steps, estimates = null) {
  const sizes = Object.values(compressMerged(steps, estimates))
    .filter((row) => row && row.state === "done")
    .map((row) => row.size);
  return sizes.length ? Math.min(...sizes) : null;
}

// A limit's state for this file: "hidden" (at or above its size: it would
// do nothing), "reachable", "too_small", or "checking".
function compressLimitState(limit, fileSize, steps, estimates = null) {
  if (limit >= fileSize) return "hidden";
  const choice = compressDecision(steps, estimates, limit);
  if (choice.step !== null) return "reachable";
  return choice.settled ? "too_small" : "checking";
}

// Whether a result for a step (or the estimates, or the free-space note)
// is newer than the one shown: each carries a sequence number, and one
// overtaken on its way (bridge calls and events run on their own threads)
// is dropped. Records the newer one's.
function compressNewer(seen, key, seq) {
  if (typeof seq !== "number") return true;
  if (seq <= (seen[key] || 0)) return false;
  seen[key] = seq;
  return true;
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
