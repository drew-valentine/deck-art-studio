#!/usr/bin/env python3
"""
=============================================================================
  HEADS I WIN, TAILS YOU LOSE — AI Art Generation & Card Assembly Pipeline
=============================================================================

This script:
  1. Generates unique AI art for every card via gpt-image-1 with reference images
  2. Uses card_frame_renderer to composite art onto print-ready card frames
  3. Outputs 750x1050px PNGs at 300 DPI — ready for PrintingProxies.com

The art prompts are in art-only format with a strong psychedelic stained-glass
Art Nouveau style emphasis. Reference images guide the visual style.

Usage:
  python3 generate_deck_art.py              # Generate all cards
  python3 generate_deck_art.py --card "Sol Ring"   # Generate one specific card
  python3 generate_deck_art.py --skip-existing      # Skip cards already generated
  python3 generate_deck_art.py --art-only           # Generate art only, no card frame
  python3 generate_deck_art.py --dry-run             # Preview prompts, don't call API
  python3 generate_deck_art.py --ref-image okaun.png  # Use specific reference image

Cost estimate: ~$10-15 for all 87 cards at gpt-image-1 pricing

Requires: pip install openai Pillow
"""

import json
import os
import sys
import time
import getpass
import argparse
import hashlib
import base64
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy imports — we check for these after parsing args so --dry-run works
# without Pillow installed
# ---------------------------------------------------------------------------
PIL_AVAILABLE = False
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
CARD_DB = SCRIPT_DIR / "card_database.json" if (SCRIPT_DIR / "card_database.json").exists() else SCRIPT_DIR.parent / "card_database.json"
ART_PROMPTS = SCRIPT_DIR / "art_prompts.json" if (SCRIPT_DIR / "art_prompts.json").exists() else SCRIPT_DIR.parent / "art_prompts.json"

# Check parent directory too
for candidate in [SCRIPT_DIR, SCRIPT_DIR.parent, Path(".")]:
    if (candidate / "card_database.json").exists():
        CARD_DB = candidate / "card_database.json"
    if (candidate / "art_prompts.json").exists():
        ART_PROMPTS = candidate / "art_prompts.json"

OUTPUT_DIR = SCRIPT_DIR / "dalle_cards"
RAW_ART_DIR = SCRIPT_DIR / "dalle_art_raw"

# Card dimensions for print
CARD_W, CARD_H = 750, 1050
DPI = 300

# Style is now baked into the prompts themselves (no prefix needed)
STYLE_PREFIX = ""

def name_to_slug(name):
    return name.lower().replace(' ', '_').replace(',', '').replace("'", "").replace('-', '_')

# ===========================================================================
#  GPT-IMAGE-1 Art Generation with Reference Image
# ===========================================================================

