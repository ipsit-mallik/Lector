# Architecture

## App shape

**Revision note (post-Milestone 4):** originally PySide6 provided both UI and logic in one process. As of the pywebview switch (see `docs/TECH_STACK.md`), the process split is: Python owns the application process, PDF rendering, voice recognition, and file I/O; an embedded native webview (not a separate app, not a server) renders the HTML/CSS/JS frontend inside that same process. This is still a single shipped desktop application — there is no client/server deployment, no network boundary between the two halves — but the UI and application-logic code now live in genuinely separate languages/folders and communicate over an explicit bridge, which the folder layout below reflects.

## Structural pattern: feature-based

Code is organized by feature, not by technical layer (no repo-wide `models/`, `views/`, `controllers/` split), and this still holds on both sides of the Python/frontend split. Each feature folder owns its own view code, logic, and local state. Shared code is promoted to `shared/` only once a second feature needs it — not preemptively.

Two pieces of shared code are justified from the start rather than waiting for a "second use," because they're app-wide by definition, not feature-specific: theme tokens (`docs/DESIGN_SYSTEM.md` is inherently cross-cutting) and icon assets (used in every feature's toolbar/nav). Under pywebview, `shared/theme.py` (Python-side constants) is joined by `frontend/shared/theme.css` (the actual CSS custom properties the HTML uses) — the Python copy exists for any Python-side logic that still needs the values (e.g. generating themed exports), not as the UI's source of truth. The CSS file is the UI's source of truth.

## Layout

```
lector/
├── docs/
│   ├── PRD.md
│   ├── TECH_STACK.md
│   ├── DESIGN_SYSTEM.md
│   ├── ARCHITECTURE.md
│   └── TASKS.md
├── src/
│   └── lector/
│       ├── __init__.py
│       ├── __main__.py          # entry point — creates the pywebview window
│       ├── api.py               # the bridge object exposed to JS
│       │                        #   (js_api); Python-side handlers for
│       │                        #   events the frontend calls
│       │
│       ├── features/            # Python side: logic, no UI code
│       │   ├── home/            # Recent list persistence, file search
│       │   ├── reading/         # PDF page rendering (PyMuPDF → image),
│       │   │                    #   book/strip page-slicing logic
│       │   ├── voice/           # Vosk engine, command grammar, activation
│       │   │                    #   (push-to-talk + wake phrase), audio
│       │   │                    #   preprocessing (VAD, noise/gain)
│       │   ├── annotations/     # Speech-to-match highlighting,
│       │   │                    #   PyMuPDF annotation writing
│       │   ├── settings/        # Theme, voice mode, save-behavior prefs,
│       │   │                    # reopen preference + per-document position
│       │   └── onboarding/      # First-run mic permission + activation
│       │                        #   mode selection
│       │
│       └── shared/
│           ├── theme.py         # Color tokens, Python-side reference only
│           │                    #   (not the UI's source of truth — see above)
│           └── settings_store.py # JSON settings read/write
│
├── frontend/                    # HTML/CSS/JS side: all UI, no business logic
│   ├── index.html
│   ├── pages/                   # home.html, reading.html, settings.html, etc.
│   ├── shared/
│   │   ├── theme.css            # Source of truth for all color/spacing tokens
│   │   ├── icons/               # SVG icon set
│   │   └── components.css       # Shared dialog/card/button/radio styles
│   └── js/
│       ├── bridge.js            # Wraps calls to the Python api.py bridge
│       └── (per-page JS files, mirroring frontend/pages/)
│
├── assets/
│   ├── vosk_model/              # Offline speech model (~68 MB). Fetched by
│   │                             #   scripts/fetch_vosk_model.py, not committed —
│   │                             #   it is gitignored. Bundled at packaging time.
│   └── silero_vad.onnx          # Offline VAD model (~2 MB). Fetched by
│                                 #   scripts/fetch_vad_model.py, not committed —
│                                 #   it is gitignored. Bundled at packaging time.
│
├── scripts/                     # Developer setup scripts, not shipped
│   ├── fetch_vosk_model.py      # Downloads/extracts assets/vosk_model/
│   └── fetch_vad_model.py       # Downloads/extracts assets/silero_vad.onnx
│
├── tests/                       # Mirrors src/lector/features/ structure
│                                 #   (frontend has no automated tests yet —
│                                 #   deferred, not in scope this round)
│
├── CHANGELOG.md
├── CLAUDE.md
├── LICENSE                      # MIT
├── pyproject.toml
└── README.md
```

