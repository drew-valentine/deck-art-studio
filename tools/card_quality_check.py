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
    {'name': 'Jace, the Mind Sculptor', 'type_line': 'Legendary Planeswalker — Jace',
     'mana_cost': '{2}{U}{U}', 'colors': ['U'], 'color_identity': ['U'], 'loyalty': '3',
     'oracle_text': '+2: Look at the top card of target player’s library. You may put that card '
                    'on the bottom of that player’s library.\n0: Draw three cards, then put two '
                    'cards from your hand on top of your library in any order.\n−1: Return target '
                    'creature to its owner’s hand.\n−12: Exile all cards from target player’s '
                    'library, then that player shuffles their hand into their library.'},
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
    glass styles are intentionally translucent and are skipped.
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


def check_rules_render_full(card, style):
    """Rules text must ALWAYS render completely and never overflow — even at
    the slider's maximum (60pt), the renderer must clamp to the max fitting
    size. Renders the style's real text path and asserts (a) no overflow
    quality note, (b) the oracle's final word made it into the output."""
    oracle = card.get('oracle_text') or ''
    if not oracle.strip():
        return None
    fs = cfr.resolve_frame_settings(card, {'style': style, 'rules_font_size': 60})
    if fs.get('no_frame') or not fs.get('show_oracle', True):
        return None
    cardobj = cfr._build_card_data(card, fs)
    try:
        if fs.get('frame_set') == 'iko':
            svg = cfr._create_iko_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'crystal':
            svg = cfr._create_crystal_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'lotr':
            svg = cfr._create_lotr_text_svg(cardobj, fs)
        elif fs.get('frame_set') == '8th':
            svg = cfr._create_8th_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'mysticalArchive':
            svg = cfr._create_msa_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'sncArtDeco':
            svg = cfr._create_artdeco_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'neoSamurai':
            svg = cfr._create_samurai_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'etched':
            svg = cfr._create_etched_text_svg(cardobj, fs)
        elif fs.get('frame_set') == 'planeswalker':
            svg = cfr._create_pw_frame_text_svg(cardobj, fs)
        elif fs.get('mode') == 'image':
            svg = cfr._create_text_only_svg(cardobj, fs)
        else:
            svg = cfr.create_card_frame_svg(cardobj, fs)
    except Exception as e:
        return f'rules_fit_render_error: {type(e).__name__}: {e}'
    overflow = [n for n in fs.get('_quality', []) if 'overflow' in n]
    if overflow:
        return f'rules_fit@60pt: {overflow[0]}'
    # Last plain word of the oracle (mana symbols render as pip images, not text)
    import re as _re
    words = _re.sub(r'\{[^}]+\}', ' ', oracle).replace(')', ' ').replace('.', ' ').split()
    if words:
        last_word = words[-1]
        if len(last_word) > 2 and last_word not in svg:
            return f'rules_truncated@60pt: final word "{last_word}" missing from render'
    return None


def check_pw_badge_alignment():
    """Loyalty badge numbers must sit centered on the badge PLATE (the icons'
    arrows extend the bounding box asymmetrically, which previously pushed
    numbers off-plate). Rasterizes each icon with and without its number and
    asserts the number ink lies inside the plate with a sane center."""
    import numpy as np
    issues = []
    probes = [('+2', lambda: cfr._authentic_loyalty_badge_svg('+2', 20, 60, 80, 28)),
              ('0', lambda: cfr._authentic_loyalty_badge_svg('0', 20, 60, 80, 28)),
              ('−12', lambda: cfr._authentic_loyalty_badge_svg('−12', 20, 60, 80, 28)),
              ('shield:3', lambda: cfr._start_loyalty_badge_svg('3', 70, 60, size=100)),
              ('shield:12', lambda: cfr._start_loyalty_badge_svg('12', 70, 60, size=100))]
    for cost, make in probes:
        badge = make()
        if not badge:
            return ['pw_badge: badge assets missing']
        head = ('<svg width="140" height="120" viewBox="0 0 140 120" '
                'xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink">')
        if cfr._FONT_FACE_CSS:
            head += f'<style>{cfr._FONT_FACE_CSS}</style>'
        both = cairosvg.svg2png(bytestring=(head + ''.join(badge) + '</svg>').encode(),
                                output_width=140, output_height=120)
        only = cairosvg.svg2png(bytestring=(head + badge[0] + '</svg>').encode(),
                                output_width=140, output_height=120)
        a = np.array(Image.open(io.BytesIO(both)).convert('RGBA')).astype(int)
        b = np.array(Image.open(io.BytesIO(only)).convert('RGBA')).astype(int)
        text_mask = (np.abs(a - b).sum(axis=2) > 40)
        if not text_mask.any():
            issues.append(f'pw_badge {cost}: number did not render')
            continue
        # plate = wide rows of the badge-only alpha
        alpha = b[:, :, 3]
        rows = (alpha > 100).sum(axis=1)
        plate_rows = np.where(rows > 0.75 * rows.max())[0]
        ty, tx = np.where(text_mask)
        if ty.min() < plate_rows.min() - 2 or ty.max() > plate_rows.max() + 2:
            issues.append(
                f'pw_badge {cost}: number y{ty.min()}-{ty.max()} outside plate '
                f'y{plate_rows.min()}-{plate_rows.max()}')
        else:
            # center must sit near the plate center
            off = abs((ty.min() + ty.max()) / 2 - (plate_rows.min() + plate_rows.max()) / 2)
            if off > (plate_rows.max() - plate_rows.min()) * 0.22:
                issues.append(f'pw_badge {cost}: number off-center by {off:.0f}px in plate')
    return issues


