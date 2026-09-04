# Deck Art Studio

## What This Is

Web app for generating custom AI art for Magic: The Gathering proxy decks. Single-file Flask backend (`deck_studio.py`, ~268K) with embedded HTML/CSS/JS — no build step, no frontend framework. Includes a browser extension for replacing card images on edhplay.com.

## Running

```bash
pip install -r requirements.txt
python3 deck_studio.py                    # http://localhost:5001 (default)
python3 deck_studio.py --port 5002        # custom port
python3 deck_studio.py --host 0.0.0.0     # LAN access (debug mode auto-disabled)
```

MLX-native pipeline (Apple Silicon only): `pip install -r requirements-mac.txt`
(installs `mflux` for FLUX image generation, `mlx-lm` for prompt LLMs, `mlx-vlm`
for vision). These are Mac-only and lazily imported, so the base `requirements.txt`
still installs/imports on the Ubuntu CI runner.

## Architecture

### Core Files
| File | Purpose |
|------|---------|
| `deck_studio.py` | **The app** — Flask routes, all HTML/CSS/JS (inline), generation orchestration, card management. ~7600 lines. |
| `local_image_generator.py` | FLUX.1-schnell image generation via **mflux** (MLX). Single-resident model; unloads the MLX LLM/VLM before loading FLUX. txt2img + img2img. |
| `mlx_llm.py` | MLX text (`mlx-lm`) + vision (`mlx-vlm`) inference wrapper. Single-resident model cache, GPU lock, lazy imports. |
| `backend_config.py` | MLX model selection + persistence to `backend_config.json`. (No cloud/Ollama lifecycle — removed.) |
| `card_frame_renderer.py` | SVG-based card frame compositing — mana pips, type lines, text rendering. |
| `prompt_generator.py` | Art prompt generation via `mlx-lm` (Llama 3.1/3.2). |
| `vision_analyzer.py` | Inspiration image style analysis via `mlx-vlm` (Qwen2.5-VL). |
| `fetch_scryfall_art.py` | Downloads card art crops from Scryfall API. Caches to disk — check `out_path.exists()` before fetching. |
| `fetch_flavor_text.py` | Flavor text fetcher for card rendering. |

### Extension Files
| File | Purpose |
|------|---------|
| `extension/manifest.json` | MV3 manifest — supports both Firefox (`scripts`) and Chrome (`service_worker`). |
| `extension/background.js` | Manifest fetching, IndexedDB operations, Scryfall UUID resolution. |
| `extension/background-worker.js` | Chrome MV3 service worker entry point (imports db.js + background.js). |
| `extension/content.js` | MutationObserver-based image replacement on edhplay.com. |
| `extension/db.js` | IndexedDB wrapper — deck-scoped card storage with connection caching. |
| `extension/popup.html` / `popup.js` | Extension popup — deck import, export, management. |
| `extension/import.html` / `import.js` | Dedicated import page (opens as tab to avoid Firefox popup lifecycle issues). |

### Data Layout
```
decks/<deck-slug>/
  deck.json               # Deck metadata (name, inspiration_images[], style, pinned cards)
  cards.json               # Card list from Scryfall
  art_prompts.json         # Generated art prompts [{name, prompt}]
  inspiration_*.png        # Style reference images (multi-image support, max 10)
  raw_art/                 # Generated art PNGs + .meta.json per card
  composites/              # Art composited into card frames
  art_versions/            # Version history (v1/, v2/, etc.)
```

### MLX-Native Pipeline (Apple Silicon only)
- **Image**: `mflux` running FLUX.1-schnell (4-bit). Default loads a non-gated, pre-quantized
  mflux mirror (`dhairyashil/FLUX.1-schnell-mflux-4bit`) — the official BFL repo is gated and
  ships fp16 weights that quantize on the fly (memory spike, tight on 18 GB). Override via the
  `MFLUX_SCHNELL_REPO` env var.
