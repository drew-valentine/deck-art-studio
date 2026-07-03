#!/usr/bin/env python3
"""
=============================================================================
  HEADS I WIN — Deck Art Studio
  Web interface for generating, previewing, and managing custom MTG card art
=============================================================================

Usage:
  python3 deck_studio.py                    # Starts on http://localhost:5001
  python3 deck_studio.py --port 8080        # Custom port
  python3 deck_studio.py --host 0.0.0.0     # Accessible on LAN

Requires: pip install flask openai Pillow cairosvg
"""

import io
import json
import os
import re
import sys
import time
import hashlib
import argparse
import threading
import base64
import urllib.request
from pathlib import Path
from datetime import datetime

from flask import Flask, jsonify, request, send_file, Response
from PIL import Image
from fetch_scryfall_art import fetch_card_art, ART_DIR as SCRYFALL_ART_DIR
from build_reference_collage import build_collage, COLLAGE_DIR
import backend_config

# v3: gpt-image-1 with reference images + SVG card frame renderer
from card_frame_renderer import (
    composite_card as render_composite,
    composite_split_card,
    composite_card_preview,
    resolve_frame_settings,
    render_frame_layer,
    render_text_overlay,
    FRAME_PRESETS,
    FRAME_STYLES,
    FRAME_LAYERS,
    FRAME_LAYER_ORDER,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
DECKS_DIR = SCRIPT_DIR / "decks"
SHARED_DIR = SCRIPT_DIR / "shared"
DECK_REGISTRY_PATH = DECKS_DIR / "decks.json"

# Legacy single-deck paths (used only for migration check)
_LEGACY_CARD_DB = SCRIPT_DIR / "card_database.json"
_LEGACY_PROMPTS = SCRIPT_DIR / "art_prompts.json"

# Active deck paths — repointed by switch_deck()
CARD_DB_PATH = _LEGACY_CARD_DB  # will be updated
ART_PROMPTS_PATH = _LEGACY_PROMPTS
RAW_ART_DIR = SCRIPT_DIR / "dalle_art_raw"
COMPOSITE_DIR = SCRIPT_DIR / "dalle_cards"
PROXY_DIR = SCRIPT_DIR / "proxy_cards"
VERSIONS_DIR = SCRIPT_DIR / "art_versions"

DEFAULT_REF_IMAGE_PATH = SCRIPT_DIR / "okaun.png"  # Fallback reference image
API_KEY_PATH = SCRIPT_DIR / ".api_key"  # Persisted API key (gitignored)

CARD_W, CARD_H = 750, 1050
DPI = 300

# Style is baked into prompts — no prefix needed
STYLE_PREFIX = ""

# Reference image base64 cache (loaded once on startup)
ref_image_b64 = None

# Active deck identity
active_deck_id = None
active_deck_meta = {}  # name, created, style_preamble, inspiration_*, etc.

# Per-deck inspiration image path(s) (set by switch_deck, falls back to okaun.png)
active_inspiration_path = DEFAULT_REF_IMAGE_PATH
active_inspiration_paths = []  # All inspiration images for multi-image support

# Cache for inspiration composite PIL image (avoids rebuilding per card).
# Key: md5 hash of sorted (path, mtime) tuples. Value: PIL Image.
_inspiration_composite_cache = {'key': None, 'image': None}

# Card back generation status per deck_id
# Card Back is just a regular card in the deck — no special tracking needed

# ---------------------------------------------------------------------------
# Model configurations with pricing
# ---------------------------------------------------------------------------
# MLX-native (Apple Silicon, local-only) image models. Keys map to
# local_image_generator.LOCAL_MODELS via the 'model' field.
MODEL_OPTIONS = {
    'local-flux-schnell': {
        'model': 'flux-schnell-4bit', 'quality': 'standard',
        # 672x896 (3:4) NOT 768x1024: FLUX txt2img peak memory is dominated by a
        # ~14.5 GB weights floor (4-bit transformer + resident T5/CLIP + VAE)
        # plus resolution-scaled activations. At 768x1024 the peak hit 17.5 GB on
        # an 18 GB machine — a 0.5 GB margin that OS-OOM-killed Flask whenever any
        # other memory user coincided. 672x896 keeps the same 3:4 aspect (so the
        # card-frame composite stays aligned) while dropping the peak to ~15.5 GB
        # (2.5 GB headroom) with no visible quality loss for composited card art.
        'size': '672x896', 'landscape_size': '896x672',
        'supports_edit': False,
        'cost_per_image': 0.00, 'backend': 'local',
        'label': 'FLUX.1 schnell (4-bit)',
        'description': 'High quality, ~40-70s on M3 Pro. Recommended.',
    },
    # NB: no 8-bit option — it has no non-gated mirror (needs an HF login) and is
    # too large for an 18 GB machine, so it was a guaranteed load failure.
}
DEFAULT_MODEL_KEY = 'local-flux-schnell'

# Active model selection (mutable at runtime via API)
active_model_key = DEFAULT_MODEL_KEY


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB upload limit

openai_client = None
cards_db = []
prompts_map = {}
# Bumped whenever the card LIST or card capabilities change (deck load,
# add/remove, backfill). Open pages watch it via /api/status and refetch
# /api/cards — so new fields (e.g. is_split_halves) reach stale tabs
# without a hard refresh.
cards_revision = 0
generation_queue = []
generation_status = {}  # card_name -> {status, message, timestamp, attempt}
generation_lock = threading.Lock()
is_generating = False
batch_phase = None       # None | 'starting' | 'waiting_ollama' | 'prefetching' | 'generating'
batch_phase_detail = ''  # e.g. "Downloading reference art (12/50)..."
batch_deck_id = None               # Which deck owns the running batch (None = no batch)
_batch_generation_status = {}      # card_name -> status dict (batch worker writes here)
_cancel_single = set()             # card names whose single-card generation should be discarded
style_analysis_progress = {}  # empty = not running; active: {phase, current, total, message}
model_load_progress = {}      # empty = idle; active: {phase, message, pct, model_key, error}
ollama_pull_progress = {}     # empty = idle; active: {model, status, completed_gb, total_gb, pct}

# ---------------------------------------------------------------------------
# MLX unified-memory guard
# ---------------------------------------------------------------------------
# The MLX LLM/VLM models (mlx-lm Llama, mlx-vlm Qwen-VL) and the FLUX image
# transformer (mflux) all share the same Metal unified-memory pool — and on an
# 18 GB machine they cannot be co-resident. This guard tracks in-flight LLM/VLM
# work so the image pipeline can wait for it to finish and then unload the text
# models (freeing the full budget for FLUX) before generating.
#
# The function names below retain their historical "ollama" spelling so the many
# call sites stay stable; they now coordinate in-process MLX models, not a
# separate Ollama server.

_ollama_active_count = 0
_ollama_count_lock = threading.Lock()
ollama_idle = threading.Event()
ollama_idle.set()  # Initially idle


def _ollama_work_start():
    """Mark the start of background MLX LLM/VLM work. Clears the idle event."""
    global _ollama_active_count
    with _ollama_count_lock:
        _ollama_active_count += 1
        ollama_idle.clear()
    print(f"[mlx_guard] LLM/VLM work started (active={_ollama_active_count})")


def _ollama_work_done():
    """Mark the end of background MLX LLM/VLM work.

    When the last active thread finishes, unloads the resident text/vision model
    from unified memory and signals that it's safe to load FLUX.
    """
    global _ollama_active_count
    with _ollama_count_lock:
        _ollama_active_count = max(0, _ollama_active_count - 1)
        count = _ollama_active_count
    print(f"[mlx_guard] LLM/VLM work done (active={count})")
    if count == 0:
        _unload_all_ollama_models()
        ollama_idle.set()


def _style_progress_update(phase, current, total, message, sub_phase=None):
    """Update the style analysis progress dict (thread-safe)."""
    global style_analysis_progress
    with generation_lock:
        style_analysis_progress = {
            'phase': phase,
            'current': current,
            'total': total,
            'message': message,
        }
        if sub_phase:
            style_analysis_progress['sub_phase'] = sub_phase


def _style_progress_clear():
    """Clear style analysis progress (thread-safe)."""
    global style_analysis_progress
    with generation_lock:
        style_analysis_progress = {}


def _model_load_progress_update(phase, message, pct=0, model_key='', error=None):
    """Update model loading progress dict (thread-safe)."""
    global model_load_progress
    with generation_lock:
        model_load_progress = {
            'phase': phase,
            'message': message,
            'pct': min(100, int(pct)),
            'model_key': model_key,
        }
        if error:
            model_load_progress['error'] = error


def _model_load_progress_clear():
    """Clear model loading progress (thread-safe)."""
    global model_load_progress
    with generation_lock:
        model_load_progress = {}


def _ollama_pull_progress_update(model, status, completed_gb=0, total_gb=0, pct=0):
    """Update Ollama model pull progress dict (thread-safe)."""
    global ollama_pull_progress
    with generation_lock:
        ollama_pull_progress = {
            'model': model,
            'status': status,
            'completed_gb': round(completed_gb, 2),
            'total_gb': round(total_gb, 2),
            'pct': min(100, int(pct)),
        }


def _ollama_pull_progress_clear():
    """Clear Ollama pull progress (thread-safe)."""
    global ollama_pull_progress
    with generation_lock:
        ollama_pull_progress = {}


def _unload_all_ollama_models():
    """Free the resident MLX text/vision model from unified memory."""
    try:
        import mlx_llm
        mlx_llm.unload()
    except Exception as e:
        print(f"[mlx_guard] Could not unload MLX models: {e}")
    print("[mlx_guard] MLX LLM/VLM models unloaded from unified memory")


def _wait_for_ollama_idle(timeout=900):
    """Block until all background MLX LLM/VLM work finishes.

    Called by the generation pipeline before loading FLUX so the text/vision
    models can be unloaded first. Force-unloads on timeout to avoid stalls.

    The timeout MUST exceed a full style analysis (5 images + distillation can
    take 3-5 min): if FLUX generation gives up waiting and starts while the
    analysis is still running, the FLUX worker's ~13 GB co-resides with the
    analysis's ~5 GB vision/text models and the OS OOM-kills the server. 900 s
    leaves wide margin; the force-unload fallback still guards a wedged state.
    """
    if ollama_idle.is_set():
        return
    print(f"[mlx_guard] Waiting for LLM/VLM work to finish (timeout={timeout}s)...")
    result = ollama_idle.wait(timeout=timeout)
    if not result:
        print("[mlx_guard] Timeout waiting for LLM/VLM — force unloading")
        _unload_all_ollama_models()
        with _ollama_count_lock:
            _ollama_active_count = 0
        ollama_idle.set()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data():
    global cards_db, prompts_map

    if not CARD_DB_PATH.exists():
        print(f"WARNING: Card database not found at {CARD_DB_PATH}")
        cards_db = []
        prompts_map = {}
        return

    with open(CARD_DB_PATH) as f:
        data = json.load(f)

    # Support both formats: plain list (legacy) or dict with 'cards' key (new)
    if isinstance(data, dict) and 'cards' in data:
        cards_db = data['cards']
    elif isinstance(data, list):
        cards_db = data
    else:
        cards_db = []

    if ART_PROMPTS_PATH.exists():
        with open(ART_PROMPTS_PATH) as f:
            for entry in json.load(f):
                prompts_map[entry['name']] = entry['prompt']

    # Initialize status for all cards
    # Cards with .meta.json have actual AI-generated art ("complete").
    # Cards with only Scryfall art composites (no meta) are still "pending"
    # from the user's perspective — they haven't generated custom art yet.
    for card in cards_db:
        name = card['name']
        slug = name_to_slug(name)
        raw_path = RAW_ART_DIR / f"{slug}.png"
        comp_path = COMPOSITE_DIR / f"{slug}.png"
        meta_path = RAW_ART_DIR / f"{slug}.meta.json"

        if meta_path.exists():
            # Has AI-generated art
            generation_status[name] = {
                'status': 'complete',
                'message': 'Art generated',
                'has_raw_art': raw_path.exists(),
                'has_composite': comp_path.exists(),
            }
        else:
            # Scryfall-only or no art — still pending for generation
            generation_status[name] = {
                'status': 'pending',
                'message': 'Not yet generated',
                'has_raw_art': raw_path.exists(),
                'has_composite': comp_path.exists(),
            }

    # Backfill: composite Scryfall art for cards with no raw art yet
    scryfall_dir = DECKS_DIR / active_deck_id / "scryfall_art" if active_deck_id else None
    if scryfall_dir and scryfall_dir.exists():
        backfilled = 0
        for card in cards_db:
            name = card['name']
            slug = name_to_slug(name)
            raw_path = RAW_ART_DIR / f"{slug}.png"
            comp_path = COMPOSITE_DIR / f"{slug}.png"
            if raw_path.exists():
                continue  # Already has art

            # Find Scryfall art
            sf_path = None
            for ext in ('.jpg', '.png', '.jpeg'):
                p = scryfall_dir / f"{slug}{ext}"
                if p.exists():
                    sf_path = p
                    break
            if not sf_path:
                continue

            # Copy as raw art and composite with card frame
            try:
                img = Image.open(sf_path).convert('RGB')
                RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
                img.save(raw_path, 'PNG')
                COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)
                render_composite_for_card(card, raw_path, comp_path,
                                          deck_fs=active_deck_meta.get('frame_settings'))
                generation_status[name] = {
                    'status': 'complete',
                    'message': 'Scryfall art (original)',
                    'has_raw_art': True,
                    'has_composite': True,
                }
                backfilled += 1
            except Exception as e:
                print(f"  [backfill] Failed for {name}: {e}")

        if backfilled:
            print(f"  Backfilled {backfilled} cards with Scryfall art composites")

    # Backfill: add scryfall_id for cards missing it
    import re
    _uuid_re = re.compile(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})')
    _sf_cache_dir = SCRIPT_DIR / "shared" / "scryfall_cache"
    uuid_backfilled = 0
    for card in cards_db:
        if card.get('scryfall_id'):
            continue
        # Try art_crop_url first
        m = _uuid_re.search(card.get('art_crop_url', ''))
        if m:
            card['scryfall_id'] = m.group(1)
            uuid_backfilled += 1
        elif _sf_cache_dir.exists():
            # Fallback: scryfall cache file
            slug = name_to_slug(card['name'])
            cache_file = _sf_cache_dir / f"{slug}.json"
            if cache_file.exists():
                try:
                    sf_data = json.loads(cache_file.read_text())
                    sid = sf_data.get('id', '')
                    if sid:
                        card['scryfall_id'] = sid
                        uuid_backfilled += 1
                except Exception:
                    pass
    # Backfill: layout + card_faces for multi-face cards imported before
    # alternative-layout support. Scryfall cache first, live API as fallback
    # (10s socket timeout so an offline start never hangs).
    faces_backfilled = 0
    _multiface = [c for c in cards_db
                  if ' // ' in c.get('name', '') and not c.get('layout')]
    if _multiface:
        import socket
        from scryfall_client import fetch_card, scryfall_to_card_entry
        _old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(10)
        try:
            for card in _multiface:
                try:
                    sf = fetch_card(card['name'], set_code=card.get('set_code'),
                                    collector_number=card.get('collector_number'))
                    if not sf:
                        continue
                    fresh = scryfall_to_card_entry(sf)
                    if fresh.get('layout'):
                        card['layout'] = fresh['layout']
                    if fresh.get('card_faces'):
                        card['card_faces'] = fresh['card_faces']
                    if fresh.get('defense') is not None and card.get('defense') is None:
                        card['defense'] = fresh['defense']  # battles
                    if fresh.get('layout') or fresh.get('card_faces'):
                        faces_backfilled += 1
                except Exception as e:
                    print(f"  [backfill] Faces failed for {card['name']}: {e}")
        finally:
            socket.setdefaulttimeout(_old_timeout)
        if faces_backfilled:
            print(f"  Backfilled {faces_backfilled} multi-face cards with layout/face data")

    if uuid_backfilled or faces_backfilled:
        # Persist updated cards to disk
        if CARD_DB_PATH.exists():
            with open(CARD_DB_PATH) as f:
                deck_data = json.load(f)
            if isinstance(deck_data, dict) and 'cards' in deck_data:
                deck_data['cards'] = cards_db
            else:
                deck_data = cards_db
            with open(CARD_DB_PATH, 'w') as f:
                json.dump(deck_data, f, indent=2)

    # Backfill: Scryfall back-face art + composites for double-faced cards,
    # mirroring the front-face behavior (original art shows until generated)
    if scryfall_dir:
        import urllib.request as _urlreq
        back_backfilled = 0
        for card in cards_db:
            if not is_dfc(card):
                continue
            name = card['name']
            bslug = name_to_slug(face_key(name, 'back'))
            braw = RAW_ART_DIR / f"{bslug}.png"
            bcomp = COMPOSITE_DIR / f"{bslug}.png"

            # Download the back face's Scryfall art crop once
            back_url = card['card_faces'][1].get('art_crop_url', '')
            dest = scryfall_dir / f"{bslug}.jpg"
            if back_url and not dest.exists():
                try:
                    scryfall_dir.mkdir(exist_ok=True)
                    req = _urlreq.Request(back_url, headers={
                        "User-Agent": "MTGProxyDeckGen/1.0"})
                    with _urlreq.urlopen(req, timeout=10) as resp:
                        dest.write_bytes(resp.read())
                except Exception as e:
                    print(f"  [backfill] Back art failed for {name}: {e}")

            if braw.exists() and bcomp.exists():
                continue
            sf_path = next((scryfall_dir / f"{bslug}{ext}"
                            for ext in ('.jpg', '.png', '.jpeg')
                            if (scryfall_dir / f"{bslug}{ext}").exists()), None)
            if not sf_path:
                continue
            try:
                if not braw.exists():
                    img = Image.open(sf_path).convert('RGB')
                    RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
                    img.save(braw, 'PNG')
                COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)
                render_composite(back_face_card(card), str(braw), None, str(bcomp),
                                 deck_frame_settings=active_deck_meta.get('frame_settings'))
                back_backfilled += 1
            except Exception as e:
                print(f"  [backfill] Back composite failed for {name}: {e}")
        if back_backfilled:
            print(f"  Backfilled {back_backfilled} DFC back faces with Scryfall art composites")

    global cards_revision
    cards_revision = int(time.time() * 1000)

    print(f"Loaded {len(cards_db)} cards, {len(prompts_map)} prompts")


def _is_safe_deck_id(deck_id: str) -> bool:
    """Validate deck_id cannot escape DECKS_DIR via path traversal."""
    if not deck_id or '..' in deck_id or '/' in deck_id or '\\' in deck_id:
        return False
    resolved = (DECKS_DIR / deck_id).resolve()
    return resolved.parent == DECKS_DIR.resolve()


def _safe_deck_dir(deck_id: str):
    """Return validated deck directory path, or None if deck_id is unsafe."""
    if not _is_safe_deck_id(deck_id):
        return None
    return DECKS_DIR / deck_id


def name_to_slug(name):
    slug = (name.lower()
            .replace(BACK_FACE_SUFFIX.lower(), '__back')  # Back face of a DFC
            .replace(' // ', '__')   # Adventure / DFC split names
            .replace('/', '_')       # Any remaining slashes
            .replace(' ', '_')
            .replace(',', '')
            .replace("'", "")
            .replace('-', '_'))
    # Strip any path traversal attempts
    return slug.replace('..', '').strip('.')


# ---------------------------------------------------------------------------
# Double-faced cards (transform / modal DFC)
# ---------------------------------------------------------------------------
# The card NAME stays the unit of work ("Accursed Witch // Infectious Curse"):
# one status entry, one grid tile. The back face piggybacks with a " [back]"
# suffix on prompt keys / version names, and a "__back" suffix on file slugs.
BACK_FACE_SUFFIX = ' [back]'
DFC_LAYOUTS = {'transform', 'modal_dfc'}


def is_dfc(card) -> bool:
    """True when this card has a distinct back face with its own art."""
    return (card.get('layout') in DFC_LAYOUTS
            and len(card.get('card_faces') or []) >= 2)


def face_key(card_name, face='front'):
    """Prompt/version key for one face of a card."""
    return card_name + BACK_FACE_SUFFIX if face == 'back' else card_name


def back_face_card(card):
    """Merged card dict for the BACK face — face fields over card-level fields.

    Card-level extras (is_commander, color_identity, ...) are inherited so
    frame rendering behaves like the front face.
    """
    faces = card.get('card_faces') or []
    if len(faces) < 2:
        return None
    face = faces[1]
    merged = dict(card)
    for k in ('name', 'mana_cost', 'type_line', 'oracle_text',
              'power', 'toughness', 'loyalty', 'defense', 'flavor_text',
              'card_type'):
        merged[k] = face.get(k)
    merged['colors'] = face.get('colors') or card.get('colors', [])
    if not merged.get('card_type'):
        from scryfall_client import normalize_card_type
        merged['card_type'] = normalize_card_type(merged.get('type_line') or '')
    # The back face has its own designer overrides. Without any, inherit only
    # style-level front overrides — text overrides and art pan/zoom authored
    # for the front face must not leak onto the back.
    if card.get('frame_overrides_back') is not None:
        merged['frame_overrides'] = card['frame_overrides_back']
    else:
        overrides = dict(card.get('frame_overrides') or {})
        for k in ('text_overrides', 'art_offset', 'art_zoom'):
            overrides.pop(k, None)
        merged['frame_overrides'] = overrides
    merged.pop('frame_overrides_back', None)
    # Back faces render standalone — never as a split/adventure text box
    merged.pop('card_faces', None)
    merged.pop('layout', None)
    return merged


def is_rotated_split(card) -> bool:
    """Split-layout cards print as two rotated halves, EACH with its own
    art — classic splits (Fire // Ice) AND Rooms (Smoky Lounge // Misty
    Salon) alike, per the real printings."""
    return (card.get('layout') == 'split'
            and len(card.get('card_faces') or []) >= 2)


def has_second_art_face(card) -> bool:
    """Cards with a second independently generated art unit. The second unit
    rides the existing back-face machinery (" [back]" keys, "__back" slugs):
    DFC backs and the right half of a rotated split card."""
    return is_dfc(card) or is_rotated_split(card)


def split_half_card(card, idx):
    """Clean card dict for ONE half of a rotated split — renders as a normal
    mini card (no layout/card_faces, so no column treatment)."""
    faces = card.get('card_faces') or []
    if len(faces) <= idx:
        return None
    face = faces[idx]
    merged = dict(card)
    for k in ('name', 'mana_cost', 'type_line', 'oracle_text', 'power',
              'toughness', 'loyalty', 'defense', 'flavor_text', 'card_type'):
        merged[k] = face.get(k)
    # Split faces carry no colors on Scryfall — derive each half's frame
    # color from its own mana cost so Fire is red and Ice is blue.
    cost_colors = sorted({c for c in (face.get('mana_cost') or '') if c in 'WUBRG'},
                         key='WUBRG'.index)
    merged['colors'] = face.get('colors') or cost_colors or card.get('colors', [])
    # The combined card's identity (e.g. R+U) would gradient BOTH halves —
    # each half frames in its own color only
    merged['color_identity'] = merged['colors']
    if not merged.get('card_type'):
        from scryfall_client import normalize_card_type
        merged['card_type'] = normalize_card_type(merged.get('type_line') or '')
    # Style-level frame overrides only — text/art overrides were authored
    # against the combined card, not a half.
    overrides = dict(card.get('frame_overrides') or {})
    for k in ('text_overrides', 'art_offset', 'art_zoom'):
        overrides.pop(k, None)
    merged['frame_overrides'] = overrides
    merged.pop('frame_overrides_back', None)
    merged.pop('card_faces', None)
    merged.pop('layout', None)
    return merged


def render_composite_for_card(card, art_path, comp_path, deck_fs=None,
                              raw_art_dir=None):
    """Render a card's stored composite. Rotated splits combine BOTH halves'
    art into one rotated portrait card; everything else renders directly."""
    if is_rotated_split(card):
        _raw = Path(raw_art_dir) if raw_art_dir else RAW_ART_DIR
        right = _raw / f"{name_to_slug(face_key(card['name'], 'back'))}.png"
        composite_split_card(
            [split_half_card(card, 0), split_half_card(card, 1)],
            [str(art_path), str(right) if right.exists() else None],
            str(comp_path), deck_frame_settings=deck_fs)
    else:
        render_composite(card, str(art_path), None, str(comp_path),
                         deck_frame_settings=deck_fs)


def _resolve_card_ref(card_name):
    """Resolve a possibly face-qualified name ("<name> [back]") from cards_db.

    Returns (render_card, base_card, face, slug):
    - render_card: dict to feed the frame renderer (back-face merged for backs)
    - base_card:   the underlying cards_db entry
    - face:        'front' | 'back'
    - slug:        file slug for this face
    (None, None, face, None) when the card isn't found.
    """
    face = 'front'
    base_name = card_name
    if card_name.endswith(BACK_FACE_SUFFIX):
        face = 'back'
        base_name = card_name[:-len(BACK_FACE_SUFFIX)]
    card = next((c for c in cards_db if c['name'] == base_name), None)
    if not card:
        return None, None, face, None
    render_card = back_face_card(card) if face == 'back' else card
    if face == 'back' and render_card is None:
        return None, None, face, None
    return render_card, card, face, name_to_slug(face_key(base_name, face))


# ---------------------------------------------------------------------------
# API key persistence
# ---------------------------------------------------------------------------
def save_api_key(key: str):
    """Save API key to disk for persistence across restarts."""
    try:
        API_KEY_PATH.write_text(key.strip())
        API_KEY_PATH.chmod(0o600)  # Owner read/write only
    except Exception as e:
        print(f"WARNING: Could not save API key: {e}")


def load_saved_api_key():
    """No-op — the cloud (OpenAI) backend was removed in the MLX-native build.

    Kept as a stub so the startup sequence and any callers stay intact.
    """
    return


# ---------------------------------------------------------------------------
# Reference image loading
# ---------------------------------------------------------------------------
def load_reference_image():
    """Load and base64-encode the reference image for gpt-image-1.

    Uses the active deck's inspiration image if available,
    otherwise falls back to the default reference.
    """
    global ref_image_b64
    ref_path = active_inspiration_path
    if ref_path and ref_path.exists():
        with open(ref_path, 'rb') as f:
            ref_image_b64 = base64.standard_b64encode(f.read()).decode('utf-8')
        print(f"Reference image loaded: {ref_path.name}")
    elif DEFAULT_REF_IMAGE_PATH.exists():
        with open(DEFAULT_REF_IMAGE_PATH, 'rb') as f:
            ref_image_b64 = base64.standard_b64encode(f.read()).decode('utf-8')
        print(f"Reference image loaded (default): {DEFAULT_REF_IMAGE_PATH.name}")
    else:
        print(f"WARNING: No reference image found")
        ref_image_b64 = None


# ---------------------------------------------------------------------------
# Deck Management
# ---------------------------------------------------------------------------
import shutil


def deck_id_from_name(name: str) -> str:
    """Convert a human-readable deck name to a filesystem-safe ID."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-')[:60]


def _load_deck_registry() -> dict:
    """Load the deck registry from disk."""
    if DECK_REGISTRY_PATH.exists():
        with open(DECK_REGISTRY_PATH) as f:
            return json.load(f)
    return {'decks': [], 'active': None}


def _save_deck_registry(registry: dict):
    """Save the deck registry to disk."""
    DECK_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DECK_REGISTRY_PATH, 'w') as f:
        json.dump(registry, f, indent=2)


def list_all_decks() -> list[dict]:
    """List all decks with summary info."""
    registry = _load_deck_registry()
    results = []
    for d in registry.get('decks', []):
        deck_dir = DECKS_DIR / d['id']
        card_db = deck_dir / "deck.json"
        card_count = 0
        complete_count = 0
        if card_db.exists():
            with open(card_db) as f:
                data = json.load(f)
                cards = data.get('cards', data) if isinstance(data, dict) else data
                if isinstance(cards, list):
                    card_count = len(cards)
                    comp_dir = deck_dir / "composites"
                    for c in cards:
                        slug = name_to_slug(c['name'])
                        if (comp_dir / f"{slug}.png").exists():
                            complete_count += 1

        # Check for inspiration image
        has_inspiration = False
        if card_db.exists():
            try:
                insp = data.get('inspiration_image') if isinstance(data, dict) else None
                has_inspiration = bool(insp and (deck_dir / insp).exists())
            except Exception:
                pass

        results.append({
            'id': d['id'],
            'name': d.get('name', d['id']),
            'created': d.get('created', ''),
            'card_count': card_count,
            'complete_count': complete_count,
            'is_active': d['id'] == active_deck_id,
            'has_inspiration': has_inspiration,
        })
    return results


def create_deck(deck_name: str, cards: list = None, prompts: list = None,
                style_preamble: str = None) -> str:
    """Create a new deck directory and register it.

    Returns the deck_id.
    """
    deck_id = deck_id_from_name(deck_name)
    deck_dir = DECKS_DIR / deck_id

    # Handle name collisions
    base_id = deck_id
    counter = 2
    while deck_dir.exists():
        deck_id = f"{base_id}-{counter}"
        deck_dir = DECKS_DIR / deck_id
        counter += 1

    # Create directory structure
    deck_dir.mkdir(parents=True, exist_ok=True)
    (deck_dir / "raw_art").mkdir(exist_ok=True)
    (deck_dir / "composites").mkdir(exist_ok=True)
    (deck_dir / "art_versions").mkdir(exist_ok=True)
    (deck_dir / "scryfall_art").mkdir(exist_ok=True)

    # Save deck.json
    deck_data = {
        'name': deck_name,
        'created': datetime.now().isoformat(),
        'style_preamble': style_preamble,
        'inspiration_image': None,           # filename in deck dir
        'inspiration_style_description': '',  # from GPT-4o vision analysis
        'cards': cards or [],
        # Showcase is the default frame for new decks (full-art bars — the
        # style key is 'godzilla' for historical reasons)
        'frame_settings': {'style': 'godzilla'},
        'art_orientation': 'portrait',
    }
    with open(deck_dir / "deck.json", 'w') as f:
        json.dump(deck_data, f, indent=2)

    # Save art_prompts.json
    if prompts:
        with open(deck_dir / "art_prompts.json", 'w') as f:
            json.dump(prompts, f, indent=2)

    # Register in deck list
    registry = _load_deck_registry()
    registry['decks'].append({
        'id': deck_id,
        'name': deck_name,
        'created': deck_data['created'],
    })
    _save_deck_registry(registry)

    print(f"  Created deck: {deck_name} ({deck_id})")
    return deck_id


def switch_deck(deck_id: str) -> bool:
    """Switch the active deck — repoints all global path variables."""
    global active_deck_id, active_deck_meta, active_inspiration_path, active_inspiration_paths
    global CARD_DB_PATH, ART_PROMPTS_PATH, RAW_ART_DIR, COMPOSITE_DIR
    global PROXY_DIR, VERSIONS_DIR

    deck_dir = _safe_deck_dir(deck_id)
    if not deck_dir or not deck_dir.exists():
        print(f"  Deck not found: {deck_id}")
        return False

    active_deck_id = deck_id

    # Repoint all path globals to this deck's directories
    CARD_DB_PATH = deck_dir / "deck.json"
    ART_PROMPTS_PATH = deck_dir / "art_prompts.json"
    RAW_ART_DIR = deck_dir / "raw_art"
    COMPOSITE_DIR = deck_dir / "composites"
    PROXY_DIR = deck_dir / "composites"  # alias — composites ARE the proxies now
    VERSIONS_DIR = deck_dir / "art_versions"

    # Ensure dirs exist
    RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
    COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure scryfall_art dir exists (may not for older decks)
    (deck_dir / "scryfall_art").mkdir(exist_ok=True)

    # Load deck metadata
    if CARD_DB_PATH.exists():
        with open(CARD_DB_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict) and 'cards' in data:
            active_deck_meta = {k: v for k, v in data.items() if k != 'cards'}
        else:
            active_deck_meta = {'name': deck_id}

    # Clear inspiration composite cache on deck switch
    _inspiration_composite_cache['key'] = None
    _inspiration_composite_cache['image'] = None

    # Set per-deck inspiration image path(s)
    # Multi-image support: use inspiration_images array if present
    insp_images = active_deck_meta.get('inspiration_images', [])
    insp_file = active_deck_meta.get('inspiration_image')

    if insp_images:
        # Multi-image mode
        active_inspiration_paths = [
            deck_dir / img['filename'] for img in insp_images
            if (deck_dir / img['filename']).exists()
        ]
        if active_inspiration_paths:
            active_inspiration_path = active_inspiration_paths[0]
            print(f"  Using {len(active_inspiration_paths)} inspiration images")
        else:
            active_inspiration_path = DEFAULT_REF_IMAGE_PATH
            active_inspiration_paths = []
    elif insp_file and (deck_dir / insp_file).exists():
        # Legacy single-image — auto-migrate to array format
        active_inspiration_path = deck_dir / insp_file
        active_inspiration_paths = [active_inspiration_path]
        print(f"  Using deck inspiration: {insp_file} (migrating to multi-image)")
        _migrate_single_inspiration(deck_id, insp_file)
    else:
        active_inspiration_path = DEFAULT_REF_IMAGE_PATH
        active_inspiration_paths = []

    # Update registry's active
    registry = _load_deck_registry()
    registry['active'] = deck_id
    _save_deck_registry(registry)

    # Clear stale generation status from previous deck
    with generation_lock:
        generation_status.clear()

    # Reload card data into globals (re-seeds generation_status from disk)
    load_data()

    print(f"  Switched to deck: {deck_id}")
    return True


def delete_deck(deck_id: str) -> bool:
    """Delete a deck and its files."""
    deck_dir = _safe_deck_dir(deck_id)
    if not deck_dir or not deck_dir.exists():
        return False

    shutil.rmtree(deck_dir)

    registry = _load_deck_registry()
    registry['decks'] = [d for d in registry['decks'] if d['id'] != deck_id]
    if registry.get('active') == deck_id:
        registry['active'] = registry['decks'][0]['id'] if registry['decks'] else None
    _save_deck_registry(registry)

    return True


def migrate_legacy_deck():
    """Migrate the single-deck layout to the multi-deck structure.

    Called once on first startup if legacy files exist but decks/ doesn't.
    """
    if not _LEGACY_CARD_DB.exists():
        return  # nothing to migrate

    if DECK_REGISTRY_PATH.exists():
        return  # already migrated

    print("  Migrating legacy deck to multi-deck structure...")

    deck_id = "heads-i-win-tails-you-lose"
    deck_dir = DECKS_DIR / deck_id
    deck_dir.mkdir(parents=True, exist_ok=True)

    # --- Migrate card_database.json → deck.json ---
    with open(_LEGACY_CARD_DB) as f:
        cards = json.load(f)
    deck_data = {
        'name': 'Heads I Win, Tails You Lose',
        'created': datetime.now().isoformat(),
        'style_preamble': None,  # uses default
        'cards': cards,
    }
    with open(deck_dir / "deck.json", 'w') as f:
        json.dump(deck_data, f, indent=2)

    # --- Migrate art_prompts.json ---
    if _LEGACY_PROMPTS.exists():
        shutil.copy2(_LEGACY_PROMPTS, deck_dir / "art_prompts.json")

    # --- Move art directories ---
    for src_name, dst_name in [
        ("dalle_art_raw", "raw_art"),
        ("dalle_cards", "composites"),
        ("art_versions", "art_versions"),
        ("ref_collages", "ref_collages"),
    ]:
        src = SCRIPT_DIR / src_name
        dst = deck_dir / dst_name
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            print(f"    Moved {src_name}/ → decks/{deck_id}/{dst_name}/")

    # --- Move shared resources ---
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    for dirname in ("fonts", "pips", "scryfall_art"):
        src = SCRIPT_DIR / dirname
        dst = SHARED_DIR / dirname
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            print(f"    Moved {dirname}/ → shared/{dirname}/")
        # Create symlink back so existing code still works
        if dst.exists() and not src.exists():
            try:
                src.symlink_to(dst)
            except Exception:
                pass  # symlinks may not work everywhere

    # --- Create deck registry ---
    registry = {
        'decks': [{
            'id': deck_id,
            'name': 'Heads I Win, Tails You Lose',
            'created': deck_data['created'],
        }],
        'active': deck_id,
    }
    _save_deck_registry(registry)

    print(f"  Migration complete! Deck: {deck_id}")


# ---------------------------------------------------------------------------
# Art Versioning
# ---------------------------------------------------------------------------

def _versions_dir_for(slug: str) -> Path:
    """Return the versioned art directory for a card slug."""
    return VERSIONS_DIR / slug


def archive_current_art(card_name: str) -> dict | None:
    """Archive the current raw art + composite + metadata before overwriting.

    Returns a version info dict, or None if there's nothing to archive.
    """
    slug = name_to_slug(card_name)
    raw_path = RAW_ART_DIR / f"{slug}.png"

    if not raw_path.exists():
        return None  # nothing to archive

    vdir = _versions_dir_for(slug)
    vdir.mkdir(parents=True, exist_ok=True)

    # Determine next version number
    existing = sorted(vdir.glob("v*_raw.png"))
    if existing:
        last_num = max(int(p.stem.split('_')[0][1:]) for p in existing)
        version_num = last_num + 1
    else:
        version_num = 1

    prefix = f"v{version_num}"

    # Copy raw art
    shutil.copy2(raw_path, vdir / f"{prefix}_raw.png")

    # Copy composite if it exists
    comp_path = COMPOSITE_DIR / f"{slug}.png"
    if comp_path.exists():
        shutil.copy2(comp_path, vdir / f"{prefix}_composite.png")

    # Copy metadata if it exists
    meta_path = raw_path.with_suffix('.meta.json')
    meta = {}
    if meta_path.exists():
        shutil.copy2(meta_path, vdir / f"{prefix}_meta.json")
        with open(meta_path) as f:
            meta = json.load(f)

    # Save version info
    version_info = {
        'version': version_num,
        'prefix': prefix,
        'timestamp': meta.get('timestamp', datetime.now().isoformat()),
        'model': meta.get('model', 'unknown'),
        'quality': meta.get('quality', 'unknown'),
        'cost_estimate': meta.get('cost_estimate', 0),
        'prompt_sent': meta.get('prompt_sent', ''),
        'feedback': meta.get('feedback'),
        'used_scryfall_ref': meta.get('used_scryfall_ref', False),
    }

    # Update versions manifest
    manifest_path = vdir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = {'card_name': card_name, 'slug': slug, 'versions': []}

    manifest['versions'].append(version_info)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    return version_info


def list_versions(card_name: str) -> list:
    """List all archived versions for a card."""
    slug = name_to_slug(card_name)
    manifest_path = _versions_dir_for(slug) / "manifest.json"
    if not manifest_path.exists():
        return []
    with open(manifest_path) as f:
        manifest = json.load(f)
    return manifest.get('versions', [])


def revert_to_version(card_name: str, version_num: int) -> tuple[bool, str]:
    """Restore a specific archived version as the active art.

    Copies the versioned raw art + composite back to the active directories,
    then re-composites to ensure the frame is current.
    """
    slug = name_to_slug(card_name)
    vdir = _versions_dir_for(slug)
    prefix = f"v{version_num}"

    versioned_raw = vdir / f"{prefix}_raw.png"
    if not versioned_raw.exists():
        return False, f"Version {version_num} not found"

    # Archive current art first (so we don't lose it)
    archive_current_art(card_name)

    # Restore raw art
    raw_path = RAW_ART_DIR / f"{slug}.png"
    shutil.copy2(versioned_raw, raw_path)

    # Restore metadata if available
    versioned_meta = vdir / f"{prefix}_meta.json"
    if versioned_meta.exists():
        shutil.copy2(versioned_meta, raw_path.with_suffix('.meta.json'))

    # Re-composite with current frame renderer. "<name> [back]" keys resolve
    # to the base card's back face so the composite actually re-renders.
    render_card, base_card, _face, _slug = _resolve_card_ref(card_name)
    comp_path = COMPOSITE_DIR / f"{slug}.png"
    if render_card:
        try:
            if base_card and is_rotated_split(base_card):
                # Either half reverting re-renders the COMBINED composite
                front_slug = name_to_slug(base_card['name'])
                front_raw = RAW_ART_DIR / f"{front_slug}.png"
                render_composite_for_card(
                    base_card, front_raw if front_raw.exists() else raw_path,
                    COMPOSITE_DIR / f"{front_slug}.png",
                    deck_fs=active_deck_meta.get('frame_settings'))
            else:
                render_composite(render_card, str(raw_path), None, str(comp_path),
                                     deck_frame_settings=active_deck_meta.get('frame_settings'))
        except Exception as e:
            return False, f"Reverted raw art but frame render failed: {e}"

    # Status is keyed by the BASE card name and reports FRONT-face file state
    # (the front drives the grid tile); back-face state rides via /api/status.
    status_key = base_card['name'] if base_card else card_name
    front_slug = name_to_slug(status_key)
    with generation_lock:
        generation_status[status_key] = {
            'status': 'complete',
            'message': f'Reverted to version {version_num}',
            'has_raw_art': (RAW_ART_DIR / f"{front_slug}.png").exists(),
            'has_composite': (COMPOSITE_DIR / f"{front_slug}.png").exists(),
        }

    return True, f"Reverted to version {version_num}"


def delete_version(card_name: str, version_num: int) -> tuple[bool, str]:
    """Delete a specific archived version's files and manifest entry."""
    slug = name_to_slug(card_name)
    vdir = _versions_dir_for(slug)
    prefix = f"v{version_num}"

    versioned_raw = vdir / f"{prefix}_raw.png"
    if not versioned_raw.exists():
        return False, f"Version {version_num} not found"

    # Delete files: raw, composite, meta
    freed = 0
    for suffix in ('_raw.png', '_composite.png', '_meta.json'):
        f = vdir / f"{prefix}{suffix}"
        if f.exists():
            freed += f.stat().st_size
            f.unlink()

    # Update manifest — remove this version's entry
    manifest_path = vdir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        manifest['versions'] = [
            v for v in manifest['versions'] if v['version'] != version_num
        ]
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

    freed_mb = freed / (1024 * 1024)
    print(f"[versions] Deleted v{version_num} for {card_name} ({freed_mb:.1f} MB freed)")
    return True, f"Deleted version {version_num} ({freed_mb:.1f} MB freed)"


# ---------------------------------------------------------------------------
# DALL-E Art Generation
# ---------------------------------------------------------------------------
def _generate_openai(card_name, model_cfg, full_prompt, status_dict=None, size_override=None):
    """Generate image using OpenAI API. Returns a PIL Image.

    Handles reference collage building, Scryfall art fetching,
    style instruction, and retry logic for rate limits.
    """
    _status = status_dict if status_dict is not None else generation_status
    model_name = model_cfg['model']
    quality = model_cfg['quality']
    size = size_override or model_cfg['size']
    supports_edit = model_cfg['supports_edit']

    # ── Build reference image (per-deck inspiration or default) ──
    # Multi-image: randomly select one inspiration image per card
    if active_inspiration_paths:
        import random
        valid_paths = [p for p in active_inspiration_paths if p.exists()]
        ref_image_path = random.choice(valid_paths) if valid_paths else active_inspiration_path
        if len(active_inspiration_paths) > 1:
            print(f"[openai_img] Using inspiration: {ref_image_path.name} (of {len(active_inspiration_paths)} images)")
    else:
        ref_image_path = active_inspiration_path
    used_scryfall_ref = False

    if supports_edit:  # cloud-only edit path (vestigial — no cloud model active)
        with generation_lock:
            _status[card_name]['message'] = 'Fetching Scryfall art for reference...'

        scryfall_path = fetch_card_art(card_name)
        if scryfall_path:
            collage_path = build_collage(card_name, scryfall_path, ref_image_path)
            if collage_path:
                ref_image_path = collage_path
                used_scryfall_ref = True

        with generation_lock:
            _status[card_name]['message'] = f'Calling {model_name} ({quality})...'

    # Build rich prompt for cloud models (up to 4000 chars)
    # Parse structured prompt: "{style_tag}.\n\n{subject}\n\n---\n\n{prose}"
    parts = full_prompt.split('\n\n---\n\n')
    style_and_subject = parts[0]
    prompt_prose = parts[1] if len(parts) > 1 else ''

    # Strip the "No text..." constraint from prose — build_collage_instruction
    # already adds its own, and duplicating it wastes cloud token budget.
    no_text_marker = 'No text, no words'
    if no_text_marker in prompt_prose:
        prompt_prose = prompt_prose[:prompt_prose.index(no_text_marker)].strip()

    # Separate style_tag from subject (split on first ".\n\n")
    ss_parts = style_and_subject.split('.\n\n', 1)
    if len(ss_parts) > 1:
        subject = ss_parts[1]
    else:
        subject = style_and_subject

    # Use full vision analysis as style description (richer than the style_tag)
    style_desc = active_deck_meta.get('inspiration_style_description', '')
    if not style_desc:
        style_desc = (
            "detailed fantasy art with rich colors and atmospheric depth, "
            "painterly rendering with strong composition and dramatic lighting"
        )

    from vision_analyzer import build_collage_instruction
    style_source = active_deck_meta.get('style_source', '')
    style_instruction = build_collage_instruction(
        style_desc, subject, has_scryfall_ref=used_scryfall_ref,
        art_direction=prompt_prose, source_override=style_source,
    )

    # Retry loop for rate limits (429 errors)
    max_retries = 5
    response = None
    for attempt in range(max_retries):
        try:
            if supports_edit and ref_image_path and ref_image_path.exists():
                with open(ref_image_path, 'rb') as img_file:
                    response = openai_client.images.edit(
                        model=model_name,
                        image=img_file,
                        prompt=style_instruction,
                        size=size,
                        quality=quality,
                        n=1,
                    )
            else:
                gen_kwargs = dict(
                    model=model_name,
                    prompt=style_instruction,
                    size=size,
                    n=1,
                )
                gen_kwargs['quality'] = quality
                response = openai_client.images.generate(**gen_kwargs)
            break
        except Exception as api_err:
            err_str = str(api_err)
            if '429' in err_str and attempt < max_retries - 1:
                import re as _re
                wait_match = _re.search(r'try again in (\d+\.?\d*)s', err_str)
                wait_time = float(wait_match.group(1)) + 1 if wait_match else 15
                with generation_lock:
                    _status[card_name]['message'] = (
                        f'Rate limited, retrying in {wait_time:.0f}s... '
                        f'(attempt {attempt+2}/{max_retries})'
                    )
                print(f"  [{card_name}] Rate limited, waiting {wait_time:.0f}s...")
                time.sleep(wait_time)
            else:
                raise

    if response is None:
        raise Exception("Failed after all retries")

    # Extract the generated image
    image_data = response.data[0]
    if hasattr(image_data, 'b64_json') and image_data.b64_json:
        image_bytes = base64.standard_b64decode(image_data.b64_json)
        return Image.open(io.BytesIO(image_bytes))
    elif hasattr(image_data, 'url') and image_data.url:
        import urllib.request as _urlreq
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp_path = Path(tmp.name)
        _urlreq.urlretrieve(image_data.url, str(tmp_path))
        img = Image.open(tmp_path)
        img.load()
        tmp_path.unlink(missing_ok=True)
        return img
    else:
        raise Exception("No image data in response")


def _get_inspiration_composite():
    """Return cached inspiration composite PIL image, rebuilding only if paths changed.

    Returns (PIL.Image, cache_key_str) or (None, None) if no inspiration available.
    """
    global _inspiration_composite_cache

    paths = active_inspiration_paths
    if not paths:
        if active_inspiration_path and active_inspiration_path.exists():
            paths = [active_inspiration_path]
        else:
            return None, None

    valid_paths = [p for p in paths if p.exists()]
    if not valid_paths:
        return None, None

    # Build cache key from sorted (path_str, mtime) pairs
    import hashlib
    key_data = tuple(sorted((str(p), p.stat().st_mtime) for p in valid_paths))
    cache_key = hashlib.md5(str(key_data).encode()).hexdigest()

    if _inspiration_composite_cache['key'] == cache_key:
        return _inspiration_composite_cache['image'], cache_key

    # Cache miss — rebuild composite
    from PIL import Image as PILImage
    try:
        pil_images = [PILImage.open(p).convert('RGB') for p in valid_paths]
        if len(pil_images) == 1:
            composite = pil_images[0]
        else:
            target_h = 512
            resized = []
            for img in pil_images:
                ratio = target_h / img.height
                resized.append(img.resize(
                    (int(img.width * ratio), target_h),
                    PILImage.LANCZOS))
            total_w = sum(im.width for im in resized)
            composite = PILImage.new('RGB', (total_w, target_h))
            x_offset = 0
            for im in resized:
                composite.paste(im, (x_offset, 0))
                x_offset += im.width

        _inspiration_composite_cache['key'] = cache_key
        _inspiration_composite_cache['image'] = composite
        print(f"[local_img] Inspiration composite cached ({len(valid_paths)} images, key={cache_key[:8]})")
        return composite, cache_key
    except Exception as e:
        print(f"[local_img] Failed to build inspiration composite: {e}")
        return None, None


def _build_clip_directives_fallback(style_tokens: dict, has_person: bool):
    """Build CLIP style anchor from style tokens using hardcoded rules.

    Fallback when LLM-generated clip_directives are not available.
    Returns (style_anchor_str, tradition_str).
    """
    anchor_parts = []
    if style_tokens:
        # Edges (was line_style) — check both new and legacy key names
        edges = (style_tokens.get('edges', '') or style_tokens.get('line_style', '')).lower()
        if any(w in edges for w in ('thick', 'bold', 'heavy')):
            anchor_parts.append('thick outlines')
        elif any(w in edges for w in ('thin', 'fine', 'delicate')):
            anchor_parts.append('fine lines')
        elif any(w in edges for w in ('none', 'no line', 'no outline', 'soft', 'lost', 'transition')):
            anchor_parts.append('no outlines, soft painted edges')
        coloring = style_tokens.get('coloring', '').lower()
        if 'flat' in coloring and 'gradient' not in coloring:
            anchor_parts.append('flat colors')
        elif any(w in coloring for w in ('layered', 'glaz', 'impasto', 'textured', 'chiaroscuro')):
            anchor_parts.append('rich layered colors')
        elif any(w in coloring for w in ('gradient', 'smooth', 'atmospheric')):
            anchor_parts.append('smooth shading')
        # Check tradition for media-specific CLIP anchors
        _tradition = style_tokens.get('tradition', '').lower()
        rendering_tok = style_tokens.get('rendering', '').lower()
        if any(w in _tradition for w in ('photograph', 'cinematograph', 'live-action', 'film')):
            anchor_parts.append('cinematic photography, film still, depth of field')
        elif any(w in _tradition for w in ('3d', 'cg ', 'cgi', 'game render', 'sculpt', 'figurine')):
            anchor_parts.append('3D render, CG animation, global illumination')
        elif any(w in _tradition for w in ('pixel art', 'retro game', '8-bit', '16-bit')):
            anchor_parts.append('pixel art, retro game art, limited palette')
        elif any(w in _tradition for w in ('woodblock', 'linocut', 'screen print', 'etching', 'engrav')):
            anchor_parts.append('printmaking, hand-printed, carved lines')
        elif any(w in _tradition for w in ('pencil', 'charcoal', 'ink wash', 'pen and ink')):
            anchor_parts.append('hand-drawn, traditional media, paper texture')
        elif any(w in _tradition for w in ('stained glass', 'mosaic')):
            anchor_parts.append('stained glass, lead lines, translucent color')
        elif any(w in _tradition for w in ('embroid', 'textile')):
            anchor_parts.append('embroidery, thread texture, fabric')
        elif 'cel' in rendering_tok:
            anchor_parts.append('cel-shaded')
        elif any(w in rendering_tok for w in ('oil', 'impasto')):
            anchor_parts.append('oil painting, visible brushstrokes')
        elif any(w in rendering_tok for w in ('realistic', 'photorealistic')):
            anchor_parts.append('highly detailed, realistic rendering')
        elif 'paint' in rendering_tok:
            anchor_parts.append('painterly, textured brushwork')
        elif any(w in rendering_tok for w in ('matte', 'concept')):
            anchor_parts.append('detailed digital painting')
        # Surface texture (was proportions)
        surface = (style_tokens.get('surface', '') or style_tokens.get('proportions', '')).lower()
        if any(w in surface for w in ('brushstroke', 'impasto', 'canvas', 'palette knife')):
            anchor_parts.append('visible brushstrokes')
        # Mood is critical for emotional tone — add ALL mood words, prioritized first
        mood = style_tokens.get('mood', '').strip()
        if mood:
            mood_words = [w.strip() for w in mood.split(',') if w.strip()]
            # Insert mood at the front so CLIP sees it early (77-token limit)
            anchor_parts = mood_words + anchor_parts
    style_anchor = ', '.join(anchor_parts) if anchor_parts else ''
    _tradition = style_tokens.get('tradition', '').strip() if style_tokens else ''
    return style_anchor, _tradition


def _build_negative_fallback(style_tokens: dict, deck_meta: dict) -> str:
    """Build negative prompt from style tokens using hardcoded rules.

    Fallback when LLM-generated clip_directives are not available.
    """
    _rendering = (style_tokens.get('rendering', '') + ' ' +
                  style_tokens.get('tradition', '')).lower() if style_tokens else ''
    _tradition = style_tokens.get('tradition', '').lower() if style_tokens else ''
    is_anime_style = any(w in _rendering for w in ('anime', 'manga'))
    is_painterly = any(w in _rendering for w in ('oil', 'paint', 'impasto',
                                                  'atmospheric', 'fantasy illustration'))
    is_photographic = any(w in _tradition for w in ('photograph', 'cinematograph',
                                                     'live-action', 'film'))
    is_3d = any(w in _tradition for w in ('3d', 'cg ', 'cgi', 'game render',
                                           'sculpt', 'figurine'))
    is_pixel = any(w in _tradition for w in ('pixel art', 'retro game', '8-bit', '16-bit'))
    is_drawn = any(w in _tradition for w in ('pencil', 'charcoal', 'ink wash',
                                              'pen and ink', 'crosshatch'))
    is_printmaking = any(w in _tradition for w in ('woodblock', 'linocut', 'screen print',
                                                    'etching', 'engrav'))
    is_craft = any(w in _tradition for w in ('stained glass', 'mosaic', 'embroid',
                                              'textile'))
    if is_anime_style:
        _source = deck_meta.get('style_source', '').lower()
        if _source and not any(w in _source for w in ('anime', 'manga')):
            is_anime_style = False
    # Build negative parts based on detected media type
    if is_photographic:
        neg_parts = ['oil painting, brushstrokes, cartoon, anime, flat colors, illustration']
    elif is_3d:
        neg_parts = ['oil painting, brushstrokes, flat colors, film grain']
    elif is_pixel:
        neg_parts = ['smooth shading, photograph, oil painting, realistic, high resolution']
    elif is_drawn:
        neg_parts = ['photograph, 3D render, smooth digital, oil painting']
    elif is_printmaking:
        neg_parts = ['photograph, 3D render, smooth digital, oil painting']
    elif is_craft:
        neg_parts = ['photograph, 3D render, oil painting, smooth digital']
    elif is_painterly:
        neg_parts = ['photograph, raw photo, camera']
    else:
        neg_parts = ['photorealistic, realistic, photograph']
    if not is_anime_style:
        neg_parts.append('anime, manga')
    _coloring = style_tokens.get('coloring', '').lower() if style_tokens else ''
    if 'flat' in _coloring and 'gradient' not in _coloring:
        neg_parts.append('gradient shading, soft shadows')
    # Check both new (edges) and legacy (line_style) key names
    _edges = (style_tokens.get('edges', '') or style_tokens.get('line_style', '')).lower() if style_tokens else ''
    if any(w in _edges for w in ('thick', 'bold', 'heavy')):
        neg_parts.append('no outlines, soft edges, airbrushed')
    if any(w in _tradition for w in ('cartoon', '2d')) or 'cel' in _rendering:
        neg_parts.append('photographic detail, smooth skin texture')
    if is_painterly:
        neg_parts.append('flat colors, thick outlines, cel-shaded, cartoon, simple shading')
    # Only negate "3D render" when tradition is NOT 3D
    if not is_3d:
        neg_parts.append('3D render')
    neg_parts.append('blurry, muddy, low quality')
    # Only negate "photograph" when tradition IS photographic (already handled above)
    # Mood-aware negatives: negate the opposite of the desired mood
    mood = style_tokens.get('mood', '').lower() if style_tokens else ''
    _dark_moods = ('dark', 'horror', 'ominous', 'sinister', 'macabre', 'eerie',
                   'foreboding', 'haunting', 'grim', 'dread', 'gothic', 'oppressive')
    if any(w in mood for w in _dark_moods):
        neg_parts.append('cute, pretty, cheerful, bright colors, whimsical, friendly')
    return ', '.join(neg_parts)


def _generate_local(card_name, model_cfg, full_prompt, status_dict=None, size_override=None):
    """Generate an image with the local FLUX model (mflux). Returns a PIL Image.

    Style always rides in the text prompt (the style source name + distilled
    clip_directives.style_tags + the distilled card subject) — FLUX's strong T5
    prompt adherence carries the aesthetic. Always txt2img: FLUX composes the
    scene from the prompt.
    """
    _status = status_dict if status_dict is not None else generation_status
    from local_image_generator import get_generator

    gen = get_generator()
    size_str = size_override or model_cfg['size']
    w, h = [int(x) for x in size_str.split('x')]

    clip_directives = active_deck_meta.get('clip_directives', {})
    style_tokens = active_deck_meta.get('style_tokens', {})

    # --- Subject: the single per-card prompt (art_prompts.json), used directly. ---
    # FLUX's 256-token budget means no distillation/truncation is needed — the card
    # prompt IS the scene. We only strip legacy bundling: the cloud-era "{style_tag}.
    # \n\n{subject}\n\n---\n\n{prose}" format and any "Additional direction:" feedback.
    body = full_prompt.split('\n\n---\n\n', 1)[0]
    feedback_text = ''
    fb_marker = 'Additional direction:'
    fb_idx = body.find(fb_marker)
    if fb_idx >= 0:
        feedback_text = body[fb_idx + len(fb_marker):].strip()
        body = body[:fb_idx].strip()
    # Drop a leading "{style_tag}.\n\n" if this is a legacy bundled prompt.
    secs = body.split('.\n\n', 1)
    subject = (secs[1] if len(secs) == 2 else body).strip()

    # --- Card Back override (avoid FLUX rendering a literal physical card back) ---
    if card_name.lower().startswith('card back'):
        subject = ('ornate symmetrical decorative pattern, central medallion, '
                   'intricate border filigree, repeating geometric motifs')

    # --- Rendering style for FLUX (rich, uncapped) ---
    # FLUX's T5 encoder accepts 256 tokens (~190 words) — far more than SDXL's
    # 77-token CLIP — so we feed it the FULL distilled style description, not the
    # 25-word `style_tags` subset that was capped for SDXL.
    #
    # FLUX also applies a NAMED style strongly and stays controllable (SDXL would
    # clone the source's characters), so we LEAD with the style-source name — the
    # single strongest signal. When a named source is present we deliberately DROP
    # the distilled `rendering`/`tradition`/`surface`/`edges` fields: the vision
    # model often mislabels the medium (e.g. tagging live-action film as "digital
    # painting"), which fights the named style. We keep the accurate descriptive
    # fields — palette, lighting/coloring, mood — for concrete detail.
    style_source = (active_deck_meta.get('style_source') or '').strip()
    flux_style_prompt = (active_deck_meta.get('flux_style_prompt') or '').strip()
    st = style_tokens or {}

    style_bits = []
    if style_source:
        style_bits.append(f"in the style of {style_source}")
    if flux_style_prompt:
        # Image-first descriptors (the vision model read the actual inspiration,
        # reconciled with the named style if one was given). Works for ANY style,
        # named or not. We use ONLY these — NOT the SDXL-era vision tokens, whose
        # mislabeled medium and warm palette pull back toward generic fantasy.
        style_bits.append(flux_style_prompt)
    else:
        # No recognized source (or no canonical descriptors yet) — use the full
        # vision-distilled tokens so the look isn't left undefined.
        for key, prefix in (('coloring', 'lighting and color: '), ('palette', 'color palette of '),
                            ('mood', 'mood: '), ('tradition', ''), ('rendering', ''),
                            ('edges', ''), ('surface', '')):
            v = (st.get(key) or '').strip()
            if v:
                style_bits.append(f"{prefix}{v}")
        if not st:
            ct = (clip_directives.get('style_tags') or '').strip()
            if ct:
                style_bits.append(ct)

    # --- Assemble the FLUX prompt: STYLE FIRST, then the scene. ---
    # FLUX weights early tokens most, so leading with the style (rather than
    # burying it after a long scene) stops a rich scene from drowning it. Validated
    # empirically — the same style words buried at the tail gave ZERO style transfer;
    # front-loaded they come through. (Kept well under the 256-token T5 budget.)
    pieces = []
    if style_bits:
        pieces.append(", ".join(style_bits))
    pieces.append(subject.rstrip(' .'))
    if feedback_text:
        pieces.append(feedback_text.rstrip(' .'))
    flux_prompt = '. '.join(p for p in pieces if p) + '.'
    flux_prompt += ' No text, no words, no watermark, no card frame, no borders.'

    # --- Progress callback — updates status per inference step ---
    def on_step(step, total):
        with generation_lock:
            s = _status.get(card_name)
            if s:
                s['step'] = step
                s['total_steps'] = total
                s['message'] = f'Step {step}/{total}...'

    print(f"[local_img] FLUX prompt ({len(flux_prompt.split())} words): {flux_prompt}")
    with generation_lock:
        _status[card_name]['message'] = 'Generating from text prompt...'
    return gen.generate(
        prompt=flux_prompt,
        width=w, height=h,
        progress_callback=on_step,
    )


def _archive_art(card_name, raw_art_dir=None, composite_dir=None, versions_dir=None):
    """Archive current art using explicit paths (defaults to globals)."""
    _raw = raw_art_dir or RAW_ART_DIR
    _comp = composite_dir or COMPOSITE_DIR
    _vers = versions_dir or VERSIONS_DIR

    slug = name_to_slug(card_name)
    raw_path = _raw / f"{slug}.png"
    if not raw_path.exists():
        return None

    vdir = _vers / slug
    vdir.mkdir(parents=True, exist_ok=True)

    existing = sorted(vdir.glob("v*_raw.png"))
    if existing:
        last_num = max(int(p.stem.split('_')[0][1:]) for p in existing)
        version_num = last_num + 1
    else:
        version_num = 1

    prefix = f"v{version_num}"
    shutil.copy2(raw_path, vdir / f"{prefix}_raw.png")

    comp_path = _comp / f"{slug}.png"
    if comp_path.exists():
        shutil.copy2(comp_path, vdir / f"{prefix}_composite.png")

    meta_path = raw_path.with_suffix('.meta.json')
    meta = {}
    if meta_path.exists():
        shutil.copy2(meta_path, vdir / f"{prefix}_meta.json")
        with open(meta_path) as f:
            meta = json.load(f)

    version_info = {
        'version': version_num,
        'prefix': prefix,
        'timestamp': meta.get('timestamp', datetime.now().isoformat()),
        'model': meta.get('model', 'unknown'),
        'quality': meta.get('quality', 'unknown'),
        'cost_estimate': meta.get('cost_estimate', 0),
        'prompt_sent': meta.get('prompt_sent', ''),
        'feedback': meta.get('feedback'),
        'used_scryfall_ref': meta.get('used_scryfall_ref', False),
    }

    manifest_path = vdir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = {'card_name': card_name, 'slug': slug, 'versions': []}
    manifest['versions'].append(version_info)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    return version_info


def generate_card_all_faces(card_name, custom_prompt=None, feedback=None,
                            face='all', **ctx):
    """Generate art for a card, covering both faces of a double-faced card.

    face: 'front' | 'back' | 'all'. For single-faced cards only the front
    exists. custom_prompt applies to the face being generated; with face='all'
    it applies to the front only (it came from the front prompt editor).
    """
    _cards_db = ctx.get('cards_db_snapshot') if ctx.get('cards_db_snapshot') is not None else cards_db
    card = next((c for c in _cards_db if c['name'] == card_name), None)
    if not card:
        return False, f"Card '{card_name}' not found"

    if face == 'all':
        faces = ['front', 'back'] if has_second_art_face(card) else ['front']
    else:
        faces = [face]

    ok, msg = True, "Success"
    in_batch = ctx.get('status_dict') is not None
    for i, f in enumerate(faces):
        if card_name in _cancel_single or (in_batch and not is_generating):
            return True, "Cancelled"
        f_prompt = custom_prompt if (face != 'all' or f == 'front') else None
        ok, msg = generate_art_for_card(card_name, custom_prompt=f_prompt,
                                        feedback=feedback, face=f, **ctx)
        if not ok:
            return ok, msg
    return ok, msg


def generate_art_for_card(card_name, custom_prompt=None, feedback=None,
                          status_dict=None, raw_art_dir=None, composite_dir=None,
                          versions_dir=None, cards_db_snapshot=None, face='front'):
    """Generate art for ONE face of a card using the active model config.

    Optional params allow batch worker to pass captured deck context,
    so file I/O targets the correct deck even if the user switches decks.
    Single-card generation omits these — defaults to globals.
    """
    global openai_client

    # Default to globals when not provided (single-card generation)
    _status = status_dict if status_dict is not None else generation_status
    _raw_art_dir = Path(raw_art_dir) if raw_art_dir else RAW_ART_DIR
    _composite_dir = Path(composite_dir) if composite_dir else COMPOSITE_DIR
    _versions_dir = Path(versions_dir) if versions_dir else VERSIONS_DIR
    _cards_db = cards_db_snapshot if cards_db_snapshot is not None else cards_db

    # Get active model configuration
    model_cfg = MODEL_OPTIONS.get(active_model_key, MODEL_OPTIONS[DEFAULT_MODEL_KEY])
    model_name = model_cfg['model']
    quality = model_cfg['quality']
    backend = model_cfg.get('backend', 'openai')

    # Art orientation: portrait (default) or landscape
    art_orientation = active_deck_meta.get('art_orientation', 'portrait')
    if art_orientation == 'landscape':
        actual_size = model_cfg.get('landscape_size', model_cfg['size'])
    else:
        actual_size = model_cfg['size']

    # Validate prerequisites
    if backend == 'openai' and not openai_client:
        return False, "No API key configured"
    if backend == 'local':
        from local_image_generator import get_generator
        gen = get_generator()
        if not gen.is_loaded:
            # Auto-load on the user's behalf — generating should just work.
            with generation_lock:
                _status[card_name] = {
                    'status': 'generating',
                    'message': 'Loading image model (first run downloads weights)...',
                    'has_raw_art': _status.get(card_name, {}).get('has_raw_art', False),
                    'has_composite': _status.get(card_name, {}).get('has_composite', False),
                }
            ok, load_msg = gen.load_model(model_name)
            if not ok:
                return False, f"Image model failed to load: {load_msg}"

    card = next((c for c in _cards_db if c['name'] == card_name), None)
    if not card:
        return False, f"Card '{card_name}' not found"

    # Face-specific context: the back face of a DFC has its own prompt key,
    # file slug, and card data for frame rendering.
    fkey = face_key(card_name, face)
    face_label = ''
    composite_card_dict = card
    if face == 'back':
        composite_card_dict = (split_half_card(card, 1) if is_rotated_split(card)
                               else back_face_card(card))
        if not composite_card_dict:
            return False, f"Card '{card_name}' has no back face"
        face_label = f" (back: {composite_card_dict.get('name', '')})"
    elif has_second_art_face(card):
        face_label = ' (front)'

    # Battle fronts are sideways cards — generate landscape-aspect art
    if face != 'back' and 'battle' in (card.get('type_line') or '').lower():
        actual_size = model_cfg.get('landscape_size', actual_size)

    # Build prompt — back faces fall back to a scene built from the face name
    # and type line when no back prompt has been generated yet.
    default_prompt = card_name
    if face == 'back':
        default_prompt = (f"{composite_card_dict.get('name', '')}, "
                          f"{composite_card_dict.get('type_line', '')}")
    base_prompt = custom_prompt or prompts_map.get(fkey, default_prompt)

    if feedback:
        # Insert feedback BEFORE the --- delimiter so local models (CLIP)
        # see it too. Cloud models get it via the subject portion.
        if '\n\n---\n\n' in base_prompt:
            before_delim, after_delim = base_prompt.split('\n\n---\n\n', 1)
            full_prompt = (
                f"{before_delim}\n\nAdditional direction: {feedback}"
                f"\n\n---\n\n{after_delim}"
            )
        else:
            full_prompt = base_prompt + f"\n\nAdditional direction: {feedback}"
    else:
        full_prompt = base_prompt

    slug = name_to_slug(fkey)
    raw_path = _raw_art_dir / f"{slug}.png"
    comp_path = _composite_dir / f"{slug}.png"

    # Status entries always report FRONT-face file state — they drive the
    # front tile/hero; back-face flags ride separately via /api/status.
    _front_slug = name_to_slug(card_name)
    _front_raw = _raw_art_dir / f"{_front_slug}.png"
    _front_comp = _composite_dir / f"{_front_slug}.png"

    # Re-roll uses the existing local prompt as-is.
    # Users can click "Regenerate" on the local prompt field to get a fresh subject.
    # Re-distilling on every re-roll was overwriting user's manual edits.

    # Archive existing art before overwriting
    if raw_path.exists():
        archived = _archive_art(fkey, _raw_art_dir, _composite_dir, _versions_dir)
        if archived:
            print(f"  [{fkey}] Archived as v{archived['version']}")

    try:
        with generation_lock:
            _status[card_name] = {
                'status': 'generating',
                'message': f'Calling {model_name} ({quality}){face_label}...',
                'has_raw_art': _front_raw.exists(),
                'has_composite': _front_comp.exists(),
            }

        # ── Generate the image ──
        if backend == 'local':
            result_image = _generate_local(card_name, model_cfg, full_prompt,
                                           status_dict=_status,
                                           size_override=actual_size)
        else:
            result_image = _generate_openai(card_name, model_cfg, full_prompt,
                                            status_dict=_status,
                                            size_override=actual_size)

        # Save raw art
        _raw_art_dir.mkdir(parents=True, exist_ok=True)
        result_image.save(raw_path, 'PNG')

        # Save metadata
        meta_path = raw_path.with_suffix('.meta.json')
        with open(meta_path, 'w') as f:
            json.dump({
                'card': fkey,
                'face': face,
                'model': model_name,
                'quality': quality,
                'size': actual_size,
                'model_key': active_model_key,
                'backend': backend,
                'cost_estimate': model_cfg['cost_per_image'],
                'prompt_sent': full_prompt,
                'distilled_subject': active_deck_meta.get('card_subjects', {}).get(card_name, ''),
                'feedback': feedback,
                'timestamp': datetime.now().isoformat(),
            }, f, indent=2)

        # Composite art with card frame using SVG renderer
        with generation_lock:
            _status[card_name]['message'] = f'Compositing card frame{face_label}...'

        if is_rotated_split(card):
            # Either half regenerating re-renders the COMBINED composite,
            # stored at the front slug
            front_art = _front_raw if _front_raw.exists() else raw_path
            render_composite_for_card(card, front_art, _front_comp,
                                      deck_fs=active_deck_meta.get('frame_settings'),
                                      raw_art_dir=_raw_art_dir)
        else:
            render_composite(composite_card_dict, str(raw_path), None, str(comp_path),
                                     deck_frame_settings=active_deck_meta.get('frame_settings'))

        # Don't overwrite 'cancelled' status if user cancelled during generation
        if card_name in _cancel_single:
            return True, "Cancelled"

        with generation_lock:
            _status[card_name] = {
                'status': 'complete',
                'message': 'Generated successfully',
                'has_raw_art': _front_raw.exists(),
                'has_composite': _front_comp.exists(),
            }

        return True, "Success"

    except Exception as e:
        error_msg = str(e)
        with generation_lock:
            _status[card_name] = {
                'status': 'error',
                'message': error_msg[:200],
                'has_raw_art': _front_raw.exists(),
                'has_composite': _front_comp.exists(),
            }
        return False, error_msg


PARALLEL_WORKERS = 2  # concurrent image generation threads (conservative for rate limits)


def batch_generate_worker(card_names, feedback_map=None, face_map=None):
    """Background worker for batch generation — runs up to PARALLEL_WORKERS at once.

    Captures deck context (paths, cards_db) at spawn time so file I/O
    targets the correct deck even if the user switches decks mid-batch.
    face_map: per-card face selection ('front'|'back'|'all'), so skip_existing
    batches only generate the faces that are actually missing.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    global is_generating, batch_phase, batch_phase_detail
    global batch_deck_id, _batch_generation_status
    feedback_map = feedback_map or {}
    face_map = face_map or {}

    # ── Capture deck context at spawn time ──
    _deck_id = active_deck_id
    _raw_art_dir = Path(RAW_ART_DIR)
    _composite_dir = Path(COMPOSITE_DIR)
    _versions_dir = Path(VERSIONS_DIR)
    _cards_db = list(cards_db)  # snapshot — immune to switch_deck()

    batch_deck_id = _deck_id
    _batch_generation_status.clear()

    try:
        # Use single worker for local models (GPU handles one at a time)
        model_cfg = MODEL_OPTIONS.get(active_model_key, MODEL_OPTIONS[DEFAULT_MODEL_KEY])
        is_local = model_cfg.get('backend') == 'local'
        workers = 1 if is_local else PARALLEL_WORKERS

        # Gate: wait for any in-flight LLM/VLM work to finish and unload it, then
        # ensure the FLUX image model is loaded — auto-load on the user's behalf so
        # "Generate" just works. If the load fails (e.g. a gated model with no HF
        # login), mark the cards with a clear error instead of silently hanging.
        if is_local:
            batch_phase = 'waiting_ollama'
            batch_phase_detail = 'Waiting for style analysis to finish...'
            _wait_for_ollama_idle(timeout=900)

            from local_image_generator import get_generator
            gen = get_generator()
            if not gen.is_loaded:
                batch_phase = 'loading_model'
                batch_phase_detail = 'Loading image model (first run downloads weights)...'
                ok, load_msg = gen.load_model(model_cfg['model'])
                if not ok:
                    err = f'Image model failed to load: {load_msg}'
                    print(f"  [batch] Aborting — {err}")
                    with generation_lock:
                        for name in card_names:
                            prev = _batch_generation_status.get(name, {})
                            _batch_generation_status[name] = {
                                'status': 'error',
                                'message': err,
                                'has_raw_art': prev.get('has_raw_art', False),
                                'has_composite': prev.get('has_composite', False),
                            }
                    return  # finally block resets is_generating / batch_phase

        # Transition to generating phase
        batch_phase = 'generating'
        batch_phase_detail = ''

        # Mark all as queued in batch status dict
        for i, name in enumerate(card_names):
            if not is_generating:
                break
            with generation_lock:
                _batch_generation_status[name] = {
                    'status': 'queued',
                    'message': f'Queued ({i+1}/{len(card_names)})',
                    'has_raw_art': _batch_generation_status.get(name, {}).get('has_raw_art', False),
                    'has_composite': _batch_generation_status.get(name, {}).get('has_composite', False),
                }

        def gen_one(name):
            if not is_generating:
                return
            feedback = feedback_map.get(name)
            generate_card_all_faces(
                name, feedback=feedback,
                face=face_map.get(name, 'all'),
                status_dict=_batch_generation_status,
                raw_art_dir=_raw_art_dir,
                composite_dir=_composite_dir,
                versions_dir=_versions_dir,
                cards_db_snapshot=_cards_db,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(gen_one, name): name for name in card_names}
            for future in as_completed(futures):
                if not is_generating:
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    future.result()
                except Exception as e:
                    card = futures[future]
                    print(f"  [batch] Error generating {card}: {e}")

    finally:
        # Merge batch results into generation_status if still viewing this deck
        with generation_lock:
            if active_deck_id == _deck_id:
                generation_status.update(_batch_generation_status)
            _batch_generation_status.clear()
            batch_deck_id = None

        is_generating = False
        batch_phase = None
        batch_phase_detail = ''


# ===========================================================================
#  API Routes
# ===========================================================================

@app.route('/')
def index():
    return HTML_TEMPLATE


@app.route('/favicon.svg')
def favicon_svg():
    return send_file(SCRIPT_DIR / 'static' / 'favicon.svg', mimetype='image/svg+xml')


@app.route('/favicon.png')
def favicon_png():
    return send_file(SCRIPT_DIR / 'static' / 'favicon.png', mimetype='image/png')


# ---------------------------------------------------------------------------
# Deck Management API
# ---------------------------------------------------------------------------

@app.route('/api/decks')
def api_list_decks():
    """List all decks with summary info."""
    return jsonify({
        'decks': list_all_decks(),
        'active_deck_id': active_deck_id,
    })


@app.route('/api/decks', methods=['POST'])
def api_create_deck():
    """Create a new empty deck."""
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Deck name required'}), 400

    deck_id = create_deck(name)
    return jsonify({'success': True, 'deck_id': deck_id})


@app.before_request
def _validate_deck_id_in_url():
    """Reject requests with path-traversal deck IDs before any route handler runs."""
    from flask import request as req
    view_args = req.view_args
    if view_args and 'deck_id' in view_args:
        if not _is_safe_deck_id(view_args['deck_id']):
            return jsonify({'error': 'Invalid deck ID'}), 400


@app.route('/api/decks/<deck_id>/activate', methods=['POST'])
def api_activate_deck(deck_id):
    """Switch the active deck."""
    if switch_deck(deck_id):
        return jsonify({'success': True, 'active_deck_id': deck_id})
    return jsonify({'error': f'Deck not found: {deck_id}'}), 404


@app.route('/api/decks/<deck_id>', methods=['DELETE'])
def api_delete_deck(deck_id):
    """Delete a deck. If it's the active deck, auto-switch to another first."""
    global active_deck_id

    registry = _load_deck_registry()
    other_decks = [d for d in registry['decks'] if d['id'] != deck_id]

    # If deleting the active deck, switch to another one first
    if deck_id == active_deck_id:
        if other_decks:
            switch_deck(other_decks[0]['id'])
        else:
            active_deck_id = None

    if delete_deck(deck_id):
        return jsonify({'success': True, 'switched_to': active_deck_id})
    return jsonify({'error': 'Deck not found'}), 404


@app.route('/api/decks/<deck_id>/rename', methods=['POST'])
def api_rename_deck(deck_id):
    """Rename a deck."""
    data = request.json or {}
    new_name = data.get('name', '').strip()
    if not new_name:
        return jsonify({'error': 'Name is required'}), 400

    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    # Update name in registry
    registry = _load_deck_registry()
    for d in registry['decks']:
        if d['id'] == deck_id:
            d['name'] = new_name
            break
    _save_deck_registry(registry)

    # Update name in deck.json
    _save_deck_meta_field(deck_id, name=new_name)

    # Update in-memory metadata if this is the active deck
    if deck_id == active_deck_id:
        active_deck_meta['name'] = new_name

    return jsonify({'success': True, 'name': new_name})


@app.route('/api/decks/<deck_id>/export')
def api_export_deck(deck_id):
    """Export a deck as ZIP with all art."""
    import zipfile
    import io

    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    # Load card data
    db_path = deck_dir / "deck.json"
    if db_path.exists():
        with open(db_path) as f:
            data = json.load(f)
        deck_cards = data.get('cards', data) if isinstance(data, dict) else data
    else:
        deck_cards = []

    buf = io.BytesIO()
    comp_dir = deck_dir / "composites"
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for card in deck_cards:
            slugs = [name_to_slug(card['name'])]
            if is_dfc(card):
                slugs.append(name_to_slug(face_key(card['name'], 'back')))
            for slug in slugs:
                comp = comp_dir / f"{slug}.png"
                if comp.exists():
                    qty = card.get('quantity', 1)
                    if qty == 1:
                        zf.write(str(comp), f"{slug}.png")
                    else:
                        for i in range(qty):
                            zf.write(str(comp), f"{slug}_{i+1}.png")

    buf.seek(0)
    registry = _load_deck_registry()
    deck_info = next((d for d in registry['decks'] if d['id'] == deck_id), {})
    filename = f"{deck_info.get('name', deck_id).replace(' ', '_')}_deck.zip"
    return Response(
        buf.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@app.route('/api/decks/<deck_id>/export-text')
def api_export_deck_text(deck_id):
    """Export a deck as Archidekt-compatible text."""
    deck_dir = DECKS_DIR / deck_id
    db_path = deck_dir / "deck.json"
    if not db_path.exists():
        return jsonify({'error': 'Deck not found'}), 404

    with open(db_path) as f:
        data = json.load(f)
    deck_cards = data.get('cards', data) if isinstance(data, dict) else data

    lines = []
    for card in deck_cards:
        qty = card.get('quantity', 1)
        name = card['name']
        cat = ''
        if card.get('is_commander'):
            cat = ' [Commander]'
        elif card.get('card_type'):
            cat = f" [{card['card_type'].title()}]"
        lines.append(f"{qty}x {name}{cat}")

    text = '\n'.join(lines) + '\n'
    return Response(text, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename={deck_id}.txt'})


@app.route('/api/decks/<deck_id>/export-manifest')
def api_export_manifest(deck_id):
    """Export deck art as a JSON manifest for the edhplay browser extension.

    Each card's composite image is resized to 375x525 and base64-encoded as JPEG.
    """
    import base64

    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    db_path = deck_dir / "deck.json"
    if not db_path.exists():
        return jsonify({'error': 'Deck has no cards'}), 404

    with open(db_path) as f:
        data = json.load(f)
    deck_cards = data.get('cards', data) if isinstance(data, dict) else data

    # Backfill UUIDs if missing (deck may not have been activated yet)
    # Strategy: art_crop_url → scryfall cache file → live Scryfall API
    import re
    from scryfall_client import fetch_card_by_name
    _uuid_re = re.compile(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})')
    scryfall_cache_dir = SCRIPT_DIR / "shared" / "scryfall_cache"
    uuid_filled = 0
    for card in deck_cards:
        if card.get('scryfall_id'):
            continue
        # 1) Try extracting from art_crop_url
        m = _uuid_re.search(card.get('art_crop_url', ''))
        if m:
            card['scryfall_id'] = m.group(1)
            uuid_filled += 1
            continue
        # 2) Try scryfall cache file
        slug = name_to_slug(card['name'])
        cache_file = scryfall_cache_dir / f"{slug}.json"
        if cache_file.exists():
            try:
                sf_data = json.loads(cache_file.read_text())
                sid = sf_data.get('id', '')
                if sid:
                    card['scryfall_id'] = sid
                    uuid_filled += 1
                    continue
            except Exception:
                pass
        # 3) Live Scryfall lookup (also populates cache for next time)
        try:
            sf_data = fetch_card_by_name(card['name'])
            if sf_data and sf_data.get('id'):
                card['scryfall_id'] = sf_data['id']
                uuid_filled += 1
        except Exception:
            pass
    if uuid_filled:
        # Persist backfilled UUIDs
        if isinstance(data, dict) and 'cards' in data:
            data['cards'] = deck_cards
        else:
            data = deck_cards
        with open(db_path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  [export-manifest] Backfilled {uuid_filled} UUIDs for {deck_id}")

    # Get deck name from registry
    registry = _load_deck_registry()
    deck_info = next((d for d in registry['decks'] if d['id'] == deck_id), {})
    deck_name = deck_info.get('name', deck_id)

    comp_dir = deck_dir / "composites"
    cards_manifest = {}
    skipped = 0

    def _encode_composite(comp_path):
        img = Image.open(comp_path).convert('RGB')
        img = img.resize((375, 525), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{b64}'

    for card in deck_cards:
        scryfall_id = card.get('scryfall_id', '')
        if not scryfall_id:
            skipped += 1
            continue

        slug = name_to_slug(card['name'])
        comp_path = comp_dir / f"{slug}.png"
        if comp_path.exists():
            try:
                cards_manifest[scryfall_id] = {
                    'name': card['name'],
                    'image': _encode_composite(comp_path),
                }
            except Exception as e:
                print(f"  [export-manifest] Failed for {card['name']}: {e}")
                skipped += 1
        else:
            skipped += 1

        # Double-faced cards: the back face rides under "<uuid>:back", which
        # the extension looks up for /back/ Scryfall image URLs. Exported
        # independently of the front so a back-only card still ships its art.
        if is_dfc(card):
            bslug = name_to_slug(face_key(card['name'], 'back'))
            bcomp = comp_dir / f"{bslug}.png"
            if bcomp.exists():
                try:
                    cards_manifest[f'{scryfall_id}:back'] = {
                        'name': card['name'],
                        'image': _encode_composite(bcomp),
                    }
                except Exception as e:
                    print(f"  [export-manifest] Back face failed for {card['name']}: {e}")

    # Include custom card back if it exists
    card_back_path = comp_dir / "card_back.png"
    if card_back_path.exists():
        try:
            img = Image.open(card_back_path).convert('RGB')
            img = img.resize((375, 525), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            cards_manifest['card-back'] = {
                'name': 'Card Back',
                'image': f'data:image/jpeg;base64,{b64}',
            }
        except Exception as e:
            print(f"  [export-manifest] Failed for Card Back: {e}")

    manifest = {
        'version': 1,
        'deck': deck_name,
        'cards': cards_manifest,
    }

    return jsonify(manifest)


# ---------------------------------------------------------------------------
# Import API
# ---------------------------------------------------------------------------

@app.route('/api/import/parse', methods=['POST'])
def api_import_parse():
    """Parse a decklist text and return the parsed entries for confirmation."""
    from scryfall_client import parse_decklist
    data = request.json
    text = data.get('text', '')
    if not text.strip():
        return jsonify({'error': 'No decklist text provided'}), 400

    entries = parse_decklist(text)
    if not entries:
        return jsonify({'error': 'Could not parse any cards from input'}), 400

    return jsonify({
        'success': True,
        'entries': entries,
        'total': len(entries),
        'commanders': [e for e in entries if e.get('is_commander')],
    })


# ---------------------------------------------------------------------------
# Import progress tracking
# ---------------------------------------------------------------------------
import_progress = {}  # job_id -> {phase, step, total, message, done, error, deck_id}


@app.route('/api/import/create', methods=['POST'])
def api_import_create():
    """Kick off a deck import as a background job. Returns a job_id for polling."""
    data = request.json
    deck_name = data.get('name', '').strip()
    text = data.get('text', '')
    use_ai_prompts = data.get('use_ai_prompts', False)
    style_preamble = data.get('style_preamble', None)

    if not deck_name:
        return jsonify({'error': 'Deck name required'}), 400
    if not text.strip():
        return jsonify({'error': 'No decklist text'}), 400

    job_id = f"import_{int(time.time() * 1000)}"
    import_progress[job_id] = {
        'phase': 'parsing',
        'step': 0,
        'total': 0,
        'message': 'Parsing decklist...',
        'done': False,
        'error': None,
        'deck_id': None,
        'cards_imported': 0,
        'prompts_generated': 0,
        'errors': [],
    }

    def import_worker():
        from scryfall_client import parse_decklist, fetch_card, scryfall_to_card_entry
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import urllib.request as _urlreq

        prog = import_progress[job_id]
        try:
            # ── Phase 1: Parse ──
            entries = parse_decklist(text)
            if not entries:
                prog['error'] = 'Could not parse any cards'
                prog['done'] = True
                return

            total = len(entries)
            prog['total'] = total
            prog['phase'] = 'scryfall'
            prog['message'] = f'Fetching {total} cards from Scryfall...'

            # ── Phase 2: Fetch from Scryfall (parallel, respecting rate limit) ──
            cards = []
            errors = []
            completed = [0]  # mutable counter for threads
            sf_lock = threading.Lock()

            def fetch_one(entry):
                name = entry['name']
                sf = fetch_card(
                    name,
                    set_code=entry.get('set_code'),
                    collector_number=entry.get('collector_number'),
                )
                if sf:
                    card = scryfall_to_card_entry(
                        sf,
                        quantity=entry.get('quantity', 1),
                        is_commander=entry.get('is_commander', False),
                    )
                    with sf_lock:
                        cards.append(card)
                else:
                    with sf_lock:
                        errors.append(f"Card not found: {name}")
                with sf_lock:
                    completed[0] += 1
                    prog['step'] = completed[0]
                    prog['message'] = f'Scryfall: {completed[0]}/{total} cards fetched'
                time.sleep(0.12)  # respect Scryfall rate limit

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(fetch_one, entries))

            if not cards:
                prog['error'] = 'No cards found on Scryfall'
                prog['errors'] = errors
                prog['done'] = True
                return

            # Add Card Back entry (prompts are generated later by the user)
            card_back_entry = {
                'name': 'Card Back', 'mana_cost': '', 'type_line': 'Card Back',
                'oracle_text': '', 'power': None, 'toughness': None,
                'loyalty': None, 'colors': [], 'color_identity': [],
                'quantity': 1, 'is_commander': False, 'card_type': 'other',
                'flavor_text': '', 'art_crop_url': '',
            }
            cards.append(card_back_entry)
            prompts = []

            # ── Phase 4: Create deck ──
            prog['phase'] = 'saving'
            prog['message'] = 'Creating deck...'

            deck_id = create_deck(deck_name, cards=cards, prompts=prompts,
                                  style_preamble=style_preamble)
            switch_deck(deck_id)

            # ── Phase 5: Fetch Scryfall art in background ──
            prog['phase'] = 'art'
            prog['step'] = 0
            prog['total'] = len(cards)
            prog['message'] = 'Downloading card art from Scryfall...'

            scryfall_dir = DECKS_DIR / deck_id / "scryfall_art"
            scryfall_dir.mkdir(exist_ok=True)
            art_done = [0]

            def _download_art(url, dest):
                if dest.exists() or not url:
                    return
                try:
                    req = _urlreq.Request(url, headers={
                        "User-Agent": "MTGProxyDeckGen/1.0"
                    })
                    with _urlreq.urlopen(req) as resp:
                        with open(dest, 'wb') as f:
                            f.write(resp.read())
                except Exception:
                    pass

            def fetch_art(card):
                slug = name_to_slug(card['name'])
                _download_art(card.get('art_crop_url', ''),
                              scryfall_dir / f"{slug}.jpg")
                # Double-faced cards: also fetch the back face's art
                if is_dfc(card):
                    bslug = name_to_slug(face_key(card['name'], 'back'))
                    _download_art(card['card_faces'][1].get('art_crop_url', ''),
                                  scryfall_dir / f"{bslug}.jpg")
                with sf_lock:
                    art_done[0] += 1
                    prog['step'] = art_done[0]
                    prog['message'] = f'Art: {art_done[0]}/{len(cards)} downloaded'
                time.sleep(0.12)

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(fetch_art, cards))

            # ── Phase 6: Composite Scryfall art with card frames ──
            prog['phase'] = 'compositing'
            prog['step'] = 0
            prog['total'] = len(cards)
            prog['message'] = 'Rendering card frames over Scryfall art...'

            deck_raw_dir = DECKS_DIR / deck_id / "raw_art"
            deck_comp_dir = DECKS_DIR / deck_id / "composites"
            deck_raw_dir.mkdir(parents=True, exist_ok=True)
            deck_comp_dir.mkdir(parents=True, exist_ok=True)

            composited = 0
            for i, card in enumerate(cards):
                # Front face (or the whole card for single-faced cards),
                # plus the back face for double-faced cards.
                faces = [(name_to_slug(card['name']), card)]
                if is_dfc(card):
                    faces.append((name_to_slug(face_key(card['name'], 'back')),
                                  back_face_card(card)))
                for slug, face_card in faces:
                    sf_path = scryfall_dir / f"{slug}.jpg"
                    raw_path = deck_raw_dir / f"{slug}.png"
                    comp_path = deck_comp_dir / f"{slug}.png"

                    if sf_path.exists() and not raw_path.exists():
                        try:
                            img = Image.open(sf_path).convert('RGB')
                            img.save(raw_path, 'PNG')
                            if face_card is card:
                                # front entry — rotated splits combine here
                                render_composite_for_card(
                                    card, raw_path, comp_path,
                                    deck_fs=active_deck_meta.get('frame_settings'),
                                    raw_art_dir=deck_raw_dir)
                            else:
                                render_composite(face_card, str(raw_path), None, str(comp_path),
                                         deck_frame_settings=active_deck_meta.get('frame_settings'))
                            composited += 1
                        except Exception as e:
                            print(f"  [import] Composite failed for {card['name']}: {e}")

                prog['step'] = i + 1
                prog['message'] = f'Compositing: {i+1}/{len(cards)}'

            print(f"  [import] Composited {composited}/{len(cards)} cards with Scryfall art")

            # Reload data so generation_status reflects the new composites
            load_data()

            # ── Done ──
            prog['phase'] = 'done'
            prog['message'] = f'Imported {len(cards)} cards with {len(prompts)} prompts!'
            prog['deck_id'] = deck_id
            prog['cards_imported'] = len(cards)
            prog['prompts_generated'] = len(prompts)
            prog['errors'] = errors
            prog['done'] = True
            print(f"  [import] Complete: {deck_name} ({deck_id}) — {len(cards)} cards")

        except Exception as e:
            prog['error'] = str(e)[:300]
            prog['done'] = True
            print(f"  [import] Failed: {e}")

    threading.Thread(target=import_worker, daemon=True).start()

    return jsonify({
        'success': True,
        'job_id': job_id,
    })


@app.route('/api/import/progress/<job_id>')
def api_import_progress(job_id):
    """Poll import progress."""
    prog = import_progress.get(job_id)
    if not prog:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(prog)


# ---------------------------------------------------------------------------
# Prompt Generation API
# ---------------------------------------------------------------------------

@app.route('/api/generate-prompts', methods=['POST'])
def api_generate_prompts():
    """Generate art prompts for cards in the active deck that are missing them."""
    from prompt_generator import generate_prompts_for_deck, generate_prompts_with_ai

    data = request.json or {}
    use_ai = data.get('use_ai', False)
    style_preamble = data.get('style_preamble', active_deck_meta.get('style_preamble'))

    # Find cards without prompts
    missing = [c for c in cards_db if c['name'] not in prompts_map]
    if not missing:
        return jsonify({'success': True, 'message': 'All cards already have prompts', 'count': 0})

    bcfg = backend_config.load_config()
    can_do_ai = (bcfg['llm_backend'] == 'local') or openai_client
    if use_ai and can_do_ai:
        new_prompts = generate_prompts_with_ai(
            missing, openai_client, style_preamble,
            backend=bcfg['llm_backend'],
            local_model=bcfg['ollama_model'],
        )
    else:
        new_prompts = generate_prompts_for_deck(missing, style_preamble)

    # Merge into prompts_map and save
    for p in new_prompts:
        prompts_map[p['name']] = p['prompt']

    # Load existing prompts file and merge
    if ART_PROMPTS_PATH.exists():
        with open(ART_PROMPTS_PATH) as f:
            all_prompts = json.load(f)
    else:
        all_prompts = []

    existing_names = {p['name'] for p in all_prompts}
    for p in new_prompts:
        if p['name'] not in existing_names:
            all_prompts.append(p)

    with open(ART_PROMPTS_PATH, 'w') as f:
        json.dump(all_prompts, f, indent=2)

    return jsonify({
        'success': True,
        'message': f'Generated {len(new_prompts)} prompts',
        'count': len(new_prompts),
    })


# ---------------------------------------------------------------------------
# Inspiration Image & Card Back Management
# ---------------------------------------------------------------------------

def _migrate_single_inspiration(deck_id: str, insp_filename: str):
    """Auto-migrate a legacy single inspiration_image to the inspiration_images array."""
    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    if not deck_json_path.exists():
        return

    with open(deck_json_path) as f:
        data = json.load(f)

    if data.get('inspiration_images'):
        return  # Already migrated

    import time as _time
    data['inspiration_images'] = [{
        'filename': insp_filename,
        'style_description': data.get('inspiration_style_description', ''),
        'uploaded_at': data.get('created', _time.strftime('%Y-%m-%dT%H:%M:%S')),
    }]

    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)

    # Sync in-memory meta
    global active_deck_meta
    if deck_id == active_deck_id:
        active_deck_meta['inspiration_images'] = data['inspiration_images']
    print(f"  [migration] Migrated single inspiration to inspiration_images array")


def _rebuild_merged_description(deck_id: str):
    """Rebuild the merged inspiration_style_description from all per-image descriptions."""
    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    if not deck_json_path.exists():
        return

    with open(deck_json_path) as f:
        data = json.load(f)

    images = data.get('inspiration_images', [])
    descriptions = [img.get('style_description', '') for img in images if img.get('style_description')]

    if not descriptions:
        merged = ''
    elif len(descriptions) == 1:
        merged = descriptions[0]
    else:
        from vision_analyzer import merge_style_descriptions
        merged = merge_style_descriptions(descriptions)

    # Apply user's style_source override to the Source: line in the merged description
    style_source = data.get('style_source', '').strip()
    if style_source and merged:
        import re as _re
        merged = _re.sub(
            r'^(- )?Source:.*$',
            f'Source: {style_source}',
            merged,
            count=1,
            flags=_re.MULTILINE,
        )

    data['inspiration_style_description'] = merged
    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)

    # Sync in-memory meta
    global active_deck_meta
    if deck_id == active_deck_id:
        active_deck_meta['inspiration_style_description'] = merged
        active_deck_meta['inspiration_images'] = images

    print(f"  [inspiration] Rebuilt merged description from {len(descriptions)} image(s)")
    return merged


def _run_style_distillation(deck_id: str, progress_callback=None, subject_progress_callback=None):
    """Distill per-image style descriptions into SDXL-optimized style tokens.

    Reads all inspiration_images[].style_description values, sends them
    through the LLM to extract structured rendering tokens, and caches
    the result in deck.json['style_tokens'].

    progress_callback(message): optional callable for UI progress updates.
    """
    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    if not deck_json_path.exists():
        return

    with open(deck_json_path) as f:
        data = json.load(f)

    images = data.get('inspiration_images', [])
    descriptions = [img.get('style_description', '') for img in images
                    if img.get('style_description')]

    if not descriptions:
        # Clear stale tokens, directives, and subjects
        data['style_tokens'] = {}
        data['clip_directives'] = {}
        data['card_subjects'] = {}
        with open(deck_json_path, 'w') as f:
            json.dump(data, f, indent=2)
        global active_deck_meta
        if deck_id == active_deck_id:
            active_deck_meta['style_tokens'] = {}
            active_deck_meta['clip_directives'] = {}
            active_deck_meta['card_subjects'] = {}
        return

    style_source = data.get('style_source', '')

    bcfg = backend_config.load_config()
    llm_backend = 'local'  # MLX-native pipeline is always local

    if progress_callback:
        progress_callback('Distilling style tokens...')
    from vision_analyzer import distill_style_tokens
    tokens = distill_style_tokens(
        descriptions,
        style_source=style_source,
        openai_client=openai_client,
        backend=llm_backend,
        local_model=bcfg.get('ollama_model', 'llama3.2:3b'),
    )

    data['style_tokens'] = tokens

    # Build CLIP directives (style_tags + negative prompt) from tokens
    clip_dirs = {}
    if tokens:
        if progress_callback:
            progress_callback('Building CLIP directives...')
        from vision_analyzer import build_clip_directives
        clip_dirs = build_clip_directives(
            tokens, descriptions, style_source=style_source,
            openai_client=openai_client, backend=llm_backend,
            local_model=bcfg.get('ollama_model', 'llama3.2:3b'),
        )
    data['clip_directives'] = clip_dirs

    # Image-first FLUX style descriptors: the vision model reads the actual
    # inspiration image (works for ANY style, named or not); if a source name is
    # set, an LLM pass reconciles it with knowledge of that named style (fixing
    # vision medium-mislabels). Cached and used by _generate_local for both modes.
    flux_style_prompt = ''
    insp_imgs = data.get('inspiration_images', [])
    first_img = None
    for img in insp_imgs:
        cand = deck_dir / img.get('filename', '')
        if img.get('filename') and cand.exists():
            first_img = cand
            break
    if first_img is not None:
        if progress_callback:
            progress_callback('Building style descriptors from inspiration...')
        from vision_analyzer import build_flux_style_descriptors
        flux_style_prompt = build_flux_style_descriptors(
            first_img, style_source=style_source, backend=llm_backend,
            vision_model=bcfg.get('ollama_vision_model', 'llava:7b'),
            text_model=bcfg.get('ollama_model', 'llama3.2:3b'))
        if flux_style_prompt:
            print(f"  [distill] FLUX style descriptors ({'named: '+style_source if style_source else 'image-only'}): {flux_style_prompt}")
    data['flux_style_prompt'] = flux_style_prompt

    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)

    if deck_id == active_deck_id:
        active_deck_meta['style_tokens'] = tokens
        active_deck_meta['clip_directives'] = clip_dirs
        active_deck_meta['flux_style_prompt'] = flux_style_prompt

    if tokens:
        print(f"  [distill] Style tokens saved for {deck_id}: {list(tokens.keys())}")
    else:
        print(f"  [distill] Style token distillation returned empty for {deck_id}")
    # NB: no card-subject distillation chain — each card's single rich prompt
    # (art_prompts.json) drives generation directly; style is applied separately.


def _run_subject_distillation(deck_id: str, progress_callback=None, card_names=None):
    """Distill art prompts into short CLIP-optimized card subjects.

    Reads cards + art_prompts.json + style_tokens + style_source, sends
    through LLM to extract 5-10 word subjects per card, and caches the
    result in deck.json['card_subjects'].

    If card_names is provided, only distills those cards (merges with existing).
    """
    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    prompts_path = deck_dir / "art_prompts.json"

    if not deck_json_path.exists():
        return
    if not prompts_path.exists():
        return

    with open(deck_json_path) as f:
        data = json.load(f)

    style_tokens = data.get('style_tokens', {})

    with open(prompts_path) as f:
        prompts_list = json.load(f)

    art_prompts = {p['name']: p['prompt'] for p in prompts_list if p.get('name') and p.get('prompt')}
    if not art_prompts:
        return

    cards = data.get('cards', [])
    if not cards:
        return

    # Filter to specific cards if requested (partial distillation)
    if card_names:
        card_name_set = set(card_names)
        cards = [c for c in cards if c.get('name') in card_name_set]
        art_prompts = {k: v for k, v in art_prompts.items() if k in card_name_set}

    style_source = data.get('style_source', '')

    bcfg = backend_config.load_config()
    llm_backend = 'local'  # MLX-native pipeline is always local

    # Prefer the 8B model for subject distillation (better quality than 3B).
    # mlx_llm downloads it lazily from the HuggingFace hub on first use; no
    # separate model-pull step is needed.
    distill_model = 'llama3.1:8b'

    from vision_analyzer import distill_card_subjects
    print(f"  [distill] Distilling subjects for {len(art_prompts)} cards using {llm_backend}/{distill_model}")
    subjects = distill_card_subjects(
        cards,
        art_prompts,
        style_tokens=style_tokens,
        style_source=style_source,
        openai_client=openai_client,
        backend=llm_backend,
        local_model=distill_model,
        progress_callback=progress_callback,
    )

    # Extract stats before saving (the key is removed from saved data)
    distill_stats = subjects.pop('_distill_stats', {})

    # Merge with existing subjects (partial distillation preserves other cards)
    existing = data.get('card_subjects', {})
    existing.update(subjects)
    data['card_subjects'] = existing
    subjects = existing  # for in-memory update below
    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)

    if deck_id == active_deck_id:
        active_deck_meta['card_subjects'] = subjects

    if subjects:
        print(f"  [distill] Card subjects saved for {deck_id}: {len(subjects)} cards")
    else:
        print(f"  [distill] Card subject distillation returned empty for {deck_id}")

    return distill_stats


def _redistill_single_card_subject(card_name: str) -> str:
    """Re-distill a single card's subject via LLM for re-roll variety.

    Thin wrapper around vision_analyzer.distill_one_card_subject — the same
    function used by batch distillation. Saves to deck metadata on success.
    """
    from vision_analyzer import distill_one_card_subject

    card = next((c for c in cards_db if c['name'] == card_name), None)
    if not card:
        return ''
    prompt_text = prompts_map.get(card_name, '')
    if not prompt_text:
        print(f"  [re-distill] {card_name}: no art prompt, skipping")
        return ''

    bcfg = backend_config.load_config()
    llm_backend = 'local'  # MLX-native pipeline is always local
    model = bcfg.get('ollama_model', 'llama3.2:3b')

    style_source = active_deck_meta.get('style_source', '') if active_deck_meta else ''

    result = distill_one_card_subject(
        card=card,
        art_prompt=prompt_text,
        backend=llm_backend,
        local_model=model,
        openai_client=openai_client,
        style_source=style_source,
    )
    if result:
        print(f"  [re-distill] {card_name}: {result}")
        subjects = active_deck_meta.get('card_subjects', {})
        subjects[card_name] = result
        active_deck_meta['card_subjects'] = subjects
        _save_deck_meta_field(active_deck_id, card_subjects=subjects)
        return result
    return ''


def _guarded_style_distillation(deck_id: str):
    """Wrapper for _run_style_distillation that manages Ollama GPU guard."""
    _ollama_work_start()
    try:
        _run_style_distillation(deck_id)
    finally:
        _ollama_work_done()


def _guarded_subject_distillation(deck_id: str):
    """Wrapper for _run_subject_distillation that manages Ollama GPU guard."""
    _ollama_work_start()
    try:
        _run_subject_distillation(deck_id)
    finally:
        _ollama_work_done()


def _save_deck_meta_field(deck_id: str, **fields):
    """Update specific fields in a deck's deck.json without touching cards."""
    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    if not deck_json_path.exists():
        return False
    with open(deck_json_path) as f:
        data = json.load(f)
    for k, v in fields.items():
        data[k] = v
    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)
    # Keep in-memory meta in sync
    global active_deck_meta
    if deck_id == active_deck_id:
        active_deck_meta.update(fields)
    return True


@app.route('/api/decks/<deck_id>/pinned-cards', methods=['POST'])
def update_pinned_cards(deck_id):
    """Pin or unpin cards for a deck. Persists to deck.json."""
    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    if not deck_json_path.exists():
        return jsonify({'error': 'Deck not found'}), 404

    data = request.json or {}
    card_names = data.get('card_names', [])
    pinned = data.get('pinned', True)

    with open(deck_json_path) as f:
        deck_data = json.load(f)

    current = set(deck_data.get('pinned_cards', []))
    if pinned:
        current.update(card_names)
    else:
        current -= set(card_names)

    deck_data['pinned_cards'] = sorted(current)
    with open(deck_json_path, 'w') as f:
        json.dump(deck_data, f, indent=2)

    if deck_id == active_deck_id:
        active_deck_meta['pinned_cards'] = deck_data['pinned_cards']

    return jsonify({'success': True, 'pinned_cards': deck_data['pinned_cards']})


@app.route('/api/decks/<deck_id>/upload-inspiration', methods=['POST'])
def upload_inspiration(deck_id):
    """Upload an inspiration/style reference image for this deck.

    Appends to the inspiration_images array (max 10). Triggers vision
    analysis for the new image, then rebuilds the merged description.
    """
    global active_inspiration_path, active_inspiration_paths

    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    # Check current count
    deck_json_path = deck_dir / "deck.json"
    if deck_json_path.exists():
        with open(deck_json_path) as f:
            deck_data = json.load(f)
    else:
        deck_data = {}
    current_images = deck_data.get('inspiration_images', [])
    if len(current_images) >= 10:
        return jsonify({'error': 'Maximum of 10 inspiration images reached. Remove one first.'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.webp'):
        return jsonify({'error': 'Unsupported format. Use PNG, JPG, or WebP.'}), 400

    # Save with unique filename to avoid collisions (including bulk uploads)
    import hashlib
    file_bytes = file.read()
    file_hash = hashlib.md5(file_bytes).hexdigest()[:8]
    file.seek(0)

    # Skip if this exact image is already uploaded
    for img_entry in current_images:
        existing_name = img_entry if isinstance(img_entry, str) else img_entry.get('filename', '')
        if file_hash in existing_name:
            return jsonify({'success': True, 'message': 'Duplicate image skipped', 'skipped': True})

    filename = f"inspiration_{file_hash}{ext}"
    dest = deck_dir / filename
    file.save(str(dest))

    # Resize if very large (keep under 4MB for GPT-4o)
    try:
        img = Image.open(dest)
        max_dim = 2048
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            img.save(str(dest))
    except Exception as e:
        print(f"[inspiration] Resize warning: {e}")

    # Append to inspiration_images array
    new_entry = {
        'filename': filename,
        'style_description': '',
        'uploaded_at': datetime.now().isoformat(),
    }
    current_images.append(new_entry)
    deck_data['inspiration_images'] = current_images
    deck_data['inspiration_image'] = current_images[0]['filename']  # backward compat
    with open(deck_json_path, 'w') as f:
        json.dump(deck_data, f, indent=2)

    # Update active paths if this is the active deck
    if deck_id == active_deck_id:
        active_inspiration_paths = [
            deck_dir / img['filename'] for img in current_images
            if (deck_dir / img['filename']).exists()
        ]
        active_inspiration_path = active_inspiration_paths[0] if active_inspiration_paths else DEFAULT_REF_IMAGE_PATH
        active_deck_meta['inspiration_images'] = current_images
        load_reference_image()  # reload b64 cache

    # Run vision analysis for the NEW image only, then rebuild merged description
    new_index = len(current_images) - 1

    # Pre-flight: check if an AI backend is available for vision analysis
    bcfg = backend_config.load_config()
    ai_available = (bcfg['llm_backend'] == 'local') or openai_client
    if not ai_available:
        print("[inspiration] No AI backend available — skipping vision analysis")
        return jsonify({
            'success': True,
            'filename': filename,
            'index': new_index,
            'count': len(current_images),
            'message': 'Image uploaded, but style analysis skipped — no AI backend configured.',
            'warning': 'No AI backend available. Configure an OpenAI API key or install Ollama for style analysis.',
        })

    def analyze_bg():
        _ollama_work_start()

        # Calculate card count for batch-level progress on card subjects
        import math
        with open(deck_json_path) as f:
            _d = json.load(f)
        card_count = len([c for c in _d.get('cards', []) if c.get('name') != 'Card Back'])
        subject_batches = max(1, math.ceil(card_count / 10))

        # 1 image + merge + tokens + CLIP directives + subject_batches + done
        total_steps = 4 + subject_batches + 1
        _style_progress_update('analyzing', 0, total_steps,
                               'Analyzing image style...', sub_phase='api_call')
        try:
            bcfg = backend_config.load_config()
            from vision_analyzer import analyze_inspiration_style
            desc = analyze_inspiration_style(
                dest, openai_client,
                backend='local',
                local_model=bcfg['ollama_vision_model'],
            )
            if desc:
                _style_progress_update('analyzing', 1, total_steps, 'Image analyzed')
                # Update the per-image description in the array
                with open(deck_json_path) as f:
                    d = json.load(f)
                imgs = d.get('inspiration_images', [])
                if new_index < len(imgs):
                    imgs[new_index]['style_description'] = desc
                    d['inspiration_images'] = imgs
                    with open(deck_json_path, 'w') as f:
                        json.dump(d, f, indent=2)
                # Rebuild merged description from all images
                _style_progress_update('merging', 2, total_steps,
                                       'Merging style descriptions...')
                _rebuild_merged_description(deck_id)

                _distill_step = [2]

                def _distill_progress(message):
                    _distill_step[0] += 1
                    _style_progress_update('distilling', _distill_step[0], total_steps, message)

                def _subject_batch_progress(batch_num, total_batches, cards_done, total_cards):
                    step = 4 + batch_num  # after image + merge + tokens + CLIP
                    _style_progress_update('distilling', step, total_steps,
                                           f'Optimizing card subjects ({cards_done}/{total_cards})...')

                _run_style_distillation(deck_id, progress_callback=_distill_progress,
                                        subject_progress_callback=_subject_batch_progress)
                _style_progress_update('complete', total_steps, total_steps,
                                       'Style analysis complete')
                print(f"[inspiration] Style analysis saved for image {new_index + 1} in {deck_id}")
                time.sleep(1.5)
        finally:
            _style_progress_clear()
            _ollama_work_done()

    threading.Thread(target=analyze_bg, daemon=True).start()

    return jsonify({
        'success': True,
        'filename': filename,
        'index': new_index,
        'count': len(current_images),
        'message': 'Inspiration image uploaded. Style analysis in progress...',
    })


@app.route('/api/decks/<deck_id>/reanalyze-inspiration', methods=['POST'])
def reanalyze_inspiration(deck_id):
    """Re-run vision analysis on all inspiration images and rebuild merged description."""
    deck_dir = DECKS_DIR / deck_id
    deck_json = deck_dir / "deck.json"
    if not deck_json.exists():
        return jsonify({'error': 'Deck not found'}), 404

    with open(deck_json) as f:
        data = json.load(f)

    images = data.get('inspiration_images', [])
    if not images:
        # Legacy fallback
        insp_name = data.get('inspiration_image')
        if not insp_name:
            return jsonify({'error': 'No inspiration images uploaded'}), 400
        insp_path = deck_dir / insp_name
        if not insp_path.exists():
            return jsonify({'error': 'Inspiration image file missing'}), 404
        images = [{'filename': insp_name, 'style_description': ''}]

    valid_images = [(i, img) for i, img in enumerate(images) if (deck_dir / img['filename']).exists()]
    if not valid_images:
        return jsonify({'error': 'No inspiration image files found on disk'}), 404

    # Auto-clean dead entries so stale descriptions don't poison style analysis
    dead_count = len(images) - len(valid_images)
    if dead_count > 0:
        data['inspiration_images'] = [img for _, img in valid_images]
        with open(deck_json, 'w') as f:
            json.dump(data, f, indent=2)
        images = data['inspiration_images']
        valid_images = list(enumerate(images))
        print(f"  [style] Cleaned {dead_count} dead inspiration entries from {deck_id}")

    # Pre-flight: check if an AI backend is available for vision analysis
    bcfg = backend_config.load_config()
    ai_available = (bcfg['llm_backend'] == 'local') or openai_client
    if not ai_available:
        print("[inspiration] No AI backend available — cannot re-analyze")
        return jsonify({
            'error': 'No AI backend available. Configure an OpenAI API key or install Ollama for style analysis.',
        }), 503

    def analyze_bg():
        _ollama_work_start()
        n_images = len(valid_images)

        # Calculate card count for batch-level progress on card subjects
        import math
        with open(deck_json) as f:
            _d = json.load(f)
        card_count = len([c for c in _d.get('cards', []) if c.get('name') != 'Card Back'])
        subject_batches = max(1, math.ceil(card_count / 10))

        # N images + merge + tokens + CLIP directives + subject_batches + done
        total_steps = n_images + 3 + subject_batches + 1
        _style_progress_update('analyzing', 0, total_steps, 'Starting style analysis...')
        try:
            bcfg = backend_config.load_config()
            from vision_analyzer import analyze_inspiration_style

            with open(deck_json) as f:
                d = json.load(f)
            imgs = d.get('inspiration_images', images)

            for step_num, (idx, img_entry) in enumerate(valid_images, start=1):
                insp_path = deck_dir / img_entry['filename']
                _style_progress_update('analyzing', step_num - 1, total_steps,
                                       f'Analyzing image {step_num}/{n_images}...', sub_phase='api_call')
                print(f"[inspiration] Re-analyzing image {idx + 1}/{n_images}: {img_entry['filename']}")
                desc = analyze_inspiration_style(
                    insp_path, openai_client,
                    backend='local',
                    local_model=bcfg['ollama_vision_model'],
                )
                _style_progress_update('analyzing', step_num, total_steps,
                                       f'Image {step_num}/{n_images} analyzed')
                if desc and idx < len(imgs):
                    imgs[idx]['style_description'] = desc

            d['inspiration_images'] = imgs
            with open(deck_json, 'w') as f:
                json.dump(d, f, indent=2)

            _style_progress_update('merging', n_images + 1, total_steps,
                                   'Merging style descriptions...')
            _rebuild_merged_description(deck_id)

            # Distillation pipeline reports sub-steps via callback
            _distill_step = [n_images + 1]  # mutable counter for closure

            def _distill_progress(message):
                _distill_step[0] += 1
                _style_progress_update('distilling', _distill_step[0], total_steps, message)

            def _subject_batch_progress(batch_num, total_batches, cards_done, total_cards):
                step = n_images + 3 + batch_num  # after images + merge + tokens + CLIP
                _style_progress_update('distilling', step, total_steps,
                                       f'Optimizing card subjects ({cards_done}/{total_cards})...')

            _run_style_distillation(deck_id, progress_callback=_distill_progress,
                                    subject_progress_callback=_subject_batch_progress)
            _style_progress_update('complete', total_steps, total_steps,
                                   'Style analysis complete')
            print(f"[inspiration] Re-analysis complete for {n_images} image(s) in {deck_id}")
            time.sleep(1.5)
        finally:
            _style_progress_clear()
            _ollama_work_done()

    threading.Thread(target=analyze_bg, daemon=True).start()
    return jsonify({
        'success': True,
        'count': len(valid_images),
        'message': f'Re-analyzing {len(valid_images)} inspiration image(s)...',
    })


def _safe_inspiration_path(deck_dir, filename):
    """Validate an inspiration filename stays within the deck directory."""
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return None
    filepath = (deck_dir / filename).resolve()
    if not filepath.is_relative_to(deck_dir.resolve()):
        return None
    return filepath


@app.route('/api/decks/<deck_id>/inspiration-image')
def serve_inspiration_image(deck_id):
    """Serve the deck's inspiration image."""
    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    # Load deck metadata to find the filename
    deck_json = deck_dir / "deck.json"
    if deck_json.exists():
        with open(deck_json) as f:
            data = json.load(f)
        insp = data.get('inspiration_image')
        filepath = _safe_inspiration_path(deck_dir, insp)
        if filepath and filepath.exists():
            return send_file(filepath)

    # Fallback: serve default reference
    if DEFAULT_REF_IMAGE_PATH.exists():
        return send_file(DEFAULT_REF_IMAGE_PATH)
    return jsonify({'error': 'No inspiration image'}), 404


@app.route('/api/decks/<deck_id>/inspiration-image/<int:index>')
def serve_inspiration_image_by_index(deck_id, index):
    """Serve a specific inspiration image by 0-based index."""
    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    deck_json = deck_dir / "deck.json"
    if not deck_json.exists():
        return jsonify({'error': 'No deck.json'}), 404

    with open(deck_json) as f:
        data = json.load(f)

    images = data.get('inspiration_images', [])
    if index < 0 or index >= len(images):
        return jsonify({'error': f'Invalid index {index}. Have {len(images)} images.'}), 404

    filename = images[index]['filename']
    filepath = _safe_inspiration_path(deck_dir, filename)
    if not filepath or not filepath.exists():
        return jsonify({'error': f'Image file not found: {filename}'}), 404

    return send_file(filepath)


@app.route('/api/decks/<deck_id>/inspiration-image/<int:index>', methods=['DELETE'])
def delete_inspiration_image(deck_id, index):
    """Remove an inspiration image by 0-based index."""
    global active_inspiration_path, active_inspiration_paths

    deck_dir = DECKS_DIR / deck_id
    deck_json_path = deck_dir / "deck.json"
    if not deck_json_path.exists():
        return jsonify({'error': 'Deck not found'}), 404

    with open(deck_json_path) as f:
        data = json.load(f)

    images = data.get('inspiration_images', [])
    if index < 0 or index >= len(images):
        return jsonify({'error': f'Invalid index {index}. Have {len(images)} images.'}), 404

    # Remove the entry and delete the file
    removed = images.pop(index)
    filepath = _safe_inspiration_path(deck_dir, removed.get('filename', ''))
    if filepath and filepath.exists():
        filepath.unlink()
        print(f"[inspiration] Deleted file: {removed['filename']}")

    # Update deck.json
    data['inspiration_images'] = images
    if images:
        data['inspiration_image'] = images[0]['filename']  # backward compat
    else:
        data['inspiration_image'] = ''
        data['inspiration_style_description'] = ''

    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)

    # Rebuild merged description and re-distill style tokens
    if images:
        _rebuild_merged_description(deck_id)
        threading.Thread(target=_guarded_style_distillation, args=(deck_id,), daemon=True).start()
    elif deck_id == active_deck_id:
        active_deck_meta['inspiration_style_description'] = ''
        active_deck_meta['inspiration_images'] = []
        active_deck_meta['style_tokens'] = {}
        active_deck_meta['clip_directives'] = {}
        active_deck_meta['card_subjects'] = {}

    # Update active paths if this is the active deck
    if deck_id == active_deck_id:
        active_inspiration_paths = [
            deck_dir / img['filename'] for img in images
            if (deck_dir / img['filename']).exists()
        ]
        active_inspiration_path = active_inspiration_paths[0] if active_inspiration_paths else DEFAULT_REF_IMAGE_PATH
        active_deck_meta['inspiration_images'] = images
        load_reference_image()

    return jsonify({
        'success': True,
        'removed': removed['filename'],
        'count': len(images),
    })


@app.route('/api/decks/<deck_id>/deck-info')
def get_deck_info(deck_id):
    """Return deck metadata including inspiration status."""
    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    deck_json = deck_dir / "deck.json"
    if not deck_json.exists():
        return jsonify({'error': 'No deck.json'}), 404

    with open(deck_json) as f:
        data = json.load(f)

    insp = data.get('inspiration_image')
    insp_images = data.get('inspiration_images', [])
    # Verify which images actually exist on disk
    valid_images = [
        {**img, 'exists': (deck_dir / img['filename']).exists()}
        for img in insp_images
    ]

    return jsonify({
        'name': data.get('name', deck_id),
        'has_inspiration': bool(insp and (deck_dir / insp).exists()) or any(v['exists'] for v in valid_images),
        'inspiration_image': insp,
        'inspiration_images': valid_images,
        'inspiration_count': sum(1 for v in valid_images if v['exists']),
        'inspiration_style_description': data.get('inspiration_style_description', ''),
        'style_preamble': data.get('style_preamble'),
        'style_source': data.get('style_source', ''),
        'card_count': len(data.get('cards', [])),
        'oversized_generation': data.get('oversized_generation', False),
    })


@app.route('/api/decks/<deck_id>/style-source', methods=['POST'])
def set_style_source(deck_id):
    """Set the manual style source name (e.g. 'Studio Ghibli') for a deck."""
    data = request.json or {}
    style_source = data.get('style_source', '').strip()
    _save_deck_meta_field(deck_id, style_source=style_source)
    # Update the Source: line in the merged description to match the override
    _rebuild_merged_description(deck_id)
    # Re-distill style tokens with new source context
    threading.Thread(target=_guarded_style_distillation, args=(deck_id,), daemon=True).start()
    return jsonify({'success': True, 'style_source': style_source})


@app.route('/api/decks/<deck_id>/distill-style', methods=['POST'])
def distill_style(deck_id):
    """Manually trigger style token distillation for a deck."""
    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    deck_json = deck_dir / "deck.json"
    with open(deck_json) as f:
        data = json.load(f)

    images = data.get('inspiration_images', [])
    # Only use descriptions from images that still exist on disk
    valid = [img for img in images if (deck_dir / img['filename']).exists()]
    dead_count = len(images) - len(valid)
    if dead_count > 0:
        data['inspiration_images'] = valid
        with open(deck_json, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  [style] Cleaned {dead_count} dead inspiration entries from {deck_id}")
    descriptions = [img.get('style_description', '') for img in valid
                    if img.get('style_description')]
    if not descriptions:
        return jsonify({'error': 'No style descriptions available. Upload inspiration images first.'}), 400

    threading.Thread(target=_guarded_style_distillation, args=(deck_id,), daemon=True).start()
    return jsonify({
        'success': True,
        'message': f'Distilling style tokens from {len(descriptions)} description(s)...',
    })


@app.route('/api/decks/<deck_id>/distill-subjects', methods=['POST'])
def distill_subjects(deck_id):
    """Manually trigger card subject distillation for a deck."""
    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    deck_json = deck_dir / "deck.json"
    with open(deck_json) as f:
        data = json.load(f)

    if not data.get('style_tokens'):
        return jsonify({'error': 'No style tokens available. Run style distillation first.'}), 400

    prompts_path = deck_dir / "art_prompts.json"
    if not prompts_path.exists():
        return jsonify({'error': 'No art prompts found. Generate prompts first.'}), 400

    threading.Thread(target=_guarded_subject_distillation, args=(deck_id,), daemon=True).start()
    return jsonify({
        'success': True,
        'message': f'Distilling card subjects for {deck_id}...',
    })


# ---------------------------------------------------------------------------
# Prompt regeneration progress tracking
# ---------------------------------------------------------------------------
prompt_regen_progress = {}  # job_id -> {step, total, message, done, error, count}
flavor_text_progress = {}   # job_id -> {step, total, message, done, error}


@app.route('/api/decks/<deck_id>/regenerate-prompts', methods=['POST'])
def regenerate_prompts_from_inspiration(deck_id):
    """Kick off prompt regeneration as a background job. Returns job_id for polling."""
    from prompt_generator import generate_style_preamble_from_analysis

    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    deck_json = deck_dir / "deck.json"
    with open(deck_json) as f:
        data = json.load(f)

    cards = data.get('cards', [])
    if not cards:
        return jsonify({'error': 'No cards in deck'}), 400

    req_data = request.json or {}
    use_ai = req_data.get('use_ai', False)
    card_names = req_data.get('card_names')
    steer = (req_data.get('steer') or '').strip()  # optional free-text direction

    # Expand cards into per-face prompt units. A double-faced card gets a
    # second unit keyed "<full name> [back]" whose card data is the back face,
    # so the LLM describes the actual back-face scene.
    units = []
    for c in cards:
        units.append((c['name'], c))
        if has_second_art_face(c):
            second = (split_half_card(c, 1) if is_rotated_split(c)
                      else back_face_card(c))
            units.append((face_key(c['name'], 'back'), second))

    # If specific unit keys requested, filter to just those. Plain names mean
    # the front face; "<name> [back]" targets the back face.
    if card_names:
        requested = set(card_names)
        units = [(k, c) for k, c in units if k in requested]
        if not units:
            return jsonify({'error': 'No matching cards found'}), 400

    # Build style preamble from inspiration analysis + manual source
    style_desc = data.get('inspiration_style_description', '')
    style_source = data.get('style_source', '')
    style_preamble = generate_style_preamble_from_analysis(
        style_desc, style_source=style_source)
    _save_deck_meta_field(deck_id, style_preamble=style_preamble)

    job_id = f"regen_{int(time.time() * 1000)}"
    prompt_regen_progress[job_id] = {
        'step': 0,
        'total': len(units),
        'pct': 0,           # 0-100 unified progress across both phases
        'phase': 'generating',
        'message': 'Starting prompt generation...',
        'done': False,
        'error': None,
        'count': 0,
        'style_preamble_preview': '',
    }

    def regen_worker():
        _ollama_work_start()
        from prompt_generator import (generate_prompt,
                                       generate_subject_with_ai)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        prog = prompt_regen_progress[job_id]
        preamble = style_preamble
        bcfg = backend_config.load_config()

        # Build the subject-generation style hint from the CLEAN image-first
        # descriptors (flux_style_prompt) + source name — NOT the SDXL-era mood/
        # themes tokens. Those mislabel (e.g. tagging Wes Anderson "unsettling,
        # eerie"), which trips the subject generator's dark/dramatic path and makes
        # scenes dramatic — the very drama that drowns a calm style. The clean
        # descriptors keep the subject's TONE matched to the actual style (calm
        # styles stay calm/concrete; genuinely dark styles still read as dark).
        _style_hint = style_source or ''
        _flux_style = data.get('flux_style_prompt', '').strip()
        if _flux_style:
            _style_hint = f"{_style_hint} — {_flux_style}" if _style_hint else _flux_style
        try:
            total = len(units)
            new_prompts = [None] * total  # preserve order
            completed = [0]
            lock = threading.Lock()

            can_do_ai = (bcfg['llm_backend'] == 'local') or openai_client
            if use_ai and can_do_ai:
                # ── Parallel AI prompt generation ──
                if bcfg['llm_backend'] == 'local':
                    prog['message'] = 'Loading language model...'
                else:
                    prog['message'] = f'Generating AI prompts: 0/{total}'

                def gen_one_ai(idx_unit):
                    idx, (key, card) = idx_unit
                    max_retries = 5
                    subject = None
                    for attempt in range(max_retries):
                        try:
                            subject = generate_subject_with_ai(
                                card, openai_client,
                                backend=bcfg['llm_backend'],
                                local_model=bcfg['ollama_model'],
                                style_hint=_style_hint,
                                steer=steer,
                            )
                            break
                        except Exception as e:
                            err_str = str(e)
                            if '429' in err_str and attempt < max_retries - 1:
                                import re as _re
                                wait_match = _re.search(r'try again in (\d+\.?\d*)s', err_str)
                                wait_time = float(wait_match.group(1)) + 1 if wait_match else 15
                                time.sleep(wait_time)
                            else:
                                # Fall back to rule-based
                                from prompt_generator import generate_subject_description
                                subject = generate_subject_description(card)
                                break

                    # Store the rich subject as the single per-face prompt. Style is
                    # applied separately (deck-level flux_style_prompt) at generation,
                    # so we no longer bundle a style preamble or distill it down.
                    prompt = subject
                    new_prompts[idx] = {'name': key, 'prompt': prompt}
                    with lock:
                        completed[0] += 1
                        prog['step'] = completed[0]
                        prog['pct'] = 5 + int(45 * completed[0] / total)
                        prog['message'] = f'Generating prompts: {completed[0]}/{total}'
                        # Update in-memory immediately so frontend sees it on next poll
                        if deck_id == active_deck_id:
                            prompts_map[key] = prompt
                        if 'updated_cards' not in prog:
                            prog['updated_cards'] = []
                        prog['updated_cards'].append(key)
                    time.sleep(0.05)

                with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
                    list(pool.map(gen_one_ai, enumerate(units)))
            else:
                # ── Rule-based (fast, but still track progress) ──
                if use_ai and not can_do_ai:
                    prog['ai_fallback'] = True
                    print(f"  [regen] AI requested but no backend available — using rule-based prompts")
                for i, (key, card) in enumerate(units):
                    prompt = generate_prompt(card, None)  # plain scene, no style preamble
                    new_prompts[i] = {'name': key, 'prompt': prompt}
                    prog['step'] = i + 1
                    # Update in-memory immediately
                    if deck_id == active_deck_id:
                        prompts_map[key] = prompt
                    if 'updated_cards' not in prog:
                        prog['updated_cards'] = []
                    prog['updated_cards'].append(key)
                    prog['pct'] = 5 + int(45 * (i + 1) / total)
                    prog['message'] = f'Generating prompts: {i + 1}/{total}'

            # Filter any Nones (shouldn't happen but safety)
            new_prompts = [p for p in new_prompts if p]

            # Save prompts (merge with existing to support partial regen)
            prompts_path = deck_dir / "art_prompts.json"
            if prompts_path.exists():
                with open(prompts_path) as f:
                    existing = json.load(f)
            else:
                existing = []
            merged = {p['name']: p['prompt'] for p in existing}
            for p in new_prompts:
                merged[p['name']] = p['prompt']
            all_prompts = [{'name': n, 'prompt': p} for n, p in merged.items()]
            with open(prompts_path, 'w') as f:
                json.dump(all_prompts, f, indent=2)

            # Update in-memory prompts if active deck
            if deck_id == active_deck_id:
                for p in new_prompts:
                    prompts_map[p['name']] = p['prompt']

            prog['count'] = len(new_prompts)
            prog['style_preamble_preview'] = (preamble[:200] + '...') if len(preamble) > 200 else preamble
            print(f"  [regen] Prompts done: {deck_id} — {len(new_prompts)} prompts")

            # One rich prompt per card now drives generation directly — no separate
            # subject-distillation pass (FLUX has the token budget for the full scene).
            if prog.get('ai_fallback'):
                prog['message'] = f'Generated {len(new_prompts)} rule-based prompts (AI unavailable)'
            else:
                prog['message'] = f'Regenerated {len(new_prompts)} prompts!'
            prog['pct'] = 100
            prog['done'] = True

        except Exception as e:
            prog['error'] = str(e)[:300]
            prog['done'] = True
            print(f"  [regen] Failed: {e}")
        finally:
            _ollama_work_done()

    threading.Thread(target=regen_worker, daemon=True).start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'total': len(units),
    })


@app.route('/api/regen-prompts/progress/<job_id>')
def api_regen_prompts_progress(job_id):
    """Poll prompt regeneration progress."""
    prog = prompt_regen_progress.get(job_id)
    if not prog:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(prog)


# ---------------------------------------------------------------------------
# Flavor text generation
# ---------------------------------------------------------------------------
@app.route('/api/decks/<deck_id>/generate-flavor-text', methods=['POST'])
def generate_flavor_text_endpoint(deck_id):
    """Kick off flavor text generation as a background job. Returns job_id for polling."""
    deck_dir = DECKS_DIR / deck_id
    if not deck_dir.exists():
        return jsonify({'error': 'Deck not found'}), 404

    deck_json = deck_dir / "deck.json"
    with open(deck_json) as f:
        data = json.load(f)

    cards = data.get('cards', [])
    if not cards:
        return jsonify({'error': 'No cards in deck'}), 400

    req_data = request.json or {}
    card_names = req_data.get('card_names')

    if card_names:
        card_name_set = set(card_names)
        cards = [c for c in cards if c['name'] in card_name_set]
        if not cards:
            return jsonify({'error': 'No matching cards found'}), 400

    inspiration_desc = data.get('inspiration_style_description', '')

    job_id = f"flavor_{int(time.time() * 1000)}"
    flavor_text_progress[job_id] = {
        'step': 0,
        'total': len(cards),
        'message': 'Starting flavor text generation...',
        'done': False,
        'error': None,
    }

    def flavor_worker():
        _ollama_work_start()
        from prompt_generator import generate_flavor_text
        from concurrent.futures import ThreadPoolExecutor, as_completed

        prog = flavor_text_progress[job_id]
        bcfg = backend_config.load_config()
        try:
            total = len(cards)
            completed = [0]
            lock = threading.Lock()
            updated_names = []

            def gen_one(card):
                max_retries = 5
                text = ''
                for attempt in range(max_retries):
                    try:
                        text = generate_flavor_text(
                            card,
                            inspiration_description=inspiration_desc,
                            openai_client=openai_client,
                            backend=bcfg['llm_backend'],
                            local_model=bcfg['ollama_model'],
                        )
                        break
                    except Exception as e:
                        err_str = str(e)
                        if '429' in err_str and attempt < max_retries - 1:
                            import re as _re
                            wait_match = _re.search(r'try again in (\d+\.?\d*)s', err_str)
                            wait_time = float(wait_match.group(1)) + 1 if wait_match else 15
                            time.sleep(wait_time)
                        else:
                            break

                # Update card in memory
                with lock:
                    db_card = next((c for c in cards_db if c['name'] == card['name']), None)
                    if db_card and text:
                        db_card['flavor_text'] = text
                        updated_names.append(card['name'])
                    completed[0] += 1
                    prog['step'] = completed[0]
                    prog['message'] = f'Flavor text: {completed[0]}/{total}'
                time.sleep(0.05)

            with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
                list(pool.map(gen_one, cards))

            # Persist to deck.json
            _persist_cards_and_prompts()

            # Auto-recomposite cards that got new flavor text
            # Only re-render cards that already have art — don't inflate generation stats
            for cname in updated_names:
                slug = name_to_slug(cname)
                raw_path = RAW_ART_DIR / f"{slug}.png"
                comp_path = COMPOSITE_DIR / f"{slug}.png"
                if raw_path.exists():
                    db_card = next((c for c in cards_db if c['name'] == cname), None)
                    if db_card:
                        try:
                            render_composite_for_card(db_card, raw_path, comp_path,
                                             deck_fs=active_deck_meta.get('frame_settings'))
                            with generation_lock:
                                existing = generation_status.get(cname)
                                if existing:
                                    existing['message'] = 'Flavor text updated'
                                    existing['has_composite'] = True
                        except Exception as e:
                            print(f"  [flavor] Recomposite failed for {cname}: {e}")

            failed = total - len(updated_names)
            if failed:
                prog['message'] = f'Generated flavor text for {len(updated_names)}/{total} cards ({failed} failed)'
            else:
                prog['message'] = f'Generated flavor text for {len(updated_names)} cards!'
            prog['done'] = True
            print(f"  [flavor] Complete: {deck_id} — {len(updated_names)} cards")

        except Exception as e:
            prog['error'] = str(e)[:300]
            prog['done'] = True
            print(f"  [flavor] Failed: {e}")
        finally:
            _ollama_work_done()

    threading.Thread(target=flavor_worker, daemon=True).start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'total': len(cards),
    })


@app.route('/api/flavor-text/progress/<job_id>')
def api_flavor_text_progress(job_id):
    """Poll flavor text generation progress."""
    prog = flavor_text_progress.get(job_id)
    if not prog:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(prog)


@app.route('/api/cards/flavor-text', methods=['POST'])
def save_flavor_text():
    """Save manually edited flavor text for a card and recomposite."""
    data = request.json
    card_name = data.get('card_name')
    flavor = data.get('flavor_text', '')
    if not card_name:
        return jsonify({'error': 'No card name'}), 400

    card = next((c for c in cards_db if c['name'] == card_name), None)
    if not card:
        return jsonify({'error': f"Card '{card_name}' not found"}), 404

    card['flavor_text'] = flavor
    _persist_cards_and_prompts()

    # Auto-recomposite if raw art exists — don't inflate generation stats
    slug = name_to_slug(card_name)
    raw_path = RAW_ART_DIR / f"{slug}.png"
    comp_path = COMPOSITE_DIR / f"{slug}.png"
    if raw_path.exists():
        try:
            render_composite_for_card(card, raw_path, comp_path,
                                 deck_fs=active_deck_meta.get('frame_settings'))
            with generation_lock:
                existing = generation_status.get(card_name)
                if existing:
                    existing['message'] = 'Flavor text saved'
                    existing['has_composite'] = True
        except Exception as e:
            print(f"  [flavor] Recomposite failed for {card_name}: {e}")

    return jsonify({'success': True})


@app.route('/api/image/scryfall/<slug>')
def serve_scryfall_art(slug):
    """Serve Scryfall default art for a card.

    Checks the active deck's scryfall_art/ dir first, then shared cache.
    """
    if active_deck_id:
        deck_scryfall = DECKS_DIR / active_deck_id / "scryfall_art"
        for ext in ('.jpg', '.png', '.jpeg'):
            path = deck_scryfall / f"{slug}{ext}"
            if path.exists():
                return send_file(path)

    # Check shared Scryfall art cache
    for ext in ('.jpg', '.png', '.jpeg'):
        path = SCRYFALL_ART_DIR / f"{slug}{ext}"
        if path.exists():
            return send_file(path)

    return jsonify({'error': 'Not found'}), 404


# ---------------------------------------------------------------------------
# API Key & Existing Routes
# ---------------------------------------------------------------------------

@app.route('/api/set-key', methods=['POST'])
def set_api_key():
    global openai_client
    data = request.json
    key = data.get('key', '').strip()
    if not key:
        return jsonify({'error': 'No key provided'}), 400

    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=key)
        # Quick validation — list models
        openai_client.models.list()
        # Persist to disk so it survives restarts
        save_api_key(key)
        return jsonify({'success': True, 'message': 'API key validated and saved'})
    except Exception as e:
        openai_client = None
        return jsonify({'error': f'Invalid key: {str(e)[:100]}'}), 400


@app.route('/api/clear-key', methods=['POST'])
def clear_api_key():
    global openai_client
    openai_client = None
    try:
        if API_KEY_PATH.exists():
            API_KEY_PATH.unlink()
    except Exception as e:
        return jsonify({'error': f'Could not remove key file: {e}'}), 500
    return jsonify({'success': True, 'message': 'API key removed'})


@app.route('/api/backend', methods=['GET'])
def get_backend():
    """Return the current backend configuration (MLX-native, always local)."""
    cfg = backend_config.load_config()
    status = backend_config.get_mlx_status()
    return jsonify({
        'config': cfg,
        'ollama_status': status,
        'has_openai_key': False,
    })


@app.route('/api/backend', methods=['POST'])
def set_backend():
    """Update model selections. The backend is always MLX/local now."""
    data = request.json or {}
    cfg = backend_config.load_config()

    # llm_backend is fixed to 'local' (MLX); ignore any cloud switch request.
    cfg['llm_backend'] = 'local'

    if 'ollama_model' in data:
        cfg['ollama_model'] = data['ollama_model']
    if 'ollama_vision_model' in data:
        cfg['ollama_vision_model'] = data['ollama_vision_model']

    backend_config.save_config(cfg)
    return jsonify({'success': True, 'config': cfg})


@app.route('/api/ollama-status')
def ollama_status():
    """Return MLX text/vision availability (legacy route name)."""
    return jsonify(backend_config.get_mlx_status())


@app.route('/api/local-image-status')
def local_image_status():
    """Return status of local image generation capabilities."""
    return jsonify(backend_config.get_local_image_status())


@app.route('/api/local-image-load', methods=['POST'])
def load_local_image_model():
    """Load a local image model in background. Poll /api/status for progress."""
    data = request.json or {}
    model_key = data.get('model', 'sdxl-turbo')

    # Prevent duplicate loads
    with generation_lock:
        if model_load_progress:
            return jsonify({'error': 'A model is already loading', 'in_progress': True}), 409

    def download_progress_cb(downloaded_bytes, total_bytes, desc):
        gb_done = downloaded_bytes / (1024**3)
        gb_total = total_bytes / (1024**3)
        pct = (downloaded_bytes / total_bytes * 100) if total_bytes else 0
        _model_load_progress_update(
            'downloading',
            f'Downloading {gb_done:.1f} / {gb_total:.1f} GB',
            pct=pct, model_key=model_key,
        )

    def phase_progress_cb(message):
        phase = 'loading'
        if 'LoRA' in message or 'Hot-swap' in message:
            phase = 'applying_lora'
        elif 'IP-Adapter' in message:
            phase = 'loading_ip_adapter'
        elif 'already loaded' in message:
            phase = 'complete'
        _model_load_progress_update(phase, message, model_key=model_key)

    def load_worker():
        import time
        _model_load_progress_update('loading', f'Loading {model_key}...', model_key=model_key)
        try:
            success, message = backend_config.activate_local_image_model(
                model_key,
                progress_callback=phase_progress_cb,
                download_progress_callback=download_progress_cb,
            )
            if success:
                _model_load_progress_update('complete', message, pct=100, model_key=model_key)
            else:
                _model_load_progress_update('error', message, model_key=model_key, error=message)
        except Exception as e:
            _model_load_progress_update('error', str(e), model_key=model_key, error=str(e))
        finally:
            time.sleep(3)
            _model_load_progress_clear()

    threading.Thread(target=load_worker, daemon=True).start()

    return jsonify({
        'success': True,
        'async': True,
        'message': f'Loading {model_key} in background...',
    })


@app.route('/api/local-image-unload', methods=['POST'])
def unload_local_image_model():
    """Unload the current local image model to free memory."""
    try:
        from local_image_generator import get_generator
        gen = get_generator()
        gen.unload()
        return jsonify({'success': True, 'message': 'Model unloaded'})
    except ImportError:
        return jsonify({'error': 'Local image generation not available'}), 400


def _is_prompt_stale(slug: str, current_prompt: str, card_name: str = '') -> bool:
    """Check if the current prompts differ from what was used to generate the art."""
    meta_path = RAW_ART_DIR / f"{slug}.meta.json"
    if not meta_path.exists():
        return False  # no art yet, nothing stale
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        # Stale if the card's prompt changed since the art was generated.
        generated_with = meta.get('prompt_sent', '')
        if generated_with and current_prompt:
            if generated_with.strip() != current_prompt.strip():
                return True
        # Check local prompt (distilled subject)
        if card_name:
            current_subject = active_deck_meta.get('card_subjects', {}).get(card_name, '')
            generated_subject = meta.get('distilled_subject', '')
            if current_subject and generated_subject:
                if current_subject.strip() != generated_subject.strip():
                    return True
            elif current_subject and not generated_subject:
                # Subject exists now but wasn't recorded at generation time —
                # older art won't have this field, so mark as stale
                return True
        return False
    except Exception:
        return False


def _composite_mtime_for(slug: str) -> int:
    """Return composite file mtime as int seconds, or 0 if missing.
    Used as cache-busting key in image URLs so the browser fetches a
    fresh copy iff the file actually changed."""
    try:
        return int((COMPOSITE_DIR / f"{slug}.png").stat().st_mtime)
    except (OSError, FileNotFoundError):
        return 0


@app.route('/api/cards')
def get_cards():
    """Return all cards with their status."""
    pinned_set = set(active_deck_meta.get('pinned_cards', []))
    result = []
    for card in cards_db:
        name = card['name']
        slug = name_to_slug(name)
        status = generation_status.get(name, {'status': 'pending'})

        # Check if Scryfall art is available
        has_scryfall = False
        if active_deck_id:
            deck_scryfall_dir = DECKS_DIR / active_deck_id / "scryfall_art"
            has_scryfall = any(
                (deck_scryfall_dir / f"{slug}{e}").exists()
                for e in ('.jpg', '.png', '.jpeg')
            )
        if not has_scryfall:
            has_scryfall = any(
                (SCRYFALL_ART_DIR / f"{slug}{e}").exists()
                for e in ('.jpg', '.png', '.jpeg')
            )

        entry = {
            'name': name,
            'slug': slug,
            'layout': card.get('layout', 'normal'),
            'mana_cost': card.get('mana_cost', ''),
            'type_line': card.get('type_line', ''),
            'card_type': card.get('card_type', ''),
            'colors': card.get('color_identity', card.get('colors', [])),
            'quantity': card.get('quantity', 1),
            'is_commander': card.get('is_commander', False),
            'prompt': prompts_map.get(name, ''),
            'distilled_subject': active_deck_meta.get('card_subjects', {}).get(name, ''),
            'status': status.get('status', 'pending'),
            'message': status.get('message', ''),
            'has_raw_art': status.get('has_raw_art', False),
            'has_composite': status.get('has_composite', False),
            'composite_mtime': _composite_mtime_for(slug),
            'has_ai_art': (RAW_ART_DIR / f"{slug}.meta.json").exists(),
            'prompt_stale': _is_prompt_stale(slug, prompts_map.get(name, ''), card_name=name),
            'has_scryfall_art': has_scryfall,
            'revised_prompt': status.get('revised_prompt', ''),
            'flavor_text': card.get('flavor_text', ''),
            'oracle_text': card.get('oracle_text', ''),
            'power': card.get('power'),
            'toughness': card.get('toughness'),
            'is_pinned': name in pinned_set,
            'frame_overrides': card.get('frame_overrides', {}),
        }

        # Cards with a second art unit (DFC backs, rotated-split right
        # halves): expose the other face so the UI can toggle to it
        if has_second_art_face(card):
            back = card['card_faces'][1]
            bslug = name_to_slug(face_key(name, 'back'))
            has_back_scryfall = False
            if active_deck_id:
                deck_scryfall_dir = DECKS_DIR / active_deck_id / "scryfall_art"
                has_back_scryfall = any(
                    (deck_scryfall_dir / f"{bslug}{e}").exists()
                    for e in ('.jpg', '.png', '.jpeg')
                )
            entry.update({
                'is_dfc': is_dfc(card),
                'is_split_halves': is_rotated_split(card),
                'face_names': [f.get('name', '') for f in card['card_faces'][:2]],
                'layout': card.get('layout'),
                'back_face': {
                    'name': back.get('name', ''),
                    'mana_cost': back.get('mana_cost', ''),
                    'type_line': back.get('type_line', ''),
                    'oracle_text': back.get('oracle_text', ''),
                    'power': back.get('power'),
                    'toughness': back.get('toughness'),
                    'flavor_text': back.get('flavor_text', ''),
                },
                'back_slug': bslug,
                'back_prompt': prompts_map.get(face_key(name, 'back'), ''),
                'has_back_raw': (RAW_ART_DIR / f"{bslug}.png").exists(),
                'has_back_composite': (COMPOSITE_DIR / f"{bslug}.png").exists(),
                'back_composite_mtime': _composite_mtime_for(bslug),
                'has_back_ai_art': (RAW_ART_DIR / f"{bslug}.meta.json").exists(),
                'has_back_scryfall_art': has_back_scryfall,
                'back_frame_overrides': card.get('frame_overrides_back', {}),
            })

        result.append(entry)

    return jsonify(result)


def _persist_cards_and_prompts():
    """Save the in-memory cards_db and prompts_map back to disk for the active deck."""
    global cards_revision
    cards_revision = int(time.time() * 1000)
    if not active_deck_id:
        return
    deck_dir = DECKS_DIR / active_deck_id
    deck_json_path = deck_dir / "deck.json"

    # Update cards in deck.json (preserve metadata fields)
    if deck_json_path.exists():
        with open(deck_json_path) as f:
            data = json.load(f)
    else:
        data = {}
    data['cards'] = cards_db
    with open(deck_json_path, 'w') as f:
        json.dump(data, f, indent=2)

    # Update art_prompts.json
    all_prompts = [{'name': n, 'prompt': p} for n, p in prompts_map.items()]
    with open(ART_PROMPTS_PATH, 'w') as f:
        json.dump(all_prompts, f, indent=2)


@app.route('/api/save-prompt', methods=['POST'])
def api_save_prompt():
    """Save a manually edited art prompt for a single card."""
    data = request.json
    card_name = data.get('card_name', '').strip()
    prompt = data.get('prompt', '').strip()
    if not card_name:
        return jsonify({'error': 'card_name required'}), 400
    if not prompt:
        return jsonify({'error': 'prompt required'}), 400

    prompts_map[card_name] = prompt

    # Persist to disk
    all_prompts = [{'name': n, 'prompt': p} for n, p in prompts_map.items()]
    with open(ART_PROMPTS_PATH, 'w') as f:
        json.dump(all_prompts, f, indent=2)

    # Update the card's prompt in allCards so polling reflects the edit
    for card in cards_db:
        if card['name'] == card_name:
            card['prompt'] = prompt
            break

    return jsonify({'success': True})


@app.route('/api/cards/add', methods=['POST'])
def api_add_card():
    """Add a single card to the active deck by name.

    Looks it up on Scryfall, generates a prompt, and appends it.
    """
    from scryfall_client import fetch_card, fetch_card_by_name, scryfall_to_card_entry, _parse_card_line
    from prompt_generator import generate_prompt

    if not active_deck_id:
        return jsonify({'error': 'No active deck'}), 400

    data = request.json or {}
    raw_input = data.get('name', '').strip()
    quantity = data.get('quantity', 1)
    if not raw_input:
        return jsonify({'error': 'Card name is required'}), 400

    # Parse decklist-format input (e.g. "1x Brightcap Badger // Fungus Frolic (blc) 28 [Creature]")
    parsed = _parse_card_line(raw_input)
    if parsed:
        card_name = parsed['name']
        if parsed['quantity'] > 1:
            quantity = parsed['quantity']
    else:
        card_name = raw_input

    # Check for duplicates — Card Back allows multiple (e.g. "Card Back 2")
    is_card_back = card_name.lower().startswith('card back')
    if not is_card_back:
        existing = next((c for c in cards_db if c['name'].lower() == card_name.lower()), None)
        if existing:
            # Just bump the quantity
            existing['quantity'] = existing.get('quantity', 1) + quantity
            _persist_cards_and_prompts()
            return jsonify({
                'success': True,
                'action': 'quantity_updated',
                'name': existing['name'],
                'quantity': existing['quantity'],
            })

    # Special handling for Card Back — not a real Scryfall card
    if is_card_back:
        from prompt_generator import generate_prompt
        # Allow custom names like "Card Back 2", "Card Back Alt"
        back_name = card_name if card_name != 'Card Back' else card_name
        # Deduplicate name if exact match exists
        existing_names = {c['name'].lower() for c in cards_db}
        final_name = back_name
        if final_name.lower() in existing_names:
            n = 2
            while f"{back_name} {n}".lower() in existing_names:
                n += 1
            final_name = f"{back_name} {n}"

        card = {
            'name': final_name,
            'mana_cost': '',
            'type_line': 'Card Back',
            'oracle_text': '',
            'power': None,
            'toughness': None,
            'loyalty': None,
            'colors': [],
            'color_identity': [],
            'quantity': 1,
            'is_commander': False,
            'card_type': 'other',
            'flavor_text': '',
            'art_crop_url': '',
        }
        style_preamble = active_deck_meta.get('style_preamble')
        prompt = generate_prompt(card, style_preamble)

        cards_db.append(card)
        prompts_map[card['name']] = prompt
        generation_status[card['name']] = {
            'status': 'pending',
            'message': 'Not yet generated',
            'has_raw_art': False,
            'has_composite': False,
        }
        _persist_cards_and_prompts()

        return jsonify({
            'success': True,
            'action': 'added',
            'name': card['name'],
            'type_line': 'Card Back',
            'prompt': prompt,
        })

    # Fetch from Scryfall — use set+number if parsed, else name lookup
    set_code = parsed.get('set_code') if parsed else None
    collector_num = parsed.get('collector_number') if parsed else None
    if set_code and collector_num:
        sf = fetch_card(card_name, set_code=set_code, collector_number=collector_num)
    else:
        sf = fetch_card_by_name(card_name)
    if not sf:
        return jsonify({'error': f'Card not found on Scryfall: {card_name}'}), 404

    card = scryfall_to_card_entry(sf, quantity=quantity)

    # Generate a prompt
    style_preamble = active_deck_meta.get('style_preamble')
    prompt = generate_prompt(card, style_preamble)

    # Add to in-memory state
    cards_db.append(card)
    prompts_map[card['name']] = prompt
    generation_status[card['name']] = {
        'status': 'pending',
        'message': 'Not yet generated',
        'has_raw_art': False,
        'has_composite': False,
    }

    # Persist to disk
    _persist_cards_and_prompts()

    # Fetch Scryfall art synchronously so it's available when JS reloads cards
    art_url = card.get('art_crop_url', '')
    slug = name_to_slug(card['name'])
    scryfall_path = None
    if art_url:
        scryfall_dir = DECKS_DIR / active_deck_id / "scryfall_art"
        scryfall_dir.mkdir(exist_ok=True)
        dest = scryfall_dir / f"{slug}.jpg"
        if not dest.exists():
            try:
                req = urllib.request.Request(art_url, headers={"User-Agent": "MTGProxyDeckGen/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    with open(dest, 'wb') as f:
                        f.write(resp.read())
                scryfall_path = dest
            except Exception as e:
                print(f"  [scryfall] Skip art for {card['name']}: {e}")
        else:
            scryfall_path = dest

    # Render card frame using Scryfall art so the card looks complete immediately
    if scryfall_path and scryfall_path.exists():
        try:
            raw_path = RAW_ART_DIR / f"{slug}.png"
            comp_path = COMPOSITE_DIR / f"{slug}.png"
            if not raw_path.exists():
                img = Image.open(scryfall_path).convert('RGB')
                RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
                img.save(raw_path, 'PNG')
            COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)
            render_composite_for_card(card, raw_path, comp_path,
                                 deck_fs=active_deck_meta.get('frame_settings'))
            with generation_lock:
                generation_status[card['name']] = {
                    'status': 'complete',
                    'message': 'Frame rendered from Scryfall art',
                    'has_raw_art': True,
                    'has_composite': True,
                }
        except Exception as e:
            print(f"  [frame] Could not render frame for {card['name']}: {e}")

    return jsonify({
        'success': True,
        'action': 'added',
        'name': card['name'],
        'type_line': card.get('type_line', ''),
        'prompt': prompt,
    })


@app.route('/api/cards/remove', methods=['POST'])
def api_remove_card():
    """Remove a card from the active deck by name."""
    if not active_deck_id:
        return jsonify({'error': 'No active deck'}), 400

    data = request.json or {}
    card_name = data.get('name', '').strip()
    if not card_name:
        return jsonify({'error': 'Card name is required'}), 400

    # Find the card
    card = next((c for c in cards_db if c['name'] == card_name), None)
    if not card:
        return jsonify({'error': f'Card not found in deck: {card_name}'}), 404

    # Remove from in-memory state
    cards_db.remove(card)
    prompts_map.pop(card_name, None)
    generation_status.pop(card_name, None)

    # Optionally clean up art files
    slug = name_to_slug(card_name)
    deck_dir = DECKS_DIR / active_deck_id
    for subdir in ('raw_art', 'composites', 'scryfall_art'):
        d = deck_dir / subdir
        for ext in ('.png', '.jpg', '.jpeg', '.meta.json'):
            p = d / f"{slug}{ext}"
            if p.exists():
                p.unlink()

    # Persist
    _persist_cards_and_prompts()

    return jsonify({
        'success': True,
        'name': card_name,
        'remaining': len(cards_db),
    })


@app.route('/api/generate', methods=['POST'])
def generate_single():
    """Generate art for a single card."""
    model_cfg = MODEL_OPTIONS.get(active_model_key, MODEL_OPTIONS[DEFAULT_MODEL_KEY])
    if model_cfg.get('backend', 'openai') == 'openai' and not openai_client:
        return jsonify({'error': 'API key not set'}), 400

    data = request.json
    card_name = data.get('card_name')
    feedback = data.get('feedback')
    custom_prompt = data.get('custom_prompt')
    face = data.get('face', 'all')
    if face not in ('front', 'back', 'all'):
        return jsonify({'error': 'face must be front, back, or all'}), 400

    if not card_name:
        return jsonify({'error': 'No card name'}), 400

    # NB: no "model not loaded" guard — the worker (via generate_art_for_card)
    # auto-loads the image model on demand, showing a "Loading image model..."
    # status, and surfaces a clear error on the card if the load fails.

    # Set status immediately so the poller picks it up before the thread starts
    slug = name_to_slug(card_name)
    with generation_lock:
        generation_status[card_name] = {
            'status': 'generating',
            'message': 'Starting...',
            'has_raw_art': (RAW_ART_DIR / f"{slug}.png").exists(),
            'has_composite': (COMPOSITE_DIR / f"{slug}.png").exists(),
        }

    # Run in background thread
    def worker():
        try:
            # Check for early cancel (before Ollama wait)
            if card_name in _cancel_single:
                _cancel_single.discard(card_name)
                return

            # Gate: ensure all LLM/VLM (style analysis) work is done before FLUX
            # generation, so the FLUX worker doesn't co-reside with the analysis
            # models and OOM the server.
            if model_cfg.get('backend') == 'local':
                _wait_for_ollama_idle(timeout=900)

            # Check for cancel after Ollama wait
            if card_name in _cancel_single:
                _cancel_single.discard(card_name)
                return

            success, msg = generate_card_all_faces(card_name, custom_prompt=custom_prompt,
                                                   feedback=feedback, face=face)

            # If cancelled while generating, discard result
            if card_name in _cancel_single:
                _cancel_single.discard(card_name)
                print(f"[generate] {card_name} cancelled — result discarded")
                return

            if not success:
                with generation_lock:
                    generation_status[card_name] = {
                        'status': 'error',
                        'message': msg[:200],
                        'has_raw_art': False,
                        'has_composite': False,
                    }
        except Exception as e:
            print(f"[generate] Thread error for {card_name}: {e}")
            import traceback
            traceback.print_exc()
            with generation_lock:
                generation_status[card_name] = {
                    'status': 'error',
                    'message': f'Error: {str(e)[:200]}',
                    'has_raw_art': False,
                    'has_composite': False,
                }
        finally:
            _cancel_single.discard(card_name)

    thread = threading.Thread(target=worker)
    thread.start()

    return jsonify({'success': True, 'message': f'Generating art for {card_name}...'})


@app.route('/api/generate-batch', methods=['POST'])
def generate_batch():
    """Generate art for multiple cards."""
    global is_generating, batch_phase, batch_phase_detail
    model_cfg = MODEL_OPTIONS.get(active_model_key, MODEL_OPTIONS[DEFAULT_MODEL_KEY])
    if model_cfg.get('backend', 'openai') == 'openai' and not openai_client:
        return jsonify({'error': 'API key not set'}), 400

    if is_generating:
        return jsonify({'error': 'Batch generation already in progress'}), 400

    data = request.json
    card_names = data.get('card_names', [])
    skip_existing = data.get('skip_existing', True)
    feedback = data.get('feedback', '')

    if not card_names:
        # Generate all
        card_names = [c['name'] for c in cards_db]

    face_map = {}
    if skip_existing:
        def _back_face_missing(name):
            card = next((c for c in cards_db if c['name'] == name), None)
            if not card or not has_second_art_face(card):
                return False
            back = name_to_slug(face_key(name, 'back'))
            if is_rotated_split(card):
                # Split halves share one composite — the half's RAW art is
                # the completeness signal
                return not (RAW_ART_DIR / f"{back}.png").exists()
            return not (COMPOSITE_DIR / f"{back}.png").exists()

        def _front_incomplete(name):
            s = generation_status.get(name, {})
            return s.get('status') != 'complete' or not s.get('has_composite')

        # Only generate the faces that are actually missing — a DFC whose
        # front is done but back is absent must NOT re-roll the approved front
        filtered = []
        for n in card_names:
            front_needed = _front_incomplete(n)
            back_needed = _back_face_missing(n)
            if not front_needed and not back_needed:
                continue
            filtered.append(n)
            if front_needed and back_needed:
                face_map[n] = 'all'
            elif back_needed:
                face_map[n] = 'back'
            else:
                face_map[n] = 'front'
        card_names = filtered

    if not card_names:
        return jsonify({'success': True, 'message': 'All cards already generated'})

    # Set phase eagerly so the first poll sees accurate state
    is_generating = True
    batch_phase = 'starting'
    batch_phase_detail = f'Preparing to generate {len(card_names)} cards...'

    # Apply same feedback to all selected cards
    feedback_map = {name: feedback for name in card_names} if feedback else {}
    thread = threading.Thread(target=batch_generate_worker,
                              args=(card_names, feedback_map, face_map))
    thread.start()

    return jsonify({
        'success': True,
        'message': f'Queued {len(card_names)} cards for generation',
        'count': len(card_names),
    })


@app.route('/api/stop-batch', methods=['POST'])
def stop_batch():
    """Stop batch generation."""
    global is_generating, batch_deck_id, _batch_generation_status
    is_generating = False
    # Clear batch state — worker's finally block will also clean up
    with generation_lock:
        _batch_generation_status.clear()
        batch_deck_id = None
    return jsonify({'success': True, 'message': 'Batch generation stopped'})


@app.route('/api/cancel-single', methods=['POST'])
def cancel_single():
    """Cancel a single-card generation in progress."""
    data = request.json
    card_name = data.get('card_name', '').strip()
    if not card_name:
        return jsonify({'error': 'card_name required'}), 400

    _cancel_single.add(card_name)
    with generation_lock:
        if card_name in generation_status:
            generation_status[card_name] = {
                'status': 'cancelled',
                'message': 'Cancelled by user',
                'has_raw_art': generation_status[card_name].get('has_raw_art', False),
                'has_composite': generation_status[card_name].get('has_composite', False),
            }
    return jsonify({'success': True})


@app.route('/api/status')
def get_status():
    """Get generation status for all cards.

    Merges batch progress when viewing the deck that owns the running batch.
    Returns batch_deck_id so frontend knows if a batch runs on another deck.
    """
    with generation_lock:
        statuses = dict(generation_status)
        # Merge batch status if viewing the batch's deck
        if batch_deck_id and batch_deck_id == active_deck_id:
            statuses.update(_batch_generation_status)
    # Attach composite_mtime to each status entry so the poller can detect
    # composite changes without re-fetching /api/cards.
    _dfc_names = {c['name'] for c in cards_db if has_second_art_face(c)}
    for name, s in list(statuses.items()):
        if not isinstance(s, dict):
            continue
        extra = {}
        if s.get('has_composite'):
            extra['composite_mtime'] = _composite_mtime_for(name_to_slug(name))
        if name in _dfc_names:
            bslug = name_to_slug(face_key(name, 'back'))
            extra['has_back_composite'] = (COMPOSITE_DIR / f"{bslug}.png").exists()
            extra['has_back_raw'] = (RAW_ART_DIR / f"{bslug}.png").exists()
            extra['has_back_ai_art'] = (RAW_ART_DIR / f"{bslug}.meta.json").exists()
            extra['back_composite_mtime'] = _composite_mtime_for(bslug)
        if extra:
            statuses[name] = {**s, **extra}
    return jsonify({
        'is_generating': is_generating,
        'has_api_key': openai_client is not None,
        'ollama_busy': not ollama_idle.is_set(),
        'batch_phase': batch_phase,
        'batch_phase_detail': batch_phase_detail,
        'batch_deck_id': batch_deck_id,
        'style_progress': style_analysis_progress,
        'model_load_progress': model_load_progress,
        'cards_rev': cards_revision,
        'ollama_pull_progress': ollama_pull_progress,
        'statuses': statuses,
    })


@app.route('/api/model-config')
def get_model_config():
    """Return available models, pricing, and the active selection."""
    remaining = sum(1 for c in cards_db
                    if generation_status.get(c['name'], {}).get('status') != 'complete'
                    or not generation_status.get(c['name'], {}).get('has_composite'))
    diffusers_ok = backend_config.check_diffusers_installed()
    options = {}
    for k, v in MODEL_OPTIONS.items():
        entry = {
            'label': v['label'],
            'description': v['description'],
            'cost_per_image': v['cost_per_image'],
            'estimated_remaining': round(v['cost_per_image'] * remaining, 2),
            'estimated_total': round(v['cost_per_image'] * len(cards_db), 2),
            'is_local': v.get('backend') == 'local',
        }
        if entry['is_local']:
            entry['_local_model'] = v['model']
        if entry['is_local'] and not diffusers_ok:
            entry['disabled'] = True
            entry['disabled_reason'] = 'Install: pip install torch diffusers transformers accelerate peft'
        options[k] = entry

    return jsonify({
        'active': active_model_key,
        'remaining_cards': remaining,
        'total_cards': len(cards_db),
        'options': options,
    })


@app.route('/api/model-config', methods=['POST'])
def set_model_config():
    """Set the active model/quality for generation."""
    global active_model_key
    data = request.json
    key = data.get('model_key', '')
    if key not in MODEL_OPTIONS:
        return jsonify({'error': f'Unknown model key: {key}'}), 400
    active_model_key = key
    # Persist so it survives server restarts / page reloads
    _bcfg = backend_config.load_config()
    _bcfg['active_model_key'] = key
    backend_config.save_config(_bcfg)
    cfg = MODEL_OPTIONS[key]
    return jsonify({
        'success': True,
        'active': key,
        'label': cfg['label'],
        'cost_per_image': cfg['cost_per_image'],
    })


@app.route('/api/fetch-flavor-text', methods=['POST'])
def fetch_flavor_text():
    """Fetch flavor text for all cards from Scryfall API."""
    try:
        from fetch_flavor_text import fetch_card_data
        import time

        updated = 0
        failed = []

        for card in cards_db:
            if card.get('flavor_text'):
                continue  # already has flavor text

            data = fetch_card_data(card['name'])
            if data.get('flavor_text'):
                card['flavor_text'] = data['flavor_text']
                updated += 1
            else:
                card['flavor_text'] = ''
                failed.append(card['name'])

            time.sleep(0.12)  # Scryfall rate limit

        # Save back to deck file
        if CARD_DB_PATH.exists():
            with open(CARD_DB_PATH) as f:
                deck_data = json.load(f)
            if isinstance(deck_data, dict) and 'cards' in deck_data:
                deck_data['cards'] = cards_db
            else:
                deck_data = cards_db
            with open(CARD_DB_PATH, 'w') as f:
                json.dump(deck_data, f, indent=2)

        return jsonify({
            'success': True,
            'updated': updated,
            'no_flavor': len(failed),
            'message': f"Added flavor text to {updated} cards ({len(failed)} have none)",
        })
    except Exception as e:
        return jsonify({'error': str(e)[:300]}), 500


@app.route('/api/recomposite', methods=['POST'])
def recomposite_single():
    """Re-render the card frame overlay for one card without regenerating art."""
    data = request.json
    card_name = data.get('card_name')
    if not card_name:
        return jsonify({'error': 'No card name'}), 400

    card = next((c for c in cards_db if c['name'] == card_name), None)
    if not card:
        return jsonify({'error': f"Card '{card_name}' not found"}), 404

    # Rotated splits: one COMBINED composite regardless of requested face
    if is_rotated_split(card):
        front_slug = name_to_slug(card_name)
        front_raw = RAW_ART_DIR / f"{front_slug}.png"
        if not front_raw.exists():
            return jsonify({'error': 'No raw art exists for this card — generate art first'}), 400
        try:
            render_composite_for_card(card, front_raw,
                                      COMPOSITE_DIR / f"{front_slug}.png",
                                      deck_fs=active_deck_meta.get('frame_settings'))
            with generation_lock:
                generation_status[card_name] = {
                    'status': 'complete', 'message': 'Frame re-rendered',
                    'has_raw_art': True, 'has_composite': True,
                }
            return jsonify({'success': True,
                            'message': f'Frame re-rendered for {card_name}',
                            'composite_mtime': _composite_mtime_for(front_slug)})
        except Exception as e:
            return jsonify({'error': str(e)[:200]}), 500

    # Faces to re-render: both for DFC cards (or just the requested one)
    req_face = data.get('face', 'all')
    faces = [('front', name_to_slug(card_name), card)]
    if is_dfc(card):
        faces.append(('back', name_to_slug(face_key(card_name, 'back')),
                      back_face_card(card)))
    if req_face in ('front', 'back'):
        faces = [f for f in faces if f[0] == req_face]

    sf_dir = DECKS_DIR / active_deck_id / "scryfall_art" if active_deck_id else None
    rendered = 0
    try:
        for face, slug, face_card in faces:
            raw_path = RAW_ART_DIR / f"{slug}.png"
            comp_path = COMPOSITE_DIR / f"{slug}.png"

            if not raw_path.exists():
                # Fall back to Scryfall art if available
                sf_path = None
                if sf_dir and sf_dir.exists():
                    for ext in ('.jpg', '.png', '.jpeg'):
                        p = sf_dir / f"{slug}{ext}"
                        if p.exists():
                            sf_path = p
                            break
                if sf_path:
                    # Copy Scryfall art as raw art so future operations work
                    img = Image.open(sf_path).convert('RGB')
                    RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
                    img.save(raw_path, 'PNG')
                elif face == 'back':
                    continue  # back face has no art yet — nothing to re-render
                else:
                    return jsonify({'error': 'No raw art exists for this card — generate art first'}), 400

            # Archive current composite as a version before overwriting
            archive = data.get('archive_version', False)
            if archive and comp_path.exists():
                archived = _archive_art(face_key(card_name, face))
                if archived:
                    print(f"  [{card_name}] Archived composition as v{archived['version']}")

            deck_fs = active_deck_meta.get('frame_settings', {})
            render_composite(face_card, str(raw_path), None, str(comp_path),
                             deck_frame_settings=deck_fs)
            rendered += 1

        if not rendered:
            return jsonify({'error': 'No art exists for this face — generate art first'}), 400
        # Status reports FRONT-face file state — a back-only re-render must
        # not assert the front composite exists.
        front_slug = name_to_slug(card_name)
        with generation_lock:
            generation_status[card_name] = {
                'status': 'complete',
                'message': 'Frame re-rendered',
                'has_raw_art': (RAW_ART_DIR / f"{front_slug}.png").exists(),
                'has_composite': (COMPOSITE_DIR / f"{front_slug}.png").exists(),
            }
        resp = {'success': True, 'message': f'Frame re-rendered for {card_name}',
                'composite_mtime': _composite_mtime_for(front_slug)}
        if is_dfc(card):
            resp['back_composite_mtime'] = _composite_mtime_for(
                name_to_slug(face_key(card_name, 'back')))
        return jsonify(resp)
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/recomposite-all', methods=['POST'])
def recomposite_all():
    """Re-render card frame overlays for cards that have raw art."""
    data = request.json or {}
    card_names = data.get('card_names', [])
    if card_names:
        card_name_set = set(card_names)
        target_cards = [c for c in cards_db if c['name'] in card_name_set]
    else:
        target_cards = cards_db
    count = 0
    errors = 0
    deck_fs = active_deck_meta.get('frame_settings', {})
    sf_dir = DECKS_DIR / active_deck_id / "scryfall_art" if active_deck_id else None
    for card in target_cards:
        if is_rotated_split(card):
            slug = name_to_slug(card['name'])
            raw_path = RAW_ART_DIR / f"{slug}.png"
            if raw_path.exists():
                try:
                    render_composite_for_card(card, raw_path,
                                              COMPOSITE_DIR / f"{slug}.png",
                                              deck_fs=deck_fs)
                    count += 1
                except Exception:
                    errors += 1
            continue
        faces = [(name_to_slug(card['name']), card)]
        if is_dfc(card):
            faces.append((name_to_slug(face_key(card['name'], 'back')),
                          back_face_card(card)))
        for slug, face_card in faces:
            raw_path = RAW_ART_DIR / f"{slug}.png"
            comp_path = COMPOSITE_DIR / f"{slug}.png"
            # Fall back to Scryfall art if no raw art
            if not raw_path.exists() and sf_dir and sf_dir.exists():
                for ext in ('.jpg', '.png', '.jpeg'):
                    p = sf_dir / f"{slug}{ext}"
                    if p.exists():
                        img = Image.open(p).convert('RGB')
                        RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
                        img.save(raw_path, 'PNG')
                        break
            if not raw_path.exists():
                continue
            try:
                render_composite(face_card, str(raw_path), None, str(comp_path),
                                 deck_frame_settings=deck_fs)
                with generation_lock:
                    generation_status[card['name']] = {
                        'status': 'complete',
                        'message': 'Frame re-rendered',
                        'has_raw_art': True,
                        'has_composite': True,
                    }
                count += 1
            except Exception:
                errors += 1

    return jsonify({
        'success': True,
        'message': f'Re-rendered {count} cards ({errors} errors)',
        'count': count,
        'errors': errors,
    })


@app.route('/api/versions/<path:card_name>')
def get_versions(card_name):
    """List all archived art versions for a card."""
    versions = list_versions(card_name)
    slug = name_to_slug(card_name)
    # Also include info about current (active) art
    raw_path = RAW_ART_DIR / f"{slug}.png"
    has_current = raw_path.exists()
    return jsonify({
        'card_name': card_name,
        'slug': slug,
        'has_current': has_current,
        'versions': versions,
        'total_versions': len(versions),
    })


@app.route('/api/decks/<deck_id>/regen-subject', methods=['POST'])
def regen_card_subject(deck_id):
    """Regenerate scene direction for a card via LLM."""
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    if not card_name:
        return jsonify({'error': 'No card_name'}), 400
    new_subject = _redistill_single_card_subject(card_name)
    if new_subject:
        return jsonify({'success': True, 'subject': new_subject})
    return jsonify({'error': 'LLM unavailable or no art prompt for this card'}), 500


@app.route('/api/decks/<deck_id>/card-subject', methods=['POST'])
def save_card_subject(deck_id):
    """Save a user-edited scene direction (distilled subject) for a card."""
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    subject = data.get('subject', '').strip()
    if not card_name:
        return jsonify({'error': 'No card_name'}), 400

    subjects = active_deck_meta.get('card_subjects', {})
    if subject:
        subjects[card_name] = subject
    elif card_name in subjects:
        del subjects[card_name]
    active_deck_meta['card_subjects'] = subjects
    _save_deck_meta_field(deck_id, card_subjects=subjects)
    return jsonify({'success': True})


@app.route('/api/revert/<path:card_name>', methods=['POST'])
def revert_version(card_name):
    """Revert a card to a specific archived version."""
    data = request.json
    version_num = data.get('version')
    if not version_num:
        return jsonify({'error': 'No version number specified'}), 400

    success, message = revert_to_version(card_name, int(version_num))
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


# ---------------------------------------------------------------------------
# Frame Designer
# ---------------------------------------------------------------------------

@app.route('/api/frame-presets', methods=['GET'])
def get_frame_presets():
    """Legacy endpoint — returns frame styles as presets for backwards compat."""
    presets = {}
    for key, val in FRAME_STYLES.items():
        presets[key] = {
            'key': key,
            'label': val.get('label', key),
            'description': val.get('description', ''),
        }
    return jsonify(presets)


@app.route('/api/frame-styles', methods=['GET'])
def get_frame_styles():
    """Return frame styles, layer order, and layer metadata for the v2 Frame Designer."""
    styles = {}
    for key, val in FRAME_STYLES.items():
        styles[key] = {
            'key': key,
            'label': val.get('label', key),
            'description': val.get('description', ''),
            'layers': val.get('layers', {}),
            'no_frame': val.get('no_frame', False),
            'mode': val.get('mode', 'svg'),
            'controls': val.get('controls', {}),
        }
    return jsonify({
        'styles': styles,
        'layer_order': FRAME_LAYER_ORDER,
        'layer_meta': {k: v for k, v in FRAME_LAYERS.items()},
    })


@app.route('/api/decks/<deck_id>/frame-settings', methods=['GET', 'POST'])
def deck_frame_settings(deck_id):
    """Get or set deck-level frame settings."""
    if request.method == 'GET':
        meta = active_deck_meta if deck_id == active_deck_id else {}
        return jsonify(meta.get('frame_settings', {}))

    data = request.json or {}
    _save_deck_meta_field(deck_id, frame_settings=data)
    return jsonify({'success': True})


@app.route('/api/decks/<deck_id>/oversized-generation', methods=['GET', 'POST'])
def deck_oversized_generation(deck_id):
    """Get or toggle oversized art generation for a deck.

    When enabled, art is generated at ~1.5x size for more pan/zoom room
    in the Frame Designer.
    """
    if request.method == 'GET':
        meta = active_deck_meta if deck_id == active_deck_id else {}
        return jsonify({'oversized_generation': meta.get('oversized_generation', False)})

    data = request.json or {}
    enabled = bool(data.get('enabled', False))
    _save_deck_meta_field(deck_id, oversized_generation=enabled)
    return jsonify({'success': True, 'oversized_generation': enabled})


@app.route('/api/decks/<deck_id>/art-orientation', methods=['GET', 'POST'])
def deck_art_orientation(deck_id):
    """Get or set art orientation (portrait/landscape) for a deck."""
    if request.method == 'GET':
        meta = active_deck_meta if deck_id == active_deck_id else {}
        return jsonify({'art_orientation': meta.get('art_orientation', 'portrait')})

    data = request.json or {}
    orientation = data.get('orientation', 'portrait')
    if orientation not in ('portrait', 'landscape'):
        return jsonify({'error': 'Invalid orientation'}), 400
    _save_deck_meta_field(deck_id, art_orientation=orientation)
    return jsonify({'success': True, 'art_orientation': orientation})


@app.route('/api/cards/frame-overrides', methods=['POST'])
def save_card_frame_overrides():
    """Save per-card frame overrides. "<name> [back]" targets the back face,
    which keeps its own override set (frame_overrides_back)."""
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    overrides = data.get('frame_overrides', {})
    if not card_name:
        return jsonify({'error': 'No card_name provided'}), 400

    _, card, face, _ = _resolve_card_ref(card_name)
    if not card:
        return jsonify({'error': f'Card not found: {card_name}'}), 404

    card['frame_overrides_back' if face == 'back' else 'frame_overrides'] = overrides
    _persist_cards_and_prompts()
    return jsonify({'success': True})


@app.route('/api/preview-frame', methods=['POST'])
def preview_frame():
    """Render a live preview with custom frame settings. Returns PNG bytes."""
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    frame_settings = data.get('frame_settings', {})

    if not card_name:
        return jsonify({'error': 'No card_name provided'}), 400

    card, _base, _face, slug = _resolve_card_ref(card_name)
    if not card:
        return jsonify({'error': f'Card not found: {card_name}'}), 404

    # Find art: raw art first, then Scryfall (deck-local, then shared)
    raw_path = RAW_ART_DIR / f"{slug}.png"
    deck_sf_dir = DECKS_DIR / active_deck_id / "scryfall_art" if active_deck_id else None

    art_path = None
    if raw_path.exists():
        art_path = raw_path
    elif deck_sf_dir and (deck_sf_dir / f"{slug}.jpg").exists():
        art_path = deck_sf_dir / f"{slug}.jpg"
    elif (SCRYFALL_ART_DIR / f"{slug}.jpg").exists():
        art_path = SCRYFALL_ART_DIR / f"{slug}.jpg"

    if not art_path:
        return jsonify({'error': 'No art available for preview'}), 404

    try:
        # Resolve frame settings so composite_card_preview gets full config
        resolved_fs = resolve_frame_settings(card, frame_settings)
        png_bytes = composite_card_preview(card, art_path, resolved_fs)
        return Response(png_bytes, mimetype='image/png')
    except Exception as e:
        return jsonify({'error': f'Preview failed: {str(e)[:200]}'}), 500


@app.route('/api/frame-asset/<frame_set>/<path:component>')
def get_frame_asset(frame_set, component):
    """Serve frame PNG assets directly (for WYSIWYG canvas layers).

    Path-traversal protected. 24h cache headers for browser caching.
    Examples: /api/frame-asset/m15/w  → shared/frames/m15/w.png
              /api/frame-asset/m15/crown/g → shared/frames/m15/crown/g.png
    """
    # Validate: no traversal, alphanumeric + slash only
    if '..' in frame_set or '..' in component:
        return '', 400
    import re
    if not re.match(r'^[a-z0-9_-]+$', frame_set):
        return '', 400
    if not re.match(r'^[a-z0-9_/]+$', component):
        return '', 400
    frames_dir = (SHARED_DIR / 'frames').resolve()
    path = (frames_dir / frame_set / f'{component}.png').resolve()
    if not path.is_relative_to(frames_dir):
        return '', 400
    if not path.exists():
        return '', 404
    return send_file(str(path), mimetype='image/png',
                     max_age=86400)  # 24h cache


@app.route('/api/render-frame-layer', methods=['POST'])
def render_frame_layer_endpoint():
    """Render just the frame chrome (no art) as transparent PNG.

    Used by WYSIWYG canvas for the frame layer.
    Input: {card_name, frame_settings}
    """
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    frame_settings = data.get('frame_settings', {})
    if not card_name:
        return jsonify({'error': 'No card_name provided'}), 400
    card, _base, _face, _slug = _resolve_card_ref(card_name)
    if not card:
        return jsonify({'error': f'Card not found: {card_name}'}), 404
    try:
        resolved_fs = resolve_frame_settings(card, frame_settings)
        png_bytes = render_frame_layer(card, resolved_fs)
        return Response(png_bytes, mimetype='image/png')
    except Exception as e:
        return jsonify({'error': f'Frame render failed: {str(e)[:200]}'}), 500


@app.route('/api/render-text-overlay', methods=['POST'])
def render_text_overlay_endpoint():
    """Render just the text overlay as transparent PNG.

    Used by WYSIWYG canvas for the text layer.
    Input: {card_name, frame_settings}
    """
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    frame_settings = data.get('frame_settings', {})
    if not card_name:
        return jsonify({'error': 'No card_name provided'}), 400
    card, _base, _face, _slug = _resolve_card_ref(card_name)
    if not card:
        return jsonify({'error': f'Card not found: {card_name}'}), 404
    try:
        resolved_fs = resolve_frame_settings(card, frame_settings)
        png_bytes = render_text_overlay(card, resolved_fs)
        return Response(png_bytes, mimetype='image/png')
    except Exception as e:
        return jsonify({'error': f'Text render failed: {str(e)[:200]}'}), 500


@app.route('/api/cards/art-position', methods=['POST'])
def save_card_art_position():
    """Save per-card art offset and zoom for the WYSIWYG frame designer."""
    data = request.json or {}
    card_name = data.get('card_name', '').strip()
    if not card_name:
        return jsonify({'error': 'No card_name provided'}), 400
    _, card, face, _slug = _resolve_card_ref(card_name)
    if not card:
        return jsonify({'error': f'Card not found: {card_name}'}), 404

    ovr_key = 'frame_overrides_back' if face == 'back' else 'frame_overrides'
    overrides = card.get(ovr_key, {})
    if 'art_offset' in data:
        overrides['art_offset'] = {
            'x': float(data['art_offset'].get('x', 0)),
            'y': float(data['art_offset'].get('y', 0)),
        }
    if 'art_zoom' in data:
        overrides['art_zoom'] = float(data['art_zoom'])
    card[ovr_key] = overrides
    _persist_cards_and_prompts()
    return jsonify({'success': True})


@app.route('/api/delete-version/<path:card_name>', methods=['POST'])
def delete_version_endpoint(card_name):
    """Delete a specific archived version for a card."""
    data = request.json
    version_num = data.get('version')
    if not version_num:
        return jsonify({'error': 'No version number specified'}), 400
    success, message = delete_version(card_name, int(version_num))
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


@app.route('/api/delete-versions-bulk/<path:card_name>', methods=['POST'])
def delete_versions_bulk_endpoint(card_name):
    """Delete all archived versions for a card."""
    versions = list_versions(card_name)
    deleted = 0
    total_freed = 0
    for v in versions:
        success, msg = delete_version(card_name, v['version'])
        if success:
            deleted += 1
    return jsonify({'success': True, 'deleted': deleted})


@app.route('/api/image/version/<slug>/<int:version_num>')
def get_version_image(slug, version_num):
    """Serve a versioned composite (or raw if no composite)."""
    vdir = _versions_dir_for(slug)
    prefix = f"v{version_num}"
    # Prefer composite, fall back to raw
    comp = vdir / f"{prefix}_composite.png"
    raw = vdir / f"{prefix}_raw.png"
    if comp.exists():
        return send_file(str(comp), mimetype='image/png')
    if raw.exists():
        return send_file(str(raw), mimetype='image/png')
    return '', 404


def _safe_serve_image(base_dir, slug):
    """Serve an image file, validating the slug cannot escape base_dir."""
    if '..' in slug or '/' in slug or '\\' in slug:
        return '', 400
    path = (base_dir / f"{slug}.png").resolve()
    if not path.is_relative_to(base_dir.resolve()):
        return '', 400
    if path.exists():
        return send_file(str(path), mimetype='image/png')
    return '', 404


@app.route('/api/image/raw/<slug>')
def get_raw_art(slug):
    """Serve raw DALL-E art."""
    return _safe_serve_image(RAW_ART_DIR, slug)


@app.route('/api/image/composite/<slug>')
def get_composite(slug):
    """Serve composited card image."""
    resp = _safe_serve_image(COMPOSITE_DIR, slug)
    if resp == ('', 404) or (isinstance(resp, tuple) and resp[1] == 404):
        return _safe_serve_image(PROXY_DIR, slug)
    return resp


@app.route('/api/image/proxy/<slug>')
def get_proxy(slug):
    """Serve procedural proxy card."""
    return _safe_serve_image(PROXY_DIR, slug)


@app.route('/api/export-all')
def export_all():
    """Download all composited cards as a ZIP (active deck)."""
    if active_deck_id:
        return api_export_deck(active_deck_id)
    # Legacy fallback
    import zipfile
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for card in cards_db:
            slug = name_to_slug(card['name'])
            comp = COMPOSITE_DIR / f"{slug}.png"
            if comp.exists():
                qty = card.get('quantity', 1)
                if qty == 1:
                    zf.write(str(comp), f"{slug}.png")
                else:
                    for i in range(qty):
                        zf.write(str(comp), f"{slug}_{i+1}.png")

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': 'attachment; filename=deck.zip'}
    )


# ===========================================================================
#  HTML Template
# ===========================================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" href="/favicon.png">
<title>Deck Art Studio</title>
<style>
:root {
  --bg: #0a0a0f;
  --surface: #141420;
  --surface2: #1a1a2e;
  --surface3: #222236;
  --border: #2a2a3e;
  --border-light: #363650;
  --accent: #e94560;
  --danger: #e94560;
  --gold: #f0c040;
  --gold-dim: rgba(240, 192, 64, 0.12);
  --text: #e8e8f0;
  --text-dim: #7a7a90;
  --text-muted: #50506a;
  --text-tertiary: #3e3e55;
  --success: #4caf50;
  --error: #f44336;
  --generating: #ff9800;
  --queued: #2196f3;
  --radius: 8px;
  --radius-lg: 12px;
  --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', 'SF Pro Display', 'Segoe UI', system-ui, -apple-system, sans-serif;
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* Selection/focus */
::selection { background: rgba(233, 69, 96, 0.3); }
:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }

/* Scrollbars */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-light); }

/* --- Header --- */
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 10px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}

header h1 {
  font-size: 1.15em;
  font-weight: 700;
  letter-spacing: -0.02em;
  background: linear-gradient(135deg, var(--accent), var(--gold));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  white-space: nowrap;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* --- Buttons --- */
button, .btn {
  padding: 6px 14px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 0.82em;
  font-weight: 500;
  transition: all var(--transition);
  letter-spacing: 0.01em;
  line-height: 1.4;
}
button:active, .btn:active { transform: scale(0.97); }

.btn-primary {
  background: var(--accent);
  color: white;
}
.btn-primary:hover { background: #d63050; box-shadow: 0 2px 12px rgba(233, 69, 96, 0.3); }
.btn:disabled, .btn-primary:disabled { opacity: 0.35; cursor: not-allowed; pointer-events: none; transform: none; }

.btn-secondary {
  background: var(--surface3);
  border-color: var(--border);
  color: var(--text);
}
.btn-secondary:hover { background: var(--border); border-color: var(--border-light); }

.btn-gold {
  background: linear-gradient(135deg, #c0960a, #f0c040);
  color: #1a1a2e;
  font-weight: 600;
}
.btn-gold:hover { box-shadow: 0 2px 16px rgba(240, 192, 64, 0.35); }

.btn-danger {
  background: rgba(244, 67, 54, 0.15);
  color: var(--error);
  border-color: rgba(244, 67, 54, 0.3);
}
.btn-danger:hover { background: rgba(244, 67, 54, 0.25); }

.btn-sm { padding: 4px 10px; font-size: 0.78em; }
.btn-xs { padding: 3px 10px; font-size: 0.72em; border-radius: 4px; }

.btn-ghost {
  background: transparent;
  color: var(--text-dim);
  border-color: transparent;
  padding: 4px 8px;
}
.btn-ghost:hover { color: var(--text); background: var(--surface3); }

/* --- Deck Selector --- */
.deck-selector {
  display: flex;
  align-items: center;
  gap: 6px;
}

.deck-selector select {
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font-size: 0.82em;
  max-width: 220px;
}

/* Deck action buttons - subtle in header */
.deck-selector .btn-sm { opacity: 0.7; }
.deck-selector .btn-sm:hover { opacity: 1; }

/* --- Import Modal --- */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.75);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.modal-content {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  width: 90%;
  max-width: 640px;
  max-height: 90vh;
  overflow-y: auto;
  animation: slideUp 0.25s ease;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.modal-content h2 {
  color: var(--gold);
  margin-bottom: 16px;
  font-size: 1.1em;
  font-weight: 600;
}

/* Custom dialog system */
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.75);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
  animation: fadeIn 0.2s ease;
}
.dialog-content {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  width: 90%;
  max-width: 440px;
  max-height: 90vh;
  overflow-y: auto;
  animation: slideUp 0.25s ease;
}
.dialog-content.dialog-danger {
  border-color: rgba(244, 67, 54, 0.3);
}
.dialog-content h2 {
  color: var(--gold);
  margin: 0 0 8px 0;
  font-size: 1.1em;
  font-weight: 600;
}
.dialog-content.dialog-danger h2 { color: var(--error); }
.dialog-message {
  color: var(--text-dim);
  font-size: 0.88em;
  margin: 0 0 16px 0;
  line-height: 1.5;
}
.dialog-field {
  margin-bottom: 14px;
}
.dialog-field label {
  display: block;
  font-size: 0.75em;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.dialog-field label.dialog-toggle {
  display: flex;
  text-transform: none;
  font-size: inherit;
  color: inherit;
  letter-spacing: normal;
  margin-bottom: 0;
}
.dialog-field textarea,
.dialog-field input[type="text"] {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font-size: 0.88em;
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;
}
.dialog-field textarea:focus,
.dialog-field input[type="text"]:focus {
  border-color: var(--gold);
  outline: none;
}
.dialog-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  padding: 8px 0;
}
.dialog-toggle input { display: none; }
.toggle-track {
  width: 36px;
  height: 20px;
  border-radius: 10px;
  background: var(--border);
  position: relative;
  flex-shrink: 0;
  transition: background var(--transition);
}
.dialog-toggle input:checked + .toggle-track { background: var(--gold); }
.toggle-track::after {
  content: '';
  position: absolute;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--text);
  top: 2px;
  left: 2px;
  transition: transform var(--transition);
}
.dialog-toggle input:checked + .toggle-track::after { transform: translateX(16px); }
.toggle-label-text {
  font-size: 0.88em;
  color: var(--text);
}
.toggle-description {
  font-size: 0.75em;
  color: var(--text-muted);
}
.dialog-checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 6px 0;
}
.dialog-checkbox input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--gold);
  cursor: pointer;
}
.dialog-checkbox span {
  font-size: 0.88em;
  color: var(--text);
}
.dialog-cost {
  font-size: 0.78em;
  color: var(--text-muted);
  padding: 8px 12px;
  background: var(--surface2);
  border-radius: var(--radius);
  margin-bottom: 16px;
}
.dialog-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 16px;
}

/* --- Action Bar (toolbar below header) --- */
#actionBar {
  display: none;
  align-items: center;
  gap: 10px;
  padding: 8px 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
#actionBar.visible { display: flex; }
.action-bar-count {
  font-size: 0.8em;
  color: var(--text);
  white-space: nowrap;
  min-width: 70px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.action-bar-divider {
  width: 1px;
  height: 22px;
  background: var(--border-light);
  flex-shrink: 0;
  opacity: 0.5;
}
.action-bar-spacer { flex: 1; }
/* --- Collapsible detail sections --- */
.detail-collapsible {
  border-top: 1px solid var(--border);
}
.detail-collapsible-header {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  padding: 10px 8px;
  margin: 0 -8px;
  border-radius: var(--radius);
  user-select: none;
  transition: color 0.15s, background 0.15s;
}
.detail-collapsible-header:hover { color: var(--text); background: var(--surface3); }
.detail-collapsible-header:hover .collapse-label { color: var(--text); }
.detail-collapsible-header:hover .collapse-arrow { color: var(--text-dim); transform: translateX(2px); }
.detail-collapsible.open .detail-collapsible-header:hover .collapse-arrow { transform: rotate(90deg); }
.detail-collapsible-header .collapse-label {
  font-size: 0.8em;
  color: var(--text-dim);
  font-weight: 600;
  flex-shrink: 0;
  letter-spacing: -0.01em;
}
.detail-collapsible-header .collapse-preview {
  font-size: 0.78em;
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
  font-style: italic;
  opacity: 0.6;
}
.detail-collapsible-header .collapse-preview.empty {
  color: var(--text-muted);
  font-style: normal;
  opacity: 0.4;
}
.detail-collapsible-header .collapse-arrow {
  font-size: 0.8em;
  color: var(--text-muted);
  transition: transform 0.2s ease, color 0.15s;
  flex-shrink: 0;
  transform: rotate(0deg);
}
.detail-collapsible.open .collapse-arrow { transform: rotate(90deg); }
.detail-collapsible-body {
  display: none;
  padding: 0 0 12px 0;
  animation: collapseReveal 0.15s ease;
}
.detail-collapsible.open .detail-collapsible-body { display: block; }
@keyframes collapseReveal {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
/* Always-visible detail sections (replacing collapsibles) */
.detail-section-block {
  border-top: 1px solid rgba(255,255,255,0.04);
  padding: 14px 0 12px;
}
.detail-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.detail-section-label {
  font-size: 0.78em;
  color: var(--text-dim);
  font-weight: 600;
}
.section-hint {
  font-weight: 400; font-size: 0.9em; color: var(--text-muted);
  cursor: help; margin-left: 3px;
}
.detail-section-block.collapsed .detail-section-body { display: none; }
.section-toggle { cursor: pointer; user-select: none; }
.section-toggle::before {
  content: '\25BE'; display: inline-block; margin-right: 4px;
  font-size: 0.8em; transition: transform 0.15s;
}
.detail-section-block.collapsed .section-toggle::before { transform: rotate(-90deg); }
.detail-action-subrow {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px; gap: 8px;
}
.detail-action-subrow .detail-generate-new { width: auto; margin-top: 0; flex-shrink: 0; }
.detail-section-count {
  font-size: 0.72em;
  color: var(--text-muted);
}
.detail-section-block textarea {
  margin-bottom: 0;
}

/* --- Card Detail: Identity --- */
.detail-subtitle {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 0.78em;
  color: var(--text-muted);
  line-height: 1.4;
}
.detail-type-line { flex: 1; }
.detail-mana-cost {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}
.detail-mana-cost img {
  width: 16px;
  height: 16px;
  vertical-align: middle;
  border-radius: 50%;
}

/* --- Card Detail: Hero image with overlays --- */
.detail-hero {
  position: relative;
  margin-bottom: 12px;
}
.detail-hero .detail-card-preview {
  margin-bottom: 0;
}
.detail-status-pip {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid rgba(0,0,0,0.4);
  z-index: 2;
  transition: all var(--transition);
}
.pip-complete  { background: var(--success); box-shadow: 0 0 6px rgba(76,175,80,0.4); }
.pip-generating { background: var(--generating); animation: pulse 1s infinite; }
.pip-queued    { background: var(--queued, #42a5f5); }
.pip-error     { background: var(--error); }
.pip-pending   { background: transparent; border-color: rgba(255,255,255,0.15); }

.detail-hero-progress {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: linear-gradient(transparent, rgba(0,0,0,0.75));
  border-radius: 0 0 var(--radius-lg) var(--radius-lg);
  padding: 28px 12px 10px;
  display: none;
  z-index: 1;
}
.detail-hero-progress.active { display: block; }
.hero-progress-bar {
  height: 4px;
  background: rgba(255,255,255,0.12);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 6px;
}
.hero-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--generating), var(--gold));
  border-radius: 2px;
  transition: width 0.3s ease;
  box-shadow: 0 0 8px rgba(240,192,64,0.4);
}
.hero-progress-fill.indeterminate {
  width: 30% !important;
  animation: indeterminate-shimmer 1.5s ease-in-out infinite;
}
.hero-progress-label {
  font-size: 0.72em;
  color: rgba(255,255,255,0.8);
  text-shadow: 0 1px 3px rgba(0,0,0,0.6);
}
.detail-hero.generating .detail-card-preview {
  animation: imageGlow 2s ease-in-out infinite;
}
@keyframes imageGlow {
  0%, 100% { box-shadow: 0 4px 24px rgba(0,0,0,0.5), 0 0 16px rgba(255,152,0,0.12); }
  50% { box-shadow: 0 4px 24px rgba(0,0,0,0.5), 0 0 28px rgba(240,192,64,0.2); }
}

/* --- Card Detail: Smart Action Area --- */
.detail-action-area {
  margin-bottom: 14px;
}
.detail-action-primary {
  width: 100%;
  padding: 12px 0;
  font-size: 0.9em;
  font-weight: 600;
  border-radius: var(--radius);
  border: none;
  cursor: pointer;
  transition: all var(--transition);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.btn-generating {
  background: var(--surface2);
  color: var(--generating);
  cursor: wait;
  opacity: 1 !important;
  border: 1px solid rgba(255,152,0,0.15);
}
.btn-queued {
  background: var(--surface2);
  color: var(--queued, #42a5f5);
  cursor: wait;
  opacity: 1 !important;
  border: 1px solid rgba(33,150,243,0.15);
}
.generating-dot, .queued-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  vertical-align: middle;
}
.generating-dot { background: var(--generating); animation: pulse 1s infinite; }
.queued-dot { background: var(--queued, #42a5f5); animation: pulse 1.5s infinite; }
.detail-feedback-row {
  display: flex;
  gap: 6px;
  align-items: stretch;
}
.detail-feedback-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface2);
  color: var(--text);
  font-size: 0.82em;
  font-style: italic;
  box-sizing: border-box;
  transition: border-color var(--transition), box-shadow var(--transition);
}
.detail-feedback-input::placeholder { color: var(--text-muted); font-style: italic; }
.detail-feedback-input:focus {
  border-color: var(--gold);
  outline: none;
  box-shadow: 0 0 0 2px var(--gold-dim);
}
.detail-feedback-row .btn {
  flex-shrink: 0;
  white-space: nowrap;
  padding: 8px 16px;
}
.detail-generate-new {
  display: block;
  width: 100%;
  text-align: center;
  margin-top: 8px;
  font-size: 0.8em;
  color: var(--text-muted);
  cursor: pointer;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 8px 16px;
  transition: color var(--transition), border-color var(--transition), background var(--transition);
}
.detail-generate-new:hover {
  color: var(--text-dim);
  border-color: var(--text-muted);
  background: rgba(255,255,255,0.03);
}
.detail-error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(244,67,54,0.08);
  border: 1px solid rgba(244,67,54,0.2);
  border-radius: var(--radius);
  margin-bottom: 8px;
  font-size: 0.8em;
  color: var(--error);
}
.detail-error-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--error);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75em;
  font-weight: 700;
}
/* Detail footer actions */
.detail-footer {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.detail-footer .btn {
  font-size: 0.76em;
  color: var(--text-dim);
  padding: 7px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition: all var(--transition);
  flex: 1;
}
.detail-footer .btn:hover { color: var(--text); border-color: var(--border-light); background: var(--surface2); }
.detail-footer .btn-remove { color: var(--text-muted); border-color: transparent; flex: 0 0 auto; }
.detail-footer .btn-remove:hover { color: var(--error); border-color: rgba(244, 67, 54, 0.2); background: rgba(244, 67, 54, 0.06); }

/* --- WYSIWYG Frame Designer --- */
.fd-panel { padding-bottom: 0 !important; }
.fd-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 60px 20px; text-align: center; min-height: 300px;
}
.fd-empty-icon { font-size: 2.5em; margin-bottom: 12px; opacity: 0.4; }
.fd-empty-text { font-size: 0.95em; color: var(--text-dim); margin-bottom: 6px; }
.fd-empty-hint { font-size: 0.78em; color: var(--text-muted); }

.fd-card-name {
  font-size: 0.82em; font-weight: 600; color: var(--text); margin-bottom: 8px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* Canvas */
.fd-canvas-container {
  position: relative; width: 100%; aspect-ratio: 5/7;
  border-radius: var(--radius); overflow: hidden;
  background: var(--surface2); border: 1px solid var(--border);
  cursor: grab;
}
.fd-canvas-container.grabbing { cursor: grabbing; }
.fd-canvas-container canvas {
  width: 100%; height: 100%; display: block;
  image-rendering: auto;
}
.fd-canvas-loading {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.3); pointer-events: none; opacity: 0; transition: opacity 0.2s;
}
.fd-canvas-loading.visible { opacity: 1; }
.fd-spinner {
  width: 28px; height: 28px; border: 3px solid rgba(255,255,255,0.2);
  border-top-color: var(--gold); border-radius: 50%;
  animation: fd-spin 0.8s linear infinite;
}
@keyframes fd-spin { to { transform: rotate(360deg); } }

/* Zoom bar */
.fd-zoom-bar {
  display: flex; align-items: center; gap: 6px; margin: 8px 0 12px;
  padding: 4px 0;
}
.fd-zoom-btn {
  padding: 3px 8px; font-size: 0.75em; font-weight: 500;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--text-dim);
  cursor: pointer; line-height: 1.2;
}
.fd-zoom-btn:hover { border-color: var(--border-light); color: var(--text); }
.fd-zoom-slider {
  flex: 1; height: 4px; -webkit-appearance: none; appearance: none;
  background: var(--border); border-radius: 2px; outline: none;
}
.fd-zoom-slider::-webkit-slider-thumb {
  -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%;
  background: var(--gold); cursor: pointer; border: 2px solid var(--surface);
}
.fd-zoom-slider::-moz-range-thumb {
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--gold); cursor: pointer; border: 2px solid var(--surface);
}

/* Sections */
.fd-section { margin-bottom: 10px; }
.fd-section-label {
  font-size: 0.72em; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.05em; font-weight: 500; margin-bottom: 6px;
}
.fd-section-header {
  display: flex; align-items: center; justify-content: space-between;
  cursor: pointer; padding: 6px 0; user-select: none;
}
.fd-section-header:hover .fd-section-label { color: var(--text-dim); }
.fd-section-arrow {
  font-size: 0.72em; color: var(--text-muted); transition: transform 0.2s;
}
.fd-section-arrow.open { transform: rotate(90deg); }
.fd-section-body { padding-bottom: 8px; }

/* Style strip */
/* Art orientation toggle */
.art-orient-row {
  display: flex; align-items: center; gap: 8px; margin: 8px 0 4px;
  font-size: 0.82em;
}
.art-orient-label { color: var(--text-dim); white-space: nowrap; }
.art-orient-toggle {
  display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden;
}
.art-orient-btn {
  padding: 3px 10px; border: none; background: transparent;
  color: var(--text-dim); font-size: 0.85em; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.art-orient-btn.active { background: var(--gold); color: #000; }
.art-orient-btn:hover:not(.active) { background: var(--surface3); }

/* Double-faced card face toggle (detail panel) */
.face-toggle {
  display: flex; border: 1px solid var(--border); border-radius: 6px;
  overflow: hidden; margin-bottom: 8px;
}
.face-toggle-btn {
  flex: 1; padding: 4px 10px; border: none; background: transparent;
  color: var(--text-dim); font-size: 0.85em; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.face-toggle-btn.active { background: var(--gold); color: #000; }
.face-toggle-btn:hover:not(.active) { background: var(--surface3); }
.face-hint {
  font-size: 0.72em; color: var(--text-muted); margin-bottom: 8px;
  padding: 3px 8px; border-left: 2px solid var(--border);
}

/* DFC badge on grid tiles — top-right, inboard of the status dot */
.dfc-badge {
  position: absolute; top: 4px; right: 22px; z-index: 2;
  background: rgba(0,0,0,0.65); color: #fff; border-radius: 4px;
  font-size: 11px; line-height: 1; padding: 3px 5px;
  pointer-events: none;
}

.fd-deck-style-hint {
  font-size: 0.72em; color: var(--text-muted); margin: 2px 0 6px;
}
.fd-deck-style-hint b { color: var(--text-dim); font-weight: 600; }
.fd-style-btn .deck-default-dot {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: var(--gold); margin-left: 6px; vertical-align: middle;
}
.fd-style-strip {
  display: flex; gap: 6px; flex-wrap: wrap; padding: 2px 0 6px;
}
.fd-style-btn {
  padding: 6px 14px; font-size: 0.75em; font-weight: 500;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 16px; color: var(--text-dim);
  cursor: pointer; white-space: nowrap;
  transition: all var(--transition);
}
.fd-style-btn:hover { border-color: var(--border-light); color: var(--text); background: var(--surface3); }
.fd-style-btn.active { border-color: var(--gold); color: var(--gold); background: rgba(240,192,64,0.08); }

/* Intensity slider */
.fd-intensity-row {
  display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
}
.fd-intensity-label { font-size: 0.75em; color: var(--text-dim); width: 55px; flex-shrink: 0; }
.fd-intensity-slider {
  flex: 1; height: 4px; -webkit-appearance: none; appearance: none;
  background: var(--border); border-radius: 2px; outline: none;
}
.fd-intensity-slider::-webkit-slider-thumb {
  -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%;
  background: var(--gold); cursor: pointer; border: 2px solid var(--surface);
}
.fd-intensity-slider::-moz-range-thumb {
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--gold); cursor: pointer; border: 2px solid var(--surface);
}
.fd-intensity-val { font-size: 0.72em; color: var(--text-muted); width: 32px; text-align: right; }

/* Layer rows (same structure as before) */
.frame-layer-row {
  display: flex; align-items: center; gap: 6px; margin-bottom: 4px; padding: 2px 0;
}
.frame-layer-row.disabled { opacity: 0.4; }
.frame-layer-name { font-size: 0.75em; color: var(--text-dim); width: 62px; flex-shrink: 0; }
.frame-layer-vis {
  width: 16px; height: 16px; accent-color: var(--gold); cursor: pointer; flex-shrink: 0;
}
.frame-layer-slider {
  flex: 1; height: 4px; -webkit-appearance: none; appearance: none;
  background: var(--border); border-radius: 2px; outline: none;
}
.frame-layer-slider::-webkit-slider-thumb {
  -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%;
  background: var(--gold); cursor: pointer; border: 2px solid var(--surface);
}
.frame-layer-slider::-moz-range-thumb {
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--gold); cursor: pointer; border: 2px solid var(--surface);
}
.frame-layer-val { font-size: 0.72em; color: var(--text-muted); width: 32px; text-align: right; }

/* Color / text controls (kept from v1) */
.frame-auto-toggle {
  display: flex; align-items: center; gap: 8px; font-size: 0.78em;
  color: var(--text-dim); cursor: pointer; margin-bottom: 8px;
}
.frame-auto-toggle input { accent-color: var(--gold); }
.frame-quick-swatches { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
/* Two-color frame (gradient/split/gold) segmented control */
.fd-gradient-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.fd-gradient-label { font-size: 0.78em; color: var(--text-dim); white-space: nowrap; }
.fd-seg { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fd-seg-btn {
  background: var(--surface); color: var(--text-dim); border: none;
  padding: 4px 9px; font-size: 0.74em; cursor: pointer; border-right: 1px solid var(--border);
}
.fd-seg-btn:last-child { border-right: none; }
.fd-seg-btn:hover { color: var(--text); }
.fd-seg-btn.active { background: var(--gold); color: #1a1a1a; font-weight: 600; }
.frame-swatch {
  width: 32px; height: 32px; border-radius: 50%; cursor: pointer;
  border: 2px solid transparent; transition: all var(--transition);
}
.frame-swatch:hover { transform: scale(1.15); border-color: var(--text-dim); }
.frame-swatch.active { border-color: var(--gold); box-shadow: 0 0 0 2px var(--gold-dim); }
.frame-color-inputs { display: flex; flex-direction: column; gap: 6px; }
.frame-color-row { display: flex; align-items: center; gap: 6px; }
.frame-color-label { font-size: 0.72em; color: var(--text-dim); width: 52px; flex-shrink: 0; }
.frame-color-picker {
  width: 28px; height: 28px; border: 1px solid var(--border);
  border-radius: 4px; cursor: pointer; padding: 0; background: none;
}
.frame-color-picker::-webkit-color-swatch-wrapper { padding: 2px; }
.frame-color-picker::-webkit-color-swatch { border: none; border-radius: 2px; }
.frame-color-hex {
  width: 72px; padding: 4px 6px; font-size: 0.75em; font-family: monospace;
  background: var(--surface2); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px;
}
.frame-color-hex:focus { border-color: var(--gold); outline: none; }
.frame-text-row { margin-bottom: 6px; }
.frame-text-label { display: block; font-size: 0.72em; color: var(--text-dim); margin-bottom: 2px; }
.frame-text-input {
  width: 100%; padding: 5px 8px; font-size: 0.78em;
  background: var(--surface2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--radius); box-sizing: border-box;
}
.frame-text-input:focus { border-color: var(--gold); outline: none; box-shadow: 0 0 0 2px var(--gold-dim); }
.frame-pt-row { display: flex; align-items: center; gap: 4px; }
.frame-pt-row .frame-text-label { width: 52px; flex-shrink: 0; margin-bottom: 0; }
.frame-pt-input { width: 40px !important; flex: 0 0 40px; text-align: center; }
.frame-pt-slash { color: var(--text-muted); font-size: 0.82em; }

/* Actions (sticky bottom) */
.fd-actions {
  display: flex; gap: 8px; padding: 12px 16px;
  border-top: 1px solid var(--border);
  background: var(--surface);
  position: sticky; bottom: 0; z-index: 2;
  margin: 0 -16px;
}

#actionBar .progress-bar { flex: 1; max-width: 300px; }
#actionBarProgress {
  display: none;
  align-items: center;
  gap: 8px;
  flex: 1;
}
#actionBarProgress .action-bar-message {
  font-size: 0.78em;
  color: var(--text-muted);
  white-space: nowrap;
}

/* --- API status badge (used in Models dialog) --- */
.api-status {
  font-size: 0.75em;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.api-status.connected { background: rgba(76, 175, 80, 0.15); color: var(--success); }
.api-status.disconnected { background: rgba(244, 67, 54, 0.12); color: var(--error); }

/* --- Layout --- */
.main-layout {
  display: grid;
  grid-template-columns: 1fr 380px;
  flex: 1;
  min-height: 0; /* allow flex child to shrink */
}

/* --- Header inputs/selects --- */
header select, header input[type="text"] {
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font-size: 0.8em;
}
header input[type="text"] {
  width: 130px;
  transition: width var(--transition);
}
header input[type="text"]:focus {
  width: 180px;
  border-color: var(--gold);
}
header .separator {
  width: 1px;
  height: 20px;
  background: var(--border);
  margin: 0 2px;
  flex-shrink: 0;
}

.ref-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.78em;
  color: var(--text-dim);
  cursor: pointer;
  white-space: nowrap;
}
.ref-toggle input { margin: 0; cursor: pointer; accent-color: var(--gold); }
.ref-toggle span { user-select: none; }


.cost-estimate {
  font-size: 0.78em;
  padding: 3px 10px;
  border-radius: 12px;
  background: var(--gold-dim);
  color: var(--gold);
  white-space: nowrap;
  font-weight: 500;
}
.cost-estimate.cost-free {
  background: rgba(76,175,80,0.15);
  color: var(--success);
}

.model-status {
  font-size: 0.78em;
  padding: 3px 10px;
  border-radius: 12px;
  font-weight: 500;
  white-space: nowrap;
  animation: pulse 1.5s ease-in-out infinite;
}
.model-status.loading { background: rgba(255,167,38,0.15); color: #ffa726; }
.model-status.ready { background: rgba(76,175,80,0.15); color: var(--success); animation: none; }
.model-status.error { background: rgba(244,67,54,0.12); color: var(--error); animation: none; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.stat-complete {
  color: var(--success);
  background: rgba(76, 175, 80, 0.1);
  padding: 2px 10px;
  border-radius: 10px;
  font-weight: 500;
}
.stat-generating {
  color: var(--generating);
  background: rgba(255, 152, 0, 0.1);
  padding: 2px 10px;
  border-radius: 10px;
  font-weight: 500;
}
.stat-pending {
  color: var(--text-muted);
  background: rgba(80, 80, 106, 0.15);
  padding: 2px 10px;
  border-radius: 10px;
}

/* --- Card Grid --- */
.card-grid-container {
  overflow-y: auto;
  cursor: default;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
  padding: 14px;
}

.card-tile {
  background: var(--surface2);
  border-radius: var(--radius);
  overflow: hidden;
  cursor: pointer;
  transition: all var(--transition);
  border: 2px solid transparent;
  position: relative;
}

.card-tile:hover {
  border-color: var(--border-light);
  transform: translateY(-2px);
  box-shadow: 0 4px 20px rgba(0,0,0,0.4);
}
.card-tile.selected {
  border-color: var(--gold);
  box-shadow: 0 0 0 1px var(--gold), 0 4px 20px rgba(240, 192, 64, 0.25);
  transform: translateY(-2px);
}
.card-tile.commander { border-color: rgba(240, 192, 64, 0.4); }
.card-tile.commander:hover { border-color: var(--gold); }

.card-tile img {
  width: 100%;
  aspect-ratio: 5/7;
  object-fit: cover;
  display: block;
  transition: opacity 0.3s;
}

.card-tile-info {
  padding: 6px 8px 7px;
  position: relative;
  background: linear-gradient(to bottom, var(--surface), var(--bg));
}

.card-tile-name {
  font-size: 0.73em;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: -0.01em;
}

.card-tile-type {
  font-size: 0.62em;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
}

.card-status-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--surface2);
  box-shadow: 0 1px 3px rgba(0,0,0,0.4);
}
.badge-complete { background: var(--success); }
.badge-generating { background: var(--generating); animation: pulse 1s infinite; }
.badge-queued { background: var(--queued); }
.badge-error { background: var(--error); }
.badge-pending { background: var(--border); }
.badge-analyzing {
  background: var(--queued);
  animation: analyzeGlow 1.5s ease-in-out infinite;
  width: 12px;
  height: 12px;
  box-shadow: 0 0 6px rgba(33, 150, 243, 0.6), 0 1px 3px rgba(0,0,0,0.4);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

@keyframes analyzeGlow {
  0%, 100% { box-shadow: 0 0 4px rgba(33, 150, 243, 0.4), 0 1px 3px rgba(0,0,0,0.4); opacity: 0.7; }
  50% { box-shadow: 0 0 10px rgba(33, 150, 243, 0.8), 0 1px 3px rgba(0,0,0,0.4); opacity: 1; }
}

/* --- Card tile visual states --- */
.card-tile.tile-generating {
  border-color: var(--generating);
  animation: borderShimmer 2s ease-in-out infinite;
}
@keyframes borderShimmer {
  0%, 100% { border-color: var(--generating); box-shadow: 0 0 8px rgba(255,152,0,0.2); }
  50% { border-color: var(--gold); box-shadow: 0 0 14px rgba(240,192,64,0.4); }
}
.card-tile.tile-queued {
  border-color: var(--queued);
  animation: borderShimmerQueued 2.5s ease-in-out infinite;
}
@keyframes borderShimmerQueued {
  0%, 100% { border-color: var(--queued); box-shadow: 0 0 4px rgba(33,150,243,0.15); }
  50% { border-color: #42a5f5; box-shadow: 0 0 10px rgba(33,150,243,0.3); }
}
.card-tile.tile-pending img { filter: saturate(0.35) brightness(0.85); }
.card-tile.tile-pending:hover img { filter: saturate(0.6) brightness(0.95); }
.card-tile.tile-error { border-color: rgba(244,67,54,0.5); }
.card-tile.tile-error:hover { border-color: var(--error); box-shadow: 0 0 10px rgba(244,67,54,0.25); }
.card-tile.selected.tile-generating,
.card-tile.selected.tile-queued,
.card-tile.selected.tile-error {
  animation: none;
  border-color: var(--gold);
  box-shadow: 0 0 0 1px var(--gold), 0 4px 20px rgba(240,192,64,0.25);
}
.card-tile.tile-generating:hover { border-color: var(--gold); }
.card-tile.tile-error:hover { border-color: var(--error); }
.card-tile.tile-queued:hover { border-color: #42a5f5; }

.select-checkbox {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  border: 1.5px solid rgba(255,255,255,0.25);
  background: rgba(0,0,0,0.5);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  opacity: 0;
  transition: opacity var(--transition);
  z-index: 10;
  cursor: pointer;
}

.card-tile:hover .select-checkbox,
.card-tile.checked .select-checkbox { opacity: 1; }
.card-tile.checked .select-checkbox {
  background: var(--accent);
  border-color: var(--accent);
}

.pin-icon {
  position: absolute;
  top: 50%;
  right: 4px;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  border-radius: 4px;
  border: 1.5px solid rgba(255,255,255,0.25);
  background: rgba(0,0,0,0.5);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity var(--transition);
  cursor: pointer;
  z-index: 2;
}
.pin-icon::after {
  content: '';
  width: 6px;
  height: 6px;
  border: 1.5px solid rgba(255,255,255,0.8);
  border-radius: 50%;
}
.pin-icon.pinned {
  opacity: 1;
  background: var(--gold);
  border-color: var(--gold);
}
.pin-icon.pinned::after {
  border-color: #000;
  background: #000;
}
.card-tile:hover .pin-icon { opacity: 0.7; }
.card-tile:hover .pin-icon.pinned { opacity: 1; }

/* --- Detail Panel --- */
.detail-panel {
  background: var(--surface);
  border-left: 1px solid var(--border);
  overflow-y: auto;
  padding: 0;
  transition: all 0.3s ease;
  scroll-behavior: smooth;
}

.detail-panel-inner {
  padding: 16px;
}

/* --- Panel Tabs --- */
.panel-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  padding: 0 16px;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  background: var(--surface);
  z-index: 2;
}
.panel-tab {
  padding: 10px 16px 8px;
  font-size: 0.82em;
  font-weight: 500;
  color: var(--text-muted);
  cursor: pointer;
  border: none;
  border-radius: 0;
  background: none;
  box-shadow: inset 0 -2px 0 transparent;
  transition: all var(--transition);
}
.panel-tab:hover { color: var(--text-dim); }
.panel-tab.active { color: var(--gold); box-shadow: inset 0 -2px 0 var(--gold); }
.detail-panel h2 {
  font-size: 1.1em;
  margin-bottom: 2px;
  color: var(--text);
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.3;
}

/* Fade-in when detail panel content refreshes */
@keyframes detailFadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
#cardDetail .detail-panel-inner {
  animation: detailFadeIn 0.2s ease;
}

.detail-card-preview {
  width: 100%;
  border-radius: var(--radius-lg);
  margin-bottom: 4px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.5);
  transition: box-shadow var(--transition);
}
.detail-card-preview:hover {
  box-shadow: 0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px rgba(240, 192, 64, 0.15);
}

/* .detail-meta removed — replaced by .detail-subtitle */

.detail-section {
  margin-bottom: 14px;
}

.detail-section label {
  display: block;
  font-size: 0.72em;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 4px;
  font-weight: 500;
}

.detail-section textarea,
.detail-section-block textarea,
.detail-collapsible-body textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface2);
  color: var(--text);
  font-family: inherit;
  font-size: 0.82em;
  line-height: 1.6;
  resize: vertical;
  transition: border-color var(--transition), box-shadow var(--transition);
  box-sizing: border-box;
}

.detail-section textarea:focus,
.detail-section-block textarea:focus,
.detail-collapsible-body textarea:focus {
  border-color: var(--gold);
  outline: none;
  box-shadow: 0 0 0 2px var(--gold-dim);
}

.collapsible-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 6px;
}

.detail-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-actions .btn { width: 100%; text-align: center; }

/* Action groups in detail panel */
.detail-action-group {
  display: flex;
  gap: 6px;
}
.detail-action-group .btn { flex: 1; }

.progress-bar {
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  overflow: hidden;
  margin: 10px 0;
}
.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--gold));
  transition: width 0.3s;
  border-radius: 2px;
}
.progress-bar-fill.indeterminate {
  width: 30% !important;
  animation: indeterminate-shimmer 1.5s ease-in-out infinite;
}
@keyframes indeterminate-shimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(400%); }
}

/* Style analysis progress */
.style-progress-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
}
.style-progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.style-progress-label {
  font-size: 0.82em;
  color: var(--text);
  font-weight: 500;
}
.style-progress-step {
  font-size: 0.72em;
  color: var(--text-muted);
}
.style-progress-bar {
  height: 6px;
  margin: 0;
}

/* Toast embedded progress bar */
.toast-progress { margin-top: 6px; width: 100%; }
.toast-progress .progress-bar { display: block; height: 3px; margin: 0; }

/* Model Hub inline progress */
.model-load-progress { margin-top: 6px; }
.model-load-progress .progress-bar { height: 4px; margin-bottom: 4px; }
.model-load-msg { font-size: 0.68em; color: var(--text-muted); }

/* .detail-progress removed — replaced by .detail-hero-progress overlay */

/* --- No Selection (Overview) state --- */
.overview-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.overview-divider {
  width: 100%;
  border: none;
  border-top: 1px solid var(--border);
  margin: 8px 0;
  opacity: 0.6;
}

/* --- Version History --- */
.version-history {
  margin-top: 0;
  padding-top: 0;
}

.version-history h3 {
  display: none; /* Label is in the collapsible header */
}

.version-strip {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 4px 0;
  scrollbar-width: thin;
}

.version-thumb {
  flex-shrink: 0;
  width: 72px;
  cursor: pointer;
  border-radius: 6px;
  border: 2px solid transparent;
  overflow: hidden;
  transition: all var(--transition);
  position: relative;
  background: var(--bg);
}

.version-thumb:hover { border-color: var(--border-light); }
.version-thumb.active { border-color: var(--gold); box-shadow: 0 0 8px rgba(240, 192, 64, 0.25); }

.version-thumb img {
  width: 100%;
  aspect-ratio: 5/7;
  object-fit: cover;
  display: block;
}

.version-thumb-label {
  font-size: 0.58em;
  text-align: center;
  padding: 2px;
  color: var(--text-dim);
  background: rgba(0,0,0,0.7);
}

.version-thumb-label .ver-model {
  display: block;
  font-size: 0.85em;
  color: var(--text-muted);
}

.version-actions {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

.no-versions {
  font-size: 0.78em;
  color: var(--text-muted);
  font-style: italic;
}

.version-delete-all {
  display: flex;
  align-items: flex-end;
  padding-bottom: 4px;
}
.version-delete-all a {
  color: var(--text-muted) !important;
  font-size: 0.65em !important;
  text-decoration: none;
  transition: color var(--transition);
  white-space: nowrap;
}
.version-delete-all a:hover {
  color: var(--error) !important;
}

/* Version hover preview — enlarged card tooltip */
.version-thumb-preview {
  position: fixed;
  width: 220px;
  pointer-events: none;
  z-index: 900;
  animation: fadeIn 0.15s ease;
}
.version-thumb-preview img {
  width: 100%;
  aspect-ratio: 5/7;
  object-fit: cover;
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.6);
  border: 2px solid var(--border-light);
}
.version-thumb-preview .preview-label {
  text-align: center;
  font-size: 0.75em;
  color: var(--text-dim);
  margin-top: 4px;
}

/* --- Deck Overflow Menu --- */
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}
.deck-overflow-wrap {
  position: relative;
  flex-shrink: 0;
}
.deck-overflow-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0 !important;
  font-size: 1.1em;
  letter-spacing: 1px;
  border-radius: var(--radius);
  color: var(--text-muted);
}
.deck-overflow-btn:hover { color: var(--text); background: var(--surface3); }
.deck-overflow-btn.active { background: var(--surface3); color: var(--gold); }
.deck-overflow-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  min-width: 180px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  z-index: 100;
  padding: 6px 0;
  animation: slideDown 0.15s ease;
}
.deck-menu-item {
  display: block;
  width: 100%;
  padding: 7px 14px;
  font-size: 0.8em;
  color: var(--text-dim);
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
}
.deck-menu-item:hover { background: var(--surface3); color: var(--text); }
.deck-menu-danger { color: var(--error); }
.deck-menu-danger:hover { background: rgba(244,67,54,0.1); }
.deck-menu-sep { height: 1px; background: var(--border); margin: 4px 0; }

/* --- Inspiration (in overview panel) --- */
.upload-btn {
  padding: 4px 10px;
  border: 1px dashed var(--border-light);
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 0.78em;
  transition: all var(--transition);
}
.upload-btn:hover {
  border-color: var(--gold);
  color: var(--gold);
  background: var(--gold-dim);
}
.inspiration-thumb-lg {
  width: 64px;
  height: 64px;
  border-radius: var(--radius);
  object-fit: cover;
  border: 2px solid var(--border);
  cursor: pointer;
  transition: border-color var(--transition);
  flex-shrink: 0;
}
.inspiration-thumb-lg:hover { border-color: var(--gold); }
.inspiration-gallery {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.inspiration-thumb-item {
  position: relative;
  width: 72px;
  height: 72px;
  flex-shrink: 0;
}
.inspiration-thumb-item img {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-lg);
  object-fit: cover;
  border: 2px solid var(--border);
  cursor: pointer;
  transition: border-color var(--transition), box-shadow var(--transition);
}
.inspiration-thumb-item img:hover { border-color: var(--gold); box-shadow: 0 2px 12px rgba(240, 192, 64, 0.2); }
.inspiration-thumb-item .insp-delete-btn {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--error);
  color: #fff;
  border: none;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  cursor: pointer;
  display: none;
  padding: 0;
}
.inspiration-thumb-item:hover .insp-delete-btn { display: block; }
.inspiration-add-btn {
  width: 72px;
  height: 72px;
  border-radius: var(--radius-lg);
  border: 2px dashed var(--border-light);
  background: transparent;
  color: var(--text-dim);
  font-size: 24px;
  cursor: pointer;
  transition: border-color var(--transition), color var(--transition), background var(--transition);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.inspiration-add-btn:hover {
  border-color: var(--gold);
  color: var(--gold);
  background: var(--gold-dim);
}
.style-desc-full {
  font-size: 0.75em;
  color: var(--text-dim);
  line-height: 1.55;
  margin: 8px 0 14px;
  max-height: 80px;
  overflow-y: auto;
  border-left: 2px solid var(--border-light);
  padding-left: 10px;
  font-style: italic;
}
.style-source-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.style-source-label { font-size: 0.75em; color: var(--text-muted); white-space: nowrap; }
.style-source-input {
  flex: 1; min-width: 0;
  padding: 6px 10px; font-size: 0.82em;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--text);
  transition: border-color var(--transition), box-shadow var(--transition);
}
.style-source-input:focus { border-color: var(--gold); outline: none; box-shadow: 0 0 0 2px var(--gold-dim); }
.style-source-input::placeholder { color: var(--text-muted); font-style: italic; }

/* --- Overview Panel Sections --- */
#noSelection {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 14px;
  padding: 20px;
  color: var(--text-dim);
  overflow-y: auto;
}
.overview-section { width: 100%; }
.overview-section-title {
  font-size: 0.82em;
  color: var(--text-dim);
  letter-spacing: -0.01em;
  margin-bottom: 12px;
  font-weight: 600;
}
.overview-btn-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.overview-stats-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78em;
  margin-bottom: 8px;
  flex-wrap: wrap;
  row-gap: 6px;
}
.overview-stats-row .stats-spacer { flex: 1; min-width: 8px; }

/* --- Model dropdown optgroups --- */
#modelSelect { max-width: 260px; min-width: 180px; }

/* Responsive */
@media (max-width: 900px) {
  .main-layout { grid-template-columns: 1fr; }
  .detail-panel { border-left: none; border-top: 1px solid var(--border); max-height: 50vh; }
}

/* --- Toast Notifications --- */
#toastContainer {
  position: fixed; top: 16px; right: 16px; z-index: 10000;
  display: flex; flex-direction: column; gap: 8px;
  pointer-events: none;
  max-height: calc(100vh - 32px); overflow-y: auto;
}
.toast {
  pointer-events: auto;
  max-width: 380px; min-width: 280px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 12px 16px;
  display: flex; align-items: flex-start; gap: 10px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  animation: toastIn 0.25s ease forwards;
  border-left: 3px solid var(--border-light);
}
.toast.dismissing { animation: toastOut 0.2s ease forwards; }
.toast.toast-success { border-left-color: var(--success); }
.toast.toast-error { border-left-color: var(--error); }
.toast.toast-warning { border-left-color: var(--generating); }
.toast.toast-info { border-left-color: var(--queued); }
.toast-icon {
  flex-shrink: 0; width: 18px; height: 18px;
  display: flex; align-items: center; justify-content: center;
  font-size: 0.85em; font-weight: 700; border-radius: 50%;
}
.toast-success .toast-icon { color: var(--success); }
.toast-error .toast-icon { color: var(--error); }
.toast-warning .toast-icon { color: var(--generating); }
.toast-info .toast-icon { color: var(--queued); }
.toast-body { flex: 1; font-size: 0.82em; color: var(--text); line-height: 1.4; }
.toast-dismiss {
  flex-shrink: 0; background: none; border: none; color: var(--text-muted);
  cursor: pointer; font-size: 1em; padding: 0; line-height: 1;
  opacity: 0.5; transition: opacity var(--transition);
}
.toast-dismiss:hover { opacity: 1; }
@keyframes toastIn {
  from { opacity: 0; transform: translateX(40px); }
  to { opacity: 1; transform: translateX(0); }
}
@keyframes toastOut {
  from { opacity: 1; transform: translateX(0); }
  to { opacity: 0; transform: translateX(40px); }
}

/* --- Empty States --- */
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 60px 24px; text-align: center; gap: 12px;
  height: 100%; min-height: 300px;
}
.empty-state-icon {
  font-size: 2.5em; opacity: 0.15; margin-bottom: 4px;
}
.empty-state-title {
  font-size: 1em; font-weight: 600; color: var(--text-dim);
}
.empty-state-hint {
  font-size: 0.82em; color: var(--text-muted); max-width: 280px; line-height: 1.5;
}
.style-hint {
  font-size: 0.72em; color: var(--text-muted); text-align: center;
  margin-top: 6px; line-height: 1.4;
}
.next-step-hint {
  font-size: 0.78em; color: var(--text-dim);
  display: flex; align-items: center; gap: 10px;
  margin-top: 8px; padding: 8px 12px;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius);
}
.next-step-hint .btn { flex-shrink: 0; }

/* --- Setup Bar --- */
#setupBar {
  background: var(--surface2); border-bottom: 1px solid var(--border);
  padding: 8px 20px; display: flex; align-items: center; justify-content: center;
  gap: 12px; font-size: 0.8em; color: var(--text-dim);
  animation: slideDown 0.2s ease;
}
#setupBar.hidden { display: none; }
.setup-bar-actions { display: flex; gap: 6px; }
.setup-bar-actions .btn {
  font-size: 0.85em; padding: 3px 12px; border-radius: 12px;
}
.setup-bar-dismiss {
  position: absolute; right: 16px;
  background: none; border: none; color: var(--text-muted);
  cursor: pointer; font-size: 0.9em; opacity: 0.5;
  transition: opacity var(--transition);
}
.setup-bar-dismiss:hover { opacity: 1; }

/* --- Welcome Hero --- */
#welcomeHero {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 48px 24px; text-align: center; height: 100%;
  animation: fadeIn 0.4s ease;
}
.welcome-title {
  font-size: 1.6em; font-weight: 700; margin-bottom: 8px;
  background: linear-gradient(135deg, var(--accent), var(--gold));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.welcome-subtitle {
  font-size: 0.95em; color: var(--text-dim); margin-bottom: 36px;
  max-width: 400px; line-height: 1.5;
}
.welcome-backends {
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
  max-width: 520px; width: 100%; margin-bottom: 28px;
}
.welcome-card {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 24px 20px;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  transition: all var(--transition); cursor: default;
}
.welcome-card:hover { border-color: var(--border-light); transform: translateY(-2px); }
.welcome-card.setup-done { border-color: var(--success); }
.welcome-card-icon { font-size: 1.8em; margin-bottom: 4px; }
.welcome-card-title { font-size: 0.9em; font-weight: 600; color: var(--text); }
.welcome-card-desc { font-size: 0.78em; color: var(--text-dim); line-height: 1.4; }
.welcome-card-action {
  margin-top: 8px; font-size: 0.8em; padding: 5px 16px;
}
.welcome-card-setup {
  width: 100%; margin-top: 10px; display: flex; flex-direction: column; gap: 8px;
  animation: fadeIn 0.2s ease;
}
.welcome-card-setup input[type="password"] {
  width: 100%; padding: 7px 10px; font-size: 0.85em;
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  color: var(--text);
}
.welcome-card-setup input:focus { border-color: var(--gold); outline: none; }
.welcome-check-list {
  text-align: left; font-size: 0.78em; color: var(--text-dim);
  display: flex; flex-direction: column; gap: 4px;
}
.welcome-check-item { display: flex; align-items: center; gap: 6px; }
.welcome-check-item .check-ok { color: var(--success); }
.welcome-check-item .check-missing { color: var(--text-muted); }
.welcome-divider {
  font-size: 0.78em; color: var(--text-muted); margin-bottom: 20px;
}
.welcome-import-btn {
  font-size: 0.9em; padding: 10px 28px;
}
.welcome-card-status {
  font-size: 0.78em; font-weight: 600; color: var(--success);
  display: flex; align-items: center; gap: 4px; margin-top: 4px;
}

/* --- Model Hub --- */
#modelHub .modal-content { max-width: 820px; }
.model-hub-section { margin-bottom: 24px; }
.model-hub-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
.model-hub-header h3 {
  font-size: 0.82em; color: var(--text-dim);
  letter-spacing: -0.01em; font-weight: 600;
}
.model-hub-header .api-status { font-size: 0.72em; }
.model-hub-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 10px;
}
.model-card {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px 14px;
  display: flex; flex-direction: column; gap: 6px;
  transition: all var(--transition);
}
.model-card:hover { border-color: var(--border-light); }
.model-card.active { border-color: var(--gold); box-shadow: 0 0 0 1px var(--gold); }
.model-card.disabled { opacity: 0.45; }
.model-card-name { font-size: 0.85em; font-weight: 600; color: var(--text); }
.model-card-quality { font-size: 0.72em; color: var(--text-dim); }
.model-card-cost {
  font-size: 0.78em; font-weight: 600;
  padding: 2px 8px; border-radius: 8px; display: inline-block; width: fit-content;
}
.model-card-cost.free { background: rgba(76,175,80,0.12); color: var(--success); }
.model-card-cost.paid { background: var(--gold-dim); color: var(--gold); }
.model-card-meta { font-size: 0.7em; color: var(--text-muted); line-height: 1.4; }
.model-card-status {
  font-size: 0.72em; display: flex; align-items: center; gap: 4px; margin-top: 2px;
}
.model-card-status .dot {
  width: 6px; height: 6px; border-radius: 50%; display: inline-block;
}
.dot-green { background: var(--success); }
.dot-gold { background: var(--gold); }
.dot-dim { background: var(--text-muted); }
.dot-red { background: var(--error); }
.dot-orange { background: var(--generating); animation: pulse 1.2s infinite; }
.model-card .btn { margin-top: auto; width: 100%; font-size: 0.78em; }
.model-prereqs {
  margin-top: 16px; padding: 14px; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius);
}
.model-prereqs h4 {
  font-size: 0.75em; color: var(--text-dim);
  letter-spacing: -0.01em; margin-bottom: 8px; font-weight: 600;
}
.model-prereqs-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px;
  font-size: 0.78em; color: var(--text-dim);
}
.prereq-item { display: flex; align-items: center; gap: 5px; }
.prereq-ok { color: var(--success); }
.prereq-missing { color: var(--text-muted); }
.models-btn {
  background: none; border: 1px solid var(--border); border-radius: var(--radius);
  color: var(--text-dim); font-size: 0.75em; padding: 3px 8px; cursor: pointer;
  transition: all var(--transition);
}
.models-btn:hover { border-color: var(--border-light); color: var(--text); }

/* --- Filter Strip (collapsible) --- */
.filter-strip {
  display: none; align-items: center; gap: 10px;
  padding: 6px 20px; background: var(--surface2);
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.filter-strip.open { display: flex; }
.filter-strip label { font-size: 0.78em; color: var(--text-dim); white-space: nowrap; }
.filter-strip select, .filter-strip input[type="text"] {
  padding: 5px 10px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg); color: var(--text); font-size: 0.8em;
}
.filter-strip input[type="text"] { width: 180px; }
.filter-strip input[type="text"]:focus { border-color: var(--gold); outline: none; }
.filter-toggle {
  background: none; border: 1px solid var(--border); border-radius: var(--radius);
  color: var(--text-dim); font-size: 0.8em; padding: 5px 10px; cursor: pointer;
  transition: all var(--transition); display: flex; align-items: center; gap: 4px;
}
.filter-toggle:hover { border-color: var(--border-light); color: var(--text); }
.filter-toggle.has-active-filter { border-color: var(--gold); color: var(--gold); }
.filter-toggle .filter-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--gold); display: none;
}
.filter-toggle.has-active-filter .filter-dot { display: block; }

/* --- Model Hub: API Key Inline --- */
.model-hub-apikey {
  margin-top: 10px; padding: 10px 12px;
  border: 1px dashed var(--border); border-radius: var(--radius);
  background: var(--surface);
}
.model-hub-apikey.connected {
  border-style: solid; background: transparent; padding: 6px 0; border: none;
  display: flex; align-items: center; gap: 8px;
}
.model-hub-key-row {
  display: flex; gap: 6px; align-items: center;
}
.model-hub-key-input {
  flex: 1; padding: 6px 10px; font-size: 0.82em;
  background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius);
}
.model-hub-key-input:focus {
  border-color: var(--gold-dim); outline: none;
}
.model-hub-change-key {
  font-size: 0.78em; color: var(--text-muted); cursor: pointer;
  background: none; border: none; text-decoration: underline;
  text-decoration-style: dotted; text-underline-offset: 2px;
}
.model-hub-change-key:hover { color: var(--text-dim); }
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center;gap:0;">
    <h1>Deck Art Studio</h1>
    <a href="https://ko-fi.com/drewvalentine" target="_blank" rel="noopener" title="Support this project on Ko-fi" style="display:inline-flex;align-items:center;margin-left:6px;color:#e94560;text-decoration:none;font-size:1em;">&hearts;</a>
  </div>
  <div class="header-controls">
    <div class="deck-selector">
      <select id="deckSelect" onchange="switchDeck()" title="Active deck">
        <option value="">Loading...</option>
      </select>
      <div class="deck-overflow-wrap">
        <button class="btn btn-ghost deck-overflow-btn" onclick="toggleDeckMenu()" title="Deck actions">&ctdot;</button>
        <div id="deckOverflowMenu" class="deck-overflow-menu" style="display:none;">
          <button class="deck-menu-item" onclick="closeDeckMenu();openAddCardModal();">Add Card</button>
          <button class="deck-menu-item" onclick="closeDeckMenu();addCardBack();">Add Card Back</button>
          <button class="deck-menu-item" onclick="closeDeckMenu();renameDeck();">Rename</button>
          <button class="deck-menu-item" onclick="closeDeckMenu();clearSelection();switchPanelTab('frame');">Deck Frame Style</button>
          <div class="deck-menu-sep"></div>
          <a id="deckMenuExportZip" href="/api/export-all" class="deck-menu-item" style="text-decoration:none;">Export ZIP</a>
          <button class="deck-menu-item" onclick="closeDeckMenu();exportManifest();">Export for EDH Play</button>
          <div class="deck-menu-sep"></div>
          <button class="deck-menu-item deck-menu-danger" onclick="closeDeckMenu();deleteDeck();">Delete Deck</button>
        </div>
      </div>
      <button class="btn btn-primary btn-sm" onclick="openImportModal()" title="Import a new deck">+ Import</button>
    </div>

    <div class="separator"></div>

    <button class="filter-toggle" id="filterToggle" onclick="toggleFilterStrip()">
      Filter <span class="filter-dot"></span>
    </button>
    <select id="modelSelect" onchange="changeModel()" title="Image generation model"></select>
    <button class="models-btn" onclick="openModelHub()" title="Browse all models">Models</button>
    <span id="costEstimate" class="cost-estimate" title="Estimated cost for remaining cards"></span>
    <span id="modelStatus" class="model-status" style="display:none;"></span>

  </div>
</header>

<div class="filter-strip" id="filterStrip">
  <label>Search</label>
  <input type="text" id="searchInput" placeholder="Card name..." oninput="applyFilters()">
  <label>Type</label>
  <select id="filterType" onchange="applyFilters()">
    <option value="all">All Types</option>
    <option value="creature">Creatures</option>
    <option value="artifact">Artifacts</option>
    <option value="sorcery">Sorceries</option>
    <option value="instant">Instants</option>
    <option value="enchantment">Enchantments</option>
    <option value="planeswalker">Planeswalkers</option>
    <option value="land">Lands</option>
  </select>
  <label>Status</label>
  <select id="filterStatus" onchange="applyFilters()">
    <option value="all">All Status</option>
    <option value="complete">Generated</option>
    <option value="pending">Not Generated</option>
    <option value="error">Errors</option>
    <option value="selected">Selected</option>
    <option value="pinned">Pinned</option>
    <option value="generating">Generating</option>
  </select>
  <div style="flex:1;"></div>
  <button class="btn btn-ghost btn-xs" onclick="clearFilters()">Clear</button>
</div>

<!-- Setup Bar -->
<div id="setupBar" class="hidden" style="position:relative;">
  <span id="setupBarMessage"></span>
  <div class="setup-bar-actions" id="setupBarActions"></div>
  <button class="setup-bar-dismiss" onclick="dismissSetupBar()" title="Dismiss">&times;</button>
</div>

<!-- Import Modal -->
<div id="importModal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeImportModal()">
  <div class="modal-content">
    <h2>Import Deck</h2>
    <div class="modal-body">
      <div class="detail-section">
        <label>Deck Name</label>
        <input type="text" id="importDeckName" placeholder="e.g. Coin Flip Chaos" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);">
      </div>
      <div class="detail-section">
        <label>Decklist (Archidekt / MTGO / Arena format — one card per line)</label>
        <textarea id="importText" rows="12" placeholder="1x Okaun, Eye of Chaos (bbd) 6 [Commander]
1x Zndrsplt, Eye of Wisdom (bbd) 5 [Commander]
1x Sol Ring
1x Arcane Signet
1 Command Tower
..." style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-family:monospace;font-size:0.85em;"></textarea>
      </div>
      <!-- AI prompts generated later when inspiration art is uploaded -->
      <div id="importProgressWrap" style="display:none;margin-top:12px;">
        <div style="display:flex;justify-content:space-between;font-size:0.8em;color:var(--text-dim);margin-bottom:4px;">
          <span id="importPhaseLabel">Parsing...</span>
          <span id="importStepLabel"></span>
        </div>
        <div class="progress-bar" style="display:block;">
          <div class="progress-bar-fill" id="importProgressFill" style="width:0%"></div>
        </div>
      </div>
      <div id="importStatus" style="font-size:0.85em;color:var(--text-dim);margin-top:8px;"></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px;">
      <button class="btn btn-gold" onclick="doImport()" id="btnImport">Import & Fetch from Scryfall</button>
      <button class="btn btn-secondary" onclick="closeImportModal()">Cancel</button>
    </div>
  </div>
</div>

<!-- Version Preview Modal -->
<div id="versionModal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeVersionModal()">
  <div class="modal-content" style="max-width:400px;text-align:center;padding:20px;">
    <img id="versionModalImg" src="" alt="" style="width:100%;border-radius:8px;">
    <p id="versionModalLabel" style="margin:12px 0 0;color:var(--text-dim);font-size:0.85em;"></p>
    <div id="versionModalActions" style="display:flex;gap:8px;margin-top:16px;justify-content:center;">
      <button class="btn btn-gold btn-sm" onclick="restoreFromModal()">Restore This Version</button>
      <button class="btn btn-sm" style="background:var(--danger);color:#fff;" onclick="deleteFromModal()">Delete</button>
      <button class="btn btn-secondary btn-sm" onclick="closeVersionModal()">Cancel</button>
    </div>
  </div>
</div>

<!-- Add Card Modal -->
<div id="addCardModal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeAddCardModal()">
  <div class="modal-content" style="max-width:420px;">
    <h2>Add Card to Deck</h2>
    <div class="modal-body">
      <div class="detail-section">
        <label>Card Name</label>
        <input type="text" id="addCardName" placeholder="e.g. Lightning Bolt"
               style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);"
               onkeydown="if(event.key==='Enter')doAddCard()">
      </div>
      <div class="detail-section">
        <label>Quantity</label>
        <input type="number" id="addCardQty" value="1" min="1" max="99"
               style="width:60px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);">
      </div>
      <div id="addCardStatus" style="font-size:0.85em;color:var(--text-dim);margin-top:8px;"></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px;">
      <button class="btn btn-gold" onclick="doAddCard()" id="btnAddCard">Add Card</button>
      <button class="btn btn-secondary" onclick="closeAddCardModal()">Cancel</button>
    </div>
  </div>
</div>

<!-- Persistent Action Bar -->
<div id="actionBar">
  <span class="stat-complete" id="statComplete" style="font-size:0.78em;">0 done</span>
  <span class="stat-generating" id="statGenerating" style="font-size:0.78em;"></span>
  <span class="stat-pending" id="statPending" style="font-size:0.78em;">0 pending</span>
  <div class="action-bar-divider"></div>
  <span class="action-bar-count" id="actionBarCount">0 selected</span>
  <button class="btn btn-ghost btn-xs" onclick="selectAll()">Select All</button>
  <button class="btn btn-ghost btn-xs" onclick="deselectAll()">Deselect</button>
  <button class="btn btn-ghost btn-xs" id="btnPinChecked" onclick="pinChecked()" disabled>Pin (0)</button>
  <button class="btn btn-secondary btn-sm" id="btnRegenPrompts" onclick="regeneratePrompts()" disabled
          title="Regenerate art prompts for selected cards">Prompts (0)</button>
  <button class="btn btn-secondary btn-sm" id="btnGenFlavor" onclick="generateFlavorText()" disabled
          title="Generate themed flavor text for selected cards">Flavor (0)</button>
  <button class="btn btn-gold btn-sm" id="btnGenArt" onclick="generateArt()" disabled
          title="Generate artwork for selected cards">Art (0)</button>
  <button class="btn btn-secondary btn-sm" id="btnRenderFrames" onclick="renderFrames()" disabled
          title="Re-render card frames for selected cards">Frames (0)</button>
  <div class="action-bar-spacer"></div>
  <button class="btn btn-danger btn-sm" onclick="stopBatch()" id="btnStop" style="display:none;">
    Stop
  </button>
  <div id="actionBarProgress">
    <div class="progress-bar" id="batchProgress">
      <div class="progress-bar-fill" id="batchProgressFill" style="width:0%"></div>
    </div>
    <span class="action-bar-message" id="batchMessage"></span>
  </div>
</div>

<div class="main-layout">
  <div class="card-grid-container">
    <div id="welcomeHero" style="display:none;">
      <div class="welcome-title">Deck Art Studio</div>
      <div class="welcome-subtitle">Generate custom AI art for every card in your Magic deck &mdash; fully local on your Mac, powered by MLX.</div>
      <div class="welcome-backends">
        <div class="welcome-card setup-done" id="welcomeLocal">
          <div class="welcome-card-icon">&#9889;</div>
          <div class="welcome-card-title">MLX-Native</div>
          <div class="welcome-card-desc">FLUX.1 art &middot; Llama + Qwen-VL prompts<br>Free &amp; private &middot; Apple Silicon</div>
          <div class="welcome-card-status" id="welcomeLocalStatus">&#10003; Ready</div>
          <button class="btn btn-secondary btn-sm welcome-card-action" id="welcomeLocalBtn" onclick="toggleWelcomeSetup('local')">Check setup</button>
          <div class="welcome-card-setup" id="welcomeLocalSetup" style="display:none;">
            <div class="welcome-check-list" id="welcomeLocalChecks"></div>
          </div>
        </div>
      </div>
      <div class="welcome-divider">&mdash; import a deck to begin &mdash;</div>
      <button class="btn btn-gold welcome-import-btn" onclick="openImportModal()">Import Your First Deck</button>
    </div>
    <div class="card-grid" id="cardGrid"></div>
  </div>

  <div class="detail-panel" id="detailPanel">
    <div class="panel-tabs">
      <button class="panel-tab active" id="tabStyle" onclick="switchPanelTab('style')">Inspiration</button>
      <button class="panel-tab" id="tabCard" onclick="switchPanelTab('card')">Art</button>
      <button class="panel-tab" id="tabFrame" onclick="switchPanelTab('frame')">Frame</button>
    </div>
    <div id="noSelection">
      <!-- Style Reference -->
      <div class="overview-section">
        <h3 class="overview-section-title">Style Reference</h3>
        <div id="inspirationGallery" class="inspiration-gallery"></div>
        <input type="file" id="inspirationUpload" accept="image/png,image/jpeg,image/webp"
               multiple style="display:none;" onchange="uploadInspiration(this)">
        <p id="styleDescPreview" class="style-desc-full"></p>
        <div id="styleProgressWrap" class="style-progress-wrap" style="display:none;">
          <div class="style-progress-header">
            <span id="styleProgressText" class="style-progress-label"></span>
            <span id="styleProgressStep" class="style-progress-step"></span>
          </div>
          <div class="progress-bar style-progress-bar">
            <div class="progress-bar-fill" id="styleProgressFill" style="width:0%"></div>
          </div>
        </div>
        <div class="style-source-row">
          <label for="styleSourceInput" class="style-source-label">Style source</label>
          <input type="text" id="styleSourceInput" class="style-source-input"
                 placeholder='e.g. "Studio Ghibli", "Borderlands"'
                 onchange="saveStyleSource(this.value)">
        </div>
        <div class="overview-btn-row">
          <button class="btn btn-secondary btn-sm" id="btnReanalyzeStyle" onclick="reanalyzeStyle()"
                  title="Re-analyze inspiration image style">Re-analyze Style</button>
        </div>
      </div>

      <hr class="overview-divider">

    </div>

    <div id="cardDetail" style="display:none;">
      <div class="detail-panel-inner">
        <!-- Zone 1: Card Identity -->
        <h2 id="detailName"></h2>
        <div class="detail-subtitle">
          <span id="detailTypeLine" class="detail-type-line"></span>
          <span id="detailManaCost" class="detail-mana-cost"></span>
        </div>

        <!-- Face toggle — only shown for double-faced cards -->
        <div class="face-toggle" id="faceToggle" style="display:none;">
          <button class="face-toggle-btn active" id="faceBtnFront" onclick="setFace('front')">Front</button>
          <button class="face-toggle-btn" id="faceBtnBack" onclick="setFace('back')">Back</button>
        </div>
        <!-- Hint for single-faced multi-part cards (adventure / room / split) -->
        <div class="face-hint" id="faceHint" style="display:none;"></div>

        <!-- Zone 2: Hero Image with overlays -->
        <div class="detail-hero" id="detailHero">
          <img id="detailImage" class="detail-card-preview" src="" alt="">
          <div class="detail-status-pip pip-pending" id="detailStatusPip"></div>
          <div class="detail-hero-progress" id="detailHeroProgress">
            <div class="hero-progress-bar">
              <div class="hero-progress-fill" id="detailHeroProgressFill"></div>
            </div>
            <span class="hero-progress-label" id="detailHeroProgressLabel"></span>
          </div>
        </div>

        <div id="staleArtBanner" style="display:none;padding:6px 10px;background:rgba(240,192,64,0.12);border:1px solid rgba(240,192,64,0.3);border-radius:6px;font-size:0.75em;color:var(--gold);margin-bottom:6px;">
          Prompt changed since this art was generated — click Render Art to apply it
        </div>

        <!-- Zone 3: Smart Action Area (populated by JS state machine) -->
        <div class="detail-action-area" id="detailActionArea"></div>

        <!-- Zone 4: Editable fields -->
        <div class="detail-section-block" id="collapsiblePrompt">
          <div class="detail-section-header">
            <span class="detail-section-label">Prompt <span class="section-hint" title="The scene this card depicts — it drives the NEXT generation. The deck's style is applied automatically on top, so describe only WHAT to depict. Edit it directly, or use Generate Random / Steer & Render to change it.">(?)</span></span>
            <button class="btn btn-ghost btn-xs" id="btnRegenPromptSingle" onclick="regeneratePromptForCard()" title="Write a brand-new random scene prompt (no steer). Doesn't render — edit it, then Render Art.">Generate Random</button>
          </div>
          <textarea id="detailPrompt" rows="6" placeholder="e.g. a faerie soaring above a moonlit forest"></textarea>
        </div>

        <div class="detail-section-block" id="collapsibleFlavor">
          <div class="detail-section-header">
            <span class="detail-section-label">Flavor text</span>
            <button class="btn btn-ghost btn-xs" id="btnGenFlavorSingle" onclick="generateFlavorTextForCard()" title="Generate themed flavor text">Generate</button>
          </div>
          <textarea id="detailFlavorEdit" rows="2" style="font-style:italic;" placeholder="No flavor text — type here or click Generate"></textarea>
        </div>

        <div class="detail-section-block" id="collapsibleVersions">
          <div class="detail-section-header">
            <span class="detail-section-label">Versions</span>
            <span class="detail-section-count" id="versionsPreview"></span>
          </div>
          <div id="versionHistory" class="version-history">
            <div id="versionStrip" class="version-strip"></div>
          </div>
        </div>

        <div id="detailRevised" style="display:none;">
          <div class="detail-section-block">
            <div class="detail-section-header">
              <span class="detail-section-label">Revised Prompt</span>
            </div>
            <p id="detailRevisedText" style="font-size:0.78em;color:var(--text-dim);line-height:1.4;margin:0;"></p>
          </div>
        </div>

        <!-- Zone 5: Footer -->
        <div class="detail-footer">
          <button class="btn btn-ghost btn-sm" onclick="recompositeCurrent()" style="flex:1;">
            Re-render Frame
          </button>
          <button class="btn btn-ghost btn-sm btn-remove" onclick="removeCurrentCard()" title="Remove card from deck">
            Remove
          </button>
        </div>
      </div>
    </div>
    <div id="cardTabEmpty" style="display:none;padding:40px 20px;text-align:center;">
      <div style="font-size:0.9em;color:var(--text-muted);margin-bottom:8px;">No card selected</div>
      <div style="font-size:0.78em;color:var(--text-muted);">Click a card in the grid to see details.</div>
    </div>

    <!-- WYSIWYG Frame Designer Tab -->
    <div id="frameDesigner" style="display:none;">
      <div class="detail-panel-inner fd-panel">
        <!-- Empty state -->
        <div id="fdEmpty" class="fd-empty">
          <div class="fd-empty-icon">&#127912;</div>
          <div class="fd-empty-text">Select a card to edit its frame</div>
          <div class="fd-empty-hint">Click any card in the grid, then switch to Frame</div>
        </div>

        <!-- Canvas preview -->
        <div id="fdCanvasWrap" style="display:none;">
          <div class="fd-card-name" id="fdCardName"></div>
          <div class="face-toggle" id="fdFaceToggle" style="display:none;">
            <button class="face-toggle-btn active" id="fdFaceBtnFront" onclick="setFace('front')">Front</button>
            <button class="face-toggle-btn" id="fdFaceBtnBack" onclick="setFace('back')">Back</button>
          </div>
          <div class="face-hint" id="fdFaceHint" style="display:none;"></div>
          <div class="fd-canvas-container" id="fdCanvasContainer">
            <canvas id="fdCanvas" width="750" height="1050"></canvas>
            <div class="fd-canvas-loading" id="fdCanvasLoading">
              <div class="fd-spinner"></div>
            </div>
          </div>

          <!-- Zoom controls -->
          <div class="fd-zoom-bar" id="fdZoomBar">
            <button class="fd-zoom-btn" id="fdZoomFit" title="Fit art to card">Fit</button>
            <button class="fd-zoom-btn" id="fdZoomOut" title="Zoom out">-</button>
            <input type="range" class="fd-zoom-slider" id="fdZoomSlider" min="30" max="300" value="100" step="1">
            <button class="fd-zoom-btn" id="fdZoomIn" title="Zoom in">+</button>
            <button class="fd-zoom-btn" id="fdZoomReset" title="Reset position">&#8634;</button>
            <span id="fdArtSize" style="display:none; font-size:10px; color:var(--text-tertiary); margin-left:4px; white-space:nowrap;"></span>
          </div>

          <!-- Style strip -->
          <div class="fd-section">
            <div class="fd-section-label">Deck Frame Style</div>
            <div class="fd-deck-style-hint" id="fdDeckStyleHint"></div>
            <div class="fd-style-strip" id="fdStyleStrip"></div>
          </div>

          <!-- Layers (SVG styles only) -->
          <div class="fd-section fd-collapsible" id="fdLayerSection" style="display:none;">
            <div class="fd-section-header" onclick="toggleFdSection('fdLayerContent')">
              <span class="fd-section-label">Layers</span>
              <span class="fd-section-arrow" id="fdLayerContentArrow">&#9656;</span>
            </div>
            <div class="fd-section-body" id="fdLayerContent" style="display:none;">
              <div class="fd-intensity-row">
                <span class="fd-intensity-label">Intensity</span>
                <input type="range" class="fd-intensity-slider" id="fdIntensity" min="0" max="100" value="100" step="1">
                <span class="fd-intensity-val" id="fdIntensityVal">100%</span>
              </div>
              <div id="fdLayerList"></div>
            </div>
          </div>

          <!-- Colors -->
          <div class="fd-section fd-collapsible" id="fdColorSection">
            <div class="fd-section-header" onclick="toggleFdSection('fdColorContent')">
              <span class="fd-section-label">Colors</span>
              <span class="fd-section-arrow" id="fdColorContentArrow">&#9656;</span>
            </div>
            <div class="fd-section-body" id="fdColorContent" style="display:none;">
              <label class="frame-auto-toggle">
                <input type="checkbox" id="frameAutoColors" checked onchange="toggleFrameAutoColors()">
                <span>Auto (from card colors)</span>
              </label>
              <!-- Two-color (multi-type land / gold) frame mode -->
              <div class="fd-gradient-row" id="fdGradientRow">
                <span class="fd-gradient-label">Two-color frame</span>
                <div class="fd-seg" id="fdGradientSeg">
                  <button type="button" data-grad="auto" class="fd-seg-btn active" onclick="setFrameGradient('auto')" title="Smooth gradient for 2-color cards (default)">Auto</button>
                  <button type="button" data-grad="gradient" class="fd-seg-btn" onclick="setFrameGradient('gradient')" title="Force a smooth left→right color blend">Blend</button>
                  <button type="button" data-grad="split" class="fd-seg-btn" onclick="setFrameGradient('split')" title="Hard left/right color split down the middle">Split</button>
                  <button type="button" data-grad="off" class="fd-seg-btn" onclick="setFrameGradient('off')" title="Flat gold multicolor frame (no gradient)">Gold</button>
                </div>
              </div>
              <div id="frameQuickSwatches" class="frame-quick-swatches" style="display:none;"></div>
              <div id="frameColorInputs" class="frame-color-inputs" style="display:none;">
                <div class="frame-color-row" id="frameColorRowBg">
                  <span class="frame-color-label">Frame</span>
                  <input type="color" class="frame-color-picker" id="frameColorBg" value="#3B90B9">
                  <input type="text" class="frame-color-hex" id="frameColorBgHex" value="#3B90B9" maxlength="7">
                </div>
                <div class="frame-color-row" id="frameColorRowField">
                  <span class="frame-color-label">Fields</span>
                  <input type="color" class="frame-color-picker" id="frameColorField" value="#A9CCE5">
                  <input type="text" class="frame-color-hex" id="frameColorFieldHex" value="#A9CCE5" maxlength="7">
                </div>
                <div class="frame-color-row" id="frameColorRowTextbox">
                  <span class="frame-color-label">Textbox</span>
                  <input type="color" class="frame-color-picker" id="frameColorTextbox" value="#D2E4F4">
                  <input type="text" class="frame-color-hex" id="frameColorTextboxHex" value="#D2E4F4" maxlength="7">
                </div>
                <div class="frame-color-row" id="frameColorRowBorder">
                  <span class="frame-color-label">Border</span>
                  <input type="color" class="frame-color-picker" id="frameColorBorder" value="#1971CE">
                  <input type="text" class="frame-color-hex" id="frameColorBorderHex" value="#1971CE" maxlength="7">
                </div>
                <div class="frame-color-row" id="frameColorRowText">
                  <span class="frame-color-label">Text</span>
                  <input type="color" class="frame-color-picker" id="frameColorText" value="#000000">
                  <input type="text" class="frame-color-hex" id="frameColorTextHex" value="#000000" maxlength="7">
                </div>
              </div>
            </div>
          </div>

          <!-- Text Overrides -->
          <div class="fd-section fd-collapsible" id="fdTextSection">
            <div class="fd-section-header" onclick="toggleFdSection('fdTextContent')">
              <span class="fd-section-label">Text Overrides</span>
              <span class="fd-section-arrow" id="fdTextContentArrow">&#9656;</span>
            </div>
            <div class="fd-section-body" id="fdTextContent" style="display:none;">
              <div class="frame-text-row" id="frameShowcaseRow" style="display:none;">
                <label class="frame-text-label">Showcase Name</label>
                <input type="text" class="frame-text-input" id="frameOverrideShowcase" placeholder="e.g. GODZILLA, KING OF THE MONSTERS" title="Big title for the Godzilla/showcase frame; the card's real name renders small beneath it.">
              </div>
              <div class="frame-text-row" id="frameBottomMaskRow" style="display:none;">
                <label class="frame-text-label">Bottom Mask</label>
                <label class="frame-auto-toggle" title="The rounded black mask across the card bottom. Off = art runs to the bottom edge.">
                  <input type="checkbox" id="frameBottomMask" checked onchange="scheduleFramePreview()">
                  <span>Show rounded bottom</span>
                </label>
              </div>
              <div class="frame-text-row" id="frameBoxOpacityRow" style="display:none;">
                <label class="frame-text-label">Box Transparency</label>
                <input type="range" class="frame-layer-slider" id="frameBoxOpacity" min="30" max="100" step="1" value="93"
                       oninput="scheduleFramePreview()" title="Rules-box opacity — lower lets more art show through.">
              </div>
              <div class="frame-text-row" id="frameRulesSizeRow" style="display:none;">
                <label class="frame-text-label">Rules Text Size</label>
                <input type="range" class="frame-layer-slider" id="frameRulesSize" min="16" max="60" step="1" value="30"
                       oninput="document.getElementById('frameRulesSizeVal').textContent=this.value+'pt'; scheduleFramePreview()" title="Rules text point size (long oracles still shrink to fit).">
                <span id="frameRulesSizeVal" class="frame-layer-val" style="width:auto;">30pt</span>
              </div>
              <div class="frame-text-row">
                <label class="frame-text-label">Card Name</label>
                <input type="text" class="frame-text-input" id="frameOverrideName" placeholder="">
              </div>
              <div class="frame-text-row">
                <label class="frame-text-label">Mana Cost</label>
                <input type="text" class="frame-text-input" id="frameOverrideMana" placeholder="" title="e.g. {3}{G}{G}{U}{U}">
              </div>
              <div class="frame-text-row">
                <label class="frame-text-label">Type Line</label>
                <input type="text" class="frame-text-input" id="frameOverrideType" placeholder="">
              </div>
              <div class="frame-text-row">
                <label class="frame-text-label">Rules Text</label>
                <textarea class="frame-text-input" id="frameOverrideOracle" rows="6" placeholder=""></textarea>
              </div>
              <div class="frame-text-row frame-pt-row">
                <label class="frame-text-label">P/T</label>
                <input type="text" class="frame-text-input frame-pt-input" id="frameOverridePower" placeholder="">
                <span class="frame-pt-slash">/</span>
                <input type="text" class="frame-text-input frame-pt-input" id="frameOverrideToughness" placeholder="">
              </div>
              <button class="btn btn-ghost btn-xs" onclick="resetTextOverrides()">Reset to Scryfall</button>
            </div>
          </div>

          <!-- Actions (sticky bottom) -->
          <div class="fd-actions">
            <button class="btn btn-gold btn-sm" id="frameSaveBtn" onclick="saveFrameSettings()" style="flex:1;" title="Save this card's frame (style, colors, art position) as a per-card override — the deck default is not changed">
              Save Frame
            </button>
            <button class="btn btn-secondary btn-sm" id="frameApplyAllBtn" onclick="applyFrameToChecked()" title="Save these frame settings onto every checked card (per-card overrides)">
              Apply to Checked
            </button>
            <button class="btn btn-gold btn-sm" id="frameDeckDefaultBtn" onclick="setDeckDefaultFrame()" style="flex:1; display:none;" title="Save these settings as the deck default — used by new imports and any card without its own saved frame">
              Save Deck Default
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// Extract the plain scene from a prompt that may still be in the legacy cloud
// bundled format ("{style_tag}.\n\n{scene}\n\n---\n\n{prose}"). New prompts are
// already plain scenes, so this is a no-op for them. Keeps the single Prompt
// field clean for decks generated before the prompt pipeline was collapsed.
function cleanScenePrompt(p) {
  if (!p) return '';
  let body = p.split('\n\n---\n\n')[0];          // drop cloud-only prose
  const fb = body.indexOf('Additional direction:');
  if (fb >= 0) body = body.slice(0, fb).trim();
  const secs = body.split('.\n\n');
  // If the head looks like a style tag (Source:/Art Style:), keep the scene part.
  if (secs.length > 1 && /^(Source:|Art Style:)/i.test(secs[0].trim())) {
    return secs.slice(1).join('.\n\n').trim();
  }
  return body.trim();
}

let allCards = [];
let selectedCard = null;
let selectedFace = 'front';  // 'front' | 'back' — which face of a DFC the detail panel shows
let lastSelectedStatus = null;  // tracks status transitions for selected card
let checkedCards = new Set();
let pinnedCards = new Set();
let pollInterval = null;
let modelConfig = null;  // cached model config from server
let currentMode = 'cloud';  // 'cloud' or 'local'
let deckCacheBust = Date.now();  // changes on deck switch to bust browser image cache
let ollamaBusy = false;  // true when Ollama analysis/distillation is running
let isGeneratingBatch = false;  // true when batch generation is running
let activePanelTab = 'style';  // 'style' or 'card'

// ═══════════════════════════════════════════════════════════════
//  Custom Dialog System
// ═══════════════════════════════════════════════════════════════
function showCustomDialog(opts = {}) {
  return new Promise((resolve) => {
    const variant = opts.variant || 'default';
    const confirmText = opts.confirmText || 'Confirm';
    const cancelText = opts.cancelText || 'Cancel';
    const confirmClass = opts.confirmClass || (variant === 'danger' ? 'btn-danger' : 'btn-gold');
    const fields = opts.fields || [];

    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';

    let fieldsHtml = '';
    fields.forEach((f, i) => {
      if (f.type === 'textarea') {
        fieldsHtml += `<div class="dialog-field">
          ${f.label ? `<label>${escapeHtml(f.label)}</label>` : ''}
          <textarea data-field="${escapeHtml(f.name)}" rows="${f.rows || 3}"
            placeholder="${escapeHtml(f.placeholder || '')}">${escapeHtml(f.value || '')}</textarea>
        </div>`;
      } else if (f.type === 'text') {
        fieldsHtml += `<div class="dialog-field">
          ${f.label ? `<label>${escapeHtml(f.label)}</label>` : ''}
          <input type="text" data-field="${escapeHtml(f.name)}"
            value="${escapeHtml(f.value || '')}"
            placeholder="${escapeHtml(f.placeholder || '')}">
        </div>`;
      } else if (f.type === 'toggle') {
        const checked = f.checked !== false ? 'checked' : '';
        fieldsHtml += `<div class="dialog-field">
          <label class="dialog-toggle">
            <input type="checkbox" data-field="${escapeHtml(f.name)}" ${checked}>
            <span class="toggle-track"></span>
            <span>
              <span class="toggle-label-text">${escapeHtml(f.label || '')}</span>
              ${f.description ? `<br><span class="toggle-description">${escapeHtml(f.description)}</span>` : ''}
            </span>
          </label>
        </div>`;
      } else if (f.type === 'checkbox') {
        const checked = f.checked ? 'checked' : '';
        fieldsHtml += `<div class="dialog-field">
          <label class="dialog-checkbox">
            <input type="checkbox" data-field="${escapeHtml(f.name)}" ${checked}>
            <span>${escapeHtml(f.label || '')}</span>
          </label>
        </div>`;
      }
    });

    const costHtml = opts.cost ? `<div class="dialog-cost">${escapeHtml(opts.cost)}</div>` : '';

    overlay.innerHTML = `<div class="dialog-content ${variant === 'danger' ? 'dialog-danger' : ''}">
      <h2>${escapeHtml(opts.title || '')}</h2>
      ${opts.message ? `<p class="dialog-message">${escapeHtml(opts.message)}</p>` : ''}
      ${fieldsHtml}
      ${costHtml}
      <div class="dialog-actions">
        <button class="btn btn-secondary" data-dialog-cancel>${escapeHtml(cancelText)}</button>
        <button class="btn ${confirmClass}" data-dialog-confirm>${escapeHtml(confirmText)}</button>
      </div>
    </div>`;

    function getValues() {
      const result = {};
      overlay.querySelectorAll('[data-field]').forEach(el => {
        const name = el.getAttribute('data-field');
        if (el.type === 'checkbox') result[name] = el.checked;
        else result[name] = el.value;
      });
      return result;
    }

    function close(result) {
      overlay.style.opacity = '0';
      overlay.style.transition = 'opacity 0.15s ease';
      setTimeout(() => overlay.remove(), 150);
      resolve(result);
    }

    overlay.querySelector('[data-dialog-cancel]').addEventListener('click', () => close(null));
    overlay.querySelector('[data-dialog-confirm]').addEventListener('click', () => close(getValues()));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });
    overlay.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(null); }
      if (e.key === 'Enter' && !e.target.matches('textarea')) { e.preventDefault(); close(getValues()); }
    });

    document.body.appendChild(overlay);

    // Autofocus first field or confirm button
    const firstInput = overlay.querySelector('textarea, input[type="text"]');
    if (firstInput) {
      firstInput.focus();
      if (firstInput.tagName === 'INPUT') firstInput.select();
    } else {
      overlay.querySelector('[data-dialog-confirm]').focus();
    }
  });
}

// --- Init ---
async function init() {
  // Load decks first
  await loadDecks();

  const cardsResp = await fetch('/api/cards');
  allCards = await cardsResp.json();

  // Load mode handles: backend state, model config, dropdown, ref toggle, cost
  await loadMode();

  renderGrid();  // also calls syncPinnedCards()
  updateFilterIndicator();  // restore indicator if browser autofilled filters
  startPolling();
  loadDeckSettings();
  updateSetupBar();
  initFrameDesigner();

  // ── Auto-save prompt edits on blur ──
  document.getElementById('detailPrompt').addEventListener('blur', async (e) => {
    if (!selectedCard) return;
    const newPrompt = e.target.value.trim();
    if (!newPrompt) return;
    // Only save if the prompt actually differs from what the server has.
    // On a DFC's back face the prompt saves under "<name> [back]".
    const card = allCards.find(c => c.name === selectedCard);
    const isBack = viewingBack(card);
    const current = card ? (isBack ? (card.back_prompt || '') : card.prompt) : '';
    if (card && current === newPrompt) return;
    try {
      const resp = await fetch('/api/save-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_name: faceKeyFor(card) || selectedCard, prompt: newPrompt }),
      });
      const data = await resp.json();
      if (data.success) {
        // Update local state so polling doesn't overwrite
        if (card) {
          if (isBack) {
            card.back_prompt = newPrompt;
          } else {
            card.prompt = newPrompt;
            if (card.has_ai_art) card.prompt_stale = true;
          }
          updateDetailPanel(card);
        }
        showToast('Prompt saved', 'success');
      }
    } catch (err) {
      console.error('Failed to save prompt:', err);
    }
  });

  // (The separate "scene direction" field was merged into the single Prompt
  // field above; its art_prompts.json blur-save handler lives just above.)

  // ── Auto-save flavor text on blur ──
  document.getElementById('detailFlavorEdit').addEventListener('blur', async (e) => {
    if (!selectedCard) return;
    const newFlavor = e.target.value.trim();
    const card = allCards.find(c => c.name === selectedCard);
    if (card && (card.flavor_text || '') === newFlavor) return;
    try {
      const deckId = document.getElementById('deckSelect').value;
      const resp = await fetch('/api/cards/flavor-text', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ card_name: selectedCard, flavor_text: newFlavor }),
      });
      const data = await resp.json();
      if (data.success) {
        if (card) card.flavor_text = newFlavor;
        showToast('Flavor text saved', 'success');
      }
    } catch (err) {
      console.error('Failed to save flavor text:', err);
    }
  });

  // ── Click-away to deselect ──
  document.querySelector('.card-grid-container').addEventListener('click', (e) => {
    // Only deselect if clicking the grid background, not a card tile
    if (e.target.closest('.card-tile')) return;
    if (selectedCard) clearSelection();
  });

  // ── Keyboard navigation ──
  document.addEventListener('keydown', (e) => {
    // Don't capture when typing in inputs/textareas
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    if (e.key === 'Escape') {
      if (selectedCard) { clearSelection(); e.preventDefault(); }
      return;
    }

    const filtered = getFilteredCards();
    if (!filtered.length) return;

    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      const idx = selectedCard ? filtered.findIndex(c => c.name === selectedCard) : -1;
      const next = filtered[Math.min(idx + 1, filtered.length - 1)];
      if (next) selectCard(next.name);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      const idx = selectedCard ? filtered.findIndex(c => c.name === selectedCard) : filtered.length;
      const prev = filtered[Math.max(idx - 1, 0)];
      if (prev) selectCard(prev.name);
    }
  });
}

// --- Deck Management ---
async function loadDecks() {
  try {
    const resp = await fetch('/api/decks');
    const data = await resp.json();
    const select = document.getElementById('deckSelect');
    select.innerHTML = '';
    if (data.decks.length === 0) {
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'No decks — import one';
      placeholder.disabled = true;
      placeholder.selected = true;
      select.appendChild(placeholder);
    }
    for (const d of data.decks) {
      const opt = document.createElement('option');
      opt.value = d.id;
      opt.textContent = `${d.name} (${d.complete_count}/${d.card_count})`;
      if (d.is_active) opt.selected = true;
      select.appendChild(opt);
    }
    // Update export link in deck menu
    if (data.active_deck_id) {
      const el = document.getElementById('deckMenuExportZip');
      if (el) el.href = `/api/decks/${data.active_deck_id}/export`;
    }
  } catch (e) {
    console.error('Failed to load decks:', e);
  }
}

async function switchDeck() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  const resp = await fetch(`/api/decks/${deckId}/activate`, { method: 'POST' });
  const data = await resp.json();
  if (data.success) {
    // Bust browser image cache for the new deck
    deckCacheBust = Date.now();
    // Reload everything
    const [cardsResp, configResp] = await Promise.all([
      fetch('/api/cards'),
      fetch('/api/model-config'),
    ]);
    allCards = await cardsResp.json();
    modelConfig = await configResp.json();
    populateModelDropdown();
    updateCostEstimate();
    selectedCard = null;
    checkedCards.clear();

    switchPanelTab('style');
    renderGrid();
    loadDecks();
    loadDeckSettings();
    initFrameDesigner();
    // Update export link in deck menu
    const exportLink = document.getElementById('deckMenuExportZip');
    if (exportLink) exportLink.href = `/api/decks/${deckId}/export`;
  }
}

async function renameDeck() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  const select = document.getElementById('deckSelect');
  const currentName = select.options[select.selectedIndex]?.textContent?.replace(/ \(\d+\/\d+\)$/, '') || '';
  const dialogResult = await showCustomDialog({
    title: 'Rename Deck',
    fields: [
      { type: 'text', name: 'deckName', label: 'Deck name', value: currentName },
    ],
    confirmText: 'Rename',
  });
  if (!dialogResult) return;
  const newName = dialogResult.deckName;
  if (!newName || newName.trim() === '' || newName.trim() === currentName) return;

  try {
    const resp = await fetch(`/api/decks/${deckId}/rename`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: newName.trim() }),
    });
    const result = await resp.json();
    if (result.success) {
      loadDecks();
      loadDeckSettings();
    } else {
      showToast(result.error || 'Rename failed', 'error');
    }
  } catch (e) {
    showToast('Rename error: ' + e.message, 'error');
  }
}

async function exportManifest() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) { showToast('No deck selected', 'warning'); return; }
  try {
    const resp = await fetch(`/api/decks/${deckId}/export-manifest`);
    if (!resp.ok) { showToast('Export failed: ' + (await resp.json()).error, 'error'); return; }
    const manifest = await resp.json();
    const cardCount = Object.keys(manifest.cards).length;
    const blob = new Blob([JSON.stringify(manifest, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${deckId}-edhplay-manifest.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Exported ${cardCount} cards for EDH Play extension`, 'success');
  } catch(e) {
    showToast('Export failed: ' + e.message, 'error');
  }
}

async function deleteDeck() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  const select = document.getElementById('deckSelect');
  const deckName = select.options[select.selectedIndex]?.textContent || deckId;

  const dialogResult = await showCustomDialog({
    title: `Delete "${deckName}"`,
    message: 'This will permanently delete all generated art. This cannot be undone.',
    variant: 'danger',
    fields: [
      { type: 'checkbox', name: 'exportFirst', label: 'Export ZIP backup before deleting', checked: true },
    ],
    confirmText: 'Delete Deck',
    confirmClass: 'btn-danger',
  });
  if (!dialogResult) return;

  if (dialogResult.exportFirst) {
    window.location.href = `/api/decks/${deckId}/export`;
    await new Promise(r => setTimeout(r, 1500));
  }

  try {
    const resp = await fetch(`/api/decks/${deckId}`, { method: 'DELETE' });
    const result = await resp.json();
    if (result.success) {
      // Reload deck list and switch to whatever deck is now active
      await loadDecks();
      const newSelect = document.getElementById('deckSelect');
      if (newSelect.value) {
        await switchDeck();
      } else {
        // No decks left
        allCards = [];
        renderGrid();
      }
    } else {
      showToast(result.error || 'Delete failed', 'error');
    }
  } catch (e) {
    showToast('Delete error: ' + e.message, 'error');
  }
}

function openImportModal() {
  document.getElementById('importModal').style.display = '';
  document.getElementById('importDeckName').value = '';
  document.getElementById('importText').value = '';
  document.getElementById('importStatus').textContent = '';
}

function closeImportModal() {
  document.getElementById('importModal').style.display = 'none';
}

function openAddCardModal() {
  document.getElementById('addCardModal').style.display = '';
  document.getElementById('addCardName').value = '';
  document.getElementById('addCardQty').value = '1';
  document.getElementById('addCardStatus').textContent = '';
  setTimeout(() => document.getElementById('addCardName').focus(), 100);
}

function closeAddCardModal() {
  document.getElementById('addCardModal').style.display = 'none';
}

async function doAddCard() {
  const name = document.getElementById('addCardName').value.trim();
  const qty = parseInt(document.getElementById('addCardQty').value) || 1;
  const statusEl = document.getElementById('addCardStatus');
  const btn = document.getElementById('btnAddCard');

  if (!name) { statusEl.textContent = 'Please enter a card name.'; return; }

  btn.disabled = true;
  statusEl.textContent = 'Looking up on Scryfall...';
  statusEl.style.color = 'var(--text-dim)';

  try {
    const resp = await fetch('/api/cards/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, quantity: qty }),
    });
    const result = await resp.json();
    if (result.success) {
      statusEl.style.color = 'var(--success)';
      if (result.action === 'added') {
        statusEl.textContent = `Added "${result.name}" (${result.type_line})`;
      } else {
        statusEl.textContent = `Updated "${result.name}" quantity to ${result.quantity}`;
      }
      // Reload card grid
      const cardsResp = await fetch('/api/cards');
      allCards = await cardsResp.json();
      renderGrid();
      loadDecks();
      // Clear for next add
      document.getElementById('addCardName').value = '';
      document.getElementById('addCardName').focus();
    } else {
      statusEl.style.color = 'var(--error)';
      statusEl.textContent = result.error || 'Failed to add card';
    }
  } catch (e) {
    statusEl.style.color = 'var(--error)';
    statusEl.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function addCardBack() {
  try {
    const resp = await fetch('/api/cards/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: 'Card Back', quantity: 1 }),
    });
    const result = await resp.json();
    if (result.success) {
      const cardsResp = await fetch('/api/cards');
      allCards = await cardsResp.json();
      renderGrid();
      loadDecks();
      // Select the new card back
      selectCard(result.name);
    } else {
      showToast(result.error || 'Failed to add card back', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function removeCurrentCard() {
  if (!selectedCard) return;

  const dialogResult = await showCustomDialog({
    title: `Remove "${escapeHtml(selectedCard)}"`,
    message: 'This will also delete any generated art for this card.',
    variant: 'danger',
    confirmText: 'Remove Card',
    confirmClass: 'btn-danger',
  });
  if (!dialogResult) return;

  try {
    const resp = await fetch('/api/cards/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: selectedCard }),
    });
    const result = await resp.json();
    if (result.success) {
      showToast('Card removed', 'success');
      selectedCard = null;
  
      switchPanelTab('style');
      // Reload
      const cardsResp = await fetch('/api/cards');
      allCards = await cardsResp.json();
      renderGrid();
      loadDecks();
    } else {
      showToast(result.error || 'Remove failed', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function doImport() {
  const name = document.getElementById('importDeckName').value.trim();
  const text = document.getElementById('importText').value.trim();
  const useAI = false;  // AI prompts generated later with inspiration art
  const statusEl = document.getElementById('importStatus');
  const btn = document.getElementById('btnImport');
  const progressWrap = document.getElementById('importProgressWrap');
  const phaseLabel = document.getElementById('importPhaseLabel');
  const stepLabel = document.getElementById('importStepLabel');
  const progressFill = document.getElementById('importProgressFill');

  if (!name) { statusEl.textContent = 'Please enter a deck name.'; return; }
  if (!text) { statusEl.textContent = 'Please paste a decklist.'; return; }

  btn.disabled = true;
  statusEl.textContent = '';
  progressWrap.style.display = 'block';
  phaseLabel.textContent = 'Starting import...';
  stepLabel.textContent = '';
  progressFill.style.width = '0%';

  try {
    // Kick off background import job
    const createResp = await fetch('/api/import/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, text, use_ai_prompts: useAI}),
    });
    const createData = await createResp.json();
    if (!createData.success) {
      statusEl.textContent = `Import failed: ${createData.error}`;
      progressWrap.style.display = 'none';
      btn.disabled = false;
      return;
    }

    const jobId = createData.job_id;

    // Phase labels for nice display
    const phaseNames = {
      parsing: 'Parsing decklist...',
      scryfall: 'Fetching cards from Scryfall...',
      saving: 'Creating deck...',
      art: 'Downloading card art...',
      done: 'Complete!',
    };

    // Poll for progress
    const poll = () => new Promise((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const resp = await fetch(`/api/import/progress/${jobId}`);
          if (!resp.ok) { return; }
          const prog = await resp.json();

          // Update phase label
          phaseLabel.textContent = phaseNames[prog.phase] || prog.phase;

          // Update step counter
          if (prog.total > 0) {
            stepLabel.textContent = `${prog.step}/${prog.total}`;
            const pct = Math.round((prog.step / prog.total) * 100);
            progressFill.style.width = pct + '%';
          } else {
            stepLabel.textContent = '';
            // Indeterminate - pulse
            progressFill.style.width = '30%';
          }

          // Update status message
          statusEl.textContent = prog.message || '';

          if (prog.done) {
            clearInterval(interval);
            if (prog.error) {
              reject(new Error(prog.error));
            } else {
              resolve(prog);
            }
          }
        } catch (e) {
          // Network blip — keep polling
        }
      }, 400);
    });

    const result = await poll();

    // Success — show final status
    progressFill.style.width = '100%';
    phaseLabel.textContent = 'Complete!';
    stepLabel.textContent = '';
    statusEl.textContent =
      `Imported ${result.cards_imported} cards!` +
      (result.errors && result.errors.length ? ` (${result.errors.length} errors)` : '');

    // Reload everything with new deck
    deckCacheBust = Date.now();
    const [cardsResp, configResp] = await Promise.all([
      fetch('/api/cards'),
      fetch('/api/model-config'),
    ]);
    allCards = await cardsResp.json();
    modelConfig = await configResp.json();
    populateModelDropdown();
    updateCostEstimate();
    selectedCard = null;
    checkedCards.clear();

    switchPanelTab('style');
    renderGrid();
    await loadDecks();
    loadDeckSettings();

    // Close modal after short delay
    setTimeout(() => {
      closeImportModal();
      progressWrap.style.display = 'none';
      progressFill.style.width = '0%';
    }, 1500);

  } catch (e) {
    statusEl.textContent = `Error: ${e.message}`;
    progressFill.style.width = '0%';
    progressFill.style.background = 'var(--error)';
    phaseLabel.textContent = 'Failed';
    stepLabel.textContent = '';
  }
  btn.disabled = false;
}

// --- Model selection ---
let _pendingModelLoad = null;  // {key, localModelKey} — set when async model load is in progress

async function changeModel() {
  const key = document.getElementById('modelSelect').value;
  if (!key) return;  // "None" placeholder selected
  const isLocal = key.startsWith('local-');

  // Auto-switch mode based on model selection
  const newMode = isLocal ? 'local' : 'cloud';
  const prevMode = currentMode;
  if (newMode !== currentMode) {
    await setMode(newMode);
  }

  // Check local model prerequisites
  if (isLocal) {
    const statusResp = await fetch('/api/local-image-status');
    const status = await statusResp.json();

    if (!status.dependencies_installed) {
      showToast('Local AI requires: pip install torch diffusers transformers accelerate peft', 'warning');
      document.getElementById('modelSelect').value = modelConfig.active;
      if (prevMode !== currentMode) await setMode(prevMode, true);
      return;
    }

    const modelKey = modelConfig.options[key]?._local_model || key.replace('local-', '');
    if (!status.is_loaded || status.active_model !== modelKey) {
      // Fire async load — polling will handle progress and completion
      showToast('Loading model...', 'info', { persistent: true, id: 'model-load', indeterminate: true });
      const loadResp = await fetch('/api/local-image-load', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({model: modelKey}),
      });
      const loadData = await loadResp.json();
      if (loadData.error) {
        updateToast('model-load', 'Failed to load model: ' + loadData.error, 'error');
        document.getElementById('modelSelect').value = modelConfig.active;
        if (prevMode !== currentMode) await setMode(prevMode, true);
        return;
      }
      // Store pending load — polling callback will finalize model activation
      _pendingModelLoad = { key, localModelKey: modelKey };
      return;  // Don't set model config yet — wait for load to complete
    }
  }

  const resp = await fetch('/api/model-config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model_key: key}),
  });
  const data = await resp.json();
  if (data.success) {
    const cfgResp = await fetch('/api/model-config');
    modelConfig = await cfgResp.json();
    updateCostEstimate();
  }
}

function updateCostEstimate() {
  const el = document.getElementById('costEstimate');
  if (!el) return;
  // Hide cost info entirely in local mode — it's free
  if (currentMode === 'local') {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  if (!modelConfig) return;
  const active = modelConfig.active;
  const opt = modelConfig.options[active];
  if (!opt) return;
  el.className = 'cost-estimate';
  const remaining = modelConfig.remaining_cards;
  let html = `~$${opt.estimated_remaining.toFixed(2)} for ${remaining} cards`;
  html += ` \u00b7 $${opt.cost_per_image}/ea`;
  el.innerHTML = html;
}

function getActiveCostPerImage() {
  if (!modelConfig) return 0.06;
  const opt = modelConfig.options[modelConfig.active];
  return opt ? opt.cost_per_image : 0.06;
}

// --- Polling ---
let _wasActive = false; // track if generation/analysis was active last tick
let _lastCardsRev = null;

function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  async function poll() {
    const resp = await fetch('/api/status');
    const data = await resp.json();

    ollamaBusy = !!data.ollama_busy;
    isGeneratingBatch = !!data.is_generating;

    // Card list changed on the server (deck reload, add/remove, backfill,
    // server restart with new capabilities) — refresh allCards so flags
    // like is_split_halves reach already-open pages without a hard refresh
    if (data.cards_rev !== undefined && data.cards_rev !== _lastCardsRev) {
      const known = _lastCardsRev !== null;
      _lastCardsRev = data.cards_rev;
      if (known) {
        try {
          allCards = await (await fetch('/api/cards')).json();
          renderGrid();
          if (selectedCard) {
            const c = allCards.find(x => x.name === selectedCard);
            if (c) updateDetailPanel(c);
          }
        } catch (e) {}
      }
    }

    // Update card statuses
    const statuses = data.statuses;
    for (const card of allCards) {
      const s = statuses[card.name];
      if (s) {
        card.status = s.status || card.status;
        card.message = s.message || '';
        card.has_raw_art = s.has_raw_art || false;
        card.has_composite = s.has_composite || false;
        card.composite_mtime = s.composite_mtime || card.composite_mtime || 0;
        card.step = s.step || 0;
        card.total_steps = s.total_steps || 0;
        if (s.revised_prompt) card.revised_prompt = s.revised_prompt;
        if (card.is_dfc || card.is_split_halves) {
          if (s.has_back_raw !== undefined) card.has_back_raw = s.has_back_raw;
          if (s.has_back_composite !== undefined) card.has_back_composite = s.has_back_composite;
          if (s.has_back_ai_art !== undefined) card.has_back_ai_art = s.has_back_ai_art;
          card.back_composite_mtime = s.back_composite_mtime || card.back_composite_mtime || 0;
        }
      }
    }

    // Update UI
    updateBadges();
    updateStats();
    // Refresh cost estimate during batch generation
    if (data.is_generating) {
      fetch('/api/model-config').then(r => r.json()).then(cfg => {
        modelConfig = cfg;
        updateCostEstimate();
      }).catch(() => {});
    }
    // Check if batch is running on a different deck
    const batchDeckId = data.batch_deck_id;
    const currentDeckId = document.getElementById('deckSelect') ? document.getElementById('deckSelect').value : null;
    const batchOnOtherDeck = data.is_generating && batchDeckId && currentDeckId && batchDeckId !== currentDeckId;

    if (data.is_generating) {
      // Show progress mode in action bar: hide action buttons, show stop + progress
      document.getElementById('btnStop').style.display = '';
      document.getElementById('actionBarProgress').style.display = 'flex';
      document.getElementById('btnRegenPrompts').style.display = 'none';
      document.getElementById('btnGenFlavor').style.display = 'none';
      document.getElementById('btnGenArt').style.display = 'none';
      document.getElementById('btnRenderFrames').style.display = 'none';
      const msgEl = document.getElementById('batchMessage');
      const phase = data.batch_phase;
      const detail = data.batch_phase_detail;

      if (batchOnOtherDeck) {
        // Batch is running on another deck — show informational message
        document.getElementById('batchProgressFill').style.width = '';
        document.getElementById('batchProgressFill').classList.add('indeterminate');
        msgEl.textContent = 'Generating art on another deck...';
      } else if (phase === 'starting') {
        document.getElementById('batchProgressFill').classList.remove('indeterminate');
        document.getElementById('batchProgressFill').style.width = '0%';
        msgEl.textContent = detail || 'Preparing...';
      } else if (phase === 'waiting_ollama') {
        document.getElementById('batchProgressFill').classList.remove('indeterminate');
        document.getElementById('batchProgressFill').style.width = '10%';
        msgEl.textContent = detail || 'Waiting for style analysis to finish...';
      } else if (phase === 'prefetching') {
        document.getElementById('batchProgressFill').classList.remove('indeterminate');
        const match = detail && detail.match(/\((\d+)\/(\d+)\)/);
        if (match) {
          const pct = Math.round((parseInt(match[1]) / parseInt(match[2])) * 100);
          document.getElementById('batchProgressFill').style.width = pct + '%';
        }
        msgEl.textContent = detail || 'Downloading reference art...';
      } else if (phase === 'loading_model') {
        document.getElementById('batchProgressFill').classList.add('indeterminate');
        msgEl.textContent = detail || 'Loading image model...';
      } else {
        // Phase is 'generating' — show card-level progress
        document.getElementById('batchProgressFill').classList.remove('indeterminate');
        const done = allCards.filter(c => c.status === 'complete' && c.has_composite).length;
        const total = allCards.length;
        const pct = (done / total * 100).toFixed(0);
        document.getElementById('batchProgressFill').style.width = pct + '%';
        msgEl.textContent = '';
      }
    } else {
      // Restore action buttons, hide progress — but NOT if another bulk op owns the bar
      document.getElementById('btnStop').style.display = 'none';
      if (!_bulkOpActive) {
        document.getElementById('actionBarProgress').style.display = 'none';
        document.getElementById('batchProgressFill').classList.remove('indeterminate');
      }
      document.getElementById('btnRegenPrompts').style.display = '';
      document.getElementById('btnGenFlavor').style.display = '';
      document.getElementById('btnGenArt').style.display = '';
      document.getElementById('btnRenderFrames').style.display = '';
      const msgEl = document.getElementById('batchMessage');
      if (msgEl.textContent.startsWith('Preparing') ||
          msgEl.textContent.startsWith('Waiting for style') ||
          msgEl.textContent.startsWith('Downloading reference') ||
          msgEl.textContent.startsWith('Generating art on another')) {
        msgEl.textContent = '';
      }
    }

    // Style analysis progress bar
    const sp = data.style_progress;
    const styleWrap = document.getElementById('styleProgressWrap');
    const styleBtn = document.getElementById('btnReanalyzeStyle');
    if (sp && sp.phase) {
      styleWrap.style.display = '';
      const styleFill = document.getElementById('styleProgressFill');
      const stepLabel = document.getElementById('styleProgressStep');
      if (sp.phase === 'complete') {
        styleFill.classList.remove('indeterminate');
        styleFill.style.width = '100%';
        stepLabel.textContent = '';
      } else if (sp.sub_phase === 'api_call') {
        styleFill.classList.add('indeterminate');
        styleFill.style.width = '';
        stepLabel.textContent = sp.total > 1 ? `${sp.current}/${sp.total}` : '';
      } else {
        styleFill.classList.remove('indeterminate');
        const pct = Math.round((sp.current / sp.total) * 100);
        styleFill.style.width = pct + '%';
        stepLabel.textContent = `${sp.current}/${sp.total}`;
      }
      document.getElementById('styleProgressText').textContent = sp.message || '';
      if (styleBtn) styleBtn.disabled = true;
      document.getElementById('batchMessage').textContent = '';
    } else {
      if (styleWrap.style.display !== 'none') {
        styleWrap.style.display = 'none';
        const styleFill = document.getElementById('styleProgressFill');
        styleFill.classList.remove('indeterminate');
        styleFill.style.width = '0%';
        document.getElementById('styleProgressText').textContent = '';
        document.getElementById('styleProgressStep').textContent = '';
        if (styleBtn) styleBtn.disabled = false;
        loadDeckSettings();
        showToast('Style updated! Re-generate prompts to apply the new style.', 'success', {duration: 8000});
      }
    }

    // Model load progress
    const mlp = data.model_load_progress;
    if (mlp && mlp.phase) {
      const toastId = 'model-load';
      if (mlp.phase === 'downloading') {
        showToast(mlp.message || 'Downloading model...', 'info', {
          persistent: true, id: toastId, progress: mlp.pct,
        });
      } else if (mlp.phase === 'complete') {
        updateToast(toastId, mlp.message || 'Model loaded!', 'success');
        // Finalize pending model activation
        if (_pendingModelLoad) {
          fetch('/api/model-config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({model_key: _pendingModelLoad.key}),
          }).then(() => fetch('/api/model-config')).then(r => r.json()).then(cfg => {
            modelConfig = cfg;
            updateModelDropdown(modelConfig);
            updateCostEstimate();
            _pendingModelLoad = null;
          }).catch(() => { _pendingModelLoad = null; });
        }
      } else if (mlp.phase === 'error') {
        updateToast(toastId, 'Load failed: ' + (mlp.message || ''), 'error');
        if (_pendingModelLoad) {
          // Revert dropdown to previous model
          document.getElementById('modelSelect').value = modelConfig.active;
          _pendingModelLoad = null;
        }
      } else {
        showToast(mlp.message || 'Loading model...', 'info', {
          persistent: true, id: toastId, indeterminate: true,
        });
      }
      updateModelHubProgress(mlp);
    }

    // Ollama pull progress
    const opp = data.ollama_pull_progress;
    if (opp && opp.status) {
      const toastId = 'ollama-pull';
      if (opp.total_gb > 0) {
        showToast(`Pulling ${opp.model}: ${opp.completed_gb} / ${opp.total_gb} GB`, 'info', {
          persistent: true, id: toastId, progress: opp.pct,
        });
      } else {
        showToast(`Pulling ${opp.model}: ${opp.status}`, 'info', {
          persistent: true, id: toastId, indeterminate: true,
        });
      }
    }

    // Show feedback when Ollama is busy outside of batch generation (fallback)
    if (data.ollama_busy && !data.is_generating && !(sp && sp.phase)) {
      document.getElementById('batchMessage').textContent = 'Style analysis in progress...';
    } else if (!data.is_generating && !data.ollama_busy) {
      const msgEl = document.getElementById('batchMessage');
      if (msgEl.textContent === 'Style analysis in progress...') {
        msgEl.textContent = '';
      }
    }

    // Update detail panel if card is selected
    if (selectedCard) {
      const card = allCards.find(c => c.name === selectedCard);
      if (card) {
        // Refresh version history when selected card transitions to complete
        if (card.status === 'complete' && lastSelectedStatus !== 'complete') {
          loadVersionHistory(selectedCard, card.slug);
          // Refresh card data to update prompt_stale flag after generation
          fetch('/api/cards').then(r => r.json()).then(cards => {
            for (const updated of cards) {
              const existing = allCards.find(c => c.name === updated.name);
              if (existing) {
                existing.prompt_stale = updated.prompt_stale;
                existing.has_ai_art = updated.has_ai_art;
              }
            }
            const c = allCards.find(c => c.name === selectedCard);
            if (c) updateDetailPanel(c);
          }).catch(() => {});
        }
        lastSelectedStatus = card.status;
        updateDetailPanel(card);
      }
    }

    // Adaptive polling: fast when active, slow when idle
    const isActive = data.is_generating || data.ollama_busy || !!(data.style_progress && data.style_progress.phase) || !!(data.model_load_progress && data.model_load_progress.phase);
    if (isActive !== _wasActive) {
      _wasActive = isActive;
      clearInterval(pollInterval);
      pollInterval = setInterval(poll, isActive ? 1500 : 10000);
    }
  }
  _wasActive = false;
  pollInterval = setInterval(poll, 10000);
  poll(); // immediate first poll
}

// --- Rendering ---
function renderGrid() {
  syncPinnedCards();
  const grid = document.getElementById('cardGrid');
  grid.innerHTML = '';

  // Show welcome hero or empty state if no cards
  showWelcomeIfNeeded();
  const filtered = getFilteredCards();
  if (allCards.length === 0 && document.getElementById('welcomeHero').style.display === 'none') {
    showEmptyGridState();
    updateStats();
    return;
  }
  if (filtered.length === 0 && allCards.length > 0) {
    grid.innerHTML = '<div class="empty-state"><div class="empty-state-title">No cards match filters</div><div class="empty-state-hint">Try adjusting your search or filter criteria.</div></div>';
    updateStats();
    return;
  }

  for (const card of filtered) {
    const tile = document.createElement('div');
    let tileStatus = '';
    if (card.status === 'generating') tileStatus = ' tile-generating';
    else if (card.status === 'queued') tileStatus = ' tile-queued';
    else if (card.status === 'error') tileStatus = ' tile-error';
    else if (!card.has_composite) tileStatus = ' tile-pending';

    tile.className = 'card-tile' +
      (card.is_commander ? ' commander' : '') +
      (selectedCard === card.name ? ' selected' : '') +
      (checkedCards.has(card.name) ? ' checked' : '') +
      tileStatus;
    tile.dataset.name = card.name;

    const badgeClass = (ollamaBusy && card.status === 'queued')
      ? 'badge-analyzing'
      : ({
        'complete': 'badge-complete',
        'generating': 'badge-generating',
        'queued': 'badge-queued',
        'error': 'badge-error',
      }[card.status] || 'badge-pending');

    // Use composite if available, then Scryfall art, then proxy frame.
    // Composites use mtime (changes per regeneration); others use deckCacheBust
    // (changes per deck switch — they don't change otherwise).
    const imgSrc = card.has_composite
      ? `/api/image/composite/${card.slug}?v=${card.composite_mtime || 0}`
      : card.has_scryfall_art
        ? `/api/image/scryfall/${card.slug}?d=${deckCacheBust}`
        : `/api/image/proxy/${card.slug}?d=${deckCacheBust}`;

    const isPinned = pinnedCards.has(card.name);
    const dfcBadge = (card.is_dfc || card.is_split_halves)
      ? `<div class="dfc-badge" title="${card.is_dfc ? 'Double-faced card' : 'Split card — two halves'}">&#x21C6;</div>` : '';
    tile.innerHTML = `
      <div class="select-checkbox"></div>
      <div class="card-status-badge ${badgeClass}"></div>
      ${dfcBadge}
      <img src="${imgSrc}" alt="${escapeHtml(card.name)}" loading="lazy">
      <div class="card-tile-info">
        <div class="card-tile-name">${escapeHtml(card.name)}</div>
        <div class="card-tile-type">${escapeHtml(card.type_line)}</div>
        <div class="pin-icon${isPinned ? ' pinned' : ''}"></div>
      </div>
    `;

    // Use addEventListener instead of inline onclick to avoid apostrophe escaping issues
    const checkbox = tile.querySelector('.select-checkbox');
    checkbox.textContent = checkedCards.has(card.name) ? '✓' : '';
    checkbox.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleCheck(card.name);
    });

    const pinEl = tile.querySelector('.pin-icon');
    pinEl.addEventListener('click', (e) => {
      e.stopPropagation();
      togglePin(card.name);
    });

    tile.addEventListener('click', (e) => {
      e.stopPropagation();
      selectCard(card.name);
    });
    grid.appendChild(tile);
  }

  updateStats();
}

function getFilteredCards() {
  const type = document.getElementById('filterType').value;
  const status = document.getElementById('filterStatus').value;
  const search = document.getElementById('searchInput').value.toLowerCase();

  return allCards.filter(c => {
    if (type !== 'all' && c.card_type !== type) return false;
    if (status === 'complete' && c.status !== 'complete') return false;
    if (status === 'pending' && c.status !== 'pending') return false;
    if (status === 'error' && c.status !== 'error') return false;
    if (status === 'selected' && !checkedCards.has(c.name)) return false;
    if (status === 'pinned' && !pinnedCards.has(c.name)) return false;
    if (status === 'generating' && c.status !== 'generating' && c.status !== 'queued') return false;
    if (search && !c.name.toLowerCase().includes(search)) return false;
    return true;
  });
}

function applyFilters() { updateFilterIndicator(); renderGrid(); }

function toggleFilterStrip() {
  const strip = document.getElementById('filterStrip');
  strip.classList.toggle('open');
  if (strip.classList.contains('open')) document.getElementById('searchInput').focus();
}

function clearFilters() {
  document.getElementById('searchInput').value = '';
  document.getElementById('filterType').value = 'all';
  document.getElementById('filterStatus').value = 'all';
  updateFilterIndicator();
  applyFilters();
}

function updateFilterIndicator() {
  const hasFilter = document.getElementById('searchInput').value !== '' ||
    document.getElementById('filterType').value !== 'all' ||
    document.getElementById('filterStatus').value !== 'all';
  document.getElementById('filterToggle').classList.toggle('has-active-filter', hasFilter);
}

function updateBadges() {
  document.querySelectorAll('.card-tile').forEach(tile => {
    const card = allCards.find(c => c.name === tile.dataset.name);
    if (!card) return;
    const badge = tile.querySelector('.card-status-badge');
    const prevStatus = tile.dataset.lastStatus || '';
    badge.className = 'card-status-badge ' + (
      (ollamaBusy && card.status === 'queued')
        ? 'badge-analyzing'
        : ({
          'complete': 'badge-complete',
          'generating': 'badge-generating',
          'queued': 'badge-queued',
          'error': 'badge-error',
        }[card.status] || 'badge-pending')
    );

    // Update image when card has a composite. Mtime-based URL changes if
    // and only if the file changed; setting img.src to the same URL is a no-op.
    if (card.has_composite) {
      const img = tile.querySelector('img');
      const expected = `/api/image/composite/${card.slug}?v=${card.composite_mtime || 0}`;
      if (img.getAttribute('src') !== expected) {
        img.src = expected;
      }
    }
    // Update tile status classes
    tile.classList.remove('tile-generating', 'tile-queued', 'tile-error', 'tile-pending');
    if (card.status === 'generating') tile.classList.add('tile-generating');
    else if (card.status === 'queued') tile.classList.add('tile-queued');
    else if (card.status === 'error') tile.classList.add('tile-error');
    else if (!card.has_composite) tile.classList.add('tile-pending');

    tile.dataset.lastStatus = card.status;
  });
}

// ── Bulk operation button helpers ──
// Guard: prevents status poller from hiding progress bar during non-art bulk ops
let _bulkOpActive = false;

function disableBulkButtons() {
  _bulkOpActive = true;
  for (const id of ['btnRegenPrompts', 'btnGenFlavor', 'btnGenArt', 'btnRenderFrames']) {
    document.getElementById(id).disabled = true;
  }
  document.getElementById('actionBarProgress').style.display = 'flex';
  document.getElementById('batchProgressFill').classList.remove('indeterminate');
  document.getElementById('batchProgressFill').style.width = '0%';
  document.getElementById('batchProgressFill').style.background = '';
}

function enableBulkButtons() {
  const n = checkedCards.size;
  const dis = n === 0;
  document.getElementById('btnRegenPrompts').textContent = `Prompts (${n})`;
  document.getElementById('btnRegenPrompts').disabled = dis;
  document.getElementById('btnGenFlavor').textContent = `Flavor (${n})`;
  document.getElementById('btnGenFlavor').disabled = dis;
  document.getElementById('btnGenArt').textContent = `Art (${n})`;
  document.getElementById('btnGenArt').disabled = dis;
  document.getElementById('btnRenderFrames').textContent = `Frames (${n})`;
  document.getElementById('btnRenderFrames').disabled = dis;
  // Hide progress after delay
  setTimeout(() => {
    _bulkOpActive = false;
    document.getElementById('actionBarProgress').style.display = 'none';
    document.getElementById('batchProgressFill').style.width = '0%';
    document.getElementById('batchProgressFill').classList.remove('indeterminate');
    document.getElementById('batchMessage').textContent = '';
  }, 3000);
}

function updateStats() {
  const complete = allCards.filter(c => c.status === 'complete' && c.has_composite).length;
  const generating = allCards.filter(c => c.status === 'generating' || c.status === 'queued').length;
  const pending = allCards.length - complete - generating;
  document.getElementById('statComplete').textContent = `${complete} done`;
  document.getElementById('statGenerating').textContent = generating > 0 ? `${generating} active` : '';
  document.getElementById('statPending').textContent = `${pending} pending`;

  const n = checkedCards.size;
  const dis = n === 0;
  document.getElementById('btnRegenPrompts').textContent = `Prompts (${n})`;
  document.getElementById('btnRegenPrompts').disabled = dis;
  document.getElementById('btnGenFlavor').textContent = `Flavor (${n})`;
  document.getElementById('btnGenFlavor').disabled = dis;
  document.getElementById('btnGenArt').textContent = `Art (${n})`;
  document.getElementById('btnGenArt').disabled = dis;
  document.getElementById('btnRenderFrames').textContent = `Frames (${n})`;
  document.getElementById('btnRenderFrames').disabled = dis;

  const allPinned = n > 0 && [...checkedCards].every(c => pinnedCards.has(c));
  document.getElementById('btnPinChecked').textContent = allPinned ? `Unpin (${n})` : `Pin (${n})`;
  document.getElementById('btnPinChecked').disabled = dis;

  // Update action bar count and visibility
  const actionBar = document.getElementById('actionBar');
  document.getElementById('actionBarCount').textContent = `${n} selected`;
  if (allCards.length > 0) {
    actionBar.classList.add('visible');
  } else {
    actionBar.classList.remove('visible');
  }
}

// --- Selection ---
function switchPanelTab(tab) {
  activePanelTab = tab;
  document.getElementById('tabStyle').classList.toggle('active', tab === 'style');
  document.getElementById('tabCard').classList.toggle('active', tab === 'card');
  document.getElementById('tabFrame').classList.toggle('active', tab === 'frame');

  document.getElementById('noSelection').style.display = tab === 'style' ? 'flex' : 'none';
  document.getElementById('cardDetail').style.display = (tab === 'card' && selectedCard) ? '' : 'none';
  document.getElementById('cardTabEmpty').style.display = (tab === 'card' && !selectedCard) ? 'block' : 'none';
  document.getElementById('frameDesigner').style.display = tab === 'frame' ? '' : 'none';

  if (tab === 'frame') {
    updateFrameTab();
  }
}

// --- Double-faced card face helpers ---
// The selected face piggybacks on the card name (" [back]") for prompt keys
// and version/revert endpoints, and on the slug ("__back") for image URLs.
function viewingBack(card) {
  return !!(card && (card.is_dfc || card.is_split_halves) && selectedFace === 'back');
}
// Hint for single-faced multi-part cards, where "A // B" names suggest a
// flip side that doesn't exist (adventures, rooms, split cards).
function faceHintFor(card) {
  if (!card || card.is_dfc || card.is_split_halves) return '';
  if (card.layout === 'adventure') return 'Adventure card — single-faced: the adventure half renders beside the creature’s rules.';
  if (card.layout === 'split') return 'Single-faced card — both halves render side by side in the text box.';
  return '';
}
function faceKeyFor(card) {
  if (!card) return selectedCard;
  return viewingBack(card) ? card.name + ' [back]' : card.name;
}
function faceSlugFor(card) {
  if (!card) return name_to_slug(selectedCard || '');
  return viewingBack(card) ? card.back_slug : card.slug;
}

function setFace(face) {
  if (selectedFace === face) return;
  selectedFace = face;
  const card = allCards.find(c => c.name === selectedCard);
  if (!card) return;
  selectedVersion = null;
  const actionArea = document.getElementById('detailActionArea');
  if (actionArea) delete actionArea.dataset.lastState;
  updateDetailPanel(card);
  loadVersionHistory(faceKeyFor(card), faceSlugFor(card));
  // Frame Designer edits the selected face — reload it if it's open
  if (activePanelTab === 'frame') updateFrameTab();
  syncFdFaceToggle(card);
}

function selectCard(name) {
  selectedCard = name;
  selectedFace = 'front';
  selectedVersion = null;
  lastSelectedStatus = null;
  const card = allCards.find(c => c.name === name);
  if (!card) return;

  // Clear action area state cache so it fully rebuilds for new card
  const actionArea = document.getElementById('detailActionArea');
  if (actionArea) delete actionArea.dataset.lastState;

  switchPanelTab('card');

  updateDetailPanel(card);
  loadVersionHistory(name, card.slug);
  renderGrid();
}

function toggleCollapsible(id) {
  document.getElementById(id).classList.toggle('open');
}

function renderManaCost(manaCostStr) {
  if (!manaCostStr) return '';
  return manaCostStr.replace(/\{([^}]+)\}/g, (_, sym) => {
    // Scryfall symbol SVGs drop the slash: {U/R} -> UR.svg, {U/P} -> UP.svg
    const slug = sym.toUpperCase().replace(/\//g, '');
    return `<img src="https://svgs.scryfall.io/card-symbols/${encodeURIComponent(slug)}.svg" alt="{${escapeHtml(sym)}}" width="16" height="16">`;
  });
}

function renderActionArea(card) {
  const area = document.getElementById('detailActionArea');
  const hero = document.getElementById('detailHero');
  const heroProgress = document.getElementById('detailHeroProgress');
  const back = viewingBack(card);
  const hasArt = back ? (card.has_back_composite || card.has_back_raw)
                      : (card.has_composite || card.has_raw_art);

  // Update hero progress overlay
  if (card.status === 'generating' && card.total_steps > 0) {
    const pct = Math.round((card.step / card.total_steps) * 100);
    heroProgress.classList.add('active');
    hero.classList.add('generating');
    const fill = document.getElementById('detailHeroProgressFill');
    fill.style.width = pct + '%';
    fill.classList.remove('indeterminate');
    document.getElementById('detailHeroProgressLabel').textContent =
      `Step ${card.step}/${card.total_steps} (${pct}%)`;
  } else if (card.status === 'generating') {
    heroProgress.classList.add('active');
    hero.classList.add('generating');
    const fill = document.getElementById('detailHeroProgressFill');
    fill.classList.add('indeterminate');
    document.getElementById('detailHeroProgressLabel').textContent =
      ollamaBusy ? 'Waiting for analysis...' : 'Generating...';
  } else {
    heroProgress.classList.remove('active');
    hero.classList.remove('generating');
  }

  // Update status pip
  const pip = document.getElementById('detailStatusPip');
  const pipClass = {
    'complete': 'pip-complete',
    'generating': 'pip-generating',
    'queued': 'pip-queued',
    'error': 'pip-error',
  }[card.status] || 'pip-pending';
  pip.className = 'detail-status-pip ' + pipClass;

  // Show stale art indicator when prompt has been updated since art was generated
  const staleBanner = document.getElementById('staleArtBanner');
  if (staleBanner) {
    staleBanner.style.display = card.prompt_stale ? '' : 'none';
  }

  // State caching: avoid clobbering feedback input on every poll
  const currentState = `${card.status}|${hasArt}|${isGeneratingBatch}|${ollamaBusy}|${selectedFace}`;
  if (area.dataset.lastState === currentState) {
    // Just update text in generating state without rebuilding DOM
    if (card.status === 'generating') {
      const label = area.querySelector('.detail-action-primary');
      if (label) {
        const text = ollamaBusy ? 'Waiting for analysis...' : 'Generating Art...';
        label.innerHTML = `<span class="generating-dot"></span> ${text}`;
      }
    }
    return;
  }

  // Save feedback input value before rebuilding
  const existingFeedback = document.getElementById('detailFeedback')?.value || '';
  area.dataset.lastState = currentState;

  if (card.status === 'error') {
    area.innerHTML = `
      <div class="detail-error-banner">
        <span class="detail-error-icon">!</span>
        <span>${escapeHtml(card.message || 'Generation failed')}</span>
      </div>
      <button class="detail-action-primary btn btn-primary" id="btnGenerateCurrent"
              onclick="generateCurrent(this)">Retry Generation</button>`;
  } else if (card.status === 'generating') {
    const label = ollamaBusy ? 'Waiting for analysis...' : 'Generating Art...';
    area.innerHTML = `
      <button class="detail-action-primary btn-generating" disabled>
        <span class="generating-dot"></span> ${label}
      </button>
      ${!isGeneratingBatch ? `<button class="detail-generate-new" onclick="cancelCurrentGeneration()">Cancel</button>` : ''}`;
  } else if (card.status === 'queued') {
    const label = ollamaBusy ? 'Waiting for analysis...' : 'Queued...';
    area.innerHTML = `
      <button class="detail-action-primary btn-queued" disabled>
        <span class="queued-dot"></span> ${label}
      </button>`;
  } else if (hasArt) {
    area.innerHTML = `
      <div class="detail-action-subrow">
        <div class="art-orient-toggle" id="artOrientToggle">
          <button class="art-orient-btn active" data-orient="portrait" onclick="setArtOrientation('portrait')">Portrait</button>
          <button class="art-orient-btn" data-orient="landscape" onclick="setArtOrientation('landscape')">Landscape</button>
        </div>
        <button class="btn btn-secondary" id="btnGenerateCurrent"
                onclick="generateCurrent(this)" style="flex:1;"
                title="Render the current prompt into a new image (fresh seed). Same scene, different take.">Render Art</button>
      </div>
      <div class="detail-feedback-row">
        <input type="text" id="detailFeedback" class="detail-feedback-input"
               placeholder="Steer a new prompt (e.g. at night, underwater, more whimsical)…"
               title="Steer & Render rewrites the prompt in this direction, then renders it. Leave blank for a fresh, undirected take.">
        <button class="btn btn-gold" id="btnRegenerateCurrent"
                onclick="regenerateCurrent(this)"
                title="Rewrite the prompt (steered by the text on the left), then render it. Blank steer = a fresh directed take.">Steer &amp; Render</button>
      </div>`;
    if (existingFeedback) document.getElementById('detailFeedback').value = existingFeedback;
  } else {
    area.innerHTML = `
      <button class="detail-action-primary btn btn-primary" id="btnGenerateCurrent"
              onclick="generateCurrent(this)">Generate Art</button>
      <div class="detail-action-subrow" style="justify-content:center;margin-top:8px;">
        <div class="art-orient-toggle" id="artOrientToggle">
          <button class="art-orient-btn active" data-orient="portrait" onclick="setArtOrientation('portrait')">Portrait</button>
          <button class="art-orient-btn" data-orient="landscape" onclick="setArtOrientation('landscape')">Landscape</button>
        </div>
      </div>`;
  }
  loadArtOrientation();
}

function updateDetailPanel(card) {
  // Face toggle — visible only for double-faced cards
  const faceToggle = document.getElementById('faceToggle');
  if (faceToggle) {
    const hasFaces = card.is_dfc || card.is_split_halves;
    faceToggle.style.display = hasFaces ? 'flex' : 'none';
    const fBtn = document.getElementById('faceBtnFront');
    const bBtn = document.getElementById('faceBtnBack');
    // Split halves aren't front/back — label the toggle with the half names
    const names = (card.is_split_halves && card.face_names) ? card.face_names : null;
    fBtn.textContent = names ? names[0] : 'Front';
    bBtn.textContent = names ? names[1] : 'Back';
    fBtn.classList.toggle('active', selectedFace !== 'back');
    bBtn.classList.toggle('active', selectedFace === 'back');
  }
  const faceHint = document.getElementById('faceHint');
  if (faceHint) {
    const hint = faceHintFor(card);
    faceHint.textContent = hint;
    faceHint.style.display = hint ? '' : 'none';
  }
  const back = viewingBack(card);
  const bf = back ? (card.back_face || {}) : null;

  document.getElementById('detailName').textContent = back ? (bf.name || card.name) : card.name;
  document.getElementById('detailTypeLine').textContent = (back ? bf.type_line : card.type_line) || '';
  document.getElementById('detailManaCost').innerHTML = renderManaCost(back ? bf.mana_cost : card.mana_cost);

  // Only update prompt textarea if user isn't actively editing it
  const promptEl = document.getElementById('detailPrompt');
  if (document.activeElement !== promptEl) {
    promptEl.value = cleanScenePrompt(back ? (card.back_prompt || '') : card.prompt);
  }

  // Populate scene direction (distilled subject)
  const subjectEl = document.getElementById('detailSubject');
  if (subjectEl && document.activeElement !== subjectEl) {
    subjectEl.value = card.distilled_subject || '';
  }

  // Flavor text — editable for the front face; the back face's flavor text
  // is display-only for now (no per-face flavor storage yet)
  const flavorEl = document.getElementById('detailFlavorEdit');
  if (flavorEl && document.activeElement !== flavorEl) {
    flavorEl.value = (back ? bf.flavor_text : card.flavor_text) || '';
    flavorEl.disabled = back;
  }

  // Smart action area (state machine)
  renderActionArea(card);

  // Load oversized toggle state
  const _osToggle = document.getElementById('cardOversizedToggle');
  if (_osToggle) {
    fetch(`/api/decks/${document.getElementById('deckSelect').value}/oversized-generation`)
      .then(r => r.json())
      .then(d => { _osToggle.checked = !!d.oversized_generation; })
      .catch(() => {});
  }

  let imgSrc;
  if (back && card.is_split_halves) {
    // Split halves share one combined composite
    imgSrc = card.has_composite
      ? `/api/image/composite/${card.slug}?v=${card.composite_mtime || 0}`
      : card.has_scryfall_art
        ? `/api/image/scryfall/${card.slug}?d=${deckCacheBust}`
        : `/api/image/proxy/${card.slug}?d=${deckCacheBust}`;
  } else if (back) {
    // Last resort is the FRONT image chain — a proxy URL for the back slug
    // would always 404 (nothing generates back-face proxies)
    imgSrc = card.has_back_composite
      ? `/api/image/composite/${card.back_slug}?v=${card.back_composite_mtime || 0}`
      : card.has_back_scryfall_art
        ? `/api/image/scryfall/${card.back_slug}?d=${deckCacheBust}`
        : card.has_composite
          ? `/api/image/composite/${card.slug}?v=${card.composite_mtime || 0}`
          : `/api/image/proxy/${card.slug}?d=${deckCacheBust}`;
  } else {
    imgSrc = card.has_composite
      ? `/api/image/composite/${card.slug}?v=${card.composite_mtime || 0}`
      : card.has_scryfall_art
        ? `/api/image/scryfall/${card.slug}?d=${deckCacheBust}`
        : `/api/image/proxy/${card.slug}?d=${deckCacheBust}`;
  }
  document.getElementById('detailImage').src = imgSrc;

  if (card.revised_prompt) {
    document.getElementById('detailRevised').style.display = '';
    document.getElementById('detailRevisedText').textContent = card.revised_prompt;
  } else {
    document.getElementById('detailRevised').style.display = 'none';
  }
}

function clearSelection() {
  selectedCard = null;
  document.getElementById('tabCard').classList.remove('has-card');
  if (activePanelTab === 'card') {
    document.getElementById('cardDetail').style.display = 'none';
    document.getElementById('cardTabEmpty').style.display = 'block';
  } else {
    switchPanelTab('style');
  }
  renderGrid();
}

function toggleCheck(name) {
  if (checkedCards.has(name)) {
    checkedCards.delete(name);
  } else {
    checkedCards.add(name);
  }
  renderGrid();
}

function selectAll() {
  getFilteredCards().forEach(c => checkedCards.add(c.name));
  renderGrid();
}

function deselectAll() {
  checkedCards.clear();
  renderGrid();
}

// --- Pin Cards (persistent across refresh) ---
function syncPinnedCards() {
  pinnedCards = new Set(allCards.filter(c => c.is_pinned).map(c => c.name));
}
// syncPinnedCards is also called at top of renderGrid() to stay in sync

async function togglePin(name) {
  const wasPinned = pinnedCards.has(name);
  const pinned = !wasPinned;
  // Optimistic update — sync allCards so syncPinnedCards() in renderGrid stays consistent
  const card = allCards.find(c => c.name === name);
  if (card) card.is_pinned = pinned;
  renderGrid();

  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;
  try {
    await fetch(`/api/decks/${deckId}/pinned-cards`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({card_names: [name], pinned}),
    });
  } catch (e) {
    // Revert on error
    if (pinned) pinnedCards.delete(name); else pinnedCards.add(name);
    renderGrid();
  }
}

async function pinChecked() {
  const names = [...checkedCards];
  if (!names.length) return;
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  // Check if all checked are already pinned — if so, unpin them
  const allPinned = names.every(n => pinnedCards.has(n));
  const pinned = !allPinned;

  names.forEach(n => {
    const card = allCards.find(c => c.name === n);
    if (card) card.is_pinned = pinned;
  });
  renderGrid();

  try {
    await fetch(`/api/decks/${deckId}/pinned-cards`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({card_names: names, pinned}),
    });
  } catch (e) {
    names.forEach(n => pinned ? pinnedCards.delete(n) : pinnedCards.add(n));
    renderGrid();
  }
}

// --- Mode Toggle (Cloud / Local) ---
async function setMode(mode, skipPost) {
  currentMode = mode;

  // POST backend change to server (unless loading from saved state)
  if (!skipPost) {
    const llmBackend = mode === 'local' ? 'local' : 'openai';
    await fetch('/api/backend', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({llm_backend: llmBackend}),
    });
  }

  updateCostEstimate();
}

function populateModelDropdown() {
  const sel = document.getElementById('modelSelect');
  if (!sel || !modelConfig) return;

  const prevValue = sel.value;
  sel.innerHTML = '';

  // Build optgroups for unified Cloud/Local dropdown
  const cloudGroup = document.createElement('optgroup');
  cloudGroup.label = 'Cloud';
  const localGroup = document.createElement('optgroup');
  localGroup.label = 'Local';

  let hasCloud = false, hasLocal = false;

  for (const [key, opt] of Object.entries(modelConfig.options)) {
    if (opt.disabled) continue;
    const option = document.createElement('option');
    option.value = key;
    if (opt.is_local) {
      option.textContent = opt.label;
      localGroup.appendChild(option);
      hasLocal = true;
    } else {
      option.textContent = opt.label + ' ($' + opt.cost_per_image + ')';
      cloudGroup.appendChild(option);
      hasCloud = true;
    }
  }

  if (hasCloud) sel.appendChild(cloudGroup);
  if (hasLocal) sel.appendChild(localGroup);

  // Restore previous selection or pick default
  if (prevValue && sel.querySelector('option[value="' + prevValue + '"]')) {
    sel.value = prevValue;
  } else if (sel.querySelector('option[value="local-flux-schnell"]')) {
    sel.value = 'local-flux-schnell';
  } else {
    sel.value = sel.options[0]?.value || '';
  }
}

async function loadMode() {
  try {
    const [backendResp, cfgResp] = await Promise.all([
      fetch('/api/backend'),
      fetch('/api/model-config'),
    ]);
    const backendData = await backendResp.json();
    modelConfig = await cfgResp.json();

    // Populate unified dropdown (all models, grouped by backend)
    populateModelDropdown();

    const llmBackend = backendData.config?.llm_backend || 'openai';
    const mode = llmBackend === 'local' ? 'local' : 'cloud';

    // Set UI without posting back (skipPost = true)
    await setMode(mode, true);

    // Sync dropdown to server's active model
    const sel = document.getElementById('modelSelect');
    if (sel && modelConfig.active && sel.querySelector('option[value="' + modelConfig.active + '"]')) {
      sel.value = modelConfig.active;
    }

    updateCostEstimate();

    // Update setup bar state
    _setupBarState.hasApiKey = backendData.has_openai_key;
    _setupBarState.hasLocalDeps = !Object.values(modelConfig.options).some(o => o.is_local && o.disabled);
  } catch (e) {
    console.warn('Could not load mode:', e);
  }
}

// --- Settings Panel ---
function toggleDeckMenu() {
  const menu = document.getElementById('deckOverflowMenu');
  const btn = menu.previousElementSibling;
  if (menu.style.display === 'none') {
    menu.style.display = '';
    btn.classList.add('active');
    setTimeout(() => document.addEventListener('click', closeDeckMenuOnClickOutside), 0);
  } else {
    closeDeckMenu();
  }
}

function closeDeckMenu() {
  const menu = document.getElementById('deckOverflowMenu');
  const btn = menu.previousElementSibling;
  menu.style.display = 'none';
  btn.classList.remove('active');
  document.removeEventListener('click', closeDeckMenuOnClickOutside);
}

function closeDeckMenuOnClickOutside(e) {
  const wrap = document.querySelector('.deck-overflow-wrap');
  if (!wrap.contains(e.target)) closeDeckMenu();
}

// --- API Key (from Models dialog) ---
async function clearApiKeyFromHub() {
  const resp = await fetch('/api/clear-key', { method: 'POST' });
  const data = await resp.json();
  if (data.success) {
    showToast('API key removed', 'success');
    _setupBarState.hasApiKey = false;
    updateSetupBar();
    openModelHub();
  } else {
    showToast('Error: ' + data.error, 'error');
  }
}

async function setApiKeyFromHub() {
  const input = document.getElementById('hubApiKeyInput');
  const key = input ? input.value.trim() : '';
  if (!key) return;

  const resp = await fetch('/api/set-key', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  });

  const data = await resp.json();
  if (data.success) {
    input.value = '';
    showToast('API key connected', 'success');
    _setupBarState.hasApiKey = true;
    updateSetupBar();
    // Refresh the hub to show connected state
    openModelHub();
  } else {
    showToast('API key error: ' + data.error, 'error');
  }
}

// --- Generation ---
function _showGeneratingState(cardName) {
  // Optimistic UI: immediately show "Generating Art..." without waiting for poll.
  // The real poll will overwrite this with accurate step/progress data.
  const area = document.getElementById('detailActionArea');
  if (area) {
    area.dataset.lastState = '';  // clear cache so next poll can update
    area.innerHTML = `
      <button class="detail-action-primary btn-generating" disabled>
        <span class="generating-dot"></span> Generating Art...
      </button>`;
  }
  // Update the card's grid badge too
  const card = allCards.find(c => c.name === cardName);
  if (card) card.status = 'generating';
}

async function generateCurrent(btn) {
  if (!selectedCard) return;
  const card = allCards.find(c => c.name === selectedCard);
  const prompt = document.getElementById('detailPrompt').value;
  // Which face to render: single-faced cards always 'all'. On a DFC, render
  // the face being viewed — except a fresh front render also covers a back
  // face that has never had AI art, so one click finishes the whole card.
  let face = 'all';
  if (card && (card.is_dfc || card.is_split_halves)) {
    face = selectedFace;
    if (face === 'front' && !card.has_back_ai_art) face = 'all';
  }
  // Save the local prompt (scene direction) before generating — the blur
  // handler races with the generate request, causing the generation to
  // use the PREVIOUS subject instead of what's currently in the textarea.
  const subjectEl = document.getElementById('detailSubject');
  if (subjectEl) {
    const deckId = document.getElementById('deckSelect').value;
    await fetch(`/api/decks/${deckId}/card-subject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_name: selectedCard, subject: subjectEl.value.trim() }),
    }).catch(() => {});
  }
  // Immediately show generating state so user sees instant feedback
  _showGeneratingState(selectedCard);

  try {
    const resp = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        card_name: selectedCard,
        custom_prompt: prompt,
        face: face,
      }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      showToast(data.error || 'Generation failed', 'error');
    }
    // Kick fast polling to pick up real status
    startPolling();
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

// "Regenerate" with a steer: regenerate the PROMPT in the user's direction
// (escaping the theme the plain roll keeps circling), then render it.
async function regenerateCurrent(btn) {
  if (!selectedCard) return;
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;
  const steerEl = document.getElementById('detailFeedback');
  const steer = steerEl ? steerEl.value.trim() : '';
  const selCard = allCards.find(c => c.name === selectedCard);
  const isBack = viewingBack(selCard);
  const promptKey = faceKeyFor(selCard);  // "<name> [back]" targets the back face
  const face = (selCard && (selCard.is_dfc || selCard.is_split_halves)) ? selectedFace : 'all';

  _showGeneratingState(selectedCard);
  if (btn) { btn.disabled = true; btn.textContent = 'Steering…'; }
  try {
    // 1) Regenerate the prompt, steered by the user's direction.
    const resp = await fetch(`/api/decks/${deckId}/regenerate-prompts`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_ai: true, card_names: [promptKey], steer }),
    });
    const data = await resp.json();
    if (!data.success) { showToast(data.error || 'Failed to regenerate prompt', 'error'); return; }

    // 2) Wait for the new prompt.
    const jobId = data.job_id;
    await new Promise((resolve) => {
      const poll = setInterval(async () => {
        try {
          const r = await fetch(`/api/regen-prompts/progress/${jobId}`);
          if (!r.ok) return;
          if ((await r.json()).done) { clearInterval(poll); resolve(); }
        } catch (e) {}
      }, 800);
    });

    // 3) Pull the steered prompt into the field, then render art from it.
    const cards = await (await fetch('/api/cards')).json();
    allCards = cards;
    const card = allCards.find(c => c.name === selectedCard);
    const newPrompt = card ? cleanScenePrompt(isBack ? (card.back_prompt || '') : card.prompt)
                           : document.getElementById('detailPrompt').value;
    const promptEl = document.getElementById('detailPrompt');
    if (promptEl) promptEl.value = newPrompt;

    if (btn) btn.textContent = 'Generating…';
    await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_name: selectedCard, custom_prompt: newPrompt, face: face }),
    });
    startPolling();
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Steer & Render'; }
  }
}

async function cancelCurrentGeneration() {
  if (!selectedCard) return;
  try {
    const resp = await fetch('/api/cancel-single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_name: selectedCard }),
    });
    const data = await resp.json();
    if (data.success) {
      showToast('Generation cancelled', 'info');
      // Immediately update local state so UI responds
      const card = allCards.find(c => c.name === selectedCard);
      if (card) {
        card.status = 'cancelled';
        card.message = 'Cancelled by user';
        updateDetailPanel(card);
      }
    }
  } catch (e) {
    showToast('Failed to cancel: ' + e.message, 'error');
  }
}

async function regeneratePromptForCard() {
  if (!selectedCard) return;
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  const btn = document.getElementById('btnRegenPromptSingle');
  btn.disabled = true;
  btn.textContent = 'Generating…';

  const isLocal = currentMode === 'local';
  let useAi = true;
  if (!isLocal) {
    const dialogResult = await showCustomDialog({
      title: 'Regenerate Prompt',
      message: `Regenerate the art prompt for "${escapeHtml(selectedCard)}".`,
      fields: [
        { type: 'toggle', name: 'useAi', label: 'AI-enhanced subject',
          checked: true,
          description: '~$0.001 via OpenAI' },
      ],
      confirmText: 'Regenerate',
    });
    if (!dialogResult) { btn.disabled = false; btn.textContent = 'Regenerate Prompt'; return; }
    useAi = dialogResult.useAi;
  }

  try {
    // Target the face being viewed — "<name> [back]" regenerates the DFC
    // back / split right-half prompt instead of the front's
    const _rpCard = allCards.find(c => c.name === selectedCard);
    const _rpKey = faceKeyFor(_rpCard);
    const resp = await fetch(`/api/decks/${deckId}/regenerate-prompts`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ use_ai: useAi, card_names: [_rpKey] }),
    });
    const data = await resp.json();
    if (!data.success) {
      showToast(data.error || 'Failed to regenerate prompt', 'error');
      btn.disabled = false;
      btn.textContent = 'Generate Random';
      return;
    }

    // Poll for completion
    const jobId = data.job_id;
    const poll = setInterval(async () => {
      try {
        const r = await fetch(`/api/regen-prompts/progress/${jobId}`);
        if (!r.ok) return;
        const prog = await r.json();
        if (prog.done) {
          clearInterval(poll);
          // Reload cards to get updated prompt
          const cardsResp = await fetch('/api/cards');
          const cards = await cardsResp.json();
          allCards = cards;
          const card = allCards.find(c => c.name === selectedCard);
          if (card) {
            document.getElementById('detailPrompt').value = cleanScenePrompt(
              viewingBack(card) ? (card.back_prompt || '') : card.prompt);
            updateDetailPanel(card);
          }
          btn.disabled = false;
          btn.textContent = 'Generate Random';
          showToast('Prompt regenerated', 'success');
        } else {
          btn.textContent = 'Generating…';
        }
      } catch (_) {}
    }, 1000);
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
    btn.disabled = false;
    btn.textContent = 'Generate Random';
  }
}

// ---------------------------------------------------------------------------
// Flavor text generation
// ---------------------------------------------------------------------------
async function generateFlavorText() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId || checkedCards.size === 0) return;

  const names = [...checkedCards];
  const dialogResult = await showCustomDialog({
    title: `Generate Flavor Text`,
    message: `Generate themed flavor text for ${names.length} selected card${names.length > 1 ? 's' : ''}?`,
    confirmText: 'Generate',
  });
  if (!dialogResult) return;

  const progressFill = document.getElementById('batchProgressFill');
  const batchMessage = document.getElementById('batchMessage');

  disableBulkButtons();
  batchMessage.textContent = 'Starting flavor text generation...';
  progressFill.style.width = '0%';
  progressFill.style.background = '';
  progressFill.classList.remove('indeterminate');

  try {
    const resp = await fetch(`/api/decks/${deckId}/generate-flavor-text`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_names: names }),
    });
    const data = await resp.json();
    if (!data.success) {
      batchMessage.textContent = data.error || 'Failed';
      enableBulkButtons();
      return;
    }

    const jobId = data.job_id;

    const result = await new Promise((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const r = await fetch(`/api/flavor-text/progress/${jobId}`);
          if (!r.ok) return;
          const prog = await r.json();

          batchMessage.textContent = 'Generating flavor text...';
          if (prog.total > 0) {
            batchMessage.textContent = `Generating flavor text: ${prog.step} / ${prog.total}`;
            progressFill.style.width = Math.round((prog.step / prog.total) * 100) + '%';
          }

          if (prog.done) {
            clearInterval(interval);
            if (prog.error) reject(new Error(prog.error));
            else resolve(prog);
          }
        } catch (_) {}
      }, 1000);
    });

    batchMessage.textContent = result.message || 'Done!';
    progressFill.style.width = '100%';
    progressFill.style.background = 'var(--success, #4caf50)';

    // Reload cards to get updated flavor text
    const cardsResp = await fetch('/api/cards');
    const cards = await cardsResp.json();
    allCards = cards;
    renderGrid();
    if (selectedCard) {
      const card = allCards.find(c => c.name === selectedCard);
      if (card) updateDetailPanel(card);
    }

  } catch (e) {
    batchMessage.textContent = `Error: ${e.message || 'Unknown'}`;
    progressFill.style.background = 'var(--error, #f44336)';
  } finally {
    enableBulkButtons();
  }
}

async function generateFlavorTextForCard() {
  if (!selectedCard) return;
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  const btn = document.getElementById('btnGenFlavorSingle');
  btn.disabled = true;
  btn.textContent = 'Generating...';

  try {
    const resp = await fetch(`/api/decks/${deckId}/generate-flavor-text`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_names: [selectedCard] }),
    });
    const data = await resp.json();
    if (!data.success) {
      showToast(data.error || 'Failed to generate flavor text', 'error');
      btn.disabled = false;
      btn.textContent = 'Generate';
      return;
    }

    const jobId = data.job_id;
    const poll = setInterval(async () => {
      try {
        const r = await fetch(`/api/flavor-text/progress/${jobId}`);
        if (!r.ok) return;
        const prog = await r.json();
        if (prog.done) {
          clearInterval(poll);
          // Reload cards to get updated flavor text
          const cardsResp = await fetch('/api/cards');
          const cards = await cardsResp.json();
          allCards = cards;
          const card = allCards.find(c => c.name === selectedCard);
          if (card) {
            updateDetailPanel(card);
          }
          renderGrid();
          btn.disabled = false;
          btn.textContent = 'Generate';
        } else {
          btn.textContent = 'Generating...';
        }
      } catch (_) {}
    }, 1000);
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

async function saveFlavorText() {
  if (!selectedCard) return;
  const editField = document.getElementById('detailFlavorEdit');
  const flavor = editField.value.trim();

  try {
    const resp = await fetch('/api/cards/flavor-text', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_name: selectedCard, flavor_text: flavor }),
    });
    const data = await resp.json();
    if (!data.success) {
      showToast(data.error || 'Failed to save flavor text', 'error');
      return;
    }

    // Update local state
    const card = allCards.find(c => c.name === selectedCard);
    if (card) {
      card.flavor_text = flavor;
      updateDetailPanel(card);
    }
    renderGrid();

    // Exit edit mode
    document.getElementById('detailFlavorEditWrap').style.display = 'none';
    document.getElementById('detailFlavorDisplay').style.display = '';
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function generateArt() {
  if (checkedCards.size === 0) return;
  const names = [...checkedCards];

  const cost = getActiveCostPerImage();
  const modelLabel = modelConfig ? modelConfig.options[modelConfig.active]?.label : 'unknown';
  const isLocal = currentMode === 'local';
  const costStr = isLocal
    ? 'Free \u2014 runs on your GPU (~3-4 min/card)'
    : `~$${(cost * names.length).toFixed(2)} (${names.length} cards \u00d7 $${cost})`;

  const result = await showCustomDialog({
    title: `Generate Art for ${names.length} Cards`,
    message: `Using ${modelLabel}`,
    fields: [
      { type: 'textarea', name: 'feedback', label: 'Art Direction (optional)',
        placeholder: 'e.g. darker tones, more dramatic lighting', rows: 3 },
    ],
    cost: costStr,
    confirmText: 'Generate',
  });
  if (!result) return;
  const feedback = result.feedback || '';

  const resp = await fetch('/api/generate-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ card_names: names, skip_existing: false, feedback: feedback || '' }),
  });
  const batchResult = await resp.json();
  if (batchResult.success) {
    // Instant feedback — show progress in action bar
    document.getElementById('batchMessage').textContent = 'Preparing...';
    document.getElementById('actionBarProgress').style.display = 'flex';
    document.getElementById('batchProgressFill').style.width = '0%';
    document.getElementById('btnStop').style.display = '';
    document.getElementById('btnRegenPrompts').style.display = 'none';
    document.getElementById('btnGenFlavor').style.display = 'none';
    document.getElementById('btnGenArt').style.display = 'none';
    document.getElementById('btnRenderFrames').style.display = 'none';
  }
}

async function stopBatch() {
  await fetch('/api/stop-batch', { method: 'POST' });
}

async function recompositeCurrent() {
  if (!selectedCard) return;
  const card = allCards.find(c => c.name === selectedCard);
  const back = viewingBack(card);
  const hasArt = back ? (card && (card.has_back_raw || card.has_back_scryfall_art))
                      : (card && (card.has_raw_art || card.has_scryfall_art));
  if (!hasArt) {
    showToast('No art exists yet — generate art first', 'warning');
    return;
  }
  const resp = await fetch('/api/recomposite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ card_name: selectedCard }),
  });
  const data = await resp.json();
  if (data.success) {
    card.composite_mtime = data.composite_mtime || card.composite_mtime;
    if (data.back_composite_mtime) card.back_composite_mtime = data.back_composite_mtime;
    // Keep showing the face the user is viewing (cache-busted with ITS mtime)
    const img = document.getElementById('detailImage');
    img.src = (back && !card.is_split_halves)
      ? `/api/image/composite/${card.back_slug}?v=${card.back_composite_mtime || 0}`
      : `/api/image/composite/${card.slug}?v=${card.composite_mtime || 0}`;
    showToast('Frame re-rendered', 'success');
  } else {
    showToast('Recomposite error: ' + (data.error || 'Unknown'), 'error');
  }
}

async function renderFrames() {
  if (checkedCards.size === 0) return;
  const names = [...checkedCards];

  const progressFill = document.getElementById('batchProgressFill');
  const batchMessage = document.getElementById('batchMessage');

  disableBulkButtons();
  batchMessage.textContent = `Re-rendering ${names.length} frames...`;
  progressFill.style.width = '';
  progressFill.classList.add('indeterminate');

  try {
    const resp = await fetch('/api/recomposite-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_names: names }),
    });
    const data = await resp.json();
    progressFill.classList.remove('indeterminate');
    progressFill.style.width = '100%';
    batchMessage.textContent = data.message;

    const r = await fetch('/api/cards');
    allCards = await r.json();
    renderGrid();
  } catch (e) {
    progressFill.classList.remove('indeterminate');
    batchMessage.textContent = `Error: ${e.message || 'Unknown'}`;
    progressFill.style.background = 'var(--error, #f44336)';
  } finally {
    enableBulkButtons();
  }
}

// --- WYSIWYG Frame Designer ---
let _frameStyles = {};
let _frameLayerOrder = [];
let _frameLayerMeta = {};
let _frameDeckSettings = {};
let _framePreviewTimer = null;
let _activeFrameStyle = 'basic';
let _fdCompositor = null;  // FrameCompositor instance

const SWATCH_COLORS = {
  'W': '#DBCFAC', 'U': '#3B90B9', 'B': '#323232', 'R': '#BB5540', 'G': '#718971',
  'Au': '#CBA74C', 'Ar': '#969EA3', 'C': '#969EA3', 'La': '#AA8E6F',
};

// ── FrameCompositor: client-side canvas compositor ──
class FrameCompositor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.artImage = null;
    this.frameImage = null;
    this.textImage = null;
    this.artOffset = { x: 0, y: 0 };
    this.artZoom = 1.0;
    this.artBaseScale = 1.0;  // cover-fit scale for the art
    // Battle fronts compose art in LANDSCAPE space then rotate 90° — the
    // designer mirrors that so pan/zoom is WYSIWYG for sideways cards
    this.rotateArt = false;
    this.frameOpacity = 1.0;
    this._dragging = false;
    this._dragStart = { x: 0, y: 0 };
    this._dragStartOffset = { x: 0, y: 0 };
    this._setupPointerEvents();
  }

  _setupPointerEvents() {
    const c = this.canvas.parentElement;  // fd-canvas-container
    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      this._dragging = true;
      this._dragStart = { x: e.clientX, y: e.clientY };
      this._dragStartOffset = { ...this.artOffset };
      c.classList.add('grabbing');
      c.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    c.addEventListener('pointermove', (e) => {
      if (!this._dragging) return;
      // Scale mouse delta to canvas resolution
      const rect = this.canvas.getBoundingClientRect();
      const scaleX = 750 / rect.width;
      const scaleY = 1050 / rect.height;
      const dx = (e.clientX - this._dragStart.x) * scaleX;
      const dy = (e.clientY - this._dragStart.y) * scaleY;
      if (this.rotateArt) {
        // Screen drag → landscape-space offset (art plane is rotated 90°)
        this.artOffset.x = this._dragStartOffset.x - dy;
        this.artOffset.y = this._dragStartOffset.y + dx;
      } else {
        this.artOffset.x = this._dragStartOffset.x + dx;
        this.artOffset.y = this._dragStartOffset.y + dy;
      }
      this.render();
    });
    const endDrag = () => {
      this._dragging = false;
      c.classList.remove('grabbing');
    };
    c.addEventListener('pointerup', endDrag);
    c.addEventListener('pointercancel', endDrag);

    // Wheel zoom
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.05 : 0.05;
      this.artZoom = Math.max(0.3, Math.min(3.0, this.artZoom + delta));
      // Update slider
      const slider = document.getElementById('fdZoomSlider');
      if (slider) slider.value = Math.round(this.artZoom * 100);
      this.render();
    }, { passive: false });
  }

  async loadArt(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        this.artImage = img;
        // Cover-fit scale — battle fronts cover the LANDSCAPE canvas
        this.artBaseScale = this.rotateArt
          ? Math.max(1050 / img.naturalWidth, 750 / img.naturalHeight)
          : Math.max(750 / img.naturalWidth, 1050 / img.naturalHeight);
        resolve();
      };
      img.onerror = reject;
      img.src = url;
    });
  }

  async loadFrame(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => { this.frameImage = img; resolve(); };
      img.onerror = reject;
      img.src = url;
    });
  }

  async loadText(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => { this.textImage = img; resolve(); };
      img.onerror = reject;
      img.src = url;
    });
  }

  render() {
    const ctx = this.ctx;
    const W = 750, H = 1050;
    ctx.clearRect(0, 0, W, H);

    // Layer 0: Dark background (visible when art doesn't cover canvas)
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, W, H);

    // Layer 0b: Art with pan/zoom
    if (this.artImage) {
      const img = this.artImage;
      const scale = this.artBaseScale * this.artZoom;
      const sw = img.naturalWidth * scale;
      const sh = img.naturalHeight * scale;
      if (this.rotateArt) {
        // Compose in landscape space (1050x750), rotated 90° CCW onto the
        // portrait canvas — same math as the server's battle composite
        ctx.save();
        ctx.translate(W / 2, H / 2);
        ctx.rotate(-Math.PI / 2);
        const dx = (1050 - sw) / 2 + this.artOffset.x - 525;
        const dy = (750 - sh) / 2 + this.artOffset.y - 375;
        ctx.drawImage(img, dx, dy, sw, sh);
        ctx.restore();
      } else {
        // Center the art, then apply user offset
        const dx = (W - sw) / 2 + this.artOffset.x;
        const dy = (H - sh) / 2 + this.artOffset.y;
        ctx.drawImage(img, dx, dy, sw, sh);
      }
    }

    // Layer 1: Frame chrome
    if (this.frameImage) {
      ctx.globalAlpha = this.frameOpacity;
      ctx.drawImage(this.frameImage, 0, 0, W, H);
      ctx.globalAlpha = 1.0;
    }

    // Layer 2: Text overlay
    if (this.textImage) {
      ctx.drawImage(this.textImage, 0, 0, W, H);
    }
  }

  resetArtPosition() {
    this.artOffset = { x: 0, y: 0 };
    this.artZoom = 1.0;
    this.render();
  }

  panArt(dx, dy) {
    if (this.rotateArt) {
      this.artOffset.x += -dy;
      this.artOffset.y += dx;
    } else {
      this.artOffset.x += dx;
      this.artOffset.y += dy;
    }
    this.render();
  }

  zoomArt(level) {
    this.artZoom = Math.max(0.3, Math.min(3.0, level));
    this.render();
  }

  getArtState() {
    return { offset: { ...this.artOffset }, zoom: this.artZoom };
  }

  setArtState(offset, zoom) {
    if (offset) this.artOffset = { x: offset.x || 0, y: offset.y || 0 };
    if (zoom != null) this.artZoom = zoom;
  }
}

// ── Frame Designer initialization ──
async function initFrameDesigner() {
  try {
    const [stylesResp, settingsResp] = await Promise.all([
      fetch('/api/frame-styles'),
      fetch(`/api/decks/${document.getElementById('deckSelect').value}/frame-settings`),
    ]);
    const stylesData = await stylesResp.json();
    _frameStyles = stylesData.styles || {};
    _frameLayerOrder = stylesData.layer_order || [];
    _frameLayerMeta = stylesData.layer_meta || {};
    _frameDeckSettings = await settingsResp.json();
  } catch (e) {
    _frameStyles = {};
    _frameLayerOrder = [];
    _frameLayerMeta = {};
    _frameDeckSettings = {};
  }

  // Initialize canvas compositor
  const canvas = document.getElementById('fdCanvas');
  if (canvas) {
    _fdCompositor = new FrameCompositor(canvas);
  }

  renderStyleStrip();
  renderLayerList();
  renderQuickSwatches();
  populateFrameFromSettings(_frameDeckSettings);
  wireFrameInputs();
  wireZoomControls();
}

function renderStyleStrip() {
  const strip = document.getElementById('fdStyleStrip');
  if (!strip) return;
  const deckStyle = _frameDeckSettings && _frameDeckSettings.style;
  strip.innerHTML = '';
  for (const [key, style] of Object.entries(_frameStyles)) {
    const btn = document.createElement('button');
    btn.className = 'fd-style-btn' + (key === _activeFrameStyle ? ' active' : '');
    btn.textContent = style.label;
    btn.title = style.description + (key === deckStyle ? ' — current deck default' : '');
    if (key === deckStyle) {
      const dot = document.createElement('span');
      dot.className = 'deck-default-dot';
      dot.title = 'Current deck default';
      btn.appendChild(dot);
    }
    btn.dataset.styleKey = key;
    btn.addEventListener('click', () => selectFrameStyle(key));
    strip.appendChild(btn);
  }
  updateDeckStyleHint();
}

function updateDeckStyleHint() {
  const hint = document.getElementById('fdDeckStyleHint');
  if (!hint) return;
  const deckStyle = _frameDeckSettings && _frameDeckSettings.style;
  const label = (deckStyle && _frameStyles[deckStyle]) ? _frameStyles[deckStyle].label : 'not set';
  hint.innerHTML = `Deck default: <b>${escapeHtml(label)}</b> — used by new imports ` +
    `and cards without their own saved frame.` +
    (selectedCard ? ' Save Frame only affects this card.' : '');
}

function renderLayerList() {
  const container = document.getElementById('fdLayerList');
  if (!container) return;
  container.innerHTML = '';
  for (const key of _frameLayerOrder) {
    const meta = _frameLayerMeta[key] || {};
    const row = document.createElement('div');
    row.className = 'frame-layer-row';
    row.id = 'frameLayer_' + key;
    row.innerHTML =
      '<span class="frame-layer-name">' + escapeHtml(meta.label || key) + '</span>' +
      '<input type="checkbox" class="frame-layer-vis" id="frameVis_' + key + '" title="Toggle visibility">' +
      '<input type="range" class="frame-layer-slider" id="frameOpacity_' + key + '" min="0" max="100" step="1" value="0">' +
      '<span class="frame-layer-val" id="frameVal_' + key + '">0.00</span>';
    container.appendChild(row);
  }
}

function renderQuickSwatches() {
  const container = document.getElementById('frameQuickSwatches');
  if (!container) return;
  container.innerHTML = '';
  for (const [label, color] of Object.entries(SWATCH_COLORS)) {
    const swatch = document.createElement('div');
    swatch.className = 'frame-swatch';
    swatch.style.background = color;
    swatch.title = label;
    swatch.addEventListener('click', () => applySwatchColor(label, color));
    container.appendChild(swatch);
  }
}

function applySwatchColor(label, color) {
  const themeMap = {
    'W':  {bg:'#DBCFAC',field:'#F2F1EF',textbox:'#F2F2F1',border:'#F6FCFC',text:'#000'},
    'U':  {bg:'#3B90B9',field:'#A9CCE5',textbox:'#D2E4F4',border:'#1971CE',text:'#000'},
    'B':  {bg:'#323232',field:'#BAB4B5',textbox:'#DFDEDE',border:'#403232',text:'#000'},
    'R':  {bg:'#BB5540',field:'#FFE0D3',textbox:'#FFEAE2',border:'#C5432B',text:'#000'},
    'G':  {bg:'#718971',field:'#CFDDCD',textbox:'#E2E5E0',border:'#324F33',text:'#000'},
    'Au': {bg:'#CBA74C',field:'#DCBB78',textbox:'#FCF4DF',border:'#D9CC71',text:'#000'},
    'Ar': {bg:'#969EA3',field:'#D5DAE1',textbox:'#DFE3E4',border:'#F0F3F2',text:'#000'},
    'C':  {bg:'#969EA3',field:'#DFDEDE',textbox:'#DFDEDE',border:'#E7E8E2',text:'#000'},
    'La': {bg:'#AA8E6F',field:'#D5CCC0',textbox:'#E4DDD4',border:'#7A6B55',text:'#000'},
  };
  const theme = themeMap[label];
  if (!theme) return;
  setColorInputs(theme);
  scheduleFramePreview();
}

function setColorInputs(theme) {
  const pairs = [['Bg', 'bg'], ['Field', 'field'], ['Textbox', 'textbox'], ['Border', 'border'], ['Text', 'text']];
  for (const [suffix, key] of pairs) {
    const picker = document.getElementById('frameColor' + suffix);
    const hex = document.getElementById('frameColor' + suffix + 'Hex');
    if (picker && theme[key]) { picker.value = theme[key]; }
    if (hex && theme[key]) { hex.value = theme[key]; }
  }
}

// Which frame layer(s) each color picker actually drives (SVG styles).
// A picker row only shows while at least one of its layers is visible, so
// users never see a control that does nothing (e.g. classic hides its Frame
// layer by default -> no Frame picker until the layer is enabled).
// Border also drives the P/T box fill, so it stays while pt_box is visible.
const COLOR_ROW_LAYERS = {
  Bg: ['frame'],
  Field: ['title_bar', 'type_bar'],
  Textbox: ['text_box'],
  Border: ['border', 'pt_box'],
  Text: [],  // text always renders
};

function updateColorRowVisibility() {
  const style = _frameStyles[_activeFrameStyle] || {};
  for (const [suffix, layerKeys] of Object.entries(COLOR_ROW_LAYERS)) {
    const row = document.getElementById('frameColorRow' + suffix);
    if (!row) continue;
    let show;
    if (style.mode === 'image') {
      show = ((style.controls || {}).colors || []).includes(suffix.toLowerCase());
    } else {
      show = layerKeys.length === 0 || layerKeys.some(lk => {
        const vis = document.getElementById('frameVis_' + lk);
        return vis ? vis.checked : false;
      });
    }
    row.style.display = show ? '' : 'none';
  }
}

function selectFrameStyle(key) {
  _activeFrameStyle = key;

  // Update style strip active state
  document.querySelectorAll('.fd-style-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.styleKey === key);
  });

  const style = _frameStyles[key];
  if (!style) return;
  const layers = style.layers || {};

  // Update layer controls for SVG styles
  if (style.mode !== 'image') {
    for (const lk of _frameLayerOrder) {
      const layerCfg = layers[lk] || {};
      setLayerControls(lk, layerCfg.visible || false, layerCfg.opacity || 0);
    }
  }

  // Show/hide sections
  const layerSection = document.getElementById('fdLayerSection');
  const colorSection = document.getElementById('fdColorSection');
  if (style.no_frame || style.mode === 'image') {
    if (layerSection) layerSection.style.display = 'none';
    if (colorSection) colorSection.style.display = style.no_frame ? 'none' : '';
  } else {
    if (layerSection) layerSection.style.display = '';
    if (colorSection) colorSection.style.display = '';
  }

  // Per-style controls (from FRAME_STYLES metadata): only show settings the
  // renderer actually honors for this style — no dead controls.
  const controls = style.controls || {};
  updateColorRowVisibility();
  const showcaseRow = document.getElementById('frameShowcaseRow');
  if (showcaseRow) showcaseRow.style.display = controls.showcase ? '' : 'none';
  const boxOpRow = document.getElementById('frameBoxOpacityRow');
  if (boxOpRow) boxOpRow.style.display = controls.box_opacity ? '' : 'none';
  const bottomMaskRow = document.getElementById('frameBottomMaskRow');
  if (bottomMaskRow) bottomMaskRow.style.display = controls.bottom_mask ? '' : 'none';
  // Rules Text Size applies to every frame style (they all have rules text).
  const rulesSizeRow = document.getElementById('frameRulesSizeRow');
  if (rulesSizeRow) rulesSizeRow.style.display = style.no_frame ? 'none' : '';

  // Reload frame layer on canvas
  loadFrameLayerForCanvas();
}

function setLayerControls(key, visible, opacity) {
  const vis = document.getElementById('frameVis_' + key);
  const slider = document.getElementById('frameOpacity_' + key);
  const val = document.getElementById('frameVal_' + key);
  const row = document.getElementById('frameLayer_' + key);
  if (vis) vis.checked = visible;
  if (slider) slider.value = Math.round(opacity * 100);
  if (val) val.textContent = opacity.toFixed(2);
  if (row) row.classList.toggle('disabled', !visible);
}

function populateFrameFromSettings(settings) {
  const styleMap = {classic:'basic', basic:'basic', modern:'m15', borderless:'basic',
                    minimal:'basic', 'full-art':'clean', nyx:'m15',
                    vintage:'m15', retro:'m15', frameless:'clean', clean:'clean',
                    m15:'m15'};
  if (settings.preset && !settings.style) {
    selectFrameStyle(styleMap[settings.preset] || 'basic');
  } else {
    const rawStyle = settings.style || 'basic';
    selectFrameStyle(styleMap[rawStyle] || rawStyle);
  }

  if (settings.layers) {
    for (const [key, cfg] of Object.entries(settings.layers)) {
      setLayerControls(key, cfg.visible || false, cfg.opacity || 0);
    }
    updateColorRowVisibility();  // saved layer visibility gates the pickers
  }

  const autoColors = settings.use_card_colors !== false;
  const autoEl = document.getElementById('frameAutoColors');
  if (autoEl) autoEl.checked = autoColors;
  toggleFrameAutoColors();

  if (settings.color_overrides && Object.keys(settings.color_overrides).length) {
    setColorInputs(settings.color_overrides);
  }

  setFrameGradient(settings.frame_gradient || 'auto');
  const boxOpEl = document.getElementById('frameBoxOpacity');
  if (boxOpEl) {
    // Per-style defaults when unset: crystal deepens its stone box to 0.84,
    // godzilla's approved cream box is ~0.93.
    const defOp = _activeFrameStyle === 'crystal' ? 0.84 : 0.93;
    boxOpEl.value = Math.round((settings.box_opacity != null ? settings.box_opacity : defOp) * 100);
  }
  const rulesSzEl = document.getElementById('frameRulesSize');
  if (rulesSzEl) {
    rulesSzEl.value = settings.rules_font_size != null ? settings.rules_font_size : 30;
    const rulesSzVal = document.getElementById('frameRulesSizeVal');
    if (rulesSzVal) rulesSzVal.textContent = rulesSzEl.value + 'pt';
  }
  const bottomMaskEl = document.getElementById('frameBottomMask');
  if (bottomMaskEl) bottomMaskEl.checked = settings.bottom_mask !== false;  // default on
}

function wireFrameInputs() {
  for (const key of _frameLayerOrder) {
    const vis = document.getElementById('frameVis_' + key);
    const slider = document.getElementById('frameOpacity_' + key);
    if (vis) vis.addEventListener('change', () => {
      const row = document.getElementById('frameLayer_' + key);
      if (row) row.classList.toggle('disabled', !vis.checked);
      updateColorRowVisibility();  // color pickers follow their layer
      scheduleFramePreview();
    });
    if (slider) slider.addEventListener('input', () => {
      const val = document.getElementById('frameVal_' + key);
      if (val) val.textContent = (slider.value / 100).toFixed(2);
      scheduleFramePreview();
    });
  }
  const pairs = ['Bg', 'Field', 'Textbox', 'Border', 'Text'];
  for (const suffix of pairs) {
    const picker = document.getElementById('frameColor' + suffix);
    const hex = document.getElementById('frameColor' + suffix + 'Hex');
    if (picker && hex) {
      picker.addEventListener('input', () => { hex.value = picker.value; scheduleFramePreview(); });
      hex.addEventListener('change', () => {
        if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) { picker.value = hex.value; scheduleFramePreview(); }
      });
    }
  }
  // Text override inputs → debounced preview
  ['frameOverrideShowcase','frameOverrideName','frameOverrideMana','frameOverrideType',
   'frameOverrideOracle','frameOverridePower','frameOverrideToughness'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => scheduleFramePreview());
  });
  // Intensity master slider — proportionally scales all visible layer opacities
  const intensitySlider = document.getElementById('fdIntensity');
  if (intensitySlider) {
    intensitySlider.addEventListener('input', () => {
      const pct = parseInt(intensitySlider.value);
      const valEl = document.getElementById('fdIntensityVal');
      if (valEl) valEl.textContent = pct + '%';
      const style = _frameStyles[_activeFrameStyle];
      if (!style || style.mode === 'image') return;
      const defaults = style.layers || {};
      for (const key of _frameLayerOrder) {
        const def = defaults[key] || {};
        if (!def.visible) continue;
        const baseOpacity = def.opacity || 0;
        const scaled = baseOpacity * (pct / 100);
        const slider = document.getElementById('frameOpacity_' + key);
        const val = document.getElementById('frameVal_' + key);
        if (slider) slider.value = Math.round(scaled * 100);
        if (val) val.textContent = scaled.toFixed(2);
      }
      scheduleFramePreview();
    });
  }
}

function wireZoomControls() {
  const slider = document.getElementById('fdZoomSlider');
  const zoomIn = document.getElementById('fdZoomIn');
  const zoomOut = document.getElementById('fdZoomOut');
  const zoomFit = document.getElementById('fdZoomFit');
  const zoomReset = document.getElementById('fdZoomReset');

  if (slider) slider.addEventListener('input', () => {
    if (_fdCompositor) {
      _fdCompositor.zoomArt(parseInt(slider.value) / 100);
    }
  });
  if (zoomIn) zoomIn.addEventListener('click', () => {
    if (_fdCompositor) {
      _fdCompositor.artZoom = Math.min(3.0, _fdCompositor.artZoom + 0.1);
      if (slider) slider.value = Math.round(_fdCompositor.artZoom * 100);
      _fdCompositor.render();
    }
  });
  if (zoomOut) zoomOut.addEventListener('click', () => {
    if (_fdCompositor) {
      _fdCompositor.artZoom = Math.max(0.3, _fdCompositor.artZoom - 0.1);
      if (slider) slider.value = Math.round(_fdCompositor.artZoom * 100);
      _fdCompositor.render();
    }
  });
  if (zoomFit) zoomFit.addEventListener('click', () => {
    if (_fdCompositor) {
      _fdCompositor.artZoom = 1.0;
      _fdCompositor.artOffset = { x: 0, y: 0 };
      if (slider) slider.value = 100;
      _fdCompositor.render();
    }
  });
  if (zoomReset) zoomReset.addEventListener('click', () => {
    if (_fdCompositor) {
      _fdCompositor.resetArtPosition();
      if (slider) slider.value = 100;
    }
  });
}

function toggleFdSection(id) {
  const body = document.getElementById(id);
  const arrow = document.getElementById(id + 'Arrow');
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : '';
  if (arrow) arrow.classList.toggle('open', !isOpen);
}

function toggleFrameAutoColors() {
  const autoEl = document.getElementById('frameAutoColors');
  if (!autoEl) return;
  const auto = autoEl.checked;
  const swatches = document.getElementById('frameQuickSwatches');
  const inputs = document.getElementById('frameColorInputs');
  if (swatches) swatches.style.display = auto ? 'none' : 'flex';
  if (inputs) inputs.style.display = auto ? 'none' : 'flex';
  scheduleFramePreview();
}

// ── Canvas layer loading ──
async function toggleOversizedGeneration(enabled) {
  const deckId = document.getElementById('deckSelect').value;
  await fetch(`/api/decks/${deckId}/oversized-generation`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled}),
  });
}

async function loadOversizedToggleState() {
  const deckId = document.getElementById('deckSelect').value;
  try {
    const resp = await fetch(`/api/decks/${deckId}/oversized-generation`);
    const data = await resp.json();
    const cb = document.getElementById('fdOversizedToggle');
    if (cb) cb.checked = !!data.oversized_generation;
  } catch(e) {}
}

function togglePromptCollapse() {
  const el = document.getElementById('collapsiblePrompt');
  el.classList.toggle('collapsed');
  el.dataset.userToggled = 'true';
}

async function regenSceneDirection() {
  if (!selectedCard) return;
  const deckId = document.getElementById('deckSelect').value;
  const el = document.getElementById('detailSubject');
  const oldVal = el.value;
  el.value = 'Generating...';
  el.disabled = true;
  try {
    const resp = await fetch(`/api/decks/${deckId}/regen-subject`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_name: selectedCard }),
    });
    const data = await resp.json();
    if (data.success && data.subject) {
      el.value = data.subject;
      const card = allCards.find(c => c.name === selectedCard);
      if (card) {
        card.distilled_subject = data.subject;
        if (card.has_ai_art) card.prompt_stale = true;
        updateDetailPanel(card);
      }
      showToast('Local prompt refreshed', 'success');
    } else {
      el.value = oldVal;
      showToast(data.error || 'Failed to regenerate', 'error');
    }
  } catch (err) {
    el.value = oldVal;
    showToast('Failed to regenerate local prompt', 'error');
  } finally {
    el.disabled = false;
  }
}

async function setArtOrientation(orientation) {
  const deckId = document.getElementById('deckSelect').value;
  await fetch(`/api/decks/${deckId}/art-orientation`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ orientation }),
  });
  document.querySelectorAll('.art-orient-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.orient === orientation);
  });
}

async function loadArtOrientation() {
  const deckId = document.getElementById('deckSelect').value;
  try {
    const resp = await fetch(`/api/decks/${deckId}/art-orientation`);
    const data = await resp.json();
    const orient = data.art_orientation || 'portrait';
    document.querySelectorAll('.art-orient-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.orient === orient);
    });
  } catch(e) {}
}

async function loadFrameDesignerForCard(cardName) {
  if (!_fdCompositor || !cardName) return;
  const card = allCards.find(c => c.name === cardName);
  if (!card) return;

  const emptyEl = document.getElementById('fdEmpty');
  const canvasWrap = document.getElementById('fdCanvasWrap');
  const loading = document.getElementById('fdCanvasLoading');
  const nameEl = document.getElementById('fdCardName');

  // Face-aware context: on a DFC's back face the designer edits the back's
  // own art, text, and override set (frame_overrides_back).
  const fdBack = viewingBack(card);
  const fdFace = fdBack ? (card.back_face || {}) : null;
  const fdSlug = faceSlugFor(card);

  if (emptyEl) emptyEl.style.display = 'none';
  if (canvasWrap) canvasWrap.style.display = '';
  if (nameEl) nameEl.textContent = fdBack ? `${fdFace.name || card.name} (back face)` : card.name;
  if (loading) loading.classList.add('visible');

  // Battle fronts compose sideways — mirror that in the art layer
  _fdCompositor.rotateArt = (!fdBack && card.card_type === 'battle');

  // Show the card's EFFECTIVE frame: deck default with this card's saved
  // style/color overrides layered on top
  const _co = (fdBack ? card.back_frame_overrides : card.frame_overrides) || {};
  const _eff = { ...(_frameDeckSettings || {}) };
  for (const k of Object.keys(_co)) {
    if (!['text_overrides', 'art_offset', 'art_zoom'].includes(k)) _eff[k] = _co[k];
  }
  populateFrameFromSettings(_eff);

  // Load saved art position
  const ovr = (fdBack ? card.back_frame_overrides : card.frame_overrides) || {};
  if (ovr.art_offset || ovr.art_zoom) {
    _fdCompositor.setArtState(ovr.art_offset, ovr.art_zoom || 1.0);
  } else {
    _fdCompositor.setArtState({ x: 0, y: 0 }, 1.0);
  }
  const slider = document.getElementById('fdZoomSlider');
  if (slider) slider.value = Math.round(_fdCompositor.artZoom * 100);

  // Populate text overrides
  populateTextOverrides(card);

  // Load art
  try {
    const _hasRaw = fdBack ? card.has_back_raw : card.has_raw_art;
    const _hasScry = fdBack ? card.has_back_scryfall_art : card.has_scryfall_art;
    const artUrl = _hasRaw
      ? `/api/image/raw/${fdSlug}?t=${Date.now()}`
      : _hasScry
        ? `/api/image/scryfall/${fdSlug}`
        : null;
    if (artUrl) {
      await _fdCompositor.loadArt(artUrl);
    } else {
      _fdCompositor.artImage = null;
    }
  } catch (e) {
    _fdCompositor.artImage = null;
  }

  // Show art size indicator
  const artSizeEl = document.getElementById('fdArtSize');
  if (artSizeEl && _fdCompositor.artImage) {
    const aw = _fdCompositor.artImage.naturalWidth;
    const ah = _fdCompositor.artImage.naturalHeight;
    const ratio = Math.min(aw / 750, ah / 1050);
    if (ratio > 1.15) {
      artSizeEl.textContent = `${aw}\u00d7${ah} (${Math.round(ratio * 100 - 100)}% extra)`;
      artSizeEl.style.display = '';
    } else {
      artSizeEl.textContent = `${aw}\u00d7${ah}`;
      artSizeEl.style.display = '';
    }
  }

  // Load frame + text layers
  await loadFrameLayerForCanvas();
  if (loading) loading.classList.remove('visible');

  // If nothing loaded, the canvas is a silent black box — say what happened
  // (most often the Flask server isn't running / was restarted).
  if (!_fdCompositor.artImage && !_fdCompositor.frameImage) {
    showToast('Frame preview failed to load — is the server running? Try refreshing.', 'error');
  }
}

async function loadFrameLayerForCanvas() {
  if (!_fdCompositor || !selectedCard) return;
  const settings = gatherFrameSettings();
  // "<name> [back]" targets a DFC's back face
  const _fdKey = faceKeyFor(allCards.find(c => c.name === selectedCard));

  try {
    // Fetch frame layer
    const frameResp = await fetch('/api/render-frame-layer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_name: _fdKey, frame_settings: settings }),
    });
    if (frameResp.ok) {
      const blob = await frameResp.blob();
      await _fdCompositor.loadFrame(URL.createObjectURL(blob));
    } else {
      _fdCompositor.frameImage = null;
    }
  } catch (e) {
    _fdCompositor.frameImage = null;
  }

  try {
    // Fetch text overlay
    const textResp = await fetch('/api/render-text-overlay', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_name: _fdKey, frame_settings: settings }),
    });
    if (textResp.ok) {
      const blob = await textResp.blob();
      await _fdCompositor.loadText(URL.createObjectURL(blob));
    } else {
      _fdCompositor.textImage = null;
    }
  } catch (e) {
    _fdCompositor.textImage = null;
  }

  _fdCompositor.render();
}

function populateTextOverrides(card) {
  // On a DFC's back face, placeholders and saved overrides come from the
  // back face's data + its own override set
  const _back = viewingBack(card);
  const _src = _back ? (card.back_face || {}) : card;
  const showcaseEl = document.getElementById('frameOverrideShowcase');
  if (showcaseEl) showcaseEl.value = '';
  const nameEl = document.getElementById('frameOverrideName');
  const manaEl = document.getElementById('frameOverrideMana');
  const typeEl = document.getElementById('frameOverrideType');
  const oracleEl = document.getElementById('frameOverrideOracle');
  const powerEl = document.getElementById('frameOverridePower');
  const toughEl = document.getElementById('frameOverrideToughness');
  if (nameEl) { nameEl.placeholder = _src.name || ''; nameEl.value = ''; }
  if (manaEl) { manaEl.placeholder = _src.mana_cost || ''; manaEl.value = ''; }
  if (typeEl) { typeEl.placeholder = _src.type_line || ''; typeEl.value = ''; }
  if (oracleEl) { oracleEl.placeholder = _src.oracle_text || ''; oracleEl.value = ''; }
  if (powerEl) { powerEl.placeholder = _src.power || ''; powerEl.value = ''; }
  if (toughEl) { toughEl.placeholder = _src.toughness || ''; toughEl.value = ''; }

  const ovr = (_back ? card.back_frame_overrides : card.frame_overrides) || {};
  const textOvr = ovr.text_overrides || {};
  // Restore the saved showcase name too — omitting it meant the next save
  // wholesale-replaced frame_overrides WITHOUT it, silently deleting it.
  if (textOvr.showcase_name && showcaseEl) showcaseEl.value = textOvr.showcase_name;
  if (textOvr.name && nameEl) nameEl.value = textOvr.name;
  if (textOvr.mana_cost && manaEl) manaEl.value = textOvr.mana_cost;
  if (textOvr.type_line && typeEl) typeEl.value = textOvr.type_line;
  if (textOvr.oracle_text && oracleEl) oracleEl.value = textOvr.oracle_text;
  if (textOvr.power && powerEl) powerEl.value = textOvr.power;
  if (textOvr.toughness && toughEl) toughEl.value = textOvr.toughness;
}

let _frameGradient = 'auto';
function setFrameGradient(mode) {
  _frameGradient = mode;
  document.querySelectorAll('#fdGradientSeg .fd-seg-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.grad === mode));
  scheduleFramePreview();
}

function gatherFrameSettings() {
  const settings = {
    style: _activeFrameStyle,
    frame_gradient: _frameGradient,
  };
  const style = _frameStyles[_activeFrameStyle];
  // Box transparency: only persisted for styles whose renderer honors it
  // (godzilla, crystal) — avoids polluting other styles' settings.
  const boxOpEl = document.getElementById('frameBoxOpacity');
  if (boxOpEl && style && style.controls && style.controls.box_opacity) {
    settings.box_opacity = parseInt(boxOpEl.value) / 100;
  }
  const bottomMaskEl = document.getElementById('frameBottomMask');
  if (bottomMaskEl && style && style.controls && style.controls.bottom_mask) {
    settings.bottom_mask = bottomMaskEl.checked;
  }
  const rulesSzEl = document.getElementById('frameRulesSize');
  if (rulesSzEl) settings.rules_font_size = parseInt(rulesSzEl.value);

  if (style && style.mode !== 'image') {
    settings.layers = {};
    for (const key of _frameLayerOrder) {
      const vis = document.getElementById('frameVis_' + key);
      const slider = document.getElementById('frameOpacity_' + key);
      settings.layers[key] = {
        visible: vis ? vis.checked : false,
        opacity: slider ? parseInt(slider.value) / 100 : 0,
      };
    }
  }

  const autoEl = document.getElementById('frameAutoColors');
  const useCardColors = autoEl ? autoEl.checked : true;
  settings.use_card_colors = useCardColors;

  if (!useCardColors) {
    settings.color_overrides = {};
    // Only persist color keys this style's renderer actually honors.
    const styleControls = (style && style.controls) || {};
    const supported = styleControls.colors ||
      (style && style.mode === 'image' ? [] : ['bg', 'field', 'textbox', 'border', 'text']);
    const pairs = [['Bg','bg'],['Field','field'],['Textbox','textbox'],['Border','border'],['Text','text']];
    for (const [suffix, key] of pairs) {
      const picker = document.getElementById('frameColor' + suffix);
      if (picker && supported.includes(key)) settings.color_overrides[key] = picker.value;
    }
  }

  if (selectedCard) {
    const textOvr = {};
    const nameVal = document.getElementById('frameOverrideName')?.value?.trim();
    const manaVal = document.getElementById('frameOverrideMana')?.value?.trim();
    const typeVal = document.getElementById('frameOverrideType')?.value?.trim();
    const oracleVal = document.getElementById('frameOverrideOracle')?.value?.trim();
    const powerVal = document.getElementById('frameOverridePower')?.value?.trim();
    const toughVal = document.getElementById('frameOverrideToughness')?.value?.trim();
    const showcaseVal = document.getElementById('frameOverrideShowcase')?.value?.trim();
    if (nameVal) textOvr.name = nameVal;
    if (manaVal) textOvr.mana_cost = manaVal;
    if (typeVal) textOvr.type_line = typeVal;
    if (oracleVal) textOvr.oracle_text = oracleVal;
    if (powerVal) textOvr.power = powerVal;
    if (toughVal) textOvr.toughness = toughVal;
    if (showcaseVal) textOvr.showcase_name = showcaseVal;
    // Always attach (even empty): the live designer state is authoritative
    // for previews — a cleared field must clear, not fall back to the saved
    // per-card override.
    settings.text_overrides = textOvr;
  }

  return settings;
}

function scheduleFramePreview() {
  clearTimeout(_framePreviewTimer);
  _framePreviewTimer = setTimeout(() => {
    if (!selectedCard || !_fdCompositor) return;
    // Reload frame + text layers on canvas
    loadFrameLayerForCanvas();
  }, 300);
}

function syncFdFaceToggle(card) {
  const toggle = document.getElementById('fdFaceToggle');
  if (!toggle) return;
  toggle.style.display = (card && (card.is_dfc || card.is_split_halves)) ? 'flex' : 'none';
  const fBtn = document.getElementById('fdFaceBtnFront');
  const bBtn = document.getElementById('fdFaceBtnBack');
  const names = (card && card.is_split_halves && card.face_names) ? card.face_names : null;
  if (fBtn) fBtn.textContent = names ? names[0] : 'Front';
  if (bBtn) bBtn.textContent = names ? names[1] : 'Back';
  if (fBtn) fBtn.classList.toggle('active', selectedFace !== 'back');
  if (bBtn) bBtn.classList.toggle('active', selectedFace === 'back');
  const hintEl = document.getElementById('fdFaceHint');
  if (hintEl) {
    const hint = faceHintFor(card);
    hintEl.textContent = hint;
    hintEl.style.display = hint ? '' : 'none';
  }
}

function updateFrameTab() {
  const emptyEl = document.getElementById('fdEmpty');
  const canvasWrap = document.getElementById('fdCanvasWrap');

  if (emptyEl) emptyEl.style.display = 'none';
  syncFdFaceToggle(allCards.find(c => c.name === selectedCard));
  if (selectedCard) {
    _setFdDeckMode(false);
    loadFrameDesignerForCard(selectedCard);
  } else {
    // No card selected: the Frame tab edits the DECK DEFAULT frame
    if (canvasWrap) canvasWrap.style.display = '';
    _setFdDeckMode(true);
    populateFrameFromSettings(_frameDeckSettings || {});
    renderStyleStrip();
  }
}

function _setFdDeckMode(on) {
  // Card-specific chrome hidden while editing the deck default
  for (const id of ['fdCanvasContainer', 'fdZoomBar', 'fdTextSection', 'fdCardName']) {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? 'none' : '';
  }
  if (on) {
    const ft = document.getElementById('fdFaceToggle');
    const fh = document.getElementById('fdFaceHint');
    if (ft) ft.style.display = 'none';
    if (fh) fh.style.display = 'none';
  }
  const saveBtn = document.getElementById('frameSaveBtn');
  const applyBtn = document.getElementById('frameApplyAllBtn');
  const deckBtn = document.getElementById('frameDeckDefaultBtn');
  if (saveBtn) saveBtn.style.display = on ? 'none' : '';
  if (applyBtn) applyBtn.style.display = on ? 'none' : '';
  if (deckBtn) deckBtn.style.display = on ? '' : 'none';
}

function resetTextOverrides() {
  ['frameOverrideShowcase','frameOverrideName','frameOverrideMana','frameOverrideType',
   'frameOverrideOracle','frameOverridePower','frameOverrideToughness'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  scheduleFramePreview();
}

// Persist the selected card's frame state as PER-CARD overrides: the full
// designer settings (style, colors, gradient, opacity, layers) plus text
// overrides and art pan/zoom. The deck default is never touched here.
async function persistSelectedCardFrameState(textOverrides, frameSettings) {
  if (!selectedCard) return;
  const cardOverrides = frameSettings ? { ...frameSettings } : {};
  delete cardOverrides.text_overrides;
  if (textOverrides && Object.keys(textOverrides).length) {
    cardOverrides.text_overrides = textOverrides;
  }
  if (_fdCompositor) {
    const artState = _fdCompositor.getArtState();
    cardOverrides.art_offset = artState.offset;
    cardOverrides.art_zoom = artState.zoom;
  }
  if (Object.keys(cardOverrides).length) {
    await fetch('/api/cards/frame-overrides', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        card_name: faceKeyFor(allCards.find(c => c.name === selectedCard)),
        frame_overrides: cardOverrides,
      }),
    });
  }
}

async function saveFrameSettings() {
  // Saves THIS CARD's frame as a per-card override. The deck default is a
  // separate, explicit action (Set Deck Default).
  if (!selectedCard) {
    showToast('Select a card first — or use Set Deck Default', 'warning');
    return;
  }
  const settings = gatherFrameSettings();
  const textOverrides = settings.text_overrides;
  delete settings.text_overrides;

  try {
    await persistSelectedCardFrameState(textOverrides, settings);

    // Re-render composite on server (archive previous as version).
    // On a DFC only the face being edited re-renders.
    const _svCard = allCards.find(c => c.name === selectedCard);
    const resp = await fetch('/api/recomposite', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_name: selectedCard, archive_version: true,
                             face: (_svCard && _svCard.is_dfc) ? selectedFace : 'all' }),
    });
    const data = await resp.json();
    if (data.success) {
      showToast('Frame saved for this card', 'success');
    } else {
      // Overrides persisted but the composite didn't re-render — say so
      // instead of failing silently (looks like "my changes didn't save").
      showToast('Frame saved, but re-render failed: ' + (data.error || 'unknown error'), 'error');
    }

    // Refresh card list to update grid thumbnails
    const r = await fetch('/api/cards');
    allCards = await r.json();
    renderGrid();
  } catch (e) {
    showToast('Error saving frame settings', 'error');
  }
}

async function setDeckDefaultFrame() {
  // Explicitly set the DECK default frame (used by new imports and any card
  // without its own saved frame). Never touches per-card overrides.
  const settings = gatherFrameSettings();
  delete settings.text_overrides;
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;
  try {
    await fetch(`/api/decks/${deckId}/frame-settings`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(settings),
    });
    _frameDeckSettings = settings;
    renderStyleStrip();  // refresh the deck-default badge + hint
    const lbl = _frameStyles[settings.style] ? _frameStyles[settings.style].label : settings.style;
    showToast(`Deck default set to ${lbl} — new imports and cards without their own frame use it`, 'info');
  } catch (e) {
    showToast('Error saving deck default', 'error');
  }
}

async function applyFrameToChecked() {
  if (checkedCards.size === 0) {
    showToast('No cards checked', 'warning');
    return;
  }
  const settings = gatherFrameSettings();
  const textOverrides = settings.text_overrides;
  delete settings.text_overrides;

  // Write the designer settings onto each CHECKED card as per-card
  // overrides, preserving that card's own text overrides and art position.
  // The deck default is untouched (use Set Deck Default for that).
  for (const n of checkedCards) {
    if (n === selectedCard) continue;  // handled below with live art state
    const c = allCards.find(x => x.name === n);
    const merged = { ...((c && c.frame_overrides) || {}), ...settings };
    await fetch('/api/cards/frame-overrides', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_name: n, frame_overrides: merged }),
    }).catch(() => {});
  }

  // Persist the selected card's live state (incl. art pan/zoom) BEFORE
  // recompositing, so a repositioned art doesn't silently revert if that
  // card is checked — but ONLY when it IS checked; otherwise an unsaved
  // experiment on the selected card would be silently made permanent by an
  // unrelated batch apply.
  if (selectedCard && checkedCards.has(selectedCard)) {
    await persistSelectedCardFrameState(textOverrides, settings);
  }

  const names = [...checkedCards];
  const resp = await fetch('/api/recomposite-all', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ card_names: names }),
  });
  const data = await resp.json();
  if (data.success) {
    showToast(`Re-rendered ${data.count} frames`, 'success');
  } else {
    showToast('Error: ' + (data.error || 'Unknown'), 'error');
  }
  const r = await fetch('/api/cards');
  allCards = await r.json();
  renderGrid();
}

// --- Version History ---
let selectedVersion = null;  // version number for modal restore
let _versionPreviewTimer = null;  // hover delay timer

async function loadVersionHistory(cardName, slug) {
  const strip = document.getElementById('versionStrip');
  selectedVersion = null;

  try {
    const resp = await fetch(`/api/versions/${encodeURIComponent(cardName)}`);
    const data = await resp.json();
    const versions = data.versions || [];

    if (versions.length === 0) {
      strip.innerHTML = '<span class="no-versions">No previous versions yet</span>';
      return;
    }

    // Build thumbnails — newest first ("<name> [back]" keys map to the card's back face)
    const isBackKey = cardName.endsWith(' [back]');
    const currentCard = allCards.find(c => c.name === cardName.replace(' [back]', ''));
    const currentMtime = (isBackKey ? currentCard?.back_composite_mtime
                                    : currentCard?.composite_mtime) || 0;
    let html = '';
    // Current version first (marked as "Current")
    if (data.has_current) {
      html += `
        <div class="version-thumb active" id="vthumb-current"
             data-version="" data-slug="${slug}" data-label="Current">
          <img src="/api/image/composite/${slug}?v=${currentMtime}" alt="Current">
          <div class="version-thumb-label">Current</div>
        </div>`;
    }
    // Archived versions, newest first
    for (let i = versions.length - 1; i >= 0; i--) {
      const v = versions[i];
      const shortModel = (v.model || '').replace('gpt-image-1', 'gpt-img').replace('dall-e-3', 'de3');
      const label = `v${v.version} — ${shortModel} ${v.quality || ''}`.trim();
      html += `
        <div class="version-thumb" id="vthumb-${v.version}"
             data-version="${v.version}" data-slug="${slug}" data-label="${escapeHtml(label)}">
          <img src="/api/image/version/${slug}/${v.version}" alt="v${v.version}">
          <div class="version-thumb-label">
            v${v.version}
            <span class="ver-model">${escapeHtml(shortModel)} ${escapeHtml(v.quality || '')}</span>
          </div>
        </div>`;
    }
    // Add "Delete All Old" link if 2+ archived versions
    if (versions.length >= 2) {
      html += `<div class="version-delete-all" id="vDeleteAll">
        <a href="#">Delete Old</a>
      </div>`;
    }
    strip.innerHTML = html;

    // Attach hover + click listeners (avoids inline onclick with card names)
    strip.querySelectorAll('.version-thumb').forEach(thumb => {
      const vNum = thumb.dataset.version === '' ? null : parseInt(thumb.dataset.version);
      const s = thumb.dataset.slug;
      const label = thumb.dataset.label;
      thumb.addEventListener('click', () => openVersionModal(vNum, s, label));
      thumb.addEventListener('mouseenter', (e) => showVersionPreview(e, thumb));
      thumb.addEventListener('mouseleave', hideVersionPreview);
    });

    // "Delete All Old" click handler
    const delAllEl = document.getElementById('vDeleteAll');
    if (delAllEl) {
      delAllEl.querySelector('a').addEventListener('click', (e) => {
        e.preventDefault();
        deleteAllOldVersions();
      });
    }
  } catch (e) {
    strip.innerHTML = '<span class="no-versions">Could not load versions</span>';
  }

  // Update versions collapsible preview
  const vpEl = document.getElementById('versionsPreview');
  if (vpEl) {
    const count = strip.querySelectorAll('.version-thumb').length;
    vpEl.textContent = count > 0 ? `${count} version${count > 1 ? 's' : ''}` : 'none';
  }
}

// --- Hover preview ---
function showVersionPreview(e, thumb) {
  hideVersionPreview();
  _versionPreviewTimer = setTimeout(() => {
    const img = thumb.querySelector('img');
    if (!img) return;
    const preview = document.createElement('div');
    preview.className = 'version-thumb-preview';
    preview.id = 'versionHoverPreview';
    preview.innerHTML = `<img src="${img.src}" alt=""><div class="preview-label">${escapeHtml(thumb.dataset.label)}</div>`;
    document.body.appendChild(preview);

    // Position above the thumbnail
    const rect = thumb.getBoundingClientRect();
    const pw = 220;
    const ph = pw * 1.4 + 24; // 5:7 ratio + label
    let left = rect.left + rect.width / 2 - pw / 2;
    let top = rect.top - ph - 8;
    // Keep on screen
    if (left < 8) left = 8;
    if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
    if (top < 8) top = rect.bottom + 8; // flip below if no room above
    preview.style.left = left + 'px';
    preview.style.top = top + 'px';
  }, 200);
}

function hideVersionPreview() {
  clearTimeout(_versionPreviewTimer);
  const el = document.getElementById('versionHoverPreview');
  if (el) el.remove();
}

// --- Click-to-restore modal ---
function openVersionModal(versionNum, slug, label) {
  hideVersionPreview();
  selectedVersion = versionNum;
  const modal = document.getElementById('versionModal');
  const img = document.getElementById('versionModalImg');
  const labelEl = document.getElementById('versionModalLabel');
  const actions = document.getElementById('versionModalActions');

  if (versionNum === null) {
    const currentCard = allCards.find(c => c.slug === slug || c.back_slug === slug);
    const mtime = (currentCard?.back_slug === slug
      ? currentCard?.back_composite_mtime
      : currentCard?.composite_mtime) || 0;
    img.src = `/api/image/composite/${slug}?v=${mtime}`;
    labelEl.textContent = 'Current Version';
    actions.style.display = 'none';
  } else {
    img.src = `/api/image/version/${slug}/${versionNum}`;
    labelEl.textContent = label || `Version ${versionNum}`;
    actions.style.display = '';
  }
  modal.style.display = '';
}

function closeVersionModal() {
  document.getElementById('versionModal').style.display = 'none';
  selectedVersion = null;
}

async function restoreFromModal() {
  if (selectedVersion === null || !selectedCard) return;
  const version = selectedVersion;
  const card = allCards.find(c => c.name === selectedCard);
  const key = faceKeyFor(card);   // "<name> [back]" targets the back face
  const slug = faceSlugFor(card);

  closeVersionModal();

  const resp = await fetch(`/api/revert/${encodeURIComponent(key)}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({version: version}),
  });
  const data = await resp.json();
  if (data.success) {
    if (card) {
      card.status = 'complete';
      if (viewingBack(card)) {
        card.has_back_composite = true;
        card.has_back_raw = true;
      } else {
        card.has_composite = true;
        card.has_raw_art = true;
      }
      updateDetailPanel(card);
    }
    renderGrid();
    loadVersionHistory(key, slug);
  } else {
    showToast('Restore failed: ' + (data.error || 'Unknown error'), 'error');
  }
}

async function deleteFromModal() {
  if (selectedVersion === null || !selectedCard) return;
  const dialogResult = await showCustomDialog({
    title: 'Delete Version',
    message: 'Delete this version permanently? This cannot be undone.',
    variant: 'danger',
    confirmText: 'Delete',
    confirmClass: 'btn-danger',
  });
  if (!dialogResult) return;
  const _delCard = allCards.find(c => c.name === selectedCard);
  const _delKey = faceKeyFor(_delCard);
  const resp = await fetch(`/api/delete-version/${encodeURIComponent(_delKey)}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({version: selectedVersion}),
  });
  const data = await resp.json();
  if (data.success) {
    closeVersionModal();
    loadVersionHistory(_delKey, faceSlugFor(_delCard));
    showToast('Version deleted', 'success');
  } else {
    showToast('Delete failed: ' + (data.error || 'Unknown error'), 'error');
  }
}

async function deleteAllOldVersions() {
  if (!selectedCard) return;
  const _daCard = allCards.find(c => c.name === selectedCard);
  const _daKey = faceKeyFor(_daCard);
  const slug = faceSlugFor(_daCard);
  const dialogResult = await showCustomDialog({
    title: 'Delete All Versions',
    message: 'Delete ALL archived versions for this card? Only the current art will remain.',
    variant: 'danger',
    confirmText: 'Delete All',
    confirmClass: 'btn-danger',
  });
  if (!dialogResult) return;
  const resp = await fetch(`/api/delete-versions-bulk/${encodeURIComponent(_daKey)}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  });
  const data = await resp.json();
  if (data.success) {
    loadVersionHistory(_daKey, slug);
  }
}

function name_to_slug(name) {
  // Mirrors the Python name_to_slug: " [back]" → __back, " // " → __, "/" → _
  return name.toLowerCase()
    .replace(/ \[back\]/g, '__back')
    .replace(/ \/\/ /g, '__')
    .replace(/\//g, '_')
    .replace(/ /g, '_')
    .replace(/,/g, '')
    .replace(/'/g, '')
    .replace(/-/g, '_');
}

// --- Start ---
// --- Deck Settings (Inspiration, Card Back) ---
let deckInfo = null;

async function loadDeckSettings() {
  const deckSelect = document.getElementById('deckSelect');
  const deckId = deckSelect ? deckSelect.value : '';
  if (!deckId) return;

  try {
    const resp = await fetch(`/api/decks/${deckId}/deck-info`);
    if (!resp.ok) return;
    deckInfo = await resp.json();

    // Render inspiration gallery
    const gallery = document.getElementById('inspirationGallery');
    const allImages = deckInfo.inspiration_images || [];
    // Tag each image with its original index before filtering
    const images = allImages
      .map((img, i) => ({...img, _origIdx: i}))
      .filter(img => img.exists !== false);
    let html = '';

    images.forEach((img) => {
      const idx = img._origIdx;
      html += `<div class="inspiration-thumb-item" data-index="${idx}">` +
        `<img src="/api/decks/${deckId}/inspiration-image/${idx}?t=${Date.now()}" alt="Inspiration ${idx + 1}" title="Inspiration ${idx + 1}">` +
        `<button class="insp-delete-btn" data-index="${idx}" title="Remove">&times;</button>` +
        `</div>`;
    });

    // Add "+" button if under max (10)
    if (images.length < 10) {
      html += `<button class="inspiration-add-btn" id="btnAddInspiration" title="Add inspiration image">+</button>`;
    }

    gallery.innerHTML = html;

    // Wire up event listeners (avoid inline onclick with potential issues)
    const addBtn = document.getElementById('btnAddInspiration');
    if (addBtn) {
      addBtn.addEventListener('click', () => document.getElementById('inspirationUpload').click());
    }
    gallery.querySelectorAll('.insp-delete-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        removeInspiration(parseInt(btn.dataset.index));
      });
    });

    // Show style description preview
    const stylePreview = document.getElementById('styleDescPreview');
    if (deckInfo.inspiration_style_description) {
      stylePreview.textContent = deckInfo.inspiration_style_description;
      stylePreview.title = deckInfo.inspiration_style_description;
    } else if (deckInfo.has_inspiration) {
      stylePreview.textContent = 'Style analysis pending...';
    } else {
      stylePreview.textContent = '';
    }

    // Populate style source input
    const sourceInput = document.getElementById('styleSourceInput');
    if (sourceInput) sourceInput.value = deckInfo.style_source || '';

    // Load art orientation toggle state
    loadArtOrientation();

    // Update setup bar state
    _setupBarState.hasStyle = images.length > 0;
    _setupBarState.hasPrompts = allCards.some(c => c.prompt);
    updateSetupBar();

    // Style reference hint text
    const existingHint = gallery.parentElement.querySelector('.style-hint');
    if (existingHint) existingHint.remove();
    if (images.length === 0) {
      const hint = document.createElement('p');
      hint.className = 'style-hint';
      hint.textContent = 'Upload inspiration art to define your deck\'s visual style';
      gallery.parentElement.insertBefore(hint, gallery.nextSibling);
    }

    // Next-step hint: guide the user to the next action
    const existingNextHint = document.querySelector('.next-step-hint');
    if (existingNextHint) existingNextHint.remove();
    const hasAnyAiArt = allCards.some(c => c.has_ai_art);
    if (images.length > 0 && !hasAnyAiArt) {
      const nextHint = document.createElement('div');
      nextHint.className = 'next-step-hint';
      nextHint.innerHTML = '<span>Next: generate styled art prompts for your cards</span>' +
        '<button class="btn btn-gold btn-sm" onclick="selectAll();regeneratePrompts()">Generate Prompts</button>';
      const btnRow = gallery.closest('.overview-section').querySelector('.overview-btn-row');
      btnRow.insertAdjacentElement('afterend', nextHint);
    }
  } catch (e) {
    console.warn('Could not load deck settings:', e);
  }
}

async function uploadInspiration(input) {
  if (!input.files.length) return;
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;

  const files = Array.from(input.files);
  const total = files.length;
  let uploaded = 0;
  let lastError = null;
  let lastWarning = null;

  try {
    for (const file of files) {
      uploaded++;
      document.getElementById('styleDescPreview').textContent =
        total > 1 ? `Uploading ${uploaded}/${total}...` : 'Uploading...';
      const formData = new FormData();
      formData.append('file', file);
      const resp = await fetch(`/api/decks/${deckId}/upload-inspiration`, {
        method: 'POST',
        body: formData,
      });
      const result = await resp.json();
      if (!result.success) {
        lastError = result.error || 'Upload failed';
        break;
      }
      if (result.warning) lastWarning = result.warning;
      // Refresh gallery after each upload to show progress
      await loadDeckSettings();
    }
    if (lastError) {
      showToast(lastError, 'error');
    } else if (lastWarning) {
      showToast(lastWarning, 'warning');
      _setupBarState.hasStyle = true;
      updateSetupBar();
    } else {
      showToast('Inspiration uploaded — analyzing style...', 'info');
      document.getElementById('styleDescPreview').textContent = '';  // Progress bar provides feedback
      // Progress bar is driven by the /api/status polling loop.
      // When style_progress clears, the poll handler refreshes deck settings automatically.
      _setupBarState.hasStyle = true;
      updateSetupBar();
    }
  } catch (e) {
    showToast('Upload error: ' + e.message, 'error');
  }
  input.value = ''; // reset file input
}

async function removeInspiration(index) {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;
  const dialogResult = await showCustomDialog({
    title: 'Remove Inspiration',
    message: 'Remove this inspiration image?',
    variant: 'danger',
    confirmText: 'Remove',
    confirmClass: 'btn-danger',
  });
  if (!dialogResult) return;

  try {
    const resp = await fetch(`/api/decks/${deckId}/inspiration-image/${index}`, {
      method: 'DELETE',
    });
    const result = await resp.json();
    if (result.success) {
      await loadDeckSettings();
    } else {
      showToast(result.error || 'Delete failed', 'error');
    }
  } catch (e) {
    showToast('Delete error: ' + e.message, 'error');
  }
}

async function saveStyleSource(value) {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;
  try {
    await fetch(`/api/decks/${deckId}/style-source`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ style_source: value }),
    });
    if (deckInfo) deckInfo.style_source = value;
  } catch (e) {
    console.error('Failed to save style source:', e);
  }
}

async function reanalyzeStyle() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId) return;
  const preview = document.getElementById('styleDescPreview');
  const btn = document.getElementById('btnReanalyzeStyle');
  preview.textContent = '';  // Progress bar provides feedback
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch(`/api/decks/${deckId}/reanalyze-inspiration`, { method: 'POST' });
    const result = await resp.json();
    if (!result.success) {
      preview.textContent = result.error || 'Re-analysis failed';
      if (btn) btn.disabled = false;
      showToast(result.error || 'Re-analysis failed — no AI backend available', 'warning');
      return;
    }
    // Progress bar is driven by the /api/status polling loop.
    // When style_progress clears, the poll handler refreshes deck settings
    // and re-enables the button automatically.
  } catch (e) {
    preview.textContent = 'Error: ' + e.message;
    if (btn) btn.disabled = false;
  }
}

async function regeneratePrompts() {
  const deckId = document.getElementById('deckSelect').value;
  if (!deckId || checkedCards.size === 0) return;

  const names = [...checkedCards];
  const isLocal = currentMode === 'local';
  const result = await showCustomDialog({
    title: `Regenerate Prompts for ${names.length} Cards`,
    message: 'Manual prompt edits will be overwritten.',
    fields: [
      { type: 'toggle', name: 'useAi', label: 'AI-enhanced subjects',
        checked: true,
        description: isLocal
          ? 'Free, runs locally via Ollama'
          : `~$${(0.001 * names.length).toFixed(3)} via OpenAI` },
    ],
    confirmText: 'Regenerate',
  });
  if (!result) return;
  const useAi = result.useAi;

  const progressFill = document.getElementById('batchProgressFill');
  const batchMessage = document.getElementById('batchMessage');

  disableBulkButtons();
  batchMessage.textContent = 'Starting prompt generation...';
  progressFill.style.width = '0%';
  progressFill.style.background = '';
  progressFill.classList.remove('indeterminate');

  try {
    const resp = await fetch(`/api/decks/${deckId}/regenerate-prompts`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ use_ai: useAi, card_names: names }),
    });
    const data = await resp.json();
    if (!data.success) {
      batchMessage.textContent = data.error || 'Failed';
      return;
    }

    const jobId = data.job_id;

    // Poll for progress — use unified pct (0-100) from backend
    let lastUpdatedCount = 0;
    let _lastRegenPct = 0;
    let _lastRegenPhase = 'generating';
    const result = await new Promise((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const r = await fetch(`/api/regen-prompts/progress/${jobId}`);
          if (!r.ok) return;
          const prog = await r.json();

          batchMessage.textContent = prog.message || 'Generating prompts...';
          // Use the backend's unified percentage (0-100). Clamp so it never goes
          // backward (prevents oscillation while the LLM works through a batch).
          const pct = Math.max(prog.pct || 0, _lastRegenPct);
          if (pct > _lastRegenPct) {
            // Real progress — show determinate bar
            progressFill.classList.remove('indeterminate');
          } else if (!prog.done) {
            // Stalled mid-generation (LLM working) — pulse to show activity
            progressFill.classList.add('indeterminate');
          }
          progressFill.style.width = pct + '%';
          _lastRegenPct = pct;
          _lastRegenPhase = prog.phase;

          // Refresh card data incrementally as prompts are generated
          const updatedCards = prog.updated_cards || [];
          if (updatedCards.length > lastUpdatedCount) {
            lastUpdatedCount = updatedCards.length;
            try {
              const cardsResp = await fetch('/api/cards');
              allCards = await cardsResp.json();
              renderGrid();
              if (selectedCard) {
                const card = allCards.find(c => c.name === selectedCard);
                if (card) updateDetailPanel(card);
              }
            } catch(_) {}
          }

          if (prog.done) {
            clearInterval(interval);
            if (prog.error) reject(new Error(prog.error));
            else resolve(prog);
          }
        } catch (e) {
          // Server busy (LLM blocking) — show indeterminate pulse at current width
          if (_lastRegenPct >= 50 || _lastRegenPhase === 'local') {
            batchMessage.textContent = 'Distilling local prompts (LLM working)...';
            progressFill.classList.add('indeterminate');
          }
        }
      }, 500);
    });

    // Success
    progressFill.classList.remove('indeterminate');
    progressFill.style.width = '100%';
    batchMessage.textContent = result.message || `Regenerated ${result.count} prompts!`;
    showToast(result.message || `Regenerated ${result.count} prompts!`, 'success');
    if (result.ai_fallback) {
      showToast('AI backend unavailable — used rule-based prompts.', 'warning');
    }
    if (result.local_prompt_warning) {
      showToast(`${result.local_prompt_warning} cards got basic prompts (LLM enhancement failed). Try Generate Random on individual cards.`, 'warning');
    }

    // Reload cards to get new prompts
    const cardsResp = await fetch('/api/cards');
    allCards = await cardsResp.json();
    renderGrid();
    if (selectedCard) {
      const card = allCards.find(c => c.name === selectedCard);
      if (card) updateDetailPanel(card);
    }

    // Remove next-step hint since prompts now exist
    const nextHint = document.querySelector('.next-step-hint');
    if (nextHint) nextHint.remove();

  } catch (e) {
    batchMessage.textContent = `Failed: ${e.message}`;
    progressFill.style.width = '0%';
    progressFill.style.background = 'var(--error)';
  } finally {
    enableBulkButtons();
  }
}

// ═══════════════════════════════════════════════════════════════
//  Toast Notification System
// ═══════════════════════════════════════════════════════════════
const _toastTimers = {};
function showToast(message, type = 'info', opts = {}) {
  const container = document.getElementById('toastContainer');
  const id = opts.id || ('toast-' + Date.now() + '-' + Math.random().toString(36).slice(2,6));

  // If updating existing toast
  if (opts.id && document.getElementById(opts.id)) {
    return updateToast(opts.id, message, type, opts);
  }

  const icons = { success: '&#10003;', error: '&#10007;', warning: '&#9888;', info: '&#8505;' };
  let progressHtml = '';
  if (opts.progress !== undefined || opts.indeterminate) {
    const fillClass = opts.indeterminate ? 'progress-bar-fill indeterminate' : 'progress-bar-fill';
    const fillWidth = opts.indeterminate ? '' : `width:${opts.progress || 0}%`;
    progressHtml = `<div class="toast-progress"><div class="progress-bar" style="display:block;height:3px;margin:0;"><div class="${fillClass}" style="${fillWidth}"></div></div></div>`;
  }
  const toast = document.createElement('div');
  toast.id = id;
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span class="toast-body"><span class="toast-msg">${escapeHtml(message)}</span>${progressHtml}</span>
    <button class="toast-dismiss" onclick="dismissToast('${id}')">&times;</button>
  `;
  container.appendChild(toast);

  // Auto-dismiss. `persistent` keeps it until explicitly updated/dismissed
  // (used for in-progress toasts); everything else auto-clears so toasts don't
  // pile up. `opts.duration` overrides the per-type default.
  const persistent = opts.persistent || false;
  if (!persistent) {
    const delay = opts.duration != null ? opts.duration
                : type === 'error' ? 6000 : type === 'warning' ? 4000 : 3000;
    _toastTimers[id] = setTimeout(() => dismissToast(id), delay);
  }
  return id;
}

function updateToast(id, message, type, opts = {}) {
  const toast = document.getElementById(id);
  if (!toast) return showToast(message, type, { id, ...opts });
  const icons = { success: '&#10003;', error: '&#10007;', warning: '&#9888;', info: '&#8505;' };
  toast.className = `toast toast-${type}`;
  toast.querySelector('.toast-icon').innerHTML = icons[type] || icons.info;

  // Update message text (preserve progress bar if present)
  const msgEl = toast.querySelector('.toast-msg');
  if (msgEl) {
    msgEl.textContent = message;
  } else {
    toast.querySelector('.toast-body').textContent = message;
  }

  // Update or add progress bar
  const existingProgress = toast.querySelector('.toast-progress');
  if (opts.progress !== undefined || opts.indeterminate) {
    if (existingProgress) {
      const fill = existingProgress.querySelector('.progress-bar-fill');
      if (opts.indeterminate) {
        fill.classList.add('indeterminate');
        fill.style.width = '';
      } else {
        fill.classList.remove('indeterminate');
        fill.style.width = opts.progress + '%';
      }
    } else {
      // Add progress bar to toast body
      const body = toast.querySelector('.toast-body');
      const fillClass = opts.indeterminate ? 'progress-bar-fill indeterminate' : 'progress-bar-fill';
      const fillWidth = opts.indeterminate ? '' : `width:${opts.progress || 0}%`;
      body.insertAdjacentHTML('beforeend', `<div class="toast-progress"><div class="progress-bar" style="display:block;height:3px;margin:0;"><div class="${fillClass}" style="${fillWidth}"></div></div></div>`);
    }
  } else if (existingProgress && (type === 'success' || type === 'error')) {
    // Remove progress bar on success/error final states
    existingProgress.remove();
  }

  // Reset auto-dismiss (e.g. a persistent progress toast finishing as a
  // non-persistent success/error so it now clears on its own).
  if (_toastTimers[id]) clearTimeout(_toastTimers[id]);
  if (!opts.persistent) {
    const delay = opts.duration != null ? opts.duration
                : type === 'error' ? 6000 : type === 'warning' ? 4000 : 3000;
    _toastTimers[id] = setTimeout(() => dismissToast(id), delay);
  }
}

function dismissToast(id) {
  const toast = document.getElementById(id);
  if (!toast) return;
  if (_toastTimers[id]) { clearTimeout(_toastTimers[id]); delete _toastTimers[id]; }
  toast.classList.add('dismissing');
  setTimeout(() => toast.remove(), 200);
}


// ═══════════════════════════════════════════════════════════════
//  Setup Bar
// ═══════════════════════════════════════════════════════════════
let _setupBarDismissed = null; // tracks which step was dismissed
let _setupBarState = {};       // cached state from APIs

function updateSetupBar() {
  const bar = document.getElementById('setupBar');
  const msg = document.getElementById('setupBarMessage');
  const actions = document.getElementById('setupBarActions');
  const deckSelect = document.getElementById('deckSelect');

  const hasKey = _setupBarState.hasApiKey || false;
  const hasLocalDeps = _setupBarState.hasLocalDeps || false;
  const hasBackend = hasKey || hasLocalDeps;
  const hasDecks = deckSelect && deckSelect.options.length > 0 && deckSelect.value;
  const hasStyle = _setupBarState.hasStyle || false;
  const hasPrompts = _setupBarState.hasPrompts || false;

  let step = null;
  let message = '';
  let btns = '';

  if (!hasBackend) {
    step = 'backend';
    message = 'Connect an AI backend to start generating';
    btns = `<button class="btn btn-gold btn-sm" onclick="openModelHub()">Set up Cloud</button>
            <button class="btn btn-secondary btn-sm" onclick="openModelHub()">Set up Local</button>`;
  } else if (!hasDecks) {
    step = 'deck';
    message = 'Import a deck to get started';
    btns = `<button class="btn btn-gold btn-sm" onclick="openImportModal()">Import Deck</button>`;
  } else if (!hasStyle) {
    step = 'style';
    message = 'Upload inspiration art to define your deck\'s style';
    btns = `<button class="btn btn-gold btn-sm" onclick="document.getElementById('inspirationUpload').click()">Upload</button>`;
  } else if (!hasPrompts) {
    step = 'prompts';
    message = 'Generate art prompts for your cards';
    btns = `<button class="btn btn-gold btn-sm" onclick="selectAll();regeneratePrompts()">Generate Prompts</button>`;
  }

  // If dismissed this step, or nothing to show
  if (!step || _setupBarDismissed === step) {
    bar.classList.add('hidden');
    return;
  }

  msg.textContent = message;
  actions.innerHTML = btns;
  bar.classList.remove('hidden');
  bar.dataset.step = step;
}

function dismissSetupBar() {
  const bar = document.getElementById('setupBar');
  _setupBarDismissed = bar.dataset.step;
  bar.classList.add('hidden');
}


// ═══════════════════════════════════════════════════════════════
//  Welcome Hero
// ═══════════════════════════════════════════════════════════════
function showWelcomeIfNeeded() {
  const hero = document.getElementById('welcomeHero');
  const grid = document.getElementById('cardGrid');
  const deckSelect = document.getElementById('deckSelect');
  const noDecks = !deckSelect || !deckSelect.value || deckSelect.options.length === 0;

  if (allCards.length === 0 && noDecks) {
    hero.style.display = '';
    grid.style.display = 'none';
    refreshWelcomeStatus();
  } else {
    hero.style.display = 'none';
    grid.style.display = '';
  }
}

async function refreshWelcomeStatus() {
  // MLX-native: the local backend is always available on Apple Silicon once
  // mflux is installed. No cloud/API-key path remains.
  try {
    const local = await (await fetch('/api/local-image-status')).json();
    const localStatus = document.getElementById('welcomeLocalStatus');
    if (localStatus) {
      localStatus.innerHTML = local.available ? '&#10003; Ready' : 'mflux not installed';
      localStatus.style.display = '';
    }
    _setupBarState.hasApiKey = false;
    _setupBarState.hasLocalDeps = !!local.available;
  } catch(e) { /* silent */ }
}

function toggleWelcomeSetup(backend) {
  const localSetup = document.getElementById('welcomeLocalSetup');
  if (!localSetup) return;
  const showing = localSetup.style.display !== 'none';
  localSetup.style.display = showing ? 'none' : '';
  if (!showing) refreshLocalChecklist();
}

async function refreshLocalChecklist() {
  const container = document.getElementById('welcomeLocalChecks');
  if (!container) return;
  container.innerHTML = '<span style="color:var(--text-muted)">Checking...</span>';
  try {
    const local = await (await fetch('/api/local-image-status')).json();
    const checks = [ { label: 'mflux (MLX) installed', ok: !!local.available } ];
    container.innerHTML = checks.map(c =>
      `<div class="welcome-check-item">
        <span class="${c.ok ? 'check-ok' : 'check-missing'}">${c.ok ? '&#10003;' : '&#9675;'}</span>
        <span>${escapeHtml(c.label)}</span>
      </div>`
    ).join('');
    if (local.available) {
      container.innerHTML += '<div style="margin-top:6px;color:var(--success);font-size:0.9em;font-weight:600;">Ready to generate! Open the model picker to load FLUX.</div>';
    } else {
      container.innerHTML += `<div style="margin-top:6px;font-size:0.85em;color:var(--text-muted);">
        Run: <code style="color:var(--gold);">pip install -r requirements-mac.txt</code>
      </div>`;
    }
  } catch(e) {
    container.innerHTML = '<span style="color:var(--error)">Check failed</span>';
  }
}


// ═══════════════════════════════════════════════════════════════
//  Model Hub
// ═══════════════════════════════════════════════════════════════
async function openModelHub() {
  const hub = document.getElementById('modelHub');
  const body = document.getElementById('modelHubBody');
  hub.style.display = '';
  body.innerHTML = '<p style="text-align:center;color:var(--text-muted);padding:24px;">Loading models...</p>';

  try {
    const [configResp, backendResp, localResp] = await Promise.all([
      fetch('/api/model-config'),
      fetch('/api/backend'),
      fetch('/api/local-image-status').catch(() => ({ json: () => ({}) }))
    ]);
    const config = await configResp.json();
    const backend = await backendResp.json();
    const local = await (localResp.json ? localResp.json() : Promise.resolve({}));

    const activeKey = config.active;
    const options = config.options;
    const localModels = local.models || {};

    // Separate cloud and local
    const cloud = [], loc = [];
    for (const [key, opt] of Object.entries(options)) {
      if (opt.is_local) loc.push({ key, ...opt });
      else cloud.push({ key, ...opt });
    }

    function renderModelCard(m, isLocal) {
      const isActive = m.key === activeKey;
      const isDisabled = m.disabled;
      const cost = m.cost_per_image > 0 ? `$${m.cost_per_image.toFixed(m.cost_per_image < 0.1 ? 3 : 2)}/card` : 'Free';
      const costClass = m.cost_per_image > 0 ? 'paid' : 'free';

      let statusHtml = '';
      if (isLocal) {
        const lm = m._local_model;
        const cached = localModels[lm]?.cached;
        const loaded = local.is_loaded && local.active_model === lm;
        if (isDisabled) {
          statusHtml = '<span class="dot dot-red"></span> <span style="color:var(--error)">Needs setup</span>';
        } else if (loaded) {
          statusHtml = '<span class="dot dot-gold"></span> <span style="color:var(--gold)">Loaded</span>';
        } else if (cached) {
          statusHtml = '<span class="dot dot-green"></span> <span style="color:var(--success)">Downloaded</span>';
        } else {
          statusHtml = '<span class="dot dot-dim"></span> <span>Not cached (~7GB)</span>';
        }
      }

      let btnHtml = '';
      if (isActive) {
        btnHtml = '<button class="btn btn-gold btn-sm" disabled>Active</button>';
      } else if (isDisabled) {
        btnHtml = '<button class="btn btn-secondary btn-sm" disabled>Unavailable</button>';
      } else if (isLocal) {
        const loaded = local.is_loaded && local.active_model === m._local_model;
        btnHtml = `<button class="btn btn-secondary btn-sm" onclick="selectModelFromHub('${m.key}')">${loaded ? 'Select' : 'Load &amp; Select'}</button>`;
      } else {
        btnHtml = `<button class="btn btn-secondary btn-sm" onclick="selectModelFromHub('${m.key}')">Select</button>`;
      }

      // Capabilities
      const caps = [];
      const size = m.size || '';
      const memMatch = m.label?.match(/(\\d+GB)/);
      const mem = memMatch ? memMatch[1] + ' VRAM' : '';

      return `<div class="model-card ${isActive ? 'active' : ''} ${isDisabled ? 'disabled' : ''}" data-model-key="${escapeHtml(m.key)}">
        <div class="model-card-name">${escapeHtml(m.label?.split('|')[0]?.split('(')[0]?.trim() || m.key)}</div>
        <div class="model-card-quality">${escapeHtml(m.description || '')}</div>
        <span class="model-card-cost ${costClass}">${cost}</span>
        <div class="model-card-meta">
          ${size ? size : ''}${caps.length ? ' &middot; ' + caps.join(', ') : ''}${mem ? '<br>' + mem : ''}
        </div>
        ${statusHtml ? '<div class="model-card-status">' + statusHtml + '</div>' : ''}
        ${btnHtml}
      </div>`;
    }

    // MLX-native, local only — no cloud backend / API key.
    let html = `<div class="model-hub-section">
      <div class="model-hub-header">
        <h3>FLUX (MLX)</h3>
        <span style="font-size:0.72em;color:var(--text-muted);">Apple Silicon &middot; local &amp; free</span>
      </div>
      <div class="model-hub-grid">${loc.map(m => renderModelCard(m, true)).join('')}</div>`;

    // Prerequisites
    const depsInstalled = local.available;
    const prereqs = [
      { label: 'mflux (MLX) installed', ok: depsInstalled, hint: 'pip install -r requirements-mac.txt' },
    ];

    html += `<div class="model-prereqs">
      <h4>Prerequisites</h4>
      <div class="model-prereqs-grid">
        ${prereqs.map(p => `<div class="prereq-item">
          <span class="${p.ok ? 'prereq-ok' : 'prereq-missing'}">${p.ok ? '&#10003;' : '&#9675;'}</span>
          <span>${escapeHtml(p.label)}${!p.ok && p.hint ? ' <span style="color:var(--text-muted);">(' + escapeHtml(p.hint) + ')</span>' : ''}</span>
        </div>`).join('')}
      </div>
    </div>`;

    html += '</div>';

    body.innerHTML = html;
  } catch(e) {
    body.innerHTML = `<p style="color:var(--error);text-align:center;">Failed to load models: ${escapeHtml(e.message)}</p>`;
  }
}

function closeModelHub() {
  document.getElementById('modelHub').style.display = 'none';
}

function updateModelHubProgress(mlp) {
  const hub = document.getElementById('modelHub');
  if (!hub || hub.style.display === 'none') return;
  const cards = hub.querySelectorAll('.model-card[data-model-key]');
  cards.forEach(card => {
    let bar = card.querySelector('.model-load-progress');
    const cardKey = card.getAttribute('data-model-key');
    // Match card to model_key: card key is e.g. 'local-sdxl-lightning',
    // backend model_key is e.g. 'sdxl-lightning-4step' (the _local_model value)
    const cardLocalModel = modelConfig.options[cardKey]?._local_model;
    const isTarget = mlp.model_key && (
      cardKey === mlp.model_key ||
      cardLocalModel === mlp.model_key
    );
    if (!isTarget || mlp.phase === 'complete' || mlp.phase === 'error' || !mlp.phase) {
      if (bar) bar.remove();
      return;
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'model-load-progress';
      bar.innerHTML = '<div class="progress-bar"><div class="progress-bar-fill"></div></div><div class="model-load-msg"></div>';
      card.appendChild(bar);
    }
    const fill = bar.querySelector('.progress-bar-fill');
    const msg = bar.querySelector('.model-load-msg');
    if (mlp.phase === 'downloading' && mlp.pct > 0) {
      fill.classList.remove('indeterminate');
      fill.style.width = mlp.pct + '%';
    } else {
      fill.classList.add('indeterminate');
      fill.style.width = '';
    }
    msg.textContent = mlp.message || 'Loading...';
  });
}

async function selectModelFromHub(key) {
  // Set the dropdown and trigger changeModel
  const select = document.getElementById('modelSelect');
  select.value = key;
  await changeModel();
  // Refresh the hub to update active state
  openModelHub();
}


// ═══════════════════════════════════════════════════════════════
//  Empty States
// ═══════════════════════════════════════════════════════════════
function showEmptyGridState() {
  const grid = document.getElementById('cardGrid');
  const deckSelect = document.getElementById('deckSelect');
  const hasDeck = deckSelect && deckSelect.value;

  if (hasDeck) {
    grid.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">&#9671;</div>
      <div class="empty-state-title">No cards yet</div>
      <div class="empty-state-hint">Use the ⋯ menu to add cards, or import a new deck.</div>
    </div>`;
  } else {
    grid.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">&#9670;</div>
      <div class="empty-state-title">No deck selected</div>
      <div class="empty-state-hint">Import a deck to start generating art.</div>
    </div>`;
  }
}


init();
</script>

<!-- Model Hub Modal -->
<div id="modelHub" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModelHub()">
  <div class="modal-content">
    <h2>Models</h2>
    <div class="modal-body" id="modelHubBody"></div>
    <div style="display:flex;justify-content:flex-end;margin-top:16px;">
      <button class="btn btn-secondary btn-sm" onclick="closeModelHub()">Close</button>
    </div>
  </div>
</div>

<!-- Toast Container -->
<div id="toastContainer"></div>

</body>
</html>
"""

# ===========================================================================
#  Main
# ===========================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Deck Art Studio")
    parser.add_argument('--port', type=int, default=5001)
    parser.add_argument('--host', type=str, default='127.0.0.1')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    # Migrate single-deck layout → multi-deck if needed
    migrate_legacy_deck()

    load_reference_image()
    load_saved_api_key()

    # Restore persisted model selection
    _bcfg = backend_config.load_config()
    _saved_model = _bcfg.get('active_model_key', '')
    if _saved_model and _saved_model in MODEL_OPTIONS:
        active_model_key = _saved_model
        print(f"Restored active model: {active_model_key}")

    # MLX text/vision models (mlx-lm, mlx-vlm) download lazily from the
    # HuggingFace hub on first use — no startup pull needed.

    # NB: we deliberately do NOT preload the FLUX image model on startup. On an
    # 18 GB machine a resident FLUX (~12 GB) collides with the mlx-lm/mlx-vlm models
    # used for prompt/style work (~5 GB) and OOM-kills the process. Instead, FLUX is
    # auto-loaded on demand at generation time (single-card and batch), and loading
    # an LLM/VLM evicts FLUX — so only one heavy model is ever resident.
    if not backend_config.check_diffusers_installed():
        print("[backend] mflux (MLX) not installed — image generation unavailable")
        print("[backend] Install with: pip install -r requirements-mac.txt")

    # Load active deck (or first available)
    registry = _load_deck_registry()
    startup_deck = registry.get('active')
    if not startup_deck and registry.get('decks'):
        startup_deck = registry['decks'][0]['id']
    if startup_deck:
        switch_deck(startup_deck)
    else:
        # No decks at all — start with empty state
        RAW_ART_DIR.mkdir(parents=True, exist_ok=True)
        COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        load_data()

    # Auto-download MTG fonts (Beleren + MPlantin) if missing
    try:
        from fetch_mtg_fonts import fonts_available, download_fonts, install_fonts
        if not fonts_available():
            print("  Downloading MTG card fonts (Beleren + MPlantin)...")
            download_fonts()
            install_fonts()
        else:
            print("  MTG fonts: OK")
    except Exception as _e:
        print(f"  [WARN] Could not download MTG fonts: {_e}")

    # Auto-fetch flavor text from Scryfall — ONLY for cards missing the field entirely.
    # Empty string ("") means we already checked and the card has no flavor text.
    try:
        if CARD_DB_PATH.exists():
            import json as _json
            with open(CARD_DB_PATH) as _f:
                _raw = _json.load(_f)
            # Handle both formats
            if isinstance(_raw, dict) and 'cards' in _raw:
                _cards = _raw['cards']
                _is_new_fmt = True
            else:
                _cards = _raw
                _is_new_fmt = False

            _missing = [c for c in _cards if 'flavor_text' not in c]
            if _missing:
                print(f"  Fetching flavor text for {len(_missing)} new cards from Scryfall...")
                from fetch_flavor_text import fetch_card_data, fetch_flavor_across_printings
                import time as _time
                _updated = 0
                for _card in _missing:
                    _data = fetch_card_data(_card['name'])
                    _flavor = _data.get('flavor_text', '')
                    if not _flavor:
                        _time.sleep(0.12)
                        _flavor = fetch_flavor_across_printings(_card['name'])
                    _card['flavor_text'] = _flavor
                    if _flavor:
                        _updated += 1
                    _time.sleep(0.12)
                if _is_new_fmt:
                    _raw['cards'] = _cards
                    with open(CARD_DB_PATH, 'w') as _f:
                        _json.dump(_raw, _f, indent=2)
                else:
                    with open(CARD_DB_PATH, 'w') as _f:
                        _json.dump(_cards, _f, indent=2)
                print(f"  Added flavor text to {_updated} cards")
            else:
                _has = sum(1 for c in _cards if c.get('flavor_text'))
                print(f"  Flavor text: {_has}/{len(_cards)} cards")
    except Exception as _e:
        print(f"  [WARN] Could not fetch flavor text: {_e}")

    print(f"\n{'='*60}")
    print(f"  Deck Art Studio")
    print(f"  Active deck: {active_deck_id or '(none)'}")
    print(f"  Open http://{args.host}:{args.port} in your browser")
    print(f"  Backend: MLX-native (mflux FLUX + mlx-lm + mlx-vlm), Apple Silicon")
    print(f"{'='*60}\n")

    # Prevent debug mode when exposed on the network (debug enables code execution)
    debug = args.debug and args.host in ('127.0.0.1', 'localhost')
    if args.debug and not debug:
        print("WARNING: --debug ignored when --host is not localhost (security risk)")
    app.run(host=args.host, port=args.port, debug=debug, threaded=True)
