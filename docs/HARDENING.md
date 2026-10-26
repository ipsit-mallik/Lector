# Lector — Hardening Instructions for Claude Code (v2, written against the real repo)

This replaces my first version, which was written blind. I have now read
`CLAUDE.md`, `docs/PRD.md`, `docs/TECH_STACK.md`, `docs/ARCHITECTURE.md`,
`docs/TASKS.md`, `pyproject.toml`, `.gitignore`, the top of `CHANGELOG.md`, the
`src/lector/` file layout, the test file list, and the code paths named
below (`document.py`, `api.py`, `settings/store.py`, `__main__.py`, imports
across `features/`). I did NOT read the `frontend/` folder, `DESIGN_SYSTEM.md`
in full, or most of the voice code. Anything about those is marked unverified.
When I wrote this file I changed nothing in the Lector folder.

## HOW TO RUN THIS FILE (Claude Code: read this section first, every session)

This file lives at `docs/HARDENING.md`. It is both the instructions and the
progress tracker. The developer starts every session with one phrase:
**"continue hardening"** (or names a task, e.g. "do H3"). When you see it,
follow this protocol. Read this whole file before doing anything.

**What you may edit in this file:** only the STATUS BOARD, the FINDINGS
section and the TASK LOG at the bottom. Never edit the task specs, the ground
rules or the checklist. If you think one of them is wrong, say so in your
reply and ask.

### STATUS BOARD

| Task | What | Plan mode | Status | Branch / commit | Review |
|------|------|-----------|--------|-----------------|--------|
| H1 | Audit (read-only on code) | no | reviewed | - (no code changed) | developer read FINDINGS, 2026-10-23 |
| H2 | Save safety | yes | reviewed | committed to master as 1a6497f (branch merged and removed) | review 2026-10-23: 1 fix; fixed 2026-10-23; re-review 2026-10-24: all PASS |
| H3 | Errors and logging | yes | reviewed | committed to master as e932540, pushed (branch merged and removed) | review 2026-10-24: all PASS |
| H4 | Settings file safety | yes | reviewed | committed to master as e4385b6, pushed (branch merged and removed) | review 2026-10-25: all PASS |
| H5 | Odd and hostile PDFs | yes | reviewed | committed to master with this file (the commit that added docs/HARDENING.md), pushed (branch merged and removed) | review 2026-10-26: all PASS |
| H6 | PDF fixtures and regression tests | no | todo | - | - |
| H7 | Guard-rail tests | no | todo | - | - |

Status values: `todo` -> `implemented` -> `reviewed`. Also `report-ready`
(H1 only, waiting for the developer to read FINDINGS) and `needs-fixes`
(a review found problems).

### Step 0 — decide what kind of session this is

1. Read the STATUS BOARD. Take the first task, in order H1..H7, that is not
   `reviewed`, unless the developer named a task.
2. Then:
   - `todo` or `needs-fixes` -> **WORK SESSION**.
   - `implemented` -> **REVIEW SESSION**.
   - `report-ready` (H1) -> **H1 CHECK-IN**.
3. Start your reply with one sentence saying which task and which kind of
   session you picked.
4. One task per session. If asked for two, do the first and stop.
5. If a REVIEW is due but you implemented that task in this same
   conversation, do nothing else. Say: "A review needs fresh context. Open a
   new session and say 'continue hardening'."

### H1 CHECK-IN

Ask the developer whether they have read FINDINGS and want to proceed. On a
yes, set H1 to `reviewed` and continue straight into the H2 WORK SESSION
steps below. Do not start H2 work before that yes.

### WORK SESSION (H1 audit, and H2-H7 implementation)

1. **H1 only:** do the audit in the H1 section. You may change this file and
   nothing else. Write the report into the FINDINGS section (numbered to match
   the audit questions, then the prioritized list), set H1 to `report-ready`,
   and end with: "H1 report is in docs/HARDENING.md under FINDINGS. Read it,
   then open a new session and say 'continue hardening'." Stop there.
2. **H2-H7:** read FINDINGS and the TASK LOG entries of earlier tasks.
   Re-verify in the code anything you rely on; the findings may be stale.
3. **Plan mode.** If the STATUS BOARD says Plan mode = yes and you are not in
   plan mode: STOP without touching any file. Tell the developer: "Press
   Shift+Tab until plan mode is on, then say 'continue hardening'." In plan
   mode, present the plan the task spec asks for, ask the developer about any
   product decision the spec lists, and wait for plan approval. For Plan mode
   = no, give a short plan in your reply and proceed.
4. **Before editing:** if the working tree has uncommitted changes unrelated
   to this task, stop and ask. Otherwise create a branch
   `hardening/hN-short-name` if you are on main/master.
5. **Implement** exactly the task spec, within the ground rules. Run the whole
   test suite before and after and report both results.