- **LLM**: `mlx-lm` (Llama 3.1 8B / 3.2 3B, 4-bit) for prompt generation + style/subject distillation.
- **Vision**: `mlx-vlm` (Qwen2.5-VL 7B, 4-bit) for inspiration style analysis.
- **Style reference (Redux)**: `flux_worker.py` loads `Flux1Redux` (schnell + SigLIP + Redux projector)
  and appends the deck's inspiration images as reference tokens — the IP-Adapter role the SDXL
  pipeline had. Weights come from the ungated mirror `Runware/FLUX.1-Redux-dev` (override with
  `MFLUX_REDUX_REPO`; mflux hardcodes the gated official repo, so the worker patches
  `ModelConfig.dev_redux`). KEY MECHANISM: **block-selective injection** (`_install_block_mask`)
  — reference tokens are masked out of attention in every FLUX block except the early DOUBLE
  blocks (`STYLE_BLOCKS_DOUBLE` = 0-9): injected everywhere a reference is CLONED (its figures
  replace the subject); in the style blocks only, it carries medium/palette/stroke and the card
  keeps its subject at the full 729-token grid. `tokens` is then a pure strength dial
  (81 light / 256 medium / 729 strong = default); `_pool_token_grid` pools the 27x27 grid. References with prominent characters leak
  them above Subtle — the image channel bypasses the text-side franchise de-naming. Per-deck setting `deck.json.style_reference` {enabled, tokens,
  strength, max_images, average}; API `/api/decks/<id>/style-reference`. References are AVERAGED
  (element-wise mean of the pooled tokens across up to `STYLE_REFERENCE_MAX_IMAGES`=4 refs): content
  differs per image and cancels, shared style stays — cleaner and leak-free vs any single reference
  and vs concatenation (4 refs concatenated leaked figures). `average: false` = concatenate.
- **Effective style source**: `_effective_style_source(meta)` = the user's declaration, else the source
  the analyst recognized in the references (`vision_analyzer.recognized_style_source`, majority of the
  per-image `Source:` lines, ignoring 'Original'). Used for the render lead, scene-writer hint,
  medium classification and distillation — de-named like any declaration. Declaration always wins.
- **Named-style idiom (text side)**: the image channel carries palette/finish, not drawing idiom.
  At distillation `vision_analyzer.style_idiom_recall` (8B Llama when cached — the 3B half-knows
  styles) + `style_idiom_seen` (VLM reads the reference, told whose work it is) put the idiom
  phrases into the FLUX block (palette excluded — evidence does palette). `style_staging_recall`
  stores how the style STAGES scenes + its tonal register in `deck.json.style_staging`; the scene
  writer gets that plus the block minus hues (`prompt_generator.hint_without_palette`) — hues
  in the writer's hint become scene content. Writer rules: one subject in the foreground, two
  sentences of ~60 words (cap 64, whole sentences) following the scene grammar — subject at a moment, camera + scale + one named light, one atmospheric detail; rules-text zones (library/graveyard) are never scenery. Creatures also get a Body line
  (`_body_line`: the FIRST subtype names what the body is — a Bat God is a bat) and every type a
  Framing line (`_camera_line`: face visible / whole object / establishing view); lands are built
  from the style's world (its plants, skies, architecture), never a generic version of the terrain;
  artifact guidance never lists relic presentations (a listed "strung on a cord" was parroted).
  Also recalled at distillation, each with an UNKNOWN escape: `style_source_kind`
  (franchise / artist / movement — the franchise gate for de-naming; the `_FRANCHISE_PHRASES`
  table is only the offline fallback), `style_lineage` (a de-named production lineage for the
  render lead), `style_idiom` (list; the writer puts it on creatures as a figure idiom).
  Render-side (deck_studio): `_style_block_window(card_type)` — creatures/planeswalkers show the
  references to double blocks 0-14 (figure design), other types 0-9; `_assemble_flux_prompt` order =
  lead + colour-coverage clause, subject sentence, full block, rest of scene; `/api/generate` takes
  `seed` for like-for-like A/Bs (experiment hooks: FLUX_PROMPT_ORDER, STYLE_BLOCKS_DOUBLE,
  FIGURE_IDIOM, FIGURE_IDIOM_ALL, SCENE_CHECK, REDUX_EDGE_CROP, FLUX_LEAD_OVERRIDE, FLUX_GUARD_EXTRA).
  Block medium = declaration → stored-evidence keyword vote → LLM; the analyst's own short `Medium:`
  phrase matching the voted bucket (`_evidence_medium_phrase`, e.g. 'papyrus parchment' filed under
  'painted illustration') is inserted right after the bucket anchor. Colour coverage
  is measured from reference pixels (`pixel_palette`); character-heavy references (VLM yes/no,
  `prominent_character`) default the deck to Medium unless `style_reference.user_set`.
  End-of-batch inspection (`INSPECT` job, `_execute_inspect_job`, `vision_analyzer.inspect_render`):
  VLM defect checklist over the batch's renders, verdict in `.meta.json['inspection']`, one
  automatic re-roll then a final record-only pass; `POST /api/decks/<id>/inspect`; RENDER_INSPECT=0.
  Inspector = count protocol (heads/arms/hands/copies + yes/no) with text/signature confirmed on the
  top/bottom strips; with `takes>1` the final pass keeps the cleaner take (`_pick_cleaner_take`);
  a final-pass signature/text-only verdict sets `frame_overrides.art_zoom=1.10` and recomposites
  (`_hide_edge_marks`) instead of re-rolling.
  Ties on defects between takes are decided by `vision_analyzer.pick_take` (reference + both takes
  on one sheet, asked twice with the takes swapped; only a consistent answer counts). The inspector
  also answers `composition=yes/no`, `face=yes/no` (a creature render that is only a fist or a
  back) and, for object/place cards, subject presence — recorded as
  `inspection.advisory` by default (`INSPECT_COMPOSITION=advisory|enforce|off`) until the
  false-positive rate is known.
  Writer backstops in order: preamble strip → opening-rule retry → franchise strip (franchise NAME
  only — `_strip_franchise_sentences(out, franchise_name)`, never the style hint) → example-leak →
  unpaintable-abstraction strip → (flat media: rewrite without light words naming the offending
  words, up to two passes, unpaintable strip again, then sentence-level light strip as last resort) → colour rewrite when a coloured flat style's scene names no colour word (`_names_a_colour`,
  `_is_coloured_style`; bare line art otherwise) → invented-cyclops fix → sentence/word cap (3 sentences / 64 words) → dangling-tail
  fix → tidy → scene checklist re-roll (deterministic `_person_problems` word check for artifact/land
  first, then the LLM checklist) → final cleanup (unpaintable / cyclops / light strip / dangling / tidy run once more, because every
  rewrite path can reintroduce what an earlier strip removed; `_tidy_prompt` also drops markdown
  markers, writer notes and lettering clauses — a quoted 'A' on a ring becomes letters in the art)
  → empty guard. Writer instructions never carry concrete example
  nouns — the writer parrots them into scenes (`test_flat_media_line_has_no_example_nouns`). Flat media = ink/cel/comic/papyrus/fresco/
  hieroglyph/woodblock/pixel/flat opaque paint (`is_flat` in `generate_subject_with_ai`).
  Idiom phrases about writing (glyph/symbol/lettering/text/script) are filtered
  (`_IDIOM_WRITING_WORDS`).
  STANDING RULE: deck-agnostic and style-agnostic — a new style must work with zero code
  changes; derive facts from the declaration, model knowledge and vision reads, never tables.
