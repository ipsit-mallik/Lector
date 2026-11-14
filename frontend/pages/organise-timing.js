// Organise pages' timing probe: how long one action takes to show, from the
// input to the first frame that shows it. Off unless Lector runs with debug
// logging (LECTOR_DEBUG=1, docs/ARCHITECTURE.md § "Errors and logging"), when
// organise_begin says so; then each action's numbers go to Python's log at
// DEBUG, and nowhere else. Only times and counts: never a page's content.
//
// What one report holds, in ms after the input:
//   bridge   the call to Python starting
//   python   Python's answer arriving
//   render   the grid showing the answer (or, optimistically, the action)
//   frame    the first frame after the grid first changed
//   settled  the last frame of the action's animation (none without one)
//   thumbs   thumbnail requests while the action was open, and `thumbsDone`,
//            when the last of them was painted
"use strict";

const ORGANISE_TIMING_QUIET_MS = 1500;

const OrganiseTiming = (() => {
  let enabled = false;
  let run = null;
  let quietTimer = 0;

  const now = () => performance.now();

  function since(of) {
    return Math.round((now() - of.input) * 10) / 10;
  }

  function finish() {
    clearTimeout(quietTimer);
    if (!run) return;
    const { input, open, ...numbers } = run;
    run = null;
    callApi("organise_debug_timing", numbers).catch(() => {});
  }

  // The run ends once nothing has happened for a while (thumbnails included).
  function touch() {
    clearTimeout(quietTimer);
    quietTimer = setTimeout(finish, ORGANISE_TIMING_QUIET_MS);
  }

  // `of` is the run a mark belongs to (an answer from Python belongs to the
  // action that asked); by default the latest.
  function mark(name, of = run) {
    if (!of || of !== run || name in of) return;
    of[name] = since(of);
    touch();
  }

  return {
    enable(on) {
      enabled = Boolean(on);
    },
    // An input that starts an action; `at` is the event's timeStamp, when
    // there was an event. A new action ends the last one's report. Returns
    // the run (null when off), for marks that come later.
    input(kind, pages, at) {
      if (!enabled) return null;
      finish();
      run = { kind, pages, input: typeof at === "number" ? at : now(), thumbs: 0, open: 0 };
      touch();
      return run;
    },
    current: () => run,
    mark,
    // The grid has changed: the next frame is the first that shows it.
    changed() {
      if (!run || "frame" in run) return;
      const current = run;
      requestAnimationFrame(() => {
        if (run === current) mark("frame");
      });
    },
    // A thumbnail requested now; call what this returns once it is painted.
    thumbRequested() {
      if (!run) return () => {};
      const current = run;
      current.thumbs += 1;
      current.open += 1;
      touch();
      return () => {
        current.open -= 1;
        requestAnimationFrame(() => {
          if (run === current && !current.open) {
            current.thumbsDone = since(current);
            touch();
          }
        });
      };
    },
  };
})();
