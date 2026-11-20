"""The reader page still has every name it had before reading.js was split by
job (docs/ARCHITECTURE.md § "Reader page layout"), and still loads cleanly.

* **The names.** Every top-level function and value reading.js declared
  before the split is listed below, taken from the file as it was. Each is
  still declared, exactly once, in one of the reader's scripts. Other scripts
  (highlight-options.js and organise.js call `adoptState`), Python's close
  gatekeeper (`promptSaveIfDirty`, through evaluate_js) and the
  `configure*()` hooks reach the reader by these names.
* **Declarations only in the modules.** Each `reading-*.js` runs, in load
  order, with nothing but `document.getElementById`, `Map` and `Set`
  available. Anything a module did on load (a listener, an observer, a call
  into another script) would throw, so start-up stays in reading.js, in one
  place and in one order.
* **One owner for `state`.** It is assigned only in the script that declares
  it (tests/test_reader_state_owner.py holds the rest of that rule).
* **The page loads.** A hidden window opens a PDF in the reader. Every
  function is there under its name, the document is open, and nothing went
  to the console as an error or was thrown.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reader_scripts import reader_script_paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

# Taken from reading.js before the split (2465 lines). Do not add to these to
# make a test pass: a name that has gone is a name something may still call.
FUNCTIONS = frozenset({
    "activeScrollEl", "adoptState", "afterAnnotationChange", "applyHighlight",
    "applyLayoutChrome", "applyPageScrollFraction", "applyWheelZoom", "applyZoomPreview",
    "askToSaveIfDirty", "b64ToDataUrl", "beginSelection", "bindSelection", "buildStrip",
    "cancelSelection", "captureStripAnchor", "centerActivePage", "charIndexAt",
    "clearSelection", "clearVoiceFocus", "closeApp", "commitInkStroke", "commitPageInput",
    "computeBookViewport", "computeStripViewport", "computeVoiceViewport",
    "currentPageReadFraction", "currentStripPage", "cycleTheme", "endSelection",
    "enterFocusMode", "finishSave", "forgetPageChars", "forgetPageThumbnails",
    "getCurrentPageEl", "getPageChars", "getPageScrollFraction", "goHome", "goNext", "goPrev",
    "goToPage", "idleHint", "init", "jumpToPageIndex", "leaveReadingTo", "markStripDirty",
    "mergeLineBoxes", "nearestCharIndex", "observeStripPages", "ocrMessage", "onSelectionMove",
    "onStripScroll", "openTool", "openToolOutput", "pageScale", "paintSelection",
    "pickCopyPath", "prefetchVisiblePage", "promptSaveIfDirty", "readPageForSelection", "redo",
    "refresh", "refreshBook", "refreshStrip", "renderHighlightCount", "renderMicState",
    "renderStripPage", "renderVoiceFocus", "repaintSelectionOnScroll", "restoreStripAnchor",
    "revertPageInput", "saveCopy", "saveFromMenu", "scheduleOcrPrefetch",
    "scheduleProgressScroll", "scheduleViewportPush", "scheduleZoomRefresh", "selectionRange",
    "setHighlightMode", "setMicHint", "setUndoButton", "setZoomPct", "showImage",
    "showVoiceOcr", "startOrganise", "stepHistory", "stripPageTop", "toggleLayout",
    "toggleProgressBar", "undo", "updateChrome", "updateStripPageImage", "updateUndoButtons",
    "voiceHighlight", "voiceScroll", "voiceStyleMode", "voiceThickness", "watchPixelRatio",
    "zoomFitGeometry", "zoomIn", "zoomOut",
})
VALUES = frozenset({
    "CLICK_SLOP_PX", "HEARD_LINGER_MS", "HIT_PAD", "INK_NOT_DRAWN", "OCR_DWELL_MS",
    "OCR_MESSAGES", "OCR_READING_TOAST_MS", "OCR_READ_FOR_MOUSE", "OCR_READ_FOR_VOICE",
    "OPEN_ORGANISE_NOTE", "ORGANISE_VOICE_ACTIONS", "THEME_ORDER", "VIEWPORT_PUSH_DEBOUNCE_MS",
    "VOICE_ACTIONS", "VOICE_MARKUP_VERBS", "VOICE_SCROLL_FRACTION", "WHEEL_ZOOM_DOUBLING_PX",
    "ZOOM_REFRESH_DEBOUNCE_MS", "ZOOM_SHORTCUTS", "backBtn", "bookScroll", "charCache",
    "charData", "dialogOpenAtKey", "docTitle", "heardTimer", "highlightBtn",
    "highlightCountLbl", "highlightMode", "historyQueue", "layoutBtn", "layoutMode", "micHint",
    "micLabel", "micPill", "mouseOcrWaits", "nextBtn", "ocrDwellTimer", "ocrPrefetchPage",
    "openFileDialog", "pageArea", "pageCard", "pageInput", "pageInputSelectOnClick",
    "pagePointSizes", "pageResizeObserver", "pageSuffix", "pageSurface", "prevBtn",
    "progressScrollTicking", "redoBtn", "referenceFooterBtn", "refitFrame", "renderedZoom",
    "requestedZoomPct", "saveAsDialog", "selection", "selectionLayer", "state",
    "stripBuildPromise", "stripContainer", "stripDirty", "stripObserver", "stripPageEls",
    "stripRebuilding", "stripScroll", "stripScrollTicking", "themeBtn", "undoBtn",
    "viewportPushTimer", "voice", "voiceEnabled", "voiceFocusLayer", "voiceOcrWait",
    "wakeListening", "wheelZoomDelta", "wheelZoomFrame", "zoomCallInFlight", "zoomInBtn",
    "zoomOutBtn", "zoomRefreshTimer",
})

# Names other files reach directly, not through a configure*() hook.
EXTERNAL = {
    "adoptState": "highlight-options.js and organise.js hand bridge replies to it",
    "promptSaveIfDirty": "Python's close gatekeeper calls it (bridge/window.py)",
    "state": "highlight-options.js and organise.js read the reader's state",
}

# The one console error the browser itself raises while a resizing page
# settles. It is not a script error (no exception comes with it), and the
# reader raised it before the split too.
BENIGN = ("ResizeObserver loop completed with undelivered notifications.",)


def _declared(source: str) -> list[tuple[str, str]]:
    return re.findall(r"^(?:async )?function ([A-Za-z0-9_$]+)\(|^(?:const|let) ([A-Za-z0-9_$]+)", source, re.M)


class NamesTests(unittest.TestCase):
    def test_every_name_is_still_declared_exactly_once(self):
        where = {}
        for path in reader_script_paths():
            for func, value in _declared(path.read_text(encoding="utf-8")):
                where.setdefault(func or value, []).append((path.name, "function" if func else "value"))
        for name in sorted(FUNCTIONS | VALUES):
            with self.subTest(name=name):
                self.assertEqual(len(where.get(name, [])), 1, where.get(name))
                if name in FUNCTIONS:
                    self.assertEqual(where[name][0][1], "function")

    def test_the_external_names_are_among_them(self):
        self.assertLessEqual(set(EXTERNAL), FUNCTIONS | VALUES)

    def test_reading_js_loads_last(self):
        self.assertEqual(reader_script_paths()[-1].name, "reading.js")

    def test_state_is_assigned_only_where_it_is_declared(self):
        owners = [p for p in reader_script_paths() if re.search(r"^let state = ", p.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(len(owners), 1, owners)
        for path in reader_script_paths():
            if path == owners[0]:
                continue
            with self.subTest(script=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"(?<![.\w$])state\s*=[^=>]")


MODULES_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const element = () => ({});
const context = vm.createContext({ document: { getElementById: element }, Map, Set });
const out = [];
for (const file of process.argv.slice(2)) {
  try {
    vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
    out.push({ file, ok: true });
  } catch (err) {
    out.push({ file, ok: false, error: String(err) });
  }
}
console.log(JSON.stringify(out));
"""


