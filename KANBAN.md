# Deck Art Studio - Kanban Board

## Backlog

- [ ] EPIC: Support Alternative Card Layouts (Scryfall multi-face / non-portrait) | Priority: P2 | Created: 2026-07-02 | Owner: unassigned
  - Requested by external user. Generally support all Scryfall alternative `layout` values: double-faced (transform / modal DFC, e.g. "Accursed Witch // Infectious Curse"), adventure (e.g. "Murderous Rider // Swift End"), rooms (e.g. "Smoky Lounge // Misty Salon"), horizontal/landscape (battles, split cards), and "PIP cards" (requester's ambiguous term — see open question).
  - **Current state (research findings):**
    - Ingestion (`scryfall_client.py:259` `scryfall_to_card_entry`): multi-face cards are flattened to front-face data (oracle_text/mana_cost/type_line/P/T from `card_faces[0]`, front-face art_crop). Scryfall `layout` field and `card_faces` array are NOT stored. Name keeps "A // B" form (reversible dupes deduped).
    - File naming: `name_to_slug()` already sanitizes "/" (" // " -> "__"), so "A // B" names work for raw_art/composites/status keys.
    - Frame renderer (`card_frame_renderer.py`): fixed 750x1050 portrait canvas, 11 frame styles, planeswalker layout supported. No landscape orientation, no split/adventure/room/flip text-box layouts, no back-face rendering.
    - Generation: one art image per card name. FLUX accepts arbitrary width/height; deck-level `art_orientation` (portrait/landscape) already exists for art aspect.
    - Extension (`extension/content.js`): replaces edhplay.com images by Scryfall UUID from image URL. DFC front and back share the same UUID (URLs differ by /front/ vs /back/), so back faces would currently be replaced with front art.
  - **Phasing (subtasks):**
    - Phases 0, 1 + 2 — **SHIPPED in v1.36.0** (2026-07-03, PR #7 squash-merged as commit 23a5d8c, owner drew-valentine). Phase 3a — **SHIPPED in v1.37.0** (2026-07-03, PR #8 squash-merged as commit dac763a, owner drew-valentine). Both moved to Done. Phase 3b (authentic rotated split cards with per-half art) — **IN PROGRESS** (started 2026-07-03, branch `feature/split-rotated`, owner drew-valentine; see In Progress). Remaining epic scope after 3b: the transform-indicator-pips polish, and a DRY cleanup pass (below).
    - [ ] Transform/MDFC face-indicator pips on frames | polish, carried from Phase 1 — a dedicated transform-indicator icon on the frame (front/back face indicator). Back-face composites currently render with the standard frame for the back face's own card data; a face-indicator icon was deferred out of Phase 1.
    - Phase 3 split into two parts (Drew approved starting 3a on 2026-07-03):
      - Phase 3a — Battle cards (landscape sieges) — **SHIPPED in v1.37.0** (see Done). Battle fronts render a dedicated landscape battle frame rotated 90° into the standard portrait composite; battles are Scryfall `layout=transform`, so the v1.36.0 DFC machinery covered faces/toggle/extension automatically.
      - [ ] **Phase 3b — Authentic rotated split cards with per-half art** — **IN PROGRESS** (started 2026-07-03, branch `feature/split-rotated`, owner drew-valentine; tracked in the In Progress column). True rotated split-card layout with two halves each having their own art. Note: split cards already render usably today via the Phase 2 side-by-side treatment, so this is a fidelity upgrade rather than net-new support.
    - [ ] DRY cleanup pass (flagged by the Phase 2 review) — de-duplicate the face-expansion logic (duplicated across paths) and consolidate the triple-copied download/slug helpers into a single shared implementation.
  - **Open question — resolved (2026-07-03):** the friend's examples are all covered by Phases 0-3 except "PIP cards", which remains unclarified. Still need the requester to clarify what "PIP cards" means (likely Kamigawa flip cards) before scoping any flip-layout work.

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

- [ ] Alt Layouts Phase 3b — Authentic rotated split cards (per-half art) | Priority: P2 | Started: 2026-07-03 | Owner: drew-valentine
  - Branch: `feature/split-rotated`
  - Part of EPIC: Support Alternative Card Layouts (see Backlog). Drew approved starting Phase 3b on 2026-07-03. Fidelity upgrade — split cards already render usably today via the Phase 2 portrait side-by-side treatment.
  - Acceptance criteria (Given/When/Then):
    - [ ] Given a classic split card (`layout=split` WITHOUT "Room" in the type line, e.g. Fire // Ice), when the composite is rendered, then each half renders as a mini card (own title/mana/type/rules/art) at ~70% scale, both rotated 90° into the standard portrait composite — like real printed splits; Rooms keep the v1.36.0 portrait side-by-side treatment.
    - [ ] Given either half of a rotated split, when art is generated, then each half has its own AI art + prompt + version history (right half reuses the existing second-face machinery: "<name> [back]" keys / "__back" slugs).
    - [ ] Given either half is (re)generated, when generation completes, then the combined composite is re-rendered; batch generation covers both halves without regenerating finished ones.
    - [ ] Given a rotated split is selected in the UI, when the face toggle appears, then it is labeled with the half names (not Front/Back) and the hero always shows the combined card.
    - [ ] Known limitation (documented): Frame Designer preview for rotated splits falls back to the column layout; the final composite is authoritative.
    - [ ] Validation gate: Playwright + live FLUX generation of both halves of a real split card + pytest.

## In Review

## Done

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
    - [x] Known limitation (documented): Frame Designer art pan/zoom is limited for battle fronts in this pass.
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
