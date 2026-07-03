#!/usr/bin/env python3
"""
Scryfall API client and decklist parser for Deck Art Studio.

Handles:
  - Parsing Archidekt/MTGO/Arena text decklists
  - Fetching card data from Scryfall API
  - Building card_database entries from Scryfall data
  - Rate limiting and caching
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Scryfall rate limit: 10 requests/sec, we'll be conservative
SCRYFALL_DELAY = 0.12  # seconds between requests

# Cache directory for raw Scryfall responses
CACHE_DIR = Path(__file__).parent / "shared" / "scryfall_cache"


def _scryfall_get(url: str) -> dict:
    """Make a GET request to Scryfall API with rate limiting."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'DeckArtStudio/1.0',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_card_by_name(name: str, use_cache: bool = True) -> Optional[dict]:
    """Fetch a single card from Scryfall by exact name.

    Returns the full Scryfall card object, or None on failure.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = (name.lower().replace(' // ', '__').replace('/', '_').replace(' ', '_')
            .replace(',', '').replace("'", "").replace('-', '_'))
    cache_path = CACHE_DIR / f"{slug}.json"

    if use_cache and cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    encoded = urllib.parse.quote(name)
    url = f"https://api.scryfall.com/cards/named?exact={encoded}"

    try:
        data = _scryfall_get(url)
        # Cache the response
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2)
        return data
    except Exception as e:
        # Try fuzzy search as fallback
        try:
            url_fuzzy = f"https://api.scryfall.com/cards/named?fuzzy={encoded}"
            data = _scryfall_get(url_fuzzy)
            with open(cache_path, 'w') as f:
                json.dump(data, f, indent=2)
            return data
        except Exception:
            print(f"  [scryfall] Failed to fetch '{name}': {e}")
            return None


def fetch_card_by_set(set_code: str, collector_number: str, use_cache: bool = True) -> Optional[dict]:
    """Fetch a specific card printing from Scryfall by set code and collector number.

    Uses the /cards/{set}/{number} endpoint for exact printing lookup.
    Returns the full Scryfall card object, or None on failure.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"{set_code.lower()}_{collector_number}"
    cache_path = CACHE_DIR / f"{slug}.json"

    if use_cache and cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    encoded_set = urllib.parse.quote(set_code.lower())
    encoded_num = urllib.parse.quote(collector_number)
    url = f"https://api.scryfall.com/cards/{encoded_set}/{encoded_num}"

    try:
        data = _scryfall_get(url)
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2)
        return data
    except Exception as e:
        print(f"  [scryfall] Set lookup failed for ({set_code}) {collector_number}: {e}")
        return None


def fetch_card(name: str, set_code: str = None, collector_number: str = None,
               use_cache: bool = True) -> Optional[dict]:
    """Fetch a card from Scryfall, preferring set+number lookup when available.

    Falls back to name-only lookup if set code is missing, set lookup fails,
    or the returned card name doesn't match the expected name.
    """
    if set_code and collector_number:
        result = fetch_card_by_set(set_code, collector_number, use_cache=use_cache)
        if result:
            returned_name = result.get('name', '')
            # Scryfall names for double-faced cards use " // " separator;
            # match if the expected name equals the full name or either face
            name_parts = [returned_name] + returned_name.split(' // ')
            # Also check reverse: if any part of the expected name matches
            # (handles doubled names like "Krark's Thumb // Krark's Thumb")
            expected_parts = [name] + name.split(' // ')
            if (name.lower() in [p.lower() for p in name_parts] or
                    any(p.lower() == returned_name.lower() for p in expected_parts)):
                return result
            else:
                print(f"  [scryfall] Set ({set_code}) {collector_number} returned "
                      f"'{returned_name}' but expected '{name}' — falling back to name lookup")
        else:
            print(f"  [scryfall] Set lookup failed for ({set_code}) {collector_number} "
                  f"— falling back to name lookup for '{name}'")

    return fetch_card_by_name(name, use_cache=use_cache)


def parse_decklist(text: str) -> list[dict]:
    """Parse a text decklist into structured card entries.

    Supports multiple formats:
      Archidekt:  1x Card Name (SET) 123 [Category] ^Label^
      MTGO:       1 Card Name
      Arena:      1 Card Name (SET) 123
      Simple:     Card Name

    Returns list of dicts:
      {quantity, name, set_code, collector_number, category, is_commander}
    """
    entries = []
    current_category = None

    for raw_line in text.strip().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue

        # Check for section headers like "Commander", "Creature (15)", etc.
        header_match = re.match(r'^(Commander|Companion|Sideboard|Maybeboard|Creatures?|Instants?|Sorcery|Sorceries|Enchantments?|Artifacts?|Planeswalkers?|Lands?|Other)\s*(\(\d+\))?$', line, re.IGNORECASE)
        if header_match:
            current_category = header_match.group(1).strip()
            continue

        # Parse the card line
        entry = _parse_card_line(line)
        if entry:
            # Override category from section header if present
            if current_category and not entry.get('category'):
                entry['category'] = current_category

            # Mark commanders
            cat = (entry.get('category') or '').lower()
            entry['is_commander'] = cat in ('commander', 'commanders')

            entries.append(entry)

    return entries


