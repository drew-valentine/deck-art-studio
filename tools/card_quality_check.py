#!/usr/bin/env python3
"""Card render quality gate.

Renders a battery of representative cards across every frame style and runs
programmatic checks so we catch rendering defects (text overflowing/overlapping,
blank frames, out-of-bounds text) automatically instead of eyeballing one card.
Nothing is "done" until this passes.

Outputs:
  * a pass/fail report to stdout (exit 1 if any check fails)
  * a contact sheet PNG (all renders in a grid) for human review

Usage:
    python3 tools/card_quality_check.py                 # all styles
    python3 tools/card_quality_check.py --style godzilla # one style
    python3 tools/card_quality_check.py --out /tmp/qc.png
"""
import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cairosvg  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
import card_frame_renderer as cfr  # noqa: E402

W, H = cfr.CARD_WIDTH, cfr.CARD_HEIGHT

# ── Battery: representative edge cases ─────────────────────────────────────
LONG = ("Partner with Okaun, Eye of Chaos (When this creature enters, target "
        "player may put Okaun into their hand from their library, then shuffle.)\n"
        "At the beginning of combat on your turn, flip a coin until you lose a "
        "flip.\nWhenever a player wins a coin flip, draw a card.")
WALL = ("Whenever a creature you control dies, create a 1/1 token. "
        "At the beginning of your upkeep, draw a card, then discard a card. "
        "{2}, {T}: Target creature gets +2/+2 until end of turn. "
        "Whenever this creature attacks, each opponent loses 2 life. "
        "Sacrifice three creatures: This creature gains indestructible until "
        "end of turn. Cycling {2}.")
CARDS = [
    {'name': 'Counterspell', 'type_line': 'Instant', 'mana_cost': '{U}{U}',
     'colors': ['U'], 'color_identity': ['U'], 'oracle_text': 'Counter target spell.'},
    {'name': 'Grizzly Bears', 'type_line': 'Creature — Bear', 'mana_cost': '{1}{G}',
     'colors': ['G'], 'color_identity': ['G'], 'oracle_text': '', 'power': '2', 'toughness': '2'},
    {'name': 'Zndrsplt, Eye of Wisdom', 'type_line': 'Legendary Creature — Homunculus',
     'mana_cost': '{4}{U}', 'colors': ['U'], 'color_identity': ['U'],
     'oracle_text': LONG, 'power': '1', 'toughness': '4'},
    {'name': 'Verbose Horror', 'type_line': 'Creature — Nightmare', 'mana_cost': '{3}{B}{B}',
     'colors': ['B'], 'color_identity': ['B'], 'oracle_text': WALL, 'power': '4', 'toughness': '5'},
    {'name': 'Sol Ring', 'type_line': 'Artifact', 'mana_cost': '{1}',
     'colors': [], 'color_identity': [], 'oracle_text': '{T}: Add {C}{C}.'},
    {'name': 'Command Tower', 'type_line': 'Land', 'mana_cost': '',
     'colors': [], 'color_identity': [], 'oracle_text': '{T}: Add one mana of any color in your commander’s color identity.'},
    {'name': 'Caves of Koilos', 'type_line': 'Land', 'mana_cost': '',
     'colors': [], 'color_identity': ['B', 'W'],
     'oracle_text': '{T}: Add {C}.\n{T}: Add {W} or {B}. This land deals 1 damage to you.'},
    {'name': 'Atarka, World Render', 'type_line': 'Legendary Creature — Dragon',
     'mana_cost': '{5}{R}{G}', 'colors': ['R', 'G'], 'color_identity': ['R', 'G'],
     'oracle_text': 'Flying, trample\nWhenever a Dragon you control attacks, double its power until end of turn.',
     'power': '6', 'toughness': '4'},
    {'name': 'Brokkos, Apex of Forever', 'type_line': 'Legendary Creature — Nightmare Beast Elemental',
     'mana_cost': '{2}{B}{G}{U}', 'colors': ['B', 'G', 'U'], 'color_identity': ['B', 'G', 'U'],
     'oracle_text': 'Mutate {2}{G}{U}{B}\nTrample\nYou may cast Brokkos, Apex of Forever from your graveyard using its mutate ability.',
     'power': '6', 'toughness': '6', 'showcase_name': 'BIO-QUARTZ SPACEGODZILLA'},
    {'name': 'Aurelia, Exemplar of Justice and the Boros Legion Forever', 'type_line': 'Legendary Creature — Angel',
     'mana_cost': '{2}{R}{W}', 'colors': ['R', 'W'], 'color_identity': ['R', 'W'],
     'oracle_text': 'Flying\nMentor', 'power': '2', 'toughness': '5'},
]


def _placeholder_art():
    """A neutral gradient so text/frame legibility is judged, not the art."""
    art = Image.new('RGB', (W, H))
    px = art.load()
    for y in range(H):
        for x in range(0, W, 4):
            v = 70 + int(60 * (x / W))
            for dx in range(4):
                if x + dx < W:
                    px[x + dx, y] = (v, v - 10, v + 15)
    return art.convert('RGBA')


ART = _placeholder_art()


