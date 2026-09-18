# Architecture

## App shape

Single desktop application — PySide6 provides both the UI and the app logic in one process. No client/server split, no frontend/backend distinction; that pattern doesn't apply here.

## Structural pattern: feature-based

Code is organized by feature, not by technical layer (no repo-wide `models/`, `views/`, `controllers/` split). Each feature folder owns its own view code, logic, and local state. Shared code is promoted to `shared/` only once a second feature needs it — not preemptively.

Two pieces of shared code are justified from the start rather than waiting for a "second use," because they're app-wide by definition, not feature-specific: theme tokens (`docs/DESIGN_SYSTEM.md` is inherently cross-cutting) and icon assets (used in every feature's toolbar/nav).

## Layout

```
lector/
├── docs/
│   ├── PRD.md
│   ├── TECH_STACK.md
│   ├── DESIGN_SYSTEM.md
│   └── ARCHITECTURE.md
├── src/
│   └── lector/
│       ├── __init__.py
│       ├── __main__.py          # entry point
│       ├── app.py               # QApplication setup, main window wiring
│       │
│       ├── features/
│       │   ├── home/            # Recent list, Open PDF, search
│       │   ├── reading/         # Book + strip layouts, page nav, zoom
│       │   ├── voice/           # Vosk engine, command grammar, activation
│       │   │                    #   (push-to-talk + wake phrase)
│       │   ├── annotations/     # Speech-to-match highlighting,
│       │   │                    #   PyMuPDF annotation writing, save dialog
│       │   ├── settings/        # Theme, voice mode, save-behavior prefs
│       │   └── onboarding/      # First-run mic permission + activation
│       │                        #   mode selection
│       │
│       └── shared/
│           ├── theme.py         # Color tokens from docs/DESIGN_SYSTEM.md
│           └── icons.py         # Shared SVG icon set
│
├── assets/
│   └── vosk_model/              # Bundled offline speech model
│
├── tests/                       # Mirrors src/lector/features/ structure
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

- **`voice/`** owns speech recognition and command interpretation. It does not know about PDF rendering or PySide6 widgets — it emits recognized intents (e.g. `NEXT_PAGE`, `HIGHLIGHT("some phrase")`) that other features subscribe to. This keeps the offline speech engine swappable later without touching UI code.
- **`annotations/`** owns both the fuzzy speech-to-text matching logic *and* the PyMuPDF write layer, because they're tightly coupled (match result directly becomes the annotation's coordinates) — splitting them into separate features would add indirection without a real benefit.
- **`reading/`** owns which layout (book/strip) is active and what "current viewport text" means for each layout — this is the piece `annotations/` depends on to know what's matchable at any given moment, per the open question flagged during design: viewport-scoped matching must behave consistently regardless of active layout.

## Naming convention

Python ecosystem standard, not a special project choice: `snake_case` for files, modules, functions, and variables; `PascalCase` for classes. Applied consistently across the codebase.

## Testing

Test structure mirrors `src/lector/features/` — one test module per feature folder. (Coverage targets and merge requirements are deferred; not in scope until `docs/TESTING.md` is written, post-prototype.)
