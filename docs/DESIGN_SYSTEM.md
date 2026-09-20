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
| Accent darker (link hover, badge text on tinted backgrounds) | `#2C463F` |
| Voice-focus / "listening here" indicator | `#C97A3D` |
| Highlight background | `#F7DE7A` |
| Text-selection wash (drag in progress, before the highlight is applied) | `rgba(62, 98, 89, 0.42)` — the accent at 42%, blended multiply |
| Destructive hover (e.g. window close button) | `#C0453B` |
| Border / divider (light) | `#D2CCC2` |
| Border / divider (subtle) | `#D8D3C9` |
| Border / muted decorative | `#A8A6A0` |

**Why the focus indicator and highlight color are different colors, on purpose:** the voice-scan focus indicator (amber/clay, `#C97A3D`) and the highlight fill (soft yellow, `#F7DE7A`) must stay visually distinct — one means "the app is currently listening/pointing here," the other means "this text is already highlighted." Collapsing them into one color was flagged early as a usability risk and deliberately avoided throughout every iteration.

**The selection wash is a third state in that same family, and follows the same rule:** it means "this is what you are *about* to highlight" — text the reader has under an in-progress drag, which is not yet an annotation and may never become one (releasing outside, or pressing Escape, discards it). It is the accent green at low opacity rather than a fourth hue, which keeps the three states legible as a sequence — selecting (green wash) → highlighted (yellow) → being read aloud to/listened at (amber) — without introducing a color the palette doesn't already contain. It is deliberately *not* themed per light/dark/sepia: it sits on the rendered PDF page, which is the document's own paper and stays white in every theme, so a per-theme value would be tuned against a background that never changes.

**Highlighting is two visible beats, not one**, matching Adobe Reader and other desktop annotators: while the mouse is held, the words under the drag are washed in the selection color, snapping to whole words and running edge-to-edge across each full line it covers; only on release does that same shape become the yellow highlight. The selection preview and the written annotation are composed identically (one box per line of text, not one per word), so the shape never changes under the cursor at the moment of release.

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

## Typography

- **Reading/body text:** `Charter, Georgia, serif` — a serif face for long-form reading content.
- **UI chrome:** `system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Helvetica, sans-serif` — native system font, not a webfont, keeping the app lightweight and OS-native-feeling.
- No Inter/Roboto/Arial as a deliberate choice, no emoji-as-icons — all icons are real stroke SVGs.

## Layout patterns

- **Home/Library:** left sidebar navigation (Recent / All PDFs / Favorites / Settings) + main content area with search, a primary "Open PDF" action, and a card grid of recent files (thumbnail + filename + relative timestamp), capped at 10.
- **Reading view:** left icon toolbar rail (highlight tool, search-in-document, zoom in/out, book/strip layout toggle, theme cycle, "What can I say?") + main reading pane. Book layout centers a bounded page card; strip layout removes the card boundary and flows continuously with a subtle page-break marker between pages.
- **Zoom control:** the topbar's zoom percentage is a button — clicking it opens a small popover with a slider for continuous zoom, in addition to the toolbar rail's step-by-25% zoom in/out buttons. Not present in the original mockups; added after implementation when stepped-only zoom proved awkward for fine adjustment.
- **Modals/dialogs** (save confirmation, "What can I say?" reference): centered card over a dimmed and slightly blurred background, not a full-screen takeover — keeps context visible.
- **Settings:** single-column sections (Theme, Voice activation, Save behavior), not tabs — small enough surface area that tabs would add navigation overhead for no benefit.

## Accessibility baseline

Every interactive element is a real `<button>`/`<a>`/`<input>`, never a clickable `div` — required for keyboard focus and screen-reader compatibility, and a direct consequence of the "voice is an accelerator, not a replacement UI" requirement: the app must be fully operable by keyboard and mouse alone.

## Design rationale notes

Each mockup screen carries inline captions naming the UX heuristic behind specific choices (Hick's Law, Von Restorff's Law, Miller's Law, Fitts's Law, Jakob's Law, Postel's Law / error prevention, Peak-End Rule, Aesthetic-Usability effect). Worth preserving as inline code comments near the relevant UI logic when implementing, not just in this doc — e.g. the save-dialog default is intentionally the non-destructive option specifically so "don't ask again" can never silently arm a destructive default.

## Naming placeholder

UI copy currently reads "Lector" throughout the mockups. Per the PRD, this is a working name, not finalized — search-and-replace before any public release once a final name is chosen.