## Why `src/lector/` and not a flat `lector/` at root

Standard Python packaging practice (the "src layout"): it prevents accidentally importing the in-development package via the current working directory instead of the installed one, which is a common and confusing bug in flat layouts. Worth the one extra directory level.

## Feature boundaries

- **`voice/`** owns speech recognition and command interpretation. It does not know about rendering or DOM structure — it emits recognized intents (e.g. `NEXT_PAGE`, `HIGHLIGHT("some phrase")`) across the bridge to the frontend, which decides what to do with them. This keeps the offline speech engine swappable later without touching UI code.
- **`annotations/`** owns both the fuzzy speech-to-text matching logic *and* the PyMuPDF write layer, because they're tightly coupled (match result directly becomes the annotation's coordinates) — splitting them into separate features would add indirection without a real benefit. The save-confirmation dialog itself is a `frontend/` concern (it's UI); `annotations/` only handles the write once the frontend confirms the choice. The *string-level* edit-distance primitive it matches with lives one feature over, in `voice/fuzzy.py`, because Milestone 6's navigation grammar needed the same arithmetic first and two copies would inevitably drift apart. That is a shared utility, not a shift in ownership: `annotations/` still owns the matching *logic* — which words are candidates, scoping to the visible viewport, breaking ties by reading order, and choosing the span to write.
- **`reading/`** (Python side) owns page rendering and what "current viewport text" means for each layout, expressed as bounding-box data sent to the frontend — this is what `annotations/` depends on to know what's matchable at any given moment. The frontend (`frontend/pages/reading.html` + its JS) owns which layout (book/strip) is visually active and renders the bounding-box overlays it's given; per the open question flagged during design, this split must still behave consistently regardless of active layout.
- **Pointer hit-testing lives on the frontend, word geometry and the write do not.** Deciding which word sits under the cursor has to be answered on every `mousemove` to keep the selection wash attached to the pointer, which is far more often than is sensible to cross the bridge for. So `api.get_page_words()` hands the frontend a page's word boxes once (in PDF points, so zoom never invalidates them — the "bounding-box data sent to the frontend" above), the frontend resolves cursor → word locally while the drag is live, and `api.highlight_words()` takes back the *word index range* it had drawn. Indices rather than coordinates on purpose: the annotation that gets written is then by construction the run the reader saw selected, instead of the result of a second, independent hit-test that could disagree with the preview. The two ends of a drag are resolved by different rules on purpose — the anchor by containment (`wordIndexAt`), so a press on empty page starts nothing, and the moving end by proximity (`nearestWordIndex`), so a drag can run past the end of a line without stopping. Composing the shape stays duplicated in both places (`highlighter.merge_line_rects` and `mergeLineBoxes` in `reading.js`), and the two are required to agree — each carries a comment pointing at the other.
- **`api.py`** is the only file that both sides import against — it's the contract. Frontend JS never reaches into `features/` directly, and Python features never touch `frontend/` files; everything crosses through `api.py`'s exposed methods and its corresponding `bridge.js` wrapper.

## App-close gatekeeper (Milestone 8.1)

*`Api.handle_window_closing`, bound to `window.events.closing` in `__main__.py`.*

pywebview's `window.events.closing` is registered with `should_lock=True` (see `webview/window.py`), which means every handler on it runs **synchronously, on the platform's own UI thread** — on Windows, inside WinForms' `FormClosing` — before the window is allowed to close, and a handler that returns a literal `False` cancels the close. That is a problem for a dirty-document check, because the only existing "ask the reader what to do" UI is `promptSaveIfDirty()`, an async JS function that opens a dialog and awaits its result — and blocking the UI thread until it resolves risks deadlocking against the very message loop `window.evaluate_js()`'s promise-callback delivery depends on.

The gatekeeper avoids that by never blocking: on a dirty document, it returns `False` to cancel the *first* close attempt, fires `promptSaveIfDirty(true)` via `window.evaluate_js()` with an async callback, and — only once that resolves to "yes, close" — sets a `_closing_confirmed` flag and calls `window.destroy()` to reissue the close. `window.destroy()` is safe to call from the callback's own (non-UI) thread because pywebview already marshals it back to the UI thread itself (`platforms/winforms.py`'s `destroy_window` wraps it in `Control.Invoke`). `_closing_confirmed` exists so that reissued close doesn't re-run the same check and prompt a second time. A clean document, or no document open, closes immediately — the check only ever adds a step when there is something to lose.

