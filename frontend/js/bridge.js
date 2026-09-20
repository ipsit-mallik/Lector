// Thin wrapper around window.pywebview.api (src/lector/api.py). Waits for
// pywebview's injection event before any call, since api.py's methods
// aren't attached to window.pywebview until the webview environment is
// ready — calling straight through on page load races that injection.
const bridgeReady = new Promise((resolve) => {
  if (window.pywebview && window.pywebview.api) {
    resolve();
  } else {
    window.addEventListener("pywebviewready", resolve, { once: true });
  }
});

async function callApi(method, ...args) {
  await bridgeReady;
  return window.pywebview.api[method](...args);
}
