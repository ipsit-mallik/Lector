# Product Requirements Document

## Problem

Adobe Reader's ~10GB install footprint is unjustified for the actual use case: read-only PDF viewing, nothing else. Separately, hands are frequently occupied while reading (eating, exercising, chores), making mouse-driven scrolling, page navigation, and highlighting impractical.

## Who it's for

- **v1:** the developer, personal use.
- **Future (conditional):** public open-source release if there's demand, and a portfolio/social-media piece regardless of adoption.

## Must-have v1 features

- Voice-driven page navigation: next/previous page, jump to page N, scroll up/down.
- Speech-to-match highlighting: user speaks a few words, app fuzzy-matches them against the text currently visible in the viewport and highlights the matched span. Word-level matching is the baseline; sentence-level highlighting extends the match to the nearest sentence-ending punctuation (heuristic, not full sentence-segmentation NLP).
- Disambiguation rule when a phrase matches more than once: scope matching to the visible viewport only (never search the whole document); on a tie within the viewport, the first occurrence in reading order (top-to-bottom, left-to-right) wins.
- Real PDF annotation persistence via PyMuPDF — highlights are written as actual PDF annotation objects, so they're visible in any standard PDF viewer, not just this app.
- Save-behavior preference: on the first explicit Save action, ask "Save a copy" vs. "Overwrite the original," with a "Don't ask again — remember this choice" checkbox (unchecked by default). The dialog is generalized to "Save changes?" — it is triggered only by an explicit Save action (voice command, click, or shortcut), never automatically as a side effect of highlighting or any other future edit.
- Dual voice activation: push-to-talk (default key: Space) and a wake phrase ("Hey Lector"), independently toggleable; both remain available as fallback regardless of which is the default.
- Home screen "Recent" list: last 10 files, most-recent-first.
- Two reading layouts, user-toggleable: book (paginated, one page at a time) and continuous strip (scrolling, with a subtle page-break marker between pages).
- Full mouse/keyboard parity everywhere: voice is an accelerator on top of a fully usable traditional GUI, never a replacement for one. Every interactive element is a real, keyboard-focusable control.
- Discoverability: an on-screen "What can I say?" command reference, categorized (Moving around / Highlighting / Finding words / The app itself), for users unfamiliar with the exact command phrasing.
- Themes: light, dark, sepia.
- First-run onboarding: microphone permission request (skippable — app remains fully usable via mouse/keyboard if skipped) and voice activation mode selection (also changeable later in Settings).

## Explicitly out of scope for v1

- Full PDF editing: modifying text, repositioning images/tables, or any PDF↔Word round-trip conversion pipeline.
- General-purpose NLU / arbitrary phrasing comprehension. Voice commands use a fixed, synonym-mapped grammar (several known phrasings per intent), not open-ended speech understanding.
- Mobile app.
- Web app.

## Success criteria

The user can read a PDF for a full session hands-free, using only voice, without needing the mouse or keyboard for navigation or highlighting.

## Hard constraints

- **Budget:** $0 — no paid or cloud-based services of any kind.
- **Voice recognition must run fully offline** — privacy, zero marginal cost, and no internet dependency for the core reading use case.
- **Licensing:** MIT for original project code. The PySide6/Qt dependency is LGPL, so packaging must ship Qt as dynamically-linked shared libraries (not statically compiled into the executable) to remain compliant. (Not formal legal advice — standard defensible practice for this situation.)
- **Platform:** Windows + Mac desktop only for v1. No Linux, no mobile, no web.

## Naming

Mockups currently use "Lector" as a working name (it was auto-filled by the design-generation tool during iteration, not a deliberate choice yet). Not finalized — revisit before any public release or portfolio posting, including a basic trademark-conflict check.
