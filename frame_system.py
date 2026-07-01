"""Declarative, component-based frame renderer — "CSS for frames".

A frame style is a COMPOSITION of styled COMPONENTS (nameplate banner, type bar,
rules box, P/T plate, pips) driven by shared design TOKENS (gold gradient, cream,
dark, radius, fonts). Everything renders as ONE clean vector SVG over the card's
art, so components cohere (same border/gradient language) instead of being
pixel-hacked overlays that look tacked-on.

Fidelity note: for ornaments too baroque to hand-author as a path (the Godzilla
nameplate's scalloped scrollwork), we use the REAL card's silhouette as the shape
and fill it with the system's gold gradient — faithful shape, coherent styling,
no extraction texture/artifacts.

This module is additive: only the Godzilla (iko) style routes through it today;
other styles keep their existing path.
"""
import io
import os

from PIL import Image, ImageFilter

import card_frame_renderer as cfr

W, H = cfr.CARD_WIDTH, cfr.CARD_HEIGHT
_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shared', 'frames', 'iko')

# ── DESIGN TOKENS ──────────────────────────────────────────────────────────
GODZILLA = {
    'gold_lo': '#6b5226', 'gold': '#b8934c', 'gold_hi': '#f0e0a8', 'gold_mid': '#8f6f38',
    'dark': '#15110c', 'dark2': '#241d14', 'ink': '#1b1712', 'white': '#f7f1e4',
    'sub': '#d8cfb8', 'radius': 22, 'bw': 5,
    'font_title': cfr.NAME_FONT_FAMILY, 'font_type': cfr.TYPE_FONT_FAMILY,
    'font_rules': cfr.RULES_FONT_FAMILY, 'font_pt': cfr.PT_FONT_FAMILY,
}


def _defs(T):
    return f'''<defs>
      <linearGradient id="goldgrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#5a441f"/><stop offset="0.12" stop-color="{T['gold_hi']}"/>
        <stop offset="0.30" stop-color="{T['gold']}"/><stop offset="0.50" stop-color="{T['gold_mid']}"/>
        <stop offset="0.70" stop-color="{T['gold']}"/><stop offset="0.90" stop-color="{T['gold_hi']}"/>
        <stop offset="1" stop-color="#5a441f"/>
      </linearGradient>
      <linearGradient id="darkgrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="{T['dark2']}"/><stop offset="1" stop-color="{T['dark']}"/>
      </linearGradient>
      <linearGradient id="creamgrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#fdf8ea"/><stop offset="1" stop-color="#e9e0c6"/>
      </linearGradient>
      <filter id="drop" x="-8%" y="-8%" width="116%" height="122%">
        <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#000" flood-opacity="0.55"/>
      </filter>
    </defs>'''


def _esc(s):
    return (s or '').replace('&', '&amp;').replace('<', '&lt;')


def _text(x, y, s, *, size, font, fill, anchor='start', italic=False, weight='bold'):
    st = 'italic' if italic else 'normal'
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-style="{st}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{_esc(s)}</text>')


def _panel(x, y, w, h, T, *, fill='url(#darkgrad)', radius=None, bw=None, shadow=True):
    """Core CSS-box component: metallic gold-bordered rounded panel with a bevel."""
    r = T['radius'] if radius is None else radius
    b = T['bw'] if bw is None else bw
    sh = ' filter="url(#drop)"' if shadow else ''
    return (
        f'<rect x="{x-1}" y="{y-1}" width="{w+2}" height="{h+2}" rx="{r+1}" fill="{T["gold_lo"]}"{sh}/>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
        f'stroke="url(#goldgrad)" stroke-width="{b}"/>'
        f'<rect x="{x+b*0.7}" y="{y+b*0.7}" width="{w-b*1.4}" height="{h-b*1.4}" '
        f'rx="{max(0, r-b)}" fill="none" stroke="{T["gold_hi"]}" stroke-width="1" opacity="0.35"/>'
    )


# ── Nameplate banner: REAL silhouette filled with system gold (fidelity) ────
_SIL_CACHE = {}


def _banner_image(box):
    """PIL RGBA banner: the real Godzilla nameplate silhouette (fidelity), filled
    with the system's gold gradient (coherence), with a dark inner text plate."""
    x, y, w, h = box
    key = (w, h)
    if key not in _SIL_CACHE:
        path = os.path.join(_ASSET_DIR, 'ornate_nameplate.png')
        if not os.path.exists(path):
            _SIL_CACHE[key] = None
        else:
            src = Image.open(path).convert('RGBA').resize((w, h), Image.Resampling.LANCZOS)
            alpha = src.getchannel('A').filter(ImageFilter.GaussianBlur(0.6))  # smooth extraction jaggies
            # system gold gradient (vertical, metallic)
            grad = Image.new('RGBA', (w, h))
            gp = grad.load()
            stops = [(0.0, (90, 68, 31)), (0.12, (240, 224, 168)), (0.30, (184, 147, 76)),
                     (0.50, (143, 111, 56)), (0.70, (184, 147, 76)), (0.90, (240, 224, 168)),
                     (1.0, (90, 68, 31))]
            for row in range(h):
                t = row / max(1, h - 1)
                for i in range(len(stops) - 1):
                    if stops[i][0] <= t <= stops[i + 1][0]:
                        f = (t - stops[i][0]) / max(1e-6, stops[i + 1][0] - stops[i][0])
                        c = tuple(int(stops[i][1][k] + f * (stops[i + 1][1][k] - stops[i][1][k])) for k in range(3))
                        break
                for col in range(w):
                    gp[col, row] = c + (255,)
            grad.putalpha(alpha)
            _SIL_CACHE[key] = grad
    banner = _SIL_CACHE[key]
    return banner.copy() if banner is not None else None


