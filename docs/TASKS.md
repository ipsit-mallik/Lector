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

- [X] `pyproject.toml` with dependencies: pywebview, PyMuPDF, vosk, sounddevice (originally PySide6 — replaced by pywebview during Milestone 4; see `docs/TECH_STACK.md` § Revision)
- [X] Create `src/lector/` package skeleton matching `docs/ARCHITECTURE.md`'s folder layout exactly (empty `features/` subfolders + `shared/`)
- [X] `__main__.py` entry point that launches a blank window (now a `webview` window, originally a `QApplication` one)
- [X] Manually verify the app launches on both Windows and Mac

## Milestone 2 — Core reader (mouse/keyboard only, no voice)

*Prove PDF rendering and navigation work correctly before adding a second hard problem (speech) on top.*

- [X] `features/home/`: minimal Home screen — "Open PDF" button (native file dialog), empty/placeholder recent list
- [X] `features/reading/`: render a PDF page via PyMuPDF into the view (book layout only for now)
- [X] Page navigation: next/previous buttons + keyboard shortcuts (arrow keys or Page Up/Down)
- [X] Jump-to-page control
- [X] Zoom in/out
- [X] Apply `docs/DESIGN_SYSTEM.md` light-theme tokens to this base window chrome

## Milestone 3 — Highlighting core + save behavior

*The core value mechanic. Prove annotation persistence actually works before voice ever touches it.*

