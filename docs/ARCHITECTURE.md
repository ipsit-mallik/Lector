# Architecture

## App shape

**Revision note (post-Milestone 4):** originally PySide6 provided both UI and logic in one process. As of the pywebview switch (see `docs/TECH_STACK.md`), the process split is: Python owns the application process, PDF rendering, voice recognition, and file I/O; an embedded native webview (not a separate app) renders the HTML/CSS/JS frontend inside that same process. The webview loads the pages from a small loopback-only file server that the same process runs (`shared/frontend_server.py`, on a fresh port each launch; it also stamps the saved theme into each page), and the bridge itself is the webview's in-process messaging, not HTTP. This is still a single shipped desktop application — there is no client/server deployment, no network boundary between the two halves — but the UI and application-logic code now live in genuinely separate languages/folders and communicate over an explicit bridge, which the folder layout below reflects.

## Structural pattern: feature-based

Code is organized by feature, not by technical layer (no repo-wide `models/`, `views/`, `controllers/` split), and this still holds on both sides of the Python/frontend split. Each feature folder owns its own view code, logic, and local state. Shared code is promoted to `shared/` only once a second feature needs it — not preemptively.

Two pieces of shared code are justified from the start rather than waiting for a "second use," because they're app-wide by definition, not feature-specific: theme tokens (`docs/DESIGN_SYSTEM.md` is inherently cross-cutting) and icon assets (used in every feature's toolbar/nav). Under pywebview, `frontend/shared/theme.css` (the CSS custom properties the HTML uses) is the *only* place token values live — no other frontend file may contain a literal colour. Python code that needs a colour (the native title bar in `shared/window_chrome.py`, the app-icon build script) reads it from that file through `shared/theme.py`'s `token(theme, name)` rather than keeping a second copy that could drift; the Python-side colour dictionaries that used to live in `theme.py` were removed for that reason.

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
│       ├── __main__.py          # entry point — claims the single-instance
│       │                        #   lock, starts the page server, creates
│       │                        #   the pywebview window
│       ├── api.py               # the bridge object exposed to JS
│       │                        #   (js_api); Python-side handlers for
│       │                        #   events the frontend calls
│       │
│       ├── features/            # Python side: logic, no UI code
│       │   ├── home/            # Recent list persistence, file search
│       │   ├── reading/         # PDF page rendering (PyMuPDF → image),
│       │   │                    #   book/strip page-slicing logic,
│       │   │                    #   find-in-document search (search.py)
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
│           ├── theme.py         # Theme names + token(theme, name), which
│           │                    #   reads values from frontend/shared/theme.css
│           ├── window_chrome.py # Windows: app icon + theme-coloured title
│           │                    #   bar via Win32/DWM, bring-to-front
│           │                    #   (no-op elsewhere)
│           ├── frontend_server.py # Serves frontend/ on 127.0.0.1, fresh
│           │                    #   port per launch, saved theme stamped
│           │                    #   into each page's <html data-theme>,
│           │                    #   plus any <html> attributes a page
│           │                    #   declares (Home: view + file counts)
│           ├── single_instance.py # One Lector per user; a second launch
│           │                    #   hands its file over and exits
│           └── settings_store.py # JSON settings read/write
│
├── frontend/                    # HTML/CSS/JS side: all UI, no business logic
│   ├── index.html
│   ├── pages/                   # home.html, reading.html, settings.html, etc.
│   ├── shared/
│   │   ├── theme.css            # Only file with literal token values (colour,
│   │   │                        #   radius, spacing, type, motion, shadows)
│   │   ├── icons/               # SVG icon set
│   │   ├── components.css       # Shared primitives: buttons, icon buttons,
│   │   │                        #   toggle, radio card, checkbox, chip, tooltip,
│   │   │                        #   toast, progress, skeleton, dialog frame
│   │   ├── file-browser.css     # Open/Save-As dialog body (imported by
│   │   │                        #   components.css)
│   │   └── scrollbars.css       # The only place scrollbars are styled
│   │                            #   (auto-hide; imported by components.css)
│   └── js/
│       ├── bridge.js            # Wraps calls to the Python api.py bridge
│       ├── ui.js                # Tooltip + Toast behaviour, loaded on every page
│       ├── scrollbars.js        # Shows/hides every scrollbar on activity,
│       │                        #   loaded on every page
│       └── (per-page JS files, mirroring frontend/pages/)
│
├── assets/
│   ├── vosk_model/              # Offline speech model (~205 MB). Fetched by
│   │                             #   scripts/fetch_vosk_model.py, not committed —
│   │                             #   it is gitignored. Bundled at packaging time.
│   ├── lector.ico               # App icon (committed). Built from logo.svg by
│   │                             #   scripts/build_app_icon.py
│   └── silero_vad.onnx          # Offline VAD model (~2 MB). Fetched by
│                                 #   scripts/fetch_vad_model.py, not committed —
│                                 #   it is gitignored. Bundled at packaging time.
│
├── scripts/                     # Developer setup scripts, not shipped
│   ├── fetch_vosk_model.py      # Downloads/extracts assets/vosk_model/
│   ├── fetch_vad_model.py       # Downloads/extracts assets/silero_vad.onnx
│   └── build_app_icon.py        # Rebuilds assets/lector.ico from the brand mark
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

