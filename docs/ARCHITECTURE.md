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
│       │   │                    #   (push-to-talk + wake phrase)
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
│   └── vosk_model/              # Offline speech model (~68 MB). Fetched by
│                                 #   scripts/fetch_vosk_model.py, not committed —
│                                 #   it is gitignored. Bundled at packaging time.
│
├── scripts/                     # Developer setup scripts, not shipped
│   └── fetch_vosk_model.py      # Downloads/extracts assets/vosk_model/
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
- **`annotations/`** owns both the fuzzy speech-to-text matching logic *and* the PyMuPDF write layer, because they're tightly coupled (match result directly becomes the annotation's coordinates) — splitting them into separate features would add indirection without a real benefit. The save-confirmation dialog itself is a `frontend/` concern (it's UI); `annotations/` only handles the write once the frontend confirms the choice.
- **`reading/`** (Python side) owns page rendering and what "current viewport text" means for each layout, expressed as bounding-box data sent to the frontend — this is what `annotations/` depends on to know what's matchable at any given moment. The frontend (`frontend/pages/reading.html` + its JS) owns which layout (book/strip) is visually active and renders the bounding-box overlays it's given; per the open question flagged during design, this split must still behave consistently regardless of active layout.
- **Pointer hit-testing lives on the frontend, word geometry and the write do not.** Deciding which word sits under the cursor has to be answered on every `mousemove` to keep the selection wash attached to the pointer, which is far more often than is sensible to cross the bridge for. So `api.get_page_words()` hands the frontend a page's word boxes once (in PDF points, so zoom never invalidates them — the "bounding-box data sent to the frontend" above), the frontend resolves cursor → word locally while the drag is live, and `api.highlight_words()` takes back the *word index range* it had drawn. Indices rather than coordinates on purpose: the annotation that gets written is then by construction the run the reader saw selected, instead of the result of a second, independent hit-test that could disagree with the preview. The two ends of a drag are resolved by different rules on purpose — the anchor by containment (`wordIndexAt`), so a press on empty page starts nothing, and the moving end by proximity (`nearestWordIndex`), so a drag can run past the end of a line without stopping. Composing the shape stays duplicated in both places (`highlighter.merge_line_rects` and `mergeLineBoxes` in `reading.js`), and the two are required to agree — each carries a comment pointing at the other.
- **`api.py`** is the only file that both sides import against — it's the contract. Frontend JS never reaches into `features/` directly, and Python features never touch `frontend/` files; everything crosses through `api.py`'s exposed methods and its corresponding `bridge.js` wrapper.

## Naming convention

Python side: ecosystem standard — `snake_case` for files, modules, functions, and variables; `PascalCase` for classes. Frontend side: `kebab-case` for HTML/CSS file names and CSS classes (e.g. `save-dialog.css`, `.recent-card`), `camelCase` for JS variables and functions — the respective ecosystem standards for each, not a project-specific choice.

## Testing

Test structure mirrors `src/lector/features/` — one test module per feature folder. (Coverage targets and merge requirements are deferred; not in scope until `docs/TESTING.md` is written, post-prototype.)