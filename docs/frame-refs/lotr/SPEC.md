# LOTR "Ring" frame — build spec

Source: cardconjurer `img/frames/lotr` (pack: `js/frames/packRing.js`,
`card.version = 'ring'`). The Tales of Middle-earth showcase: a circular art
window wreathed in Ring-inscription script, wavy legendary crown, dark blue
title/type bars with light text, parchment rules box with dark text, P/T
plate, and a gold holo stamp at the bottom center.

## Assets (in `shared/frames/lotr/`, 1500×2100 RGBA)

- `w/u/b/r/g/m/l/a.png` — per-color full frames (circular art cutout baked in).
- `crown/<color>.png` (1500×272) — wavy legendary crown across the very top.
- `pt/<color>.png` (268×134) — P/T plate.
- `stamp/<color>.png` + `stamp/gray.png` (212×95) — holo stamp, bottom center.
- `title/type/rules/border.png` — palette masks (recolor targets, not layers).
- `maskRightHalf.png` — native two-color gradient support.

## Pack bounds → 750×1050 px space

| Element | packRing.js (of 1500×2100) | 750×1050 |
|---|---|---|
| Crown | (0, 0, 1500, 272) | (0, 0, 750, 136) |
| P/T plate | (1148, 1857, 268, 134) | (574, 928.5, 134, 67) |
| Holo stamp | (644, 1893, 212, 95) | (322, 946.5, 106, 47.5) |
| Art window | (149, 252, 1202, 918) — circular | (74.5, 126, 601, 459) |
| Title text | x 0.0854, y 120/2100, size 0.0381, **white** | x 64, y≈60–117, ~40pt |
| Type text | x 0.0854, y 0.5664, size 0.0324, **white** | x 64, y≈595–652, ~34pt |
| Rules text | x 0.086, y 0.6303, w 0.828, h 0.2875, default **dark** | x 64.5, y 662–964, ~38pt max |
| P/T text | x 1184/1500, y 1887/2100, size 0.0372, **white** | center ≈(643, 963), ~39pt |

## Build steps

1. **Remeasure** the bar/box alpha bounds from the assets before coding.
2. `FRAME_STYLES['lotr']` — mode 'image', frame_set 'lotr', layout 'lotr',
   `controls: {'colors': ['text'], ...}`; add `LOTR_LAYOUT`.
3. Compositing: base color frame; crown gated on Legendary (gradient-aware);
   P/T plate for creatures at pack bounds; holo stamp always (per-color).
   Two-color gradient reuses `_gradient_frame_image` (native mask exists).
4. Text overlay: white title/type on the dark bars, DARK rules text on the
   parchment box, white P/T. Rules Text Size ceiling + P/T avoid-region wrap
   (plate top y928 → avoid ≈ (920, narrow to x566)).
5. Print-safe check: P/T bottom y995.5 → 54.5px = 4.6mm ✓; stamp y994 ✓.
6. **Fidelity proof before "done"**: side-by-side vs `reference_raw_layers.png`
   for 5 representative cards; 0-diff chrome check vs the raw assembly.

## Deliberate divergence: bottom mask

The asset's baked black rounded bottom (border.png region, y830–1050) covered
too much art. The renderer extracts it via border.png, erases it from the
base frame, and re-composites it squashed to 70% height anchored at the card
bottom (side curves begin ~y896 instead of ~y830). The `bottom_mask` setting
(designer toggle, default on) hides it entirely — art runs to the bottom
edge. Chrome matched the raw assembly at 0.000% diff before this rework.
