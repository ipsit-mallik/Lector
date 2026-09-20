# Tech Stack

## Language

**Python.** Matches existing developer comfort; avoids adding a new language's learning curve on top of an already ambitious solo project.

## Desktop GUI: pywebview (HTML/CSS/JS frontend, Python backend) — supersedes PySide6

**Revision note (post-Milestone 4):** PySide6/Qt was the original choice and was implemented through Milestone 4. It was replaced after repeated, verified failures to translate `docs/DESIGN_SYSTEM.md` into correct QSS — including native-decorated dialogs appearing instead of frameless overlays, unstyled default radio indicators, and unbound selection-state borders, surviving multiple attempts with explicit literal values (exact hex codes, pixel sizes, pseudo-state selectors). The root cause was the QSS *translation step* itself, not any single prompt being underspecified. Switching to pywebview removes that translation step: the actual `.dc.html`/CSS mockup files can be used close to as-is, rather than re-implemented in a different styling language each time.

pywebview embeds the OS's already-installed native webview (WebView2 on Windows, WKWebView on Mac) to render real HTML/CSS/JS — it does **not** bundle Chromium, so it does not reintroduce the Electron-style bloat this project explicitly rejected. Python remains the application's owning process; the webview is embedded inside it, not a separate app.

- **PDF pages**: still rendered by PyMuPDF (unchanged), then handed to the HTML view as images (PNG/base64) rather than drawn via `QPainter`.
- **Focus indicator & highlight overlays**: PyMuPDF's word/line bounding boxes (PDF point-coordinates) are translated into absolutely-positioned, scaled `<div>` overlays in CSS — a more direct mapping than Qt's `QGraphicsView` coordinate work, and CSS transitions handle focus-box movement animation without manual `QPropertyAnimation`.
- **Python↔JS bridge**: carries discrete events only (a voice command firing, a highlight committed, a page turn) — not a continuous per-frame stream. This bounds the indirection cost to something acceptable for this interaction pattern; it would not be acceptable for something needing per-frame two-way sync.
- **Known caveat**: on a genuinely bare-bones Windows install, the WebView2 Runtime may need a small (~2MB) redistributable installed alongside the app if not already present (it ships with Windows 10/11 and current Edge by default). Far short of a Chromium-bundle concern, but worth knowing at packaging time.

Rejected alternatives and why (unchanged from the original evaluation, PySide6 included, now itself rejected for the reason above):
- **Tkinter** — too primitive for the required canvas/overlay UI.
- **Kivy** — mobile-first, awkward fit for desktop.
- **Electron** — bundles Chromium, directly contradicts the lightweight-install goal.
- **Tauri** — lighter than Electron, but requires Rust + a web frontend, discarding the Python-comfort advantage.
- **Fully separate native codebases per OS** — unrealistic maintenance burden for a solo developer.
- **PySide6/Qt** — see revision note above.

## PDF rendering & annotation: PyMuPDF (fitz)

The only library that provides page rendering, word/line-level bounding-box extraction, AND real PDF annotation writing in one dependency. Avoids stitching together a separate rendering library (e.g. PDFium) with a separate annotation-writing approach.

## Voice recognition: Vosk (offline)

Free, runs fully on-device, grammar-constrained against a small fixed command vocabulary — not general-purpose transcription.

Rejected alternatives and why:
- **Cloud APIs** (Whisper API, Google Speech, Azure) — cost per use, require internet, contradict the $0/offline constraints.
- **Whisper.cpp** — offline, but built for general transcription; overkill for a small fixed command set.
- **OS-native speech APIs** (Windows SAPI, macOS Speech framework) — smaller install size, but require two separate platform-specific code paths. Rejected in favor of one consistent, testable codebase given solo maintenance.

## Audio capture: sounddevice

Straightforward, cross-platform microphone input.

## Packaging: Nuitka

Compiles Python to C; produces smaller and faster standalone binaries than PyInstaller. Chosen because install size is an explicit project goal.

## App state & settings: no database

State is limited to two kinds of data:
1. **Highlight/annotation data** — lives inside the PDF file itself as real PyMuPDF-written annotations, not in a separate store.
2. **App-level settings** — theme, save-behavior preference, voice activation mode, recent-files list (capped at 10). Stored via a local JSON settings file (no `QSettings` equivalent under pywebview — this is a straightforward substitution, not a design change).

A database would be unjustified complexity for this data shape — there is no multi-record relational data, no concurrent access, and no querying need beyond "read this small settings blob."

## Explicitly banned / ruled out

- Any paid or cloud-based API, for voice recognition or anything else.
- Electron or any Chromium-bundling GUI approach.
- A PDF↔DOCX/Word conversion pipeline of any kind.
- General-purpose NLU frameworks for voice command parsing.

## Licensing

MIT for project code. The PySide6/Qt LGPL dynamic-linking requirement from the original stack no longer applies — pywebview is BSD-licensed and embeds the OS's native webview rather than a bundled framework. Verify pywebview's own license terms and any bundled JS libraries used in the frontend before release. (Not formal legal advice.)