## Voice context router (Milestone 8.1–8.11)

*Extends the reading-only voice grammar to cover the whole app.*

The recognizer has always run one active grammar at a time — `wake.py` already proves this pattern by swapping between a minimal idle grammar (the wake phrase only) and the full command grammar on activation, and `api.py`'s `update_voice_viewport()`/`_viewport_vocabulary()` already proves the grammar can be widened dynamically (the visible page's own words, during highlighting). Full in-app voice control generalizes both: instead of exactly two grammars (idle / reading-command), a router tracks whichever screen or dialog currently has focus (`home`, `reading`, `settings`, `save_dialog`, `open_dialog`, `picker`, `dictation`, extendable) and sets the active grammar to a fixed set of **global** commands (available regardless of context — "undo", "redo", "help", "go home", "close app") unioned with that context's own scoped command set. `command_grammar.py`'s existing reading-view grammar becomes the `reading` context's scoped set without changing its behavior; Home and Settings gain their own.

Two new UI patterns fall out of this, both frontend-only (no new architectural boundary):
- **Numbered-overlay picker** — for a list item with no natural single-word voice label (a specific Recent card), an overlay badges each item with a number under a `picker` context; saying the number performs the same action a click would.
- **Dictation mode** — for the one free-text field that exists today (the command-reference panel's search field), a `dictation` context temporarily runs Vosk open-vocabulary rather than grammar-constrained, the same generalization of `update_voice_viewport()`'s existing widening trick, scoped narrowly rather than left on globally.

The native Open/Save-As file dialogs (`api.py`'s `open_pdf_dialog()`, the "Save a copy" path) are replaced with in-app `open_dialog`/`save_dialog` screens for the same reason `annotations/`'s save-confirmation dialog is already a `frontend/` concern rather than an OS-native one: the router can only see and control the app's own UI, not OS chrome.

**Implemented (Milestone 8.4):** `features/voice/router.py` holds `resolve(context, text, alternatives)` and `vocabulary_for(context)`, and `api.py` holds the context itself (`self._voice_context`, defaulting to `reading` so a caller that never calls the new `set_voice_context()` — every pre-8.4 test and any future one that doesn't need this — keeps behaving as it did before the router existed). Only `home` and `reading` claim a context so far, from `home.js`/`reading.js`'s `init()`; `settings` and the dialog/picker/dictation contexts are named in `router.CONTEXTS` but have no scoped grammar or caller yet, pending 8.5–8.8. The global set built in 8.4 is `undo`/`redo`/`help`/`go home` only — **"close app" is deliberately not among them**, held for 8.11 once 8.1's close gatekeeper (above) is what it needs to invoke, rather than being included here ahead of that dependency existing.

**Implemented (Milestone 8.7):** the native Open/Save-As dialogs described above are gone. `features/dialogs/browser.py` (`list_directory`, `default_open_dir`, `default_save_dir`) is a plain filesystem helper with no pywebview dependency, called from three new `api.py` bridge methods (`browse_directory`, `get_save_dialog_start`, and `perform_save`, whose `path` parameter now comes from the dialog rather than `create_file_dialog()`'s return value). On the frontend, `frontend/js/file-browser.js`'s `createFileBrowser()` factory backs both Home's `open_dialog` and Reading's `save_dialog` screen — one shared implementation rather than two, since the two differ only in whether picking a file closes the dialog immediately (Open) or fills a filename field first (Save). Both reuse 8.6's numbered-overlay badge, but always visible for as long as the dialog is open rather than toggled, since every row in a file listing lacks a natural spoken label the way a Recent card's own filename gives it one. This is the one place voice needs to keep working *while* a `.dialog-scrim` is open, which required narrowing `voice.js`'s push-to-talk-suppression check (originally "any open dialog blocks Space") to exclude scrims carrying a new `.voice-dialog` marker class — the keyboard-only save-confirmation and reference dialogs still suppress push-to-talk unchanged.

## Naming convention

Python side: ecosystem standard — `snake_case` for files, modules, functions, and variables; `PascalCase` for classes. Frontend side: `kebab-case` for HTML/CSS file names and CSS classes (e.g. `save-dialog.css`, `.recent-card`), `camelCase` for JS variables and functions — the respective ecosystem standards for each, not a project-specific choice.

## Testing

Test structure mirrors `src/lector/features/` — one test module per feature folder. (Coverage targets and merge requirements are deferred; not in scope until `docs/TESTING.md` is written, post-prototype.)