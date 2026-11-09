// The file tools' running job, on every page (docs/PRD.md § "Post-v1: file
// tools"). Python runs one job at a time and pushes its progress as
// `lector:filejob` events (api.py's `_on_file_job_event`); this follows them
// for whatever shows the job — the tool's dialog, the reader's footer pill —
// and, when nobody on screen is showing a job that ends, says how it ended in
// a toast (with Open and Show in folder).
//
// It also owns the question the close gatekeeper asks while a job runs
// (`confirmStopFileJob`, called from api.py's `handle_window_closing`).
"use strict";

// Sizes as the reader sees them: "860 KB", "6.2 MB", "1.4 GB".
function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const mb = bytes / (1024 * 1024);
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

// How a sentence takes a job's `where` (destinations.py's where_phrase):
// "Saved as X.pdf, beside the original." but "Saved as X.pdf in Documents."
function savePlaceClause(where) {
  if (!where) return "";
  return where.startsWith("beside") ? `, ${where}` : ` ${where}`;
}

// "7", "7 and 19", "3, 7 and 19".
function listPages(pages) {
  if (pages.length <= 1) return pages.join("");
  return `${pages.slice(0, -1).join(", ")} and ${pages[pages.length - 1]}`;
}

// What each tool is doing, for the pill.
const FILE_JOB_VERBS = {
  compress: "Compressing",
  searchable: "Reading text",
  export: "Exporting",
  convert: "Converting",
};

function isCountedJob(event) {
  return Boolean(event && event.unit && event.unit !== "step" && event.total);
}

// The phase line in a dialog's Working state: "Exporting · image 34 of 120",
// or the phase alone for a whole-file step.
function fileJobPhaseText(event) {
  if (!event || !event.phase) return "Starting…";
  if (!isCountedJob(event)) return event.phase;
  return `${event.phase} · ${event.unit} ${Math.max(1, event.current)} of ${event.total}`;
}

// 0 to 1, or null when there is nothing to count (Compress's image pass:
// MuPDF rewrites every image in one call).
function fileJobFraction(event) {
  if (!event || !event.total || event.phase === "Compressing images") return null;
  return Math.min(1, Math.max(0, event.current / event.total));
}

// The footer pill's words: "Compressing 34 / 120", or "Compressing…".
function fileJobPillText(event) {
  const verb = FILE_JOB_VERBS[event.tool] || "Working";
  return isCountedJob(event) ? `${verb} ${Math.max(1, event.current)} / ${event.total}` : `${verb}…`;
}

// The toast for a job that ended with nobody watching it, or null for one
// that needs none (cancelled). It says where the output went, as the
// dialog's result line does ("… saved in Documents").
function fileJobOutcomeToast(event) {
  if (event.state === "failed") return { message: event.error };
  if (event.state !== "done") return null;
  const r = event.result || {};
  const where = r.where ? ` ${r.where}` : "";
  switch (event.tool) {
    case "compress":
      if (r.compact) return { message: "This PDF is already compact, so no file was written." };
      return { message: `Compressed copy saved${where} · ${formatFileSize(r.after)}`, path: r.path, open: true };
    case "searchable":
      if (r.written === false) return { message: "No readable text was found, so no file was written." };
      return {
        message: `Searchable copy saved${where} · text added to ${plural(r.added, "page", "pages")}`,
        path: r.path,
        open: true,
      };
    case "export":
      return { message: `${plural(r.count, "image", "images")} exported${where}`, path: r.path, open: false };
    case "convert":
      return { message: `Created ${r.name}${where} · ${plural(r.pages, "page", "pages")}`, path: r.path, open: true };
    default:
      return null;
  }
}

async function showFileJobOutput(path) {
  const { error } = await callApi("show_in_folder", path);
  if (error) showToast(error);
}

