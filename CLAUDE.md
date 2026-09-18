# CLAUDE.md

Working context for any AI coding agent (Claude or otherwise) operating in this repository.

## Project

**Lector** (working name, not finalized — see `docs/PRD.md`) — a lightweight, offline, voice-controlled desktop PDF reader for Windows and Mac. Built to replace Adobe Reader for read-only use: hands-free page navigation and highlighting by voice, with full mouse/keyboard parity as a hard requirement, not an afterthought.

## Governance docs

@import docs/PRD.md
@import docs/TECH_STACK.md
@import docs/DESIGN_SYSTEM.md
@import docs/ARCHITECTURE.md
@import docs/TASKS.md
@import CHANGELOG.md

Read these before making product, stack, visual, or structural decisions. They reflect deliberate, already-settled choices — not defaults to silently override because a different approach seems reasonable in isolation.

## Workflow rules

1. **doc-sync-on-change** — If a change contradicts or supersedes something stated in `docs/PRD.md`, `docs/TECH_STACK.md`, `docs/DESIGN_SYSTEM.md`, or `docs/ARCHITECTURE.md`, update the relevant doc in the same change, not as a follow-up. Docs that drift from the actual code are worse than no docs.
2. **changelog-on-change** — Any user-facing change (feature added, behavior changed, bug fixed) gets an entry in `CHANGELOG.md` under `[Unreleased]`, following Keep a Changelog format, in the same change that makes it.
3. **ask-before-logging-to-engineering-log** — `docs/ENGINEERING_LOG.md` does not exist yet (deferred past prototype stage, per `docs/PRD.md` scope). If/when it exists: do not write to it automatically as a side effect of other work — ask first, since it's a narrative record the developer curates deliberately, not an auto-generated activity log.

## Notes for the agent

- This project has an explicit, deliberately narrow scope (see `docs/PRD.md` § Explicitly out of scope). Suggestions that reintroduce cut scope (e.g. PDF↔Word editing, cloud voice APIs, a database) should be flagged as scope changes and confirmed before implementing, not built quietly because they seem like an improvement.
- Dark and sepia theme tokens in `docs/DESIGN_SYSTEM.md` are marked provisional/unverified — treat them as a starting point to confirm during implementation, not as settled as the light theme is.
- "Lector" is a placeholder name throughout the codebase and UI copy. Don't treat it as final branding.
- `docs/TASKS.md` is the implementation checklist, in deliberate build order (core reader before voice, push-to-talk before wake phrase, navigation commands before highlight matching). Work through it top to bottom; don't jump ahead to a later milestone because it seems more interesting or because a task looks trivial in isolation — the ordering exists to isolate failure causes (rendering vs. recognition vs. matching) and was chosen for that reason, not arbitrarily. Check items off as completed, and stop to ask if a task needs a decision the docs don't cover, rather than guessing.
