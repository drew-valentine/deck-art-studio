# 8th Edition frame — build spec

Source: cardconjurer `img/frames/8th` (pack: `js/frames/pack8th.js`,
`card.version = '8th'`). The iconic 2003–2014 "modern" border: metallic
beveled title/type bars with BLACK text, inset rectangular art window,
white-ish rules box, no legendary crown (pre-M15 era).

## Assets (in `shared/frames/8th/`, 1500×2100 RGBA)

- `w/u/b/r/g/m/a/c/l.png` — per-color full frames.
- `wl/ul/bl/rl/gl/ml.png` — **colored land frames** (land that produces a
  color gets that color's land variant — pick by land color identity).
- `pt/<color>.png` (322×176) — P/T box.
- `pinline/title/type/rules/frame.png` — palette masks (not visual layers).
- No crown assets; no maskRightHalf (use our own gradient mask util).

## Pack bounds → 750×1050 px space

| Element | pack8th.js | 750×1050 |
|---|---|---|
| P/T box | (0.7227, 0.8796, 0.2147, 0.0839) | (542, 923.6, 161, 88.1) |
| Art window | (0.088, 0.12, 0.824, 0.4348) — inset rect | (66, 126, 618, 456.5) |
| Title text | x 0.09, y 0.0629, size 0.0429, **black** (matrixb) | x 67.5, y≈66–111, ~45pt |
| Type text | x 0.1, y 0.572, size 0.0358, **black** | x 75, y≈600–638, ~38pt |
| Rules text | x 0.1, y 0.6277, w 0.8, h 0.2691, **black** | x 75, y 659–941.6, ~38pt max |
| P/T text | x 0.7667, y 0.8953, size 0.0443, **black** | center ≈(626, 963), ~46.5pt |

Note: title/type use the Matrix font on real cards (we render with our
beleren-family stack — acceptable; note in fidelity proof).

## Build steps

1. **Remeasure** bar/box alpha bounds from the assets first.
2. `FRAME_STYLES['8th']` — mode 'image', frame_set '8th', layout '8th',
   label "8th Edition"; add `EIGHTH_LAYOUT`.
3. Compositing: `_determine_color_key`; **lands with 1-color identity use the
   `<c>l.png` variant** (new lookup rule); two-color → gradient via
   `_gradient_frame_image` (also works for `<c>l` pairs); P/T box at pack
   bounds. No crown.
4. Text overlay: black text everywhere (light metallic bars + white box).
5. Rules Text Size ceiling + P/T avoid region (box top y923.6 → avoid ≈
   (915, narrow to x534)).
6. Print-safe: P/T bottom y1011.7 → 38px = 3.2mm ✓ (just inside 3mm).
7. **Fidelity proof before "done"** vs `reference_raw_layers.png` + real
   8th-edition card scans; 0-diff chrome check vs raw assembly.