const FileJobs = (() => {
  let latest = null;
  let watched = null; // the id of the job a dialog on screen is showing
  const listeners = new Set();
  let openFile = null;

  function notify(event) {
    for (const listener of listeners) {
      try {
        listener(event);
      } catch (err) {
        console.error("file-jobs: a listener failed", err);
      }
    }
  }

  function toastOutcome(event) {
    const toast = fileJobOutcomeToast(event);
    if (!toast) return;
    const actions = [];
    if (toast.path && toast.open && openFile) {
      actions.push({ label: "Open", onAction: () => openFile(toast.path) });
    }
    if (toast.path) {
      actions.push({ label: "Show in folder", onAction: () => showFileJobOutput(toast.path) });
    }
    showToast(toast.message, { actions, durationMs: actions.length ? 8000 : undefined });
  }

  window.addEventListener("lector:filejob", (ev) => {
    const event = ev.detail || {};
    latest = event.state === "working" ? event : null;
    notify(event);
    if (event.state === "working") return;
    if (watched === event.id) watched = null;
    else toastOutcome(event);
  });

  return {
    // `fn(event)` for every progress and end event. Returns the unsubscribe.
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    // The running job's latest progress, or null.
    current: () => latest,
    // A dialog showing job `id` says so, so its end shows there instead of
    // as a toast; `null` hands it back (Keep reading).
    watch(id) {
      watched = id;
    },
    // Ask the backend for the running job, for a page that just loaded.
    async sync() {
      try {
        latest = await callApi("get_file_job");
      } catch (err) {
        latest = null;
      }
      if (latest) notify(latest);
      return latest;
    },
    // How this page opens a file a job wrote (the reader asks about unsaved
    // highlights first; Home opens it straight away).
    configure({ open }) {
      openFile = open;
    },
  };
})();

// "A file job is still running. Stop it and close?" — the close
// gatekeeper's question (api.py's `handle_window_closing`), on whatever page
// is showing. Resolves true for Stop and close. Esc, Keep running, or
// "cancel" by voice keep the job running; "confirm" stops it. Static markup:
// nothing in it comes from the reader or a file.
const STOP_JOB_MARKUP = `
  <div class="dialog-scrim dialog-scrim--enter voice-dialog" id="stopJobScrim" hidden>
    <div class="dialog-card" role="dialog" aria-modal="true" aria-labelledby="stopJobTitle"
         aria-describedby="stopJobDesc" tabindex="-1">
      <div class="dialog-body">
        <div class="dialog-header">
          <div class="dialog-badge" data-icon="close"></div>
          <div>
            <p class="dialog-title" id="stopJobTitle">A file job is still running</p>
            <p class="dialog-desc" id="stopJobDesc">Stop it and close? Stopping writes nothing, and the original stays as it is.</p>
          </div>
        </div>
      </div>
      <div class="dialog-footer">
        <span class="hint">Say “confirm” to stop it, or “cancel”.</span>
        <button class="btn btn-secondary" type="button" id="stopJobKeepBtn">Keep running</button>
        <button class="btn" type="button" id="stopJobStopBtn">Stop and close</button>
      </div>
    </div>
  </div>`;

const StopJobDialog = (() => {
  let scrim = null;
  let settle = null;
  let release = null;
  let scope = null;

  function build() {
    if (scrim) return;
    document.body.insertAdjacentHTML("beforeend", STOP_JOB_MARKUP);
    scrim = document.getElementById("stopJobScrim");
    mountIcons(scrim);
    scope = createVoiceScope("file_tool");
    document.getElementById("stopJobKeepBtn").addEventListener("click", () => close(false));
    document.getElementById("stopJobStopBtn").addEventListener("click", () => close(true));
    document.addEventListener("keydown", (ev) => {
      if (!scrim.hidden && ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        close(false);
      }
    });
  }

  function close(stop) {
    if (!scrim || scrim.hidden) return;
    scrim.hidden = true;
    if (release) release();
    release = null;
    scope.leave();
    const resolve = settle;
    settle = null;
    if (resolve) resolve(stop);
  }

  function ask() {
    build();
    if (scrim.hidden) {
      scrim.hidden = false;
      release = trapFocus(scrim.querySelector(".dialog-card"));
      document.getElementById("stopJobKeepBtn").focus();
      scope.enter();
    }
    return new Promise((resolve) => {
      const previous = settle;
      settle = (value) => {
        if (previous) previous(value);
        resolve(value);
      };
    });
  }

  return {
    ask,
    isOpen: () => Boolean(scrim) && !scrim.hidden,
    handleCommand(command) {
      if (command.intent === "TOOL_CONFIRM") close(true);
      else if (command.intent === "CANCEL") close(false);
    },
  };
})();

function confirmStopFileJob() {
  return StopJobDialog.ask();
}
