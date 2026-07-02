# Planeswalker frame — build spec

Source: cardconjurer `img/frames/planeswalker/regular` (pack
`packPlaneswalkerRegular.js` + `versionPlaneswalker.js`,
`card.version = 'planeswalkerRegular'`). The authentic M15-era planeswalker
frame: near-full-height art window, ability area TRANSPARENT in the frame
(art ghosts through translucent ability bands), alternating light/dark
bands with gradient transition strips, raster loyalty-cost badges on the
left, baked dark loyalty shield bottom-right.

## Assets (shared/frames/planeswalker/, frames 1500×2100)

- `regular/planeswalkerFrame{W,U,B,R,G,M,A}.png` — per-color frames
  (no colorless/land; fall back artifact→multi).
- `abilityLineEven/Odd(.Darkened).png` (802×26) — gradient transition strips
  between ability bands.
- `planeswalkerPlus/Minus/Neutral.png` (~140×100) — loyalty cost badges.
- `maskLoyalty.png`, `planeswalkerMaskText.png` — masks (not composited).

## Pack bounds → 750×1050

| Element | pack | 750×1050 |
|---|---|---|
| Art window | (0.068, 0.101, 0.864, 0.8143) | (51, 106, 648, 855) |
| Title text | x 0.0867, y 0.0372, size 0.0381, dark | x 65, y≈39–96, ~40pt |
| Type text | x 0.0867, y 0.5625, size 0.0324, dark | x 65, y≈591–648, ~34pt |
| Ability area | x 0.1167 w 0.8094; text x 0.18 w 0.7467, y 0.6239→ | bands x 87–694, text x 135 w 560, y 655–990 |
| Loyalty text | (0.806, 0.902, 0.14, 0.0372), white | center ≈(657, 966) |

## Build notes

- Band layout computed from measured ability text (fitting loop, never
  truncates): heights proportional to text, min height for the badge,
  scaled to fill the full ability area like real cards.
- Bands alternate translucent white / #a4a4a4 (light/dark); black ability
  text on both; transition strips pasted at band boundaries.
- Badges chrome-side (PIL paste, scaled ~70×50) with white cost text from
  the overlay; starting loyalty white over the baked shield.
- m15-style planeswalkers AUTO-route to this frame (real cards always use
  the pw frame); also registered as an explicit 'planeswalker' style.
