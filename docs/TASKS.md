# Tasks

Working checklist for implementation, in build order. See `docs/ARCHITECTURE.md` for the folder structure these tasks map to, `docs/PRD.md` for feature requirements, and `docs/DESIGN_SYSTEM.md` for visual/copy specifics.

**Rules for whoever (or whatever) works through this:**
- Work top to bottom. Don't start a later milestone's tasks before the current one is done and manually verified — the ordering is deliberate (see rationale in each milestone header), not arbitrary.
- Check items off as completed (`- [x]`).
- Follow `CLAUDE.md`'s workflow rules: update the relevant doc in `docs/` if a task's implementation reveals a doc needs correcting (doc-sync-on-change), and log user-facing changes to `CHANGELOG.md` as you go (changelog-on-change).
- If a task turns out to need a decision not covered in `docs/`, stop and ask rather than guessing and proceeding.

---

## Milestone 1 — Environment & skeleton

*No voice, no PDF rendering yet — just confirm the environment works before building on it.*

- [x] `pyproject.toml` with dependencies: PySide6, PyMuPDF, vosk, sounddevice
- [x] Create `src/lector/` package skeleton matching `docs/ARCHITECTURE.md`'s folder layout exactly (empty `features/` subfolders + `shared/`)
- [x] `__main__.py` entry point that launches a blank `QApplication` window
- [x] Manually verify the app launches on both Windows and Mac

## Milestone 2 — Core reader (mouse/keyboard only, no voice)

*Prove PDF rendering and navigation work correctly before adding a second hard problem (speech) on top.*

- [x] `features/home/`: minimal Home screen — "Open PDF" button (native file dialog), empty/placeholder recent list
- [x] `features/reading/`: render a PDF page via PyMuPDF into the view (book layout only for now)
- [x] Page navigation: next/previous buttons + keyboard shortcuts (arrow keys or Page Up/Down)
- [x] Jump-to-page control
- [x] Zoom in/out
- [x] Apply `docs/DESIGN_SYSTEM.md` light-theme tokens to this base window chrome

## Milestone 3 — Highlighting core + save behavior

*The core value mechanic. Prove annotation persistence actually works before voice ever touches it.*

- [x] `features/annotations/`: mouse-based text selection using PyMuPDF word/line bounding boxes
- [x] Live selection feedback while dragging — words wash in the selection color as the cursor moves and only become a highlight on release, per `docs/DESIGN_SYSTEM.md`'s "two visible beats" note (verified by screenshotting the running app mid-drag, on release, and after a single-word click)
- [x] Write selection as a real PDF highlight annotation via PyMuPDF (not a UI overlay)
- [x] Save-confirmation dialog: "Save a copy" / "Overwrite the original", pre-selected to copy, "Don't ask again" checkbox (unchecked by default) — generalized wording, fires only on explicit Save action
- [x] `features/settings/`: persist the save-behavior preference via `QSettings`
- [ ] **Manual verification:** open the saved file in a different PDF viewer (not Lector) and confirm the highlight is actually there — implementation was verified by reopening both the copy and the overwritten original as fresh PyMuPDF documents and confirming the highlight annotation is structurally present in each; still needs a human check in an actual third-party viewer (e.g. Adobe Reader, Preview, browser PDF viewer) before this box is checked

## Milestone 4 — Strip layout, Settings, theme, recent files

*Round out the non-voice app into something usable standalone.*

- [x] `features/reading/`: continuous-strip layout, toggle between book/strip
- [x] `features/settings/`: theme picker (light/dark/sepia) wired to `shared/theme.py` tokens
- [x] Recent files: persist last 10 opened files (path + timestamp) via `QSettings`; render as the card grid on Home
- [x] Resume reading position: persist each document's last page + layout (book/strip) alongside its recent-files entry; restore both when it is reopened, governed by a Settings preference ("Continue where I left off" default vs. "Always start at the beginning"). Added at the developer's request after the rest of this milestone; verified by an end-to-end test over a real 20-page PDF (restore, start-at-beginning, clamping a stale page against a shortened file, corrupt stored values, recent-list order left undisturbed) and by a rendered screenshot of the new Settings section, which matches the option-row pattern of the save-behavior section above it.
- [x] Resume reading position now also covers zoom: the last zoom level (from the toolbar's step buttons, the zoom-popover slider, or a restored session) is stored and restored alongside page/layout, using the same recent-files entry and the same "Continue where I left off" preference — no separate setting. Added at the developer's request after the above was already shipped.
- [ ] **Manual verification:** confirm in the running app that a document reopened from Recent comes back in strip layout when that is what it was left in — the restore path itself is covered by the test above, but the reading view actually *coming up* in the restored layout was not screenshotted (the app's pywebview window can't be driven from the agent environment; only the Settings page was rendered headlessly).
- [ ] Stub onboarding screens (placeholder only — full flow built in Milestone 8) — deferred: not started yet, no onboarding screen exists at all. Milestone 8 owns the real thing; a stub wasn't built this pass since Settings/theme/recent-files already made this a large milestone. Pick up next.

## Milestone 5 — Voice engine: push-to-talk only

*Isolate "does speech recognition work at all" from "does always-on listening work" as separate problems.*

- [ ] `features/voice/`: load Vosk model, set up grammar-constrained (not general transcription) recognizer
- [ ] Push-to-talk key binding (default: Space) — captures audio via `sounddevice` while held
- [ ] Emit recognized text as an event/signal other features can subscribe to

## Milestone 6 — Voice navigation commands

*Lowest-risk voice commands: no ambiguity, no matching logic, just intent → action.*

- [ ] `command_grammar.py`: fixed vocabulary + synonym map for navigation ("next page"/"turn the page"/"forward", etc.)
- [ ] Wire recognized navigation intents to `reading/` page-nav functions
- [ ] Fuzzy/edit-distance tolerance for recognition errors on command words (reuse for Milestone 7's matching, don't build twice)

## Milestone 7 — Voice highlighting (speech-to-match)

*The hardest, riskiest piece — build last, once rendering, annotation writing, and recognition are each already proven independently.*

- [ ] `annotations/highlight_matcher.py`: fuzzy-match spoken phrase against the word list of the currently visible viewport only
- [ ] Disambiguation: viewport-only scope, first occurrence (reading order) wins ties
- [ ] Word-level highlight as baseline; sentence-level extension via nearest-punctuation heuristic
- [ ] Wire the "highlight" voice command to the matcher + annotation writer
- [ ] Wire the "save" voice command to explicitly trigger the save dialog — confirm it never fires automatically as a side effect of highlighting

## Milestone 8 — Wake phrase, discoverability, onboarding

*Polish layer, once the core loop works end to end.*

- [ ] Idle low-cost Vosk grammar listening for the wake phrase ("Hey Lector")
- [ ] Settings toggle: push-to-talk and wake phrase independently enabled, both usable regardless of default
- [ ] "What can I say?" panel — categorized command reference (Moving around / Highlighting / Finding words / The app itself)
- [ ] First-run onboarding: mic permission request (skippable, app stays fully usable without it) + activation mode selection

## Milestone 9 — Packaging

*Don't consider the project "done" before this — packaging surfaces its own bugs.*

- [ ] Nuitka build config with dynamically-linked Qt (LGPL compliance — see `docs/TECH_STACK.md`)
- [ ] Build and test the installer on Windows
- [ ] Build and test the installer on Mac
- [ ] Sanity-check final bundle size against the project's original lightweight-vs-Adobe-Reader premise
