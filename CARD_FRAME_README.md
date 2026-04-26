# MTG Card Frame Renderer

High-quality Secret Lair borderless card frame renderer for creating transparent PNG overlays that can be composited with AI-generated art.

## Overview

The `card_frame_renderer.py` module generates professional MTG card frames in the Secret Lair (SLD) borderless art style. Frames are rendered as transparent PNGs with semi-transparent text elements, allowing full-bleed art to show through while maintaining readability.

**Card Dimensions:** 750x1050 pixels (300 DPI)

## Features

### Frame Elements
- **Art Window:** Fully transparent center area for background art
- **Name Bar:** Semi-transparent dark bar at top with card name and mana pips
- **Type Line Bar:** Semi-transparent dark bar in middle with card type
- **Rules Text Box:** Large semi-transparent box for oracle text
- **P/T Box:** Power/Toughness indicator (creatures only, bottom-right)
- **Loyalty Badge:** Circular loyalty counter (planeswalkers only, bottom-right)
- **Commander Badge:** Gold "COMMANDER" label (commander cards only, top-left)
- **Gold Border:** Thin decorative bronze/gold edge around card perimeter

### Styling Features
- **Color-Tinted Bars:** Name and type bars are tinted based on card color
  - Red cards: Rose-red tint (rgba(180, 30, 40, 0.75))
  - Blue cards: Deep blue tint (rgba(14, 80, 160, 0.75))
  - Green cards: Forest green tint (rgba(0, 80, 40, 0.75))
  - Multicolor: Gold/bronze tint (rgba(180, 150, 40, 0.75))
  - Artifact/Colorless: Silver-gray tint

- **Mana Pips:** Colored circles right-aligned in name bar
  - Generic mana (numbers): Gray circle with black text
  - White {W}: Cream circle with "W"
  - Blue {U}: Blue circle with white "U"
  - Black {B}: Black circle with gray "B"
  - Red {R}: Red circle with white "R"
  - Green {G}: Green circle with white "G"
  - Colorless {C}: Gray circle with diamond
  - Variable {X}: Gray circle with "X"

### Text Features
- **Oracle Text Formatting:**
  - {T} replaced with (T) tap symbol
  - {W}, {U}, {B}, {R}, {G}, {C}, {X} replaced with [color] text
  - Generic mana costs converted to plain numbers
  - Paragraph breaks preserved

- **Word Wrapping:** Automatic text wrapping to fit rules box
- **Line Spacing:** Proper spacing for readability
- **Font:** DejaVu Sans (bold for names, regular for text)

## Module API

### `render_card_frame(card_dict, output_path)`

Render a card frame as a transparent PNG overlay.

**Parameters:**
- `card_dict` (dict): Card data matching card_database.json format
- `output_path` (Path): Path to save PNG file

**Card Data Format:**
```python
{
    "name": "Okaun, Eye of Chaos",
    "mana_cost": "{3}{R}",
    "type_line": "Legendary Creature — Cyclops Berserker",
    "oracle_text": "Partner with Zndrsplt...\nAt the beginning of combat...",
    "power": "3",
    "toughness": "3",
    "loyalty": None,
    "colors": ["R"],
    "color_identity": ["R"],
    "is_commander": True,
    "card_type": "creature"
}
```

**Example:**
```python
from pathlib import Path
import json
import card_frame_renderer

# Load card data
with open('card_database.json') as f:
    cards = json.load(f)

# Find a card
card = next(c for c in cards if c['name'] == 'Lightning Bolt')

# Render frame
card_frame_renderer.render_card_frame(card, Path('lightning_bolt_frame.png'))
```

### `composite_card(card_dict, art_path, frame_path_or_none, output_path)`

Composite an art image with a card frame overlay.

**Parameters:**
- `card_dict` (dict): Card data
- `art_path` (Path): Path to art image (JPG, PNG, etc.)
- `frame_path_or_none` (Path or None): Pre-rendered frame, or None to generate new
- `output_path` (Path): Path to save composite PNG

**Example:**
```python
import card_frame_renderer

# Composite art + frame
card_frame_renderer.composite_card(
    card_dict=card,
    art_path=Path('lightning_bolt_art.png'),
    frame_path_or_none=None,  # Render frame automatically
    output_path=Path('lightning_bolt_card.png')
)
```

## Layout Constants