def _parse_card_line(line: str) -> Optional[dict]:
    """Parse a single decklist line into a card entry.

    Handles formats:
      1x Card Name (SET) 123 *F* [Category] ^Label,#color^
      1 Card Name (SET) 123
      1x Card Name
      Card Name
    """
    entry = {
        'quantity': 1,
        'name': '',
        'set_code': None,
        'collector_number': None,
        'category': None,
        'is_foil': False,
        'is_commander': False,
    }

    # Extract [Category] if present
    cat_match = re.search(r'\[([^\]]+)\]', line)
    if cat_match:
        entry['category'] = cat_match.group(1).strip()
        line = line[:cat_match.start()] + line[cat_match.end():]

    # Remove ^Label^ tags
    line = re.sub(r'\^[^^]*\^', '', line)

    # Check for foil marker *F*
    if '*F*' in line:
        entry['is_foil'] = True
        line = line.replace('*F*', '')

    line = line.strip()

    # Extract quantity: "1x " or "1 " prefix
    qty_match = re.match(r'^(\d+)\s*x?\s+', line)
    if qty_match:
        entry['quantity'] = int(qty_match.group(1))
        line = line[qty_match.end():]

    # Extract (SET) code and optional collector number
    set_match = re.search(r'\(([a-zA-Z0-9]+)\)\s*(\S+)?', line)
    if set_match:
        entry['set_code'] = set_match.group(1).lower()
        if set_match.group(2):
            # Collector number — strip non-numeric suffixes
            entry['collector_number'] = set_match.group(2).strip()
        line = line[:set_match.start()].strip()

    entry['name'] = line.strip()

    # Deduplicate doubled names: "Krark's Thumb // Krark's Thumb" → "Krark's Thumb"
    # Some deck builders (Archidekt) double the name for non-DFC cards.
    # Preserve legitimate split names like "Bonecrusher Giant // Stomp".
    if ' // ' in entry['name']:
        parts = entry['name'].split(' // ')
        if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
            entry['name'] = parts[0].strip()

    if not entry['name']:
        return None

    return entry


def normalize_card_type(type_line: str) -> str:
    """Derive a simple card_type from a Scryfall type_line."""
    tl = type_line.lower()
    # Check in order of specificity
    if 'battle' in tl:
        return 'battle'
    if 'creature' in tl:
        return 'creature'
    if 'planeswalker' in tl:
        return 'planeswalker'
    if 'instant' in tl:
        return 'instant'
    if 'sorcery' in tl:
        return 'sorcery'
    if 'enchantment' in tl:
        return 'enchantment'
    if 'artifact' in tl:
        return 'artifact'
    if 'land' in tl:
        return 'land'
    return 'other'


# Layouts where each face has its OWN art and image_uris (true double-faced
# cards). Adventure/split/room faces share a single art, so they're not here.
DFC_LAYOUTS = {'transform', 'modal_dfc'}


def _face_entry(face: dict) -> dict:
    """Distill one Scryfall card face into the fields we store per face."""
    type_line = face.get('type_line', '')
    return {
        'name': face.get('name', ''),
        'mana_cost': face.get('mana_cost', ''),
        'type_line': type_line,
        'oracle_text': face.get('oracle_text', ''),
        'power': face.get('power'),
        'toughness': face.get('toughness'),
        'loyalty': face.get('loyalty'),
        'defense': face.get('defense'),  # battles (sieges)
        # Back faces of transform cards carry a color_indicator instead of a
        # mana cost — fall back to it so frame coloring works.
        'colors': face.get('colors', face.get('color_indicator', [])),
        'flavor_text': face.get('flavor_text', ''),
        'art_crop_url': (face.get('image_uris') or {}).get('art_crop', ''),
        'card_type': normalize_card_type(type_line),
    }


