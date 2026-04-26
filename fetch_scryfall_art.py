"""
Fetch original card art from Scryfall for use as reference images.

Downloads the 'art_crop' version of each card (just the art, no frame)
and saves to scryfall_art/ directory.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRYFALL_API = "https://api.scryfall.com"
ART_DIR = Path("scryfall_art")
DB_PATH = Path("card_database.json")
HEADERS = {"Accept": "application/json", "User-Agent": "MTGProxyDeckGen/1.0"}

# Rate limit: Scryfall asks for 50-100ms between requests
RATE_LIMIT = 0.12


def name_to_slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _api_get(url: str) -> dict:
    """GET with proper headers."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_card_art(card_name: str, force: bool = False) -> Path | None:
    """Fetch the art_crop image for a card from Scryfall.

    Returns the local path to the downloaded image, or None on failure.
    """
    slug = name_to_slug(card_name)
    out_path = ART_DIR / f"{slug}.jpg"

    if out_path.exists() and not force:
        return out_path

    ART_DIR.mkdir(exist_ok=True)

    try:
        # Try exact name lookup first
        encoded = urllib.parse.quote(card_name)
        data = _api_get(f"{SCRYFALL_API}/cards/named?exact={encoded}")

        # Get art_crop URL (just the art, no frame)
        image_uris = data.get("image_uris", {})
        art_url = image_uris.get("art_crop")

        if not art_url:
            # Some cards have faces (double-faced, etc.)
            faces = data.get("card_faces", [])
            if faces and "image_uris" in faces[0]:
                art_url = faces[0]["image_uris"].get("art_crop")

        if not art_url:
            print(f"  [{card_name}] No art_crop URL found")
            return None

        # Download the image
        req = urllib.request.Request(art_url, headers={
            "User-Agent": "MTGProxyDeckGen/1.0"
        })
        with urllib.request.urlopen(req) as resp:
            img_data = resp.read()

        with open(out_path, 'wb') as f:
            f.write(img_data)

        print(f"  [{card_name}] Art saved ({len(img_data) // 1024}KB)")
        return out_path

    except Exception as e:
        print(f"  [{card_name}] Error: {e}")
        return None


def fetch_all_art(force: bool = False) -> dict:
    """Fetch art for all cards in the database.

    Returns dict of {card_name: path_or_none}.
    """
    with open(DB_PATH) as f:
        cards = json.load(f)

    results = {}
    total = len(cards)

    for i, card in enumerate(cards):
        name = card["name"]
        slug = name_to_slug(name)
        existing = ART_DIR / f"{slug}.jpg"

        if existing.exists() and not force:
            results[name] = existing
            continue

        print(f"[{i + 1}/{total}] Fetching art for {name}...")
        path = fetch_card_art(name, force=force)
        results[name] = path
        time.sleep(RATE_LIMIT)

    fetched = sum(1 for v in results.values() if v is not None)
    print(f"\nDone: {fetched}/{total} cards have art")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch Scryfall card art")
    parser.add_argument("--force", action="store_true", help="Re-download existing art")
    parser.add_argument("--card", type=str, help="Fetch art for a single card")
    args = parser.parse_args()

    if args.card:
        path = fetch_card_art(args.card, force=args.force)
        if path:
            print(f"Saved to: {path}")
        else:
            print("Failed to fetch art")
    else:
        fetch_all_art(force=args.force)
