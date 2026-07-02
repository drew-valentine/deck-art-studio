# Crystal frame — build spec (n+1 frame)

Source: cardconjurer `img/frames/crystal`. A custom "Crystal" frame — a shattered
blue-ice border with a **legendary crown** of ice shards at the top, dark
gray title/type bars, a scratched-stone rules box, and an ice P/T box.

This is the first frame built under the `docs/frame-editing-retro.md` playbook:
assets + reference are pinned on disk **before** any renderer code.

## Assets (in `shared/frames/crystal/`, 1500×2100 RGBA)

- `w/u/b/r/g/m/l/a.png` — per-color full frames (pinline + boxes baked in). `m`
  = multicolor/gold, `l` = land, `a` = artifact.
- `crowns/<color>.png` (1500×107) — legendary crown, ice shards; sits at the very
  top (y≈0), above the title bar.
- `pt/<color>.png` (294×170) — power/toughness box; `pt/c.png` = colorless.
- `pinline.png`, `title.png`, `type.png`, `rules.png`, `border.png` — overlay /
  mask pieces (title/type/border are palette masks).
- `maskRightHalf.png` — right-half mask → enables the left/right two-color
  gradient (composite color A full + color B masked to the right half), same
  approach as `_gradient_frame_image`.
- `*Thumb.png` previews were intentionally **not** downloaded.

## Reference

`reference_raw_layers.png` = base(`u`) + `crowns/u` + `pt/u` (at packCrystal.js
bounds) over neutral gray. This is the fidelity target the finished render must
match. (The original pin also composited `pinline.png`, but that file is a flat
palette *mask*, not a visual layer — it painted the whole card flat blue and
made the reference useless; regenerated without it.)

**Deliberate divergence:** the asset's rules box is only ~45% opaque, which
washes out the light rules text over bright art (measured 159/255 in the rules
band over white). The renderer self-composites the frame's own box region
(selected by the `rules.png` mask) to deepen it to ~214/255 alpha while keeping
the scratched-stone texture. Chrome elsewhere matches the raw assembly exactly
(0.000% pixel diff measured before the box deepening was added).

## Observed layout (approx, measured off the 1500×2100 reference — REMEASURE precisely first)

| Region     | y range (of 2100) | notes |
|------------|-------------------|-------|
| Crown      | 0 – ~110          | ice shards poke above the title bar |
| Title bar  | ~110 – ~215       | dark rounded bar; name text, mana cost right |
| Art window | ~230 – ~1560      | large; art sits behind the frame cutout |
| Type bar   | ~1600 – ~1705     | dark rounded bar; type line |
| Rules box  | ~1725 – ~2000     | scratched-stone; oracle/flavor, dark→needs LIGHT text |
| P/T box    | ~1180–1470 x, ~1880–2010 y | ice box, bottom-right |

Note: the rules box is DARK stone, so rules text must be light (unlike m15's
cream box). Confirm the exact text color against the reference.

## Build steps (fresh session — this is a full image-mode frame integration)

1. **Remeasure** every box precisely from the assets (alpha bounds of title/type/
   rules masks), don't trust the table above.
2. Add `'crystal'` to `FRAME_STYLES` (`mode:'image'`, `frame_set:'crystal'`,
   `layout:'crystal'`), plus a `CRYSTAL_LAYOUT` dict.
3. Compositing in `_render_image_frame`/`_compose_image_frame_base`: pick base by
   color key, overlay crown (always, or gate behind the existing legendary-crown
   logic), overlay P/T box for creatures. Reuse the `maskRightHalf.png` gradient
   path for two-color cards (goal item #5 — this frame has native gradient assets).
4. Text overlay (`_create_*_text_svg` analog): name/type in light text over the
   dark bars; oracle in **light** text over the dark stone rules box; P/T over the
   ice box. Honor `rules_font_size` (authoritative), color_overrides, box_opacity.
5. Register in the UI style picker + the crown toggle if gated.
6. **Fidelity-proof before calling it done:** side-by-side vs
   `reference_raw_layers.png` for 3–5 cards (short, wall-of-text, creature w/ P/T,
   land, multicolor). Full card, no crops. Only then commit as "good".