- **18 GB memory rule**: FLUX and the LLM/VLM cannot be co-resident. `mlx_llm.unload()` is
  called before loading FLUX; the in-process guard (`_ollama_work_*`/`_wait_for_ollama_idle`,
  historical names) waits for in-flight LLM work to finish before generating.
- `MODEL_OPTIONS` dict defines the FLUX models; `LOCAL_MODELS` in `local_image_generator.py` maps
  each to its mflux config. `active_model_key` selects the active model; `backend_config.json` persists it.
- All MLX imports are lazy (inside functions) so CI can import the modules without MLX installed.

### Key Globals in deck_studio.py
- `generation_lock` — threading.Lock protecting `generation_status` dict. **Always** use `with generation_lock:` for status updates.
- `generation_status` — dict of `{card_name: {status, message, has_raw_art, has_composite}}`
- `is_generating` — bool flag for batch generation; checked by workers for cancellation
- `active_model_key` — current model selection (e.g. `'local-flux-schnell'`)
- `cards_db`, `prompts_map` — in-memory card/prompt data for the active deck

### Security
- Path traversal protection: `_is_safe_deck_id()`, `_safe_deck_dir()`, `before_request` hook validates all `deck_id` URL params
- Image serving: `_safe_serve_image()` validates slugs, `_safe_inspiration_path()` validates filenames
- DOM XSS prevention: `escapeHtml()` used for all user-derived content in innerHTML
- Upload limit: `MAX_CONTENT_LENGTH` = 16MB
- Debug mode auto-disabled when `--host` is not localhost

### Generation Pipeline (Local)
```
batch_generate_worker()                    # ThreadPoolExecutor, 1 worker for local
  → _prefetch_scryfall_refs()              # Parallel pre-fetch (8 threads) before batch starts
  → generate_art_for_card()                # Per-card orchestration
    → _generate_local()                    # Builds styled prompt, picks reference image
      → gen.generate_with_reference()      # img2img with torch.inference_mode()
      → OR gen.generate()                  # txt2img fallback
    → save raw PNG + metadata JSON
    → render_composite()                   # SVG card frame overlay
    → update generation_status
```

