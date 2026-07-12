# Deck Art Studio - Kanban Board

## Backlog

- [ ] BUG: Planeswalker frame style drops adventure/split half rules text | Priority: P3 | Found: 2026-07-06 | Owner: unassigned
  - Found during the split-header full-style review. Pre-existing on main (verified by rendering Murderous Rider // Swift End in the planeswalker style from main — only the creature half's Lifelink/dies text renders in the loyalty-style ability bands; the Swift End adventure half is silently omitted).
  - Root cause area: the planeswalker style's text renderer (`_create_pw_frame_text_svg` in `card_frame_renderer.py`) renders `card.oracle_text` into loyalty-style ability bands and never checks `card.split_faces`, unlike the other styles which route through `_render_split_rules_svg`.
  - Edge case: user must deliberately pick the planeswalker frame for an adventure/split card, hence P3.

- [ ] Alt Layouts: transform/MDFC face-indicator pips on frames | Priority: P3 | Created: 2026-07-03 | Owner: unassigned
  - Polish leftover from the now-complete "Support Alternative Card Layouts" epic (see Done). A dedicated transform-indicator icon on the frame (front/back face indicator). Back-face composites currently render with the standard frame for the back face's own card data; a face-indicator icon was deferred out of Phase 1.

- [ ] Alt Layouts: DRY cleanup pass for face-expansion + shared helpers | Priority: P3 | Created: 2026-07-03 | Owner: unassigned
  - Cleanup leftover from the now-complete "Support Alternative Card Layouts" epic (see Done); flagged by the Phase 2 review. De-duplicate the face-expansion logic (duplicated across paths) and consolidate the triple-copied download/slug helpers into a single shared implementation.

- [ ] Alt Layouts: Frame Designer preview for rotated splits | Priority: P3 | Created: 2026-07-03 | Owner: unassigned
  - Documented known limitation from Phase 3b+ (v1.38.0). The Frame Designer preview for rotated *splits* still falls back to the column layout; the final composite is authoritative. Bring the designer preview to parity with the rotated per-half composite (as was done for battle fronts in v1.38.0).

- [ ] Alt Layouts: clarify "PIP cards" with requester | Priority: P3 | Created: 2026-07-03 | Owner: unassigned
  - Open question owed by the external requester, unresolved as of the epic's completion. Every other example the friend gave is covered by the shipped epic (DFCs, adventures, rooms, battles, splits); "PIP cards" remains ambiguous (likely Kamigawa flip cards). Need the requester to clarify before scoping any flip-layout work.

- [ ] Frame Designer UX polish + validation harness | Priority: P2
  - Carries forward the two unfinished work items from the Frame Editor Overhaul (merged as v1.34.0)
  - (a) Frame Designer UX overhaul: style gallery with live thumbnails, intuitive color + gradient controls, art pan/zoom, per-card and apply-to-all UX
  - (b) Visual validation harness: browser-vs-Python composite parity screenshots across styles × colors (resemblance partially covered by per-frame 0-diff chrome checks in tools/card_quality_check.py)

- [ ] Selection UX Improvements | Priority: P2
  - Hover-reveals checkbox on card tiles for easier multi-select
  - Shift+click range select for selecting contiguous cards
  - Quick-select shortcuts: "All without art", "Errors only"
  - Better visual affordance for selectable state

- [ ] Generation Feels Alive | Priority: P3
  - Cards shimmer in grid during generation (beyond border animation)
  - Larger progress overlay option showing generation steps
  - Visual feedback that makes the generation process feel dynamic

- [ ] Collapse Art Prompts by Default | Priority: P2
  - Show one-line truncated summary of art prompt in card detail
  - Click to expand full prompt text
  - Reduces visual noise in the detail panel

- [ ] Typography + Spacing Pass | Priority: P3
  - Consistent section headers across all panels
  - More breathing room in detail panel between sections
  - Muted card labels and better visual hierarchy
  - Note: PR #72 (fix/style-panel-spacing-and-progress) partially addresses this — adds breathing room to Style Reference section
  - Note: PR #81 (v1.12.0) further addresses this — cleaner hierarchy in card detail panel, status pip, mana symbol rendering

- [ ] Art Comparison View | Priority: P3
  - A/B slider or side-by-side view for before/after regeneration
  - Compare current art against previous versions
  - Useful for evaluating prompt changes

- [ ] Custom Flavor Text Generation | Priority: P1 | Owner: drew-valentine
  - Future feature: Generate custom themed flavor text for MTG cards using LLM, driven by inspiration image theme
  - Tone: light, witty, cheeky
  - Supports all/selected/individual card generation
  - Subtasks:
    - [ ] LLM flavor text generation function (OpenAI + Ollama)
    - [ ] Backend API endpoints with background job processing
    - [ ] Frontend UI (toolbar button, detail panel display, inline edit)
    - [ ] Auto-recomposite after flavor text update

## Refinement

- [ ] README Open-Source Prep: Accuracy and Completeness Update | Priority: P1 | Created: 2026-04-03 | Owner: drew-valentine
  - Review and update README.md to be accurate and complete for open-source publication
  - Audit found 5 key issues that need to be addressed before the repo is publicly presentable
  - Subtasks:
    - [ ] Fix outdated Mermaid diagrams: style tokens schema changed (removed cel-shaded, flat colors, line_style, proportions), CLIP prompt ordering is now subject-first
    - [ ] Update Project Structure section: missing color_transfer.py, build_pips_from_mana.py, fetch_mtg_fonts.py, tests/, static/, .github/
    - [ ] Add license badge to top of README
    - [ ] Add Contributing / Development section for open-source contributors (branching model, pre-commit hooks, test suite, CI pipeline)
    - [ ] Fix style token examples in diagrams: remove references to removed concepts (cel-shaded, flat colors, line_style, proportions) and reflect current schema
  - Acceptance criteria:
    - Given a new contributor clones the repo, when they read the README, then the project structure matches reality and all referenced files exist
    - Given a contributor looks at the Mermaid diagrams, when they follow the style token flow, then the schema and examples match the current codebase
    - Given someone views the README on GitHub, when they see the top of the file, then a license badge is visible

## Ready

## In Progress

## In Review

## Done

- [x] Always-on filter bar | Priority: P3 | Completed: 2026-07-12 | Owner: drew-valentine
  - Squash-merged to main via PR #21 (commit 4b09261); tagged v1.44.1 (patch/UI bump, released 2026-07-12).
  - The deck filter strip is now always visible — removed the header Filter toggle button and its dead styles/handler.
  - Moved the "Add Card" and "Add Card Back" actions out of the deck overflow menu into the filter bar as buttons (they were unintuitive to find in the menu).
  - Active-filter cue now highlights the Clear button.
  - Validation: 319 pytest tests green; validated in-browser via Playwright.

- [x] Global cross-deck generation queue | Priority: P1 | Completed: 2026-07-10 | Owner: drew-valentine
  - Branch: `feat/global-generation-queue` merged via PR #20 (squash-merged to main as commit a0bac32)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/20
  - Tagged: v1.44.0 (released 2026-07-10) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.44.0
  - Minor bump (new feature).
  - Summary: A single global in-memory generation queue that sits ABOVE decks. Enqueue is instant and non-blocking; a single FIFO worker (with manual bump-to-top) drains it while the user critiques and switches decks freely — each job carries its own deck id and runs against that deck regardless of the UI's active deck. Foundational fix for the blocking critique loop and the deck-switch-stops-rendering problem.
  - Implementation:
    - New `generation_queue.py` module — `Job` model, background worker, unit-testable in isolation.
    - Deck-context parameterization of the art / prompt / flavor generation functions so each job renders against its own deck rather than the globally-active deck.
    - Right slide-in queue drawer UI: Running / Queued / Recent sections with cancel / bump / pause / clear controls and click-a-row-to-open-card.
    - Queue management endpoints for enqueue, status, and job actions (cancel / bump / pause / resume / clear).
    - Removed the 409 deck-switch guard (switching decks no longer aborts in-flight rendering); deck-delete drains that deck's jobs; removed enqueue toasts and the single-model Models menu.
  - Decisions: in-memory only (no persistence across restart); right-side drawer; FIFO ordering with manual bump.
  - Acceptance criteria (Given/When/Then) — all met:
    - [x] Given a generation is requested, when it is enqueued, then the call returns immediately (non-blocking) and a background worker drains the queue FIFO.
    - [x] Given a job is running, when the user switches the UI to a different deck, then the job keeps running and writes its output to the correct (originating) deck.
    - [x] Given queued jobs, when the user bumps / cancels / pauses / resumes / clears the queue, then the queue state and worker behavior update accordingly.
    - [x] Given `generation_queue.py`, when the unit suite runs, then the Job model and worker are covered in isolation.
  - Code review: a high-effort review surfaced 10 findings — ALL fixed and validated: cross-deck `_cancel_single`/status bleed, cancel-not-cancelling-the-queue-job, `finally` masking failures as complete, status-sink writing after deck switch, hidden cancel button, stale flavor recomposite, delete-deck race, prompt retry-with-backoff, dead progress endpoints, per-card registry read.
  - Validation status: **PASSED (2026-07-10)** — 319 unit tests green; Playwright with real FLUX confirmed non-blocking enqueue, an art job kept running and wrote to the correct deck after switching the UI to another deck, and bump/cancel/pause/resume/clear verified, 0 console errors. CI green on PR #20; released as v1.44.0.

- [x] Full-codebase review: document all findings, fix in a single PR | Priority: P1 | Completed: 2026-07-08 | Owner: drew-valentine
  - Branch: `chore/full-codebase-review` merged via PR #19 (squash-merged to main as commit 5b41975)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/19
  - Tagged: v1.43.0 (released 2026-07-08) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.43.0
  - Minor bump.
  - Goal: A comprehensive, whole-repo review that surfaces bugs, risks, and cleanup opportunities across every major surface, triages/verifies the findings, documents them, and lands all confirmed fixes in a single PR for Drew's review.
  - Approach: 8 parallel scoped reviews, each owning a slice of the codebase, so coverage is broad and the reviews can run concurrently:
    1. `deck_studio.py` backend — slice 1 (routes/API, generation orchestration)
    2. `deck_studio.py` backend — slice 2 (card management, deck/version/persistence, security helpers)
    3. Inline frontend JS (the embedded HTML/CSS/JS template in `deck_studio.py`)
    4. `card_frame_renderer.py` (SVG/image frame compositing, per-style chrome, text rendering)
    5. MLX / worker stack (`local_image_generator.py`, `mlx_llm.py`, `flux_worker.py`, `mlx_worker.py`, `gpu_coord.py`, `backend_config.py`)
    6. Prompt / vision / Scryfall utilities (`prompt_generator.py`, `vision_analyzer.py`, `fetch_scryfall_art.py`, `fetch_flavor_text.py`, and related helpers)
    7. Browser extension (`extension/` — manifest, background/service worker, content, db, popup, import)
    8. CI workflows + tests (`.github/workflows/*.yml`, `tests/`, pre-commit hooks)
  - Deliverables:
    - Findings triaged and verified (confirm real vs. false positive before acting).
    - All findings documented in the PR #19 description (kept OUT of the repo per Drew's request — no tracked docs file).
    - All confirmed fixes landed in a single PR for Drew's review.
  - Outcome:
    - 8 scoped review passes across the whole codebase; all verified findings fixed in one PR.
    - Findings documented in the PR #19 description (with a Deferred section for out-of-scope items).
    - Highlights: CRITICAL auto-release approver-gating fix, SVG-injection escaping, cross-deck prompt contamination fix, atomic JSON persistence, and the frozen-poller fix.
  - Validation: PASSED 2026-07-08 — 294 tests (254 baseline + 40 new: frame/util/mlx/backend regression tests), Playwright browser smoke test (0 console errors, frame designer + version/prompt round-trip intact), and curl checks on the input-hardening endpoints.
  - Acceptance criteria (Given/When/Then):
    - [x] Given the 8 scoped reviews, when each completes, then its findings are captured (with severity + file/line context) in the PR #19 description.
    - [x] Given a raw finding, when it is triaged, then it is verified as a real issue (or marked false-positive with rationale) before any fix is attempted.
    - [x] Given the set of confirmed fixes, when they are implemented, then they land together in a single PR on `chore/full-codebase-review` for Drew's review.
    - [x] Given the validation requirements, when fixes touch generation/UI, then they are verified per CLAUDE.md (Playwright + local FLUX) before the PR is marked ready.

- [x] BUG: Version prompt off-by-one — versions carried the NEXT generation's prompt | Priority: P2 | Completed: 2026-07-07 | Owner: drew-valentine
  - Branch: `fix/version-prompt-off-by-one` merged via PR #18 (squash-merged to main as commit cf60736)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/18
  - Tagged: v1.42.1 (released 2026-07-07) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.42.1
  - Patch bump (bug fix).
  - Follow-up to the v1.41.0 prompt-versioning feature (PR #15) — the snapshotted prompt was off by one generation.
  - User repro: generate → save → repeat; restoring version n-1 showed the CURRENT generation's prompt instead of the prompt that produced that older art.
  - Root cause: prompts were snapshotted at archive time, but archiving is lazy (it runs right before the NEXT generation), by which point the prompt had already been edited for the new art — so the version captured the new prompt, not the one that made the archived art.
  - Fix: the editable prompt is now stamped into the art's `.meta.json` at generation time (`card_prompt`), and the archiver reads the prompt from there. Archive-time capture stays as a fallback for pre-stamp art (art generated before this fix has no stamped `card_prompt`).
  - Acceptance criteria (Given/When/Then):
    - [x] Given a card is generated with prompt A, when the art is saved, then prompt A is stamped into that art's `.meta.json` (`card_prompt`) at generation time.
    - [x] Given the prompt is edited to B and the card is regenerated, when the older art is archived, then the archiver reads the stamped `card_prompt` (A) from `.meta.json` rather than the current live prompt (B).
    - [x] Given pre-stamp art with no `card_prompt` in `.meta.json`, when it is archived, then the archive-time capture is used as a fallback (no regression for existing versions).
    - [x] Given the user restores the older art version, when restore completes, then the panel and `art_prompts.json` return to prompt A.
  - Validation status: **PASSED (2026-07-07)** — 254 unit tests pass (3 new), plus a real two-generation FLUX repro on Okaun: the archived version carried prompt A while the live prompt was B, and the browser restore returned everything to A.

- [x] Separate rules text color from heading text color | Priority: P2 | Completed: 2026-07-07 | Owner: drew-valentine
  - Branch: `feat/separate-rules-text-color` merged via PR #17 (squash-merged to main as commit 3c12938)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/17
  - Tagged: v1.42.0 (released 2026-07-07) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.42.0
  - Minor bump (new feature): headings and rules-body text can now be colored independently.
  - User story: The single Colors > Text override currently drives ALL card text. Users want the headings (title / type line / P/T) and the rules-body text colored independently — e.g. white headings with black rules text — instead of every text element sharing one color.
  - Implementation:
    - New `rules_text` color override added alongside the existing `text` override.
    - `text` keeps driving the headings (title / type / P/T) and remains the fallback for all text, so existing saved decks render unchanged (no `rules_text` → falls back to `text`).
    - `rules_text` wins for the rules body wherever it renders: main rules text, flavor text, split columns, planeswalker abilities, saga chapters, and battle rules.
    - Shared `_text_color_overrides` helper swept through every text creator so the fallback logic lives in one place.
    - New "Rules" color row added to the Frame Designer, wired through gather / populate / visibility / live-preview.
    - Every style's `controls.colors` gained `rules_text`.
  - Acceptance criteria (Given/When/Then):
    - [x] Given a card with a `text` override but no `rules_text` override (existing saved decks), when the composite is rendered, then all text (headings + rules body) renders in the `text` color — unchanged from before.
    - [x] Given a card with distinct `text` and `rules_text` overrides (e.g. white text / black rules_text), when the composite is rendered, then the title / type / P/T render in `text` and the rules body renders in `rules_text`.
    - [x] Given the rules body renders as flavor text, split columns, planeswalker abilities, saga chapters, or battle rules, when `rules_text` is set, then each of those rules surfaces honors `rules_text` (falling back to `text` when unset).
    - [x] Given the Frame Designer, when the user edits the new Rules color row, then gather / populate / visibility / live-preview all reflect the `rules_text` override for every style whose `controls.colors` includes it.
  - Validation gate: Playwright browser verification (headings vs rules colored independently across the affected layouts) + full pytest suite.
  - Validation PASSED 2026-07-07:
    - 251 unit tests green, 4 new: helper fallback chain, IKO heading/rules divergence, text-only back-compat, SVG-style rules color.
    - Playwright browser verification on Kykar (Auto off, Text white, Rules dark) — live canvas shows white title / type / P/T with dark rules body.

- [x] Version the art prompt with each art version; restore it on revert | Priority: P2 | Completed: 2026-07-07 | Owner: drew-valentine
  - Branch: `feat/version-prompts` merged via PR #15 (squash-merged to main as commit 0373bc4)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/15
  - Tagged: v1.41.0 (released 2026-07-07) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.41.0
  - Minor bump (new feature): the editable card prompt now travels with each art version and is restored on revert.
  - User story: When card art is versioned (archived before regeneration/recomposite), the editable card prompt (`art_prompts.json` / `prompts_map`) used for that art should be snapshotted with the version. Restoring an older art version also restores its prompt, so users can iterate on prompts and revert with confidence — the prompt and the art it produced always travel together.
  - Problem: Today the version manifest snapshots the art PNG but not the prompt that produced it. After a user edits the prompt and regenerates, reverting to an older art version leaves the current (edited) prompt in place, so the restored art and the visible prompt no longer match.
  - Planned implementation:
    - Consolidate the duplicate archive functions — `archive_current_art` delegates to `_archive_art` (per the DRY parallel-paths lesson: make one path call the other rather than maintain two implementations; verify quality empirically).
    - Add `card_prompt` to `version_info` in the version manifest so the prompt is snapshotted alongside the archived art.
    - On `revert_to_version`, restore `prompts_map` + `cards_db` prompt from the snapshot and persist (write back to `art_prompts.json`, merge-not-overwrite).
    - Surface the archived prompt in the version UI so users can see the prompt tied to each art version.
  - Acceptance criteria (Given/When/Then):
    - [x] Given a card whose art is about to be archived (regenerate/recomposite), when the version is created, then `card_prompt` is captured in that version's `version_info`.
    - [x] Given an older art version with a snapshotted prompt, when the user reverts to it, then `prompts_map` and `cards_db` are updated to the archived prompt and `art_prompts.json` is persisted (merged, not overwritten).
    - [x] Given the archive path, when both `archive_current_art` and `_archive_art` are exercised, then they share a single implementation (no divergent duplicate logic).
    - [x] Given the version history UI, when a user views an art version, then the prompt associated with that version is visible.
  - Implementation note: the duplicate archive functions were consolidated — `archive_current_art` now delegates to `_archive_art`.
  - Validation status: **PASSED** 2026-07-07 — 247 unit tests (6 new: prompt snapshot, restore round-trip, flip-forward archiving, pre-feature manifest compatibility, back-face keys, empty-prompt archive), plus the full browser flow via Playwright on Okaun in heads-i-win (archived version with prompt A, changed to B, restored via version modal — modal showed archived prompt, panel and `art_prompts.json` returned to A, B auto-archived as newest version).

- [x] BUG: Showcase style — rules box missing the black outer keyline the bars have | Priority: P2 | Completed: 2026-07-07 | Owner: drew-valentine
  - Branch: `fix/showcase-box-outer-keyline` merged via PR #16 (squash-merged to main as commit a04aab0)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/16
  - Tagged: v1.40.1 (released 2026-07-07) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.40.1
  - Symptom (user-reported with screenshot): the thin black border at the very outer edge of the gold trim is present around the title/type bars but stops where the rules box starts — inconsistent edging along the gold trim.
  - Root cause: the iko frame's bars are baked assets that include a black outer keyline outside their gold trim, but the DRAWN rules box strokes its outlines inward from the rect (PIL stroke behavior), leaving gold at the outermost edge with no black ring — so the bars and the box don't match.
  - Fix: draw a 2px black keyline ring on an expanded rect around the rules box (and the P/T plate), in both the base paint path and the two-color-gradient paint path. (Keyline weight tuned 3px → 2px per Drew's review before shipping.)
  - Acceptance criteria (Given/When/Then):
    - [x] Given a Showcase card, when the composite is rendered, then the black outer keyline runs continuously along the gold trim across the title bar, type bar, and rules box (no break where the box begins).
    - [x] Given a two-color-gradient Showcase card, when the composite is rendered, then the rules box and P/T plate carry the same 2px black outer keyline ring (gradient paint path covered).
    - [x] Given a P/T plate is present, when rendered, then it also carries the black outer keyline ring.
  - Validation status: **PASSED (2026-07-07)** — 241 tests pass; Kykar (the user's reported card) recomposited and the bar→box transition verified continuous at zoom. CI green on PR #16; released as v1.40.1.

- [x] Split-rules headers need contrast (adventure/split/room mini titles) | Priority: P2 | Completed: 2026-07-06 | Owner: drew-valentine
  - Branch: `fix/split-rules-header-contrast` merged via PR #14 (squash-merged to main as commit 10cc004)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/14
  - Tagged: v1.40.0 (released 2026-07-06) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.40.0
  - Minor bump (scope grew from a contrast fix into a feature): headers are now built from each frame's own chrome per half color, plus a split-card frame half-order fix.
  - Problem: On cards with two-column rules (Murderous Rider's adventure half, split/room halves), the mini name + type header renders as plain text on the rules box background. The real cards (e.g. SLD Murderous Rider reference) put the half's name on a dark contrasting banner with light text and the type line on a distinct lighter band — the current flat rendering has no separation and reads poorly.
  - Fix: Added contrasting header bands in `_render_split_rules_svg` — a dark name banner (light text) plus a lighter type band. Style-agnostic so it reads on both light parchment and dark stone/brushed panels.
  - Scope evolution (per iterative reference-checking): flat contrast bands → transparency/container fill → text-grid plaques → frame-chrome material per half color → an independent trader-review round (fixed mirrored split frame halves, ornament bleed, identity tints).
  - Acceptance criteria (Given/When/Then):
    - [x] Given a two-column rules card (adventure/split/room), when a half's header is rendered, then the half name sits on a dark contrasting banner with light text and the type line sits on a distinct lighter band.
    - [x] Given both light (parchment) and dark (stone/brushed) frame styles, when the split-rules header renders, then the header bands remain legible against each background.
  - Validation status: **PASSED (2026-07-06)** — 237 unit tests pass (2 new regression tests for the header bands). Murderous Rider and Smoky Lounge rendered in godzilla + crystal styles against the printed SLD reference; WYSIWYG designer browser-verified via Playwright; stored composite re-rendered. CI green on PR #14; released as v1.40.0.

- [x] Saga card layout — real saga frame structure | Priority: P2 | Completed: 2026-07-06 | Owner: drew-valentine
  - Branch: `feat/saga-card-layout` merged via PR #13 (squash-merged to main as commit 14b0a6f)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/13
  - Tagged: v1.39.0 (released 2026-07-06) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.39.0
  - Problem: Sagas (e.g. Urza's Saga) rendered with the normal card layout (art on top, rules crammed below), matching nothing about the printed saga frame.
  - Implemented: Dedicated portrait saga chrome in `card_frame_renderer.py` following the printed structure — title bar at top, a LEFT chapter panel with italic reminder text, roman-numeral chapter badges on a rail with dividers (combined "I, II" chapters stack their badges), a transparent RIGHT art window, and a full-width type bar at the BOTTOM. Art stays full-bleed under the chrome so pan/zoom, the WYSIWYG designer, printing, and the edhplay extension all work unchanged.
  - Chapter parsing: extracted from Scryfall oracle text ("I — ..." em-dash chapter lines after the parenthesized reminder text).
  - Scope expansion (per user request, committed as 791754a on the same PR #13 branch): sagas now honor **every** frame style, not just one chrome. Per-style sliced saga chrome for image-mode styles reuses the battle band metadata — the chapter panel is built from each style's own rules-box texture with that style's rules text color. SVG styles keep the dedicated saga chrome; `clean` stays frameless.
  - Wiring: `render_frame_layer` (chrome + text baked), `render_text_overlay` (empty — text is baked into the frame layer), `composite_card`, and `composite_card_preview`.
  - Acceptance criteria (Given/When/Then):
    - [x] Given a saga card (e.g. Urza's Saga), when the composite is rendered, then it uses the dedicated portrait saga chrome (top title bar, left chapter panel with italic reminder text, roman-numeral chapter badges on a divided rail, transparent right art window, full-width bottom type bar) — not the normal art-on-top/rules-below layout.
    - [x] Given a saga with a combined chapter (e.g. "I, II — ..."), when the chapter rail is drawn, then the combined chapters stack their badges on the shared rail segment.
    - [x] Given a saga's Scryfall oracle text, when chapters are parsed, then the em-dash chapter lines after the parenthesized reminder are extracted into their numbered chapters.
    - [x] Given the saga chrome renders, when art is composited, then the art stays full-bleed under the chrome so pan/zoom, the WYSIWYG designer, printing, and the edhplay extension work unchanged.
    - [x] Validation gate: 235 unit tests pass (new per-style saga regression test plus the 5 original saga tests — chapter parser incl. combined chapters, saga detection, geometry pixel checks, composite smoke); Urza's Saga and History of Benalia rendered against the real card reference; browser-verified the WYSIWYG designer on Urza's Saga in `heads-i-win` matches the stored composite; export manifest picks up the saga for the extension.
    - [x] Given the scope expansion, when a saga is rendered under any of the 11 frame styles, then it honors that style's chrome (image-mode styles get per-style sliced saga chrome from their own rules-box texture and rules text color; SVG styles keep the dedicated saga chrome; `clean` stays frameless).
  - Validation status: **PASSED (2026-07-06)** — 235 unit tests pass (new per-style saga regression test added). Urza's Saga contact sheet rendered across all 11 styles; live style switching browser-verified in the WYSIWYG designer; the stored composite was re-rendered with the deck's crystal default. Also rendered Urza's Saga + History of Benalia against real card references; export manifest includes the saga for the extension.

- [x] BUG: Showcase (godzilla) frame — manual Colors > Border only recolors the rules box; title/type bars keep baked accent color | Priority: P1 | Completed: 2026-07-05 | Owner: drew-valentine
  - Branch: `fix/showcase-bar-border-color` merged via PR #12 (squash-merged to main as commit ffb60c4)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/12
  - Tagged: v1.38.3 (released 2026-07-05) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.38.3
  - Symptom: On the Showcase (godzilla) frame, setting a manual Colors > Border override only recolors the rules-box border. The title bar and type bar keep the baked-in accent color (e.g. blue on a blue card), so they mismatch the recolored rules border — visible in BOTH the WYSIWYG designer preview and the final composites.
  - Root cause: The rules-box border is drawn dynamically in `_compose_image_frame_base` (honors `color_overrides.border`), but the title-bar and type-bar outlines are baked into the per-color iko frame PNG assets and are never recolored.
  - Fix: Derive an accent mask from the `u.png` asset (unambiguous blue chroma; all iko frames share identical geometry), then composite the override color over any frame's accent pixels — preserving shading / anti-aliasing — before the type-bar relocation.
  - Acceptance criteria (Given/When/Then):
    - [x] Given a Showcase (godzilla) card with a manual Colors > Border override, when the WYSIWYG designer preview renders, then the title bar, type bar, and rules-box border all show the override color (not the baked accent).
    - [x] Given the same card, when the final composite is rendered, then the title bar, type bar, and rules-box border all match the override color (parity with the designer preview).
    - [x] Given a Showcase card on ANY color's iko frame (WUBRG/gold/artifact/colorless/land), when the override is applied, then only the accent chrome recolors — art, shading, and anti-aliasing are preserved (no flat/aliased fill).
    - [x] Given no manual Border override, when the card renders, then the frame keeps its baked accent color (no regression).
    - [x] Validation gate: Playwright browser verification on a blue Showcase card — apply a Border override, confirm the title/type bars and rules border match in both the designer preview and the saved composite; full pytest suite passes.
  - Validation status: **PASSED (2026-07-05)** — 229 unit tests pass. AC3 is now covered by a new parametrized regression test rendering all 8 frame variants (WUBRG, gold, artifact, land) through `render_frame_layer` with the border override and asserting gold trim (committed as 4ede8ba on PR #12); AC4 is covered by the existing `test_no_override_keeps_baked_trim` regression asserting the blue card's trim stays blue without an override. Playwright browser verification on Cyclonic Rift (demo-alela) confirmed matching gold trim on the title bar, type bar, and rules box against the gold swatch; the `/api/preview-frame` composite path renders identically.

- [x] BUG: Frame editor Auto color choice ignored in saved composites (title renders black instead of auto/white) | Priority: P1 | Completed: 2026-07-05 | Owner: drew-valentine
  - Branch: `fix/frame-save-auto-colors` merged via PR #11 (squash-merged to main as commit eac0666)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/11
  - Tagged: v1.38.2 (released 2026-07-05) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.38.2
  - Merge also included the /code-review follow-up: keyless `color_overrides` now infer manual mode (legacy/API compat) + regression tests (219 tests passing).
  - Symptom: A card saved with the Frame editor's "Auto" color choice renders its title black in the final saved composite, even though the live WYSIWYG preview correctly renders auto/white. The saved composite silently inherits the deck default's manual black text.
  - Repro: Cyclonic Rift on the `demo-alela` deck (godzilla style, Auto colors). The deck default is classic style + manual colors with black title text. Live preview shows auto/white; saved composite shows black.
  - Root cause: `resolve_frame_settings()` in `card_frame_renderer.py` read `use_card_colors` only from the DECK settings (never the card's saved override) and merged the deck's `color_overrides` unconditionally. A card saved with Auto colors therefore inherited the deck default's manual black text in the final composite, while the live preview (which uses the live designer settings) rendered auto correctly.
  - Fix (implemented): `use_card_colors` resolves card-over-deck (card's saved override takes precedence over the deck default), and `color_overrides` only apply when the effective choice is manual (auto choice no longer inherits the deck's manual color overrides).
  - Acceptance criteria (Given/When/Then):
    - [x] Given a card saved with the Frame editor's Auto color choice on a deck whose default is manual colors, when the final composite is rendered, then the title renders with auto colors (matching the live preview), not the deck default's manual black text.
    - [x] Given a card saved with a manual color choice, when the final composite is rendered, then its manual `color_overrides` are honored (no regression).
    - [x] Given the saved composite and the live WYSIWYG preview for the same card, when both render, then the title color matches between them.
    - [x] Validation gate: Playwright browser verification on Cyclonic Rift (demo-alela deck) — save with Auto colors, confirm the saved composite title matches the live preview; full pytest suite passes.
  - Validation status: **PASSED (2026-07-04)** — 216 unit tests pass (3 new regression tests). Playwright browser validation on demo-alela confirmed Cyclonic Rift and Anointed Procession (both Auto colors) now composite with the correct white showcase titles matching the designer preview; Sol Ring (manual colors) re-rendered byte-identical as a control.

- [x] BUG: WYSIWYG Frame Designer live preview freezes after saving a per-card frame | Priority: P1 | Completed: 2026-07-03 | Owner: drew-valentine
  - Branch: `fix/frame-designer-live-preview-after-save` merged via PR #10 (squash-merged to main as commit 4f29fc2)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/10
  - Tagged: v1.38.1 (released 2026-07-03) — https://github.com/drew-valentine/deck-art-studio/releases/tag/v1.38.1
  - Symptom: In the WYSIWYG Frame Designer, the live preview canvas stops updating after the first "Save Frame" on a card. Subsequent live control changes (colors, gradients, art position, etc.) no longer reflect in the preview.
  - Root cause: `/api/render-frame-layer` and `/api/render-text-overlay` pass the live designer settings into `resolve_frame_settings()`, where the saved `card.frame_overrides` take precedence. After the first Save Frame, the saved style shadows all live changes, so the preview endpoints keep rendering the saved frame instead of the in-flight designer state — the canvas never updates.
  - Fix (implemented): Make the live designer settings authoritative for the preview endpoints (same treatment `text_overrides` already received), so live changes drive the preview even when `frame_overrides` are already persisted for the card.
  - Acceptance criteria — all met:
    - [x] Given a card with a previously saved per-card frame, when the user changes a live designer control (color/gradient/art position), then the preview canvas updates immediately to reflect the live change.
    - [x] Given the same card, when no live change is pending, then the preview still matches the saved frame.
    - [x] Validation gate: Playwright — save a per-card frame, then change a live control and confirm the preview canvas updates; full pytest suite passes.
  - Validation status: **PASSED** — 213 unit tests pass + Playwright browser verification confirmed the preview canvas updates after saving a per-card frame. CI green on PR #10; released as v1.38.1.

- [x] EPIC: Support Alternative Card Layouts (Scryfall multi-face / non-portrait) | Priority: P2 | Completed: 2026-07-03 | Owner: drew-valentine
  - Requested by an external user. Goal: support Scryfall's alternative `layout` values so multi-face / non-portrait cards render authentically. **Rendering scope COMPLETE and shipped across v1.36.0–v1.38.0.**
  - Shipped:
    - v1.36.0 (PR #7, commit 23a5d8c) — Phases 0–2: data foundation + per-face storage/backfill, double-faced cards (transform / MDFC), adventure + room split text rendering, Frame Designer front/back face support.
    - v1.37.0 (PR #8, commit dac763a) — Phase 3a: battle cards (landscape sieges) as a dedicated landscape frame rotated into the portrait composite (battles are `layout=transform`, so DFC machinery covered faces/toggle/extension).
    - v1.38.0 (PR #9, commit 35a61d8) — Phase 3b+: authentic rotated split cards with per-half art, rooms corrected to the rotated per-half split treatment, per-style battle frames across all 10 image styles, and an alt-layout polish + review-hardening pass. (See the Phase 0–3 items below and the Phase 3b+ item for full detail.)
  - Net result: DFCs, adventures, rooms, battles, and rotated splits all render authentically, generate per-face art, and flow through the grid/exports/extension.
  - Board hygiene note: closed the epic once its rendering scope shipped. The four remaining items are polish/cleanup/clarification, not rendering support, so they were split out as small standalone P3 Backlog items: (1) transform/MDFC face-indicator pips, (2) DRY cleanup pass, (3) Frame Designer preview for rotated splits, (4) clarify "PIP cards" with the requester.

- [x] Alt Layouts Phase 3b+ — Rotated split cards, per-style battle frames, and alt-layout polish | Priority: P2 | Completed: 2026-07-03 | Owner: drew-valentine
  - Branch: `feature/split-rotated` merged via PR #9 (squash-merged to main as commit 35a61d8)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/9 (retitled "feat: rotated split cards, per-style battle frames, and alt-layout polish (Phase 3b+)", label `semver:minor`)
  - Tagged: v1.38.0 (released 2026-07-03)
  - Part of EPIC: Support Alternative Card Layouts (see Backlog). Drew approved starting Phase 3b on 2026-07-03. Started as a rotated-split fidelity upgrade; broadened into a per-style battle-frame + alt-layout polish pass driven by reference-checking real printings in the browser. **This shipped the last of the epic's rendering scope — see the epic's move to Done below.**
  - Final additions that landed after the last board sync (v1.38.0):
    - Per-card frame saves with an explicit deck-default editing mode: opening the Frame tab with no card selected edits the deck default, and a deck ⋯ menu entry provides a direct way in — so per-card overrides and the deck-wide default are edited through clearly distinct modes.
    - Showcase is the default frame style for new decks.
    - 10-finding review-hardening pass: split halves honor their saved per-half frames/art/flavor; no duplicated half art; version archiving + Scryfall fallbacks on split recomposites; per-half prompts; planeswalker-style battle fallback; Apply-to-Checked covers second faces.
  - Scope added beyond original Phase 3b (from live testing):
    - Rooms corrected to the rotated-split treatment: real Duskmourn Room printings have per-half art, so Rooms now render as rotated per-half splits like classic splits. This reverses the original "Rooms stay portrait side-by-side" criterion — the reversal is backed by reference evidence from actual printings.
    - Battle frames now render in EVERY image style: the landscape battle chrome is sliced from each style's own composited portrait assets (rather than a single hardcoded battle frame), verified across a 10-style contact sheet. Battle rules also corrected to match the reference's full-width bottom band.
    - Frame Designer WYSIWYG art rotation for battle fronts — the designer preview now rotates battle-front art to match the final composite (pixel-verified parity). This RESOLVES the battle-front pan/zoom limitation documented in Phase 3a (v1.37.0).
    - Alt-layout polish: uniform rules-area mana pip sizing; face-aware "Generate Random"; `cards_revision` self-healing card list that recovers stale/out-of-date pages; Showcase is now the default frame style for new decks.
  - Acceptance criteria (Given/When/Then) — all met:
    - [x] Given a classic split card (`layout=split`, e.g. Fire // Ice), when the composite is rendered, then each half renders as a mini card (own title/mana/type/rules/art) at ~70% scale, both rotated 90° into the standard portrait composite — like real printed splits, with per-half frame colors (Fire red / Ice blue).
    - [x] Given a Room card (Duskmourn `layout=room`, e.g. Smoky Lounge // Misty Salon), when the composite is rendered, then it uses the rotated per-half split treatment (per-half art), matching real printings — correcting the earlier portrait side-by-side treatment (reversed with reference evidence).
    - [x] Given a battle front, when the composite is rendered in ANY of the 10 image styles, then a landscape battle frame is sliced from that style's own composited portrait assets and rotated into the portrait composite — verified via a 10-style contact sheet — with the rules in the reference's full-width bottom band.
    - [x] Given either half of a rotated split, when art is generated, then each half has its own AI art + prompt + version history (right half reuses the existing second-face machinery: "<name> [back]" keys / "__back" slugs).
    - [x] Given either half is (re)generated, when generation completes, then the combined composite is re-rendered from either half; batch generation covers both halves without regenerating finished ones.
    - [x] Given a rotated split is selected in the UI, when the face toggle appears, then it is labeled with the half names (not Front/Back) and the hero always shows the combined card.
    - [x] Given a battle front in the Frame Designer, when the designer preview renders, then battle-front art is rotated to match the final composite (pixel-verified parity) — battle pan/zoom limitation from Phase 3a resolved.
    - [x] Given a stale/out-of-date grid page, when `cards_revision` advances, then the card list self-heals rather than showing stale entries; "Generate Random" is face-aware; rules-area mana pips are uniformly sized; Showcase is the default frame style for new decks.
    - [x] Known limitation (documented, remaining): Frame Designer preview for rotated *splits* still falls back to the column layout; the final composite is authoritative. (The battle-front pan/zoom limitation is now RESOLVED — see above.)
    - [x] Validation gate: Playwright + live FLUX generation of both halves of a real split card, 10-style battle contact sheet, and the full unit-test suite pass. CI green on PR #9; released as v1.38.0.

- [x] Alt Layouts Phase 3a — Battle cards (landscape sieges) | Priority: P2 | Completed: 2026-07-03 | Owner: drew-valentine
  - Branch: `feature/battles-landscape` merged via PR #8 (squash-merged to main as commit dac763a)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/8 (label `semver:minor`)
  - Tagged: v1.37.0 (released 2026-07-03)
  - Part of EPIC: Support Alternative Card Layouts (see Backlog). Phase 3 was split into 3a (this item) and 3b (authentic rotated split cards, remains Backlog). Remaining epic scope: Phase 3b, transform-indicator-pips polish, and a DRY cleanup pass.
  - **Pleasant discovery:** battles are Scryfall `layout=transform`, so the v1.36.0 DFC machinery already covered per-face storage, the Front/Back toggle, and the extension automatically — the PR came in smaller than scoped.
  - Acceptance criteria (Given/When/Then) — all met:
    - [x] Given a battle card (e.g. Invasion of Zendikar // Awakened Skyclave — Scryfall `layout=transform` with front type "Battle — Siege" and a `defense` field), when the deck is imported or an existing deck is backfilled, then the per-face `defense` value is stored on the card entry (per-face + top-level).
    - [x] Given a battle card front, when the composite is rendered, then a dedicated landscape battle frame (title bar, art region, rules panel, defense shield) is composed on a landscape canvas and rotated 90° into the standard 750×1050 portrait composite — matching how real battles are printed — so the grid, exports, and extension continue to work unchanged.
    - [x] Given a battle card front, when art is generated, then landscape-aspect FLUX art is produced to fill the landscape art region.
    - [x] Given a battle card, when the back face (a normal portrait card, e.g. Awakened Skyclave) is viewed/generated, then it works via the existing DFC machinery (Front/Back toggle, per-face art + composite).
    - [x] Known limitation (documented): Frame Designer art pan/zoom is limited for battle fronts in this pass. **RESOLVED in PR #9 (Phase 3b+)** — battle-front art now rotates in the Frame Designer with pixel-verified parity to the final composite.
    - [x] Validation gate: Playwright browser verification + live local FLUX generation of a battle front + full pytest suite (206 tests).
  - CI fix included in the release: the PR health check now polls up to 45s and dumps the server log on failure (previously a fixed 6s sleep that raced first-start font downloads with no diagnostics).

- [x] Alt Layouts Phase 2 — Adventure + Room split text rendering & Frame Designer face support | Priority: P2 | Completed: 2026-07-03 | Owner: drew-valentine
  - Branch: `feature/alt-layouts-dfc` merged via PR #7 (squash-merged to main as commit 23a5d8c)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/7 (retitled "feat: alternative card layouts — DFCs, adventures, rooms (Phases 0-2)", label `semver:minor`)
  - Tagged: v1.36.0 (released 2026-07-03)
  - Part of EPIC: Support Alternative Card Layouts (see Backlog). Only Phase 3 (landscape) + transform-indicator-pips polish + a DRY cleanup pass remain in the epic in Backlog.
  - Acceptance criteria:
    - [x] Given an adventure card (e.g. Murderous Rider // Swift End), when the composite is rendered, then BOTH halves render: the adventure half (name, mana cost, type, rules) in a left text-box panel beside the creature half's rules text; the title shows the creature-half name like real cards.
    - [x] Given a room card (e.g. Smoky Lounge // Misty Salon), when the composite is rendered, then both door halves render side by side, each with its own name/cost header.
    - [x] Given any frame style, when an adventure/room card is rendered, then the split happens inside that style's existing rules-text region — hooked into every rules-text path (incl. iko/LOTR/Crystal/ABU), so it works across all frame styles.
    - [x] Given a DFC and the Art tab's Front/Back face selection, when the Frame Designer is opened, then it previews the back face's art + text and saves per-card back-face overrides stored separately from front overrides via `frame_overrides_back` persistence.
    - [x] Validation gate: Playwright browser verification of the adventure split render (creature-half title, adventure left column), room side-by-side door halves, and the Frame Designer Front/Back toggle flow; 200 unit tests pass.
  - Bug fixed en route (pre-existing): Scryfall name-cache slug broke for "A // B" names (slash in the cache filename); now sanitized.
  - Post-review hardening pass: fixed 9 verified findings — face-state conflation, art-destroying regens, and extension manifest migration among them — plus planeswalker loyalty badges on transform backs and shield-number centering.

- [x] Alt Layouts Phase 0 + 1 — Data foundation + Double-faced cards (transform / MDFC) | Priority: P2 | Completed: 2026-07-03 | Owner: drew-valentine
  - Branch: `feature/alt-layouts-dfc` merged via PR #7 (squash-merged to main as commit 23a5d8c)
  - PR: https://github.com/drew-valentine/deck-art-studio/pull/7 (label `semver:minor`)
  - Tagged: v1.36.0 (released 2026-07-03)
  - Part of EPIC: Support Alternative Card Layouts (see Backlog). Phase 3 (landscape) + transform-indicator-pips polish + a DRY cleanup pass remain in the epic in Backlog.
  - Scope: Phase 0 (store `layout` + per-face data on import; backfill existing decks) merged with Phase 1 (per-face art generation + back-face composites + UI/extension/export support) as a single work item per Drew's approval.
  - Acceptance criteria:
    - [x] Given a DFC (transform/MDFC) is imported, when the deck is saved, then `layout` and the full `card_faces` array (per-face name, mana_cost, type_line, oracle_text, P/T, art_crop) are stored on the card entry; single-face cards are unchanged.
    - [x] Given a deck was created before this change, when it is loaded, then a migration/backfill populates `layout` + `card_faces` for existing DFCs without user action.
    - [x] Given a DFC, when art is generated, then art is produced per face (front and back) rather than a single shared image.
    - [x] Given a DFC with generated art, when composites are rendered, then both the front and back faces render as card composites. **Scope adjustment:** back-face composites render with the standard frame for the back face's own card data (name/type/oracle/colors incl. `color_indicator`); a dedicated transform-indicator icon on the frame was NOT included — carried as a small follow-up bullet under the Backlog epic ("Transform/MDFC face-indicator pips on frames").
    - [x] Given a DFC is selected in the UI, when the user toggles the face, then the card detail (and grid badge) switches between front and back faces.
    - [x] Given the browser extension replaces images on edhplay.com, when a card URL is a back face (/back/), then the back-face art is used and it is not overwritten with the front-face art (distinguishes /front/ vs /back/).
    - [x] Given a deck with DFCs, when export-manifest runs, then both front and back faces are included.
    - [x] Validation gate: verified in the actual browser via Playwright with local FLUX back-face generation and a fresh import containing transform/adventure cards; 194 unit tests pass.
  - Bugs fixed en route (both pre-existing): version endpoints returned 404 for "//" (multi-face) names; front-face text/art overrides were leaking onto back faces.

- [x] Planeswalker Frame Support | Priority: P1 | Completed: 2026-07-02 | Owner: drew-valentine
  - Branch merged via PR #5 (squash-merged to main as commit 4ee9899)
  - Tagged: v1.35.0 (GitHub release published)
  - Loyalty rendering across all 12 frame styles with authentic cardconjurer badge art
  - New dedicated "Planeswalker" frame style with M15 auto-routing
  - Showcase text area expanded +41% via type-bar relocation
  - 10 code-review findings fixed: loyalty regex MULTILINE + X costs, font-scaled band minimums, shield occlusion avoid, text color control, overflow flag sync, band truncation, m15 override honoring, gate dispatch consolidation
  - Quality gate expanded to renderer-declared geometry containment, badge/shield/numeral centering probes, print-safe assertion, and a hostile battery walker (144 renders, 186 unit tests)

- [x] Frame Editor Overhaul — "recreate any MTG frame, beautifully" | Priority: P1 | Completed: 2026-07-02 | Owner: drew-valentine
  - Branch: `feat/frame-editor` merged via PR #3
  - Tagged: v1.34.0
  - Summary: 11-style frame library (Basic, Clean, Crystal, Showcase, LOTR, 8th Edition, Mystical Archive, Art Deco, Samurai, Etched, M15), each verified at 0.000% chrome fidelity per frame
  - Per-style Frame Designer controls (dead controls removed, controls metadata data-driven per style)
  - Rules text never overflows — the size slider acts as a ceiling, text auto-fits below it
  - WYSIWYG browser preview reaches parity with the final Python composite
  - 3mm print-safe margins throughout; two-color gradients apply card-wide (blend + hard split)
  - High-effort code review with 10 verified defects fixed
  - CardConjurer attribution added (NOTICE / README / shared-frames README)
  - AI code review removed from CI in favor of local review
  - Carried forward: the two unfinished work items (Designer UX overhaul + validation harness) now tracked as a new P2 Backlog item "Frame Designer UX polish + validation harness"

- [x] MLX-Native Pipeline (Mac-only) | Priority: P1 | Completed: 2026-07-01 | Owner: drew-valentine
  - Replaced the entire generation pipeline with MLX-native components — Mac-only, pure-local, removing the OpenAI cloud backend
  - Branch: `feat/mlx-native-pipeline` merged via PR #2
  - Tagged: v1.33.0
  - All 6 stages done and validated (LLM→mlx-lm, Vision→mlx-vlm, image→FLUX.1-schnell via mflux, single-resident 18GB memory manager, cloud backend removed, 185/185 tests + full Playwright/local-FLUX validation)

- [x] Comprehensive Unit Test Suite with Pre-Commit Hooks | Priority: P1 | Completed: 2026-03-27 | Owner: drew-valentine
  - Added test infrastructure with conftest.py, pyproject.toml config, and fixtures
  - 185+ tests across 7 test modules covering security, slugs, decklist parser, prompts, frame helpers, vision merge, CLIP directives, and Flask endpoints
  - Pre-commit hook runs pytest automatically on commit
  - CI workflow updated to run pytest
  - Branch: `feat/unit-test-suite` (merged to main)

- [x] GitHub Actions CI/CD Pipeline — Claude Code Automation | Priority: P1 | Completed: 2026-03-19 | Owner: drew-valentine
  - Three workflows: Issue->PR (claude-issue-fix.yml), PR Review+Tests (claude-pr-review.yml), Auto Release (claude-auto-release.yml)
  - Label-triggered: `claude` label on issues triggers Claude to implement fixes
  - Basic tests: syntax check, import validation, Flask health check, extension manifest validation
  - AI code review with project-specific security checklist
  - Auto-merge + semantic version tagging on approval
  - Branch: `feature/ci-cd-pipeline` (merged to main)
  - Note: Manual setup still needed for CLAUDE_CODE_OAUTH_TOKEN secret, labels, Claude GitHub App

- [x] Fix "Prompt updated" banner not dismissing after re-roll | Priority: P1 | Completed: 2026-03-27 | Owner: drew-valentine
  - Bug: Polling function only refreshes status fields from /api/status, but prompt_stale is only returned by /api/cards
  - After a re-roll completes, the stale banner persists because the card data (including prompt_stale) is never refreshed
  - Fix: Refresh card data (including prompt_stale) when a card transitions to 'complete' status
  - Branch: `fix/stale-prompt-banner-dismiss`

- [x] Oversized Art Generation for Frame Designer Pan/Zoom | Priority: P1 | Completed: 2026-03-20 | Owner: drew-valentine
  - "Oversized" toggle generates at square resolution (1024x1024 for Lightning/Hyper) instead of portrait (768x1024)
  - Square composition gives wider scene with subject in environment, enabling pan/zoom in Frame Designer
  - CLIP prompt adds "wide shot, full body in environment" for creatures to compose subject smaller in scene
  - Negative prompt adds anti-close-up directives
  - Dark background fill in Frame Designer canvas when art doesn't cover (zoom out gracefully)
  - Removed broken upscale-to-square approach (edge-extend artifacts) in favor of native square generation
  - Branch: `feat/frame-designer-v2`

- [x] Remove Borderless Style and Consistent Field Corner Rounding | Priority: P2 | Completed: 2026-03-20 | Owner: drew-valentine
  - Removed Borderless frame style (redundant with Classic, nearly identical config)
  - Remapped 'borderless' and 'minimal' to 'classic' in both Python migration map and frontend styleMap
  - Consistent rounded corners on title bar, type bar, and text box (removed flat-corner logic when no pinline)
  - Branch: `feat/frame-designer-v2`

- [x] Fix SVG Frame Field Strokes and Classic Style Restoration | Priority: P1 | Completed: 2026-03-20 | Owner: drew-valentine
  - Restored simple rounded-rect fields for Classic/Borderless/Full-art SVG styles (commit 0747def)
  - Removed dark field strokes from frosted-glass overlay styles that were visually heavy (commit 5c4332a)
  - Fixed PT text centering in Frame Designer text overlay for accurate baseline positioning (commit dbf31a1)
  - Branch: `feat/frame-designer-v2`

- [x] Fix M15 P/T Box Proportions and Text Centering | Priority: P1 | Completed: 2026-03-20 | Owner: drew-valentine
  - CardConjurer PT box PNG was being composited at 1:1 (37.6% of card width) instead of authentic ~16% — applied 0.42x scale factor
  - Repositioned PT box to center on textbox bottom border
  - Text centering now uses measured interior center ratio from the isolated PT box PNG for pixel-accurate centering
  - Branch: `feat/frame-designer-v2`
  - Commit: 09d4013

- [x] Frame Designer v2: WYSIWYG Canvas Editor (#88) | Priority: P1 | Completed: 2026-03-20 | Owner: drew-valentine
  - WYSIWYG canvas-based frame editor replacing the v1 opacity-only presets
  - Canvas compositor (FrameCompositor class) with 3-layer architecture: art layer, frame chrome layer, text overlay layer
  - Art panning via drag and zoom via scroll wheel / slider — per-card art offset/zoom saved and applied in server-side final composite
  - Real-time style switching between 5 styles: M15, Classic, Borderless, Full Art, Clean
  - Modern style removed (redundant with M15, remapped for backwards compatibility)
  - Per-card editing: click card in grid, Frame tab shows live canvas preview of that card
  - Intensity master slider for SVG layer opacity
  - Collapsible sections and sticky action bar in Frame tab
  - Dual-mode rendering pipeline: 'svg' for programmatic frames (Classic, Borderless, Full Art, Clean) and 'image' for pre-rendered PNG overlays (M15)
  - M15 image-based frame style using CardConjurer assets (GPL license) — authentic pre-rendered PNG frames for all WUBRG colors, gold, artifact, colorless, and land, with legendary crown and P/T box overlays
  - SVG rendering improvements: pill-shaped name/type fields, lens-shaped P/T box, 4-layer inner shadow filters, filled border with proper width/radius, field strokes, improved legendary crown
  - New API endpoints: /api/frame-asset, /api/render-frame-layer, /api/render-text-overlay, /api/cards/art-position
  - Transparent v1 to v2 migration: existing deck settings auto-convert without user action
  - Phase 1-2 implementation complete (core editor + art positioning)
  - Branch: `feat/frame-designer-v2`
  - PR: #88

- [x] Art Repositioning — Draggable Pan/Zoom Within Card Frame | Priority: P2 | Completed: 2026-03-20 | Owner: drew-valentine
  - Implemented as part of Frame Designer v2 WYSIWYG canvas editor
  - Drag to pan art within frame, scroll wheel / slider to zoom
  - Art offset and zoom saved per-card in .meta.json
  - Server-side composite applies saved offset/zoom for final render
  - /api/cards/art-position endpoint for persisting position data

- [x] Frame Designer (#86) | Priority: P1 | Completed: 2026-03-19 | Owner: drew-valentine
  - New Frame tab in right sidebar with 6 presets (Classic, Borderless, Full Art, Minimal, Vintage, No Frame)
  - Opacity sliders and custom color controls with MTG quick-pick swatches
  - Per-card text overrides and live preview
  - Deck-level and per-card persistence
  - Batch apply to checked cards
  - Branch: merged to main
  - PR: #86 (merged)
  - Tagged: v1.14.0

- [x] Persist Model Selection and Add API Key Removal (#85) | Priority: P2 | Completed: 2026-03-19 | Owner: drew-valentine
  - Model selection now persists across page reloads and server restarts via backend_config.json
  - Local models auto-load on startup when a local model was previously selected
  - Added API key removal button so users can delete their stored OpenAI API key from the UI
  - Branch: merged to main
  - PR: #85 (merged)
  - Tagged: v1.13.2

- [x] Fix Art Quality Regression: Remove Themes from CLIP style_tags (#84) | Priority: P0 | Completed: 2026-03-19 | Owner: drew-valentine
  - Critical patch fixing a regression introduced in v1.13.0 where themes were injected into CLIP style_tags
  - Caused flat vector/clip-art output instead of painterly output because thematic words polluted style anchors
  - Fix: Remove thematic content from CLIP style_tags so only visual style descriptors drive image generation
  - Branch: merged to main
  - PR: #84 (merged)
  - Tagged: v1.13.1

- [x] Strengthen Mood Fidelity in Art Generation Pipeline (#83) | Priority: P1 | Completed: 2026-03-19 | Owner: drew-valentine
  - Added themes field to style analysis for mood-driven art generation
  - Button UX improvements for generation workflow
  - Instant generate feedback for better responsiveness
  - Branch: merged to main
  - PR: #83 (merged)
  - Tagged: v1.13.0

- [x] Fail CI When Claude Review Requests Changes (#82) | Priority: P2 | Completed: 2026-03-19 | Owner: drew-valentine
  - CI now fails when Claude code review requests changes, preventing auto-merge of problematic PRs
  - Branch: merged to main
  - PR: #82 (merged)
  - Tagged: v1.13.0

- [x] Surface AI Backend Errors to Users (#78) | Priority: P1 | Completed: 2026-03-19 | Owner: drew-valentine
  - AI backend failures (API key issues, Ollama not running, model not loaded) were failing silently
  - Fix: Surface backend availability errors to the user with actionable feedback
  - Branch: `fix/ai-backend-availability-feedback` (merged to main)
  - PR: #78 (merged)
  - Tagged: v1.13.0

- [x] Fix Analyze Style Silently Skipping in Local-Only Setup (#77) | Priority: P1 | Completed: 2026-03-19 | Owner: drew-valentine
  - Analyze Style was silently skipping when running in a local-only setup without cloud backend
  - Fix: Ensure style analysis works correctly in local-only mode
  - Branch: merged to main
  - PR: #77 (merged)
  - Tagged: v1.13.0

- [x] Card Detail Panel UX Redesign + One-Off Card Add Fixes (#81) | Priority: P1 | Completed: 2026-03-18 | Owner: drew-valentine
  - State-driven action area in card detail panel (context-aware buttons based on card state)
  - Mana symbol rendering in card detail panel (inline mana pips in mana cost and type line)
  - Hero progress overlay for active generation (replaces inline status text)
  - Status pip indicator on card detail header (visual generation state at a glance)
  - Cleaner visual hierarchy throughout the detail panel
  - Fix: Scryfall art was not showing for one-off card additions (cards added individually via Add Card)
  - Fix: Auto-render card frame composite when adding cards one-off (no manual recomposite needed)
  - Branch: `feat/card-detail-ux-redesign` (merged to main)
  - PR: #81 (merged)
  - Tagged: v1.12.0

- [x] Fix Cartoony Output + LLM-Driven CLIP Prompt Generation (#80) | Priority: P1 | Completed: 2026-03-18 | Owner: drew-valentine
  - Expands style fidelity for painterly/realistic inspiration art by replacing hardcoded CLIP elif chains with LLM-generated clip_directives
  - Dynamic style analysis drives CLIP prompt generation instead of static rules
  - Improves art quality for non-cartoon styles (watercolor, oil, photorealism, etc.)
  - Granular progress bar for the style analysis pipeline with per-batch card subject progress
  - Style-neutral analysis prompts (fix cartoon bias)
  - WebP inspiration image fix for Ollama llava
  - Post-analysis prompt regeneration hint toast
  - Branch: `fix/style-fidelity-painterly` (merged to main)
  - PR: #80 (merged)
  - Tagged: v1.11.0
  - Commits:
    - [x] Fix cartoony output when inspiration art is painterly/realistic
    - [x] Add LLM-driven CLIP prompt generation (replaces hardcoded elif chains)
    - [x] Add granular progress bar for style analysis pipeline

- [x] Fix Prompt Editing Overwrite + Single-Card Cancel (#79) | Priority: P1 | Completed: 2026-03-18 | Owner: drew-valentine
  - Bug: Polling loop in `updateDetailPanel()` was overwriting the prompt textarea on every tick, making it impossible to manually edit prompts
  - Fix: Skip prompt textarea update when the user is actively editing (focus check)
  - Also added ability to cancel single-card generation (not just batch cancel)
  - Fix: Card Back checkbox was unclickable when image failed to load due to z-index issue
  - Branch: `fix/prompt-editing-overwrite`
  - PR: #79 (merged)
  - Tagged: v1.10.1

- [x] Fix Batch Generation Progress Lost on Deck Switch | Priority: P1 | Completed: 2026-03-13 | Owner: drew-valentine
  - Batch generation progress was lost when switching decks or refreshing the page
  - Art files could also save to wrong deck during a switch
  - Fix: capture deck context at batch spawn time, separate batch status from per-deck display status
  - Ensures batch generation is scoped to the deck it was started on, independent of UI navigation
  - Tagged: v1.10.0

- [x] Next-Step Prompt Generation Hint | Priority: P2 | Completed: 2026-03-13 | Owner: drew-valentine
  - Contextual hint in Style Reference section nudging users toward prompt generation after uploading inspiration art
  - Shows "Next: select cards and generate art prompts" with gold CTA button
  - Only appears when inspiration exists but no prompts generated
  - Fixed: `has_ai_art` detection now correctly uses `.meta.json` to distinguish AI-generated art from Scryfall crops in raw_art/
  - Hint now properly shows on decks without AI art (like Lightning) and hides on decks with AI art (like original Heads I Win)
  - Branch: `feature/next-step-prompt-hint`
  - PR: #73 (merged)
  - Tagged: v1.10.0

- [x] UX Polish: Header Simplification, Panel Tabs, Card Grid States | Priority: P1 | Completed: 2026-03-12 | Owner: drew-valentine
  - Three structural UX improvements to take the interface from "developer tool" to "creative studio"
  - Card grid visual states: tile-generating (border shimmer), tile-queued (blue pulse), tile-pending (desaturated), tile-error (red border)
  - Right panel tabs: Style/Card tabs replacing silent panel morphing, auto-switches to Card tab on selection
  - Header simplification: filters tucked behind collapsible strip with gold indicator dot when active
  - Branch: `feature/ux-polish-header-tabs-grid`
  - PR: #71 (merged)
  - Tagged: v1.9.0

- [x] Move Action Bar from Bottom to Top | Priority: P2 | Completed: 2026-03-12 | Owner: drew-valentine
  - Visual polish / UX improvement: relocated the persistent action bar from the bottom of the viewport to the top for better visibility and faster access to batch controls
  - Part of the ongoing UX improvements track

- [x] UX Overhaul: Custom Dialogs, Action Bar, Card Detail | Priority: P1 | Completed: 2026-03-12 | Owner: drew-valentine
  - Replaced all 14 native browser dialogs with themed custom dialog system
  - Added persistent action bar with batch controls always visible
  - Reorganized card detail panel with Generate/Regenerate above fold, collapsible sections, compact feedback
  - Design system cleanup
  - Branch: `feature/ux-overhaul-dialogs-actionbar`
  - PR: #69 (merged)

- [x] Progress Bars for All User-Visible Wait Times | Priority: P1 | Completed: 2026-03-11 | Owner: drew-valentine
  - Real-time progress bars for: local model loading/downloading, Ollama model pulling, and vision analysis API calls
  - Indeterminate shimmer animation for phases without granular progress data
  - Determinate bars with byte-level download tracking where available
  - Branch: `feature/progress-bars`
  - PR: #68 (merged)

- [x] Progressive Disclosure UX Overhaul | Priority: P1 | Completed: 2026-03-11 | Owner: drew-valentine
  - Toast system replacing all alert() calls — non-blocking success/error/warning/info notifications with auto-dismiss
  - Setup bar for workflow guidance — step-by-step progress indicator guiding users through deck setup
  - Welcome hero for first-run experience — inviting landing state when no deck is loaded
  - Model hub for browsing all 8 models with live status — searchable grid with backend/cost/capability badges
  - Branch: `feature/progressive-disclosure`
  - PR: #67 (merged)

- [x] Usage Guide for README | Priority: P2 | Completed: 2026-03-11 | Owner: drew-valentine
  - Rewrote the README usage guide to match the actual UI based on Playwright inspection
  - Updated instructions to reflect current workflow, button labels, and UI layout
  - Branch: `docs/usage-guide`
  - PR: #66 (merged)

- [x] Style Analysis Progress Bar | Priority: P1 | Completed: 2026-03-10 | Owner: drew-valentine
  - Add real-time progress bar to the Style Reference section showing progress through the style analysis pipeline
  - Pipeline stages: analyzing images -> merging analyses -> distilling style tokens
  - Branch: `feature/style-analysis-progress`
  - All subtasks completed:
    - [x] Backend: emit progress events through the analysis pipeline stages
    - [x] Frontend: progress bar UI in the Style Reference section
    - [x] Wire up real-time progress updates (polling or SSE)

- [x] Repo Hygiene Cleanup | Completed: 2026-03-10 | Owner: drew-valentine
  - Clean up 25+ untracked PNG screenshots from root directory
  - Update .gitignore to prevent future screenshot/artifact accumulation
  - Add LICENSE file to the repository
  - Remove or organize stray files (scripts/, etc.)
  - PR: #55 (merged)
  - Tagged: v1.6.1

- [x] Update CLAUDE.md with Stale Info | Completed: 2026-03-10 | Owner: drew-valentine
  - Review CLAUDE.md for any outdated instructions, file descriptions, or architecture notes
  - Update line references, file sizes, and any other stale details
  - Ensure testing and API reference sections reflect current state
  - Completed as part of PR #55

- [x] Medium Polish Items from Code Review | Completed: 2026-03-10 | Owner: drew-valentine
  - Address remaining medium-priority items identified during code review
  - Includes any deferred refactoring, error handling improvements, or minor UX fixes
  - Version label escaping fix completed as part of PR #55

- [x] Code Review Security & Quality Hardening | Completed: 2026-03-10 | Owner: drew-valentine
  - Comprehensive security and code quality improvements for open-source readiness
  - Branch: `fix/code-review-security-and-quality`
  - PR: #54 (merged)
  - Tagged: v1.6.0
  - Changes:
    - [x] Path traversal protection for deck_id routes and image serving
    - [x] DOM XSS prevention with escapeHtml() utility
    - [x] Upload limit (MAX_CONTENT_LENGTH 16MB)
    - [x] Debug mode safety — disabled when --host is not localhost
    - [x] Missing urllib.parse import fix in fetch_scryfall_art.py
    - [x] Chrome MV3 service_worker support in extension manifest
    - [x] IndexedDB connection caching and migration safety

- [x] Fix Stale Inspiration Images After Deck Import | Completed: 2026-03-09 | Owner: drew-valentine
  - Bug: After importing a deck, stale inspiration images from a previously loaded deck were displayed
  - PR: #53 (merged)

- [x] Add Donation Links to Deck Art Studio | Completed: 2026-03-06 | Owner: drew-valentine
  - Added Ko-fi donation links across three surfaces:
    - [x] README.md — donation/support section
    - [x] App UI header in deck_studio.py — visible link/button in the web app
    - [x] Browser extension popup (extension/popup.html) — link in the extension UI
  - PR: #52 (merged)

- [x] Default to port 5001 to avoid macOS AirPlay conflict | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #51 (merged)

- [x] Remove hardcoded deck name from page title | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #50 (merged)

- [x] Update extension docs with install and sharing instructions | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #49 (merged)

- [x] Fix export crash with multi-deck v2 manifests | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #48 (merged)

- [x] Preserve Deck Names Through Export/Import | Priority: P1 | Completed: 2026-03-05 | Owner: drew-valentine
  - Export now groups cards by deck name instead of flattening to "Exported Collection"
  - Added v2 multi-deck manifest format with backward compatibility for v1 single-deck format
  - Import endpoint handles both v1 and v2 format manifests seamlessly
  - Branch: `feature/preserve-deck-names` (merged to main, tagged v1.5.7)
  - PR: #47 (merged)
  - Benefit: Users can export multiple decks and import them with their original deck names preserved

- [x] Fix File Import UI — Popup to Dedicated Tab | Priority: P1 | Completed: 2026-03-05 | Owner: drew-valentine
  - Root cause: Firefox was closing the popup when file picker opened, destroying the JS context silently
  - Solution: Moved file/URL import from popup.html to dedicated import.html tab page
  - File picker now works reliably in Firefox, Chrome, and Safari
  - Branch: `fix/file-import-ui` (merged to main, tagged v1.5.6)
  - PR: #46 (merged)
  - Result: Reliable art sharing across all browsers

- [x] File import: alert on failure, detailed errors | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #45 (merged)

- [x] Fix file import: direct IndexedDB write with progress | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #44 (merged)

- [x] Track shared imports in imported decks list | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #43 (merged)

- [x] Change extension default port to 5000 | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #42 (merged)

- [x] Add custom app icon and favicon | Completed: 2026-03-06 | Owner: drew-valentine
  - PR: #41 (merged)

- [x] Fix black bar on card composites | Completed: 2026-03-03 | Owner: drew-valentine
  - PR: #40 (merged)

- [x] EDH Play Custom Art Browser Extension | Priority: P1 | Completed: 2026-03-02 | Owner: drew-valentine
  - Cross-browser extension (Firefox + Chrome) that replaces Scryfall card images on edhplay.com with custom AI-generated art
  - Branch: `feature/edhplay-extension` (merged to main, tagged v1.5.0)
  - PR: #39 (merged)
  - Deliverables:
    - [x] Scryfall UUID storage in deck data (scryfall_client.py + live API fallback backfill on load)
    - [x] Export manifest endpoint (`GET /api/decks/<id>/export-manifest`) with embedded base64 JPEG images
    - [x] Browser extension core (manifest.json, content.js, background.js, popup, db.js) with MutationObserver-based image replacement
    - [x] Name-based fallback matching for alternate card printings
    - [x] Custom card back replacement (backs.scryfall.io images replaced with deck's custom card back art)
    - [x] Art sharing via self-contained JSON manifests (file + URL import, Google Drive link conversion)
    - [x] Popup UX fix (URL race condition between content script and popup)
    - [x] README documentation for extension installation and usage

- [x] Restore ip_adapter_steps, add traceback logging | Completed: 2026-03-02 | Owner: drew-valentine
  - PR: #38 (merged)

- [x] Remove Rick and Morty References | Priority: P3 | Completed: 2026-02-28 | Owner: drew-valentine
  - Replaced all R&M references with neutral examples (Studio Ghibli, Borderlands)

- [x] Fix Scheduler Index-Out-of-Bounds During Local Generation | Priority: P0 | Completed: 2026-03-01 | Owner: drew-valentine
  - Bug: Users got "index 11 is out of bounds for dimension 0 with size 11" during local art generation
  - Root cause: IP-Adapter step counts (10, 6) caused trailing scheduler index errors
  - Fix: Changed ip_adapter_steps from 10->8 and 6->4 (power-of-2 values safer with trailing schedulers), plus a defensive retry on IndexError
  - Branch: merged to main, tagged v1.4.4
  - PR: #37 (merged)

- [x] Recomposite Falls Back to Scryfall Art | Priority: P2 | Completed: 2026-03-01 | Owner: drew-valentine
  - When recompositing cards without generated art, the /api/recomposite endpoint now copies Scryfall art to raw_art as a fallback
  - Relaxes the JS guard so recomposite is available when Scryfall art exists (not only when raw_art exists)
  - Branch: merged to main, tagged v1.4.3
  - PR: #36 (merged)

- [x] Parse Decklist-Format Input in Add Card Dialog | Priority: P1 | Completed: 2026-03-01 | Owner: drew-valentine
  - The Add Card endpoint now parses decklist-format lines like "1x Brightcap Badger // Fungus Frolic (blc) 28 [Creature]" by reusing the existing `_parse_card_line()` parser
  - Uses set+number lookup when available for precise Scryfall matching
  - Branch: merged to main, tagged v1.4.2
  - PR: #35 (merged)

- [x] Fix Adventure/DFC Card Slug Crash | Priority: P0 | Completed: 2026-03-01 | Owner: drew-valentine
  - Bug: Cards with " // " in their name (Adventure cards like "Brightcap Badger // Fungus Frolic") produced slugs containing "/" which caused filesystem path errors
  - Root cause: `name_to_slug()` did not handle the " // " separator used by Adventure and Double-Faced cards
  - Fix: Handle " // " -> "__" in `name_to_slug()` before general slug sanitization
  - Branch: merged to main, tagged v1.4.1
  - PR: #34 (merged)

- [x] Style-Aware Prompt Generation | Priority: P1 | Completed: 2026-03-01 | Owner: drew-valentine
  - `generate_subject_with_ai()` now accepts a `style_hint` parameter so the LLM tailors subject descriptions to match the deck's aesthetic
  - COLOR_VIBES softened from fantasy cliches to neutral visual descriptors
  - Style hint built from style_source + Art Style line, truncated at `|` for conciseness
  - Branch: merged to main, tagged v1.4.0
  - PR: #33 (merged)

- [x] Scryfall Set Code + Collector Number Lookup | Priority: P1 | Completed: 2026-02-28 | Owner: drew-valentine
  - Added set code + collector number lookup for deck imports via Scryfall API
  - Branch: `feat/scryfall-set-lookup` (merged to main, tagged v1.2.0)
  - PR: #28 (merged)
  - Critical bugfix in v1.2.1: set+number lookup was silently importing wrong cards when name didn't match
  - Branch: `fix/set-lookup-name-verify` (merged to main, tagged v1.2.1)
  - PR: #29 (merged)

- [x] Fix Card Back Prompt Quality | Priority: P2 | Completed: 2026-02-28 | Owner: drew-valentine
  - Bug: "card back design" in prompt caused AI to generate photo of physical card back instead of decorative art
  - Fix: Rewrote prompts to describe ornamental pattern, central medallion, border filigree directly
  - Also overrides CLIP prompt for local SDXL: replaces "Card Back" tokens with descriptive art terms
  - Branch: `fix/card-back-prompt` (merged to main, tagged v1.1.1)
  - PR: #26 (merged)

- [x] Fix Art Prompt Quality for Artifacts / Non-Creature Cards | Priority: P1 | Completed: 2026-02-27 | Owner: drew-valentine
  - Bug: AI prompt generator produced wrong subjects for non-creature cards (Sol Ring depicted as sun landscape instead of ring artifact)
  - Root cause: LLM received no card-type-specific guidance — just name, type line, and rules text
  - Fix: Pass rule-based description as reference anchor plus type-specific direction (artifact = "depict the object", enchantment = "depict the effect", etc.)
  - LLM enhances correct baseline rather than inventing from scratch
  - Branch: `fix/artifact-prompt-quality` (merged to main, tagged v0.18.1)
  - PR: #23 (merged)
  - Tested: Sol Ring, Lightning Greaves, Propaganda, Acidic Slime, Fog — all correct subjects

- [x] Fix Style Fidelity Regression for Local SDXL Generation | Priority: P0 | Completed: 2026-02-27 | Owner: drew-valentine
  - Art quality had regressed — generated art showed character cloning and lost style essence
  - Root causes: LLM misclassified tradition (anime for non-anime), style_source lost in prompts, CLIP proper noun triggered character rendering
  - Branch: `fix/style-fidelity-regression` (merged to main, tagged v0.18.0)
  - PR: #22 (merged)
  - Fixes:
    - [x] Anti-anime cross-reference: validates tradition against style_source to catch LLM misclassification
    - [x] Style_source preserved in preamble: always replaces LLM's "Source: Original" with user's explicit source
    - [x] Tradition validation in distill_style_tokens: corrects anime→western 2D cartoon when source doesn't suggest anime
    - [x] Rich style anchor: CLIP prompt built from ALL style_tokens (coloring, rendering, mood, proportions)
    - [x] Style-first CLIP ordering: style anchors placed first where CLIP gives maximum attention weight
    - [x] Dynamic anti-style negative: negative prompt derived as antithesis of desired style tokens
    - [x] Character cloning fix: replaced proper noun with abstract tradition token in CLIP to prevent SDXL rendering franchise characters
    - [x] Reversed IP-Adapter gradient: weak→strong across cross-attention layers to suppress semantic/character features
    - [x] Tuned IP-Adapter steps (8→10) and CFG (2.5→3.5) for better style convergence

- [x] Fix updateDetailPanel Poller Button State on Ollama Busy | Priority: P0 | Completed: 2026-02-27 | Owner: drew-valentine
  - Bug: `updateDetailPanel()` poller was overwriting button text with "Generating..." on every tick, ignoring the `ollamaBusy` state
  - Root cause: Button text update didn't check whether generation was blocked by active Ollama analysis
  - Fix: Buttons now correctly show "Waiting for analysis..." when generation is blocked by active Ollama operation (vision analysis, prompt generation, subject distillation)
  - Found during e2e testing of v0.17.2 feedback feature
  - Branch: `fix/ollama-busy-feedback` (merged to main, tagged v0.17.3)
  - PR: #21 (merged)
  - Result: Accurate button state feedback to users during Ollama-blocked operations

- [x] Fix Ollama GPU Memory Contention on Apple Silicon | Priority: P0 | Completed: 2026-02-27 | Owner: drew-valentine
  - Bug: Ollama models (llava:7b, llama3.1:8b) stayed loaded in GPU memory when SDXL Turbo generation started, causing system lockup on unified memory exhaustion
  - Root cause: No coordination between Ollama background threads and SDXL memory-hungry operations
  - Fix: Centralized "Ollama GPU guard" using threading.Event + atomic counter
  - Implementation:
    - [x] Track all background Ollama threads with `ollama_active_count` and `ollama_all_idle` event
    - [x] Auto-unload all Ollama models after each operation (prompt generation, vision analysis, subject distillation)
    - [x] Gate SDXL generation pipeline to wait for Ollama idle before starting (prevents memory collision)
    - [x] Applied to both single card and batch generation flows
  - Branch: `fix/ollama-gpu-guard` (merged to main, tagged v0.17.2)
  - PR: #19 (merged)
  - Status: Implementation complete and merged

- [x] Batch Generation Performance Optimization — Eliminate Redundant Work | Completed: 2026-02-27 | Owner: drew-valentine
  - Branch: `feature/batch-gen-perf` (merged to main, tagged v0.17.0)
  - PR: #18 (merged)
  - Three-layer optimization: inspiration composite caching, CLIP embedding pre-encoding, MPS memory cleanup
  - Fixed progressive MPS degradation bug (45s→280s per card) — now stable at ~55-65s/card across 12-card batches
  - Eliminated per-card redundant work: single inspiration composite per batch, pre-encoded CLIP embeddings shared across cards, explicit MPS cache clear between generations
  - Result: 3.5x faster batch generation, predictable performance scaling, eliminated memory leak

- [x] Art-Prompt-Aware Subject Distillation, Pinned Cards, Multi-Upload Improvements | Completed: 2026-02-27 | Owner: drew-valentine
  - Branch: `feature/subject-quality` (merged to main, tagged v0.16.0)
  - PR: #17 (merged)
  - Art-prompt-aware subject distillation: Rewrote distill_card_subjects() to feed art prompt snippets as LLM context (llama3.1:8b), producing rich CLIP subjects. Smart IP-Adapter scaling (FULL for characters, REDUCED for objects/lands). Auto-unloads heavy Ollama models after use.
  - Pinned cards feature: Server-side persistence in deck.json, API endpoint, pure CSS pin icon on tiles, "Pinned" filter option, batch pin/unpin.
  - Multi-upload improvements: Raised inspiration limit to 10, multi-file upload with progress, MD5 content-hash deduplication.

- [x] Intensive Style Capture Tuning for Local SDXL Generation | Completed: 2026-02-27 | Owner: drew-valentine
  - Major tuning pass on IP-Adapter + CLIP prompt architecture for local SDXL generation
  - Branch: `feature/style-capture-tuning` (merged to main)
  - PR: #16 (merged)
  - Key architectural insight: IP-Adapter drives style, CLIP drives subject
  - IP-Adapter scales increased, multi-image compositing, simplified CLIP prompt, removed over-constraining style text
  - Unlocked 768x1024 for Lightning/Hyper-SD models, subject distillation improvements, humanoid detection, anti-anime negative prompt

- [x] Status Filter Options | Completed: 2026-02-27 | Owner: drew-valentine
  - Added status filters for card grid, card name CLIP anchoring, and MPS performance fixes
  - Branch: `feature/status-filter-options` (merged to main)
  - PR: #15 (merged)
  - Also included: LLM-distilled style tokens + card subjects for local SDXL pipeline

- [x] Multiple Inspiration Images (Max 5) | Completed: 2026-02-26 | Owner: drew-valentine
  - Allow uploading up to 5 inspiration images per deck
  - Merged vision analyses for text prompts, random IP-Adapter selection per card for variety
  - Gallery UI with add/delete
  - Branch: `feature/multi-inspiration` (merged to main)
  - PR: #14 (merged)
  - Delivered: upload/delete/gallery UI, merged vision analysis, backward compatibility migration, CLIP prompt rewrite, creature type anchoring, graduated IP-Adapter scale, borderless card frames, pipeline docs

- [x] Local Prompt Tuning | Completed: 2026-02-24 | Owner: drew-valentine
  - IP-Adapter working end-to-end for style transfer with cartoon style fidelity
  - Branch: `fix/local-prompt-tuning` (merged to main)
  - PR: #12 (merged)
  - Fixed IP-Adapter attention slicing conflict, reduced scale for better balance, verified style output

- [x] Fix IP-Adapter Style Transfer | Completed: 2026-02-24 | Owner: drew-valentine
  - IP-Adapter was silently failing because enable_attention_slicing() conflicted with load_ip_adapter()
  - Fixed load order and removed attention slicing
  - Reduced IP-Adapter scale from 1.0 to 0.5 for better style balance

- [x] Unified Action Buttons UX | Completed: 2026-02-24 | Owner: drew-valentine
  - Replaced "all vs selected" buttons with unified 4-button grid (Prompts, Flavor, Art, Frames)
  - Grid always operates on current selection for consistent UX
  - Simplifies user interaction model

- [x] Subject-First CLIP Prompt Reordering | Completed: 2026-02-24 | Owner: drew-valentine
  - When IP-Adapter is active, lead prompts with subject, append short style hint
  - Trimmed creature descriptions for better CLIP token budget allocation
  - Improves style transfer accuracy with limited local model token window

- [x] Prompt Iteration Test Harness | Completed: 2026-02-24 | Owner: drew-valentine
  - Created test_prompt.py for fast prompt tuning with grid output
  - Enables rapid iteration on style transfer without full generation pipeline
  - Test results confirm style transfer working correctly

- [x] Rich Cloud Model Prompts | Completed: 2026-02-23 | Owner: drew-valentine
  - Cloud models (gpt-image-1) now get properly structured, detailed prompts with full vision analysis prose art direction
  - Separated from CLIP-optimized local model prompts
  - `_generate_openai()` parses structured prompts into subject + art direction prose
  - `build_collage_instruction()` builds rich multi-section prompts for cloud models
  - Fixed feedback insertion to work for both backends

- [x] Add Batch Feedback for Selected Card Generation | Completed: 2026-02-23 | Owner: drew-valentine
  - Added prompt() dialog to generateSelected() for batch feedback input
  - Wired feedback through API to batch_generate_worker's feedback_map

- [x] Creativity Slider (Simplified) | Completed: 2026-02-23 | Owner: drew-valentine
  - Local img2img generation produces results too similar to Scryfall reference art — same composition/colors
  - PR #8 (feature/unified-mode-toggle): Merged to main and tagged v0.8.0
  - Branch: `feature/creativity-slider` (merged to main)
  - Completed work:
    - [x] Simplified to 3 creativity levels: Low (img2img str 0.55), Medium (img2img str 0.80), High (pure txt2img)
    - [x] Fixed CFG/guidance_scale for prompt adherence
    - [x] LoRA hot-swap for faster model switching
    - [x] Removed DEFAULT_STYLE_PREAMBLE — style only from inspiration art
    - [x] Added style essence tag (~20 words) at front of prompts for CLIP token window
    - [x] Prompt structure: style tag + subject first, full preamble after --- delimiter

- [x] Unified Mode Toggle UX Overhaul | Completed: 2026-02-23 | Owner: drew-valentine
  - PR #7 (feature/unified-mode-toggle): Major UX overhaul replacing fragmented Cloud/Local toggle
  - Branch: `feature/unified-mode-toggle` (merged to main)
  - Included: per-card Scryfall art reference, regenerate prompts for selected cards, apostrophe fix, generate button fixes, cost display fix, speed optimizations, SDXL Lightning/Hyper-SD models, Scryfall art as version 0

- [x] Remove dead Scryfall pip fetcher and legacy scripts | Completed: 2026-02-22 | Owner: drew-valentine
  - PR #1 (cleanup/remove-dead-code): Cleaned up dead code and removed unused legacy scripts
  - Branch: `cleanup/remove-dead-code` (merged to main)

- [x] Add local AI backend via Ollama | Completed: 2026-02-22 | Owner: drew-valentine
  - PR #2 (feature/local-llm-backend): Phase 1 of "Local AI Backend" feature
  - Added Ollama as an alternative backend for LLM prompt generation (llama3.2:3b) and vision analysis (llava:7b)
  - Implemented automatic Ollama server lifecycle management
  - Branch: `feature/local-llm-backend` (merged to main)

- [x] Add local image generation via Stable Diffusion | Completed: 2026-02-22 | Owner: drew-valentine
  - PR #3 (feature/local-image-gen): Phase 2 of "Local AI Backend" feature
  - Added SDXL Turbo and SD 1.5 as free local image generation via HuggingFace diffusers
  - Supports txt2img and img2img with inspiration images
  - MPS acceleration enabled on Apple Silicon
  - Branch: `feature/local-image-gen` (merged to main)

- [x] Add backend toggle UI for Cloud/Local switching | Completed: 2026-02-22 | Owner: drew-valentine
  - PR #4 (feature/backend-toggle-ui): Initial Cloud/Local toggle in the UI
  - Branch: `feature/backend-toggle-ui` (merged to main)

- [x] Fix img2img missing width/height parameters | Completed: 2026-02-22 | Owner: drew-valentine
  - PR #5 (fix/img2img-width-height): Bug fix for generate_with_reference
  - Branch: `fix/img2img-width-height` (merged to main)

- [x] Fix SDXL Turbo producing black images on MPS | Completed: 2026-02-22 | Owner: drew-valentine
  - PR #6 (fix/sdxl-turbo-black-images): Fixed black image output on Apple Silicon
  - Branch: `fix/sdxl-turbo-black-images` (merged to main)

---

## Project Context

**Deck Art Studio** is a web-based tool for generating custom AI art for Magic: The Gathering proxy decks.

### Key Features
- Multi-deck management
- Scryfall integration
- AI art generation (currently OpenAI DALL-E 3, GPT Image, with local Ollama support added)
- SVG-based card frame rendering with mana pips and hybrid mana support
- Inspiration system with GPT Vision style analysis
- Version history and feedback loop
- Batch generation with parallel processing
- Card backs generation
- ZIP export for print-ready cards

### Technology Stack
- **Web Framework**: Flask (single-file app)
- **AI Backends**: OpenAI API (DALL-E 3, GPT-4 Vision), Ollama (llama3.2:3b, llava:7b), Local Stable Diffusion (SDXL Turbo, SD 1.5)
- **Image Processing**: PIL/Pillow, SVG rendering, HuggingFace Diffusers
- **Data Source**: Scryfall API
- **Target Platform**: Web (localhost:5001)
- **Hardware Acceleration**: Metal Performance Shaders (MPS) on Apple Silicon

### Recent Development Trajectory
"Local AI Backend" feature fully implemented across two phases:
- Phase 1 (PR #2): Added local LLM backend via Ollama for prompt generation and vision analysis
- Phase 2 (PR #3): Added local image generation via Stable Diffusion (SDXL Turbo, SD 1.5) with MPS acceleration

Subsequent work focused on local generation quality:
- PRs #14-#16: Multi-inspiration images, status filters, CLIP prompt rewriting, IP-Adapter style capture tuning
- PR #17 (v0.16.0): Art-prompt-aware subject distillation (llama3.1:8b), pinned cards, multi-upload with MD5 dedup
- PRs #18-#21: Batch perf optimization, Ollama GPU memory guard, Ollama busy button feedback
- PR #22 (v0.18.0): Style fidelity regression fix — anti-anime cross-ref, style_source preservation, rich CLIP style anchors, character cloning fix via abstract tradition tokens, reversed IP-Adapter gradient

The project now supports completely local operation on Apple Silicon devices, reducing dependency on cloud APIs for text and image generation while maintaining optional OpenAI integration as a fallback.
- PR #23 (v1.0.0): Full-featured local + cloud AI art generation with style fidelity overhaul, character cloning fix, and type-aware AI prompts.
- PR #33 (v1.4.0): Style-aware prompt generation — LLM subject descriptions now tailored to deck aesthetic via style_hint parameter, COLOR_VIBES neutralized.
- PR #34 (v1.4.1): Fix Adventure/DFC card slug crash — " // " in card names (e.g. "Brightcap Badger // Fungus Frolic") produced "/" in slugs causing filesystem errors.
- PR #35 (v1.4.2): Parse decklist-format input in Add Card dialog — reuses `_parse_card_line()` parser so pasted decklist lines work directly, with set+number Scryfall lookup when available.
- PR #36 (v1.4.3): Recomposite falls back to Scryfall art — cards without generated art can now be recomposited using Scryfall art as fallback, JS guard relaxed to allow recomposite when Scryfall art is available.
- PR #37 (v1.4.4): Fix scheduler index-out-of-bounds during local generation — ip_adapter_steps changed to power-of-2 values (10->8, 6->4) to avoid trailing scheduler index errors, plus defensive retry.
- PR #39 (v1.5.0): EDH Play custom art browser extension — cross-browser extension (Firefox + Chrome) replaces Scryfall card images on edhplay.com with custom AI art from Deck Art Studio. Export manifest endpoint with embedded base64 JPEGs, name-based fallback matching, custom card back replacement, art sharing via JSON manifests.
- PR #46 (v1.5.6): Fix file import UI — moved import from popup to dedicated import.html tab page to work around Firefox popup closure on file picker. Reliable art import across all browsers.
- PR #47 (v1.5.7): Preserve deck names through export/import — export groups cards by deck name, v2 manifest format supports multiple decks, import handles both v1 and v2 formats.

- PR #80 (v1.11.0): Fix cartoony output + LLM-driven CLIP prompt generation — replaces hardcoded CLIP elif chains with dynamic LLM-generated clip_directives, granular progress bar for style analysis pipeline, style-neutral analysis prompts to fix cartoon bias, WebP inspiration image fix for Ollama llava, post-analysis prompt regeneration hint toast.
- PR #81 (v1.12.0): Card detail panel UX redesign — state-driven action area, mana symbol rendering, hero progress overlay, status pip, cleaner hierarchy. Also fixes Scryfall art not showing for one-off card additions and auto-renders card frame when adding cards one-off.
- v1.13.0: Four PRs merged — fix Analyze Style silently skipping in local-only setup (#77), surface AI backend errors to users (#78), fail CI when Claude review requests changes (#82), strengthen mood fidelity in art generation pipeline with themes field, button UX, and instant generate feedback (#83).
- v1.13.1: Critical patch — fix art quality regression from v1.13.0 where themes were injected into CLIP style_tags, causing flat vector/clip-art output instead of painterly output (#84).
- v1.13.2: Persist model selection and add API key removal (#85) — model selection persists across reloads/restarts via backend_config.json, local models auto-load on startup, API key removal button added to UI.
- v1.14.0: Frame Designer (#86) — new Frame tab in right sidebar with 6 presets (Classic, Borderless, Full Art, Minimal, Vintage, No Frame), opacity sliders, custom color controls with MTG quick-pick swatches, per-card text overrides, live preview, deck-level and per-card persistence, batch apply to checked cards.
- v1.15.0: Frame Designer v2: WYSIWYG Canvas Editor (#88) — complete rewrite of frame editing with canvas-based compositor (FrameCompositor class, 3 layers: art/chrome/text), art panning via drag and zoom via scroll/slider, 5 frame styles (M15, Classic, Borderless, Full Art, Clean), per-card art offset/zoom persistence, new API endpoints (/api/frame-asset, /api/render-frame-layer, /api/render-text-overlay, /api/cards/art-position), intensity master slider, collapsible sections, sticky action bar. Also subsumes the Art Repositioning backlog item.

- v1.35.0 (PR #5): Planeswalker frame support — loyalty rendering across all 12 frame styles with authentic cardconjurer badge art, new dedicated "Planeswalker" frame style with M15 auto-routing, Showcase text area expanded +41% via type-bar relocation, 10 code-review findings fixed (loyalty regex MULTILINE + X costs, font-scaled band minimums, shield occlusion avoid, text color control, overflow flag sync, band truncation, m15 override honoring, gate dispatch consolidation). Quality gate expanded to renderer-declared geometry containment, badge/shield/numeral centering probes, print-safe assertion, and a hostile battery walker (144 renders, 186 unit tests).

All stale feature/fix branches have been purged. Only `main` and active PR branches remain.