6. **Write a TASK LOG entry** for the task: date, branch, plan summary
   (5-10 lines), decisions the developer made, files changed, test results
   before/after, anything you could not verify, screenshots taken (or "not
   possible"). Set the task to `implemented`.
7. **Do not check anything off in `docs/TASKS.md`** and do not add these tasks
   to it.
8. **Commit question.** Ask the developer whether to commit, with a proposed
   Conventional Commits message, and follow their answer (including whether to
   push). Do not include `docs/HARDENING.md` in the commit unless they say so.
   Record the commit hash in the TASK LOG, or "uncommitted" so the reviewer
   knows to diff the working tree.
9. End your reply with exactly: "H<n> implemented. Open a NEW session and say
   'continue hardening' for the independent review."

### REVIEW SESSION

1. The task is the one marked `implemented`. Read its TASK LOG entry for the
   branch, base and commit (or "uncommitted").
2. Get the diff yourself (`git diff <base>...<branch>`, or the working tree if
   uncommitted). Read the diff itself; do not trust the log's description of
   it.
3. Run the whole test suite.
4. Under that task's TASK LOG entry, add a **Review** block with:
   - every item of the "Fresh-context review checklist" below: PASS / FAIL /
     N/A, with one line of evidence (file:line);
   - every explicit requirement in the task spec: met / not met, with
     evidence.
5. Any FAIL or unmet requirement: set the task to `needs-fixes`, list exactly
   what to fix, and tell the developer to open a new session and say
   "continue hardening". Do not fix things in the review session.
6. All PASS: set the task to `reviewed`, say which task is next, and tell the
   developer to open a new session and say "continue hardening".
7. State plainly what this review did not cover. It is the same model reading
   the code again with fresh context: it catches real mistakes, but it is not
   proof that the code is correct.

### Rules that hold in every session

- Never commit, push, or edit `docs/TASKS.md` without asking first.
- Do not change anything outside the current task's scope.
- If the spec does not cover a decision, stop and ask. Do not guess.
- Visual changes need a screenshot compared against `DESIGN_SYSTEM.md` before
  you call the task implemented; if you cannot take one, say so and mark it
  unverified in the TASK LOG.

---

## What the real code changed about my advice

1. **The project is far past where I assumed.** `TASKS.md` shows Milestones
   1-8.12 done. Open: 8.13 (voice search on library pages), 8.14 (voice find
   in document), and all of Milestone 9 (packaging). So this is a hardening
   pass *before packaging*, not a build-from-scratch plan.
2. **My "isolate PyMuPDF into one module" task is dropped.** Five files import
   it today (`reading/document.py`, `reading/ocr.py`, `reading/search.py`,
   `annotations/highlighter.py`, `home/recent.py`), and `ARCHITECTURE.md`
   deliberately says `annotations/` owns the PyMuPDF write layer. A big
   refactor now contradicts a documented decision, costs a lot, and buys
   nothing until you actually decide on licensing. Replaced by a cheap
   "don't let it spread" test (task H7).
3. **"Voice defaults OFF" was wrong for this project.** Settings default is
   push-to-talk ON (mic opens only while the key is held) and wake phrase
   OFF (`settings/store.py` `_DEFAULT_PUSH_TO_TALK = True`). That is a
   documented, reasonable choice. Leave it.
4. **My CLAUDE.md block is deleted.** Your `CLAUDE.md` already has the
   workflow rules and mine would conflict (it said "one commit per task";
   yours says ask before committing). Do not add a second set of rules.
5. **Real risks I confirmed in the code** (these drive tasks H2-H5):
   - Overwrite save is `self._doc.saveIncr()` (`document.py` ~L575): in place,
     no temp file, no backup, no verification.
   - Save-a-copy is `self._doc.save(new_path, deflate=True)` (~L579): writes
     straight to the destination. Whether an existing file gets silently
     overwritten depends on the in-app Save-As screen; unverified.
   - `Api.perform_save` (`api.py` ~L655) catches every exception and returns
     `str(exc)` to the UI, i.e. raw library error text for the user.
   - `settings/store.py` `_save()` writes `settings.json` directly with
     `open(path, "w")`. A crash mid-write leaves a truncated file. Loading
     falls back to `{}` on corruption (good), but there is no schema version
     and no backup of the bad file.
   - No file logging or global exception handler found. `logging` is used in
     a few modules, but I found no handler setup and no `excepthook`. Verify.
   - `tests/fixtures/` holds only three `.wav` files. There are no PDF
     fixtures, and no test I could see for a failed or interrupted save.
   - `import fitz` is used (older alias). Works; cosmetic.
6. **Two doc inconsistencies you should decide on (not Claude Code):**
   - `PRD.md` and `TECH_STACK.md` say the project is **MIT**. PyMuPDF/MuPDF is
     AGPL-3.0 (or Artifex commercial). An MIT project that depends on and
     distributes PyMuPDF is not simply "MIT": the combined work is effectively
     AGPL. The docs only discuss the old Qt/LGPL issue. This does not block
     building, but it must be settled before Milestone 9 produces anything
     you hand to other people. You said you'd defer it; the gate below makes
     that deferral explicit instead of forgotten.
   - `PRD.md` calls the product "read-only" in its problem statement, yet it
     writes highlights into the PDF (and overwrites the original if chosen).
     That is fine as a product decision, but it is exactly why save safety
     (H2) matters more here than for a pure viewer.

## Ground rules for every task below (from your own CLAUDE.md, restated)

- Work in the repo's own order. These tasks are proposed as a new block
  **before Milestone 9** in `docs/TASKS.md`. Claude Code must NOT add them
  to `TASKS.md` itself without asking, because that file is the developer's
  checklist and changes scope (rule: stop and ask when docs don't cover it).
- **Ask before committing.** Never commit or push without asking, and wait for
  the message/push decision. This overrides any "commit at the end" habit.
  Suggest a branch name and a message in the reply, then stop.
- **doc-sync-on-change:** if a fix changes behavior documented in `PRD.md`,
  `TECH_STACK.md` or `ARCHITECTURE.md`, update that doc in the same change.
- **changelog-on-change:** any user-facing change gets a `[Unreleased]` entry
  in `CHANGELOG.md` in plain language, like the existing entries.
- **verify-before-checkoff:** anything that changes what the app looks like
  (error dialogs, messages, toasts) needs a screenshot of the running app
  compared to `DESIGN_SYSTEM.md` before it is reported done. If a screenshot
  is impossible, say so and mark it unverified.
- Do not touch `docs/ENGINEERING_LOG.md` (does not exist; rule 3).
- Windows AND Mac: nothing here may break the Mac path. Windows-only
  behavior (file locking, `%APPDATA%`) must degrade cleanly on Mac.
- Don't add dependencies. Everything below works with the standard library
  and PyMuPDF.
- Keep the PRD scope. Anything that adds a feature not in the PRD gets
  flagged as a scope change and confirmed first.

## Order of work

The developer runs the loop; the protocol at the top drives Claude Code.

1. Developer: check `git status` and commit/stash current work first. (I
   could not verify the repo state.)
2. Each session: say "continue hardening". Order: H1 (audit), then for each of
   H2-H7 a work session followed by a separate, fresh review session.
3. Developer's manual steps per task: switch to plan mode when asked
   (Shift+Tab), approve the plan, answer the commit question, and open a new
   session for the review.
4. Licensing gate (below) before Milestone 9.

---

## H1 — Audit the suspects (read-only, change nothing)

```
Read-only audit of this repo. Do NOT modify any source, test, doc or config
file, and do not run git commands that change state. The ONLY file you may
write is docs/HARDENING.md, and only its FINDINGS section and STATUS BOARD,
as described in the protocol. Report with file paths and line numbers.

1. SAVE PATH. Trace every route into Document.save_overwrite and
   Document.save_copy (src/lector/features/reading/document.py) and
   Api.perform_save (src/lector/api.py). Answer:
   a. What happens on disk if the process is killed during saveIncr()?
   b. Does the in-app Save-As screen (Milestone 8.7) stop the user choosing
      a path that already exists? What does the user see?
   c. Api._suggested_copy_path produces "<name> (highlighted).pdf" but the
      Settings screen promises "<name> — highlighted.pdf". Which one is
      actually used, and is the mismatch real?
   d. What happens for a read-only file, a locked file, a full disk, an
      encrypted PDF, or a path on a drive that disappears?
2. ERRORS. List every `except Exception` / bare except in api.py and the
   features, and what each one returns to the frontend. Flag any that send
   raw exception text to the user.
3. LOGGING. Is there any logging configuration, log file, or global
   exception hook (sys.excepthook / threading.excepthook)? Where do
   uncaught exceptions on bridge threads and the OCR worker go?
4. SETTINGS. Trace settings/store.py _load/_save. Is _save safe against a
   crash mid-write and against two bridge threads writing at once?
5. OPENING PDFs. What does Document.open do for: zero-byte file,
   truncated/malformed PDF, password-protected PDF, a path that no longer
   exists (e.g. a Recent entry whose file was deleted)? Does Api.open_pdf
   report each to the user in plain language?
6. UNTRUSTED CONTENT. In src/lector and frontend/, is any PDF-supplied data
   ever executed, navigated to, or opened externally (links, URIs, launch
   actions, embedded JS)? List what exists, or say "none found".
7. NETWORK. Every use of sockets/HTTP/urllib in application code
   (excluding scripts/). Expected: only shared/frontend_server.py on
   127.0.0.1. Confirm or list others.
8. PYMUPDF SPREAD. Confirm the list of modules importing fitz/pymupdf and
   count call sites per module.
9. TESTS. Which of the above have tests today? Name them. Which are untested?

Finish with a short prioritized list: what could lose or corrupt a user's
file, what could crash, what is fine. Verify claims against the code; mark
anything you could not verify.
```

---

## H2 — Save safety (highest priority; this is the one that can lose data)

Plan mode first. Do not edit until the plan is approved.

```
Read the H1 FINDINGS in docs/HARDENING.md and the current save code, then
propose a plan. Do not edit until I approve it.

Goal: a failed or interrupted save must never damage the user's original PDF.

Design questions your plan must answer explicitly (they are not obvious):
- saveIncr() appends to the file in place; it cannot be made atomic with a
  temp file. Propose replacing "overwrite" with: full save to a temp file in
  the SAME directory -> flush/fsync -> reopen the temp file with PyMuPDF to
  confirm it opens and has the expected page count and highlight count ->
  keep a backup of the original until verified -> os.replace(temp, original).
- PyMuPDF holds the original file open while the document is open. On
  Windows, os.replace over a file that is still open will likely fail with a
  permission error. Determine the real behavior and say how you will handle
  it (close and reopen the document around the replace). The reopen must
  keep: current page, zoom, layout, OCR state, and the undo/redo stacks
  (the stacks reference annotation xrefs, which can change). If undo/redo
  cannot survive the reopen safely, say so and propose the least bad option.
- save_copy's comment says garbage collection corrupts the live document.
  Keep that constraint; do not add garbage=... to either path.
- Existing-file collision for "Save a copy": never silently overwrite an
  existing file. Reuse the in-app Save-As screen's behavior if it already
  confirms; otherwise add a confirm or auto-number ("name (2).pdf"). Resolve
  the "(highlighted)" vs "— highlighted" naming mismatch against
  DESIGN_SYSTEM.md / the Settings copy and pick one.
- Backup policy for overwrite: delete after verified success, or keep as a
  setting? This is a product decision: list the options and ask me. Do not
  add a new setting without asking (scope).
- Mac and Windows: say what differs.

Required failure handling, each with a plain-language message that says what
happened and that the original file is safe: read-only file, no permission,
file locked by another program, disk full, destination folder missing,
drive or network share gone mid-save.

Tests (pytest, in tests/, using tmp_path and monkeypatching; PDFs built in
code with PyMuPDF so no binary fixtures are committed):
- highlight -> save copy -> reopen -> highlight present, original untouched
- highlight -> overwrite -> reopen -> highlight present
- forced failure mid-save (monkeypatch the write) -> original is
  byte-for-byte identical (compare hashes), and no stray temp files remain
- each failure mode above returns ok=False with a friendly message
- existing destination file is not clobbered

Also follow the repo rules: CHANGELOG entry, doc updates if PRD/ARCHITECTURE
statements about saving change, screenshot any new dialog text against
DESIGN_SYSTEM.md, and do NOT commit; propose a branch name and commit
message and wait for me.
```

---

## H3 — Errors and logging (no tracebacks to users, no content in logs)

```
Plan first, then wait for approval.

1. Replace the `return {"ok": False, "error": str(exc)}` pattern in
   src/lector/api.py (perform_save and any other broad except you found in
   H1) with a small mapping from known failures to short plain-language
   messages written in the app's voice (check DESIGN_SYSTEM.md copy rules).
   Unknown errors get a generic message plus a log entry, never raw text.
2. Add logging setup at startup in __main__.py: a rotating file handler in a
   `logs` folder next to settings (use settings.settings_dir()), small size
   cap, a few backups. Works on Windows and Mac via the existing
   settings_dir().
3. Install sys.excepthook and threading.excepthook so an uncaught exception
   is logged and the user sees a short message, not a crash or traceback.
   Make sure exceptions in OCR worker processes and voice threads end up in
   the log too.
4. Logs must never contain document text, OCR output, highlight text or voice
   transcripts. File NAMES only when needed. Add a test that exercises the
   logger with a highlight/OCR/voice scenario and asserts none of the test
   document's words appear in the log output.
5. If any error text is shown in the UI, screenshot it and compare against
   DESIGN_SYSTEM.md (verify-before-checkoff). CHANGELOG entry. Do not commit;
   ask me.
```

---

## H4 — Settings file safety

```
Plan first.

settings/store.py:
- Make _save() atomic: write to a temp file in the same folder, flush and
  fsync, then os.replace. Serialize writers with a lock, because bridge
  calls can come from several threads.
- If settings.json exists but cannot be parsed, rename it to
  settings.json.corrupt-<timestamp> (keep one or two) before falling back to
  defaults, so a user's Recent list and positions are not silently destroyed
  on the next write.
- Add a "schema_version" key with a no-op migration hook. Do not change any
  existing key names or defaults.
- Tests: crash mid-write leaves the old file intact; corrupt file is
  preserved and defaults load; unknown keys survive a round trip.
- Keep the public functions' signatures unchanged. CHANGELOG entry only if
  user-visible. Do not commit; ask me.
```

---

## H5 — Opening odd and hostile PDFs

```
Plan first.

Make opening a file fail gracefully, using the H1 findings:
- zero-byte, truncated/malformed, and password-protected PDFs: Api.open_pdf
  returns a plain-language failure, the app stays on the screen it was on,
  and nothing is added to Recent. For password-protected files, a clear
  message is enough unless I approve a password prompt (that is a new
  feature; flag it, don't build it).
- A Recent or Favorites entry whose file was moved or deleted: friendly
  message, and offer the existing "remove from Recent" path. Do not delete
  anything on disk, ever.
- Huge PDFs (hundreds of pages): confirm nothing at open time walks every
  page on the UI path. highlight_counts() iterates all pages; measure it on
  a generated 500-page PDF and report. Fix only if it is actually slow.
- Untrusted content: if H1 found any place where PDF-supplied URIs/actions
  can be followed, route them through an explicit confirm showing the
  destination, or remove that path. If H1 found none, add a test asserting
  the reader never opens external targets automatically, and stop.
Tests with PDFs generated in code. CHANGELOG entry. Screenshot any new UI
text. Do not commit; ask me.
```

---

## H6 — Fixtures and regression tests

```
The repo's tests mirror features/ and use pytest; tests/fixtures currently
holds only .wav files. Add PDF test helpers, built in code with PyMuPDF
(tests/pdf_support.py, matching the existing tests/voice_support.py pattern),
not committed binaries. Cover: plain text, multi-page, rotated page,
image-only (scanned-looking) page, encrypted with a known password, unicode
text, an already-highlighted file, zero-byte, truncated.

Use them to add regression tests for anything H2-H5 did not already cover:
highlight round trip, search on a no-text page, recolor/remove highlight then
save. Follow the existing test naming. Run the whole suite, report failures
honestly, and fix real bugs one at a time. Do not weaken or delete existing
tests to get green. Do not commit; ask me.
```

---

## H7 — Guard rails (cheap, replaces my earlier "isolate PyMuPDF" task)

```
Add two small tests; no refactor.

1. test: only these modules may import fitz/pymupdf (the current five:
   features/reading/document.py, ocr.py, search.py,
   features/annotations/highlighter.py, features/home/recent.py). Fails if a
   new file imports it. Message should say "decide in ARCHITECTURE.md before
   spreading PyMuPDF further".
2. test: scan src/lector for network modules (urllib.request, http.client,
   requests, httpx, aiohttp, ftplib, smtplib, and `socket` outside
   shared/frontend_server.py and single_instance.py). scripts/ is exempt
   (model download scripts). The localhost frontend server and the
   single-instance pipe are expected and allowed. This backs the "fully
   offline" line in the Settings footer.

Optional, only if I say yes: switch `import fitz` to `import pymupdf` in the
five files (same API, newer name). CHANGELOG not needed (not user-facing).
Do not commit; ask me.
```

---

## Licensing gate (before Milestone 9, not before)

Nothing above needs this decided. But **do not build an installer or hand a
build to anyone** until you choose:

1. Publish source under AGPL-compatible terms (and correct the "MIT" claims
   in `PRD.md` / `TECH_STACK.md`),
2. buy Artifex's commercial license, or
3. replace PyMuPDF. Be realistic about the cost: PyMuPDF is currently
   rendering, word boxes, annotation writing AND the built-in Tesseract OCR.
   Swapping engines means redoing all four, so this is a large change, not
   a configuration tweak.

Paste into `CLAUDE.md` yourself if you want it enforced (I did not edit it):

```
- Before building an installer or sharing any build (Milestone 9), stop and
  ask me to settle the PyMuPDF licensing question first (docs say MIT; the
  dependency is AGPL/commercial).
```

Also worth knowing for Milestone 9, per `TECH_STACK.md`: WebView2 runtime on
bare Windows, the OCR worker process under Nuitka, and Mac packaging are all
still unverified.

---

## Fresh-context review checklist (after each task)

- Can any failure path change or lose the original PDF? (must be no)
- Any new `fitz`/`pymupdf` import outside the five allowed files?
- Any new network use besides the localhost frontend server?
- Any raw exception text or traceback reaching the UI? Any document text,
  OCR output or transcript in logs?
- Does anything new block the UI thread or run PyMuPDF from several threads
  without the serialization `recent.py` already uses?
- Windows AND Mac still fine?
- Tests added for the change and the whole suite passing?
- CHANGELOG `[Unreleased]` entry present; docs updated if behavior moved;
  screenshots taken for visual changes; NOTHING committed without asking.

## Outside Claude Code

- Keep a few real PDFs of your own for manual testing (not committed): a
  scanned book, a very large one, a password-protected one, one with forms.
- Pin dependency versions before Milestone 9 (`pyproject.toml` lists all six
  unpinned) and run `pip-audit`.
- Run `gitleaks` before pushing the repo anywhere public.
- Test the final build on a second, clean Windows account or machine, and
  on a Mac, before giving it to anyone.

---

## FINDINGS (H1 report goes here; Claude Code fills this in)

**H1 audit, 2026-10-23**, against `master` @ `551cb44`. Read-only: no source,
test, doc or config file was changed. Claims marked **verified** were
reproduced by probe scripts run outside the repo (synthetic PDFs in a temp
folder; settings redirected to a temp `settings.json`). Environment: Windows
11, Python 3.13, PyMuPDF 1.28.2, pywebview 6.2.1. **Nothing was checked on
a Mac.** Baseline suite: `python -m pytest -q` → 1101 passed, 2360 subtests
passed.

### 1. Save path

Routes in: `reading.js:1267-1307` `promptSaveIfDirty` (Ctrl+S, the back
button, leaving for Home/Settings `reading.js:1820-1824`, a file dropped
on Reading `reading.js:1832-1844`, window close via
`api.py:1083-1118` → `promptSaveIfDirty(true)`) → `Api.perform_save`
(`api.py:655-669`) → `PdfDocument.save_overwrite` (`document.py:575-577`,
`saveIncr()`) or `save_copy` (`document.py:579-585`,
`save(path, deflate=True)`). The voice commands `SAVE_COPY`/`SAVE_OVERWRITE`
go through the same dialog (`save-dialog.js:72-87`). Nothing else calls
these methods.

- **a. Killed during `saveIncr()`.** **Verified:** `saveIncr` only appends.
  The original bytes stay identical as a prefix of the saved file. I
  simulated a kill by truncating the appended part at about 40 cut points;
  every cut reopened with the original pages and text, and the highlights
  from that save were simply missing. So a kill loses that save's
  highlights, not the PDF. Not tested: a real power loss or an OS write
  cache, and a kill inside MuPDF's own repair path. There is no backup and
  no check after the write.
- **b. Save-As onto an existing path.** **Verified: it is not stopped, and
  the user sees nothing.** `file-browser.js:639-646` `confirmSave` only adds
  `.pdf` and joins the path, with no existence check. `activateRow`
  (`file-browser.js:606-617`) even copies an existing file's name into the
  filename field when its row is clicked. `save_copy` then replaced an
  unrelated 7-page PDF with the 3-page document, silently. MuPDF *deletes*
  the destination before it writes (seen in the error text
  `cannot remove file …`). So a write that fails partway (full disk, a
  drive that goes away) would leave neither the old file nor a complete new
  one. That last part is inferred and was not reproduced.
  - Copy onto **the open file itself**, same path string: MuPDF refuses
    (`save to original must be incremental`). The user gets that raw text
    in an `alert`, and the file is untouched. **Verified.**
  - Same file under a **different spelling** (upper-case name, forward
    slashes): on Windows MuPDF cannot delete the file it has open, so it
    fails with `cannot remove file … Permission denied` and the original is
    untouched. **Verified on Windows.** On macOS, deleting an open file
    succeeds, so this path could unlink the original and write a fresh
    copy in its place. **Unverified.**
- **c. Naming.** **The mismatch is real.** The suggestion actually used is
  `<name> (highlighted).pdf` (`api.py:671-673` → `get_save_dialog_start`
  `api.py:218-226`; **verified** output `api_ro (highlighted).pdf`). Only
  `settings.html:259` says `"name — highlighted.pdf"`. `DESIGN_SYSTEM.md:417`
  and `CHANGELOG.md:85` both use `(highlighted)`, so the Settings subtitle
  is the odd one out.
- **d. Failure cases** (all reach `perform_save`'s `except Exception` →
  `{"ok": False, "error": str(exc)}` → `alert("Could not save the
  PDF:\n" + error)` at `reading.js:1304`). After every failure `is_dirty`
  stays True, so the highlights are still in memory and the reader stays on
  the page. **Verified** for read-only.
  - Read-only file (overwrite): `code=2: cannot open file '<full path>':
    Permission denied`. Original byte-identical. **Verified.**
  - Locked by another program (a handle opened with share-read only):
    same `Permission denied` text, original intact. **Verified.**
  - Destination folder missing (copy): `code=2: cannot open file
    '<path>': No such file or directory`. **Verified.**
  - Encrypted PDF: never gets as far as saving (see 5).
  - **A PDF MuPDF had to repair on open** (truncated, damaged xref, which
    is common in real-world files) **can never be overwrite-saved**:
    `code=4: Can't do incremental writes on a repaired file`, shown raw.
    `doc.can_save_incrementally()` returns 0 for it. "Save a copy" still
    works. **Verified.**
  - Full disk, or a drive/network share that disappears mid-save: **not
    reproduced** (no way to do it safely here). By the mechanics above:
    overwrite leaves a partial append, which behaves like the kill case.
    Copy leaves a partial file at the destination, after any existing file
    there was already deleted.
  - For H2's design: **verified** that `os.replace(temp, original)` while
    PyMuPDF holds `original` open fails on Windows with `PermissionError
    [WinError 5] Access is denied`. The document has to be closed around
    the replace.

### 2. Errors (`except Exception` / broad catches, and what reaches the UI)

Sends raw exception text to the user (**FLAG**):
- `api.py:668-669` `perform_save` → `str(exc)` → `alert` at
  `reading.js:1304`. That means MuPDF codes and full paths (see 1d). A test
  pins this behaviour: `tests/test_api.py:367-374` asserts
  `{"ok": False, "error": "disk full"}`, so H3 has to change that
  assertion on purpose.
- `engine.py:260-262` `"Could not load the speech model: {exc}"`,
  `engine.py:430-434` `"Could not open the microphone: {exc}"`, and
  `engine.py:226-229` (`"No speech model at <path>. Run: python
  scripts/…"`) → `status()["error"]` → the voice box detail line
  (`voice-box.js:36,86`) and the mic pill tooltip (`reading.js:1944`).
- `audio_frontend.py:143-145` `"Could not load the VAD model: {exc}"`,
  same kind of channel.
- `voice.js:237,299,307` `String(err)` of a rejected bridge call → voice
  box detail. pywebview rejects with the Python exception's message
  (`webview/util.py` `js_bridge_call`).

Return a fixed plain-language message (fine):
- `api.py:193-194` `show_in_folder` → "Couldn't open the folder."
- `api.py:792-793` `request_microphone` → returns `str(exc)`, but
  `onboarding.js:60-73` never shows it (fixed copy).
- `browser.py:48-56` `list_directory` → `error: str(exc)`, but
  `file-browser.js:228` only shows "— couldn't be read".

Swallowed silently, nothing logged (worth a log line in H3):
- `api.py:1015-1016` (`_on_voice_result` `evaluate_js`),
  `api.py:1076-1077` (`_dispatch_page_event`), `engine.py:200-206`
  (`_emit`: a subscriber that raises is dropped with `pass`),
  `engine.py:570-571` (stream teardown), `engine.py:636-639` (bad audio
  frame, `continue`).
- `recent.py:67-81,105-121`: an unreadable PDF becomes 0 pages and no
  thumbnail (intended; no log).
- `ocr_cache.py:57,84,98`: `utime`/`stat`/`unlink` failures (harmless).

Caught and logged (fine): `ocr.py:390-393,411-422`,
`ocr_cache.py:48-50,72-73`, `frontend_server.py:139-151`,
`single_instance.py:99-112`, `window_chrome.py:53,126,144,160`,
`__main__.py:40-43`. Narrow catches with defined fallbacks:
`store.py:89,469`, `document.py:424`, `engine.py:474,719,753`,
`single_instance.py:47,60,87,120,131,138,158`.

### 3. Logging

- **No logging configuration anywhere.** No `basicConfig` and no handlers;
  the root logger has none (**verified** at runtime). `__main__.py:15`
  only creates a logger. No log file.
- **No `sys.excepthook` / `threading.excepthook`** anywhere in `src/`.
- Where things go today:
  - Records from `lector.*` loggers: WARNING and above reach stderr through
    `logging.lastResort`. INFO and DEBUG are dropped. In a windowed,
    packaged build there is no stderr, so all of it is lost.
  - Bridge calls: pywebview runs **every call on a new thread**
    (`webview/util.py` `js_bridge_call`, verified in 6.2.1). It catches the
    exception, logs the traceback to its own `pywebview` logger (which has
    a stderr `StreamHandler`), and rejects the JS promise with
    `{message, name, stack}`. The frontend then either shows a generic
    toast (open paths), shows `String(err)` (voice), or has no `catch`
    (console only).
  - Other threads (`voice-wake-start` `api.py:761-765`, the engine worker
    `engine.py:437-438`, the server thread): uncaught exceptions go to the
    default `threading.excepthook` → stderr.
  - OCR worker process: an exception in `recognize` is pickled back and
    logged by `ocr.py:415-416` (`log.exception`, page index only). A hard
    crash leaves the pending result unanswered until the 120 s timeout
    (`ocr.py:51,407-414`); then the worker is replaced and the page shows
    "Couldn't read the text on this page" (`reading.js:709,746`).
- Content in log calls today: page indexes, cache file names (hashes) and
  paths. No document text, OCR output or transcripts were found in a
  `lector.*` log call. pywebview's traceback logging could include whatever
  an exception message contains. Not exhaustively checked.

### 4. Settings (`store.py:81-97`)

- `_save` is `open(path, "w")` + `json.dump`: truncate, then write in
  place, with no temp file and no `fsync`. A crash mid-write leaves a
  truncated file. **Verified** consequence: a truncated `settings.json`
  loads as `{}`, so every setting falls back to its default and
  Recent/Favorites come back empty. The **next write of any setting
  replaces the file with only that key** (after `set_theme` the file was
  just `{"theme": "sepia"}`). The bad file is not kept.
- **No lock, and every setter is load → modify → save.** Bridge calls run
  on separate threads (see 3). **Verified:** two threads, one calling
  `set_document_position` (what page turns do, `api.py:269-277`) and one
  calling `set_outline_width`, run for 3 s against 20 Recent entries and 1
  Favorite. All three runs ended with **0 Recent entries and 0
  Favorites**, and neither thread raised. One writer reads the file while
  the other has just truncated it, gets `{}`, and saves that. Real-world
  frequency is unmeasured: the window is small per write, but strip-view
  scrolling makes a burst of position writes (`api.py:57-61`).
- No `schema_version`. `ocr_cache.py:66-71` already writes atomically
  (temp file + `os.replace`), so H4 can follow that pattern.

### 5. Opening PDFs (`document.py:102-121`, `api.py:232-250`)

**Verified**, via `PdfDocument.open` and `Api.open_pdf`:

| Input | `fitz.open` | `Api.open_pdf` | Added to Recent? | User sees |
|---|---|---|---|---|
| zero-byte | `EmptyFileError` | raises | no | toast "Couldn't open that PDF. It may be damaged or no longer there." |
| not a PDF | `FileDataError` | raises | no | same toast |
| truncated | opens (silent repair) | OK | yes | the document (but see 1d: overwrite-save fails) |
| password-protected | **opens**; then any page access raises `document closed or encrypted` | raises from `_state()` (`api.py:250`→`292`) **after** `add_recent_file` (`api.py:246`) | **yes** | same "damaged or no longer there" toast (wrong reason) |
| path gone (Recent entry deleted) | `FileNotFoundError` | raises | no (already listed) | same toast; no offer to remove it from Recent |

The toast comes from `home.js:12-23`, `settings.js:51-62` and
`reading.js:1832-1844`. All three catch the rejected call. It is plain
language, but it is the same message for every cause.

**Bug, verified:** `PdfDocument.open` closes the current document
*before* calling `fitz.open` (`document.py:103-105`). When the open fails,
`self._doc` still points at the **closed** document, so `is_open` stays
True, `path` is the old file, and `page_count`, `highlight_counts` and
every render raise `ValueError: document closed`. The page that can reach
this is Reading, with a file dropped on it: the toast appears and the
reader is left on a page whose document is gone. Unsaved highlights are
not lost, because `promptSaveIfDirty` already ran. For an encrypted file
the `Api` is also left with that file's `_title`/`_path`
(`api.py:239-240`).

### 6. Untrusted content

**None found.** Nothing in `src/lector` reads links, URIs, launch actions,
embedded files, form widgets or JavaScript from a PDF (no `get_links`,
`LINK_*`, `uri`, `embfile`, `widgets`). Pages reach the web view only as
PNG bitmaps. PDF-supplied strings (outline titles, metadata titles,
highlight text, file names) are set with `textContent` or attributes
(`outline-panel.js:241-256`, `recent-card.js:67`,
`recent-list.js:140-145`, `reading.js:65`, `highlight-colors.js:214-325`).
`innerHTML` is only cleared, or given local icon SVGs (`icons.js:54`) and
static markup (`command-reference.js:65`). Python→JS payloads go through
`json.dumps` (`api.py:1010,1071`). There is no Content-Security-Policy;
nothing needs one today.

### 7. Network

**Confirmed: localhost only.**
- `shared/frontend_server.py`: `socket`/`socketserver`/`http.server`,
  `HOST = "127.0.0.1"` (`:42`), port 0, `SO_EXCLUSIVEADDRUSE`
  (`:131-137`).
- `shared/single_instance.py`: `multiprocessing.connection`
  `Listener`/`Client` over a named pipe on Windows and a Unix socket
  elsewhere (`:25,30`). Not a network socket.
- Frontend: the one `fetch` is for local icon files (`icons.js:26`).
  Navigation only goes to the app's own pages.
- No `urllib`, `http.client`, `requests`, `httpx`, `aiohttp`, `ftplib`,
  `smtplib` or `webbrowser` anywhere in `src/`.

### 8. PyMuPDF spread

**Confirmed: exactly five modules in `src/`** import `fitz`. Lines that
reference `fitz.`: `annotations/highlighter.py` 23, `reading/ocr.py` 20,
`reading/document.py` 7, `home/recent.py` 5, `reading/search.py` 4. (Other
call sites go through objects, e.g. `page.get_pixmap`.) Outside `src/`:
`scripts/build_app_icon.py` and 10 test files. H7's guard test must scan
`src/` only. **Note:** on PyMuPDF 1.28.2, `import fitz` prints
`The fitz API is deprecated and will be removed in future`. That makes the
rename to `pymupdf` more than cosmetic once dependencies are upgraded; it
belongs with pinning versions.

### 9. Tests today

- Save: `test_api.py:340-374`. These cover dispatch only, with a mocked
  `PdfDocument`, and `:367-374` pins the raw-text behaviour.
  `test_highlight_colors.py:185-190` (copy round trip),
  `test_ocr.py:125-140` (page key survives copy and `saveIncr`),
  `test_ocr.py:353-372` (copy keeps the OCR cache).
  **Untested:** any failed or interrupted save on a real file, an existing
  destination, read-only/locked files, overwriting a repaired file,
  copying onto the open file, "original unchanged" hashes.
- Errors and logging: **nothing** asserts that users get no raw text, and
  nothing covers logging or exception hooks.
- Settings: `test_settings_store.py:80` (a hand-edited value falls back),
  `:88` (missing file), `test_favorites.py:74` (malformed items).
  **Untested:** truncated/invalid JSON, crash mid-write, concurrent
  writers.
- Opening: `test_file_drop.py:79,214` (missing file at drop or launch
  time, reported before `open_pdf`), `test_home_thumbnails.py:92`
  (unreadable file has no thumbnail), `test_api.py:215-272` (per-document
  state on reopen). **Untested:** `open_pdf`/`PdfDocument.open` with
  zero-byte, malformed, encrypted or missing files, and the state left
  after a failed open.
- Untrusted content, network, PyMuPDF spread: **no tests.** The port and
  single-instance behaviour have tests (`test_frontend_server.py:177,184`,
  `test_single_instance.py`), but nothing checks that `src/` has no other
  network use.

### Prioritized list

**Could lose or corrupt user data**
1. **"Save a copy" silently replaces an existing file** (1b). The Save-As
   screen even fills in an existing name when its row is clicked, and
   MuPDF deletes the target before writing. High. H2.
2. **Concurrent settings writes wipe Recent, Favorites and reading
   positions** (4). Reproduced 3/3 under stress; real-world frequency
   unmeasured. A crash mid-write resets everything, and the next write
   makes that permanent. High. H4.
3. **Overwrite has no backup and no check** (1a). Killing mid-append kept
   the original in every simulation, so the realistic loss is that save's
   highlights. Power loss and macOS are untested. Medium. H2.
4. **macOS: copying onto the original under another spelling of its
   path** may unlink it before writing (1b). Unverified. Medium. H2.

**Could crash or leave the app broken**
5. **A failed open leaves a closed document behind** (5). Every later call
   on Reading raises. High. H5.
6. **Repaired PDFs can never be overwrite-saved** (1d). The user gets a raw
   MuPDF message, and this is common with real files. High for H2 (the
   temp-file full save design fixes it as a side effect).
7. **Password-protected PDFs** end up in Recent, leave the `Api` pointing
   at them, and get the wrong message (5). Medium. H5.
8. **Raw exception text in the UI** (2): save alert, voice box, mic pill.
   Medium. H3.
9. **No log file or exception hooks** (3). Errors go to stderr or nowhere,
   and several failures are swallowed with `pass`. Medium. H3.
10. **No lock around the shared `PdfDocument`**, although every bridge call
    is its own thread. PyMuPDF holds the GIL per call (`ocr.py:14-16`), so
    two threads inside MuPDF at once is unlikely. Python-level interleaving
    is unguarded, for example `open()` closing the document while a render
    is in flight. Not reproduced. Low–medium.
11. Copy naming mismatch in Settings (1c). Low. H2.
12. `import fitz` is deprecated in PyMuPDF 1.28.2 (8). Low. H7 optional /
    pinning.

**Fine as is**
- Untrusted PDF content (6): nothing is followed, executed or injected.
- Network (7): localhost server and local pipe only.
- PyMuPDF spread (8): the five documented modules.
- Zero-byte, malformed and missing files fail before Recent is touched,
  with a plain toast (5).
- Failed saves keep highlights in memory and leave the original intact in
  every case that could be tested (1d).
- The OCR worker is contained: exceptions are logged, crashes and hangs
  time out and the worker is replaced (3).
- The OCR cache already writes atomically (4).

**Not verified in this audit:** anything on macOS; a real full disk; a
network drive disappearing; power loss; concurrent use of `PdfDocument`;
how often the settings race happens in real use; whether pywebview's
traceback logging can carry document text.

---

## TASK LOG (Claude Code adds one entry per task: date, branch, plan summary, decisions, files changed, tests before/after, unverified items, screenshots, commit hash or "uncommitted"; the reviewer adds a Review block under it)

### H2 — Save safety (2026-10-23)

- **Branch:** `hardening/h2-save-safety`, from `master` @ `551cb44`. **Uncommitted**: diff the working tree. The branch also carries an unrelated chore, already staged: `.gitignore` additions (Claude Code local files) and six `.pyc` files removed from the index (`git rm --cached`). The developer chose to carry it here.
- **Plan (approved in plan mode):**
  - Both save kinds go through the new `features/reading/safe_write.py` (no fitz): temp file in the destination's folder → fsync → verify (opens, no password, same page count, same per-page highlight counts) → copy permission bits → `os.replace` (5 × 100 ms retry on Windows sharing violations) → fsync the folder (POSIX). The temp file is always removed.
  - Overwrite drops `saveIncr()`: a pre-flight `check_writable`, then a full save, then close the live doc → replace → reopen. Page, zoom, OCR state and undo/redo carry over. If the replace fails after the close, the doc is reopened from the temp file's bytes.
  - Signed PDFs (`get_sigflags() > 0`) are refused for overwrite.
  - A copy onto the open file under any spelling is refused. An existing destination is replaced only after a new "Replace “name”?" confirmation (`replace-dialog.js`, voice scope `replace_confirm`).
  - `perform_save` returns plain sentences (`safe_write.MESSAGES`), never `str(exc)`. The suggested name skips taken names.
  - Settings subtitle fixed to `(highlighted)`.
- **Developer decisions:**
  - Overwrite backup: delete (no backup file, no setting).
  - Name clash on copy: ask before replacing.
  - Signed PDFs: refuse overwrite, suggest a copy.
  - The staged chore rides on this branch.
- **Probe before coding** (scratchpad, PyMuPDF 1.28.2): annotation xrefs are identical after `save(tmp, deflate=True)` (no garbage) + `os.replace` + reopen, including after a delete frees an xref. A truncated (repaired) file saves fully. `xref_set_key(catalog, "AcroForm", "<</SigFlags 3>>")` makes `get_sigflags()` return 3.
- **Files changed:**
  - New: `src/lector/features/reading/safe_write.py`, `frontend/pages/replace-dialog.js`, `tests/test_save_safety.py`, `tests/test_replace_dialog_behavior.py`.
  - Modified: `src/lector/features/reading/document.py` (`save_overwrite`/`save_copy` rewritten, `_write_full`, `_verify_saved`, `_swap_into_place`, `_reopen_from_memory`, `_forget_history_if_stale`, `is_open_document`, `_save_lock`), `src/lector/api.py` (`perform_save`, new `describe_save_target`, `_suggested_copy_path`), `src/lector/features/voice/router.py` (`REPLACE_CONFIRM`, `CONFIRM_REPLACE`, phrases, no globals), `src/lector/features/voice/reference.py` (prompt listed), `frontend/pages/reading.html` (dialog markup + script), `frontend/pages/reading.js` (`pickCopyPath`, command routing, alert text), `frontend/pages/settings.html` (subtitle).
  - Tests: `tests/test_api.py` (the raw-text assertion H1 flagged is replaced on purpose by a plain-message one, plus one more), `tests/test_voice_global_commands.py` (`ReplaceConfirmScopeTests`).
  - Docs: `docs/ARCHITECTURE.md` (new "Saving: all or nothing" section), `docs/DESIGN_SYSTEM.md` (Replace confirmation, save failure message, `.dialog-scrim--enter` list), `docs/PRD.md` (save-behaviour bullet), `CHANGELOG.md` (six `[Unreleased]` → Fixed entries).
- **Tests:**
  - Before: `python -m pytest -q`, 1101 passed, 2360 subtests passed.
  - After: 1134 passed, 2445 subtests passed, 0 failed.
  - New tests cover:
    - copy and overwrite round trips;
    - undo/redo after overwrite;
    - a repaired PDF overwritten;
    - a half-written failure in both modes (sha256 of the original and of the existing destination unchanged, no temp file left, doc still open and dirty);
    - a verify mismatch;
    - a replace failing after the close;
    - a sharing violation waited out;
    - signed refusal;
    - copy onto the open file via `sub/..`;
    - real read-only original;
    - read-only destination;
    - missing folder;
    - `classify` for every reason;
    - each reason through `Api.perform_save`;
    - `describe_save_target`;
    - suggestion numbering;
    - the replace dialog under Node (8 scenarios).
- **Screenshots** (running app, isolated `APPDATA` profile, light theme, WebView2 DevTools port; files in the session scratchpad, not committed):
  1. Replace confirmation on Reading, reached through the real flow: highlight → Save a copy → Save-As suggested `report (highlighted) (2).pdf` → typed the taken `report (highlighted).pdf` → Save. Compared with DESIGN_SYSTEM.md (overwrite-confirmation pattern): same `.dialog-card`, folder badge, serif title `Replace “report (highlighted).pdf”?`, description, footer hint, Cancel secondary / Replace primary, dim scrim, focus on the card. Matches. Cancel returned to Save-As with the same name, and cancelling that left the document dirty and no file written.
  2. Read-only overwrite failure: the native alert reads "Couldn't save: the file is read-only. Your original PDF is unchanged and your highlights are still open." sha256 of the PDF unchanged, no `.lector-save-*` file.
  3. Settings → "When I save highlights": `Writes "name (highlighted).pdf" next to the original.`
  
  Dark and sepia themes were **not** screenshotted. The dialog uses only existing theme tokens, but that is unverified by eye.
- **Not verified:** anything on macOS (`check_writable` as the read-only guard, the folder fsync, `same_file` on a case-insensitive APFS volume); a real full disk; a real network share disappearing; a real power loss; a real second program locking the file (simulated with `winerror` 32 in tests).
- **Known limits:**
  - A hard kill between the temp write and the replace leaves a `.lector-save-*.tmp` beside the PDF (the original is intact; no cleanup).
  - An overwrite makes a new file at the same path, so creation time, extended attributes/ADS and hard links are not kept (permission bits are).
  - A render request landing in the few ms between close and reopen fails once (no document-wide lock; H1 item 10, out of scope).
  - A long folder path wraps over several lines in the Replace description (seen with the scratchpad path; ordinary paths are shorter).
  - `api.py` was 1146 lines before this change and is 1178 now, over the 800-line soft ceiling. That predates H2 and wasn't split here (scope).

#### Review (2026-10-23, fresh context)

Diffed the working tree against `master` @ `551cb44` (14 modified files, 4 new; the `.gitignore`/`.pyc` chore is staged and outside H2, as the entry says). Read the diff itself. Suite: `python -m pytest -q` → **1134 passed, 2445 subtests passed, 0 failed** (143 s).

**Checklist**
- Can any failure path change or lose the original PDF? **PASS.** Every failure before the move leaves the destination untouched: temp file in the same folder, fsync, verify, then `os.replace` (`safe_write.py:104-116`). The temp file is removed in `finally` (`:122-123`). A failed move after the close reopens from the temp file's bytes (`document.py:650-656`). The sha256 assertions cover the half-written copy and overwrite, the verify mismatch, a failed move, read-only, signed, and copy-onto-self (`test_save_safety.py:202-331`). *But see Fix 1:* the two failures that can happen **after** a successful move do change the file (into the verified new one) while reporting that it is unchanged.
- New `fitz`/`pymupdf` import outside the five? **PASS.** `safe_write.py` has none (docstring `:10`). Grep of `src/lector` still finds only the five modules.
- New network use? **PASS.** None. Grep for urllib/http.client/requests/httpx/aiohttp/ftplib/smtplib/webbrowser/socket outside `frontend_server.py` found nothing.
- Raw exception text in the UI, or content in logs? **PASS.** `api.py:676-680` returns `SaveError.message` or the fixed UNKNOWN sentence. `reading.js:1304` alerts only that. The one new log call (`api.py:679`, `log.exception`) carries the mode and a traceback, no document text. The traceback can include MuPDF's message with a full path, which is for H3 to vet.
- UI thread blocked / PyMuPDF from several threads? **PASS, with the known limit.** `_save_lock` serialises saves (`document.py:104`). A render or `begin_ocr` (`ocr.single_page_pdf(self._doc…)`) that lands in the ms between close and reopen fails once, as the entry already says.
- Windows AND Mac? **FAIL (macOS only), see Fix 1a.** `_fsync_folder` (`safe_write.py:219`) calls `os.open(folder, O_RDONLY)` outside its try, although its docstring (`:214-216`) says a refusal is not a failure. A writable but unreadable folder (e.g. macOS `~/Public/Drop Box`, mode `-wx`) raises **after** the copy is in place: the reader is told "isn't allowed to write to that folder… unchanged", the copy exists, and `is_dirty` stays True. Windows returns early (`:217-218`) and is fine. Nothing was run on a Mac.
- Tests added, suite passing? **PASS.** `test_save_safety.py` (32 tests), `test_replace_dialog_behavior.py` (8, Node), `test_api.py` (2), `test_voice_global_commands.py` (`ReplaceConfirmScopeTests`). Full suite green.
- CHANGELOG / docs / screenshots / nothing committed? **PASS.** `CHANGELOG.md` has six `[Unreleased]` → Fixed entries. ARCHITECTURE "Saving: all or nothing", DESIGN_SYSTEM (Replace confirmation, failure message, `.dialog-scrim--enter` list) and the PRD save bullet are updated. Light-theme screenshots were recorded in the entry; dark and sepia are marked unverified, as the rules allow. `git log` still ends at `551cb44`.

**Spec requirements**
- Temp file in the same folder → fsync → reopen and check pages and highlights → replace: **met** (`safe_write.py:102-117`, `document.py:625-642`; also checks `needs_pass`).
- Windows refuses to replace an open file; close and reopen around it, keeping page, zoom, layout, OCR state and undo/redo: **met** (`document.py:644-659`; state lives on `PdfDocument`, xrefs kept without `garbage=`, `_forget_history_if_stale` guard; tests `:146-177`). Layout is not on `PdfDocument`, so it is untouched by the reopen.
- No `garbage=` on either path: **met** (`document.py:618-623`).
- Copy never silently overwrites; naming mismatch resolved: **met.** `pickCopyPath` + `describe_save_target` + `replace-dialog.js` (`reading.js:1314-1327`, `api.py:682-693`). The suggestion skips taken names (`api.py:695-705`). Settings now says `(highlighted)` (`settings.html:259`). The dialog is guarded against reader shortcuts by `reading.js:1408` and stays voice-reachable via `.voice-dialog` (`voice.js:81`).
- Backup policy asked, no new setting: **met** (developer chose delete; no setting added).
- Mac vs Windows stated: **met** in ARCHITECTURE and the entry, but see Fix 1a for the Mac-only path that misreports.
- Plain-language message for read-only, no permission, locked, disk full, folder missing, drive gone: **met** for every failure before the move (`safe_write.py:40-55, 168-186`; `test_save_safety.py:348-412`). **Not met** for a failure after the move (Fix 1).
- Tests: copy round trip, overwrite round trip, forced mid-save failure with hashes and no temp files, each failure via `perform_save`, existing destination not clobbered: **met** (`test_save_safety.py:134-224, 397-424, 317-328`).

**Fix list (status → needs-fixes)**
1. A failure **after** `os.replace` has succeeded must not be reported as "Couldn't save… unchanged", and must not leave the reader holding a closed document:
   a. `safe_write._fsync_folder` (`safe_write.py:213-225`): make the whole thing best-effort, including the `os.open`, as its docstring already says.
   b. `PdfDocument._swap_into_place` (`document.py:657-659`): if `fitz.open(dest)` raises after a successful replace, the save has happened. Don't propagate it as a SaveError with the "unchanged" message, and don't leave `self._doc` as the closed document. Retry the open briefly; failing that, fall back to opening the bytes now at `dest`. The save then counts as done (`is_dirty` False). Only if even that fails, a distinct message that says the file **was** saved and asks the reader to reopen it.
   c. Tests: patch `safe_write.os.open` to raise with `safe_write.os.name` forced to `"posix"` (copy and overwrite both report success, the file on disk has the highlights). Patch `fitz.open` to fail once after the replace in an overwrite (no SaveError, document still open, `render_page(0)` works, highlights on disk).

**Advisory (not blocking)**
- `safe_write.replace` (`:126-140`) also retries on WinError 5, which a truly read-only destination raises too, so that failure is reported about 0.4 s later. Harmless.
- `_reopen_from_memory`'s last-resort fallback (`document.py:667-672`) clears undo/redo and sets `is_dirty` False, so the unsaved highlights are gone while the message says they are still open. It is reachable only if a file that just passed verification cannot be read back. Worth a distinct message when Fix 1 is done.
- `api.py` is 1178 lines (pre-existing, over the 800-line soft ceiling).

**What this review did not cover:** anything on macOS (all Mac claims are from reading the code); a real full disk, a vanishing network share, power loss, or a real second program holding the file; dark and sepia rendering of the Replace dialog; and concurrent bridge calls under load. This is the same model reading the code again with fresh context. It catches real mistakes, but it is not proof that the code is correct.

#### Fixes (2026-10-23, work session after the review)

- **Branch:** same, still **uncommitted**: diff the working tree against `master` @ `551cb44`. Plan approved in plan mode; the developer also asked for advisory item 2 ("last-resort reload says highlights are still open") to be fixed.
- **What changed:**
  - Fix 1a: `safe_write._fsync_folder` is fully best-effort; the `os.open` of the folder is inside the try.
  - Fix 1b: in `PdfDocument._swap_into_place`, once `os.replace` has succeeded:
    - `is_dirty` is cleared at once;
    - the reopen goes through the new `safe_write.retry_briefly` (5 tries, 100 ms apart), then falls back to the bytes now at `dest` (`_reopen_saved`);
    - if even that fails: `_doc = None` (not the closed document), a `log.exception` (no content), and `SaveError(SAVED_NOT_REOPENED)`;
    - `_forget_history_if_stale` is guarded, and clears history if it raises.
  - Advisory 2: `_reopen_from_memory` now returns whether the highlights were kept. When only the original reopens: `HIGHLIGHTS_LOST`. When not even the original opens: `_doc = None` and `UNSAVED_NOT_REOPENED`. Before this, that last case left a closed document behind (found by the code-reviewer agent).
  - `except BaseException` in the replace branch only turns an `Exception` into a SaveError. A KeyboardInterrupt is re-raised as is.
  - `SaveError.closes_document` (reasons `SAVED_NOT_REOPENED`, `UNSAVED_NOT_REOPENED`). `Api.perform_save` adds `closed: true` for these.
  - `reading.js` `promptSaveIfDirty`: after the alert, `outcome.closed` → go to `../index.html`.
  - Three new messages in `safe_write.MESSAGES`, each saying what did happen instead of "unchanged… still open".
- **Files changed:**
  - Code: `src/lector/features/reading/safe_write.py`, `src/lector/features/reading/document.py` (module `log`, `_swap_into_place`, `_reopen_saved`, `_reopen_from_memory`, `_clear_history`), `src/lector/api.py` (`perform_save`), `frontend/pages/reading.js`.
  - Tests: `tests/test_save_safety.py`, `tests/test_api.py`.
  - Docs: `docs/ARCHITECTURE.md` (Overwrite bullet), `docs/DESIGN_SYSTEM.md` (Save failure message), `CHANGELOG.md` (one sentence added to the first H2 entry).
- **Tests:** before 1134 passed, 2445 subtests. After **1142 passed, 2448 subtests, 0 failed**. New:
  - `AfterTheMoveTests`:
    - folder fsync refused, with `os.name` forced to `"posix"` and `os.open` refused for folders only (asserts it fired twice);
    - reopen fails once;
    - reopen by path always fails, so it reopens from bytes and saves again;
    - path and bytes both fail, giving `SAVED_NOT_REOPENED` with `is_open` False.
  - In `InterruptedSaveTests`: `HIGHLIGHTS_LOST`, `UNSAVED_NOT_REOPENED`, and a KeyboardInterrupt not being wrapped.
  - `test_api.py`: `closed: True`.
  - `test_every_message_says…` now allows the three exceptions.
- **Code review** (ecc:python-reviewer agent, before finishing):
  - The HIGH finding (closed document if even the original won't reopen) and three MEDIUM findings (is_dirty after the move, the guarded history check, KeyboardInterrupt wrapping) are fixed above.
  - LOW, fixed: `retry_briefly` ends in an explicit return.
  - Not acted on:
    - a save after `_doc` became None would report UNKNOWN; Reading leaves right after the alert, so it isn't reachable from the UI;
    - a KeyboardInterrupt during the reopen; bridge threads never receive one;
    - render calls landing in the close→reopen window; pre-existing, H1 item 10.
- **In-app check** (real app, isolated `APPDATA`, **hidden** window, `_reopen_saved` patched to fail, from a scratch launcher outside the repo): highlight → `promptSaveIfDirty` with Overwrite.
  - The alert text was recorded by replacing `window.alert`: "Your highlights were saved, but Lector couldn't open the PDF again afterwards. Open it from Home to keep reading."
  - Reading then went to `/index.html`.
  - The PDF on disk had the highlight, and no temp file was left.
- **Screenshots: not taken; unverified.** The new text appears in the existing native `alert` (no new visual element). A first attempt captured the screen region of a window that wasn't in front, so it got the developer's other windows. Those images were deleted at once, without being kept or shared. That attempt also sent one Enter keypress to whatever window was in front. No screen capture or synthetic input was used after that.
- **Not verified:** anything on macOS (1a is simulated); a real scanner holding the file after the move; how the native alert looks with the new, longer text.
- **Known limits (unchanged):** see the H2 entry above. Also, `log.exception` tracebacks can carry MuPDF's message, including a path; H3 vets logging.

#### Re-review (2026-10-24, fresh context)

Diffed the working tree against `master` @ `551cb44` (14 modified files, 4 new; the staged `.gitignore`/`.pyc` chore is outside H2, as recorded). Read the diff itself, not this log's description of it. Suite: `python -m pytest -q` → **1142 passed, 2448 subtests passed, 0 failed** (187 s). `git log` still ends at `551cb44`.

**Fix list from the first review**
- 1a, folder fsync fully best-effort: **met.** `os.open(folder)` is inside its own try and returns on `OSError` (`safe_write.py:252-256`); `fsync` is wrapped too (`:257-262`). Test: `test_a_folder_that_cannot_be_opened_to_flush_it_does_not_fail_a_save` (`test_save_safety.py:370-393`), copy and overwrite both succeed with `os.name` forced to `"posix"`, and the refusal fired twice.
- 1b, nothing after a successful move reported as "unchanged", and no closed document left behind: **met.** In `_swap_into_place`, `_dirty = False` is set right after `replace` (`document.py`, after the "save has happened" comment). Reopen goes `retry_briefly(fitz.open(dest))` → bytes (`_reopen_saved`). If both fail: `_doc = None`, `log.exception` (no content), `SaveError(SAVED_NOT_REOPENED)`, whose message says the highlights *were* saved. `_forget_history_if_stale` is guarded and clears history on error. `write_atomically` re-raises a `SaveError` as is (`safe_write.py:139-140`), so it is not reclassified. The leftover temp file is already gone (`_discard` ignores `FileNotFoundError`).
- 1c, tests: **met.** `AfterTheMoveTests` (`test_save_safety.py:336-428`): retried once, falls back on bytes and saves again, and path and bytes both fail (`SAVED_NOT_REOPENED`, `is_open` False, `is_dirty` False, highlights on disk, no temp file).
- Advisory 2 (last-resort reload claimed highlights were open): **fixed.** `_reopen_from_memory` returns whether the highlights were kept. Otherwise `HIGHLIGHTS_LOST`, or `UNSAVED_NOT_REOPENED` with `_doc = None`. Neither message says "still open" (`safe_write.py:64-67`). Tests at `test_save_safety.py:274-334`, including a KeyboardInterrupt that is not wrapped.
- `perform_save` → `closed: true` for `CLOSES_DOCUMENT` (`api.py`, `exc.closes_document`). `reading.js` alerts, then goes to `../index.html`. **Met.** Test in `test_api.py`.
- A reopen from bytes leaves `fitz.Document.name` empty. Checked that nothing relies on it: `grep` finds no `doc.name` use in `src/lector`, saves use `PdfDocument._path`, and OCR cache keys hash page content (`ocr.py:107-120`), not the file name.

**Checklist**
- Can any failure path change or lose the original PDF? **PASS.** Before the move the destination is untouched (temp file → fsync → verify → `os.replace`, `safe_write.py:124-144`), and the sha256 tests hold. After the move the file *is* the verified new one, and every message there now says so.
- New `fitz`/`pymupdf` import outside the five? **PASS.** `grep` of `src/lector` finds exactly highlighter, recent, document, ocr, search.
- New network use? **PASS.** None outside `frontend_server.py`.
- Raw exception text or traceback in the UI; content in logs? **PASS.** `perform_save` returns only `MESSAGES[...]`. The new `log.exception` calls carry fixed text plus a traceback, which can include MuPDF's message with a path (left for H3, as logged).
- UI thread blocked / PyMuPDF from several threads? **PASS, with the known limit.** `_save_lock` serialises saves. A render landing between close and reopen, or one in flight after `SAVED_NOT_REOPENED` sets `_doc = None` and before Reading leaves, fails once and is rejected to JS (H1 item 10, pre-existing).
- Windows AND Mac? **PASS by reading the code.** The Mac folder-fsync path is fixed and simulated. Nothing was run on a Mac.
- Tests added, suite passing? **PASS.** 1142 passed, 0 failed.
- CHANGELOG / docs / screenshots / nothing committed? **PASS.** The CHANGELOG first H2 entry has the "saved but couldn't reopen" sentence. The ARCHITECTURE Overwrite bullet and the DESIGN_SYSTEM "Save failure message" describe the three rare cases as coded. The new text appears only in the existing native `alert`; that it was not screenshotted is recorded as unverified, which the rules allow. Nothing committed.

**Spec requirements:** all met as in the first review. The one that was not met there, a plain-language and *truthful* message for every failure including after the move, is now met.

**Advisory (not blocking)**
- `AfterTheMoveTests` patches `safe_write.os.name`/`safe_write.os.open`, which is the global `os` module, so everything in the process sees `"posix"` for the duration of the `with`. It passes on Windows today. If it ever turns flaky, inject the platform check instead.
- Still open from the first review: `replace` retries WinError 5 (≈0.4 s delay on a truly read-only file), and `api.py` is over the 800-line soft ceiling (pre-existing).

**What this re-review did not cover:** anything run on macOS; a real full disk, a vanishing share, power loss, or a real second program holding the file; dark and sepia rendering of the Replace dialog; the native alert with the longer messages, by eye; concurrent bridge calls under load. This is the same model reading the code again with fresh context. It catches real mistakes, but it is not proof that the code is correct.

**Status → reviewed.** Next: H3 (errors and logging), plan mode.

**Committed 2026-10-24** to `master` as `1a6497f` ("fix(save): make saves atomic and confirm before replacing a file"); the branch was merged and deleted.

### H3 — Errors and logging (2026-10-24)

- **Branch:** `hardening/h3-errors-logging`, from `master` @ `1a6497f`. **Uncommitted**: diff the working tree. New, untracked files (include them in the diff):
  - `src/lector/shared/app_log.py`
  - `frontend/js/background-error.js`
  - `tests/test_app_log.py`
  - `tests/test_log_privacy.py`
  - `tests/test_error_text_behavior.py`
- **Plan (approved in plan mode):**
  - A new `shared/app_log.py`:
    - writes a rotating `lector.log` (1 MB × 3) in `settings_dir()/logs`, set up in `__main__` after the single-instance lock;
    - `ContentFreeFormatter` writes exceptions as type + frames, never their message;
    - other libraries' records (pywebview, `py.warnings`, …) are cut to content-free text;
    - `sys.excepthook`, `threading.excepthook` and `sys.unraisablehook` log uncaught errors.
  - What the user sees:
    - a toast for a background-thread error (once per page);
    - a Windows `MessageBoxW` for a fatal one.
  - Raw text replaced by fixed sentences or codes:
    - voice engine and VAD load errors;
    - microphone open;
    - `request_microphone`;
    - `list_directory`;
    - `voice.js` `String(err)`;
    - Home/Settings "couldn't start" `${err}`;
    - command reference `${err}`;
    - onboarding.
  - Silent `pass`/`continue` sites now log:
    - `_emit` (no payload);
    - stream teardown;
    - first bad chunk per session;
    - `evaluate_js` failures at DEBUG;
    - `pool.terminate`.
  - OCR worker:
    - a Pool initializer enables `faulthandler` into `logs/ocr-worker.log`;
    - the parent's timeout line points at that file.
- **Developer decisions** (all four plan defaults accepted):
  1. Exception messages stripped from the log (type and frames kept).
  2. A toast for background-thread errors, once per page.
  3. A Windows message box on a fatal error; macOS logs only.
  4. The voice wording as proposed: "No speech model installed.", "The speech model couldn't be loaded.", "Couldn't open the microphone. Check that one is connected and that Lector may use it.", "Voice couldn't start.", "Voice commands couldn't start on this screen.", "Couldn't load the command list."

  Flagged as out of scope, not built: a "Show log folder" control.
- **Files changed:**
  - **New:** the five above.
  - **Code:**
    - `src/lector/__main__.py` (setup + hooks after the lock, `Api(log_dir)`)
    - `src/lector/api.py` (`Api(log_dir)`, `MICROPHONE_UNAVAILABLE`, `notify_background_error`, two DEBUG logs)
    - `features/voice/engine.py` (`MODEL_MISSING`/`MODEL_FAILED`/`MIC_FAILED`, logging, an unreadable model folder no longer raises)
    - `features/voice/audio_frontend.py`
    - `features/dialogs/browser.py` (`UNREADABLE`)
    - `features/reading/ocr.py` (`crash_log`, `_worker_init`, guarded `terminate`)
  - **Frontend:**
    - `js/voice.js`
    - `pages/home.js`, `settings.js`, `command-reference.js`, `onboarding.js`
    - `background-error.js` loaded by `index.html`, `pages/reading.html`, `settings.html`, `onboarding.html`
  - **Tests changed:**
    - `test_voice_engine.py`: the two status-text assertions changed on purpose to the new constants (the `fetch_vosk_model` hint is now asserted in the log), plus `VoiceErrorWordingTests`.
    - `test_single_instance.py`: `app_log` mocked in `MainTest`, plus 3 tests.
    - `test_api.py`: `ErrorReportingTests`.
    - `test_dialog_browser.py`: exact `UNREADABLE`.
  - **Docs:**
    - `docs/ARCHITECTURE.md`: new "Errors and logging (hardening H3)" section.
    - `docs/DESIGN_SYSTEM.md`: Voice unavailable text, Background error toast, Crash message.
    - `CHANGELOG.md`: one Added, one Fixed.
- **Tests:**
  - **Before:** clean `master` @ `1a6497f` in a scratch worktree: `python -m pytest -q` → 1119 passed, 23 skipped, 2444 subtests passed. The 23 skips are the tests that need the speech/VAD models in `assets/`, which are untracked and so absent from a fresh worktree; in the main checkout they run, which matches H2's 1142. The first run in the main checkout had 6 failures while other work was running; the worktree run stands in for it.
  - **After:** main checkout, final run: **1187 passed, 0 skipped, 2454 subtests passed, 0 failed.** That is 45 more tests than H2's 1142, and none removed. One intermediate full run had a single failure: `test_frontend_server.py::test_a_page_state_that_fails_leaves_the_pages_defaults`, a localhost socket `TimeoutError` while the machine was busy. It passed alone, in its module, alongside the new log tests, and in the next full run (1180 passed, 0 failed). It's unrelated to H3, but noted in case it recurs.
- **Code review** (ecc:python-reviewer agent). Its verdict was BLOCK. Fixed:
  - **HIGH:** pywebview logs exception objects (`logger.exception(e)`) and f-strings with `{e}`, which kept their first line. Every non-Lector record is now cut: an exception object becomes its type; other text keeps its first line up to the first ": ".
  - **HIGH:** the last line of a multi-line message could pass for the exception type, and a message line could pass for a frame. Only the *first* printed traceback block is trusted (header, strict frame lines, then an exception name that must look like a class). The same applies to the OCR worker's `RemoteTraceback`. A test proved the forged-frame leak before this fix.
  - **MEDIUM:**
    - `py.warnings` reduced to `file:line: Category`;
    - a rollover that fails on Windows keeps appending (`_RotatingFileHandler`);
    - `_worker_init` catches everything (a failing initializer makes Pool respawn forever);
    - the spawn test has a 60 s timeout.
  - **LOW:**
    - `formatException` prints the traceback it is given;
    - `ExceptionGroup` lists its sub-exceptions;
    - `sys.unraisablehook` is covered;
    - the folder name is no longer logged;
    - an unreadable model folder reports `MODEL_FAILED`, not "not installed";
    - the test handler cleanup is guarded.

  Not acted on:
  - `notify_background_error`'s `evaluate_js` can block the dying thread until the page answers (only that thread waits; it is a daemon in every case found).
  - Lector's own log calls that pass content as a pre-made string are the caller's job, which the docstring says.
- **In-app check** (scratch launcher outside the repo: **hidden** window, isolated `APPDATA`, own single-instance pipe, speech model path pointed at a missing folder; read through `evaluate_js`; screenshots through the WebView2 DevTools port, page only; no desktop capture, no synthetic input):
  - Home, light: two background-thread errors gave exactly **one** toast, "Something went wrong in the background. Lector kept running."
  - Reading:
    - the mic pill `title` was "No speech model installed."; label "Voice unavailable", hint "Mouse and keyboard work as usual";
    - one toast in the DOM.
  - Home, dark and sepia: voice box detail "No speech model installed.", and one toast each.
  - `lector.log` had every error as `RuntimeError (message withheld)` with frames, and the planted word appeared nowhere in it.
- **Screenshots** (in the session scratchpad, not committed): `home_toast_light`, `home_toast_dark`, `home_toast_sepia`, `reading_toast_light`. Compared against DESIGN_SYSTEM.md:
  - Voice box: "VOICE UNAVAILABLE" with the detail line "No speech model installed." Matches.
  - Toast: bottom-centre, inverted colours (light on dark, dark on sepia), the new sentence. Matches.
  - Reading mic pill: matches the existing unavailable state.
- **Not verified:**
  - **The Reading toast, by eye.** The hidden window reports `visibilityState: hidden`, so `showToast`'s `requestAnimationFrame` never adds `is-visible`, and the CSS fade never runs even when the class is added by hand. The toast was in the DOM, bottom-centre, its layer at z-index 250 and on top at its centre point (`elementFromPoint`). The light Home capture shows it mid-fade for the same reason. Reading uses the same `showToast` and CSS as Home.
  - **The native crash message box** (cannot be captured from a page; its text and flags are unit-tested).
  - **Anything on macOS**, including the `faulthandler` file and the log folder under Application Support.
  - **A real native crash** of the OCR worker (the crash log's creation is tested; a segfault was not provoked).
  - **A real rollover** blocked by another program (simulated).
- **Known limits:**
  - Failures before `app_log.setup` (the single-instance claim, a broken install) still only reach stderr.
  - `ocr-worker.log` is appended to and never rotated (it only grows on native crashes).
  - A log line says `RuntimeError (message withheld)`, so MuPDF's own wording is no longer available when debugging.
  - Other libraries' warnings keep only where they came from and their category.
  - `api.py` is now 1210 lines (pre-existing, over the 800-line soft ceiling).

#### Review (2026-10-24, fresh context)

Diffed the working tree against `master` @ `1a6497f`: 22 modified files (+337/−49) and the five untracked files, read in full (`app_log.py`, `background-error.js`, the three new test files). Read the diff itself, not this log's description of it. Suite: `python -m pytest -q` → **1187 passed, 2454 subtests passed, 0 failed** (91 s). `git log` still ends at `1a6497f`.

**Checklist**
- Can any failure path change or lose the original PDF? **PASS.** H3 does not touch the save path. `perform_save` only had its message source checked (`api.py:669-694`); `document.py`/`safe_write.py` are unchanged in this diff.
- New `fitz`/`pymupdf` import outside the five? **PASS.** `grep` of `src/lector` finds exactly highlighter, recent, document, ocr, search. `app_log.py` has none. `test_log_privacy.py` imports `fitz`, but tests are outside the guard's scope.
- New network use? **PASS.** None. `notify_background_error` uses the existing `evaluate_js`; `MessageBoxW` is a local `ctypes` call.
- Raw exception text or traceback in the UI; content in logs? **PASS.**
  - UI: `grep` of `src/lector` finds no `str(exc)`/`{exc}` reaching a return value (the one `str(exc)` left, `ocr.py:458`, only classifies). `grep` of `frontend/` finds no `String(err)`/`${err}`. Each remaining `alert`/`showToast(error)` shows a fixed sentence or code (`safe_write.MESSAGES`, `file_drop.py`, `set_favorite`/`show_in_folder`).
  - Engine `status()["error"]` is one of `engine.py:52-54`. `SileroVAD._load_error` ("No VAD model installed.") is never read outside its class, so it is not user-facing.
  - Logs: every `log.*` call in `src/lector` was read. Arguments are page indexes, hashes, model/tessdata paths, an event name and the constant window title (`window_chrome.WINDOW_TITLE`). Exceptions passed as arguments (`__main__.py:43`, `single_instance.py:100`) become their type (`app_log.py:146-158`). `lector.*` INFO now reaches the file; the only INFO call is `window_chrome.py:111` (an attribute number). DEBUG calls carrying `exc_info` (`browser.py:59`, `api.py:1066,1127,1141`) are dropped by `_keep_record`.
  - Tests: `test_log_privacy.py` plants words in the PDF, query, transcript and OCR text and runs real `Api` paths.
- UI thread blocked / PyMuPDF from several threads? **PASS.** No new PyMuPDF use. `notify_background_error` blocks only the thread that already died (`api.py:1129-1141`). `MessageBoxW` runs on the main thread only as it ends. Log writes are small appends.
- Windows AND Mac? **PASS by reading the code.** `show_fatal_message` returns early off `win32` (`app_log.py:306-307`), so `ctypes.windll` is never touched on a Mac. `faulthandler` and `settings_dir()` are cross-platform. Nothing was run on a Mac.
- Tests added, suite passing? **PASS.** `test_app_log.py`, `test_log_privacy.py`, `test_error_text_behavior.py` (Node), `ErrorReportingTests`, `VoiceErrorWordingTests`, 3 `MainTest` cases. The changed assertions in `test_voice_engine.py` and `test_dialog_browser.py` were tightened to exact constants, not loosened; nothing was deleted. Full suite green.
- CHANGELOG / docs / screenshots / nothing committed? **PASS.** `CHANGELOG.md` has one Added and one Fixed entry. ARCHITECTURE "Errors and logging (hardening H3)" and three DESIGN_SYSTEM entries match the code. The screenshots are still in the H3 session's scratchpad; I opened `home_toast_dark.png` (voice box "No speech model installed.", inverted bottom-centre toast: matches DESIGN_SYSTEM:137,440-441) and `reading_toast_late.png` (Reading toast drawn, mid-fade). The native message box is recorded as unverified, which the rules allow. Nothing committed.

**Spec requirements**
1. Raw `str(exc)` replaced by plain-language messages; unknown → generic + log: **met.** `perform_save` (`api.py:690-694`, from H2), `request_microphone` (`api.py:839-841`), engine (`engine.py:52-54, 240-247, 279-280, 450-451`), VAD (internal), `list_directory` (`browser.py:64`), and the frontend (`voice.js` `VOICE_CALL_FAILED`, `home.js`, `settings.js`, `command-reference.js`, `onboarding.js`). Wording is in DESIGN_SYSTEM "Voice unavailable text".
2. Rotating log in `logs` next to settings, small cap, a few backups, Windows and Mac: **met.** `__main__.py:72-73` → `app_log.setup` (`app_log.py:84-104`), 1 MB × 3, via `settings.settings_dir()`.
3. `sys.excepthook` + `threading.excepthook`, a short message for the user, OCR worker and voice threads in the log: **met.**
   - Hooks: `app_log.py:238-290`, plus `sys.unraisablehook` for PortAudio-callback errors.
   - What the user sees: a toast (`background-error.js`, loaded on all four pages) and a Windows message box (`app_log.py:302-313`). macOS logs only, per developer decision 3.
   - OCR: a worker exception's `RemoteTraceback` is logged as frames only (`app_log.py:185-189`); native crashes go to `faulthandler` (`ocr.py:348-362, 472`).
   - Voice: `engine.py:217, 663` and the thread hook.
4. No document text, OCR output, highlights or transcripts in logs, with a test: **met.** `test_log_privacy.py` covers highlight + find + failed save, a voice subscriber failure, an OCR worker failure, pywebview's three logging styles, warnings, an unraisable error and a dying thread.
5. Screenshot any UI error text, CHANGELOG, no commit: **met**, with the native box and macOS recorded as unverified.

**Advisory (not blocking)**
- Running from a terminal, Lector's own warnings and uncaught thread errors no longer print to the console. The root logger now has a handler, so `logging.lastResort` never fires, and `thread_hook` doesn't chain to the previous hook. This is documented in ARCHITECTURE ("the file is the place to look"), but it is a change for development.
- `_first_traceback_block` stops at the first frame whose file isn't `*.py` (`<frozen …>`, `<string>`), so later frames are dropped and the type reads `Exception`. That loses detail, never privacy.
- While a rollover keeps failing, every record retries it (close, rename fails, reopen). It's harmless at this log volume.
- The shared toast (bottom-centre, `components.css:661-665`) sits over Reading's footer hint ("Mouse and keyboard work as usual") in `reading_toast_late.png`. This predates H3: the OCR and Focus-mode toasts sit in the same place. Outside H3's scope.
- `api.py` is 1210 lines (pre-existing, over the 800-line soft ceiling).

**What this review did not cover:** anything run on macOS; a real native crash of the OCR worker; a real rollover blocked by another program; the native message box, by eye; dark and sepia Reading. pywebview's call sites were not re-read against the installed version: the review relies on the formatter cutting every non-Lector record and on the tests that imitate pywebview's styles. This is the same model reading the code again with fresh context. It catches real mistakes, but it is not proof that the code is correct.

**Status → reviewed.** Next: H4 (settings file safety), plan mode.

**Committed 2026-10-24** to `master` as `e932540` ("feat(logging): keep a content-free error log and stop showing raw error text"), fast-forwarded and pushed to `origin/master`; the branch was deleted. `docs/HARDENING.md` was left out of the commit, as before.

### H4 — Settings file safety (2026-10-25)

- **Branch:** `hardening/h4-settings-safety`, from `master` @ `e932540`. **Uncommitted**: diff the working tree. New, untracked file (include it in the diff): `tests/test_settings_file_safety.py`.
- **Plan (approved in plan mode):**
  - `_save` writes `settings.json.tmp` beside the file, flush + `fsync`, then `safe_write.replace` (H2's `os.replace` with retries on a Windows sharing violation); the temp file is removed in a `finally`.
  - A module `RLock`, taken by `_load`, held around each load-modify-save setter by a `@_serialized` decorator (16 setters, signatures unchanged via `functools.wraps`).
  - A file that is there but doesn't parse (`ValueError`, which includes `UnicodeDecodeError`, plus `RecursionError` and a non-object top level) is renamed to `settings.json.corrupt-YYYYmmdd-HHMMSS`; the 2 newest copies are kept; defaults load.
  - `SCHEMA_VERSION = 1` stamped on every save (a higher stored int is kept); no-op `_migrate` hook; no key names or defaults changed.
- **Developer decisions** (both plan defaults accepted, "go with defaults for both A and B"):
  - **A.** A file that is there but can't be *read*: retry briefly (only Windows sharing/lock violations, 5 × 0.1 s), then getters return defaults and setters skip their write and log it, never raise. The real file is left alone.
  - **B.** No UI for a reset; the log and the kept copy are the record.
- **Files changed:**
  - **Code:** `src/lector/features/settings/store.py` (only file).
  - **Tests:** new `tests/test_settings_file_safety.py` (30 tests). No existing test changed.
  - **Docs:** `docs/ARCHITECTURE.md` new "Settings file (hardening H4)" section; `CHANGELOG.md` one Fixed entry. `docs/TECH_STACK.md` unchanged (it only says "a local JSON settings file").
- **Tests:**
  - **Before:** this branch at `e932540`, main checkout: `python -m pytest -q` → 1187 passed, 2454 subtests passed, 0 failed.
  - **After:** 1217 passed, 2463 subtests passed, 0 failed. +30, none removed.
  - **Red check.** Against `master`'s `store.py` the new module had 26 failed / 6 passed; the 6 passing ones pin behaviour that was already right (unknown keys survive, a pre-H4 file loads, signatures). The deterministic lost-update test failed 5 of 5 runs with `store._lock` replaced by a no-op.
- **Code review** (ecc:python-reviewer agent): no CRITICAL. Fixed:
  - **HIGH:** the 1 s two-thread test only caught a missing lock by chance (failed 1 of 3 runs with the decorators removed). Added `LostUpdateTests`: `_save` slowed by 0.1 s, `add_favorite` and `add_recent_file` on two threads; without the lock one change is always lost. The soak test stays as a second check.
  - **MEDIUM:**
    - The write hold was an unscoped global: a getter that failed a read left it set for a bare `_save`, and a nested setter would have cleared the outer one's hold. Now `_write_depth` counts `_serialized` calls; only the outermost resets the hold, and `_hold()` does nothing outside a setter. Two tests.
    - A file that stays unreadable or stuck logged a warning on every read. Now `_note` logs a problem when it starts and once ("Resolved: …") when it clears. Three tests.
    - `_save` raised `OSError` on a failed write, so `open_pdf` (`add_recent_file`) and page turns (`_remember_position`) could fail with half-set state. A failed write is now logged and skipped, matching decision A's "a setting is never why a PDF fails to open". The crash-mid-write tests assert no raise and the old file intact.
  - **LOW:**
    - Two damaged files in the same second overwrote the first copy; now `-2`, `-3`, … (test).
    - `_serialized` typed with `ParamSpec`.
  - **Not acted on (recorded under ARCHITECTURE "Not covered"):**
    - Access denied (winerror 5) is not retried, since a real ACL denial would then cost 0.4 s on every getter.
    - While writes are held, a setter's return value can describe a change that was not kept (`add_favorite` → True). Changing return types would break the "signatures unchanged" requirement.
    - macOS `fsync` is not `F_FULLFSYNC`, and the folder is not fsynced: atomicity holds, the very last change may not survive a power cut.
    - A symlinked settings.json becomes a plain file.
    - Each save now fsyncs under the lock (about 15 ms here). Position writes already happen only when the page or layout changes (`api.py:68-72`); debouncing them would be a reader change, not H4.
  - **Question for the developer, not done:** `features/settings/store.py` now imports `features/reading/safe_write.py`, and `reading/ocr_cache.py` imports `settings.store`. That is no runtime cycle (`reading/__init__.py` is empty and `safe_write` imports only the standard library), but ARCHITECTURE says code moves to `shared/` "once a second feature needs it". Moving `safe_write` touches H2's `document.py`, `api.py` and two test files' import paths, so it was left as is. Say if it should move, here or as its own change.
- **Manual check** (scratch script outside the repo, isolated `APPDATA`, real `store` module, no UI):
  - A truncated `settings.json` → `get_theme()` "light", one `settings.json.corrupt-20261025-115222` with the exact original bytes.
  - H1's repro for 3 s (two threads, `set_document_position` and `set_outline_width`, 20 Recent + 1 Favorite) → still 20 Recent and 1 Favorite (H1: 0 and 0 in every run); no temp file left; `schema_version` 1.
  - The developer's real `%APPDATA%\Lector\settings.json` was last modified 2026-10-24 22:22, before this session: no test touched it.
- **Screenshots:** none. No visual change (decision B).
- **Not verified:**
  - Anything on macOS.
  - A real antivirus or sync client holding the file (simulated with `winerror` 32 and `PermissionError`).
  - A real power cut or hard kill mid-write (simulated by failing `fsync`/`replace` and a `KeyboardInterrupt` mid-`json.dump`).
- **Known limits:**
  - A hard kill can leave `settings.json.tmp` behind; the next save overwrites it.
  - Without the lock, two threads collide on the fixed temp name (seen in the red check); across processes the single-instance lock is what prevents that.
  - The log now records that a settings problem happened, never the file's content (H3's formatter keeps only exception types and frames; `test_nothing_from_the_file_reaches_the_log`).

#### Review (2026-10-25, fresh context)

Diffed the working tree against `master` @ `e932540`: 3 modified files (`store.py` +240/−10, `ARCHITECTURE.md` +11, `CHANGELOG.md` +1) plus the untracked `tests/test_settings_file_safety.py` (463 lines), read in full. Read the diff itself, not this log's description of it. Suite: `python -m pytest -q` → **1217 passed, 2463 subtests passed, 0 failed** (90 s). `git log` still ends at `e932540`. The developer's real `%APPDATA%\Lector\settings.json` is still dated 2026-10-24 22:22 after the run.

**Checklist**
- Can any failure path change or lose the original PDF? **PASS.** H4 touches no save or PDF code. `store.py` only calls `safe_write.replace` (`store.py:241, 291`) on settings files; `document.py`/`safe_write.py`/`api.py` are unchanged.
- New `fitz`/`pymupdf` import outside the five? **PASS.** `grep` of `src/lector` finds exactly highlighter, recent, document, ocr, search.
- New network use? **PASS.** None. A `grep` for urllib.request/http.client/requests/httpx/aiohttp/ftplib/smtplib/socket outside `frontend_server.py`/`single_instance.py` finds nothing.
- Raw exception text in the UI; content in logs? **PASS.** Nothing new reaches the UI: failures are logged and swallowed (`store.py:292-296`), never returned. Log calls carry fixed sentences (`store.py:135-137`), the copy's file name (`store.py:247`) and `exc_info` of an `OSError` (H3's formatter keeps type and frames only). A JSON parse error is caught without `exc_info` (`store.py:203-205`). `test_nothing_from_the_file_reaches_the_log` checks a planted path does not reach the log.
- UI thread blocked / PyMuPDF from several threads? **PASS.** No PyMuPDF. Settings calls run on pywebview's bridge threads, not the UI thread. The lock holds for one fsynced write (about 15 ms per the log). Worst case is the read retry, 5 × 0.1 s per read while another program holds the file, and it is documented. See the advisory below.
- Windows AND Mac? **PASS by reading the code.** The retry keys on `getattr(exc, "winerror", None)` (`store.py:212-225`), which is None on macOS, so there it simply raises once. `os.fsync` and `os.replace` are cross-platform. Nothing was run on a Mac.
- Tests added, suite passing? **PASS.** 30 new tests across 8 classes; no existing test changed (`git diff --stat` shows no file under `tests/` modified). Full suite green.
- CHANGELOG / docs / screenshots / nothing committed? **PASS.** One Fixed entry in plain language (`CHANGELOG.md:19`). ARCHITECTURE "Settings file (hardening H4)" matches the code, including its "Not covered" list. No visual change, so no screenshot is due (decision B). Nothing committed.

**Spec requirements**
1. Atomic `_save` (temp file in the same folder, flush + fsync, `os.replace`): **met.** `store.py:267-301`: `settings.json.tmp` beside the file, `f.flush()` + `os.fsync` (`:289-290`), `safe_write.replace` → `os.replace` (`:291`), and the temp file removed in `finally` (`:297-301`).
2. Writers serialized with a lock: **met.** `_lock = threading.RLock()` (`:119`) is taken by `_load` and `_save`, and by every setter through `@_serialized` (`:144-162`). `test_every_function_that_saves_holds_the_lock` walks the AST so a new unguarded setter fails. `LostUpdateTests` is deterministic.
3. An unparseable file is renamed to `settings.json.corrupt-<timestamp>`, one or two kept, then defaults load: **met.** `_set_aside` (`:228-253`) keeps `CORRUPT_COPIES_KEPT = 2` (`:106`). `DamagedFileTests` covers five kinds of damage byte-for-byte and the keep-two pruning.
4. `schema_version` with a no-op migration hook; no key names or defaults changed: **met.**
   - `SCHEMA_VERSION = 1` (`:100`) is stamped in `_save` (`:281-283`), and a higher stored int is kept. `_migrate` (`:256-264`) is a no-op.
   - The diff around the getters and setters only adds `@_serialized` lines. No key constant or default changed.
5. Tests: crash mid-write keeps the old file; a corrupt file is kept and defaults load; unknown keys survive a round trip: **met.**
   - Crash mid-write: `InterruptedWriteTests` (fsync, replace, `KeyboardInterrupt` mid-dump, full disk).
   - Corrupt file kept, defaults load: `DamagedFileTests`.
   - Unknown keys: `test_unknown_keys_survive_every_kind_of_write`.
6. Public signatures unchanged: **met.** `functools.wraps` (`:148`), and `test_the_lock_keeps_each_setters_name_signature_and_docstring`. Return values are unchanged.
7. CHANGELOG only if user-visible (it is), no commit: **met.**

**Advisory (not blocking)**
- While another program holds the file, a setter costs more than one read's retries: `add_recent_file` reads three times (`get_recent_files` ×2 + `_load`, `store.py:502-510`), up to about 1.2 s on `open_pdf`. This is rare, and it happens on a bridge thread.
- The kept-copy pruning sorts by name (`store.py:248`). Within one second, `-10` sorts below `-9`, and a clock set backwards could prune the newest copy. Both are edge cases; neither touches settings.json itself.
- The open `safe_write` question in the log above, whether to move it to `shared/` now that `settings/` imports it, is still the developer's to answer. It is not a defect: there is no runtime cycle, and the module is standard-library only.
- `api.py` is still over the 800-line soft ceiling. That was already true; H4 did not touch it.

**What this review did not cover:** anything on macOS; a real antivirus or sync client holding the file; a real power cut or hard kill; two Lector processes at once (that relies on the single-instance lock). The concurrency tests were read and run, but the lock's correctness was not proven beyond them. This is the same model reading the code again with fresh context. It catches real mistakes, but it is not proof that the code is correct.

**Status → reviewed.** Next: H5 (odd and hostile PDFs), plan mode.

**Committed 2026-10-25** to `master` as `e4385b6` ("fix(settings): save settings atomically, one writer at a time, and keep a damaged file"), fast-forwarded and pushed to `origin/master`; the branch was deleted. `docs/HARDENING.md` was left out of the commit, as before. The question of moving `safe_write` to `shared/` was not answered, so it stays in `features/reading/`.

### H5 — Odd and hostile PDFs (2026-10-25)

- **Branch:** `hardening/h5-open-pdfs`, from `master` @ `e4385b6`. **Uncommitted**: diff the working tree. New, untracked files (include them in the diff):
  - `src/lector/features/reading/open_errors.py`
  - `frontend/pages/password-dialog.js`, `frontend/pages/open-pdf.js`
  - `frontend/shared/icons/lock.svg`
  - `tests/test_open_failures.py`, `tests/test_password_unlock.py`, `tests/test_untrusted_pdf_content.py`, `tests/test_open_pdf_behavior.py`
- **Plan (approved in plan mode, after one revision):**
  - `PdfDocument.open` checks the new file completely (`_open_checked`) before letting the open one go, and raises `OpenError(reason)` with a plain sentence (`open_errors.MESSAGES`).
  - `Api.open_pdf` returns `{"error", "reason"}` and changes nothing else: no Recent entry; title, path and viewport untouched.
  - One frontend open flow (`openPdf`) replaces three copies of the old toast. Home's toast for a missing Recent file offers "Remove from Recent" through the existing confirmation.
  - Password-protected PDFs get a password dialog (below).
  - Untrusted content: H1 found nothing that follows a PDF's links or actions, so a guard test was added, as the spec says.
  - `highlight_counts()` measured, not changed.
- **Developer decisions:**
  1. **Wording:** "The PDF…" / "the file…", never "That PDF…", everywhere (including `file_drop.MISSING`, which now reuses `open_errors`'s sentence).
  2. **Password-protected PDFs get a password input box** that unlocks them, matching the project's dialogs. **This is a scope change, approved by the developer:** the spec said "flag it, don't build it" without approval. PRD, DESIGN_SYSTEM, ARCHITECTURE and CHANGELOG updated in this change.
  3. **Plan defaults accepted** (with the approved plan):
     - passwords are never remembered;
     - a copy keeps the same password;
     - a protected PDF's OCR text is not cached on disk;
     - a missing Favorite gets the message only;
     - `highlight_counts()` is not changed.
- **What was built:**
  - **`document.py`:**
    - **New helpers:** `_open_checked`, `_check_opened`, `_unlock` (module level).
    - **`open(path, password=None)`:** swaps under `_save_lock` via `_switch_to` (`_doc`/`_path`/`_password`/generation together under `_ocr_lock`). `close()` bumps the generation and drops the password.
    - **Password handling:** `is_password_protected`; save-a-copy always `PDF_ENCRYPT_KEEP` (as overwrite); `_verify_saved` requires the saved file to need a password exactly when the open one did. `_reopen_saved` (now an instance method), `_reopen_from_memory` and its fallback unlock through `_open_unlocked`.
    - **OCR:** `begin_ocr` marks the job `cacheable` and `finish_ocr` honours it (new `OcrJob.cacheable`, default True).
  - **`api.py`:** `open_pdf(path, password=None)`, which also refuses a non-string password.
  - **Voice:**
    - `router.PASSWORD_PROMPT` (only "cancel" / "never mind", no globals);
    - `reference._PROMPTS` (the existing Cancel card lists it);
    - each page's `lector:command` routes `CANCEL` to the dialog first.
  - **Frontend:**
    - `password-dialog.js`: markup injected once for Home, Reading and Settings, using the `.dialog-*` frame.
    - `open-pdf.js`, loaded by `index.html`, `reading.html` and `settings.html` before each page's script.
    - `home.js` `offerRemoval`.
    - `reading.js` no longer reloads after a failed or cancelled drop.
    - `components.css` `.dialog-field` / `.dialog-field-error`, and `lock.svg`.
- **A PyMuPDF trap found and handled:** on 1.28.2, reading `needs_pass` after `authenticate()` locks the document again, and every page then reads as empty, with no error. `_unlock` reads it once, before unlocking. This is documented in ARCHITECTURE, and `test_the_right_password_opens_it_with_its_text` would catch a regression.
- **Huge PDFs (measured on generated PDFs, Windows):**
  - `highlight_counts()` runs on every `_state()`: 26–37 ms at 500 pages, about 100–135 ms at 2,000.
  - `page_sizes()`: 34 ms at 500, about 147 ms at 2,000.
  - Nothing else walks every page at open.
  - Not slow at 500, so not changed (the spec's bar). The 2,000-page cost is in ARCHITECTURE's "Not covered".
- **Files changed:**
  - **Code:**
    - `src/lector/features/reading/document.py`, `ocr.py` (one dataclass field)
    - `src/lector/api.py`, `features/home/file_drop.py`
    - `features/voice/router.py`, `features/voice/reference.py`
    - `frontend/index.html`, `pages/reading.html`, `pages/settings.html`
    - `pages/home.js`, `pages/reading.js`, `pages/settings.js`, `shared/components.css`
    - plus the new files above
  - **Tests:** the four new modules above. `tests/test_voice_global_commands.py` gained `PASSWORD_PROMPT` in its hand-written `SCOPED_TABLES` and a `PasswordPromptScopeTests` class. No other existing test was changed.
    - The plan named two Node test files; they are one, `test_open_pdf_behavior.py`, since `openPdf` drives the dialog.
  - **Docs:**
    - `docs/PRD.md`: two must-have bullets; passwords are excluded from dictation in the voice section.
    - `docs/DESIGN_SYSTEM.md`: a Dialog field component and an "Opening a PDF: messages and the password dialog (hardening H5)" section.
    - `docs/ARCHITECTURE.md`: "Opening a PDF (hardening H5)", with its "Not covered" list.
    - `CHANGELOG.md`: one Added, one Fixed.
    - `docs/TECH_STACK.md` unchanged (nothing in it moved). `docs/TASKS.md` not touched.
- **Tests:**
  - **Before:** `python -m pytest -q` at `e4385b6` → 1217 passed, 2463 subtests passed.
  - **After:** **1262 passed, 2561 subtests passed, 0 failed.** That is +45, none removed.
  - **Red check:** the four new modules in a temporary worktree at `e4385b6` (removed afterwards), with only `open_errors.py` copied in so they import → 46 failed, 12 passed, 2 errors. Every new behaviour failed. What passed was already true on master: repaired files open, nothing follows links, the message table.
- **Code review** (ecc:python-reviewer and ecc:security-reviewer, in parallel): no CRITICAL. Fixed:
  - **HIGH (both reviewers): cancel while a password was being tried.** A right password then opened the new PDF on the backend while the page stayed on the old one.
    - Fix: while a try is in flight, Cancel is disabled and Escape and spoken "cancel" are ignored.
    - The Node test that asserted the old behaviour was replaced by one asserting the new.
  - **MEDIUM:**
    - **OCR cache race:** a protected job finishing just after another PDF opened, or after close, could have cached its text. Fixed by `OcrJob.cacheable` (decided at job creation), the atomic switch and the generation bump in `close()`, with two tests.
    - **`open()` not serialised with saves:** fixed by `_save_lock` around the swap.
    - **A copy of an owner-password-only (restricted) PDF dropped its restrictions:** the copy now uses KEEP, with a test. This predates H5 (H2 behaviour).
    - **No test of a failed save with a password:** added (`test_a_failed_overwrite_leaves_it_open_unlocked_and_unsaved`).
  - **LOW:**
    - a non-string password (refused), and a lone surrogate (now `wrong_password`, not a `TypeError`);
    - a file vanishing between the checks (`missing`, not `unreadable`);
    - MuPDF's `ValueError` is now logged at WARNING;
    - `_password` is reset where `_doc` becomes None after a failed save;
    - `setBusy(false)` runs after the stale-answer check;
    - a test docstring names its MuPDF-repair dependency;
    - the launcher scan was hardened against `from subprocess import Popen`, `import … as`, `from os import system`, `os.spawn*` / `os.exec*` and `ShellExecute`. Proven against a planted file; an innocent `.execute(...)` is not flagged.
  - **Not acted on:**
    - **The password lives in process memory** as a Python string while the PDF is open (cannot be wiped; recorded under ARCHITECTURE "Not covered").
    - **A protected PDF's path and reading position** go to Recent like any other PDF's.
- **In-app check** (scratch launcher outside the repo):
  - **Method:** a **hidden** window, isolated `APPDATA`, its own single-instance pipe, WebView2 DevTools port. Driven with `evaluate_js`: setting the field and `requestSubmit()`, dispatching a `lector:filedrop` event, clicking buttons in the page. No desktop capture and no OS input.
  - **Home (light, dark, sepia):**
    - opening "Tax return 2025.pdf" showed the dialog, with focus in the field and Open disabled;
    - a wrong password showed "The password isn't right. Try again." under the field, `aria-invalid`, the text selected;
    - Cancel emptied the field and Recent was unchanged;
    - a Recent file that was deleted showed "The PDF couldn't be found. It may have been moved or deleted." with **Remove from Recent**, which opened the existing confirmation.
  - **Light only:**
    - the right password opened Reading on the protected PDF, and Recent gained it;
    - dropping a second protected PDF on Reading asked there; after Cancel the first was still open ("Tax return 2025", 3 pages) and rendering.
  - The developer's real `%APPDATA%\Lector\settings.json` is still dated 2026-10-24 22:22.
- **Screenshots** (in the job's temp folder, `h5app/run4`–`run7`, not committed). Compared against DESIGN_SYSTEM.md:
  - **Files:**
    - `home_password_light` (run4), `home_password_wrong_{light,dark,sepia}` (run5)
    - `home_missing_toast_{dark,sepia}` (run6), `home_missing_toast_light` (run7)
    - `reading_password_light` (run4), `reading_after_cancel_light` (run7)
  - **Dialog:** the `.dialog-*` frame of the Replace confirmation, with lock badge, serif title, field with 2px focus ring, checkbox row, footer hint and Cancel/Open. It follows each theme. The error line is in `--danger`. **Matches.**
  - **Toast:** inverted colours, bottom-centre, with the action button. **Matches** "Toast" in Shared components.
  - **Found and fixed through the screenshots:** the field was only as wide as the description line; the header column holding a field now takes the card's width.
- **Not verified:**
  - **Animations, by eye.** The dialog's entrance and the toast's fade can't run in a hidden window (as H3 found), so for the screenshots they were set to their final state in the page.
  - **Screenshot reliability.** A few DevTools captures in the hidden window stalled until the page changed, so the launcher retries once after a style nudge. This is a tooling limit, not an app behaviour.
  - **Reading's screens in dark and sepia** (Reading was only driven in light).
  - **Anything on macOS.**
  - **A real file held open by another program** (simulated with `PermissionError`).
  - **Very large real-world PDFs** (generated ones were measured).
- **Known limits:**
  - The password is asked for on every open, including from Recent.
  - A protected PDF's Recent card has no thumbnail.
  - Voice can only cancel the dialog; the password must be typed.
  - `api.py` is still over the 800-line soft ceiling (pre-existing).

#### Review (2026-10-26, fresh context)

- **Diff reviewed:** working tree against `e4385b6` (18 tracked files, +356/−50) plus the eight untracked files listed above, read in full. `docs/HARDENING.md` excluded.
- **Tests:** `python -m pytest -q` → **1262 passed, 2561 subtests passed, 0 failed** (81.6 s). Matches the log.
- **Fresh-context review checklist:**
  - **Can a failure path change or lose the original PDF?** PASS. Opening never writes (`document.py` `_open_checked`, opens read-only). The new file is checked in full before the old one is closed (`open` → `_switch_to` under `_save_lock`). Saves still go through `safe_write.write_atomically`. `_verify_saved` now also refuses a result whose protection differs from the open PDF's. The reopen fallbacks (`_reopen_saved`, `_reopen_from_memory`) only read.
  - **New `fitz`/`pymupdf` import outside the five files?** PASS. The importers are still `highlighter.py`, `recent.py`, `document.py`, `ocr.py`, `search.py`. `open_errors.py` imports nothing.
  - **New network use?** PASS. Nothing added in `src/` or `frontend/` (no fetch/XHR/socket/urllib in the diff or the new files).
  - **Raw exception text or traceback to the UI; content in logs?** PASS.
    - **UI:** `Api.open_pdf` returns only `open_errors.MESSAGES` sentences (`api.py` `open_pdf`). `open-pdf.js` `callOpenPdf` turns a rejected bridge call into the generic sentence, with the error going to the console only.
    - **Logs:** the new `log.warning(..., exc_info=True)` / `log.exception` in `_open_checked` go through H3's formatter, which withholds exception messages (`shared/app_log.py:118-139`).
    - **Password:** never logged, stored or sent back (`test_password_unlock.py` `test_not_in_the_log_settings_recent_or_what_the_page_is_sent`). A protected PDF's OCR text is not cached (`finish_ocr` honours `OcrJob.cacheable`).
  - **UI-thread blocking / PyMuPDF from several threads?** PASS.
    - `_open_checked` runs where the old `fitz.open(path)` ran, on the same bridge call, so no new thread touches PyMuPDF. The swap is under `_save_lock` + `_ocr_lock`.
    - Pre-existing and unchanged: `PdfDocument` does not share `recent.py`'s `_fitz_lock`.
    - **Thumbnail check:** `recent.load_thumbnail` on an AES-256 user-password PDF returns `None` (checked this session), so Home never draws a locked PDF's page.
  - **Windows AND Mac?** PASS, not run on Mac.
    - `os.path.isfile` / `open(..., "rb")` and `pdfFileName`'s `/[\/]/` split are portable.
    - The CSS `:has()` (`components.css` `.dialog-header > div:has(> .dialog-field)`) needs WebView2 (Chromium 105+) or WKWebView (Safari 15.4+).
    - Nothing Windows-only was added.
  - **Tests added, whole suite passing?** PASS. Four new modules plus `PasswordPromptScopeTests`. 1262 passed.
  - **CHANGELOG / docs / screenshots / nothing committed?** PASS.
    - **CHANGELOG:** `[Unreleased]` has one Added and one Fixed.
    - **Docs:** PRD (scope change recorded), ARCHITECTURE "Opening a PDF (hardening H5)", DESIGN_SYSTEM dialog field and messages section.
    - **Screenshots:** recorded in the log. Reading in dark/sepia is unshot, but it is the same markup and CSS as Home's, which were shot in all three themes.
    - **Commit:** none (uncommitted).
- **H5 spec requirements:**
  - **Zero-byte, truncated/malformed, password-protected → plain-language failure, screen stays, nothing added to Recent:** met.
    - **Code:** `_open_checked` reasons `empty` / `not_a_pdf` / `password`. `Api.open_pdf` returns before `add_recent_file`. `openPdf` returns false, so no navigation; Reading no longer reloads.
    - **Tests:** `test_open_failures.py`, `test_open_pdf_behavior.py`.
    - **Password prompt:** built as an approved scope change (developer decision 2), recorded in PRD, ARCHITECTURE and CHANGELOG.
  - **Missing Recent/Favorites entry → friendly message, offer the existing "remove from Recent", never delete on disk:** met.
    - **Code:** `home.js` `offerRemoval` → `removeEntry` → `openRemoveConfirm` → `remove_recent_file` (list only; "The file stays on disk."). Favorites shows the message alone.
    - **Tests:** missing-file cases in `test_open_failures.py`.
  - **Huge PDFs: nothing at open walks every page on the UI path; measure `highlight_counts()` at 500 pages; fix only if slow:** met. Measured 26–37 ms at 500 pages and reported; not changed. `page_sizes()` noted.
  - **Untrusted content: H1 found no followed URIs/actions → add a test that external targets never open automatically:** met (`test_untrusted_pdf_content.py`, with a launcher source scan).
  - **Tests with PDFs generated in code:** met. `tests/fixtures/` still holds only the three `.wav` files.
  - **CHANGELOG entry:** met.
  - **Screenshot new UI text:** met (log's run4–run7, three themes on Home).
  - **Do not commit; ask:** met (uncommitted).
- **Edge checked, no fault:** `PasswordDialog.ask` closes an open dialog even while a try is in flight. A second drop could have brought back the cancel-while-trying bug, but every drop handler refuses drops while any `.dialog-scrim` is showing (`home.js:80`, `reading.js:1855`, `settings.js:51`). The scope admits no voice globals, so `ask` can't be re-entered.
- **Result:** all PASS, every requirement met → `reviewed`.
- **Not covered by this review:**
  - **No app run this session.** Visual and in-app behaviour rest on the implementation session's hidden-window check and screenshots.
  - **Nothing run on macOS.**
  - **No real other-program file lock.**
  - **No real-world (non-generated) protected or huge PDFs.**
  - **Same model:** this is the same model reading the code again with fresh context. It catches real mistakes, but it is not proof that the code is correct.

#### After the review (2026-10-26)

- **`import fitz` → `import pymupdf`**, at the developer's request: the running app printed PyMuPDF's "The `fitz` API is deprecated" warning.
  - **What changed:** all 21 Python files that used the old name (the five allowed modules, 15 test files, `scripts/build_app_icon.py`), plus the TECH_STACK.md heading and one ARCHITECTURE.md mention. It is the same library under its newer name; no behaviour changed.
  - **H7:** this is H7's optional item, so H7 has nothing left to do for it.
  - **Checks:** with every warning shown, importing the app's modules prints nothing about it. Suite: 1262 passed, 2561 subtests passed.
- **Commit:** the developer chose one commit for H5 and the rename together, including this file, merged to master and pushed.
