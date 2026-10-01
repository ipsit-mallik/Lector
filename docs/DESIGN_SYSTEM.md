# Design System

Source of truth: `Lector Desktop Mockups.dc.html` — a 9-screen mockup canvas covering Home/Library, empty state, Reading (book + continuous-strip layouts), the "What can I say?" command reference, Settings, two-step first-run onboarding, and the save-changes confirmation dialog. Tokens below are extracted directly from that file's light theme, which is the only fully-rendered theme in the mockups.

## Brand feel

Calm, minimal, utilitarian. A focused reading tool, not a busy productivity suite. Confirmed through iterative review, not a first guess.

## Color tokens — Light theme (authoritative, extracted from final mockups)

| Role | Hex |
|---|---|
| Canvas/outer background | `#E7E2D9` |
| Surface (window/card background) | `#F6F3EE` |
| Secondary panel background (sidebar, toolbars, footers) | `#EFEAE2` |
| Tertiary panel shade (blurred/inactive toolbar states) | `#F2EEE7` |
| Input/search field background | `#FFFDF9` |
| Primary text (UI chrome) | `#161A18` |
| Reading body text (serif content) | `#1C211E` |
| Secondary text / inactive nav items | `#4A4F49` |
| Body copy in dialogs and notes | `#5A5F58` |
| Muted/tertiary text | `#6B6F6A` |
| Very muted text (chapter labels, timestamps) | `#9A968E` |
| Accent (primary buttons, active nav, links) | `#3E6259` |
| Accent hover/pressed | `#33534B` |
| Accent darker (link hover, active nav/toolbar items on tinted backgrounds) | `#2C463F` |
| Voice-focus / "listening here" indicator | `#C97A3D` |
| Highlight background | `#F7DE7A` |
| Text-selection wash (drag in progress, before the highlight is applied) | `rgba(62, 98, 89, 0.42)` — the accent at 42%, blended multiply |
| Destructive hover (e.g. window close button) | `#C0453B` |
| Border / divider (light) | `#D2CCC2` |
| Border / divider (subtle) | `#D8D3C9` |
| Border / muted decorative | `#A8A6A0` |

**Why the focus indicator and highlight color are different colors, on purpose:** the voice-scan focus indicator (amber/clay, `#C97A3D`) and the highlight fill (soft yellow, `#F7DE7A`) must stay visually distinct — one means "the app is currently listening/pointing here," the other means "this text is already highlighted." Collapsing them into one color was flagged early as a usability risk and deliberately avoided throughout every iteration.

**The selection wash is a third state in that same family, and follows the same rule:** it means "this is what you are *about* to highlight" — text the reader has under an in-progress drag, which is not yet an annotation and may never become one (releasing outside, or pressing Escape, discards it). It is the accent green at low opacity rather than a fourth hue, which keeps the three states legible as a sequence — selecting (green wash) → highlighted (yellow) → being read aloud to/listened at (amber) — without introducing a color the palette doesn't already contain. It is deliberately *not* themed per light/dark/sepia: it sits on the rendered PDF page, which is the document's own paper and stays white in every theme, so a per-theme value would be tuned against a background that never changes.

**Highlighting is two visible beats, not one**, matching Adobe Reader and other desktop annotators: while the mouse is held, the text under the drag is washed in the selection color, following the cursor character by character and running edge-to-edge across each full line it covers; only on release does that same shape become the yellow highlight. The selection preview and the written annotation are composed identically (one box per line of text, not one per character), so the shape never changes under the cursor at the moment of release. Pressing on empty page — a margin, the gap between paragraphs, the blank lower half of a page — selects nothing and highlights nothing; the drag has to start on a character. Once it has, it extends to the nearest character, so dragging past the end of a line or off the page keeps selecting rather than stopping at the last glyph.

