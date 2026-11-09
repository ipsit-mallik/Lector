# File tools mockups (Tier 1 and Tier 2)

Clickable mockups for the file tools in `docs/PRD.md` § "Post-v1: file tools". They are a **visual reference**: what the screens look like and how they behave when clicked. They are not the spec and not code to copy.

## Which source wins

1. `docs/PRD.md` and the implementation prompt for the task: what to build.
2. `docs/DESIGN_SYSTEM.md` and `frontend/shared/theme.css`: tokens, components, spacing, motion.
3. These mockups: layout, hierarchy, copy, states and flow.

If a mockup disagrees with 1 or 2, follow 1 or 2 and say where they differ. Don't copy hex values, inline styles or markup from these files. The app's colours come only from `theme.css` tokens, and its controls are the shared components in `frontend/shared/components.css`. Copy text (labels, button names, messages) *is* meant to be used as written unless the prompt says otherwise.

## Opening them

Every page is self-contained and works offline: open it straight from disk, or serve the folder.

```
python -m http.server 8765 --bind 127.0.0.1 --directory docs/mockups/file-tools
```

Then go to `http://127.0.0.1:8765/`, which has an index of all four. In a browser-automation tool, use a 1280×800 viewport: each mockup is a fixed 1280×800 window.

Options go in the URL (combine them with `&`):

| Option | Values | Pages |
|---|---|---|
| `theme` | `light` (default), `dark`, `sepia` | all |
| `unsavedHighlights` | `true` (default), `false` | reader-tier1 |
| `signedPdf` | `false` (default), `true` | reader-tier1 |
| `compressResult` | `smaller` (default), `already%20compact` | reader-tier1 |
| `startView` | `tools` (default), `recent` | home-tier1, home-tier2 |
| `startIn` | `reader%20menu` (default), `organise%20pages` | organise-tier2 |

Example: `reader-tier1.html?theme=dark&signedPdf=true`.

## The pages

| Page | Shows | Try |
|---|---|---|
| `reader-tier1.html` | The reader's new ⋯ More menu (open on load) and the Compress, Make searchable and Export as images dialogs, each through Set up → Working → Result | Pick a tool and run it; "Keep reading" during a job shows the footer pill, then the finished toast; Export's "Pages" range updates the count on the button |
| `home-tier1.html` | Home's new Tools page; the Choose-a-PDF step; Convert to PDF; the Recent row ⋯ menu with the tools added | Click a tool card; "Add files…" in Convert adds an e-book and shows its note; Recent in the sidebar, then a row's ⋯ |
| `home-tier2.html` | The same Tools page with the Tier 2 "Pages" section: Organise pages, Combine files, Split; the Recent row ⋯ menu gains Organise pages… and Split… | Combine: reorder, add and remove files, and the note for a password-protected input. Split: "Every N pages" or "Page ranges", the live preview of the files it will make, the warning for pages left out (try `1–10, 41–120`), and the error that disables the button (try `1-200`, or `0` pages) |
| `organise-tier2.html` | The reader's ⋯ menu with Organise pages, and the Organise pages view | Click, Ctrl+click and Shift+click to select; drag to reorder; Rotate; Delete (the confirmation dialog, then the toast with Undo); Select all then Delete (refused: a PDF needs a page); Extract (with "Save in"); Insert (position choices); Undo and Redo; Done, then the reader's page count has changed |

## What is placeholder

- Page content is grey bars; documents, file names, sizes and page counts are invented.
- Progress is simulated on a timer; results are canned (the real numbers come from the engine).
- Buttons that only lead somewhere already built (Save, Show in folder, Change…, Browse…) do nothing here.
- Window chrome is simplified: only the parts each tool touches are drawn faithfully.
- Keyboard shortcuts named in the prompts (Esc, Delete, Ctrl+A, Ctrl+←/→, arrow-key focus in the grid) aren't wired up here; the prompts define them.

## Files

- `*.html`: the mockups. Each page's markup is in a `<template data-dc>` block with `{{holes}}`, and its behaviour is in the `<script type="text/x-dc">` block at the end, which is where the states and the copy for each state live.
- `dc-lite.js`: the small offline renderer that makes the pages run. It is a mockup aid only: never load it from `frontend/` or ship it.
