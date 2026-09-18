# Tech Stack

## Language

**Python.** Matches existing developer comfort; avoids adding a new language's learning curve on top of an already ambitious solo project.

## Desktop GUI: PySide6 (Qt for Python)

Native look on Windows and Mac. Avoids bundling a full Chromium runtime the way Electron does. Free under LGPL.

Rejected alternatives and why:
- **Tkinter** — too primitive for the required canvas/overlay UI (focus indicators, highlight rendering).
- **Kivy** — mobile-first, awkward fit for desktop.
- **Electron** — bundles Chromium, directly contradicts the lightweight-install goal.
- **Tauri** — lighter than Electron, but requires Rust + a web frontend, discarding the Python-comfort advantage for no clear win here.
- **Fully separate native codebases per OS** (e.g. WinUI + SwiftUI) — smallest/fastest in theory, but unrealistic maintenance burden for a solo developer.

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
2. **App-level settings** — theme, save-behavior preference, voice activation mode, recent-files list (capped at 10). Stored via `QSettings` or a local JSON file.

A database would be unjustified complexity for this data shape — there is no multi-record relational data, no concurrent access, and no querying need beyond "read this small settings blob."

## Explicitly banned / ruled out

- Any paid or cloud-based API, for voice recognition or anything else.
- Electron or any Chromium-bundling GUI approach.
- A PDF↔DOCX/Word conversion pipeline of any kind.
- General-purpose NLU frameworks for voice command parsing.

## Licensing

MIT for project code. PySide6/Qt is LGPL — packaging must keep Qt as dynamically-linked shared libraries, not statically compiled into the executable, to remain compliant. (Not formal legal advice.)
