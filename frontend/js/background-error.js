// A short note when something fails off-screen (hardening H3), shared by
// every page.
//
// Python's `threading.excepthook` (shared/app_log.py) logs the error, then
// calls `window.lectorBackgroundError()` through `Api.notify_background_error`.
// The reader gets one toast per page: an error that repeats (a voice thread
// failing on every chunk, say) must not stack up a column of them. The
// details are in the log, never on screen.

const BACKGROUND_ERROR_MESSAGE = "Something went wrong in the background. Lector kept running.";

let backgroundErrorShown = false;

window.lectorBackgroundError = function lectorBackgroundError() {
  if (backgroundErrorShown) return;
  backgroundErrorShown = true;
  showToast(BACKGROUND_ERROR_MESSAGE);
};