def generate_art(client, card_name, prompt_text, output_path, ref_image_path, dry_run=False):
    """Call gpt-image-1 API with reference image and save the result."""
    full_prompt = STYLE_PREFIX + prompt_text

    if dry_run:
        print(f"  [DRY RUN] Would generate with gpt-image-1")
        print(f"  Reference: {ref_image_path.name if ref_image_path else 'None'}")
        print(f"  Prompt ({len(full_prompt)} chars): {full_prompt[:100]}...")
        return True

    try:
        # Build style instruction with reference
        style_instruction = (
            "Generate art in exactly this psychedelic stained-glass Art Nouveau style. "
            "Match the bold black outlines, vibrant saturated colors, and dreamlike "
            "composition of this reference image. Create ONLY the art, no text or "
            f"card frames. Subject: {full_prompt}"
        )

        if ref_image_path and ref_image_path.exists():
            # Use images.edit() to pass reference image for style matching
            with open(ref_image_path, 'rb') as img_file:
                response = client.images.edit(
                    model="gpt-image-1",
                    image=img_file,
                    prompt=style_instruction,
                    size="1024x1536",
                    quality="high",
                    n=1,
                )
        else:
            # Fallback: generate without reference
            response = client.images.generate(
                model="gpt-image-1",
                prompt=style_instruction,
                size="1024x1536",
                quality="high",
                n=1,
            )

        # Extract the generated image
        image_data = response.data[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if hasattr(image_data, 'b64_json') and image_data.b64_json:
            image_bytes = base64.standard_b64decode(image_data.b64_json)
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
        elif hasattr(image_data, 'url') and image_data.url:
            urllib.request.urlretrieve(image_data.url, str(output_path))
        else:
            print(f"  ✗ No image data in response")
            return False

        # Save metadata
        meta_path = output_path.with_suffix('.txt')
        with open(meta_path, 'w') as f:
            f.write(f"Card: {card_name}\n")
            f.write(f"Model: gpt-image-1\n")
            f.write(f"Reference: {ref_image_path.name}\n")
            f.write(f"Prompt: {full_prompt}\n")

        print(f"  ✓ Art saved to {output_path.name}")
        return True

    except Exception as e:
        print(f"  ✗ Error: {e}")
        err_path = output_path.with_suffix('.error')
        with open(err_path, 'w') as f:
            f.write(str(e))
        return False


# ===========================================================================
#  Card Frame Compositing (via card_frame_renderer)
# ===========================================================================

try:
    from card_frame_renderer import composite_card as render_composite
    RENDERER_AVAILABLE = True
except ImportError:
    RENDERER_AVAILABLE = False


# ===========================================================================
#  Main Pipeline
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Generate AI art for your MTG deck with gpt-image-1")
    parser.add_argument('--card', type=str, help='Generate only this specific card')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip cards that already have generated art')
    parser.add_argument('--art-only', action='store_true',
                        help='Generate raw art only, skip card frame compositing')
    parser.add_argument('--composite-only', action='store_true',
                        help='Skip art generation, only composite existing art onto frames')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview prompts without calling the API')
    parser.add_argument('--ref-image', type=str, default='okaun.png',
                        help='Reference image to use for style (default: okaun.png)')
    parser.add_argument('--output-dir', type=str, default=None,
                        help='Output directory (default: dalle_cards/ next to this script)')
    parser.add_argument('--delay', type=float, default=1.0,
                        help='Seconds between API calls (default: 1.0)')
    args = parser.parse_args()

    # --- Load data ---
    print("=" * 60)
    print("  HEADS I WIN — GPT-IMAGE-1 Art Generation Pipeline")
    print("=" * 60)

    if not CARD_DB.exists():
        print(f"\n✗ Card database not found at {CARD_DB}")
        print("  Make sure card_database.json is in the same directory as this script")
        sys.exit(1)

    with open(CARD_DB) as f:
        cards = json.load(f)

    prompts_map = {}
    if ART_PROMPTS.exists():
        with open(ART_PROMPTS) as f:
            for entry in json.load(f):
                prompts_map[entry['name']] = entry['prompt']

    # Resolve reference image path
    ref_image_path = SCRIPT_DIR / args.ref_image
    if not ref_image_path.exists():
        print(f"\n✗ Reference image not found at {ref_image_path}")
        sys.exit(1)

    # Filter to specific card if requested
    if args.card:
        cards = [c for c in cards if c['name'].lower() == args.card.lower()]
        if not cards:
            print(f"\n✗ Card '{args.card}' not found in database")
            sys.exit(1)

    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    raw_dir = RAW_ART_DIR

    print(f"\n  Cards to process: {len(cards)}")
    print(f"  Raw art output:   {raw_dir}")
    print(f"  Final cards:      {out_dir}")
    print(f"  Reference image:  {ref_image_path.name}")
    print(f"  Skip existing:    {args.skip_existing}")
    print(f"  Dry run:          {args.dry_run}")

    # --- API key ---
    client = None
    if not args.dry_run and not args.composite_only:
        print("\n" + "-" * 60)
        api_key = getpass.getpass("  Enter your OpenAI API key: ")
        if not api_key.strip():
            print("  ✗ No API key provided. Exiting.")
            sys.exit(1)

        from openai import OpenAI
        client = OpenAI(api_key=api_key.strip())
        print("  ✓ API client initialized")

    # --- Generate ---
    print("\n" + "=" * 60)
    print("  GENERATING ART")
    print("=" * 60)

    success = 0
    failed = 0
    skipped = 0

    for i, card in enumerate(cards):
        slug = name_to_slug(card['name'])
        art_path = raw_dir / f"{slug}.png"
        card_path = out_dir / f"{slug}.png"

        print(f"\n[{i+1}/{len(cards)}] {card['name']}")

        # --- Step 1: Generate art ---
        if not args.composite_only:
            if args.skip_existing and art_path.exists():
                print(f"  → Art exists, skipping generation")
                skipped += 1
            else:
                prompt = prompts_map.get(card['name'], card.get('art_description', card['name']))
                ok = generate_art(client, card['name'], prompt, art_path, ref_image_path, dry_run=args.dry_run)
                if ok:
                    success += 1
                else:
                    failed += 1
                    continue  # Don't try to composite if art gen failed

                if not args.dry_run:
                    time.sleep(args.delay)  # Rate limiting

        # --- Step 2: Composite art with card frame ---
        if not args.art_only and not args.dry_run:
            if art_path.exists():
                if RENDERER_AVAILABLE:
                    ok = render_composite(card, str(art_path), None, str(card_path))
                    if ok:
                        print(f"  ✓ Card saved to {card_path.name}")
                    else:
                        print(f"  ✗ Compositing failed")
                else:
                    print("  ⚠ card_frame_renderer not available, skipping composite")
            else:
                print(f"  → No art file found, skipping composite")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Generated: {success}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {failed}")

    if failed > 0:
        print(f"\n  Tip: Re-run with --skip-existing to retry only failed cards")

    if success > 0 or skipped > 0:
        print(f"\n  Raw art:     {raw_dir}/")
        print(f"  Final cards: {out_dir}/")
        print(f"\n  Upload the PNGs from {out_dir}/ to PrintingProxies.com!")

    print()


if __name__ == '__main__':
    main()
