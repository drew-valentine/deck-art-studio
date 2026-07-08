# Full-Codebase Review — July 2026

Systematic review of the entire codebase, conducted as eight scoped passes run
in parallel, findings triaged and verified against the code, and all confirmed
fixes landed in this single PR. Baseline test suite went from 254 → 294 tests
(40 new regression tests: 12 frame, 8 utility, 8 MLX, 12 backend). All 294 pass.

## Scope

| # | Area | Files |
|---|------|-------|
| 1 | Backend core | `deck_studio.py` (setup, decks, versioning, generation orchestration, threading) |
| 2 | Backend API | `deck_studio.py` (endpoints, persistence, security hooks) |
| 3 | Frontend | `deck_studio.py` (inline HTML/JS template) |
| 4 | Frame renderer | `card_frame_renderer.py` |
| 5 | MLX/worker stack | `local_image_generator.py`, `mlx_llm.py`, `flux_worker.py`, `mlx_worker.py`, `gpu_coord.py`, `backend_config.py` |
| 6 | Utilities | `prompt_generator.py`, `vision_analyzer.py`, `scryfall_client.py`, `fetch_*.py`, `generate_deck_art.py`, build/tools scripts |
| 7 | Browser extension | `extension/*` |
| 8 | CI + tests | `.github/workflows/*`, `tests/*` |

Security helpers (`_is_safe_deck_id`, `_safe_deck_dir`, `_safe_serve_image`,
`_safe_inspiration_path`, `get_frame_asset`, `before_request`) were audited and
found **traversal-safe** — no path-escape findings.

---

## Findings — Fixed

### Security (highest impact)

- **[CRITICAL] auto-release approved by anyone** — `claude-auto-release.yml`
  gated only on `review.state == 'approved'` with no author check. On the
  planned public repo, any account's approval would auto-merge to main and cut
  a release. **Fix:** job `if:` now requires `author_association` in
  OWNER/MEMBER/COLLABORATOR.
- **[HIGH] approve-then-swap check bypass** — merge used the current branch head,
  not the approved SHA. **Fix:** `gh pr merge --match-head-commit "$HEAD_SHA"`.
- **[HIGH] issue-fix runnable by any commenter** — any `@claude` comment on a
  labeled issue drove Claude with `contents:write` + `Bash(git/gh)`. **Fix:**
  author-association gate on both the labeled and comment paths.
- **[HIGH] SVG injection via card fields** (`card_frame_renderer.py`) — power,
  toughness, mana symbols, loyalty, and defense (all user-editable overrides)
  were interpolated into SVG `<text>` unescaped; a `<` blanked the composite or
  injected markup. **Fix:** shared `_esc()` helper applied to every text sink.
- **[HIGH] attribute-unsafe escapeHtml** (frontend) — the `textContent→innerHTML`
  trick left `"`/`'` unescaped while used in `alt="…"`/`value="…"`; a card name
  with a quote (Un-cards) broke out of the attribute. **Fix:** replace-chain
  escaper that also escapes quotes.
- **[MEDIUM] extension fetches arbitrary URLs unbounded** — manifest fetch from
  any URL (Drive links) buffered the whole body → OOM. **Fix:** content-length
  cap before parse.
- **[MEDIUM] permissions hardening** — `claude-pr-review.yml` had no
  `permissions:` block (inherited write while running PR-author code). **Fix:**
  `contents: read`.

### Concurrency & state integrity

- **[HIGH] cross-deck prompt contamination** — `load_data` merged the new deck's
  prompts into the existing `prompts_map` without clearing, so a staple in two
  decks kept the wrong prompt. **Fix:** reset `prompts_map` before merge.
- **[HIGH] deck-switch mid-generation writes into the wrong deck** — the worker
  resolves paths/style/prompts from live globals. **Fix:** `/api/…/activate` and
  DELETE now return **409** while `is_generating` (the safe guard the reviewers
  offered vs. threading a full deck snapshot through the worker).
- **[HIGH] non-atomic JSON writes + unguarded reads** — an OOM-kill mid-write
  (documented failure mode) truncated deck.json/registry and bricked startup.
  **Fix:** `_atomic_json_dump` (temp + fsync + `os.replace`) and `_load_json_safe`
  (quarantine-and-default) used across persistence; `persist_lock` serializes
  read-modify-write of deck.json / art_prompts.json.
- **[HIGH] `_wait_for_ollama_idle` missing `global`** — the timeout force-reset
  rebound a dead local, so the idle event never re-fired and every later
  generation stalled the full 900 s. **Fix:** add `global`; also move the
  unload+signal in `_ollama_work_done` under the lock.
- **[MEDIUM] batch check-then-set race** — two `/api/generate-batch` POSTs both
  passed the `is_generating` check. **Fix:** atomic claim under `generation_lock`.
- **[MEDIUM] `_save_deck_meta_field` clobber** — long workers held a stale
  deck.json snapshot. **Fix:** re-read under `persist_lock`, update only own keys.
- **[MEDIUM] card-subject cross-deck clobber** — read active deck, persisted to
  URL deck. **Fix:** 409 when `deck_id != active_deck_id` (both handlers).
- **[MEDIUM] remove-card during generation / orphaned files** — **Fix:** 409 if
  the card is generating; cleanup now covers the back-face slug and the
  `art_versions/<slug>/` history.