### Frontend (embedded in deck_studio.py)
- All JS is inline in the HTML template (starts around line ~5461)
- Key JS globals: `allCards`, `selectedCard`, `checkedCards` (Set), `pinnedCards` (Set), `modelConfig`
- `startPolling()` polls `/api/status` every 2s, updates card badges and detail panel
- Card grid rendered by `renderGrid()` — uses `checkedCards.has(card.name)` for checkbox state
- Model dropdown controls cloud/local model selection directly

## Pitfalls and Hard-Won Lessons

- **Card names with apostrophes**: Never use inline `onclick` with template literal card names (e.g. `onclick="fn('${card.name}')"`). Apostrophes in names like "Assassin's Trophy" break the JS string. Always use `addEventListener` with closures. Also use `escapeHtml()` when inserting card names via innerHTML.
- **Port 5001**: Default port is 5001. macOS AirPlay Receiver binds port 5000 — avoid using it.
- **No cloud / no API key**: The pipeline is MLX-native local-only. There is no OpenAI backend — don't add `openai_client`/API-key guards. `/api/generate` just needs the FLUX worker (auto-loaded on demand).
- **18 GB memory rule**: FLUX (~13 GB) and the mlx-lm/mlx-vlm models (~5 GB) can't co-reside. They run in separate subprocesses (`flux_worker.py` / `mlx_worker.py`) and are mutually evicted; all heavy work is serialized under `gpu_coord.GPU_LOCK`. Don't load two heavy models in-process.
- **Stale Flask server**: After editing `deck_studio.py`, you must restart Flask to pick up changes. Kill with `lsof -ti:<port> | xargs kill -9` then restart. (Worker subprocesses re-spawn on demand, so edits to `flux_worker.py`/`mlx_worker.py` apply on the next generation.)
- **Prompt merging**: When regenerating prompts for a subset of cards, `art_prompts.json` must be merged (not overwritten) to preserve other cards' prompts.
- **Firefox popup lifecycle**: Firefox closes extension popups when file picker dialogs open. File import must use a dedicated tab page (import.html), not the popup.

## Validation Requirements

**CRITICAL: ALWAYS test with Playwright and local models BEFORE declaring success and releasing.** Never commit, merge, tag, or release without first verifying the change works in the actual browser using local generation. The full validation loop is:

1. **Restart the server** (changes to .py files require restart)
2. **Open the actual browser UI** via Playwright MCP — this is what the user sees
3. **Confirm the FLUX model is selected** (`local-flux-schnell` is the only model) — it auto-loads on first generate
4. **Navigate to the affected card/feature** and trigger the exact action that was changed
5. **For generation changes**: trigger a generation, check the FLUX prompt in server logs, **and view the generated image** to verify the subject matches the prompt
6. **Take a screenshot** and verify the result matches expectations
7. **If it doesn't work, keep iterating** — do NOT report success to the user
8. **Only commit/merge/release when you've visually confirmed** the fix in the actual browser
9. **For UI changes**: verify elements appear, buttons work, progress bars show/hide
10. **NEVER skip this** — the user has been burned by untested releases multiple times

If you don't know what "good" looks like for a visual change, **ask the user** rather than guessing. Study reference images thoroughly before implementing. One well-researched implementation beats eight guess-and-check iterations.

Common traps to avoid:
- Tiny crops on transparent backgrounds hide real problems
- Python test renders don't match browser canvas rendering
- The WYSIWYG frame designer canvas and the final composite use different code paths — test both
- Browser caching can show stale results — always hard-refresh

## Testing Strategy

**Unit tests** run via `pytest tests/` (~185 tests, <2s). Pre-commit hook runs automatically. Manual validation is done via the Playwright MCP and curl.

### Starting the Server for Testing
```bash
# Kill any existing instance and start fresh
lsof -ti:5001 | xargs kill -9 2>/dev/null
python3 deck_studio.py --port 5001 > /tmp/flask-server.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/  # expect 200
```

### Playwright MCP (configured in .mcp.json)
Use Playwright for all UI validation. Key patterns:

