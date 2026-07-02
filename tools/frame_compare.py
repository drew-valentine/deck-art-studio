#!/usr/bin/env python3
"""Frame reference-comparison harness.

Per the project rule: any frame we ship MUST be compared against a real
reference card, or we won't know when it's done. This renders a card in a given
frame style and stitches it side-by-side with a reference image, so the gap is
visible at a glance.

Usage:
    python3 tools/frame_compare.py <style> <ref_image> [--art PATH] [--out PATH]

Example:
    python3 tools/frame_compare.py godzilla refs/godzilla_brokkos.webp \
        --art decks/heads-i-win/raw_art/okaun_eye_of_chaos.png
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image  # noqa: E402
import card_frame_renderer as cfr  # noqa: E402

# A representative Godzilla-series-style test card (multicolor legendary creature).
DEFAULT_CARD = {
    'name': 'Brokkos, Apex of Forever',
    'type_line': 'Legendary Creature — Nightmare Beast Elemental',
    'mana_cost': '{2}{B}{G}{U}',
    'colors': ['B', 'G', 'U'], 'color_identity': ['B', 'G', 'U'],
    'oracle_text': 'Mutate {2}{G}{U}{B}\nTrample\nYou may cast Brokkos, Apex of Forever '
                   'from your graveyard using its mutate ability.',
    'power': '6', 'toughness': '6',
    'showcase_name': 'BIO-QUARTZ SPACEGODZILLA',
}


def render_card(style, art_path, card=None, extra_settings=None):
    card = card or DEFAULT_CARD
    settings = {'style': style}
    if extra_settings:
        settings.update(extra_settings)
    out = '/tmp/frametest/_fc_render.png'
    Path('/tmp/frametest').mkdir(parents=True, exist_ok=True)
    cfr.composite_card(card, art_path, None, out, settings)
    return Image.open(out).convert('RGB')


def side_by_side(ref_img, mine_img, out_path, height=820):
    def fit(im):
        return im.resize((int(im.width * height / im.height), height))
    ref, mine = fit(ref_img), fit(mine_img)
    gap = 20
    canvas = Image.new('RGB', (ref.width + mine.width + gap, height), (28, 28, 28))
    canvas.paste(ref, (0, 0))
    canvas.paste(mine, (ref.width + gap, 0))
    canvas.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('style')
    ap.add_argument('ref_image')
    ap.add_argument('--art', default='decks/heads-i-win/raw_art/okaun_eye_of_chaos.png')
    ap.add_argument('--out', default='/tmp/frametest/compare.png')
    args = ap.parse_args()

    mine = render_card(args.style, args.art)
    ref = Image.open(args.ref_image).convert('RGB')
    out = side_by_side(ref, mine, args.out)
    print(f"REFERENCE (left) vs {args.style} (right) -> {out}")


if __name__ == '__main__':
    main()