- **[MEDIUM] delete last deck left stale state** — **Fix:** clear `cards_db`,
  `prompts_map`, `generation_status`; refuse deleting a deck a batch is writing.
- **[MEDIUM] cancel wrote art anyway** — the cancel check ran *after* the files
  were written. **Fix:** check `_cancel_single` right after generation, before
  save; `generate_single` clears any stale flag and 404s unknown cards.
- **[LOW] unlocked `generation_status` writes** — `load_data` seeding and
  `api_add_card`. **Fix:** wrapped in `generation_lock`.
- **[MEDIUM] backend_config non-atomic/unlocked** — **Fix:** `_config_lock` +
  atomic write.
- **[LOW] rotated-split front fallback duplicated the right half** — **Fix:**
  pass `None` (empty front slot); `render_composite_for_card` handles it.

### MLX / worker robustness

- **[MEDIUM] FLUX `_free()` never released the old model** → OOM landmine on a
  second model key. **Fix:** null `self._flux`/`_model_key` before GC.
- **[MEDIUM] dropped Popen on write failure** → orphaned 13 GB worker + respawn.
  **Fix:** `kill()`+`wait()` before nulling; `wait()` after final kill in unload.
- **[LOW] leaked temp PNG on failure**, **[LOW] watchdog kill race**, **[LOW]
  progress-callback accumulation**, **[LOW] first-download killed by 600 s
  watchdog** — all fixed (see `tests/test_review_mlx.py`).

### Utilities

- **[HIGH] no network timeouts** (`fetch_scryfall_art.py`) → hung generation
  thread. **Fix:** `timeout=15/30`.
- **[HIGH] rate-limit sleep never fired** (`scryfall_client.py`) — checked cache
  *after* the write; split-card slug mismatch. **Fix:** capture `was_cached`
  first, shared `_cache_slug`, inter-attempt sleep.
- **[MEDIUM] cache read unguarded + non-atomic** → a truncated cache permanently
  aborted imports. **Fix:** try/except delete-and-refetch, atomic write.
- **[MEDIUM] `card_database.json` rewritten in place** — **Fix:** atomic write.
- **[MEDIUM] split-card slug produced nested dirs** (`generate_deck_art.py`) —
  **Fix:** regex slug. Dead SDXL harness `test_prompt.py` deleted.

### Frontend

- **[HIGH] `updateModelDropdown` never existed** → ReferenceError swallowed, UI
  never refreshed after model load. **Fix:** `populateModelDropdown()`.
- **[HIGH] progress pollers froze forever on 404** (server restart mid-job) —
  **Fix:** shared `pollJobProgress()` that rejects after N consecutive misses;
  all six poll loops converted, callers get a toast + button re-enable.
- **[HIGH] `FrameCompositor` stacked listeners per init** → dragging/zoom
  multiplied after deck switches. **Fix:** create the compositor and wire zoom
  controls once (`_fdWired`).
- **[MEDIUM] version history used the front face key** while viewing a back —
  **Fix:** `faceKeyFor`/`faceSlugFor`.
- **[MEDIUM] `checkedCards` kept ghost selections** after remove/switch — **Fix:**
  prune against live cards in `renderGrid`.
- **[LOW] double-escaped dialog titles**, **[LOW] `generateArt` no error
  feedback**, **[LOW] VRAM regex `\\d` never matched** (raw string), **[LOW]
  import bar stuck error-red on retry** — all fixed.

### Extension

- Export-all now uses `getAllCards()` (was active-deck only); zero-card import no
  longer reported as success / auto-activated; `originalSrcCache` → `WeakMap`
  (detached-node leak); `popup.js` meta line via `textContent`.

---

## Deferred (documented, not changed in this PR)

These need real-browser testing or architectural work out of scope for a
low-risk sweep; each is safe to leave and tracked for follow-up.

- **Extension `get-all-cards` oversized-message protocol** — with "All Decks"
  active, all base64 art ships in one runtime message; Chrome rejects oversized
  messages. Needs a per-card fetch protocol + real MV3 testing.
- **Extension MV3 service-worker keepalive** for multi-minute imports.
- **Extension Firefox MV3 host-permissions grant UX.**
- **`resolve-uuid` Scryfall throttle** in the content script.
- **Full deck-content snapshot through the generation worker** — the 409 guard
  closes the data-corruption hole; threading a snapshot would additionally allow
  switching decks *during* a generation. Larger change, deferred.
- **Frontend low-severity polish:** preview blob-URL revocation, frame-preview
  request-ordering guard, `enableBulkButtons` timeout cancellation, the
  Steer&Render optimistic-state double-submit window — narrow, low-impact.
- **`generate_deck_art.py` / `_generate_openai`** — dead cloud path; fixes
  applied where trivial, full removal deferred to a dedicated cleanup.

---

## Verification

- `pytest tests/` — **294 passed** (254 baseline + 40 new).
- Browser smoke test via Playwright: app loads with **0 console errors**, frame
  designer renders and switches styles, version history and prompt round-trip
  intact.
- Curl checks: NaN art-zoom clamped (not persisted), non-dict frame settings /
  overrides rejected (400), bad version int rejected (400), unknown card 404,
  deck activate still 200.