def check_pw_content_in_rect():
    """Planeswalker content (badges + text + loyalty shield) must stay inside
    the content rect each renderer DECLARES while laying out (fs['_pw_rect'],
    fs['_pw_shield_bbox'] — single source of truth, no mirrored math).
    Rasterizes the overlay with and without the pw content; the diff is the
    pw ink; body ink must stay in the rect and shield ink in its bbox."""
    import numpy as np
    jace = next(c for c in CARDS if c.get('loyalty'))
    styles = ['godzilla', 'crystal', 'lotr', '8th', 'msa',
              'artdeco', 'samurai', 'etched', 'basic']
    issues = []
    for style in styles:
        fs = cfr.resolve_frame_settings(jace, {'style': style})
        full = cfr._build_card_data(jace, fs)
        blank_dict = dict(jace)
        blank_dict['oracle_text'] = ''
        blank_dict['loyalty'] = None
        blank = cfr._build_card_data(blank_dict, fs)
        try:
            if fs.get('mode') == 'image':
                svg_full = _text_svg_for(full, fs)
                fs_blank = dict(fs)
                svg_blank = _text_svg_for(blank, fs_blank)
            else:
                svg_full = cfr.create_card_frame_svg(full, fs)
                fs_blank = dict(fs)
                svg_blank = cfr.create_card_frame_svg(blank, fs_blank)
        except Exception as e:
            issues.append((style, f'pw_rect_render_error: {e}'))
            continue
        rect = fs.get('_pw_rect')
        if rect is None:
            issues.append((style, 'pw_rect: renderer did not declare _pw_rect'))
            continue
        sb = fs.get('_pw_shield_bbox')
        a = _rasterize(svg_full)
        b = _rasterize(svg_blank)
        ink = (np.abs(a.astype(int) - b.astype(int)).sum(axis=2) > 40)
        if not ink.any():
            issues.append((style, 'pw_rect: no planeswalker ink rendered'))
            continue
        x0, y0, x1, y1 = rect
        TOL = 3
        if sb is not None:
            in_shield = ((np.arange(ink.shape[1])[None, :] >= sb[0] - TOL) &
                         (np.arange(ink.shape[1])[None, :] <= sb[2] + TOL) &
                         (np.arange(ink.shape[0])[:, None] >= sb[1] - TOL) &
                         (np.arange(ink.shape[0])[:, None] <= sb[3] + TOL))
            body_ink = ink & ~in_shield
        else:
            body_ink = ink
        if sb is not None and sb[3] > 1015:
            issues.append((style, f'pw_shield_print_unsafe: bottom y{sb[3]:.0f} '
                                  f'past the 3mm limit (1014)'))
        if not body_ink.any():
            continue
        ys, xs = np.where(body_ink)
        out = []
        if xs.min() < x0 - TOL: out.append(f'left {x0 - xs.min():.0f}px')
        if xs.max() > x1 + TOL: out.append(f'right {xs.max() - x1:.0f}px')
        if ys.min() < y0 - TOL: out.append(f'top {y0 - ys.min():.0f}px')
        if ys.max() > y1 + TOL: out.append(f'bottom {ys.max() - y1:.0f}px')
        if out:
            issues.append((style, f'pw_content_outside_rect: {", ".join(out)} '
                                  f'(ink x{xs.min()}-{xs.max()} y{ys.min()}-{ys.max()} '
                                  f'vs rect x{x0:.0f}-{x1:.0f} y{y0:.0f}-{y1:.0f})'))
    return issues


