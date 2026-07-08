#!/usr/bin/env python3
"""
Fetch flavor text for all cards in card_database.json from Scryfall API.

Usage:
    python fetch_flavor_text.py          # Add flavor text to all cards missing it
    python fetch_flavor_text.py --force  # Re-fetch all flavor text even if present

Scryfall API is free, no key needed. Rate-limited to 100ms between requests.
"""

import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "card_database.json"
SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"
SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"

HEADERS = {
    "User-Agent": "MTGProxyDeckBuilder/1.0",
    "Accept": "application/json",
}


def _api_get(url: str) -> dict:
    """Make a GET request to Scryfall API."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def fetch_card_data(card_name: str) -> dict:
    """Fetch a card's data from Scryfall by exact name (with fuzzy fallback)."""
    # Try exact match first
    params = urllib.parse.urlencode({"exact": card_name})
    try:
        return _api_get(f"{SCRYFALL_NAMED}?{params}")
    except urllib.error.HTTPError:
        pass
    except Exception as e:
        print(f"  [WARN] Exact lookup failed for '{card_name}': {e}")

    # Fuzzy fallback
    params = urllib.parse.urlencode({"fuzzy": card_name})
    try:
        return _api_get(f"{SCRYFALL_NAMED}?{params}")
    except Exception as e:
        print(f"  [WARN] Could not fetch '{card_name}': {e}")
        return {}


def fetch_flavor_across_printings(card_name: str) -> str:
    """Search all printings of a card to find ANY version with flavor text."""
    # Scryfall search: find all printings, prefer ones with flavor text
    q = urllib.parse.urlencode({
        "q": f'!"{card_name}" include:extras',
        "unique": "prints",
        "order": "released",
        "dir": "desc",
    })
    try:
        data = _api_get(f"{SCRYFALL_SEARCH}?{q}")
        for printing in data.get("data", []):
            ft = printing.get("flavor_text", "")
            if ft:
                return ft
    except Exception:
        pass
    return ""


def main():
    force = "--force" in sys.argv

    with open(DB_PATH) as f:
        cards = json.load(f)

    updated = 0
    skipped = 0
    no_flavor = 0

    for i, card in enumerate(cards):
        name = card["name"]

        if not force and card.get("flavor_text"):  # skip if already has non-empty flavor
            skipped += 1
            continue
        # Also clear empty strings from previous failed runs so we retry
        if card.get("flavor_text") == "":
            del card["flavor_text"]

        print(f"[{i+1}/{len(cards)}] Fetching: {name}...")
        data = fetch_card_data(name)
        flavor = data.get("flavor_text", "")

        # If default printing has no flavor, search across ALL printings
        if not flavor:
            time.sleep(0.12)
            flavor = fetch_flavor_across_printings(name)
            if flavor:
                print(f"  ✓ (alt printing) ", end="")

        if flavor:
            card["flavor_text"] = flavor
            print(f"  ✓ \"{flavor[:60]}...\"" if len(flavor) > 60
                  else f"  ✓ \"{flavor}\"")
            updated += 1
        else:
            card["flavor_text"] = ""
            print(f"  — No flavor text in any printing")
            no_flavor += 1

        # Scryfall rate limit: 100ms between requests
        time.sleep(0.12)

    # Save updated database atomically — write to a temp file then replace, so
    # a crash mid-write can't truncate the input database.
    tmp_path = DB_PATH.with_name(DB_PATH.name + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(cards, f, indent=2)
    os.replace(tmp_path, DB_PATH)

    print(f"\nDone! Updated: {updated}, No flavor: {no_flavor}, Skipped: {skipped}")
    print(f"Database saved to {DB_PATH}")


if __name__ == "__main__":
    main()