- **Find in document is sliced in Python and drawn in JS.** `features/reading/search.py` runs PyMuPDF's `search_for` page by page and stops after 50ms (`SLICE_BUDGET_S`), returning that slice's hits in PDF points plus the page to resume from; `api.find_in_document(query, start_page, end_page)` is one slice. A whole-document scan in one bridge call would hold up the page renders queued behind it, so `frontend/pages/find-bar.js` asks for slices in a loop instead, from the page being read to the end and then from the start back to it. Cancelling is not a backend call: each search carries a generation number, and a slice that returns for an older one is dropped and not followed up, which also makes out-of-order replies harmless. `search_for` gives one rect per line of a hit, so `group_fragments` rejoins a hit that wraps lines (by checking the text inside each rect spells out the query), and a slice counts the pages that have any text so the frontend can tell a scanned PDF from a missing word. Marks are drawn the way the selection wash is: an overlay over the page area, positioned from each page image's measured rect, repainted on scroll, zoom preview and refresh, and handed page geometry by `reading.js` through `configureFindBar()` so `find-bar.js` never needs to know which layout is showing. This is separate from voice highlight matching, which stays scoped to the visible viewport (`docs/PRD.md`).
- **Home lists first and draws thumbnails after.** Measured on a cold 20-file Recent list, rendering first-page thumbnails was about 83% of the list's backend time (opening each PDF for its page count and title was the rest). So `features/home/recent.py` keeps two mtime-keyed caches: `list_recent()`/`list_favorites()` open each file for its count and title only, and hand back a thumbnail only if one is already rendered, marking the rest `thumbnail_pending`; `api.get_thumbnails(paths)` renders those, four per call, for listed paths only (a round trip per thumbnail cost several times the render itself). PyMuPDF is used from separate bridge threads, so `recent.py` serialises its document work behind one lock. On the page, `frontend/pages/library-loading.js` holds the loading placeholders: a whole-list skeleton chosen from the grid/list view and last known file count that the page server writes into `<html>` (`recent.page_state()` → `frontend_server.with_page_state()`), so no bridge call stands between the page and the skeleton's shape. It appears only after 150ms, stays at least 300ms, and fades out over the rows drawn under it. Each pending thumbnail slot also gets a placeholder, until its batch arrives. `home.js` asks for the list at the very start of `init()`, alongside the first-run check, rather than after the icons and voice set-up, which used to hold it back by 200-250ms. A load overtaken by a newer one (Recent, then Favorites) draws nothing.

## App-close gatekeeper (Milestone 8.1)

*`Api.handle_window_closing`, bound to `window.events.closing` in `__main__.py`.*

pywebview's `window.events.closing` is registered with `should_lock=True` (see `webview/window.py`), which means every handler on it runs **synchronously, on the platform's own UI thread** — on Windows, inside WinForms' `FormClosing` — before the window is allowed to close, and a handler that returns a literal `False` cancels the close. That is a problem for a dirty-document check, because the only existing "ask the reader what to do" UI is `promptSaveIfDirty()`, an async JS function that opens a dialog and awaits its result — and blocking the UI thread until it resolves risks deadlocking against the very message loop `window.evaluate_js()`'s promise-callback delivery depends on.

