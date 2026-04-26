"""
Build reference collage images for gpt-image-1 art generation.

Combines the Okaun style reference (left) with the original Scryfall card art
(right) into a single image that can be passed to images.edit() — giving the
model both the target art style AND the card's subject matter.

Layout:  [ Okaun style ref | Original card art ]
         [ with label      | with label         ]

Output: 1024×1024 PNG (square, optimal for images.edit input)
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

COLLAGE_DIR = Path("ref_collages")
STYLE_REF = Path("okaun.png")

# Collage dimensions (square input for images.edit)
COLLAGE_W = 1024
COLLAGE_H = 1024
HALF_W = COLLAGE_W // 2
LABEL_H = 36       # height reserved for label text at bottom
GAP = 4            # gap between the two halves


def build_collage(card_name: str, scryfall_art_path: Path,
                  style_ref_path: Path = None,
                  output_dir: Path = None) -> Path | None:
    """Create a side-by-side reference collage.

    Left half:  Okaun style reference (or custom style ref)
    Right half: Original Scryfall card art

    Returns path to the saved collage PNG, or None if inputs missing.
    """
    if style_ref_path is None:
        style_ref_path = STYLE_REF

    if not style_ref_path.exists():
        print(f"Style reference not found: {style_ref_path}")
        return None

    if not scryfall_art_path or not Path(scryfall_art_path).exists():
        print(f"Scryfall art not found for {card_name}")
        return None

    if output_dir is None:
        output_dir = COLLAGE_DIR
    output_dir.mkdir(exist_ok=True)

    # Load images
    style_img = Image.open(style_ref_path).convert('RGBA')
    art_img = Image.open(scryfall_art_path).convert('RGBA')

    # Create collage canvas
    collage = Image.new('RGBA', (COLLAGE_W, COLLAGE_H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(collage)

    # Available area for each image (account for gap and labels)
    img_w = HALF_W - GAP
    img_h = COLLAGE_H - LABEL_H

    # Resize style reference to fit left half (maintain aspect ratio)
    style_resized = _fit_image(style_img, img_w, img_h)
    # Center it in the left half
    sx = (img_w - style_resized.width) // 2
    sy = (img_h - style_resized.height) // 2
    collage.paste(style_resized, (sx, sy), style_resized)

    # Resize original art to fit right half
    art_resized = _fit_image(art_img, img_w, img_h)
    ax = HALF_W + GAP + (img_w - art_resized.width) // 2
    ay = (img_h - art_resized.height) // 2
    collage.paste(art_resized, (ax, ay), art_resized)

    # Draw divider line
    div_x = HALF_W
    draw.line([(div_x, 0), (div_x, COLLAGE_H)], fill=(80, 80, 80, 200), width=2)

    # Add labels
    try:
        font = ImageFont.truetype("fonts/MPlantin.ttf", 20)
    except Exception:
        font = ImageFont.load_default()

    label_y = COLLAGE_H - LABEL_H + 6
    draw.text((10, label_y), "STYLE REFERENCE", fill=(200, 200, 200), font=font)
    draw.text((HALF_W + GAP + 10, label_y), "ORIGINAL ART", fill=(200, 200, 200), font=font)

    # Save
    slug = _slug(card_name)
    out_path = output_dir / f"{slug}_ref.png"
    collage_rgb = collage.convert('RGB')
    collage_rgb.save(out_path, 'PNG')

    return out_path


def _fit_image(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Resize image to fit within max dimensions, maintaining aspect ratio."""
    ratio = min(max_w / img.width, max_h / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _slug(name: str) -> str:
    import re
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


if __name__ == "__main__":
    # Quick test
    import sys
    from fetch_scryfall_art import fetch_card_art, ART_DIR

    card_name = sys.argv[1] if len(sys.argv) > 1 else "Academy Ruins"

    # Fetch original art if needed
    art_path = fetch_card_art(card_name)
    if art_path:
        collage_path = build_collage(card_name, art_path)
        if collage_path:
            print(f"Collage saved: {collage_path}")
        else:
            print("Failed to build collage")
    else:
        print("Failed to fetch art")
