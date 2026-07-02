# Mystical Archive frame — build spec

Source: cardconjurer `img/frames/mysticalArchive` (pack:
`js/frames/packMysticalArchive.js`, `card.version = 'mysticalArchive'`).
The Strixhaven Mystical Archive showcase: ornate color+gold arabesque
frame over parchment, huge arched art window, decorated parchment rules
panel with dark text. Real MSA cards were instants/sorceries only, but
cardconjurer ships P/T plates so creatures work too.

## Assets (in `shared/frames/mysticalArchive/`, normalized to 1500×2100 RGBA)

- `w/u/b/r/g/m/a/c.png` — per-color full frames (`c` doubles as land),
  plus `gold.png` (alt gold trim) and `paper.png` (parchment backdrop —
  check whether frames are already opaque before compositing it).
- `crowns/<color>.png` (1500×97) — arched top pinline strip.
- `pt/<color>.png` (424×213 native) + `pt/maskInner/maskOuter.png`.
- `pinline.png`, `border.png` — masks/overlays.
- No maskRightHalf — use our own gradient mask util for two-color.

## Pack bounds → 750×1050 px space

| Element | packMysticalArchive.js | 750×1050 |
|---|---|---|
| Crown strip | (0, 0, 1500, 97) | (0, 0, 750, 48.5) |
| P/T plate | (1135, 1848, 317, 159) | (567.5, 924, 158.5, 79.5) |
| Art window | (0, 0.1205, 1, 0.7539) — huge, arched | (0, 126.5, 750, 791.6) |
| Title text | x 0.0854, y 0.0522, size 0.0381, **dark** | x 64, y≈55–112, ~40pt |
| Type text | x 0.0854, y 0.5664, size 0.0324, **dark** | x 64, y≈595–652, ~34pt |
| Rules text | x 0.0934, y 0.6303, w 0.8134, h 0.2875, **dark** | x 70, y 662–964, ~38pt max |
| P/T text | x 0.7928, y 0.902, size 0.0372, **dark** | center ≈(646, 966), ~39pt |

## Build steps

1. **Remeasure** panel alpha bounds first; verify whether `paper.png` is
   needed under the color frames (check frame alpha coverage).
2. `FRAME_STYLES['msa']` — mode 'image', frame_set 'mysticalArchive',
   layout 'msa', label "Mystical Archive"; add `MSA_LAYOUT`.
3. Compositing: base frame by color key (land→'c'); crown strip decision at
   build (legendary-gated vs always — compare real MSA scans); P/T plate for
   creatures; two-color via `_gradient_frame_image`.
4. Text overlay: dark text on parchment everywhere; Rules Text Size ceiling +
   P/T avoid region (plate top y924 → avoid ≈ (916, narrow to x559)).
5. Print-safe: P/T bottom y1003.5 → 46.5px = 3.9mm ✓.
6. **Fidelity proof before "done"** vs `reference_raw_layers.png` + a real
   MSA scan (e.g. Strixhaven Counterspell); 0-diff chrome check.