```
# Navigate and wait for cards to load
browser_navigate → http://localhost:5001/
browser_wait_for → text: "Arcane Signet" (or any known card name), time: 10

# Check UI state without screenshots
browser_snapshot → returns accessibility tree with refs
browser_evaluate → run JS to inspect state (e.g. checkedCards.size)

# Interact with elements
browser_click → use ref from snapshot
browser_select_option → for dropdowns

# Verify backend state
curl /api/status → check generation_status
curl /api/model-config → check active model
tail /tmp/flask-server.log → check server-side logs
```

### What to Validate After Changes
- **Backend changes**: `curl` the endpoint directly, check response code and body
- **Frontend JS changes**: Use `browser_evaluate` to call functions or check variable state
- **UI changes**: `browser_snapshot` to verify element presence, `browser_take_screenshot` for visual checks
- **Generation changes**: Trigger generate via curl, wait, check `/api/status` and server logs

### Cleanup After Testing
```bash
lsof -ti:5001 | xargs kill -9 2>/dev/null    # stop server
browser_close                                  # close Playwright
```

## API Quick Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/status` | GET | All card generation statuses + `is_generating` flag |
| `/api/generate` | POST | Single card generation `{card_name, custom_prompt?, feedback?}` |
| `/api/generate-batch` | POST | Batch generation `{card_names[], skip_existing}` |
| `/api/stop-batch` | POST | Cancel batch generation |
| `/api/model-config` | GET/POST | Get or set active model |
| `/api/backend` | GET/POST | Get or set cloud/local mode |
| `/api/decks` | GET | List all decks |
| `/api/decks/<id>/activate` | POST | Switch active deck |
| `/api/decks/<id>/deck-info` | GET | Deck metadata, inspiration images, style info |
| `/api/decks/<id>/regenerate-prompts` | POST | Regenerate art prompts `{use_ai, card_names?}` |
| `/api/decks/<id>/export-manifest` | GET | Export deck as JSON manifest with embedded base64 images |
| `/api/decks/<id>/inspiration-image` | GET/POST/DELETE | Manage inspiration images |
| `/api/local-image-load` | POST | Load local SD model `{model_key}` |
| `/api/local-image-unload` | POST | Unload local model, free GPU memory |
| `/api/recomposite` | POST | Re-render card frame for a card |

## Git Workflow

- Always work on a feature branch — never commit directly to `main`
- Merge to main via PR, then tag with semantic version
- Use `kanban-coordinator` agent to keep `KANBAN.md` up to date when planning/completing work

## CI/CD Pipeline

Three GitHub Actions workflows automate issue resolution, PR testing, and releases.
Code review is done locally (e.g. `/code-review`) before pushing — there is no
AI review action in CI.

### Workflows

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| Claude Issue Fix | `claude-issue-fix.yml` | Issue labeled `claude`, or `@claude` comment on labeled issue | Claude reads the issue, creates a branch, implements a fix, opens a PR |
| PR Tests | `claude-pr-review.yml` | PR opened/synchronized/reopened | Basic tests: syntax, imports, pytest, server health, extension manifest |
| Auto Release | `claude-auto-release.yml` | PR review approved | Waits for checks, squash merges, tags, creates GitHub release |

### Label Strategy

| Label | Purpose |
|-------|---------|
| `claude` | Adding this to an issue triggers Claude to implement a fix |
| `semver:patch` | Version bump: patch (default if no semver label) |
| `semver:minor` | Version bump: minor |
| `semver:major` | Version bump: major |

### CI Test Constraints (Ubuntu Runner)

The `basic-tests` job runs on Ubuntu without GPU or torch (numpy is in the
base requirements — frame compositing needs it). It validates:

- **Syntax**: `py_compile` on all `.py` files
- **Imports**: All core modules except those requiring GPU/heavy deps
- **Server health**: Flask starts, returns 200 on `/`, `/api/status`, `/api/decks`, `/api/model-config`
- **Extension manifest**: Valid JSON, MV3, required fields present

**Modules skipped in CI import check** (require torch, numpy, or have heavy deps):
- `local_image_generator.py` — requires torch, diffusers, transformers
- `color_transfer.py` — requires numpy
- `test_prompt.py` — test harness, not a core module
- `generate_deck_art.py` — CLI script, not imported by the app

When adding new modules, ensure they can be imported on Ubuntu without torch/numpy, or add them to the skip list.

### Loop Prevention

All workflows use `GITHUB_TOKEN` (not a PAT), so workflow-created events (PRs, merges, tags) do not trigger other workflows. This prevents infinite loops.
