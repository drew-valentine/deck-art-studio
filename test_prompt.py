#!/usr/bin/env python3
"""Fast prompt iteration script for local SDXL generation tuning.

Round 9: Comprehensive validation of conditional IP scaling.
Strategy: IP 0.90 for creatures (anchor prevents cloning),
          IP 0.70 for non-creatures (lower scale prevents cloning).
Also validates using clean Art Style (no source name).

Usage:
    python test_prompt.py
"""

import argparse
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ──────────────────────────────────────────────────────────────
# ROUND 9 — Validation across diverse card types
#
# Findings:
#   R6: Card type anchoring ineffective. "Scene" prefix helps slightly.
#   R7: IP 0.70/0.60/0.50 prevents cloning while keeping style.
#       IP 0.90 works for creatures with anchor. IP 0.50 too low.
#
# Test: Conditional IP scale across 6 diverse card types.
# All use CLEAN Art Style (no source name) + technique keywords.
# ──────────────────────────────────────────────────────────────

ART_STYLE = "Flat vector illustration with vibrant limited color palettes"
TECHNIQUE = "thick black outlines, flat color shading, zero gradients, clean vector lines"

# Diverse card types with appropriate IP scale
TESTS = [
    {
        "label": "Mind Control\n(Enchantment, IP 0.70)",
        "prompt": (
            "a dark sorceress standing at the edge of a mist-shrouded forest, "
            "her long fingers weaving an ethereal thread around the twisted, "
            f"{ART_STYLE}, {TECHNIQUE}"
        ),
        "ip_scale": {"up": {"block_0": [0.70, 0.60, 0.50]}},
    },
    {
        "label": "Human Noble\n(Creature, IP 0.90)",
        "prompt": (
            "Human Noble creature, a regal human lord with sharp features "
            "and a flowing crimson cape standing before a grand stone throne, "
            f"{ART_STYLE}, {TECHNIQUE}"
        ),
        "ip_scale": {"up": {"block_0": [0.90, 0.80, 0.70]}},
    },
    {
        "label": "Sol Ring\n(Artifact, IP 0.70)",
        "prompt": (
            "a glowing golden ring hovering above a moss-covered pedestal "
            "in an ancient forest, pulsating with ethereal light, "
            f"{ART_STYLE}, {TECHNIQUE}"
        ),
        "ip_scale": {"up": {"block_0": [0.70, 0.60, 0.50]}},
    },
    {
        "label": "Beast\n(Creature, IP 0.90)",
        "prompt": (
            "Beast creature, a massive scaly behemoth with gnarled horns "
            "and twisted limbs rising from a misty forest, "
            f"{ART_STYLE}, {TECHNIQUE}"
        ),
        "ip_scale": {"up": {"block_0": [0.90, 0.80, 0.70]}},
    },
    {
        "label": "Devastation Tide\n(Sorcery, IP 0.70)",
        "prompt": (
            "a swirling oceanic vortex as if reality itself is unraveling, "
            "scattered creatures cling to the verge of the maelstrom, "
            f"{ART_STYLE}, {TECHNIQUE}"
        ),
        "ip_scale": {"up": {"block_0": [0.70, 0.60, 0.50]}},
    },
    {
        "label": "Propaganda\n(Enchantment, IP 0.70)",
        "prompt": (
            "a legion of faceless soldiers marching in perfect lockstep "
            "through a swirling vortex of silver and gray, "
            f"{ART_STYLE}, {TECHNIQUE}"
        ),
        "ip_scale": {"up": {"block_0": [0.70, 0.60, 0.50]}},
    },
]

NEGATIVE = (
    "photorealistic, realistic, photograph, gradient shading, "
    "soft shading, detailed textures, realistic lighting, 3D render, "
    "blurry, muddy, watercolor, impressionist"
)

STYLE_IMAGE = "decks/holly-s-propaganda/inspiration.png"
DEFAULT_OUTPUT = "/tmp/prompt_grid_r9.png"


def main():
    parser = argparse.ArgumentParser(description="Round 9: validation across card types")
    parser.add_argument("--style", default=STYLE_IMAGE, help="Inspiration image path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output grid image path")
    args = parser.parse_args()

    from local_image_generator import get_generator

    print("[test] Loading SDXL Lightning...")
    t0 = time.time()
    gen = get_generator()
    gen.load_model("sdxl-lightning-4step")
    print(f"[test] Model ready in {time.time() - t0:.1f}s")

    style_img = None
    style_path = Path(args.style)
    if style_path.exists():
        style_img = Image.open(style_path).convert("RGB")
        print(f"[test] Style image: {style_path}")
    else:
        print(f"[test] No style image at {style_path}")
        return

    results = []
    total = len(TESTS)

    for idx, test in enumerate(TESTS, 1):
        # Set IP scale per card type
        gen._pipeline.set_ip_adapter_scale(test["ip_scale"])

        print(f"\n[test] [{idx}/{total}] {test['label'].split(chr(10))[0]}")
        print(f"  IP: {list(test['ip_scale']['up']['block_0'])}")
        print(f"  prompt: {test['prompt'][:100]}...")

        t1 = time.time()
        img = gen.generate(
            prompt=test["prompt"],
            negative_prompt=NEGATIVE,
            width=512,
            height=768,
            style_image=style_img,
            guidance=2.5,
            steps=8,
        )
        elapsed = time.time() - t1
        print(f"  done in {elapsed:.1f}s")
        results.append((test["label"], img))

    # Build grid (3 rows x 2 cols)
    cols = 2
    rows = 3
    label_h = 40
    header_h = 10
    cell_w = 384
    cell_h = int(384 * 768 / 512) + label_h
    grid_w = cols * cell_w
    grid_h = rows * cell_h + header_h

    grid = Image.new("RGB", (grid_w, grid_h), (30, 30, 40))
    draw = ImageDraw.Draw(grid)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except Exception:
        font = ImageFont.load_default()

    img_h = int(384 * 768 / 512)
    for i, (label, img) in enumerate(results):
        r, c = divmod(i, cols)
        x = c * cell_w
        y = r * cell_h + header_h
        resized = img.resize((cell_w, img_h), Image.Resampling.LANCZOS)
        grid.paste(resized, (x, y + label_h))
        for li, line in enumerate(label.split('\n')):
            draw.text((x + 4, y + 4 + li * 16), line, fill=(200, 200, 200), font=font)

    grid.save(args.output)
    print(f"\n[test] Grid saved to {args.output}")
    print(f"[test] {total} images in {time.time() - t0:.1f}s total")


if __name__ == "__main__":
    main()