**Both beats are drawn as plain rectangles**, sharply cornered and exactly as wide as the text they cover — again the Adobe Reader shape. This is worth stating because it is not what a PDF library gives you for free: MuPDF renders a `/Highlight` annotation marker-pen style, with rounded bezier end-caps that bulge sideways by roughly a sixth of the line height. Across a whole word that reads as a soft edge, but selection here is character-level, and a single character is narrower than the caps themselves — the highlight becomes an oval about twice the glyph's width that visibly tints the characters on either side, so the reader cannot tell which character they actually marked. Lector therefore rewrites the annotation's appearance stream as one rectangle per line (`_set_rectangular_appearance` in `features/annotations/highlighter.py`), keeping the multiply blend so the glyphs stay readable underneath, and leaving the annotation a real `/Highlight` that other viewers still list as one.

## Color tokens — Dark and Sepia themes (provisional, not yet re-verified against final mockups)

The final mockup file includes a theme *selector* (on the Settings screen) but only fully renders the light theme end-to-end — dark and sepia are represented as an intent/toggle, not fully specified across every screen. The values below are the last working proposal from earlier design iteration and should be treated as a starting point to confirm, not as finalized as the light-theme table above:

| Role | Dark | Sepia |
|---|---|---|
| Background | `#1C1A17` | `#F1E7D0` |
| Text | `#EDE8DF` | `#3B2F20` |
| Muted text | `#9C948A` | `#8A7859` |
| Accent | `#6FA394` | `#4F6B5C` |
| Border | `#3A362F` | `#DDCBA0` |
| Surface | `#26231F` | `#F8F0DE` |
| Highlight | `#6B5A24` | `#E8C468` |
| Focus indicator | `#E0954F` | `#B5672E` |

**Action item before implementation:** run the same "is this readable, is the focus/highlight distinction still clear" check against dark and sepia that the light theme already passed, since only light has actually been eyeballed across all 9 screens.

**Implementation note (Milestone 4):** `shared/theme.py`'s `DARK` and `SEPIA` dicts use these 8 values as-is, then interpolate the rest of the full token set (the light theme's 19 keys — `panel_bg`, `panel_tertiary`, `input_bg`, `accent_hover`, etc. aren't covered by this table) to make the themes actually usable end-to-end. That interpolation is this implementation's own guess, not a design decision from the mockups, and still needs the readability/contrast check above before being treated as settled — the action item isn't closed by having code that compiles.

## Motion & elevation foundation

Added after real-usage feedback on the Home/Library screen: cards, sidebar nav items, and interactive surfaces had **no hover state at all** (`.recent-card` had zero `:hover` rule defined, anywhere), and the one hover effect that did exist (`.nav-item:hover`) used a hardcoded `rgba(20, 26, 22, 0.06)` near-black wash — a darkening tint that only works over a *light* background, so it was silently doing nothing in dark theme. Separately, dark theme's `--color-canvas` (`#1C1A17`) and `--color-panel` (`#211E1A`) were only ~5 units per channel apart, making the sidebar nearly indistinguishable from the page behind it, and dark mode's card borders relied on the same black-alpha `box-shadow` recipe as light mode, which does not read against a near-black surface. None of this was a per-component bug to patch individually — the tokens for hover/elevation/motion simply didn't exist yet, so this section adds them once, as the foundation every component below should consume rather than re-inventing.

**New theme-independent tokens** (`frontend/shared/theme.css`, one `:root` block, same across light/dark/sepia):

