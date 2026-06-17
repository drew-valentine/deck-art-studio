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
- `active_model_key` — current model selection (e.g. `'local-sdxl-turbo'`)
- `use_scryfall_ref` — whether to use per-card Scryfall art as img2img reference
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
- **API key guard**: `/api/generate` and `/api/generate-batch` check `active_model_key` backend before requiring an OpenAI API key. Don't add blanket `if not openai_client` guards — local mode doesn't need a key.
- **SDXL Turbo on MPS**: Must use float32 (not float16) — float16 produces solid black images on Apple Silicon.
- **Stale Flask server**: After editing `deck_studio.py`, you must restart Flask to pick up changes. Kill with `lsof -ti:<port> | xargs kill -9` then restart.
- **Prompt merging**: When regenerating prompts for a subset of cards, `art_prompts.json` must be merged (not overwritten) to preserve other cards' prompts.
- **Firefox popup lifecycle**: Firefox closes extension popups when file picker dialogs open. File import must use a dedicated tab page (import.html), not the popup.

## Validation Requirements

**CRITICAL: ALWAYS test with Playwright and local models BEFORE declaring success and releasing.** Never commit, merge, tag, or release without first verifying the change works in the actual browser using local generation. The full validation loop is:

1. **Restart the server** (changes to .py files require restart)
2. **Open the actual browser UI** via Playwright MCP — this is what the user sees
3. **Switch to a local SDXL model** (e.g. SDXL Lightning) — cloud models can't be tested without API keys
4. **Navigate to the affected card/feature** and trigger the exact action that was changed
5. **For generation changes**: trigger a local generation, check the CLIP prompt in server logs, **and view the generated image** to verify the subject matches the prompt
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

Three GitHub Actions workflows automate issue resolution, code review, and releases.

### Workflows

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| Claude Issue Fix | `claude-issue-fix.yml` | Issue labeled `claude`, or `@claude` comment on labeled issue | Claude reads the issue, creates a branch, implements a fix, opens a PR |
| Claude PR Review | `claude-pr-review.yml` | PR opened/synchronized/reopened, or `@claude` in review comment | Runs basic tests + AI code review in parallel |
| Auto Release | `claude-auto-release.yml` | PR review approved | Waits for checks, squash merges, tags, creates GitHub release |

### Label Strategy

| Label | Purpose |
|-------|---------|
| `claude` | Adding this to an issue triggers Claude to implement a fix |
| `semver:patch` | Version bump: patch (default if no semver label) |
| `semver:minor` | Version bump: minor |
| `semver:major` | Version bump: major |

### CI Test Constraints (Ubuntu Runner)

The `basic-tests` job runs on Ubuntu without GPU, torch, or numpy. It validates:

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