The gatekeeper avoids that by never blocking: on a dirty document, it returns `False` to cancel the *first* close attempt, fires `promptSaveIfDirty(true)` via `window.evaluate_js()` with an async callback, and — only once that resolves to "yes, close" — sets a `_closing_confirmed` flag and calls `window.destroy()` to reissue the close. `window.destroy()` is safe to call from the callback's own (non-UI) thread because pywebview already marshals it back to the UI thread itself (`platforms/winforms.py`'s `destroy_window` wraps it in `Control.Invoke`). `_closing_confirmed` exists so that reissued close doesn't re-run the same check and prompt a second time. A clean document, or no document open, closes immediately — the check only ever adds a step when there is something to lose.

## Voice context router (Milestone 8.1–8.12)

*Extends the reading-only voice grammar to cover the whole app.*

The recognizer has always run one active grammar at a time — `wake.py` already proves this pattern by swapping between a minimal idle grammar (the wake phrase only) and the full command grammar on activation, and `api.py`'s `update_voice_viewport()`/`_viewport_vocabulary()` already proves the grammar can be widened dynamically (the visible page's own words, during highlighting). Full in-app voice control generalizes both: instead of exactly two grammars (idle / reading-command), a router tracks whichever screen or dialog currently has focus (`home`, `reading`, `settings`, `save_dialog`, `open_dialog`, `picker`, `dictation`, extendable) and sets the active grammar to a fixed set of **global** commands (available regardless of context — "undo", "redo", "help", "go home", "close app") unioned with that context's own scoped command set. `command_grammar.py`'s existing reading-view grammar becomes the `reading` context's scoped set without changing its behavior; Home and Settings gain their own.