@unittest.skipIf(NODE is None, "Node is not installed")
class ModulesOnlyDeclareTests(unittest.TestCase):
    def test_each_module_runs_with_nothing_but_element_lookups(self):
        modules = [p for p in reader_script_paths() if p.name != "reading.js"]
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "modules.js"
            harness.write_text(MODULES_HARNESS, encoding="utf-8")
            result = subprocess.run([NODE, str(harness), *map(str, modules)], capture_output=True,
                                    text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for entry in json.loads(result.stdout):
            with self.subTest(module=Path(entry["file"]).name):
                self.assertTrue(entry["ok"], entry.get("error"))


# Run in its own process: pywebview's loop owns the main thread until the
# window closes. Kept behind __main__ because the OCR worker is a spawned
# process that imports the main script again.
PROBE = r'''
import json, os, sys, tempfile, threading, time, shutil
from pathlib import Path

def main(root, out_file, names):
    work = Path(tempfile.mkdtemp(prefix="lector-reader-load-"))
    os.environ["APPDATA"] = str(work / "appdata")
    (work / "appdata").mkdir()
    sys.path.insert(0, str(root / "src"))
    import pymupdf
    import webview
    from lector.api import Api
    from lector.features.onboarding import state as onboarding
    from lector.features.settings import store as settings
    from lector.shared.frontend_server import FrontendServer

    # The page as shipped, with one script put first that records every
    # error from the very start of loading, before any reader script runs.
    frontend = work / "frontend"
    shutil.copytree(root / "frontend", frontend)
    page = frontend / "pages" / "reading.html"
    capture = """<script>
      window.__loadErrors = [];
      const __push = (m) => window.__loadErrors.push(String(m));
      window.addEventListener("error", (e) => __push(e.message));
      window.addEventListener("unhandledrejection", (e) => __push(e.reason && (e.reason.stack || e.reason.message || e.reason)));
      const __consoleError = console.error;
      console.error = function (...a) { __push(a.map(String).join(" ")); return __consoleError.apply(this, a); };
    </script>"""
    html = page.read_text(encoding="utf-8")
    assert "<head>" in html
    page.write_text(html.replace("<head>", "<head>" + capture, 1), encoding="utf-8")

    pdf = work / "Load check.pdf"
    doc = pymupdf.open()
    for n in range(3):
        doc.new_page().insert_text((72, 100), f"Page {n + 1}: the quick brown fox.", fontsize=14)
    doc.save(pdf)
    doc.close()
    onboarding.mark_seen()
    settings.set_voice_activation(False, False)

    api = Api(work / "logs")
    api.open_pdf(str(pdf))
    server = FrontendServer(frontend, settings.get_theme, lambda: {})
    server.start()
    window = webview.create_window("Reader load check", url=server.url("pages/reading.html"), js_api=api,
                                   hidden=True, width=1280, height=800)
    result = {"exception": None}

    def drive():
        try:
            end = time.time() + 40
            result["ready"] = False
            while time.time() < end and not result["ready"]:
                try:
                    result["ready"] = bool(window.evaluate_js(
                        "typeof state !== 'undefined' && state.is_open === true && "
                        "document.getElementById('pageSurface').naturalWidth > 0"))
                except Exception:
                    pass
                time.sleep(0.2)
            result["where"] = window.evaluate_js("location.pathname")
            time.sleep(1.5)  # anything init() set off after the page was drawn
            # `typeof <name>` reads a let/const too, which window[name] cannot;
            # the names are the fixed list above, each checked to be an identifier.
            assert all(n.replace("_", "a").replace("$", "a").isalnum() for n in names)
            probe = ("(" + json.dumps(names) + ").map((n) => { try { return [n, eval('typeof ' + n)]; }"
                     " catch (e) { return [n, 'threw: ' + e.message]; } })")
            result["types"] = dict(window.evaluate_js(probe))
            result["is_open"] = window.evaluate_js("typeof state !== 'undefined' && state.is_open === true")
            result["errors"] = window.evaluate_js("window.__loadErrors")
        except Exception as exc:
            result["exception"] = repr(exc)
        finally:
            Path(out_file).write_text(json.dumps(result), encoding="utf-8")
            window.destroy()

    threading.Thread(target=drive, daemon=True).start()
    try:
        webview.start(private_mode=True)
    finally:
        api.shutdown_ocr()
        server.stop()

if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")))
'''


def _webview_available() -> bool:
    if sys.platform != "win32" or os.environ.get("LECTOR_SKIP_WINDOW_TESTS"):
        return False
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(_webview_available(), "needs pywebview on Windows (WebView2)")
class PageLoadsTests(unittest.TestCase):
    """Opens a real (hidden) window: about ten seconds."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            probe, out, names = Path(tmp) / "probe.py", Path(tmp) / "out.json", Path(tmp) / "names.json"
            probe.write_text(PROBE, encoding="utf-8")
            names.write_text(json.dumps(sorted(FUNCTIONS | VALUES)), encoding="utf-8")
            run = subprocess.run([sys.executable, str(probe), str(ROOT), str(out), str(names)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=120)
            cls.stderr = run.stderr
            cls.result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

    def setUp(self):
        self.assertIsNotNone(self.result, self.stderr[-2000:])
        self.assertIsNone(self.result["exception"], self.result["exception"])

    def test_the_document_opens(self):
        self.assertTrue(self.result["ready"], self.result["where"])
        self.assertTrue(self.result["is_open"])

    def test_every_function_is_there_under_its_name(self):
        types = self.result["types"]
        for name in sorted(FUNCTIONS):
            with self.subTest(name=name):
                self.assertEqual(types.get(name), "function")
        for name in sorted(VALUES):
            with self.subTest(name=name):
                self.assertNotEqual(types.get(name), "undefined")

    def test_nothing_went_wrong_on_load(self):
        errors = [e for e in self.result["errors"] if e not in BENIGN]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