def _text_svg_for(cardobj, fs):
    if fs.get('frame_set') == 'iko':
        return cfr._create_iko_text_svg(cardobj, fs)
    if fs.get('frame_set') == 'crystal':
        return cfr._create_crystal_text_svg(cardobj, fs)
    if fs.get('frame_set') == 'lotr':
        return cfr._create_lotr_text_svg(cardobj, fs)
    if fs.get('frame_set') == '8th':
        return cfr._create_8th_text_svg(cardobj, fs)
    if fs.get('frame_set') == 'mysticalArchive':
        return cfr._create_msa_text_svg(cardobj, fs)
    if fs.get('frame_set') == 'sncArtDeco':
        return cfr._create_artdeco_text_svg(cardobj, fs)
    if fs.get('frame_set') == 'neoSamurai':
        return cfr._create_samurai_text_svg(cardobj, fs)
    if fs.get('frame_set') == 'etched':
        return cfr._create_etched_text_svg(cardobj, fs)
    raise ValueError(f"no text creator for {fs.get('frame_set')}")


def _rasterize(svg):
    import numpy as np
    png = cairosvg.svg2png(bytestring=svg.encode('utf-8'),
                           output_width=W, output_height=H)
    return __import__('numpy').array(Image.open(io.BytesIO(png)).convert('RGBA'))


def check_pw_frame_loyalty_centered():
    """The Planeswalker frame's starting-loyalty numeral must center on the
    baked plate (mask-isolated bbox x600-713.75 y923.5-994.5, plate center
    (657, 956.5)). Isolates the glyph ink by diffing the overlay with and
    without loyalty."""
    import numpy as np
    jace = next(c for c in CARDS if c.get('loyalty'))
    fs = cfr.resolve_frame_settings(jace, {'style': 'planeswalker'})
    full = cfr._build_card_data(jace, fs)
    noloy_dict = dict(jace); noloy_dict['loyalty'] = None
    noloy = cfr._build_card_data(noloy_dict, dict(fs))
    a = _rasterize(cfr._create_pw_frame_text_svg(full, fs))
    b = _rasterize(cfr._create_pw_frame_text_svg(noloy, dict(fs)))
    ink = (np.abs(a.astype(int) - b.astype(int)).sum(axis=2) > 40)
    # restrict to the plate zone (the loyalty diff also removes shield-band
    # differences elsewhere if any)
    ink[:900, :] = False
    if not ink.any():
        return ['pw_loyalty: numeral did not render']
    ys, xs = np.where(ink)
    gcx, gcy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    issues = []
    if abs(gcx - 657) > 6 or abs(gcy - 956.5) > 7:
        issues.append(f'pw_loyalty_off_center: glyph center ({gcx:.0f},{gcy:.0f}) '
                      f'vs plate center (657,956)')
    if xs.min() < 604 or xs.max() > 710 or ys.min() < 928 or ys.max() > 991:
        issues.append(f'pw_loyalty_outside_plate: glyph x{xs.min()}-{xs.max()} '
                      f'y{ys.min()}-{ys.max()}')
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--style', default=None, help='only this style')
    ap.add_argument('--out', default='/tmp/frametest/quality_contact_sheet.png')
    args = ap.parse_args()

    styles = [args.style] if args.style else list(cfr.FRAME_STYLES.keys())
    grid, failures, total = [], [], 0
    for iss in check_pw_badge_alignment():
        failures.append(('(badge geometry)', '-', iss))
    for style, iss in check_pw_content_in_rect():
        failures.append(('(pw rect)', style, iss))
    for iss in check_pw_frame_loyalty_centered():
        failures.append(('(pw loyalty)', 'planeswalker', iss))
    for card in CARDS:
        row = {}
        for st in styles:
            total += 1
            img, issues = render(card, st)
            row[st] = img
            opacity_issue = check_textbox_legible(card, st)
            if opacity_issue:
                issues.append(opacity_issue)
            fit_issue = check_rules_render_full(card, st)
            if fit_issue:
                issues.append(fit_issue)
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