Two new UI patterns fall out of this, both frontend-only (no new architectural boundary):
- **Numbered-overlay picker** — for a list item with no natural single-word voice label (a specific Recent card), an overlay badges each item with a number under a `picker` context; saying the number performs the same action a click would.
- **Dictation mode** — for the one free-text field that exists today (the command-reference panel's search field), a `dictation` context temporarily runs Vosk open-vocabulary rather than grammar-constrained, the same generalization of `update_voice_viewport()`'s existing widening trick, scoped narrowly rather than left on globally.

The native Open/Save-As file dialogs (`api.py`'s `open_pdf_dialog()`, the "Save a copy" path) are replaced with in-app `open_dialog`/`save_dialog` screens for the same reason `annotations/`'s save-confirmation dialog is already a `frontend/` concern rather than an OS-native one: the router can only see and control the app's own UI, not OS chrome.

**Implemented (Milestone 8.4):** `features/voice/router.py` holds `resolve(context, text, alternatives)` and `vocabulary_for(context)`, and `api.py` holds the context itself (`self._voice_context`, defaulting to `reading` so a caller that never calls the new `set_voice_context()` — every pre-8.4 test and any future one that doesn't need this — keeps behaving as it did before the router existed). Only `home` and `reading` claim a context so far, from `home.js`/`reading.js`'s `init()`; `settings` and the dialog/picker/dictation contexts are named in `router.CONTEXTS` but have no scoped grammar or caller yet, pending 8.5–8.8. The global set built in 8.4 is `undo`/`redo`/`help`/`go home` only — **"close app" is deliberately not among them**, held for 8.11 once 8.1's close gatekeeper (above) is what it needs to invoke, rather than being included here ahead of that dependency existing.

**Implemented (Milestone 8.7):** the native Open/Save-As dialogs described above are gone. `features/dialogs/browser.py` (`list_directory`, `default_open_dir`, `default_save_dir`) is a plain filesystem helper with no pywebview dependency, called from three new `api.py` bridge methods (`browse_directory`, `get_save_dialog_start`, and `perform_save`, whose `path` parameter now comes from the dialog rather than `create_file_dialog()`'s return value). On the frontend, `frontend/js/file-browser.js`'s `createFileBrowser()` factory backs both Home's `open_dialog` and Reading's `save_dialog` screen — one shared implementation rather than two, since the two differ only in whether picking a file closes the dialog immediately (Open) or fills a filename field first (Save). Both reuse 8.6's numbered-overlay badge, but always visible for as long as the dialog is open rather than toggled, since every row in a file listing lacks a natural spoken label the way a Recent card's own filename gives it one. This is the one place voice needs to keep working *while* a `.dialog-scrim` is open, which required narrowing `voice.js`'s push-to-talk-suppression check (originally "any open dialog blocks Space") to exclude scrims carrying a new `.voice-dialog` marker class — the keyboard-only save-confirmation and reference dialogs still suppress push-to-talk unchanged.

**Implemented (Milestone 8.8):** dictation mode, described above, is live for the one free-text field that exists — the command-reference panel's search box. `router.py` gained a `DICTATION` context that is the single exception to "global commands match first": a dedicated branch in `resolve()` checks it before the global table, so a dictated sentence containing a word like "undo" is never misheard as that command — only "done" (`STOP_DICTATION`) and "cancel"/"never mind" (`CANCEL`) resolve to anything there; everything else comes back as `command: None` and is treated as the dictated text itself. `START_DICTATION` ("start search") is a global intent rather than scoped to `reading`, since the panel opens as an overlay without changing the underlying context — mirroring how `HELP` already works, and a silent no-op anywhere that doesn't wire it into an action table. `engine.py`'s `set_vocabulary(None)` now builds the Vosk recognizer with no grammar argument at all (true open-vocabulary), rather than the widened-but-still-constrained word list `update_voice_viewport()` uses; `api.py`'s `_refresh_voice_vocabulary()` special-cases `DICTATION` to call it and skip viewport computation. Only final recognition results are ever written into the field — partial results are not streamed in, since a partial cannot yet be told apart from the start of "done"/"cancel". The context dictation returns to on exit is hardcoded to `reading` rather than tracked as a stack, since the panel is, today, only ever opened from that one screen.

**Implemented (Milestone 8.9):** two more confirmation dialogs become their own router contexts, following the same "the router can only see and control the app's own UI" shape 8.7's dialog contexts already established, rather than either dialog trying to interpret global commands meant for the screen underneath it. `REMOVE_CONFIRM` backs the new remove-from-Recent confirmation (`docs/DESIGN_SYSTEM.md`'s "Remove-from-Recent affordance" section, `docs/PRD.md`'s scoped-in note): `HOME_PHRASES` gains `REMOVE_RECENT`/`REMOVE_PICKER` to open it (directly for the single most-recent entry, or via 8.6's numbered-overlay picker in a removal mode for any other), and the context itself scopes `CONFIRM_REMOVE`/`CANCEL`. `SAVE_CONFIRM` backs the older, separate "Save changes?" dialog (`save-dialog.js`) — a **distinct** context from 8.7's `SAVE_DIALOG`, which already owns that name for the Save-As folder browser's own row-picking grammar; `SAVE_CONFIRM` instead reuses the `SAVE_COPY`/`SAVE_OVERWRITE` intents 8.5 defined for Settings (a spoken copy/overwrite choice both selects and confirms in one command, the same one-command-does-the-whole-action shape `CONFIRM_REMOVE` uses), adding only one new intent, `DONT_SAVE`, gated as a no-op when the dialog's Don't-Save button isn't offered. Both contexts fall through the same generic `_CONTEXT_PHRASE_TABLES` lookup `SETTINGS` already uses in `resolve()` — no new branching was needed in the router's control flow, unlike `DICTATION`'s dedicated branch above.

**Added later (Favorites):** four more `HOME_PHRASES` intents, no new context. `OPEN_FAVORITES`/`SHOW_RECENT` switch Home between its two lists (`home.js`'s `homeSection`); `FAVORITE_RECENT`/`FAVORITE_PICKER` repeat 8.9's most-recent/numbered-picker split for starring, with the picker gaining a third mode, `favorite`, beside `open` and `remove`. Starring toggles and has no confirmation step (unlike removal, saying it again undoes it), so neither intent routes through a dialog context. Storage is `settings.json`'s own `favorites` list (`store.get_favorites`/`add_favorite`/`remove_favorite`), not a field on the recent entries, so a favorite survives Recent's 20-item cap; `Api.get_favorites`/`set_favorite` are the bridge, and `home/recent.py`'s `list_favorites()` shares its entry shape with `list_recent()`.

**Implemented (Milestone 8.10):** `features/voice/reference.py`'s `categories()`/`panel()` — the payload behind the "What can I say?" panel — take a `context` parameter and build different categories for `HOME` and `SETTINGS` from their own `router.HOME_PHRASES`/`SETTINGS_PHRASES` tables, rather than always returning the reading grammar's "Moving around"/"Highlighting"/"The app itself" regardless of which screen asked. `api.py`'s `get_command_reference()` passes its own `self._voice_context` through, so the panel — once a screen wires "help" to open it — shows only what that screen can actually do. Every context without its own categories (`READING`, and every dialog/picker/dictation context, none of which offer a "help" entry point today) keeps the pre-8.10 reading categories as its default, which is also what every existing caller of `categories()`/`panel()` with no argument still gets.

**Superseded (Milestone 8.13):** the "What can I say?" list is one shared component (`frontend/pages/command-reference.js` + `frontend/shared/reference.css`, markup and styles defined once, loaded by Home, Settings and the reader) driven only by `features/voice/reference.py`, which builds it from every router and grammar table. `panel(context)` now returns `sections` (not `categories`) and each command's `phrases` (every phrasing the recognizer accepts, not a sample); a phrase with a variable is a pattern (`{page}`, `{number}`, `{words}`) that the page fills with the real range through `configureCommandReference()`. Every screen gets every section: `context` decides only which come first (that screen's own, then the rest in one fixed order) and which sections say where they do work. So the 8.10 per-context scoping above no longer applies to what is *listed*, only to ordering and availability; what the recognizer *accepts* in each context is unchanged. `tests/test_command_reference.py` fails if a phrase in any `*_PHRASES` table is missing from the list, if a listed phrase does not resolve in the contexts its card names, or if the component or any page carries command text of its own.

**Implemented (Milestone 8.12):** the router grows from per-screen commands to the whole-app set, and gains a way to nest scopes.
- *Globals.* `GLOBAL_PHRASES` gains `OPEN_PDF` ("open a pdf"), `OPEN_SETTINGS` (moved from `HOME_PHRASES`) and `GO_RECENT` ("go to recent"/"show recent files", replacing Home's `SHOW_RECENT`), beside the existing `HELP`. Each screen's `VOICE_ACTIONS` decides what they mean there: Home opens its dialog; Settings leaves a one-shot `localStorage` note (`lector-open-dialog`) and navigates to Home, which opens the dialog on arrival (the Open dialog only exists on Home); Reading does the same but only after `promptSaveIfDirty`, so leaving by voice never skips the unsaved-highlights check.
- *`OPEN_NUMBER`.* "open number N" (Home) is parsed like `GOTO_PAGE` — a lead phrase fuzzy-matched against `OPEN_NUMBER_PREFIXES`, then a spoken number — and opens `displayedEntries[N-1]`, the same ordering the picker's badges use, so the two cannot disagree. Unlike the picker's bare numbers it needs the lead, since nothing on screen says numbers are live.
- *Modal scopes.* `Api.push_voice_context`/`pop_voice_context` keep a stack, so a modal gives back whichever grammar it covered rather than a hardcoded one. `set_voice_context` clears the stack (a page declaring itself is a fresh baseline, and any modal still pushed belonged to a page already left); popping an empty stack is a no-op, so a close handler that runs twice cannot pop past the screen. Two contexts use it: `REFERENCE` (the "What can I say?" panel — only "got it"/"go back"/"cancel", so "next page" no longer turns pages behind it; there is deliberately no bare "close", one misheard word from "close app") and `OVERWRITE_CONFIRM` (Settings' "confirm overwrite"/"cancel"; the phrase is two words on purpose, since a bare "yes" is too easy to produce by accident). Both are **really modal**: `MODAL_ALLOWED_GLOBALS` lets only `HELP`/`START_DICTATION` through in `REFERENCE` and nothing in `OVERWRITE_CONFIRM`, in `resolve` *and* in the recognizer's vocabulary, so "quit" or "open settings" cannot fire behind a dialog (before, they were inert only because each page's listener happened to swallow them). On the frontend, `voice.js`'s `createVoiceScope(context)` wraps push/pop: every bridge call goes through one shared queue, because pywebview runs each on its own thread and a close that landed during the open's push used to pop first (or not at all), leaving the screen's commands dead; it also makes double-open/close idempotent and never pops a failed push. `Api` guards the stack with a lock. Dictation now pushes on top of `REFERENCE` and pops back to it, which replaces the hardcoded `reading` return described under 8.8 — the panel is now opened from Home and Settings too.
- *One registry.* `reference.py` is the single source for both the panel and the empty state's "TRY SAYING" chips (`try_saying(context)`, carried in the `get_command_reference` payload). Both are built from `router`'s tables, a test checks that every phrase either shows resolves through `router.resolve`, and another that the page's `VOICE_ACTIONS` handles its intent — so neither can advertise a command that does nothing.
- *Voice can't switch voice off.* No intent exists for it, and tests assert the phrases resolve to nothing in any context. Settings' toggles remain mouse/keyboard only.
- *Status honesty.* The sidebar voice box (`frontend/js/voice-box.js`, shared by Home and Settings) starts as "CHECKING VOICE", says "VOICE READY" only once the engine has answered and the page's `set_voice_context` succeeded, and says plainly when it didn't. The old hardcoded "VOICE READY" claimed readiness before either was true.
- *Push-to-talk and focus.* `voice.js`'s `shouldIgnoreKey` used to give way to every focused button, so after any click in the sidebar holding Space did nothing. It now gives way only to a control focused *by keyboard* (`:focus-visible`), where Space is how they mean to press it; a button merely clicked no longer swallows it.
- *Drag-and-drop.* Split across the bridge because the page only ever sees a dropped file's name, never its path. `Api.bind_file_drop(window)` registers a pywebview `drop` handler (with `prevent_default`, or the WebView navigates to the file and replaces the app with a bare viewer) on every `window.events.loaded`, since pywebview forgets DOM bindings on navigation. `features/home/file_drop.py` picks the PDF (`pywebviewFullPath`, `.pdf`, an existing file; first PDF wins; refusals carry a message) and `Api` forwards the outcome as a `lector:filedrop` event. `frontend/js/filedrop.js` cancels `dragover` for file drags (what makes the page a valid target), shows the "Drop a PDF to open it" overlay, and hands the path to the page's `onFileDrop` handler. It only reacts to drags carrying `Files`, so dragging selected text is ignored. Opening is each page's decision, which is why Reading prompts about unsaved highlights first and no page opens anything while a dialog is up.
- *Shared help panel.* `command-reference.js` now builds the panel's markup itself and `reference.css` is in `frontend/shared/`, so Home, Settings and Reading each load one script instead of carrying a copy.

**Implemented (Milestone 8.11):** `router.GLOBAL_PHRASES` gains `CLOSE_APP` ("close app"/"quit"), the one global command held back at 8.4 pending this milestone's dependency on the close gatekeeper above. `api.py`'s new `close_app()` method is deliberately thin — it looks up `webview.windows[0]` and calls `window.destroy()`, the exact same call `handle_window_closing()` itself makes to reissue a confirmed close. Because `window.destroy()` (via WinForms' `Window.Close()`) fires `FormClosing` the same way the OS close button or Alt+F4 does, it re-enters `window.events.closing` and therefore `handle_window_closing()` on its own, so the dirty-document check and save-or-discard prompt from 8.1 apply unchanged — no separate close logic was written for the voice path. `home.js`/`reading.js`/`settings.js` each gain a `closeApp()` calling `callApi("close_app")`, wired into their `VOICE_ACTIONS` tables the same way `GO_HOME` already is; unlike `GO_HOME`, `home.js` wires it too, since quitting is never a no-op the way "go home" is on the screen that already is Home. Like the rest of the global set, "close app"/"quit" is not yet listed in the "What can I say?" panel (8.10's scoping did not add global commands to it, since none were listed there before either).

## Naming convention

Python side: ecosystem standard — `snake_case` for files, modules, functions, and variables; `PascalCase` for classes. Frontend side: `kebab-case` for HTML/CSS file names and CSS classes (e.g. `save-dialog.css`, `.recent-card`), `camelCase` for JS variables and functions — the respective ecosystem standards for each, not a project-specific choice.

## Testing

Test structure mirrors `src/lector/features/` — one test module per feature folder. (Coverage targets and merge requirements are deferred; not in scope until `docs/TESTING.md` is written, post-prototype.)