- [X] `features/annotations/`: mouse-based text selection using PyMuPDF character/line bounding boxes (character granularity, not word — a drag can stop mid-word, matching Adobe Reader's selection behavior)
- [X] Live selection feedback while dragging — text washes in the selection color as the cursor moves, following the cursor character by character, and only becomes a highlight on release, per `docs/DESIGN_SYSTEM.md`'s "two visible beats" note (verified by screenshotting the running app mid-drag, on release, and after a single-click selection)
- [X] Write selection as a real PDF highlight annotation via PyMuPDF (not a UI overlay)
- [X] Save-confirmation dialog: "Save a copy" / "Overwrite the original", pre-selected to copy, "Don't ask again" checkbox (unchecked by default) — generalized wording, fires only on explicit Save action
- [X] `features/settings/`: persist the save-behavior preference (a JSON file in the per-user app-data dir, not `QSettings` — that went with PySide6)
- [X] **Manual verification:** open the saved file in a different PDF viewer (not Lector) and confirm the highlight is actually there — implementation was verified by reopening both the copy and the overwritten original as fresh PyMuPDF documents and confirming the highlight annotation is structurally present in each, then confirmed by hand in a third-party viewer.

## Milestone 4 — Strip layout, Settings, theme, recent files

*Round out the non-voice app into something usable standalone.*

- [X] `features/reading/`: continuous-strip layout, toggle between book/strip
- [X] `features/settings/`: theme picker (light/dark/sepia) wired to `shared/theme.py` tokens
- [X] Recent files: persist last 10 opened files (path + timestamp) in the same `settings.json` store; render as the card grid on Home
- [X] Resume reading position: persist each document's last page + layout (book/strip) alongside its recent-files entry; restore both when it is reopened, governed by a Settings preference ("Continue where I left off" default vs. "Always start at the beginning"). Added at the developer's request after the rest of this milestone; verified by an end-to-end test over a real 20-page PDF (restore, start-at-beginning, clamping a stale page against a shortened file, corrupt stored values, recent-list order left undisturbed) and by a rendered screenshot of the new Settings section, which matches the option-row pattern of the save-behavior section above it.
- [X] Resume reading position now also covers zoom: the last zoom level (from the toolbar's step buttons, the zoom-popover slider, or a restored session) is stored and restored alongside page/layout, using the same recent-files entry and the same "Continue where I left off" preference — no separate setting. Added at the developer's request after the above was already shipped.
- [X] **Manual verification:** confirm in the running app that a document reopened from Recent comes back in strip layout when that is what it was left in — the restore path itself is covered by the test above; the reading view actually *coming up* in the restored layout was confirmed by hand in the running app.
- [X] Stub onboarding screens (placeholder only — full flow built in Milestone 8). `frontend/pages/onboarding.{html,css,js}` plus `features/onboarding/state.py`, which records an `onboarding_seen` flag in the same `settings.json` store. Home redirects to it on first run only, and both buttons ("Skip for now" / "Start reading") set the flag and continue — a screen that reappeared after being dismissed would not be skippable, which `docs/PRD.md` requires it to be. The mic button and the two activation-mode rows are deliberately inert and dimmed rather than fake-functional, and the screen says out loud that it is a placeholder. Verified by screenshot against `frontend/pages/settings.html`: it reuses that page's `.section` / `.option-row` markup, so the stub already sits in the layout the real flow will use. Neither activation mode is marked as recommended.

## Milestone 5 — Voice engine: push-to-talk only

*Isolate "does speech recognition work at all" from "does always-on listening work" as separate problems.*

- [X] `features/voice/`: load Vosk model, set up grammar-constrained (not general transcription) recognizer. `features/voice/engine.py` builds a `KaldiRecognizer` over an explicit word list plus `[unk]`, so anything outside the vocabulary comes back as nothing rather than as a wrong command. The model itself (~68 MB) is fetched by `scripts/fetch_vosk_model.py` into `assets/vosk_model/` and is gitignored, not committed. A missing model is a normal state, not a crash: the engine records the load error and reports `available: False`, and the UI keeps working.
- [X] Push-to-talk key binding (default: Space) — captures audio via `sounddevice` while held. `frontend/js/voice.js` binds keydown/keyup (ignoring auto-repeat, text fields, buttons, and open dialogs) and releases on window blur so a held key can't be left stuck; `api.py` exposes `start_listening` / `stop_listening`, and the microphone is closed when the window closes.
- [X] Emit recognized text as an event/signal other features can subscribe to. Python pushes each result to JS as a `lector:voice` CustomEvent, and final non-empty phrases are re-dispatched as `lector:command` — so Milestone 6's navigation commands and Milestone 7's matcher can each subscribe independently. Verified end to end without a microphone by synthesizing speech and feeding it through the engine's own audio worker; that pass caught a real bug where a phrase Vosk finished decoding mid-hold was dropped from the final result (`tests/test_voice_engine.py` now pins it). The push-to-talk indicator was screenshotted in all four states (idle / listening / heard / unavailable) on both Home and the reading view; listening uses `--color-voice-focus`, visibly distinct from the highlight yellow.

## Milestone 6 — Voice navigation commands

*Lowest-risk voice commands: no ambiguity, no matching logic, just intent → action.*

- [X] `command_grammar.py`: fixed vocabulary + synonym map for navigation ("next page"/"turn the page"/"forward", etc.). `features/voice/command_grammar.py` declares five intents (`NEXT_PAGE`, `PREV_PAGE`, `GOTO_PAGE`, `SCROLL_UP`, `SCROLL_DOWN`), each with several accepted phrasings, plus spoken page numbers ("go to page twenty three", "page 7", "one hundred and five"). The recognizer's word list is *derived* from those phrasings rather than maintained beside them, so a synonym cannot be added without the engine being able to hear it — `tests/test_command_grammar.py` pins both directions. First/last page were deliberately left out: `docs/PRD.md` does not list them, and adding commands is cheaper than removing ones readers have learned.
- [X] Wire recognized navigation intents to `reading/` page-nav functions. Python parses the phrase and ships the intent alongside the text (`api.py`); `frontend/js/voice.js` re-broadcasts it and `frontend/pages/reading.js` maps it through a `VOICE_ACTIONS` table onto the *same* `goNext` / `goPrev` / `goToPage` functions the toolbar buttons call, so mouse/keyboard parity is preserved by construction rather than by a parallel code path. Spoken scrolling moves 80% of a screen (the overlap keeps the line you were reading visible) and turns the page at the end of one, landing at the top of the next. Commands are ignored while a dialog is open. Verified against the real grammar and the real reading view over a 12-page PDF: every intent, both scroll directions, the page-turn fallback at both edges, and the first/last-page boundaries; screenshots confirm page 8 of 12 after "go to page eight", and an unrecognized phrase leaving the page untouched while the footer reads "Not a command I know — try “next page”" in the same muted style as the normal hint.
- [X] Fuzzy/edit-distance tolerance for recognition errors on command words (reuse for Milestone 7's matching, don't build twice). `features/voice/fuzzy.py` holds the primitive — Levenshtein distance, length-normalized similarity, and a `best_match` that returns `None` instead of a least-bad guess when nothing clears the threshold. It is a separate module from the grammar precisely so Milestone 7's matcher imports it rather than reimplementing it; ties resolve to the earliest candidate, which is how that milestone's "first occurrence in reading order wins" rule will fall out of ordering alone. The tolerance recovers "text page" and "prevous page" while still refusing "bananas", and is deliberately tight enough that "next" and "previous" — opposite actions — cannot cross. The whole suite is 60 tests (4 skipped: they need the Vosk model, which is not committed).

## Milestone 7 — Voice highlighting (speech-to-match)

*The hardest, riskiest piece — build last, once rendering, annotation writing, and recognition are each already proven independently.*

- [X] `annotations/highlight_matcher.py`: fuzzy-match spoken phrase against the word list of the currently visible viewport only
- [X] Disambiguation: viewport-only scope, first occurrence (reading order) wins ties
- [X] Word-level highlight as baseline; sentence-level extension via nearest-punctuation heuristic
- [X] Wire the "highlight" voice command to the matcher + annotation writer
- [X] Wire the "save" voice command to explicitly trigger the save dialog — confirm it never fires automatically as a side effect of highlighting

## Milestone 8 — Wake phrase, discoverability, onboarding

*Polish layer, once the core loop works end to end.*

- [X] Idle low-cost Vosk grammar listening for the wake phrase ("Hey Lector"). `features/voice/wake.py` builds a second recognizer whose entire vocabulary is the wake phrase plus `[unk]`, so room conversation is rejected by the recognizer rather than transcribed and then filtered. It matches on **final** results only — partials are retractable, and acting on one opened a command window on a half-heard word. A hit hands straight over to the normal command capture in `engine.py`, so wake and push-to-talk converge on one listening path instead of two. The phrase has a single home: `WAKE_PHRASE_DISPLAY` is derived from the recognized words rather than typed out again, and a test asserts the displayed form and the grammar's form are the same words, so Settings and onboarding cannot advertise a phrase nothing is listening for.
- [X] Settings toggle: push-to-talk and wake phrase independently enabled, both usable regardless of default. A "Voice activation" section holds two equally-weighted cards, each with a real `<input type="checkbox">` behind the switch so keyboard and screen-reader semantics come for free — the same parity the voice features themselves are held to. Both flags are sent together on every change, because the engine has to know the whole state to decide whether a microphone should stay open. Neither mode is badged as recommended. Screenshot-verified against `docs/mockups/06 Settings.png`; the mockup's rebind control and editable phrase field are deliberately rendered as static facts marked "fixed for now", since neither is implemented and a dead control is worse than an honest label. That pass caught the phrase rendering lowercase, fixed at the source rather than per-page.
- [X] "What can I say?" panel — categorized command reference (Moving around / Highlighting / The app itself). Built from the live grammar in `features/voice/reference.py`, not hand-written, and `tests/test_command_reference.py` feeds every advertised phrase back through `command_grammar.parse` — a reference that lists wording the recognizer rejects teaches the reader that voice is broken. Each row shows its phrasings, what it does, and the keyboard or mouse equivalent, with a test asserting no command ships without one. A live search field filters rows and hides emptied categories. **"Finding words" is deliberately absent**: in-document search does not exist, and an empty category reads as a broken feature rather than a missing one — a test pins its absence so it is added the day search is. Screenshot-verified against `docs/mockups/05`, including the filter narrowing to a single category; the badge uses `--color-voice-focus`, not the highlight yellow, per `docs/DESIGN_SYSTEM.md`'s separation of "this is about listening" from "this text is highlighted".
- [X] First-run onboarding: mic permission request (skippable, app stays fully usable without it) + activation mode selection. Two steps replacing the Milestone 4 stub. The permission step calls `request_microphone`, which briefly opens the device — neither Windows nor macOS raises its prompt any other way — and returns a refusal as data, shown as a sentence rather than an error. Skipping leaves the microphone unasked rather than refused. Every exit lands in the same working app; with no microphone access no mode is enabled, because an indicator claiming to listen without a microphone is worse than one saying voice is off. The flag is set on every path, since a screen that reappears after dismissal is not skippable. Both steps screenshot-verified against `docs/mockups/07a`/`07b`; neither mode card is marked recommended, and selection uses `visibility` rather than `display` so choosing one does not resize the cards. That pass found a real bug — `.btn`'s `display: inline-flex` overrode the UA `[hidden]` rule, leaving "Start reading" visible on step 1 — fixed in `components.css`, which also repaired the save dialog's "Don't Save".

## Milestone 9 — Packaging

*Don't consider the project "done" before this — packaging surfaces its own bugs.*

- [ ] Nuitka build config (the LGPL/dynamically-linked-Qt requirement went away with PySide6 — see `docs/TECH_STACK.md`)
- [ ] Build and test the installer on Windows
- [ ] Build and test the installer on Mac
- [ ] Sanity-check final bundle size against the project's original lightweight-vs-Adobe-Reader premise