| Token | Value | Use for |
|---|---|---|
| `--duration-fast` | `120ms` | micro feedback — icon toggle, button press/active state |
| `--duration-base` | `180ms` | hover transitions — card lift, background/border tint changes |
| `--duration-slow` | `260ms` | expand/collapse, panel-scale motion (matches the tree-sidebar dialog's existing spec) |
| `--ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | default easing for hover/enter transitions — decelerate, no overshoot |
| `--ease-emphasized` | `cubic-bezier(0.34, 1.56, 0.64, 1)` | spring-overshoot easing for expand/collapse and anything meant to feel a little alive (already used by the Open PDF tree's branch motion; promoted here as a named token instead of a magic number restated per component) |

**New per-theme tokens** (light, dark, sepia each define their own value):

| Token | Purpose |
|---|---|
| `--color-surface-hover` | background tint for a hovered interactive surface (card, list row, nav item). Light/sepia mix 4% black into the surface color (darken); dark mixes 8% *white* into it instead — there is no headroom to darken further this close to the canvas floor, so dark-mode hover has to lighten. |
| `--color-surface-active` | same idea, one step further, for the pressed/active state (8%/8%/14% respectively). |
| `--shadow-elevation-1` | resting elevation for a card-like surface. Light/sepia: a real (subtle) box-shadow. Dark: `none` — a black shadow is invisible on a near-black canvas, so dark-mode resting elevation comes from the surface/canvas contrast and border alone, not a shadow. |
| `--shadow-elevation-2` | hover/lifted elevation, one step up from elevation-1. Light/sepia: a stronger box-shadow. Dark: a much fainter shadow than light's (`rgba(0,0,0,0.35)` at a tight 2px/8px spread) — present mainly so the hover *transition* has something to animate, not to carry the visual weight; the real dark-mode hover signal is the border and background tint above. |
| `--color-elevation-border` | the border color a card/panel should use to read as a distinct surface against its background. In light/sepia this is just `--color-border-light`; called out as its own token because dark mode may want to diverge from the ambient border color here without that becoming implicit. |

**Dark theme's canvas/panel/surface were widened** to restore proper separation:

| Token | Old (provisional) | New (still provisional — confirm visually) |
|---|---|---|
| `--color-canvas` | `#1C1A17` | `#171512` |
| `--color-panel` | `#211E1A` | `#201C18` |
| `--color-panel-tertiary` | `#232019` | `#241F1A` |
| `--color-surface` | `#26231F` | `#2A2621` |
| `--color-input-bg` | `#2C2822` | `#322D26` |

The ordering `canvas < panel < surface < input-bg` (darkest to lightest) now mirrors light theme's `#E7E2D9 < #EFEAE2 < #F6F3EE < #FFFDF9` relationship with comparable perceptual spacing between steps, instead of canvas and panel sitting almost on top of each other. Like the rest of the dark/sepia table above this section, these are a reasoned starting point, not eyeballed against a rendered screen yet — the same "confirm before treating as settled" caveat applies.

**Usage pattern for any interactive surface** (card, list row, nav item, button):
- At rest: a visible `--color-elevation-border` (or the card's existing border token) so the surface reads as its own thing even before interaction — don't rely on hover alone to establish that a card is a card.
- On hover: `background-color` moves to `--color-surface-hover`, `border-color`/`box-shadow` moves from elevation-1 to elevation-2, transition on `background-color, border-color, box-shadow, transform` using `--duration-base` and `--ease-standard`. A small lift (`transform: translateY(-1px)`) is optional per component but should use the same duration/easing when present, not a separate value.
- On press/active: `--color-surface-active`, `--duration-fast`.
- Reduced motion: handled globally now — `theme.css` carries a `prefers-reduced-motion: reduce` block that collapses every transition/animation duration to near-zero, so individual components don't each need their own copy of that query. A component only needs its own reduced-motion handling when a *state change itself* (not just the transition into it) must be skipped, as documented in the Open PDF dialog's Motion note below.

## Typography

- **Reading/body text:** `Charter, Georgia, serif` — a serif face for long-form reading content.
- **UI chrome:** `system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Helvetica, sans-serif` — native system font, not a webfont, keeping the app lightweight and OS-native-feeling.
- No Inter/Roboto/Arial as a deliberate choice, no emoji-as-icons — all icons are real stroke SVGs.

## Layout patterns

- **Home/Library:** left sidebar navigation (Recent / All PDFs / Favorites / Settings) + main content area with search, a primary "Open PDF" action, and a card grid of recent files (thumbnail + filename + relative timestamp), capped at 20 (raised from the original 10 per explicit product direction; the original 10 was sized to Miller's Law's "7±2 items held in working memory" heuristic per the Design rationale notes below, and 20 is a deliberate, known trade of some of that at-a-glance/spoken-recall ease for a longer at-hand history -- not an oversight if a future review flags it against that heuristic).
- **Reading view:** left icon toolbar rail (highlight tool, search-in-document, zoom in/out, book/strip layout toggle, theme cycle, "What can I say?") + main reading pane. Book layout centers a bounded page card; strip layout removes the card boundary and flows continuously with a subtle page-break marker between pages.
- **Zoom control:** the topbar's zoom percentage is a button — clicking it opens a small popover with a slider for continuous zoom, in addition to the toolbar rail's step-by-25% zoom in/out buttons. Not present in the original mockups; added after implementation when stepped-only zoom proved awkward for fine adjustment.
- **Modals/dialogs** (save confirmation, "What can I say?" reference): centered card over a dimmed and slightly blurred background, not a full-screen takeover — keeps context visible.
- **Settings:** single-column sections (Theme, Voice activation, Save behavior), not tabs — small enough surface area that tabs would add navigation overhead for no benefit.

## Numbered-overlay picker (Milestone 8.6)

Mockup: `docs/mockups/09 Numbered picker overlay.png` (composited directly on the real Screen 01 export for pixel-accurate color/spacing, not a fresh drawing). This postdates the original 9-screen set, but follows a pattern the app already established rather than inventing a new one:

- Same rule as the reading view's "Listening here" outline (Milestone 6): a marker that exists only while voice attention is actually scoped to that content, drawn in `--color-voice-focus` (`#C97A3D`) — never the highlight yellow or the accent green, per this doc's existing rule that those three stay visually distinct. This is a "the app is pointing here" state, not a highlight or a selection.
- Each pickable card (starting with the Recent grid's `.recent-card`) gets a small circular badge in its top-left corner, overlapping the corner slightly like a notification count — ~24px diameter, `--color-voice-focus` fill, `--color-surface` numeral text, centered, `font-weight: 600`. A plain circle, not the card's own 9px corner radius — a numeral reads better in a circle than a rounded square at this size.
- Badges render only while the `picker` context (Milestone 8.4's router) is active — e.g. after a voice command that needs disambiguation among more than one match — and disappear the instant a number is spoken/clicked or the picker is cancelled. No persistent chrome, exactly like the amber outline only existing for the duration of a push-to-talk hold.
- No dimmed/blurred backdrop behind the grid — that treatment is reserved for modal dialogs (see Layout patterns above). The picker overlays in place on the still-fully-visible, still-clickable grid; voice and mouse/keyboard must stay simultaneously usable per the parity requirement.
- Mouse/keyboard parity: clicking a card, or tabbing to it and pressing Enter, works identically whether or not the picker overlay happens to be showing. The badges are an additional voice affordance layered on top of existing behavior, never a mode that disables it.

## Remove-from-Recent affordance (Milestone 8.9)

List-entry removal only (never deletes, renames, or modifies the underlying PDF file — reopening it re-adds it to Recent). No new mockup exists for this (it postdates the original 9-screen set, decided directly against this doc during implementation), so it is built entirely from tokens/components already established rather than a fresh design:

- **Entry point:** a small icon-button (`remove.svg`) in each `.recent-card`'s top-right corner, hidden at rest (`opacity: 0`) and revealed on card hover or the button's own `:focus-visible` — the same "hidden until relevant" treatment the numbered-overlay picker's badges use. Still a real, independently focusable `<button>` even while visually hidden, so it stays keyboard-reachable regardless of hover state. **Deviates from the original spec on one point, confirmed with the developer:** the plan called for reusing the existing `.icon-btn` class verbatim; the built version instead uses a new `.recent-remove-btn` class sized 30px (vs. `.icon-btn`'s 44px), because 44px was judged too large for this corner without crowding the thumbnail. The developer reviewed both the built version and the original plan and chose to keep the 30px version.
- **Color:** `--color-destructive` on hover/active only (a tinted background plus destructive-colored icon), resolving to `#C0453B` in the light theme — the one other destructive-leaning affordance in the app besides the save dialog's implicit "Overwrite" risk. At rest it matches the card's ordinary muted icon color; the destructive tint is a hover/focus consequence, not a permanent visual flag on every card. Uses the `--color-destructive` token rather than a hardcoded hex so dark/sepia get their own values automatically.
- **Confirmation dialog:** reuses the existing `.dialog-scrim`/`.dialog-card`/`.dialog-body`/`.dialog-header`/`.dialog-badge`/`.dialog-desc`/`.dialog-footer` frame verbatim — no new dialog chrome or copy pattern. Title "Remove from Recent?", a `.dialog-badge` using the same `remove` icon, a one-line description naming the file, and a footer hint ("The file itself is never touched.") stating the non-destructive scope explicitly.
- **Mouse/keyboard parity:** clicking the icon-button (or tabbing to it and pressing Enter/Space) opens the same confirmation dialog voice opens; nothing about the button's presence changes how the card itself behaves when clicked (still opens the file).
- **Voice path:** "remove recent" / "delete recent" (global, from Home) removes the single most-recent file's entry, mirroring 8.5's `OPEN_RECENT`; "remove a file" / "delete a file" opens the numbered-overlay picker in a removal mode instead of open mode, so a spoken number picks which entry to remove rather than which to open. Either path lands on the same confirmation dialog, itself voice-drivable ("remove it" / "confirm remove" to proceed, "cancel" / "never mind" to back out) regardless of whether it was opened by voice or by clicking the icon-button — voice never skips the confirmation step a mouse user would also see.
- **Revised (Motion & elevation foundation pass):** real-usage feedback was that the button, pinned flush against the thumbnail's corner and fully invisible (`opacity: 0`) until hover, read as a loose icon floating over the image rather than a discoverable control — a first-time user had no way to know it existed. It is now 28px (down from 30px), inset 8px from the corner instead of 4px, and *always rendered* at a low-key resting background/icon color rather than hidden — hover/focus raises it to full contrast and the destructive tint, but it's never fully invisible. Uses the new `--color-panel`-based scrim tokens rather than a flat transparent background, so it reads correctly against both the card surface and whatever's behind it in every theme.

## Recent-card hover, elevation & keyboard focus (Motion & elevation foundation pass)

`.recent-card` had no `:hover` state at all before this pass — see "Motion & elevation foundation" above for why — and was also built as a plain, non-focusable `<div>` with a click handler, in direct violation of this doc's own Accessibility baseline ("never a clickable div"). Both are fixed together, since the hover styling and keyboard-focus styling are the same rule:
- **Elevation:** rests at `--shadow-elevation-1` with a `--color-elevation-border` border; on hover *or* keyboard focus, border shifts to the accent color, elevation steps up to `--shadow-elevation-2`, and the card lifts 2px (`transform: translateY(-2px)`), settling to 1px on `:active`. Same transition timing as every other surface covered by the foundation section (`--duration-base` / `--ease-standard`).
- **Keyboard focus:** the card cannot be a literal `<button>` (it contains its own independently-focusable remove button, and a `<button>` cannot contain another `<button>`), so it gets `role="button"`, `tabindex="0"`, an `aria-label` naming the file, and an Enter/Space handler that calls the same open action the click listener does — the standard fallback pattern for a composite control shaped like this, and the closest this doc's "real button" rule can get without invalid HTML. `:focus-visible` gets the same lift/elevation treatment as `:hover`, so keyboard users get the same feedback mouse users do, not a lesser version of it.

## Recent count display

Previously a plain text label reading "N of last 10 files" (or "3 of last 10 files"). Replaced with a small pill/badge next to the "RECENT" heading showing just the count of cards actually on screen (e.g. "12") — the cap is an implementation detail, not something the heading needs to surface. Empty state still reads "no files yet" as plain muted text (nothing to badge). See the cap change above (10 → 20) for why the count and the cap are two different numbers worth keeping visually distinct.


## Recent grid/list toggle

Added alongside the cap increase (10 → 20) since a longer history is exactly when a scannable, sortable-at-a-glance list starts earning its keep over a grid of thumbnails:

- **Toggle:** a segmented pair of icon buttons (`grid_view`/`list_view`) at the right edge of the "RECENT" heading row, `aria-pressed` marking the active one. A persisted preference (`settings.json`'s `recent_view` key, `get_recent_view`/`set_recent_view` — same pattern as `theme`/`reopen_behavior`), not session-only UI state: the point of a view preference is that it stays how the reader left it.
- **List row columns:** a small 34×44px thumbnail (down from the grid's full-width preview — no room for a 118px-tall image in a dense row, and the title is already the primary identifier), Title, Last opened, Pages, and Progress. File size was deliberately left out: this is a reading app, not a file manager, and size tells a reader nothing they'd act on.
- **Progress column:** the reader's last-recorded page as a percentage of the document (`recent.py`'s `list_recent()`, from the same position data Resume Reading already tracks — no new state). A file with no recorded position (never opened past page 1, or never opened at all through this build) shows "Not started" text rather than a 0%-filled bar — collapsing "no data" into "read 0%" would misrepresent a file the reader may well have already finished reading elsewhere before this feature existed.
- **Shared interactive-surface contract:** grid cards and list rows both carry the `.recent-card` class (list rows add `.recent-row` alongside it, never instead of it) so the numbered-overlay picker's `.recent-card` query, and the hover/elevation/focus recipe from "Motion & elevation foundation" above, work identically in either view without a separate branch. Layout (width, padding, flex direction) is the only thing that differs by container (`.recent-grid .recent-card` vs. `.recent-list .recent-card`); list rows also drop the grid card's 2px hover lift (rows sit too close together, at a 6px gap, for a vertical shift not to visually collide with a neighbor) while keeping the border/shadow feedback.
- **Remove-from-Recent button:** unchanged control (`buildRemoveBtn()`, shared by both `buildCard()` and `buildListRow()`), just repositioned — absolutely positioned as a thumbnail-corner overlay in the grid, a normal trailing flex child in a list row.

## Open/Save-As dialog (`open_dialog`/`save_dialog`) (Milestone 8.7; drive navigation added later; tree-sidebar redesign added later still)

**Redesigned from the original flat numbered list**, after real-usage feedback: the "Other Drives" row worked but was easy to miss (one row in a long alphabetized list a non-technical user had no reason to scroll to), and the footer's Up/Cancel pair — two same-style buttons doing very different things — read as ambiguous. Both dialogs share one implementation (`createFileBrowser()` in `frontend/js/file-browser.js`), so this redesign applies to both Open PDF and Save-As identically; only the footer's filename field and Save vs. Open behavior still differ between them, as before.

**Layout — two-pane, not a single list.** The dialog card widens from the shared 560px (every other dialog in the app) to **940px** — a deliberate, documented exception, because a persistent directory tree needs real width to stay legible. Left pane (268px): Quick Access + a directory tree. Right pane: the folder's contents (unchanged numbered-row mechanic from the original design).

- **Quick Access** (top of the left pane): Home, Desktop, Documents, Downloads — each resolved from the OS at runtime (`Path.home()` for Home; on Windows, Desktop/Documents/Downloads come from the shell's known-folder lookup so OneDrive-style folder redirection is followed, and elsewhere they are `Path.home()`'s children — and an entry is only offered when it is an actual folder; a missing one is simply omitted, never shown broken). Each has a natural spoken label, so — matching the numbered-overlay picker's existing principle that "items with natural spoken labels are not forced through numbering" — these are voice-reachable by name ("documents", "desktop", "downloads", "home"), not by number.
- **Directory tree** (below Quick Access, under a "THIS PC" label): one node per detected drive (from the existing `list_drives()`), each expandable to show its immediate children, each of those expandable in turn — lazy-loaded one level at a time via the existing `list_directory()`, not a full recursive walk up front. Branch-line connectors (1px `--color-border`, indent 14px per level) show parent/child relationships, matching the reference pattern this was modeled on (a "branched menu" tree). The current folder's node is highlighted (filled `--color-accent` background, matching the Quick Access active-chip treatment) and behaves like an accordion header: clicking a folder navigates to it and auto-expands its immediate subfolders, and clicking the already-current folder again toggles it collapsed/expanded (without navigating).
- **Motion — modeled on [reactbits.dev's Branched Menu](https://reactbits.dev/micro/branched-menu), hand-built in plain CSS/JS rather than adopted as a dependency.** That component is a React component (part of the React Bits library) and pulling it in would mean adding React, a bundler, and almost certainly Tailwind to a frontend that today is deliberately plain HTML/CSS/vanilla JS with no build step — a real architecture change the PRD's lightweight/$0/offline framing argues against, just to get one widget's animation. The two effects worth keeping are reproducible without any of that:
  - **Expand/collapse:** a node's children wrapper animates via the `grid-template-rows: 0fr` → `1fr` technique (`display: grid` on the wrapper, the row content inside a single child with `overflow: hidden`, transitioning `grid-template-rows` rather than `height`/`max-height` so it works without a measured pixel value) over 260ms, easing `cubic-bezier(0.34, 1.56, 0.64, 1)` (a slight overshoot approximating the spring feel of the original, without a physics/animation library). The row's chevron rotates 90deg over the same duration/easing.
  - **Accent line tracing to the current selection:** each branch connector (the 1px `--color-border` vertical line) gets a second, absolutely-positioned overlay line in `--color-accent`, starting at 0 height. On navigation, JS walks the ancestor chain of the newly-current folder and toggles an `.is-active-path` class on exactly those connector segments (removing it from any segment no longer on the path); each active segment transitions its overlay to full height over ~220ms with a per-depth `transition-delay` (e.g. `depth * 60ms`) so the line visibly travels downward one branch at a time rather than every segment lighting up simultaneously.
  - **Respect `prefers-reduced-motion: reduce`:** both effects collapse to an instant state change (no transition) under that media query — expand/collapse and the active-path highlight still work, they just don't animate.
- **The OS user-profile folder segment is never shown or built as a tree node.** Earlier drafts of this redesign showed `Local Disk (C:) > Users > <account> > Documents` — both guessing at the real folder name (wrong) and exposing an implementation detail no non-technical user needs. The tree goes straight from a drive to that drive's real top-level folders; Quick Access is how a user reaches their own profile folders (Documents, Desktop, Downloads, Home) without ever seeing the OS path segment that contains them.
- **"Other Drives" is gone as a special row.** Every detected drive is now always visible as a top-level tree node (This PC's direct children), not one buried inside a synthetic listing reached by picking a number — this was the actual fix for the original "I can't find other drives" report. `list_drives()`'s existing backend logic (Windows: A–Z checked by existence; Mac: `/` + `/Volumes`) is reused as-is; only where its results surface in the UI changes.
- **Breadcrumb, above the two panes:** `This PC › <Drive> › <folder path>`, collapsing the OS profile-folder segment the same way the tree does — when the current folder is under the user's home directory, the breadcrumb skips straight from the drive to whatever comes after the home directory (so Documents reads as `This PC › Local Disk (C:) › Documents`, never `› Users › <name> ›`). The current folder is the breadcrumb's own last segment and must be visually distinct from the ancestor segments it follows: bold, primary-colored text (no filled badge or chip) so "you are here" reads at a glance against the plain, muted ancestors. Ancestor segments stay plain, clickable text.
- **Up is no longer a footer button.** Navigating to an ancestor folder now happens by clicking it directly in the breadcrumb or the tree — strictly more capable than "one level at a time," and the actual replacement for the old Up button, not a removal of the capability. The `DIALOG_UP` voice intent (router.py) and `goUp()` (file-browser.js) remain — "go up" still works by voice — there is simply no visible button for it anymore. One behavior was added so voice-only users are not stranded: from a drive root, where there is no parent folder, "go up" lists the drives themselves in the right pane (breadcrumb `This PC`, rows numbered like any folder's, so a spoken number picks a drive). The tree has no spoken labels, so this is the voice route to another drive. While that list shows, "save here" does nothing, since there is no folder to save into.
- **Footer now holds exactly Cancel + the primary action** (Open, or Save for Save-As), not three visually-identical buttons. Cancel is a plain outline/secondary button (`.btn-secondary`, unchanged), separated from Open/Save (`.btn`, unchanged) by spacing alone — no new ambiguity, because Cancel-vs-primary-action is a standard, universally-understood pair; the ambiguity that mattered was specifically Up looking like Cancel, and Up is gone from the footer.
- **Right pane (folder contents):** unchanged from the original spec below — same numbered rows, same folders-then-PDFs ordering, same voice picking by number.
- **Search field** (top of the right pane): filters the current folder's visible rows client-side as you type — no new backend call, no voice grammar change; existing `DIALOG_PICK`/number-by-index behavior is unaffected since filtering only hides rows, it does not renumber the ones that remain hidden mid-list (renumber visible rows sequentially, the same way any filtered list here would, so a spoken number always matches what's on screen).

- **Rows (right pane):** folders first, then PDF-only files, each alphabetically — every row numbered and visible for as long as the dialog is open (unlike the Recent grid's opt-in picker toggle), since nothing here has a natural spoken label the way "recent" does.
- **Platform note — drives:** every drive/volume `list_drives()` detects is now a permanent top-level tree node rather than a row behind a picker detour.
  - **Windows:** every drive letter A–Z that actually exists, checked directly rather than depending on `psutil` or `os.listdrives()` (the latter needs Python 3.12+; this project's floor is 3.11).
  - **Mac (and other POSIX platforms):** there's no drive-letter concept, so this surfaces `/` plus whatever is mounted under `/Volumes`, except an entry that resolves back to `/` itself (`/Volumes/<boot volume>` is normally a symlink to it), so the boot volume is not listed twice.
- **Hidden folders:** dot-folders everywhere, and on Windows folders carrying the hidden attribute (`$RECYCLE.BIN`, `System Volume Information`), are left out of the tree and the right pane. The system attribute alone does not hide a folder — customised user folders (desktop.ini) carry it and must stay browsable.

## Accessibility baseline

Every interactive element is a real `<button>`/`<a>`/`<input>`, never a clickable `div` — required for keyboard focus and screen-reader compatibility, and a direct consequence of the "voice is an accelerator, not a replacement UI" requirement: the app must be fully operable by keyboard and mouse alone.

## Contrast & token-usage clarifications (resolved during implementation)

These resolve specific ambiguities found when verifying implementation against this doc and the mockup file. Documented here so the same judgment call doesn't need re-litigating on the next similar component.

- **Status pills (e.g. the "Listening" indicator) decouple color-identity from text-legibility.** The amber voice-focus color (`#C97A3D`) stays as the pill's fill/border/dot — its "this is the listening state" signal — but the label text itself uses the same high-contrast color already used for the idle "VOICE READY" state, not amber-on-amber. Measured: no single amber-text/amber-background pairing clears WCAG AA (4.5:1) across all three themes, so the pill's identity color and its text color are intentionally independent values, not one derived from the other. Apply this same pattern to any future status pill/badge.
- **Informational/explanatory captions use `--color-text-muted`, not `--color-text-very-muted`**, even where the mockup's pixels sample as the very-muted token. Very-muted (`#9A968E`) is reserved for genuinely decorative or low-priority metadata (timestamps, chapter labels). Text that explains something to the user — onboarding step-asides, mode-card notes, any copy serving the PRD's "usable by someone without technical knowledge" requirement — uses the muted token instead, which passes AA. If a future caption's informational value is genuinely low (truly decorative), very-muted is still fine — judge by function, not by which token the mockup happened to sample at.
- **All focus indicators use 2px outline + outline-offset**, including text inputs. No exceptions for a lighter-weight treatment (e.g. a border-color swap on focus) — consistency of the focus pattern across every control matters more than any single component's visual weight.

## Design rationale notes

Each mockup screen carries inline captions naming the UX heuristic behind specific choices (Hick's Law, Von Restorff's Law, Miller's Law, Fitts's Law, Jakob's Law, Postel's Law / error prevention, Peak-End Rule, Aesthetic-Usability effect). Worth preserving as inline code comments near the relevant UI logic when implementing, not just in this doc — e.g. the save-dialog default is intentionally the non-destructive option specifically so "don't ask again" can never silently arm a destructive default.

## Naming placeholder

UI copy currently reads "Lector" throughout the mockups. Per the PRD, this is a working name, not finalized — search-and-replace before any public release once a final name is chosen.