def render_godzilla(card_dict, fs):
    """Render the full Godzilla frame chrome + text as a transparent RGBA image
    (art is composited under it by the caller). One coherent vector system."""
    T = GODZILLA
    card = cfr._build_card_data(card_dict, fs)
    pips = cfr.parse_mana_cost(card.mana_cost or '')
    has_nick = bool(card.showcase_name)

    result = Image.new('RGBA', (W, H), (0, 0, 0, 0))

    # ── Nameplate banner (real silhouette, system gold) ──
    nb = (30, 18, 690, 108)
    use_banner = fs.get('ornate_nameplate', True)
    banner = _banner_image(nb) if use_banner else None
    if banner is not None:
        result.alpha_composite(banner, (nb[0], nb[1]))

    # ── SVG layer: type+rules panel, P/T plate, all text ──
    svg = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if cfr._FONT_FACE_CSS:
        svg.append(f'<style>{cfr._FONT_FACE_CSS}</style>')
    svg.append(_defs(T))

    nx, ny, nw, nh = nb
    # The dark inner name plate is short (single row) when there's a nickname (the
    # sub-banner below holds the real name); tall (covers the sub-banner) when
    # there's no nickname, so no empty gold strip hangs below the name.
    plate_h = 52 if has_nick else 80
    pcy = ny + 11 + (plate_h / 2)   # vertical centre of the name row / pips
    if not use_banner:
        svg.append(_panel(nx, ny + 6, nw, nh - 12, T, radius=18))  # plain fallback bar
    else:
        svg.append(f'<rect x="{nx+28}" y="{ny+11}" width="{nw-56}" height="{plate_h}" rx="11" '
                   f'fill="url(#darkgrad)" stroke="{T["gold_lo"]}" stroke-width="2"/>')

    # fit the display name so it clears the mana pips
    disp = card.showcase_name if has_nick else card.name
    pip_w = len(pips) * 40 + 20
    name_avail = nw - 92 - pip_w
    nf = 30
    est = len(disp or '') * nf * 0.56
    if est > name_avail and name_avail > 0:
        nf = max(19, int(nf * name_avail / est))

    if has_nick:
        svg.append(_text(nx + 46, pcy + nf * 0.34, card.showcase_name, size=nf,
                         font=T['font_title'], fill=T['white']))
        # subtitle: dark ink on the gold sub-banner
        svg.append(_text(nx + nw / 2, ny + 92, card.name, size=18, italic=True,
                         font=T['font_rules'], fill=T['ink'], anchor='middle'))
    else:
        svg.append(_text(nx + 46, pcy + nf * 0.34, card.name, size=nf,
                         font=T['font_title'], fill=T['white']))
    # mana pips (on the dark name plate, right side)
    px = nx + nw - 54
    for pip in reversed(pips):
        svg.append(f'<circle cx="{px+16}" cy="{pcy}" r="16" fill="rgba(0,0,0,0.45)"/>')
        svg.append(cfr._pip_image_tag(pip, px, pcy - 16, 32))
        px -= 40

    # type + rules as ONE cohesive panel
    tb = (34, 720, 682, 298)
    tx, ty, tw, th = tb
    split = 78
    svg.append(_panel(tx, ty, tw, th, T))
    svg.append(f'<rect x="{tx+10}" y="{ty+split}" width="{tw-20}" height="{th-split-10}" rx="12" '
               f'fill="url(#creamgrad)" stroke="url(#goldgrad)" stroke-width="2.5"/>')
    svg.append(_text(tx + 26, ty + split / 2 + 11, card.type_line, size=27, font=T['font_type'], fill=T['white']))
    if card.oracle_text:
        lines, _ = cfr.render_rules_text_svg(card.oracle_text, tx + 26, ty + split + 34,
                                             tw - 52, th - split - 30, 27, 37, text_color=T['ink'])
        svg.extend(lines)

    # P/T plate
    if card.power is not None and card.toughness is not None:
        svg.append(_panel(598, 984, 108, 46, T, radius=10, bw=3))
        svg.append(_text(652, 1016, f'{card.power}/{card.toughness}', size=30,
                         font=T['font_pt'], fill=T['gold_hi'], anchor='middle'))

    svg.append('</svg>')
    chrome = Image.open(io.BytesIO(cfr.cairosvg.svg2png(
        bytestring=''.join(svg).encode('utf-8'), output_width=W, output_height=H))).convert('RGBA')
    return Image.alpha_composite(result, chrome)