def render(card, style):
    """Render frame chrome+text over placeholder art; return (image, issues)."""
    fs = cfr.resolve_frame_settings(card, {'style': style})
    cardobj = cfr._build_card_data(card, fs)
    issues = []
    try:
        if fs.get('mode') == 'image':
            frame = cfr._render_image_frame(card, cardobj, fs)
        else:
            svg = cfr.create_card_frame_svg(cardobj, fs)
            png = cairosvg.svg2png(bytestring=svg.encode('utf-8'),
                                   output_width=W, output_height=H)
            frame = Image.open(io.BytesIO(png)).convert('RGBA')
    except Exception as e:
        return None, [f'render_error: {type(e).__name__}: {e}']

    issues.extend(fs.get('_quality', []))

    # blank-frame check (skip no-frame styles which are intentionally empty)
    if not fs.get('no_frame'):
        opaque = sum(frame.getchannel('A').histogram()[8:])  # alpha>8
        if opaque < 0.03 * W * H:
            issues.append(f'blank_frame: only {100*opaque/(W*H):.1f}% opaque chrome')

    composed = Image.alpha_composite(ART.copy(), frame).convert('RGB')
    return composed, issues


def check_textbox_legible(card, style):
    """Legibility gate: dark rules text must stay readable over ANY art. Render
    the frame over a DARK solid 'art' — the rules box must still be light enough
    for dark text to contrast. (A translucent-but-light showcase box passes; a
    dark or too-see-through box fails, because busy/dark art bleeds through and
    the text disappears — the exact defect from the earlier Godzilla frame.)

    Only image-mode frames (m15, godzilla) claim a readable box; the SVG frosted-
    glass / full-art styles are intentionally translucent and are skipped.
    """
    fs = cfr.resolve_frame_settings(card, {'style': style})
    if fs.get('mode') != 'image' or fs.get('no_frame'):
        return None
    cardobj = cfr._build_card_data(card, fs)
    try:
        frame = cfr._render_image_frame(card, cardobj, fs)
    except Exception:
        return None
    # Light-text frames (e.g. crystal's dark stone box) invert the failure mode:
    # the box must stay DARK even over bright art, or the light text vanishes.
    light_text = cfr.FRAME_STYLES.get(style, {}).get('rules_text') == 'light'
    worst_art = (240, 240, 240, 255) if light_text else (12, 12, 12, 255)
    over_worst = Image.alpha_composite(
        Image.new('RGBA', (W, H), worst_art), frame).convert('L')
    # Sample a band squarely inside every image frame's rules box (m15's runs to
    # the bottom; iko's ends ~y0.92) — staying above 0.90 avoids the bottom margin.
    x0, x1 = int(W * 0.15), int(W * 0.80)
    y0, y1 = int(H * 0.80), int(H * 0.89)
    crop = over_worst.crop((x0, y0, x1, y1))
    hist = crop.histogram()
    mean = sum(i * c for i, c in enumerate(hist)) / max(1, sum(hist))
    if light_text:
        # Box must read dark over bright art so light text contrasts.
        if mean > 165:
            return f'rules_box_illegible: brightness {mean:.0f}/255 over light art (light text would vanish)'
        return None
    # Threshold 90: cardconjurer's authentic gold-tan box reads ~110 over pure-dark
    # art (clearly legible over real art); wall-of-text cards dip to ~99 because
    # the dense dark text lowers the sample average, not because the box is dark.
    # Genuinely dark tints (blue ~63, black ~28) still fail.
    if mean < 90:
        return f'rules_box_illegible: brightness {mean:.0f}/255 over dark art (dark text would vanish)'
    return None


def contact_sheet(grid, styles, out_path):
    thumb_w = 220
    thumb_h = int(thumb_w * H / W)
    pad, label_h = 10, 22
    cols = len(styles)
    rows = len(grid)
    sheet_w = cols * (thumb_w + pad) + pad
    sheet_h = rows * (thumb_h + label_h + pad) + pad + 24
    sheet = Image.new('RGB', (sheet_w, sheet_h), (24, 24, 26))
    d = ImageDraw.Draw(sheet)
    for ci, st in enumerate(styles):
        d.text((pad + ci * (thumb_w + pad) + 4, 6), st, fill=(230, 210, 150))
    for ri, (card_name, imgs) in enumerate(grid):
        y0 = 24 + pad + ri * (thumb_h + label_h + pad)
        d.text((pad, y0), card_name[:34], fill=(200, 200, 210))
        for ci, st in enumerate(styles):
            im = imgs.get(st)
            x0 = pad + ci * (thumb_w + pad)
            if im is not None:
                sheet.paste(im.resize((thumb_w, thumb_h)), (x0, y0 + label_h))
    sheet.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--style', default=None, help='only this style')
    ap.add_argument('--out', default='/tmp/frametest/quality_contact_sheet.png')
    args = ap.parse_args()

    styles = [args.style] if args.style else list(cfr.FRAME_STYLES.keys())
    grid, failures, total = [], [], 0
    for card in CARDS:
        row = {}
        for st in styles:
            total += 1
            img, issues = render(card, st)
            row[st] = img
            opacity_issue = check_textbox_legible(card, st)
            if opacity_issue:
                issues.append(opacity_issue)
            for iss in issues:
                failures.append((card['name'], st, iss))
        grid.append((card['name'], row))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    sheet = contact_sheet(grid, styles, args.out)

    print(f"\nCard quality check — {len(CARDS)} cards × {len(styles)} styles = {total} renders")
    print(f"Contact sheet: {sheet}")
    if failures:
        print(f"\n❌ {len(failures)} issue(s):")
        for name, st, iss in failures:
            print(f"   [{st:9}] {name[:32]:32} {iss}")
        return 1
    print("\n✅ all checks passed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