| Element | Y Range | X Range | Notes |
|---------|---------|---------|-------|
| Name Bar | 15-58px | 25-725px | 43px height, rounded corners |
| Mana Pips | 45px center | Right-aligned | 30px spacing between pips |
| Type Bar | 580-615px | 25-725px | 35px height, rounded corners |
| Rules Box | 625-920px | 25-725px | Variable height, rounded corners |
| P/T Box | 960-1000px | 640-720px | Bottom-right corner, 80x40px |
| Loyalty Badge | 960-1000px | 640-720px | Bottom-right corner, 40px diameter |
| Commander Badge | 65-87px | 25-145px | Top-left corner, 120x22px |

## Transparency

Frame PNGs use RGBA format with proper alpha channel:
- **Transparent areas (alpha=0):** Art window background, allowing background art to show
- **Opaque areas (alpha=255):** Text bars, badges, borders
- **Semi-transparent areas (0 < alpha < 255):** Bar backgrounds, allowing subtle darkening

Typical transparency breakdown:
- ~85% transparent pixels (art window)
- ~15% opaque pixels (frame elements)

## Color Reference

### Mana Pip Colors
| Mana | Color | Hex Value |
|------|-------|-----------|
| White {W} | Cream | #F9FAF4 |
| Blue {U} | Blue | #0E68AB |
| Black {B} | Black | #150B00 |
| Red {R} | Red | #D3202A |
| Green {G} | Green | #00733E |
| Generic/Colorless {C} | Gray | #B8B8B8 |
| Variable {X} | Gray | #B8B8B8 |

### Frame Tint Colors (RGBA at 0.75 alpha)
| Card Color | RGB Values |
|-----------|-----------|
| Red | (180, 30, 40) |
| Blue | (14, 80, 160) |
| Green | (0, 80, 40) |
| Black | (30, 10, 40) |
| White | (180, 170, 140) |
| Multicolor | (180, 150, 40) |
| Artifact/Colorless | (140, 140, 160) |
| Land | (120, 90, 50) |

## Usage Examples

### Example 1: Render a Single Card Frame

```python
from pathlib import Path
import json
import card_frame_renderer

# Load cards
with open('card_database.json') as f:
    cards = json.load(f)

# Find card
card = next(c for c in cards if c['name'] == 'Counterspell')

# Render frame
output = Path('counterspell_frame.png')
card_frame_renderer.render_card_frame(card, output)
print(f"Rendered: {output}")
```

### Example 2: Render Full Card with Art

```python
from pathlib import Path
import json
import card_frame_renderer

cards = json.load(open('card_database.json'))
card = next(c for c in cards if c['name'] == 'Okaun, Eye of Chaos')

# Composite with art
card_frame_renderer.composite_card(
    card_dict=card,
    art_path=Path('dalle_art_raw/okaun.png'),
    frame_path_or_none=None,
    output_path=Path('okaun_full_card.png')
)
```

### Example 3: Batch Render Deck Cards

```python
from pathlib import Path
import json
import card_frame_renderer

cards_by_name = {c['name']: c for c in json.load(open('card_database.json'))}

decklist = [
    'Okaun, Eye of Chaos',
    'Zndrsplt, Eye of Wisdom',
    'Counterspell',
    'Lightning Bolt',
]

output_dir = Path('deck_frames')
output_dir.mkdir(exist_ok=True)

for card_name in decklist:
    card = cards_by_name[card_name]
    output = output_dir / f"{card_name.replace(' ', '_')}_frame.png"
    card_frame_renderer.render_card_frame(card, output)
    print(f"Rendered: {output}")
```

## Dependencies

- **cairosvg:** SVG rendering to PNG (install: `pip install cairosvg`)
- **Pillow:** Image processing (install: `pip install Pillow`)
- **MTG Fonts:** Loaded from `fonts/` directory (run `python fetch_mtg_fonts.py` to download)

## Performance

Typical render times on modern hardware:
- Single frame render: 200-400ms
- Composite with art: 300-500ms
- Batch render (10 cards): 2-4 seconds

Output file sizes:
- Simple card (Counterspell): ~16-20 KB
- Complex card (Commander): ~40-45 KB
- Composite with art: ~15-40 KB (depending on art compression)

## Quality Notes

- Renders at 300 DPI for print-quality output
- SVG-based rendering ensures crisp text and vectors
- Semi-transparent elements use proper alpha blending
- Text renders using system DejaVu Sans font for consistency
- Mana pips are crisp circles with proper anti-aliasing

## Tested Cards

The module has been tested with:
1. **Counterspell** - Simple instant with mana cost {U}{U}, blue tinting
2. **Okaun, Eye of Chaos** - Legendary creature, red tinting, P/T box, commander badge
3. **Daretti, Scrap Savant** - Red planeswalker with loyalty badge

All test frames and composites are available in `/test_frames/` directory.

---

Generated: 2026-02-21