def scryfall_to_card_entry(sf: dict, quantity: int = 1, is_commander: bool = False) -> dict:
    """Convert a Scryfall card object to our card_database format."""
    # Handle multi-face cards: use first face when top-level data is missing.
    # Covers transform, modal_dfc, meld, reversible_card, adventure, art_series.
    faces = sf.get('card_faces', [])
    if faces and not sf.get('oracle_text'):
        face = faces[0]
        oracle_text = face.get('oracle_text', '')
        mana_cost = face.get('mana_cost', sf.get('mana_cost', ''))
        type_line = face.get('type_line', sf.get('type_line', ''))
        power = face.get('power')
        toughness = face.get('toughness')
        loyalty = face.get('loyalty')
        defense = face.get('defense')
    else:
        oracle_text = sf.get('oracle_text', '')
        mana_cost = sf.get('mana_cost', '')
        type_line = sf.get('type_line', '')
        power = sf.get('power')
        toughness = sf.get('toughness')
        loyalty = sf.get('loyalty')
        defense = sf.get('defense')

    # Extract art_crop URL (for Scryfall default art display)
    image_uris = sf.get('image_uris', {})
    art_crop_url = image_uris.get('art_crop', '')
    if not art_crop_url:
        # Double-faced cards store image_uris on each face
        faces = sf.get('card_faces', [])
        if faces and 'image_uris' in faces[0]:
            art_crop_url = faces[0]['image_uris'].get('art_crop', '')

    # Deduplicate reversible_card names: "Okaun, Eye of Chaos // Okaun, Eye of Chaos"
    # → "Okaun, Eye of Chaos" (same card on both faces, different art)
    card_name = sf.get('name', '')
    if ' // ' in card_name:
        parts = card_name.split(' // ')
        if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
            card_name = parts[0].strip()

    entry = {
        'name': card_name,
        'mana_cost': mana_cost,
        'type_line': type_line,
        'oracle_text': oracle_text,
        'power': power,
        'toughness': toughness,
        'loyalty': loyalty,
        'defense': defense,
        'colors': sf.get('colors', []),
        'color_identity': sf.get('color_identity', []),
        'quantity': quantity,
        'is_commander': is_commander,
        'card_type': normalize_card_type(type_line),
        'flavor_text': sf.get('flavor_text', ''),
        'art_crop_url': art_crop_url,
        'set_code': sf.get('set', ''),
        'collector_number': sf.get('collector_number', ''),
        'set_name': sf.get('set_name', ''),
        'scryfall_id': sf.get('id', ''),
    }

    # Alternative layouts: keep the layout + per-face data so downstream code
    # can render/generate each face (transform, modal_dfc) or, later, special
    # text layouts (adventure, split, room). Single-face cards stay unchanged.
    layout = sf.get('layout', 'normal')
    if layout != 'normal':
        entry['layout'] = layout
    if len(faces) >= 2 and card_name == sf.get('name', ''):
        # (skip reversible_card duplicates whose name we collapsed above)
        entry['card_faces'] = [_face_entry(f) for f in faces]

    return entry


def populate_cards(parsed_entries: list[dict], progress_callback=None) -> tuple[list[dict], list[str]]:
    """Fetch Scryfall data for each parsed entry and build card_database entries.

    Args:
        parsed_entries: Output of parse_decklist()
        progress_callback: Optional fn(current, total, card_name) called per card

    Returns:
        (cards, errors): list of card dicts, list of error strings
    """
    cards = []
    errors = []
    total = len(parsed_entries)

    for i, entry in enumerate(parsed_entries):
        name = entry['name']
        if progress_callback:
            progress_callback(i + 1, total, name)

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
            cards.append(card)
        else:
            errors.append(f"Card not found: {name}")

        # Rate limit (skip delay for cached results)
        name_slug = name.lower().replace(' ', '_').replace(',', '').replace("'", "").replace('-', '_')
        set_code = entry.get('set_code')
        cnum = entry.get('collector_number')
        cached = (CACHE_DIR / f"{name_slug}.json").exists()
        if not cached and set_code and cnum:
            cached = (CACHE_DIR / f"{set_code.lower()}_{cnum}.json").exists()
        if not cached:
            time.sleep(SCRYFALL_DELAY)

    return cards, errors


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 scryfall_client.py <decklist.txt>")
        print("       python3 scryfall_client.py --card 'Card Name'")
        sys.exit(1)

    if sys.argv[1] == '--card':
        name = sys.argv[2]
        data = fetch_card_by_name(name)
        if data:
            card = scryfall_to_card_entry(data)
            print(json.dumps(card, indent=2))
        else:
            print(f"Not found: {name}")
    else:
        path = Path(sys.argv[1])
        text = path.read_text()
        entries = parse_decklist(text)
        print(f"Parsed {len(entries)} cards")
        for e in entries:
            cmd = ' [CDR]' if e['is_commander'] else ''
            print(f"  {e['quantity']}x {e['name']}{cmd}")

        print("\nFetching from Scryfall...")
        cards, errs = populate_cards(entries, lambda i, t, n: print(f"  [{i}/{t}] {n}"))
        print(f"\nDone: {len(cards)} cards, {len(errs)} errors")
        if errs:
            for e in errs:
                print(f"  ERROR: {e}")
