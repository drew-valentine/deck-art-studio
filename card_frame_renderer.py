"""
MTG card frame renderer — authentic M15/SLD-style frames.
Generates transparent PNG overlays with proper frame geometry, pinlines,
inner shadows, and color theming based on the FeSens/mtg-card SVG design system.

Uses locally-built mana pip images (from mana-master SVGs) for authentic symbol rendering.
Output: 750×1050 PNG at 300 DPI (standard proxy print size).
"""

import copy
import io
import json
import re
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

try:
    import cairosvg
    from PIL import Image, ImageDraw
    import xml.etree.ElementTree as ET
except ImportError as e:
    raise ImportError(f"Required package missing: {e}. Install with: pip install cairosvg pillow")

# ---------------------------------------------------------------------------
# Load pre-rendered pip images (base64-encoded PNGs)
# If pips_b64.json is missing, builds it locally from mana-master SVGs.
# ---------------------------------------------------------------------------
PIPS_DIR = Path(__file__).parent / "shared" / "pips"
PIPS_B64_PATH = PIPS_DIR / "pips_b64.json"
_PIPS_B64 = {}

# ---------------------------------------------------------------------------
# Loyalty SVG path data — extracted from mana-master for crisp vector rendering.
# These are the actual SVG <path d="..."> strings at 32×32 viewBox scale,
# embedded directly in the card SVG so they scale perfectly at any size.
# ---------------------------------------------------------------------------
MANA_SVG_DIR = Path(__file__).parent / "mana-master" / "svg"
_LOYALTY_SVG_PATHS: Dict[str, str] = {}  # key -> SVG path 'd' attribute


def _extract_svg_path_d(svg_path: Path) -> Optional[str]:
    """Extract the 'd' attribute from the first <path> in an SVG file."""
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        ns = {'svg': 'http://www.w3.org/2000/svg'}
        # Try with namespace
        path_el = root.find('.//svg:path', ns)
        if path_el is None:
            # Try without namespace
            path_el = root.find('.//{http://www.w3.org/2000/svg}path')
        if path_el is None:
            path_el = root.find('.//path')
        if path_el is not None:
            return path_el.get('d')
    except Exception as e:
        print(f"[loyalty] Could not extract path from {svg_path}: {e}")
    return None


def _load_loyalty_svg_paths() -> Dict[str, str]:
    """Load loyalty shape SVG path data from mana-master SVGs."""
    shapes = {}
    loyalty_files = {
        'loyalty_up': 'loyalty-up.svg',
        'loyalty_down': 'loyalty-down.svg',
        'loyalty_zero': 'loyalty-zero.svg',
        'loyalty_start': 'loyalty-start.svg',
    }
    for key, filename in loyalty_files.items():
        svg_file = MANA_SVG_DIR / filename
        if svg_file.exists():
            d = _extract_svg_path_d(svg_file)
            if d:
                shapes[key] = d
                print(f"[loyalty] Loaded SVG path: {key}")
    return shapes


try:
    _LOYALTY_SVG_PATHS = _load_loyalty_svg_paths()
    if _LOYALTY_SVG_PATHS:
        print(f"[loyalty] {len(_LOYALTY_SVG_PATHS)} vector loyalty shapes ready")
except Exception as _e:
    print(f"[loyalty] Could not load SVG paths: {_e}")

# ---------------------------------------------------------------------------
# Embedded font loading — fonts are base64-encoded into SVG @font-face rules
# so they render correctly regardless of OS font installation.
# ---------------------------------------------------------------------------
FONTS_DIR = Path(__file__).parent / "fonts"
_FONT_FACE_CSS = ""   # populated on load


def _build_font_face_css() -> str:
    """Build @font-face CSS rules from TTF files in the fonts/ directory."""
    import base64
    rules = []
    font_map = {
        "Beleren2016-Bold.ttf": ("Beleren2016", "bold", "normal"),
        "Beleren-Bold.ttf":     ("Beleren Bold", "bold", "normal"),
        "MPlantin.ttf":         ("MPlantin", "normal", "normal"),
        "MPlantin-Italic.ttf":  ("MPlantin", "normal", "italic"),
    }
    for filename, (family, weight, style) in font_map.items():
        ttf_path = FONTS_DIR / filename
        if ttf_path.exists():
            b64 = base64.b64encode(ttf_path.read_bytes()).decode()
            rules.append(
                f'@font-face {{\n'
                f'  font-family: "{family}";\n'
                f'  font-weight: {weight};\n'
                f'  font-style: {style};\n'
                f'  src: url("data:font/ttf;base64,{b64}") format("truetype");\n'
                f'}}'
            )
    return "\n".join(rules)


try:
    _FONT_FACE_CSS = _build_font_face_css()
    if _FONT_FACE_CSS:
        _n = _FONT_FACE_CSS.count('@font-face')
        print(f"[fonts] Embedded {_n} MTG font(s) for SVG rendering")
    else:
        print("[fonts] No MTG fonts found in fonts/ — using system fallbacks")
except Exception as _e:
    print(f"[fonts] Could not load fonts: {_e}")


def _load_or_download_pips() -> dict:
    """Load pip images, building from mana-master SVGs if needed."""
    global _PIPS_B64

    if PIPS_B64_PATH.exists():
        with open(PIPS_B64_PATH) as f:
            _PIPS_B64 = json.load(f)
            print(f"[pips] Loaded {len(_PIPS_B64)} symbols from cache")
            return _PIPS_B64

    # No cached pips — build from local mana-master SVGs
    try:
        from build_pips_from_mana import build_pips
        print("[pips] Building mana symbols from local mana-master SVGs...")
        _PIPS_B64 = build_pips()
        print(f"[pips] Built {len(_PIPS_B64)} symbols")
        return _PIPS_B64
    except Exception as e:
        print(f"[pips] Could not build pips from mana-master: {e}")

    return _PIPS_B64


def _load_loyalty_map() -> dict:
    """Load the loyalty symbol mapping from build_pips_from_mana output.
    Returns dict like: {"+1": "Lplus1", "-2": "Lminus2", "0": "L0", ...}
    Maps cost strings to their safe b64 key names.
    """
    loyalty_map_path = PIPS_DIR / "loyalty_map.json"
    if loyalty_map_path.exists():
        with open(loyalty_map_path) as f:
            m = json.load(f)
            print(f"[pips] Loaded loyalty map with {len(m)} entries: {list(m.keys())}")
            return m
    return {}


_PIPS_B64 = _load_or_download_pips() if True else {}
_LOYALTY_MAP = _load_loyalty_map()


def _pip_image_tag(symbol: str, x: float, y: float, size: float) -> str:
    """Return an SVG <image> tag embedding the pre-rendered pip PNG.
    Handles both raw keys ('W', '3') and sanitized keys ('U_R' for 'U/R').
    """
    b64 = _PIPS_B64.get(symbol)
    if not b64:
        # Try sanitized key (/ -> _, + -> plus, - -> minus)
        safe = symbol.replace('/', '_').replace('+', 'plus').replace('-', 'minus')
        b64 = _PIPS_B64.get(safe)
    if not b64:
        # Fallback: gray circle with text
        return (
            f'<circle cx="{x + size/2}" cy="{y + size/2}" r="{size/2}" '
            f'fill="#888" stroke="#222" stroke-width="2"/>'
            f'<text x="{x + size/2}" y="{y + size/2 + size*0.15}" text-anchor="middle" '
            f'font-family="serif" font-size="{size*0.55}" '
            f'font-weight="bold" fill="#000">{symbol}</text>'
        )
    return (
        f'<image x="{x}" y="{y}" width="{size}" height="{size}" '
        f'href="data:image/png;base64,{b64}" />'
    )


# ===========================================================================
# Card dimensions — design in 672×936 viewBox, output at 750×1050
# ===========================================================================
CARD_WIDTH = 750
CARD_HEIGHT = 1050
VB_W = 672    # viewBox width (matches mtg-card proportions)
VB_H = 936    # viewBox height
DPI = 300

# ===========================================================================
# Authentic MTG color themes (from FeSens/mtg-card)
# Each entry: (frame_bg, field_fill, textbox_fill, border_color)
# field_fill / textbox_fill include alpha as last 2 hex digits
# ===========================================================================
COLOR_THEMES = {
    'W': {'bg': '#DBCFAC', 'field': '#F2F1EF', 'textbox': '#F2F2F1', 'border': '#F6FCFC', 'text': '#000'},
    'U': {'bg': '#3B90B9', 'field': '#A9CCE5', 'textbox': '#D2E4F4', 'border': '#1971CE', 'text': '#000'},
    'B': {'bg': '#323232', 'field': '#BAB4B5', 'textbox': '#DFDEDE', 'border': '#403232', 'text': '#000'},
    'R': {'bg': '#BB5540', 'field': '#FFE0D3', 'textbox': '#FFEAE2', 'border': '#C5432B', 'text': '#000'},
    'G': {'bg': '#718971', 'field': '#CFDDCD', 'textbox': '#E2E5E0', 'border': '#324F33', 'text': '#000'},
    'gold': {'bg': '#CBA74C', 'field': '#DCBB78', 'textbox': '#FCF4DF', 'border': '#D9CC71', 'text': '#000'},
    'artifact': {'bg': '#969EA3', 'field': '#D5DAE1', 'textbox': '#DFE3E4', 'border': '#F0F3F2', 'text': '#000'},
    'colorless': {'bg': '#969EA3', 'field': '#DFDEDE', 'textbox': '#DFDEDE', 'border': '#E7E8E2', 'text': '#000'},
    'land': {'bg': '#AA8E6F', 'field': '#D5CCC0', 'textbox': '#E4DDD4', 'border': '#7A6B55', 'text': '#000'},
}

# Dual-color textbox overrides
DUAL_TEXTBOX = {
    'WU': '#E8EDF5', 'WB': '#E8E4E2', 'WR': '#FFFFFF', 'WG': '#EDF0E8',
    'UB': '#D8DDE8', 'UR': '#E8DDED', 'UG': '#D8E8E4',
    'BR': '#E8D8D8', 'BG': '#DDE4D8',
    'RG': '#F0E8D8',
}

# ===========================================================================
# Frame layers — each visual component is an independently controllable layer.
# Render order (bottom to top) defined by FRAME_LAYER_ORDER.
# ===========================================================================
FRAME_LAYERS = {
    'border':    {'label': 'Border',    'description': 'Outer card edge'},
    'frame':     {'label': 'Frame',     'description': 'Colored background with art window cutout'},
    'title_bar': {'label': 'Title Bar', 'description': 'Name field background'},
    'pinlines':  {'label': 'Pinlines',  'description': 'Colored lines between sections'},
    'type_bar':  {'label': 'Type Bar',  'description': 'Type line background'},
    'text_box':  {'label': 'Text Box',  'description': 'Rules text background'},
    'pt_box':    {'label': 'P/T Box',   'description': 'Power/toughness box'},
    'info_bar':  {'label': 'Info Bar',  'description': 'Bottom collector info bar'},
}

FRAME_LAYER_ORDER = [
    'border', 'frame', 'title_bar', 'pinlines',
    'type_bar', 'text_box', 'pt_box', 'info_bar',
]

# ===========================================================================
# Default render params — visual parameters that differentiate frame eras.
# Each style can override any of these to change the actual look, not just opacity.
# ===========================================================================
DEFAULT_RENDER_PARAMS = {
    'border_width': 17,         # outer border width (authentic M15 ~2.5% of 672)
    'border_radius': 30,        # outer border corner radius (matches real MTG cards)
    'border_color': None,       # None = use '#17140f' authentic dark brown-black
    'field_radius': 16,         # end-cap size for pill-shaped fields
    'art_margin': 28,           # inset from card edge to art window
    'inner_border': False,      # draw inner colored border around art window
    'inner_border_width': 3,    # width of inner border if enabled
    'pinline_width': 2,         # width of pinline strokes
    'pt_shape': 'pentagon',     # 'pentagon' (M15 lens) or 'pointed' (retro)
    'frame_pattern': 'solid',   # 'solid', 'nyx' (starfield), 'none'
    'bevel': False,             # add bevel highlight/shadow to fields
    'textbox_style': 'rounded', # 'rounded' (M15) or 'squared' (retro)
    'field_shape': 'pill',      # 'pill' (M15 lozenge) or 'simple' (rounded rect)
    'double_border': False,     # draw a second inner border stroke
    'frame_tint': None,         # optional color tint applied to frame (nyx uses this)
}

# ===========================================================================
# Frame styles — each configures ALL layers for a distinct visual look.
# The 'render' dict controls *how* visible layers look (shape, size, texture).
# The 'layers' dict controls *which* layers are visible and their opacity.
# ===========================================================================
# Per-style designer controls: which settings the renderer actually honors.
# 'colors': color_overrides keys that have an effect. 'box_opacity': rules-box
# transparency slider. 'showcase': the big showcase-name field. The UI hides
# anything not listed so no dead controls are shown.
FRAME_STYLES = {
    'm15': {
        'label': 'M15',
        'description': 'Authentic M15 card frame from CardConjurer assets',
        'mode': 'image',
        'frame_set': 'm15',
        'controls': {'colors': ['text']},
    },
    'basic': {
        'label': 'Basic',
        'description': 'Frosted glass overlays — the original Deck Art Studio look',
        'mode': 'svg',
        'render': {
            'border_width': 0,
            'field_radius': 8,
            'art_margin': 20,
            'pinline_width': 0,
            'frame_pattern': 'none',
            'bevel': False,
            'textbox_style': 'rounded',
            'field_shape': 'simple',
            'field_stroke': False,
        },
        'layers': {
            'border':    {'visible': False, 'opacity': 0},
            'frame':     {'visible': False, 'opacity': 0},
            'title_bar': {'visible': True,  'opacity': 0.65},
            'pinlines':  {'visible': False, 'opacity': 0},
            'type_bar':  {'visible': True,  'opacity': 0.65},
            'text_box':  {'visible': True,  'opacity': 0.62},
            'pt_box':    {'visible': True,  'opacity': 0.82},
            'info_bar':  {'visible': False, 'opacity': 0},
        },
    },
    'godzilla': {
        'label': 'Showcase',
        'description': 'Ikoria borderless showcase — full-bleed art, gold-trimmed title with a '
                       'big display name over the original name, light rules box. Built from the '
                       'authentic Ikoria frame assets.',
        'mode': 'image',
        'frame_set': 'iko',
        'layout': 'iko',
        'controls': {'colors': ['textbox', 'border', 'text'],
                     'box_opacity': True, 'showcase': True},
    },
    'planeswalker': {
        'label': 'Planeswalker',
        'description': 'The authentic M15-era planeswalker frame — near-full-height art, '
                       'translucent alternating ability bands, loyalty badges and shield. '
                       'Planeswalkers in the M15 style use this automatically. Built from the '
                       'cardconjurer planeswalker assets.',
        'mode': 'image',
        'frame_set': 'planeswalker',
        'layout': 'planeswalker',
        'controls': {'colors': ['text']},
    },
    'artdeco': {
        'label': 'Art Deco',
        'description': 'New Capenna Art Deco showcase — geometric gilded frame, dark bars with '
                       'light title, parchment rules panel. Built from the cardconjurer '
                       'snc/artDeco assets.',
        'mode': 'image',
        'frame_set': 'sncArtDeco',
        'layout': 'artdeco',
        'controls': {'colors': ['text']},
    },
    'samurai': {
        'label': 'Samurai',
        'description': 'Kamigawa Neon Dynasty samurai showcase — brushed dark frame with light '
                       'text and a rare stamp. Built from the cardconjurer neo/samurai assets.',
        'mode': 'image',
        'frame_set': 'neoSamurai',
        'layout': 'samurai',
        'rules_text': 'light',
        'controls': {'colors': ['text']},
    },
    'etched': {
        'label': 'Etched',
        'description': 'Commander etched-foil frame — dark engraved metal with light text and a '
                       'holo stamp. Built from the cardconjurer etched assets.',
        'mode': 'image',
        'frame_set': 'etched',
        'layout': 'etched',
        'rules_text': 'light',
        'controls': {'colors': ['text']},
    },
    '8th': {
        'label': '8th Edition',
        'description': 'The iconic 2003-2014 modern border — metallic beveled bars, inset art '
                       'window, colored land frames. Built from the cardconjurer 8th assets.',
        'mode': 'image',
        'frame_set': '8th',
        'layout': '8th',
        'controls': {'colors': ['text']},
    },
    'msa': {
        'label': 'Mystical Archive',
        'description': 'Strixhaven Mystical Archive showcase — ornate color-and-gold arabesque '
                       'frame over parchment with full-bleed art. Built from the cardconjurer '
                       'mysticalArchive assets.',
        'mode': 'image',
        'frame_set': 'mysticalArchive',
        'layout': 'msa',
        'controls': {'colors': ['text']},
    },
    'lotr': {
        'label': 'LOTR',
        'description': "Tales of Middle-earth 'Ring' showcase — circular ring-inscription art "
                       'window, wavy legendary crown, parchment rules panel and holo stamp. '
                       'Built from the cardconjurer LOTR frame assets.',
        'mode': 'image',
        'frame_set': 'lotr',
        'layout': 'lotr',
        'controls': {'colors': ['text'], 'bottom_mask': True},
    },
    'crystal': {
        'label': 'Crystal',
        'description': 'Shattered-ice showcase — crystalline border with a legendary crown of '
                       'ice shards, dark stone bars and rules box with light text. Built from '
                       'the cardconjurer Crystal frame assets.',
        'mode': 'image',
        'frame_set': 'crystal',
        'layout': 'crystal',
        'rules_text': 'light',   # light text on the dark stone box (quality gate inverts)
        'controls': {'colors': ['text'], 'box_opacity': True},
    },
    'clean': {
        'label': 'Clean',
        'description': 'Raw art, no frame at all',
        'mode': 'svg',
        'no_frame': True,
        'render': {},
        'layers': {k: {'visible': False, 'opacity': 0} for k in [
            'border', 'frame', 'title_bar', 'pinlines',
            'type_bar', 'text_box', 'pt_box', 'info_bar',
        ]},
    },
}

# Legacy alias for backwards compatibility during migration
FRAME_PRESETS = FRAME_STYLES


def _layer_visible(layers: dict, key: str) -> bool:
    """Check if a layer is visible in the resolved settings."""
    layer = layers.get(key, {})
    return layer.get('visible', False)


def _layer_opacity(layers: dict, key: str) -> float:
    """Get a layer's opacity from resolved settings."""
    layer = layers.get(key, {})
    if not layer.get('visible', False):
        return 0.0
    return layer.get('opacity', 1.0)


def _migrate_v1_to_v2(settings: dict) -> dict:
    """Convert v1 frame_settings (preset + alpha_overrides) to v2 (style + layers)."""
    # Map v1 preset names to v2 style names
    preset_map = {
        'classic': 'basic', 'modern': 'm15',
        'borderless': 'basic',
        'minimal': 'basic', 'full-art': 'clean',
        'vintage': 'm15', 'retro': 'm15',
        'frameless': 'clean',
    }
    v1_preset = settings.get('preset', 'basic')
    style_key = preset_map.get(v1_preset, 'basic')

    # Start from the mapped style's layer defaults
    style = FRAME_STYLES.get(style_key, FRAME_STYLES['basic'])
    # Image-mode styles (e.g. m15) carry no layers dict — use basic's
    layers = copy.deepcopy(style.get('layers', FRAME_STYLES['basic']['layers']))

    # Apply v1 alpha_overrides to corresponding v2 layers
    alphas = settings.get('alpha_overrides', {})
    if 'field_alpha' in alphas:
        fa = alphas['field_alpha']
        layers['title_bar']['opacity'] = fa
        layers['title_bar']['visible'] = fa > 0
        layers['type_bar']['opacity'] = fa
        layers['type_bar']['visible'] = fa > 0
    if 'textbox_alpha' in alphas:
        ta = alphas['textbox_alpha']
        layers['text_box']['opacity'] = ta
        layers['text_box']['visible'] = ta > 0
    if 'pt_alpha' in alphas:
        pa = alphas['pt_alpha']
        layers['pt_box']['opacity'] = pa
        layers['pt_box']['visible'] = pa > 0

    return {
        'style': style_key,
        'layers': layers,
        # No explicit choice in v1 data: the presence of color_overrides
        # implies manual — v1 always honored them.
        'use_card_colors': settings.get(
            'use_card_colors', not settings.get('color_overrides')),
        'color_overrides': settings.get('color_overrides', {}),
    }


def _is_v2_format(settings: dict) -> bool:
    """Detect whether frame_settings are v2 format (has 'layers' or 'style' key)."""
    return 'layers' in settings or 'style' in settings


def resolve_frame_settings(card_dict: dict, deck_settings: dict = None,
                           live: bool = False) -> dict:
    """Merge style → deck layers → card overrides into final frame settings.

    Handles both v1 (preset + alpha_overrides) and v2 (style + layers) formats.
    Returns a dict with: style, layers, use_card_colors, color_overrides,
    text_overrides, plus style-level flags (no_frame, type_y, show_oracle, etc.)

    live=True: deck_settings is the LIVE designer state (preview endpoints),
    which is authoritative — the card's SAVED overrides must not shadow it,
    or the designer freezes on the saved frame after the first Save. Only art
    pan/zoom (which the live payload never carries) survives from the card.
    """
    deck_settings = deck_settings or {}
    card_overrides = card_dict.get('frame_overrides', {})
    if live:
        card_overrides = {k: v for k, v in card_overrides.items()
                          if k in ('art_offset', 'art_zoom')}

    # ── Migrate v1 deck settings if needed ──
    if deck_settings and not _is_v2_format(deck_settings):
        deck_settings = _migrate_v1_to_v2(deck_settings)

    # ── Determine style: card override > deck > basic ──
    # Map removed/renamed styles to the closest remaining style.
    # full-art -> clean: closest surviving look (art-dominant, no textbox),
    # not basic (which would add a frosted textbox + oracle over the art).
    _style_remap = {'modern': 'm15', 'retro': 'm15', 'nyx': 'm15', 'vintage': 'm15',
                    'classic': 'basic', 'full-art': 'clean',
                    'borderless': 'basic', 'minimal': 'basic'}
    _card_style = card_overrides.get('style')
    style_key = _card_style or deck_settings.get('style', 'basic')
    style_key = _style_remap.get(style_key, style_key)
    # Planeswalkers in the standard M15 style use the authentic planeswalker
    # frame automatically, like real cards — but only when m15 came from the
    # DECK style. An explicit per-card 'm15' override is honored (the user
    # chose that frame deliberately, with its full set of color controls).
    _card_is_m15 = _style_remap.get(_card_style, _card_style) == 'm15'
    if (style_key == 'm15' and not _card_is_m15
            and card_dict.get('loyalty') is not None
            and _LOYALTY_RE.search(card_dict.get('oracle_text') or '')):
        style_key = 'planeswalker'
    style = FRAME_STYLES.get(style_key, FRAME_STYLES['basic'])

    # ── Build layers: start from style defaults (image-based styles have no layers) ──
    layers = copy.deepcopy(style.get('layers', {}))

    # Apply deck-level layer overrides. setdefault: image-mode styles carry no
    # layers dict, but legacy decks (e.g. v1 'modern' remapped to m15) may have
    # saved layer overrides — indexing blind raised KeyError and made the whole
    # deck unrenderable.
    deck_layers = deck_settings.get('layers', {})
    for key in FRAME_LAYER_ORDER:
        if key in deck_layers:
            layers.setdefault(key, {}).update(deck_layers[key])

    # Apply card-level layer overrides
    card_layers = card_overrides.get('layers', {})
    for key in FRAME_LAYER_ORDER:
        if key in card_layers:
            layers.setdefault(key, {}).update(card_layers[key])

    # ── Build render params: start from defaults, apply style overrides ──
    render = dict(DEFAULT_RENDER_PARAMS)
    render.update(style.get('render', {}))

    # ── Auto-vs-manual colors: card override wins over deck ──
    # A card saved with Auto must not inherit the deck's manual colors (that
    # painted its title black while the designer preview showed auto/white).
    # When NEITHER level carries the key — legacy v1 data, bare API payloads —
    # the presence of color_overrides implies manual: those callers always
    # had their colors honored.
    use_card_colors = card_overrides.get(
        'use_card_colors', deck_settings.get('use_card_colors'))
    if use_card_colors is None:
        use_card_colors = not (deck_settings.get('color_overrides')
                               or card_overrides.get('color_overrides'))

    # ── Build result ──
    result = {
        'style': style_key,
        'mode': style.get('mode', 'svg'),
        'frame_set': style.get('frame_set'),
        'layout': style.get('layout'),
        'layers': layers,
        'render': render,
        'use_card_colors': use_card_colors,
        'color_overrides': {},
        # Text overrides: when the passed (live designer) settings carry a
        # text_overrides key, they are AUTHORITATIVE — merging saved values
        # underneath resurrected fields the user had just cleared in the
        # designer. Final renders (deck settings never carry per-card text)
        # use the saved card overrides.
        'text_overrides': (deck_settings['text_overrides']
                           if 'text_overrides' in deck_settings
                           else card_overrides.get('text_overrides', {})),
        'no_frame': style.get('no_frame', False),
        'show_oracle': style.get('show_oracle', True),
        'show_flavor': style.get('show_flavor', True),
        'type_y': style.get('type_y', 545),
        # Two-color gradient frame mode (card > deck > 'auto'): 'auto'/True (smooth
        # blend for 2-color cards), 'gradient', 'split', or 'off'/False (flat gold).
        'frame_gradient': card_overrides.get(
            'frame_gradient', deck_settings.get('frame_gradient', 'auto')),
        # Godzilla/iko rules-box opacity 0..1 (transparency); card > deck.
        'box_opacity': card_overrides.get(
            'box_opacity', deck_settings.get('box_opacity', None)),
        # LOTR bottom mask toggle (rounded black bottom); card > deck > on.
        'bottom_mask': card_overrides.get(
            'bottom_mask', deck_settings.get('bottom_mask', True)),
        # Godzilla/iko rules text size in px (user-tunable); card > deck.
        'rules_font_size': card_overrides.get(
            'rules_font_size', deck_settings.get('rules_font_size', None)),
    }

    # Color overrides: deck, then card on top — but ONLY when the effective
    # choice is manual. The renderer applies color_overrides unconditionally
    # (and disables showcase treatments when any are present), so an Auto
    # card must resolve to NO overrides even if the deck default has some.
    if not result['use_card_colors']:
        if deck_settings.get('color_overrides'):
            result['color_overrides'].update(deck_settings['color_overrides'])
        if card_overrides.get('color_overrides'):
            result['color_overrides'].update(card_overrides['color_overrides'])

    # Art position: per-card only
    if card_overrides.get('art_offset'):
        result['art_offset'] = card_overrides['art_offset']
    if card_overrides.get('art_zoom') is not None:
        result['art_zoom'] = card_overrides['art_zoom']

    # Flat alpha accessors for backwards compat during transition
    result['field_alpha'] = _layer_opacity(layers, 'title_bar')
    result['textbox_alpha'] = _layer_opacity(layers, 'text_box')
    result['pt_alpha'] = _layer_opacity(layers, 'pt_box')

    return result


# ===========================================================================
# Layout constants — based on 672×936 viewBox
# Standard MTG card proportions: name bar at top, fixed art window,
# type line at ~58% down, standard-size textbox below it.
# ===========================================================================
MARGIN = 28          # inner margin from card edge
CORNER_R = 22        # rounded corners on card
FIELD_R = 16         # rounded corners on fields
PINLINE_W = 2        # pinline stroke width

# Translucency for frosted glass effect (art shows through)
FIELD_ALPHA = 0.65   # name & type fields
TEXTBOX_ALPHA = 0.62 # rules textbox — more translucent so art shows through
PT_ALPHA = 0.82      # P/T box

# ── Name field (top of card) ──
NAME_Y = 32
NAME_H = 56          # fixed name bar height
NAME_PADDING_V = 10
NAME_TEXT_X = MARGIN + 16

# ── Art window (between name bar and type line) ──
ART_Y = NAME_Y + NAME_H + PINLINE_W
# (Art bottom = TYPE_Y - PINLINE_W, calculated from type line position)

# ── Type line (FIXED position — standard MTG location ~58% down) ──
TYPE_Y = 545
TYPE_H = 42
TYPE_PADDING_V = 8

# ── Rules textbox (FIXED standard size below type line) ──
RULES_Y = TYPE_Y + TYPE_H + PINLINE_W
RULES_BOTTOM = 882   # bottom of textbox
RULES_H = RULES_BOTTOM - RULES_Y  # ~293px — standard textbox
RULES_PADDING = 16
RULES_BOTTOM_PAD = 8

# ── Bottom metadata ──
META_Y = VB_H - 16

# P/T box (overlaps bottom-right of rules box)
PT_W = 110
PT_H = 52

# Mana pips in name bar
MANA_PIP_SIZE = 36
MANA_PIP_GAP = 4
MANA_PIPS_RIGHT = VB_W - MARGIN - 12

# Inline pips in rules text
RULES_PIP_SIZE = 24
RULES_PIP_RADIUS = RULES_PIP_SIZE / 2


def _rules_pip_size(font_size: int) -> int:
    """Mana symbol size for EVERYTHING in the rules area (inline symbols and
    split-column header costs): 0.83em — the classic 24px-at-font-29 look —
    scaled with the fitted font so long-text cards keep pips and text in
    proportion, and all pips in one text box are the same size."""
    return max(14, round(font_size * 0.83))

# Font sizes — LARGE for print readability at 750×1050
NAME_FONT = 35
TYPE_FONT = 29
RULES_FONT = 29           # standard MTG ~8.5pt scaled to 672×936 viewBox
RULES_LINE_H = 37
FLAVOR_FONT = 21          # italic flavor text — noticeably smaller than rules
FLAVOR_LINE_H = 27
PT_FONT = 33
META_FONT = 10
COMMANDER_FONT = 14

# ── MTG-authentic fonts ──
# Beleren Bold  — card names (official MTG font)
# Beleren       — type lines
# MPlantin      — rules text (Plantin variant used on real cards)
# MPlantin Italic — flavor text
# Fallbacks: P052 (Palatino) for names, Lora for body text
NAME_FONT_FAMILY = "Beleren2016, Beleren Bold, Beleren, P052, serif"
TYPE_FONT_FAMILY = "Beleren2016, Beleren Bold, Beleren, P052, serif"
RULES_FONT_FAMILY = "MPlantin, Lora, P052, serif"
FLAVOR_FONT_FAMILY = "MPlantin, Lora, P052, serif"
PT_FONT_FAMILY = "Beleren2016, Beleren Bold, Beleren, P052, serif"
META_FONT_FAMILY = "MPlantin, Lora, serif"


# ===========================================================================
# Font measurement (actual TTF metrics via Pillow)
# ===========================================================================
_pil_font_cache: Dict[int, Any] = {}

def _get_pil_font(size: int):
    """Load MPlantin at the given size for measuring text widths."""
    if size not in _pil_font_cache:
        from PIL import ImageFont
        for path in [
            Path('fonts/MPlantin.ttf'),
            Path.home() / 'Library' / 'Fonts' / 'MPlantin.ttf',
            Path.home() / '.local' / 'share' / 'fonts' / 'MPlantin.ttf',
        ]:
            if path.exists():
                _pil_font_cache[size] = ImageFont.truetype(str(path), size)
                break
        else:
            _pil_font_cache[size] = None  # font not found, fall back to estimate
    return _pil_font_cache[size]

def _measure_text(text: str, font_size: int) -> float:
    """Measure actual rendered width of text using TTF font metrics."""
    pil_font = _get_pil_font(font_size)
    if pil_font:
        return pil_font.getlength(text)
    # Fallback: rough estimate if font not available
    return len(text) * font_size * 0.48


# ===========================================================================
# Data structures
# ===========================================================================
@dataclass
class CardData:
    name: str
    mana_cost: str
    type_line: str
    oracle_text: str
    power: Optional[str] = None
    toughness: Optional[str] = None
    loyalty: Optional[str] = None
    colors: List[str] = None
    color_identity: List[str] = None
    flavor_text: Optional[str] = None
    is_commander: bool = False
    card_type: str = "creature"
    # Showcase/Godzilla frame: a big alternate display name; the real `name`
    # renders small beneath it (e.g. "GODZILLA, KING OF THE MONSTERS" over
    # "Zilortha, Strength Incarnate").
    showcase_name: Optional[str] = None
    # Single-art multi-part cards (adventure, split/room): Scryfall layout +
    # both face dicts (name, mana_cost, type_line, oracle_text). When set, the
    # rules box renders as two columns instead of one.
    layout: str = 'normal'
    split_faces: Optional[List[dict]] = None
    # Battles (sieges): defense value, rendered in a shield on the landscape frame
    defense: Optional[str] = None

    def __post_init__(self):
        if self.colors is None:
            self.colors = []
        if self.color_identity is None:
            self.color_identity = []


# ===========================================================================
# Helpers
# ===========================================================================
def parse_mana_cost(mana_cost: str) -> List[str]:
    if not mana_cost:
        return []
    return re.findall(r'\{([^}]+)\}', mana_cost)


def get_color_theme(card: CardData) -> dict:
    """Return the color theme dict for a card."""
    if card.card_type == 'land':
        colors = card.color_identity or card.colors
        if colors and len(colors) == 1:
            return COLOR_THEMES[colors[0]]
        return COLOR_THEMES['land']
    if card.card_type == 'artifact' and not card.colors:
        return COLOR_THEMES['artifact']

    colors = card.colors or card.color_identity
    if not colors:
        return COLOR_THEMES['colorless']
    if len(colors) == 1:
        return COLOR_THEMES[colors[0]]
    # Multicolor — use gold frame
    theme = dict(COLOR_THEMES['gold'])
    # Try dual textbox override
    if len(colors) == 2:
        key = ''.join(sorted(colors, key=lambda c: 'WUBRG'.index(c)))
        if key in DUAL_TEXTBOX:
            theme['textbox'] = DUAL_TEXTBOX[key]
    return theme


def hex_with_alpha(hex_color: str, alpha: float) -> str:
    """Convert #RRGGBB + alpha float to rgba() string."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


def tokenize_oracle_text(text: str) -> List[dict]:
    if not text:
        return []
    tokens = []
    parts = re.split(r'(\{[^}]+\})', text)
    for part in parts:
        if not part:
            continue
        m = re.match(r'^\{([^}]+)\}$', part)
        if m:
            tokens.append({'type': 'symbol', 'value': m.group(1)})
        else:
            tokens.append({'type': 'text', 'value': part})
    return tokens


# ===========================================================================
# SVG building blocks
# ===========================================================================
def _svg_filters(theme: dict) -> str:
    """SVG <defs> with authentic MTG-style filters (matches FeSens inner shadow system).

    FeSens uses 4-layer inner shadow filters on fields:
      1. Top shadow (dark, dy=-6, stdDeviation=1)
      2. Right shadow (dark, dx=6, stdDeviation=1)
      3. Corner shadow (dark, dx=6 dy=-6, stdDeviation=1)
      4. Bottom highlight (white, dx=-2 dy=6, lighten blend)
    """
    border = theme['border']
    return f'''<defs>
  <!-- Drop shadow for text -->
  <filter id="textShadow" x="-5%" y="-5%" width="115%" height="115%">
    <feDropShadow dx="0.5" dy="1" stdDeviation="0.8" flood-color="rgba(0,0,0,0.7)"/>
  </filter>
  <!-- 4-layer inner shadow for fields (authentic MTG depth from FeSens) -->
  <filter id="fieldShadow" x="-2%" y="-4%" width="104%" height="112%"
          color-interpolation-filters="sRGB">
    <!-- Layer 1: Top inner shadow -->
    <feFlood flood-color="black" flood-opacity="0.50" result="c1"/>
    <feComposite in="c1" in2="SourceAlpha" operator="in" result="s1"/>
    <feGaussianBlur in="s1" stdDeviation="1" result="b1"/>
    <feOffset in="b1" dy="-4" result="o1"/>
    <feComposite in="o1" in2="SourceAlpha" operator="in" result="i1"/>
    <!-- Layer 2: Right inner shadow -->
    <feFlood flood-color="black" flood-opacity="0.30" result="c2"/>
    <feComposite in="c2" in2="SourceAlpha" operator="in" result="s2"/>
    <feGaussianBlur in="s2" stdDeviation="1" result="b2"/>
    <feOffset in="b2" dx="4" result="o2"/>
    <feComposite in="o2" in2="SourceAlpha" operator="in" result="i2"/>
    <!-- Layer 3: Top-right corner shadow -->
    <feFlood flood-color="black" flood-opacity="0.20" result="c3"/>
    <feComposite in="c3" in2="SourceAlpha" operator="in" result="s3"/>
    <feGaussianBlur in="s3" stdDeviation="1" result="b3"/>
    <feOffset in="b3" dx="4" dy="-4" result="o3"/>
    <feComposite in="o3" in2="SourceAlpha" operator="in" result="i3"/>
    <!-- Layer 4: Bottom-left highlight (lighten blend) -->
    <feFlood flood-color="white" flood-opacity="0.30" result="c4"/>
    <feComposite in="c4" in2="SourceAlpha" operator="in" result="s4"/>
    <feGaussianBlur in="s4" stdDeviation="0.8" result="b4"/>
    <feOffset in="b4" dx="-1.5" dy="4" result="o4"/>
    <feComposite in="o4" in2="SourceAlpha" operator="in" result="i4"/>
    <!-- Merge all layers -->
    <feMerge>
      <feMergeNode in="SourceGraphic"/>
      <feMergeNode in="i1"/>
      <feMergeNode in="i2"/>
      <feMergeNode in="i3"/>
    </feMerge>
    <!-- Apply highlight on top with lighten blend -->
    <feBlend in2="i4" mode="lighten"/>
  </filter>
  <!-- P/T box inner shadow (dual: dark top + white bottom highlight) -->
  <filter id="ptShadow" x="-5%" y="-10%" width="110%" height="130%"
          color-interpolation-filters="sRGB">
    <feFlood flood-color="black" flood-opacity="0.50" result="pc1"/>
    <feComposite in="pc1" in2="SourceAlpha" operator="in" result="ps1"/>
    <feGaussianBlur in="ps1" stdDeviation="1.2" result="pb1"/>
    <feOffset in="pb1" dy="-3" result="po1"/>
    <feComposite in="po1" in2="SourceAlpha" operator="in" result="pi1"/>
    <feFlood flood-color="white" flood-opacity="0.30" result="pc2"/>
    <feComposite in="pc2" in2="SourceAlpha" operator="in" result="ps2"/>
    <feGaussianBlur in="ps2" stdDeviation="0.8" result="pb2"/>
    <feOffset in="pb2" dy="3" result="po2"/>
    <feComposite in="po2" in2="SourceAlpha" operator="in" result="pi2"/>
    <feMerge>
      <feMergeNode in="SourceGraphic"/>
      <feMergeNode in="pi1"/>
    </feMerge>
    <feBlend in2="pi2" mode="lighten"/>
  </filter>
  <!-- Drop shadow for border structure -->
  <filter id="borderShadow" x="-2%" y="-2%" width="104%" height="104%">
    <feOffset dx="2.5" dy="-2" result="off"/>
    <feGaussianBlur in="off" stdDeviation="0.5" result="blur"/>
    <feColorMatrix in="blur" values="1 1 1 0 0  1 1 1 0 0  1 1 1 0 0  0 0 0 0.15 0" result="white"/>
    <feBlend in="SourceGraphic" in2="white" mode="normal"/>
  </filter>
  <!-- Subtle depth gradient for textbox -->
  <linearGradient id="boxGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="white" stop-opacity="0.10"/>
    <stop offset="100%" stop-color="black" stop-opacity="0.06"/>
  </linearGradient>
  <!-- Frosted glass blur backdrop -->
  <filter id="frost" x="-2%" y="-2%" width="104%" height="104%">
    <feGaussianBlur in="SourceGraphic" stdDeviation="0.6"/>
  </filter>
</defs>'''


def _card_shape_path() -> str:
    """The card outline path with rounded corners, matching MTG card proportions."""
    r = CORNER_R
    w = VB_W
    h = VB_H
    return (
        f"M{r} 0 H{w-r} Q{w} 0 {w} {r} "
        f"V{h-r} Q{w} {h} {w-r} {h} "
        f"H{r} Q0 {h} 0 {h-r} "
        f"V{r} Q0 0 {r} 0 Z"
    )


def _simple_field_path(x: float, y: float, w: float, h: float, r: float = FIELD_R,
                       r_top: float = None, r_bottom: float = None) -> str:
    """Simple rounded rectangle field path — the original Deck Art Studio look.

    Uses quadratic Bezier curves (Q) for subtle corner rounding that looks
    clean on translucent frosted-glass overlays.
    r_top/r_bottom override the radius for top/bottom corners independently.
    """
    r = min(r, h / 2, w / 2)
    rt = min(r_top if r_top is not None else r, h / 2, w / 2)
    rb = min(r_bottom if r_bottom is not None else r, h / 2, w / 2)
    return (
        f"M{x+rt} {y} H{x+w-rt} Q{x+w} {y} {x+w} {y+rt} "
        f"V{y+h-rb} Q{x+w} {y+h} {x+w-rb} {y+h} "
        f"H{x+rb} Q{x} {y+h} {x} {y+h-rb} "
        f"V{y+rt} Q{x} {y} {x+rt} {y} Z"
    )


def _field_path(x: float, y: float, w: float, h: float, r: float = FIELD_R,
                r_top: float = None, r_bottom: float = None) -> str:
    """Authentic MTG pill-shaped field path (matches FeSens name-field.svg).

    Creates a lozenge/pill shape where the ends curve outward at the vertical
    midpoint using cubic bezier curves, rather than simple rounded corners.
    This is the distinctive shape used on real M15+ MTG card name/type bars.

    The end-cap proportions are derived from the FeSens reference:
      viewBox 597×56, path M12 2 H585 C585,2 595,11 595,28 C595,45 585,54 ...
    Ratios relative to field height: inset=h*0.214, edge=h*0.036, mid=h*0.5
    """
    # Proportions scaled from FeSens name-field.svg (597×56 viewBox)
    yi = h * 0.036   # top/bottom inset (2/56)
    xi = h * 0.214   # where straight edge starts from each end (12/56)
    xe = h * 0.036   # how close curve reaches to field edge (2/56)
    mid = h * 0.5    # vertical midpoint
    cy1 = h * 0.196  # control point y for top curve (11/56)
    cy2 = h * 0.804  # control point y for bottom curve (45/56)

    # Right end curves
    rx1 = x + w - xi  # where straight top edge ends
    rx2 = x + w - xe  # rightmost point of curve
    # Left end curves
    lx1 = x + xi      # where straight top edge starts
    lx2 = x + xe      # leftmost point of curve

    return (
        f"M{lx1} {y+yi} "                                    # start at top-left
        f"H{rx1} "                                            # straight across top
        f"C{rx1} {y+yi} {rx2} {y+cy1} {rx2} {y+mid} "       # right end top curve
        f"C{rx2} {y+cy2} {rx1} {y+h-yi} {rx1} {y+h-yi} "   # right end bottom curve
        f"H{lx1} "                                            # straight across bottom
        f"C{lx1} {y+h-yi} {lx2} {y+cy2} {lx2} {y+mid} "    # left end bottom curve
        f"C{lx2} {y+cy1} {lx1} {y+yi} {lx1} {y+yi} Z"      # left end top curve
    )


def _pt_box_path(x: float, y: float, w: float, h: float) -> str:
    """Authentic MTG lens/eye-shaped P/T box (matches FeSens pt-outer.svg).

    Creates a convex lens shape where the sides bow outward, wider at the
    vertical midpoint. This is the distinctive shape used on real M15+ cards.

    Derived from FeSens pt-outer.svg (viewBox 119×61):
      Path with sides curving from straight top/bottom to a wider midpoint.
      The shape is 111px wide effective (8→119) in a 119px viewBox,
      with height 53px in a 61px viewBox.
    """
    # Proportions from FeSens pt-outer.svg
    mid = h * 0.5       # vertical midpoint
    # How far the sides bow outward beyond the top/bottom straight edges
    bow = w * 0.065      # ~8/119 of total width
    # Corner radius at top/bottom
    cr = h * 0.12        # small curve at corners
    # Inset of straight edges from full width (where corners start)
    ci = w * 0.08        # ~9/119

    return (
        f"M{x+ci} {y} "                                          # top-left corner start
        f"H{x+w-ci} "                                            # straight top edge
        f"C{x+w-ci+cr} {y} {x+w+bow} {y+mid*0.48} {x+w+bow} {y+mid} "  # right side bows out
        f"C{x+w+bow} {y+mid+mid*0.52} {x+w-ci+cr} {y+h} {x+w-ci} {y+h} "  # right side back in
        f"H{x+ci} "                                              # straight bottom edge
        f"C{x+ci-cr} {y+h} {x-bow} {y+mid+mid*0.52} {x-bow} {y+mid} "  # left side bows out
        f"C{x-bow} {y+mid*0.48} {x+ci-cr} {y} {x+ci} {y} Z"   # left side back in
    )


# ===========================================================================
# New frame layer SVG renderers (v2)
# ===========================================================================

def _render_border_layer(opacity: float, render: dict = None) -> str:
    """Authentic MTG outer border with proper structure.

    The border is a filled shape (not just a stroke) that forms the card edge,
    with cutouts for the card interior. This matches how real MTG borders work —
    a solid dark frame with the colored card interior visible through the cutout.
    """
    render = render or {}
    # A zero-width border is invisible even when the layer is toggled on
    # (basic sets border_width 0) — floor it so enabling the
    # Border layer always actually draws a border.
    bw = render.get('border_width', 8) or 8
    br = render.get('border_radius', 22)
    bc = render.get('border_color') or '#17140f'  # authentic dark brown-black
    w, h = VB_W, VB_H

    parts = [f'<g opacity="{opacity}">']

    # Outer card shape (rounded rect)
    outer = (
        f"M{br} 0 H{w-br} Q{w} 0 {w} {br} "
        f"V{h-br} Q{w} {h} {w-br} {h} "
        f"H{br} Q0 {h} 0 {h-br} "
        f"V{br} Q0 0 {br} 0 Z"
    )
    # Inner cutout (slightly smaller, inset by border width)
    ibr = max(br - bw * 0.6, 4)  # inner border radius
    ix, iy = bw, bw
    iw, ih = w - 2 * bw, h - 2 * bw
    inner = (
        f"M{ix+ibr} {iy} H{ix+iw-ibr} Q{ix+iw} {iy} {ix+iw} {iy+ibr} "
        f"V{iy+ih-ibr} Q{ix+iw} {iy+ih} {ix+iw-ibr} {iy+ih} "
        f"H{ix+ibr} Q{ix} {iy+ih} {ix} {iy+ih-ibr} "
        f"V{iy+ibr} Q{ix} {iy} {ix+ibr} {iy} Z"
    )

    # Filled border using evenodd
    parts.append(
        f'<path d="{outer} {inner}" fill="{bc}" fill-rule="evenodd"/>'
    )

    # Subtle highlight on top-left edge for depth
    parts.append(
        f'<rect x="0" y="0" width="{w}" height="{h}" rx="{br}" '
        f'fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1.5"/>'
    )

    # Double border: second inner stroke for retro style
    if render.get('double_border'):
        inner_gap = bw + 4
        ibr2 = max(ibr - 3, 3)
        inner_inner = (
            f"M{inner_gap+ibr2} {inner_gap} H{w-inner_gap-ibr2} "
            f"Q{w-inner_gap} {inner_gap} {w-inner_gap} {inner_gap+ibr2} "
            f"V{h-inner_gap-ibr2} Q{w-inner_gap} {h-inner_gap} "
            f"{w-inner_gap-ibr2} {h-inner_gap} "
            f"H{inner_gap+ibr2} Q{inner_gap} {h-inner_gap} "
            f"{inner_gap} {h-inner_gap-ibr2} "
            f"V{inner_gap+ibr2} Q{inner_gap} {inner_gap} "
            f"{inner_gap+ibr2} {inner_gap} Z"
        )
        parts.append(
            f'<path d="{inner_inner}" fill="none" stroke="{bc}" '
            f'stroke-width="1.5" opacity="0.6"/>'
        )

    parts.append('</g>')
    return ''.join(parts)


def _render_frame_bg_layer(theme: dict, opacity: float,
                           type_y: int = TYPE_Y, render: dict = None) -> str:
    """Authentic MTG colored frame background with art window cutout.

    The frame background is the colored portion of the card (green for green cards,
    blue for blue, gold for multicolor, etc.). It fills the entire card interior
    with a cutout for the art window. On real cards, this is where the card's
    color identity is most visible.

    Matches FeSens colored-bg.svg structure: solid color fill with evenodd cutout.
    """
    render = render or {}
    bw = render.get('border_width', 8)
    r = max(CORNER_R - bw * 0.5, 6)  # inner radius matching border
    w, h = VB_W, VB_H
    m = render.get('art_margin', MARGIN)
    pw = render.get('pinline_width', PINLINE_W)
    art_top = NAME_Y + NAME_H + pw
    art_bottom = type_y - pw
    ar = max(render.get('field_radius', FIELD_R) - 2, 4)

    # Frame outline (inset from border, clockwise)
    inset = max(bw, 1)
    ix, iy = inset, inset
    iw, ih = w - 2 * inset, h - 2 * inset
    ir = max(r - 2, 4)
    outer = (
        f"M{ix+ir} {iy} H{ix+iw-ir} Q{ix+iw} {iy} {ix+iw} {iy+ir} "
        f"V{iy+ih-ir} Q{ix+iw} {iy+ih} {ix+iw-ir} {iy+ih} "
        f"H{ix+ir} Q{ix} {iy+ih} {ix} {iy+ih-ir} "
        f"V{iy+ir} Q{ix} {iy} {ix+ir} {iy} Z"
    )
    # Art window cutout (counter-clockwise, rounded corners)
    ax, ay = m, art_top
    aw = w - 2 * m
    ah = art_bottom - art_top
    inner = (
        f"M{ax+ar} {ay} "
        f"Q{ax} {ay} {ax} {ay+ar} "
        f"V{ay+ah-ar} Q{ax} {ay+ah} {ax+ar} {ay+ah} "
        f"H{ax+aw-ar} Q{ax+aw} {ay+ah} {ax+aw} {ay+ah-ar} "
        f"V{ay+ar} Q{ax+aw} {ay} {ax+aw-ar} {ay} Z"
    )

    bg_color = theme.get('bg', '#2E2E2E')
    # Apply frame tint if specified (nyx style mixes tint with card color)
    tint = render.get('frame_tint')
    if tint:
        bg_color = _blend_hex(bg_color, tint, 0.4)

    parts = [f'<g opacity="{opacity}">']
    parts.append(f'<path d="{outer} {inner}" fill="{bg_color}" fill-rule="evenodd"/>')

    # Subtle highlight on frame edges for depth (like real cards catching light)
    parts.append(
        f'<path d="{outer}" fill="none" stroke="rgba(255,255,255,0.06)" '
        f'stroke-width="1"/>'
    )

    # Nyx starfield pattern overlay
    if render.get('frame_pattern') == 'nyx':
        parts.append(
            f'<path d="{outer} {inner}" fill="url(#nyxStars)" fill-rule="evenodd" '
            f'opacity="0.7"/>'
        )

    parts.append('</g>')
    return ''.join(parts)


def _render_inner_border_layer(theme: dict, opacity: float,
                               type_y: int = TYPE_Y, render: dict = None) -> str:
    """Inner colored border around the art window (retro/old-border style)."""
    render = render or {}
    m = render.get('art_margin', MARGIN)
    pw = render.get('pinline_width', PINLINE_W)
    ibw = render.get('inner_border_width', 3)
    fr = render.get('field_radius', FIELD_R)

    art_top = NAME_Y + NAME_H + pw
    art_bottom = type_y - pw
    border_color = theme.get('border', '#8B7D6B')

    # Rect around art window with inner border
    ax = m - ibw
    ay = art_top - ibw
    aw = VB_W - 2 * m + 2 * ibw
    ah = art_bottom - art_top + 2 * ibw
    return (
        f'<g opacity="{opacity}">'
        f'<rect x="{ax}" y="{ay}" width="{aw}" height="{ah}" '
        f'rx="{fr + 2}" fill="none" stroke="{border_color}" '
        f'stroke-width="{ibw}"/>'
        f'</g>'
    )


def _render_pinlines_layer(theme: dict, opacity: float,
                           type_y: int = TYPE_Y, type_h: int = TYPE_H,
                           render: dict = None) -> str:
    """Authentic MTG pinlines — thin colored lines between frame sections.

    On real M15 cards, pinlines are the thin colored lines that separate
    the name bar from the art, the art from the type bar, etc. They run
    the full width of the card interior and are a key visual element
    that defines the card's color identity (they're often gradient-colored
    on multicolor cards).
    """
    render = render or {}
    pw = render.get('pinline_width', PINLINE_W)
    if pw <= 0:
        return ''
    bw = render.get('border_width', 8)
    m = max(bw + 1, render.get('art_margin', MARGIN) - 8)  # extend close to border
    border_color = theme.get('border', '#8B7D6B')
    name_bottom = NAME_Y + NAME_H
    type_bottom = type_y + type_h
    rules_bottom = RULES_BOTTOM
    x1 = m
    x2 = VB_W - m

    lines = []
    # Below name bar
    lines.append(
        f'<line x1="{x1}" y1="{name_bottom}" x2="{x2}" y2="{name_bottom}" '
        f'stroke="{border_color}" stroke-width="{pw}"/>'
    )
    # Above type bar
    lines.append(
        f'<line x1="{x1}" y1="{type_y}" x2="{x2}" y2="{type_y}" '
        f'stroke="{border_color}" stroke-width="{pw}"/>'
    )
    # Below type bar
    lines.append(
        f'<line x1="{x1}" y1="{type_bottom}" x2="{x2}" y2="{type_bottom}" '
        f'stroke="{border_color}" stroke-width="{pw}"/>'
    )
    # Below text box (above info/bottom area)
    lines.append(
        f'<line x1="{x1}" y1="{rules_bottom}" x2="{x2}" y2="{rules_bottom}" '
        f'stroke="{border_color}" stroke-width="{pw}"/>'
    )
    return f'<g opacity="{opacity}">{"".join(lines)}</g>'



def _render_info_bar_layer(opacity: float,
                           render: dict = None) -> str:
    """Small dark bar at card bottom with collector info."""
    render = render or {}
    m = render.get('art_margin', MARGIN)
    fr = render.get('field_radius', FIELD_R)
    bar_h = 22
    bar_y = VB_H - m - bar_h
    bar_x = m
    bar_w = VB_W - 2 * m
    return (
        f'<g opacity="{opacity}">'
        f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" '
        f'rx="{min(fr, 6)}" fill="rgba(0,0,0,0.65)"/>'
        f'<text x="{bar_x + 8}" y="{bar_y + 15}" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="10" '
        f'fill="rgba(255,255,255,0.6)">Deck Art Studio</text>'
        f'</g>'
    )


def _render_bevel(path_d: str, opacity: float = 0.3) -> str:
    """Add a bevel highlight/shadow effect to a field (retro style)."""
    return (
        # Top/left highlight
        f'<path d="{path_d}" fill="none" stroke="rgba(255,255,255,{opacity})" '
        f'stroke-width="1.5" clip-path="inset(0 50% 50% 0)"/>'
        # Bottom/right shadow
        f'<path d="{path_d}" fill="none" stroke="rgba(0,0,0,{opacity})" '
        f'stroke-width="1.5" clip-path="inset(50% 0 0 50%)"/>'
    )


def _render_nyx_defs() -> str:
    """SVG <pattern> definition for the Nyx/enchantment starfield effect."""
    return '''<pattern id="nyxStars" x="0" y="0" width="40" height="40"
  patternUnits="userSpaceOnUse" patternTransform="rotate(15)">
  <circle cx="5" cy="8" r="1.2" fill="rgba(255,255,255,0.8)"/>
  <circle cx="22" cy="4" r="0.7" fill="rgba(255,255,255,0.5)"/>
  <circle cx="35" cy="12" r="0.9" fill="rgba(255,255,255,0.65)"/>
  <circle cx="12" cy="22" r="0.5" fill="rgba(255,255,255,0.4)"/>
  <circle cx="30" cy="25" r="1.4" fill="rgba(255,255,255,0.75)"/>
  <circle cx="8" cy="35" r="0.6" fill="rgba(255,255,255,0.45)"/>
  <circle cx="18" cy="32" r="1.0" fill="rgba(255,255,255,0.6)"/>
  <circle cx="38" cy="36" r="0.8" fill="rgba(255,255,255,0.55)"/>
  <circle cx="26" cy="15" r="0.4" fill="rgba(255,255,255,0.35)"/>
  <circle cx="3" cy="18" r="0.6" fill="rgba(255,255,255,0.5)"/>
</pattern>'''


def _blend_hex(color1: str, color2: str, ratio: float) -> str:
    """Blend two hex colors. ratio=0 returns color1, ratio=1 returns color2."""
    try:
        r1, g1, b1 = int(color1[1:3], 16), int(color1[3:5], 16), int(color1[5:7], 16)
        r2, g2, b2 = int(color2[1:3], 16), int(color2[3:5], 16), int(color2[5:7], 16)
        r = int(r1 * (1 - ratio) + r2 * ratio)
        g = int(g1 * (1 - ratio) + g2 * ratio)
        b = int(b1 * (1 - ratio) + b2 * ratio)
        return f'#{r:02x}{g:02x}{b:02x}'
    except (ValueError, IndexError):
        return color1


def _pt_box_path_pointed(x: float, y: float, w: float, h: float) -> str:
    """P/T box with sharper pointed corners (retro style)."""
    notch = 12
    return (
        f"M{x+notch} {y} H{x+w-notch} "
        f"L{x+w} {y+h/2} "
        f"L{x+w-notch} {y+h} H{x+notch} "
        f"L{x} {y+h/2} Z"
    )


# ===========================================================================
# Rules text renderer with inline mana symbols
# ===========================================================================
def _wrap_paragraph_with_pips(tokens: List[dict], max_width: float,
                              font_size: int, width_fn=None) -> List[List[dict]]:
    """Word-wrap a tokenized paragraph into lines, estimating widths.

    Returns a list of lines.  Each line is a list of items:
        {'type': 'text', 'value': "word1 word2 ..."}   (accumulated text)
        {'type': 'symbol', 'value': 'R'}                (pip)

    `width_fn(line_index)` optionally gives a per-line max width (used to wrap
    bottom lines narrower around a P/T plate); falls back to `max_width`.
    """
    space_w = _measure_text(' ', font_size)
    pip_w = _rules_pip_size(font_size) + 2   # width reserved for a pip image

    # Break tokens into individual words and symbols, with measured widths
    items: List[dict] = []
    for tok in tokens:
        if tok['type'] == 'symbol':
            items.append(tok)
        else:
            for w in tok['value'].split(' '):
                if w:
                    items.append({'type': 'text', 'value': w,
                                  'width': _measure_text(w, font_size)})

    lines: List[List[dict]] = []
    cur_line: List[dict] = []
    cur_width = 0.0
    cur_text_run = ""

    def cur_max_width():
        return width_fn(len(lines)) if width_fn else max_width

    def flush_text_run():
        nonlocal cur_text_run
        if cur_text_run:
            cur_line.append({'type': 'text', 'value': cur_text_run})
            cur_text_run = ""

    def start_new_line():
        nonlocal cur_line, cur_width, cur_text_run
        flush_text_run()
        if cur_line:
            lines.append(cur_line)
        cur_line = []
        cur_width = 0.0

    for item in items:
        if item['type'] == 'symbol':
            needed = pip_w + (space_w if cur_width > 0 else 0)
            if cur_width > 0 and cur_width + needed > cur_max_width():
                start_new_line()
            # Flush text BEFORE the space so the space is a separate gap
            flush_text_run()
            if cur_width > 0:
                cur_line.append({'type': 'gap', 'width': space_w})
                cur_width += space_w
            cur_line.append(item)
            cur_width += pip_w
        else:
            word = item['value']
            word_width = item.get('width', _measure_text(word, font_size))
            needed = word_width + (space_w if cur_width > 0 else 0)
            # Punctuation sticks to previous word (no extra space)
            if word and word[0] in ':,.;\u2014' and cur_width > 0:
                needed = word_width

            # Check if previous item was a pip (last item in cur_line is a symbol)
            after_pip = (cur_line and cur_line[-1].get('type') == 'symbol')

            if cur_width > 0 and cur_width + needed > cur_max_width():
                start_new_line()
                cur_text_run = word
                cur_width = word_width
            else:
                if cur_width > 0 and not (word and word[0] in ':,.;\u2014'):
                    if after_pip:
                        # Use exact gap after pip
                        flush_text_run()
                        cur_line.append({'type': 'gap', 'width': space_w})
                    else:
                        cur_text_run += " "
                    cur_width += space_w
                cur_text_run += word
                cur_width += word_width

    flush_text_run()
    if cur_line:
        lines.append(cur_line)

    return lines


def render_rules_text_svg(oracle_text: str, x_start: float, y_start: float,
                          max_width: float, max_height: float,
                          font_size: int, line_spacing: int,
                          text_color: str = '#000',
                          font_family: str = None,
                          italic: bool = False,
                          avoid: Optional[Tuple[float, float]] = None) -> Tuple[List[str], float]:
    """Render oracle text as SVG with inline pip symbols.

    Text segments are rendered as single <text> elements so the SVG engine
    handles character spacing natively.  Pips are placed inline using
    estimated widths.

    `avoid` = (y_top, narrow_width): lines whose baseline reaches y_top wrap at
    `narrow_width` instead of `max_width`, flowing around a P/T plate in the
    box's bottom-right corner instead of running under it.
    """
    if not oracle_text:
        return [], 0

    if font_family is None:
        font_family = RULES_FONT_FAMILY

    italic_attr = ' font-style="italic"' if italic else ''

    svg_elements = []
    # Use actual font metrics for pixel-perfect positioning
    space_w = font_size * 0.22
    pip_size = _rules_pip_size(font_size)
    pip_img_w = pip_size + 2

    current_y = y_start
    paragraphs = oracle_text.split('\n')

    for para_idx, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            current_y += line_spacing * 0.5
            continue

        tokens = tokenize_oracle_text(paragraph)
        width_fn = None
        if avoid is not None:
            base_y = current_y
            width_fn = (lambda i, _b=base_y:
                        min(max_width, avoid[1])
                        if _b + i * line_spacing + font_size * 0.3 >= avoid[0]
                        else max_width)
        lines = _wrap_paragraph_with_pips(tokens, max_width, font_size, width_fn)

        for line_items in lines:
            if current_y + line_spacing > y_start + max_height:
                return svg_elements, current_y - y_start

            cx = x_start
            for seg in line_items:
                if seg['type'] == 'gap':
                    # Exact-width space between text and pip
                    cx += seg['width']
                elif seg['type'] == 'symbol':
                    pip_x = cx
                    pip_y = current_y - pip_size + 2
                    # Background circle so pip is visible against any textbox color
                    pip_cx = pip_x + pip_size / 2
                    pip_cy = pip_y + pip_size / 2
                    svg_elements.append(
                        f'<circle cx="{pip_cx}" cy="{pip_cy}" r="{pip_size/2 + 1}" '
                        f'fill="rgba(255,255,255,0.6)"/>'
                    )
                    svg_elements.append(_pip_image_tag(seg['value'], pip_x, pip_y, pip_size))
                    cx += pip_img_w
                else:
                    # Render entire text segment as one <text> element
                    escaped = (seg['value']
                               .replace('&', '&amp;')
                               .replace('<', '&lt;')
                               .replace('>', '&gt;')
                               .replace('"', '&quot;'))
                    svg_elements.append(
                        f'<text x="{cx}" y="{current_y}" font-family="{font_family}" '
                        f'font-size="{font_size}"{italic_attr} fill="{text_color}">{escaped}</text>'
                    )
                    # Advance by actual measured text width
                    cx += _measure_text(seg['value'], font_size)

            current_y += line_spacing

        if para_idx < len(paragraphs) - 1:
            current_y += line_spacing * 0.35

    return svg_elements, current_y - y_start


# ===========================================================================
# Main frame SVG generator
# ===========================================================================
def _measure_rules_text(oracle_text: str, max_width: float,
                        font_size: int, line_spacing: int,
                        avoid: Optional[Tuple[float, float]] = None) -> float:
    """Pre-measure how tall the rules text will be without rendering.

    `avoid` mirrors render_rules_text_svg's parameter, with y_top expressed
    RELATIVE to the first baseline (callers pass abs_y_top - y_start)."""
    if not oracle_text:
        return 0

    current_y = 0.0
    paragraphs = oracle_text.split('\n')

    for para_idx, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            current_y += line_spacing * 0.5
            continue

        tokens = tokenize_oracle_text(paragraph)
        width_fn = None
        if avoid is not None:
            base_y = current_y
            width_fn = (lambda i, _b=base_y:
                        min(max_width, avoid[1])
                        if _b + i * line_spacing + font_size * 0.3 >= avoid[0]
                        else max_width)
        lines = _wrap_paragraph_with_pips(tokens, max_width, font_size, width_fn)
        current_y += len(lines) * line_spacing

        if para_idx < len(paragraphs) - 1:
            current_y += line_spacing * 0.35

    return current_y


# Rules text must ALWAYS render completely and can never overflow its box.
# The user's Rules Text Size is a CEILING, not an absolute: the renderer finds
# the largest font <= desired that fits this card's box and uses that.
RULES_FONT_FLOOR = 8  # absolute last resort — real cards never get near it


def _max_fitting_rules_font(measure, box_h: float, desired: int,
                            floor: int = RULES_FONT_FLOOR) -> int:
    """Largest font size <= desired whose measured rules text fits box_h.

    `measure(font)` returns the needed height at that font size (including
    any flavor reserve / P/T avoid-region wrapping the caller bakes in).
    """
    f = max(int(desired), floor)
    while f > floor and measure(f) > box_h:
        f -= 1
    return f


def _render_split_rules_svg(card: CardData, fs: dict, x: float, y_top: float,
                            w: float, h: float, text_color: str,
                            desired_font: int,
                            avoid: Optional[Tuple[float, float]] = None) -> List[str]:
    """Two-column rules area for single-art multi-part cards.

    Adventure (Murderous Rider // Swift End): LEFT column = the adventure half
    (name / cost / type header + its rules), RIGHT column = the creature
    half's rules — matching the real Eldraine layout. Split/room cards
    (Smoky Lounge // Misty Salon): one column per half, each with its own
    name/cost/type header.

    Coordinate-agnostic: works in any style's rules rect (672- or 750-space).
    `avoid` (abs y_top, narrow width from box left) applies to the right
    column only — the creature half wraps around the P/T plate.
    """
    faces = card.split_faces
    if card.layout == 'adventure':
        cols = [
            {'face': faces[1], 'text': faces[1].get('oracle_text') or ''},
            {'face': None, 'text': card.oracle_text or ''},
        ]
    else:  # split / room
        cols = [
            {'face': faces[0], 'text': faces[0].get('oracle_text') or ''},
            {'face': faces[1], 'text': faces[1].get('oracle_text') or ''},
        ]

    gap = max(16.0, w * 0.03)
    col_w = (w - gap) / 2
    xs = [x, x + col_w + gap]

    def _col_avoid_narrow():
        """Allowed line width inside the RIGHT column above the P/T plate."""
        return avoid[1] - (col_w + gap) if avoid is not None else None

    def measure(f):
        line_h = int(RULES_LINE_H * f / RULES_FONT)
        worst = 0.0
        for i, col in enumerate(cols):
            hh = (line_h + line_h * 0.85) if col['face'] else 0.0
            need = hh
            if avoid is not None and i == 1:
                narrow = _col_avoid_narrow()
                if narrow >= 60:
                    av = (avoid[0] - (y_top + hh + f * 0.8), narrow)
                    need += _measure_rules_text(col['text'], col_w, f, line_h, avoid=av)
                else:
                    # Plate eats the whole column bottom — text must END above it
                    body = _measure_rules_text(col['text'], col_w, f, line_h)
                    cap = max(avoid[0] - y_top - 4, 0)
                    need += body if hh + body <= cap else body + (h - cap)
            else:
                need += _measure_rules_text(col['text'], col_w, f, line_h)
            worst = max(worst, need)
        return worst

    f = _max_fitting_rules_font(measure, h, int(desired_font))
    line_h = int(RULES_LINE_H * f / RULES_FONT)
    if measure(f) > h + 2:  # only possible at the hard floor
        fs.setdefault('_quality', []).append(
            f'rules_overflow: split text needs {measure(f):.0f}px but box is {h:.0f}px (font {f})')

    parts = []
    mid = x + col_w + gap / 2
    parts.append(f'<line x1="{mid:.1f}" y1="{y_top + 2:.1f}" x2="{mid:.1f}" '
                 f'y2="{y_top + h - 2:.1f}" stroke="{text_color}" '
                 f'stroke-width="1.2" opacity="0.3"/>')

    for i, col in enumerate(cols):
        cx = xs[i]
        cy = y_top + f * 0.8  # first baseline
        if col['face']:
            face = col['face']
            raw_name = face.get('name') or ''
            fname = raw_name.replace('&', '&amp;').replace('<', '&lt;')
            pips = parse_mana_cost(face.get('mana_cost') or '')
            # Half titles print larger than body text on real cards; their
            # cost pips match the body's inline pips so every symbol in the
            # rules area is the same size
            nf = int(f * 1.12)
            ps = _rules_pip_size(f)
            pips_w = len(pips) * (ps + 2) + 6 if pips else 0
            est = len(raw_name) * nf * 0.55
            name_avail = col_w - pips_w
            if est > name_avail and name_avail > 0:
                nf = max(12, int(nf * name_avail / est))
            parts.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="{NAME_FONT_FAMILY}" '
                         f'font-size="{nf}" font-weight="bold" fill="{text_color}">{fname}</text>')
            if pips:
                px = cx + col_w
                pcy = cy - nf * 0.32
                for pip in reversed(pips):
                    pxx = px - ps
                    parts.append(f'<circle cx="{pxx + ps/2:.1f}" cy="{pcy:.1f}" '
                                 f'r="{ps/2 + 0.5:.1f}" fill="rgba(0,0,0,0.25)"/>')
                    parts.append(_pip_image_tag(pip, pxx, pcy - ps / 2, ps))
                    px -= (ps + 2)
            cy += line_h
            ftype = (face.get('type_line') or '').replace('&', '&amp;').replace('<', '&lt;')
            tf2 = max(11, int(f * 0.78))
            parts.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="{TYPE_FONT_FAMILY}" '
                         f'font-size="{tf2}" font-weight="bold" fill="{text_color}" '
                         f'opacity="0.82">{ftype}</text>')
            cy += line_h * 0.85

        av_abs = None
        if avoid is not None and i == 1:
            narrow = _col_avoid_narrow()
            if narrow >= 60:
                av_abs = (avoid[0], narrow)
        body_h = h - (cy - (y_top + f * 0.8))
        if avoid is not None and i == 1 and av_abs is None:
            body_h = min(body_h, avoid[0] - cy - 4)  # hard-stop above the P/T plate
        body, _ = render_rules_text_svg(col['text'], cx, cy, col_w, body_h,
                                        f, line_h, text_color=text_color,
                                        avoid=av_abs)
        parts.extend(body)

    return parts


# ===========================================================================
# Planeswalker loyalty ability renderer
# ===========================================================================
# Loyalty ability cost at a line start: '+2:', '0:', '−12:', '−X:', '+X:'.
# MULTILINE so .search() detects planeswalkers whose oracle text OPENS
# with a static ability (e.g. Nissa, Who Shakes the World).
_LOYALTY_RE = re.compile(r'^([+\-−]?(?:\d+|[Xx]))\s*:\s*', re.MULTILINE)


def _get_loyalty_shape_key(cost_str: str) -> Optional[str]:
    """Get the SVG path key for the loyalty shape (up/down/zero arrow)
    that corresponds to a cost string like '+1', '−2', '0'.
    Prefers vector SVG paths from mana-master, falls back to pips_b64 PNGs.
    """
    normalized = cost_str.replace('\u2212', '-')
    if normalized.startswith('+'):
        return 'loyalty_up'
    elif normalized.startswith('-'):
        return 'loyalty_down'
    else:
        return 'loyalty_zero'


def _loyalty_badge_path(x: float, y: float, w: float, h: float) -> str:
    """Fallback SVG path for a loyalty ability cost badge — a pointed shield
    shape like on real MTG planeswalker cards. Points left.
    Used only when official Scryfall symbols aren't available.
    """
    arrow = 8  # pointiness of the left arrow
    r = 4      # corner rounding on right side
    return (
        f"M{x} {y + h/2} "
        f"L{x + arrow} {y + 2} "
        f"L{x + w - r} {y} Q{x + w} {y} {x + w} {y + r} "
        f"V{y + h - r} Q{x + w} {y + h} {x + w - r} {y + h} "
        f"L{x + arrow} {y + h - 2} Z"
    )


def _loyalty_counter_path(cx: float, cy: float, size: float) -> str:
    """Fallback SVG path for the loyalty counter — a downward-pointing
    shield/chevron like on real MTG planeswalker cards.
    Used only when official Scryfall symbols aren't available.
    """
    w = size
    h = size * 1.15
    x = cx - w / 2
    y = cy - h * 0.42
    notch = 5
    point_y = y + h
    return (
        f"M{x + notch} {y} "
        f"H{x + w - notch} "
        f"L{x + w} {y + notch} "
        f"V{y + h * 0.6} "
        f"L{cx} {point_y} "
        f"L{x} {y + h * 0.6} "
        f"V{y + notch} Z"
    )


def _parse_loyalty_abilities(oracle_text: str) -> list[dict]:
    """Parse planeswalker oracle text into loyalty abilities.

    Returns list of dicts:
        {cost: '+1', text: 'Tap target permanent...'}
    Non-loyalty paragraphs (like static abilities) get cost=None.
    """
    abilities = []
    for para in oracle_text.split('\n'):
        para = para.strip()
        if not para:
            continue
        m = _LOYALTY_RE.match(para)
        if m:
            cost = m.group(1)
            # Normalize minus signs to unicode minus for display
            cost = cost.replace('-', '\u2212')
            text = para[m.end():].strip()
            abilities.append({'cost': cost, 'text': text})
        else:
            abilities.append({'cost': None, 'text': para})
    return abilities


def _render_loyalty_badge_vector(cost_str: str, x: float, y: float,
                                  w: float, h: float,
                                  badge_font_size: int) -> Optional[list]:
    """Render a loyalty badge using embedded SVG vector paths from mana-master.

    The mana-master loyalty shapes are defined in a 32×32 viewBox.
    We scale them to the desired size and overlay the cost number in bold white.

    Returns a list of SVG elements, or None if no shape available.
    """
    shape_key = _get_loyalty_shape_key(cost_str)
    if not shape_key:
        return None

    # Prefer vector path from mana-master
    path_d = _LOYALTY_SVG_PATHS.get(shape_key)
    if not path_d:
        b64 = _PIPS_B64.get(shape_key)
        if not b64:
            return None

    elements = []

    if path_d:
        # Scale the 32×32 path to target w×h
        sx = w / 32.0
        sy = h / 32.0
        # Drop shadow
        elements.append(
            f'<g transform="translate({x + 1.5},{y + 1.5}) scale({sx:.4f},{sy:.4f})">'
            f'<path d="{path_d}" fill="rgba(0,0,0,0.35)"/>'
            f'</g>'
        )
        # Main shape
        elements.append(
            f'<g transform="translate({x},{y}) scale({sx:.4f},{sy:.4f})">'
            f'<path d="{path_d}" fill="#1a1410"/>'
            f'</g>'
        )
    else:
        # Rasterized PNG fallback
        elements.append(
            f'<image x="{x}" y="{y}" width="{w}" height="{h}" '
            f'href="data:image/png;base64,{b64}" />'
        )

    # ── Overlay cost text, centered on the area-weighted visual mass ──
    # Computed from actual pixel analysis of the mana-master SVG shapes:
    #   loyalty-up:   area-weighted center at 53.7% of viewBox height
    #   loyalty-down: area-weighted center at 45.9% of viewBox height
    #   loyalty-zero: area-weighted center at 49.7% of viewBox height
    # SVG <text y="..."> is the baseline, so we add ~35% of font size
    # to convert from visual center to text baseline position.
    normalized = cost_str.replace('\u2212', '-')
    num_part = normalized.lstrip('+-')
    sign = ''
    font_baseline_offset = badge_font_size * 0.35

    if normalized.startswith('+'):
        sign = '+'
        text_y = y + h * 0.537 + font_baseline_offset
    elif normalized.startswith('-'):
        sign = '-'
        text_y = y + h * 0.459 + font_baseline_offset
    else:
        text_y = y + h * 0.497 + font_baseline_offset

    text_x = x + w / 2

    if sign == '-':
        # Draw minus as a line (cairosvg doesn't render Unicode minus reliably)
        # Measure the number width to center the whole "−N" pair
        num_w = _measure_text(num_part, badge_font_size)
        minus_w = badge_font_size * 0.38
        gap = badge_font_size * 0.06  # small gap between minus and number
        total_w = minus_w + gap + num_w
        pair_start_x = text_x - total_w / 2
        line_y = text_y - badge_font_size * 0.28
        elements.append(
            f'<line x1="{pair_start_x}" y1="{line_y}" '
            f'x2="{pair_start_x + minus_w}" y2="{line_y}" '
            f'stroke="white" stroke-width="3.5" stroke-linecap="round"/>'
        )
        num_x = pair_start_x + minus_w + gap
        elements.append(
            f'<text x="{num_x}" y="{text_y}" '
            f'font-family="{PT_FONT_FAMILY}" font-size="{badge_font_size}" '
            f'font-weight="bold" fill="white">{num_part}</text>'
        )
    else:
        display_text = f"{sign}{num_part}" if sign else num_part
        elements.append(
            f'<text x="{text_x}" y="{text_y}" text-anchor="middle" '
            f'font-family="{PT_FONT_FAMILY}" font-size="{badge_font_size}" '
            f'font-weight="bold" fill="white">{display_text}</text>'
        )

    return elements


_BADGE_URI_CACHE: Dict[str, str] = {}

# Per-icon plate geometry, measured from the badge PNGs' alpha channel. The
# arrow extends the bounding box differently per icon (Plus: arrow on top,
# Minus: bottom, Neutral: none), so the cost number must anchor to the PLATE
# center, not the image center. aspect = h/w; cy/cx = plate center as a
# fraction of image height/width (baked colon excluded from cx).
_BADGE_GEOM = {
    'planeswalkerPlus':    {'aspect': 101 / 140, 'cy': 0.525, 'cx': 0.493},
    'planeswalkerMinus':   {'aspect': 99 / 142,  'cy': 0.354, 'cx': 0.493},
    'planeswalkerNeutral': {'aspect': 85 / 142,  'cy': 0.453, 'cx': 0.489},
}


def _badge_icon_for(cost_str: str) -> str:
    cs = cost_str.replace('−', '-').strip()
    return ('planeswalkerPlus' if cs.startswith('+')
            else 'planeswalkerMinus' if cs.startswith('-')
            else 'planeswalkerNeutral')


def _authentic_loyalty_badge_svg(cost_str: str, x: float, line_center_y: float,
                                 badge_w: float, font_size: float) -> list:
    """Loyalty-cost badge using the real cardconjurer badge art (embedded as
    a data URI), with the white cost number anchored to the measured PLATE
    center — vertically aligned to line_center_y regardless of which way the
    icon's arrow extends. Returns [] if the badge assets aren't available
    (callers fall back to vector shapes)."""
    import base64
    cs = cost_str.replace('−', '-').strip()
    icon = _badge_icon_for(cost_str)
    uri = _BADGE_URI_CACHE.get(icon)
    if uri is None:
        path = FRAMES_DIR / 'planeswalker' / f'{icon}.png'
        if not path.exists():
            _BADGE_URI_CACHE[icon] = ''
            return []
        uri = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()
        _BADGE_URI_CACHE[icon] = uri
    if not uri:
        return []
    g = _BADGE_GEOM[icon]
    bh = badge_w * g['aspect']
    by = line_center_y - g['cy'] * bh   # plate center sits on line_center_y
    cx = x + g['cx'] * badge_w
    cy = line_center_y + font_size * 0.35
    return [
        f'<image x="{x:.1f}" y="{by:.1f}" width="{badge_w:.1f}" height="{bh:.1f}" '
        f'xlink:href="{uri}"/>',
        f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
        f'font-family="{PT_FONT_FAMILY}" font-size="{font_size}" '
        f'font-weight="bold" fill="white">{cs}</text>',
    ]


def render_planeswalker_abilities(card_oracle: str,
                                  x_start: float, y_start: float,
                                  max_width: float, max_height: float,
                                  font_size: int, line_spacing: int,
                                  text_color: str = '#1a1410',
                                  theme: dict = None,
                                  avoid=None) -> tuple:
    """Render planeswalker abilities with large loyalty cost badges and dividers.

    Matches authentic MTG planeswalker card layout:
    - Big, bold loyalty badges (up/down/zero arrows) on the left
    - Horizontal divider lines between abilities
    - Ability text indented past the badge with clear spacing
    - Starting loyalty counter rendered separately by the caller

    Returns (svg_elements, used_height).
    """
    abilities = _parse_loyalty_abilities(card_oracle)
    if not abilities:
        return [], 0

    svg_elements = []
    current_y = y_start

    # ── Badge sizing — proportioned to match real MTG cards ──
    # Scaled with font_size so the fitting loop can actually shrink the whole
    # ability block (fixed badges made tight boxes unfittable at any font).
    # Ratios calibrated so the DEFAULT pw font (23) gives the original
    # 80/68/12/28 pixel values exactly.
    badge_w = round(font_size * 3.48)       # width of loyalty shape render area
    badge_h = round(font_size * 2.96)       # height (shapes pad in 32×32 viewBox)
    badge_margin = round(font_size * 0.52)  # gap between badge and ability text
    text_indent = badge_w + badge_margin
    badge_font_size = round(font_size * 1.02)  # bold cost text, breathing room in plate

    border_color = theme['border'] if theme else '#DAA520'

    for i, ability in enumerate(abilities):
        # NOTE: no truncation — every ability always renders. The caller
        # fits the font so the returned used_height <= max_height.


        if ability['cost'] is not None:
            cost_str = ability['cost']

            # Position badge: flush with the content rect's left edge
            badge_x = x_start
            badge_y = current_y - badge_h * 0.42
            line_center_y = current_y - font_size * 0.32  # first line visual center

            # Authentic cardconjurer badge art (Plus/Minus/Neutral raster,
            # colon baked in) with a white cost number — the vector shapes
            # read as flat clip-art next to the real thing.
            badge_elements = _authentic_loyalty_badge_svg(
                cost_str, badge_x, line_center_y, badge_w, badge_font_size)
            if not badge_elements:
                # Vector fallback if the badge assets are missing
                badge_elements = _render_loyalty_badge_vector(
                    cost_str, badge_x, badge_y, badge_w, badge_h,
                    badge_font_size
                )

            if badge_elements:
                svg_elements.extend(badge_elements)
            else:
                # Fallback: draw simple badge shape
                fb_w = 60
                fb_h = 36
                fb_y = current_y - fb_h * 0.55
                path = _loyalty_badge_path(badge_x, fb_y, fb_w, fb_h)
                svg_elements.append(
                    f'<path d="{path}" fill="#1a1410"/>'
                )
                cost_text_x = badge_x + fb_w / 2 + 2
                cost_text_y = fb_y + fb_h / 2 + badge_font_size * 0.35
                normalized = cost_str.replace('\u2212', '-')
                display = normalized
                svg_elements.append(
                    f'<text x="{cost_text_x}" y="{cost_text_y}" text-anchor="middle" '
                    f'font-family="{PT_FONT_FAMILY}" font-size="{badge_font_size * 0.8}" '
                    f'font-weight="bold" fill="white">{display}</text>'
                )

            # Render ability text indented past the badge
            ability_x = x_start + text_indent
            ability_w = max_width - text_indent
        else:
            # No loyalty cost — static ability, render full width
            ability_x = x_start
            ability_w = max_width

        # Render the ability text (unbounded height — the caller's fitting
        # loop guarantees the total fits, so no inner truncation either).
        # `avoid` (absolute y) wraps bottom lines around the loyalty shield.
        text_svg, text_h = render_rules_text_svg(
            ability['text'],
            ability_x, current_y,
            ability_w, 100000,
            font_size, line_spacing,
            text_color=text_color,
            avoid=avoid,
        )
        svg_elements.extend(text_svg)

        # Advance by at least the badge visible height or text height
        # The visible shape in the badge is ~65% of badge_h
        badge_visible_h = badge_h * 0.65
        actual_h = max(text_h, badge_visible_h) if ability['cost'] is not None else text_h
        current_y += actual_h + line_spacing * 0.45

    return svg_elements, current_y - y_start


def create_card_frame_svg(card: CardData, frame_settings: dict = None) -> str:
    """Create SVG card frame with layer-based rendering.

    Renders layers in order: border → frame → crown → title_bar → pinlines →
    type_bar → text_box → pt_box → info_bar.
    Text/symbols always render at full opacity on top of their background layers.

    Each style carries render params that control visual dimensions, shapes,
    and textures — producing genuinely different-looking frames per MTG era.

    frame_settings: dict from resolve_frame_settings() with 'layers' dict
    controlling visibility/opacity, and 'render' dict controlling visual params.
    """
    fs = frame_settings or {}

    theme = dict(get_color_theme(card))
    # Apply color overrides
    for key in ('bg', 'field', 'textbox', 'border', 'text'):
        if key in fs.get('color_overrides', {}):
            theme[key] = fs['color_overrides'][key]

    mana_pips = parse_mana_cost(card.mana_cost)
    layers = fs.get('layers', FRAME_STYLES['basic']['layers'])
    render = fs.get('render', dict(DEFAULT_RENDER_PARAMS))

    field = theme['field']
    textbox = theme['textbox']
    border_color = theme['border']
    text_color = theme['text']

    # Showcase (Godzilla) frames: dark cinematic banners with light text over
    # full-bleed art, regardless of the card's colors. The card's own border
    # color is kept as a thin accent.
    showcase = render.get('showcase', False)
    if showcase and not fs.get('color_overrides'):
        field = '#141210'
        textbox = '#17130d'
        text_color = '#f5efe2'

    # Render params
    fr = render.get('field_radius', FIELD_R)
    field_path_fn = _simple_field_path if render.get('field_shape') == 'simple' else _field_path
    use_field_stroke = render.get('field_stroke', True)
    art_m = render.get('art_margin', MARGIN)
    use_bevel = render.get('bevel', False)
    pw = render.get('pinline_width', PINLINE_W)

    # Visibility flags from style-level settings
    show_oracle = fs.get('show_oracle', True)
    show_flavor = fs.get('show_flavor', True)

    fx = art_m  # field x — uses style's art_margin
    fw = VB_W - 2 * art_m  # field width
    rules_inner_w = fw - 2 * RULES_PADDING

    # Layout positions (styles can override e.g. type_y / show_oracle)
    name_y = NAME_Y
    name_bottom = NAME_Y + NAME_H
    type_y = fs.get('type_y', TYPE_Y)
    type_bottom = type_y + TYPE_H
    field_gap = max(pw, 6)  # minimum 6px gap between type bar and text box
    rules_y = type_bottom + field_gap
    rules_bottom = fs.get('rules_bottom', RULES_BOTTOM)

    # Detect legendary for crown rendering
    is_legendary = 'Legendary' in (card.type_line or '')

    # ── Start SVG ──
    svg = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg width="{CARD_WIDTH}" height="{CARD_HEIGHT}" '
        f'viewBox="0 0 {VB_W} {VB_H}" '
        f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
    ]

    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    svg.append(_svg_filters(theme))

    # ── Two-color gradient chrome (multi-type lands & gold cards) ──
    # Blend/split the frame background + title/type/PT boxes between the two
    # colors, left→right — the goal's "left/right color gradients". Honors the
    # frame_gradient setting: 'off'/False disables, 'split' = hard center seam,
    # anything else = smooth blend. Skipped when the user overrode bg/field
    # colors or on showcase (Godzilla) frames.
    grad_theme = theme
    _grad_set = fs.get('frame_gradient', 'auto')
    _gcols = card.colors or card.color_identity or []
    _co = fs.get('color_overrides', {}) or {}
    if (_grad_set not in ('off', False) and len(_gcols) == 2 and not showcase
            and 'field' not in _co and 'bg' not in _co and 'textbox' not in _co):
        g1, g2 = sorted(_gcols, key=lambda c: 'WUBRG'.index(c)
                        if c in 'WUBRG' else 99)
        if g1 in COLOR_THEMES and g2 in COLOR_THEMES:
            _hard = (_grad_set == 'split')

            def _grad_def(gid, c1, c2):
                if _hard:
                    stops = (f'<stop offset="50%" stop-color="{c1}"/>'
                             f'<stop offset="50%" stop-color="{c2}"/>')
                else:
                    stops = (f'<stop offset="0%" stop-color="{c1}"/>'
                             f'<stop offset="100%" stop-color="{c2}"/>')
                # userSpaceOnUse spanning the CARD width: one card-wide
                # left→right gradient shared by every shape, so 'split' seams
                # at the card's center (not each box's own center) and boxes
                # near an edge read as solidly that side's color.
                return (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
                        f'x1="0" y1="0" x2="{VB_W}" y2="0">{stops}</linearGradient>')

            svg.append(
                '<defs>'
                + _grad_def('fdBgGrad', COLOR_THEMES[g1]['bg'], COLOR_THEMES[g2]['bg'])
                + _grad_def('fdFieldGrad', COLOR_THEMES[g1]['field'], COLOR_THEMES[g2]['field'])
                + _grad_def('fdTextboxGrad', COLOR_THEMES[g1]['textbox'], COLOR_THEMES[g2]['textbox'])
                + '</defs>')
            field = 'url(#fdFieldGrad)'  # field only ever fills boxes → gradient them all
            grad_theme = dict(theme)
            grad_theme['bg'] = 'url(#fdBgGrad)'
            # the rules textbox is the largest colored surface — gradient it too
            textbox = 'url(#fdTextboxGrad)'
            grad_theme['textbox'] = textbox

    # Add nyx starfield pattern definition if needed
    if render.get('frame_pattern') == 'nyx':
        svg.append(f'<defs>{_render_nyx_defs()}</defs>')

    # ── Layer: Border ──
    if _layer_visible(layers, 'border'):
        # Colors > Border recolors the border layer (default stays the
        # authentic dark, not the card-color theme border).
        render_b = render
        if 'border' in (fs.get('color_overrides') or {}):
            render_b = dict(render)
            render_b['border_color'] = fs['color_overrides']['border']
        svg.append(_render_border_layer(_layer_opacity(layers, 'border'), render_b))

    # ── Layer: Frame background ──
    if _layer_visible(layers, 'frame'):
        svg.append(_render_frame_bg_layer(grad_theme, _layer_opacity(layers, 'frame'),
                                          type_y, render))

    # ── Inner border around art window (retro style) ──
    if render.get('inner_border') and _layer_visible(layers, 'frame'):
        svg.append(_render_inner_border_layer(grad_theme, _layer_opacity(layers, 'frame'),
                                              type_y, render))


    # ── Layer: Title bar (background + stroke + text rendered separately) ──
    name_path = field_path_fn(fx, name_y, fw, NAME_H, fr)
    title_opacity = _layer_opacity(layers, 'title_bar')
    if _layer_visible(layers, 'title_bar'):
        svg.append(f'<g opacity="{title_opacity}">')
        name_filter = ' filter="url(#fieldShadow)"' if use_field_stroke else ''
        svg.append(f'<path d="{name_path}" fill="{field}"{name_filter}/>')
        svg.append(f'<path d="{name_path}" fill="url(#boxGrad)"/>')
        if use_bevel:
            svg.append(_render_bevel(name_path))
        if use_field_stroke:
            svg.append(f'<path d="{name_path}" fill="none" stroke="{border_color}" stroke-width="2"/>')
        svg.append(f'</g>')

    # Title bar text (always full opacity for readability)
    escaped_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    name_text_x = fx + 16
    pips_right = fx + fw - 12
    pips_total_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 12 if mana_pips else 0
    name_avail_w = fw - 32 - pips_total_w

    if showcase:
        # Two-line showcase title: a BIG display name over the real card name.
        # If no explicit showcase_name, the card's own name becomes the big title.
        big_name = (card.showcase_name or card.name)
        big_esc = big_name.replace('&', '&amp;').replace('<', '&lt;')
        has_sub = bool(card.showcase_name) and card.showcase_name.strip() != card.name.strip()
        big_font = int(NAME_FONT * 1.28)
        big_est_w = len(big_name) * big_font * 0.60
        if big_est_w > name_avail_w and name_avail_w > 0:
            big_font = max(24, int(big_font * name_avail_w / big_est_w))
        if has_sub:
            big_y = name_y + NAME_H * 0.42 + big_font * 0.35
            svg.append(
                f'<text x="{name_text_x}" y="{big_y}" font-family="{NAME_FONT_FAMILY}" '
                f'font-size="{big_font}" font-weight="bold" fill="{text_color}" '
                f'letter-spacing="0.5" stroke="rgba(0,0,0,0.45)" stroke-width="0.6">'
                f'{big_esc}</text>')
            sub_font = max(15, int(NAME_FONT * 0.5))
            sub_y = name_y + NAME_H * 0.82 + sub_font * 0.35
            svg.append(
                f'<text x="{name_text_x + 2}" y="{sub_y}" font-family="{TYPE_FONT_FAMILY}" '
                f'font-size="{sub_font}" font-style="italic" fill="{text_color}" '
                f'opacity="0.82">{escaped_name}</text>')
        else:
            big_y = name_y + NAME_H / 2 + big_font * 0.35
            svg.append(
                f'<text x="{name_text_x}" y="{big_y}" font-family="{NAME_FONT_FAMILY}" '
                f'font-size="{big_font}" font-weight="bold" fill="{text_color}" '
                f'letter-spacing="0.5" stroke="rgba(0,0,0,0.45)" stroke-width="0.6">'
                f'{big_esc}</text>')
    else:
        name_est_w = len(card.name) * NAME_FONT * 0.62
        actual_name_font = NAME_FONT
        if name_est_w > name_avail_w and name_avail_w > 0:
            actual_name_font = max(22, int(NAME_FONT * name_avail_w / name_est_w))
        name_text_y = name_y + NAME_H / 2 + actual_name_font * 0.35
        svg.append(
            f'<text x="{name_text_x}" y="{name_text_y}" font-family="{NAME_FONT_FAMILY}" '
            f'font-size="{actual_name_font}" font-weight="bold" fill="{text_color}" '
            f'stroke="rgba(255,255,255,0.4)" stroke-width="0.3">'
            f'{escaped_name}</text>'
        )

    # Mana pips (right-aligned in name bar, always full opacity)
    if mana_pips:
        pip_cy = name_y + NAME_H / 2
        px = pips_right
        for pip in reversed(mana_pips):
            pip_x = px - MANA_PIP_SIZE
            pip_y = pip_cy - MANA_PIP_SIZE / 2
            svg.append(
                f'<circle cx="{pip_x + MANA_PIP_SIZE/2 + 0.5}" cy="{pip_y + MANA_PIP_SIZE/2 + 1}" '
                f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>'
            )
            svg.append(_pip_image_tag(pip, pip_x, pip_y, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # ── Layer: Pinlines ──
    if _layer_visible(layers, 'pinlines') and pw > 0:
        svg.append(_render_pinlines_layer(theme, _layer_opacity(layers, 'pinlines'),
                                          type_y, TYPE_H, render))

    # ── Layer: Type bar (background + stroke + text rendered separately) ──
    type_path = field_path_fn(fx, type_y, fw, TYPE_H, fr)
    type_opacity = _layer_opacity(layers, 'type_bar')
    if _layer_visible(layers, 'type_bar'):
        svg.append(f'<g opacity="{type_opacity}">')
        field_filter = ' filter="url(#fieldShadow)"' if use_field_stroke else ''
        svg.append(f'<path d="{type_path}" fill="{field}"{field_filter}/>')
        svg.append(f'<path d="{type_path}" fill="url(#boxGrad)"/>')
        if use_bevel:
            svg.append(_render_bevel(type_path))
        if use_field_stroke:
            svg.append(f'<path d="{type_path}" fill="none" stroke="{border_color}" stroke-width="2"/>')
        svg.append(f'</g>')

    # Type bar text (always full opacity)
    escaped_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_text_y = type_y + TYPE_H / 2 + TYPE_FONT * 0.35
    svg.append(
        f'<text x="{fx + 16}" y="{type_text_y}" font-family="{TYPE_FONT_FAMILY}" '
        f'font-size="{TYPE_FONT}" fill="{text_color}">'
        f'{escaped_type}</text>'
    )

    # ── Layer: Text box (background + stroke + text rendered separately) ──
    rules_h = rules_bottom - rules_y
    text_box_opacity = _layer_opacity(layers, 'text_box')
    tb_r = fr if render.get('textbox_style') != 'squared' else max(fr - 4, 3)
    if _layer_visible(layers, 'text_box') and rules_h > 0:
        rules_path = field_path_fn(fx, rules_y, fw, rules_h, tb_r)
        svg.append(f'<g opacity="{text_box_opacity}">')
        svg.append(f'<path d="{rules_path}" fill="{textbox}"/>')
        svg.append(f'<path d="{rules_path}" fill="url(#boxGrad)"/>')
        if use_bevel:
            svg.append(_render_bevel(rules_path, 0.2))
        if use_field_stroke:
            svg.append(f'<path d="{rules_path}" fill="none" stroke="{border_color}" stroke-width="2"/>')
        svg.append(f'</g>')

    rules_max_h = max(rules_h - RULES_PADDING * 2, 0) if rules_h > 0 else 0

    # Use planeswalker-specific renderer for loyalty abilities
    is_planeswalker = _is_planeswalker(card)
    if show_oracle and rules_max_h > 0 and is_planeswalker:
        # Smaller font & tighter line spacing for planeswalkers to fit loyalty
        # badges + longer ability text. Fitting loop: shrink until every
        # ability renders inside the box (abilities must never truncate).
        _sx, _sy = CARD_WIDTH / VB_W, CARD_HEIGHT / VB_H
        fs['_pw_rect'] = ((fx + RULES_PADDING) * _sx, (rules_y + RULES_PADDING) * _sy,
                          (fx + RULES_PADDING + rules_inner_w) * _sx,
                          (rules_y + RULES_PADDING + rules_max_h) * _sy)
        # Loyalty shield geometry (mirrors the shield block below) so the
        # ability text can wrap AROUND the shield instead of being occluded
        # by it — same avoid mechanism as _render_pw_content_svg.
        _shield_avoid_top = _shield_left = None
        if card.loyalty:
            _g = _LOYALTY_SHIELD_GEOM
            _lsize = 102
            _limit_vb = 1014 * VB_H / CARD_HEIGHT
            _lcx = (VB_W - art_m) + 8 - _lsize * (1 - _g['cx'])
            _lcy = min(rules_bottom + 8,
                       _limit_vb - (_lsize * _g['aspect']) * (1 - _g['cy']))
            _shield_avoid_top = _lcy - _g['cy'] * _lsize * _g['aspect'] - 4
            _shield_left = _lcx - _g['cx'] * _lsize
        pw_font = int(RULES_FONT * 0.82)
        while True:
            pw_line_h = int(RULES_LINE_H * pw_font / RULES_FONT)
            # first badge's plate extends above its line center — start low
            # enough that it stays inside the textbox (same math as
            # _render_pw_content_svg)
            _badge_above = _BADGE_GEOM['planeswalkerPlus']['cy'] * \
                _BADGE_GEOM['planeswalkerPlus']['aspect'] * (pw_font * 3.48)
            _start_off = max(pw_font * 0.8, _badge_above + pw_font * 0.32 + 3)
            rules_text_y_start = rules_y + RULES_PADDING + _start_off
            _avoid = None
            if _shield_avoid_top is not None:
                _text_x = (fx + RULES_PADDING) + pw_font * 3.48 + round(pw_font * 0.52)
                _avoid = (_shield_avoid_top,
                          max(60.0, _shield_left - 8 - _text_x))
            rules_svg, rules_used_h = render_planeswalker_abilities(
                card.oracle_text or "",
                fx + RULES_PADDING, rules_text_y_start,
                rules_inner_w, rules_max_h,
                pw_font, pw_line_h,
                text_color=text_color,
                theme=theme,
                avoid=_avoid,
            )
            if _start_off + rules_used_h <= rules_max_h or pw_font <= RULES_FONT_FLOOR:
                break
            pw_font -= 1
        if _start_off + rules_used_h > rules_max_h + 2:  # only at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: planeswalker needs {_start_off + rules_used_h:.0f}px '
                f'but box is {rules_max_h:.0f}px (font {pw_font})')
    elif show_oracle and rules_max_h > 0 and card.split_faces:
        # Adventure / split / room: two-column rules area
        rules_svg = _render_split_rules_svg(
            card, fs, fx + RULES_PADDING, rules_y + RULES_PADDING,
            rules_inner_w, rules_max_h, text_color,
            int(fs.get('rules_font_size') or RULES_FONT))
        rules_used_h = rules_max_h
        rules_text_y_start = rules_y + RULES_PADDING
    elif show_oracle and rules_max_h > 0:
        oracle = card.oracle_text or ""
        desired = int(fs.get('rules_font_size') or RULES_FONT)
        # Account for flavor text height if present
        flavor_reserve = 0
        if card.flavor_text and card.flavor_text.strip():
            flavor_reserve = _measure_rules_text(
                card.flavor_text.strip(), rules_inner_w, FLAVOR_FONT, FLAVOR_LINE_H
            ) + FLAVOR_LINE_H  # extra for divider gap

        # Rules Text Size is a CEILING: find the max font that fits so text
        # always renders completely and can never overflow the box.
        def _msr(f):
            return _measure_rules_text(oracle, rules_inner_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT)) + flavor_reserve

        r_font = _max_fitting_rules_font(_msr, rules_max_h, desired)
        r_line_h = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rules_max_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rules_max_h:.0f}px (font {r_font})')

        rules_text_y_start = rules_y + RULES_PADDING + r_font * 0.8
        rules_svg, rules_used_h = render_rules_text_svg(
            oracle,
            fx + RULES_PADDING, rules_text_y_start,
            rules_inner_w, rules_max_h,
            r_font, r_line_h,
            text_color=text_color
        )
    else:
        rules_svg = []
        rules_used_h = 0
        rules_text_y_start = rules_y + RULES_PADDING
    svg.extend(rules_svg)

    # ── Flavor text (italic, quoted, BOTTOM-ALIGNED in textbox) ──
    # (skipped for split-text cards — both columns own the full box height)
    has_pt = card.power is not None and card.toughness is not None
    if show_flavor and card.flavor_text and rules_h > 0 and not card.split_faces:
        ft = card.flavor_text.strip()
        if not ft.startswith('"') and not ft.startswith('\u201c'):
            ft = f'\u201c{ft}\u201d'  # wrap in curly quotes

        # Pre-measure flavor text height so we can bottom-align it
        flavor_h = _measure_rules_text(ft, rules_inner_w, FLAVOR_FONT, FLAVOR_LINE_H)
        # Bottom of textbox minus padding, flavor grows upward from there
        flavor_bottom = rules_y + RULES_H - RULES_BOTTOM_PAD
        flavor_y_start = flavor_bottom - flavor_h + FLAVOR_FONT * 0.8

        # Skip flavor text if it would collide with the P/T box
        render_flavor = True
        if has_pt and flavor_h > 0:
            pt_x = VB_W - art_m - PT_W - 6
            pt_y = rules_bottom - PT_H + 12
            # Check each wrapped line — if any line in the P/T overlap zone
            # is wide enough to reach the P/T box, skip flavor entirely
            for paragraph in ft.split('\n'):
                if not paragraph.strip():
                    continue
                tokens = tokenize_oracle_text(paragraph)
                lines = _wrap_paragraph_with_pips(tokens, rules_inner_w, FLAVOR_FONT)
                for line_idx, line_items in enumerate(lines):
                    line_y = flavor_y_start + line_idx * FLAVOR_LINE_H
                    if line_y < pt_y or line_y > pt_y + PT_H:
                        continue
                    # Line overlaps P/T vertically — measure its width
                    line_w = sum(
                        _measure_text(seg['value'], FLAVOR_FONT) if seg['type'] == 'text'
                        else seg.get('width', _rules_pip_size(FLAVOR_FONT) + 2)
                        for seg in line_items
                    )
                    if fx + RULES_PADDING + line_w > pt_x:
                        render_flavor = False
                        break
                if not render_flavor:
                    break

        if render_flavor and flavor_h > 0:
            # Subtle translucent divider between rules and flavor text
            sep_y = flavor_y_start - FLAVOR_FONT * 0.8 - 6
            # Only draw if there's enough space between rules text and flavor
            if sep_y > rules_text_y_start + rules_used_h + 4:
                sep_x1 = fx + RULES_PADDING + 40
                sep_x2 = fx + fw - RULES_PADDING - 40
                svg.append(
                    f'<line x1="{sep_x1}" y1="{sep_y}" x2="{sep_x2}" y2="{sep_y}" '
                    f'stroke="{text_color}" stroke-width="0.6" opacity="0.2"/>'
                )

            # Render flavor text as italic, bottom-aligned
            flavor_svg, _ = render_rules_text_svg(
                ft,
                fx + RULES_PADDING, flavor_y_start,
                rules_inner_w, flavor_h + FLAVOR_LINE_H,
                FLAVOR_FONT, FLAVOR_LINE_H,
                text_color=text_color,
                font_family=FLAVOR_FONT_FAMILY,
                italic=True
            )
            svg.append(f'<g opacity="0.85">')
            svg.extend(flavor_svg)
            svg.append('</g>')

    # ── Layer: P/T box (background + text rendered separately) ──
    if card.power is not None and card.toughness is not None:
        pt_x = VB_W - art_m - PT_W - 6
        pt_y = rules_bottom - PT_H + 12
        pt_shape = render.get('pt_shape', 'pentagon')
        pt_path_fn = _pt_box_path_pointed if pt_shape == 'pointed' else _pt_box_path
        pt_path = pt_path_fn(pt_x, pt_y, PT_W, PT_H)
        pt_shadow = pt_path_fn(pt_x + 1.5, pt_y + 1.5, PT_W, PT_H)
        pt_opacity = _layer_opacity(layers, 'pt_box')
        if _layer_visible(layers, 'pt_box'):
            svg.append(f'<path d="{pt_shadow}" fill="rgba(0,0,0,0.35)"/>')
            svg.append(f'<g opacity="{pt_opacity}">')
            svg.append(f'<path d="{pt_path}" fill="{field}" filter="url(#ptShadow)"/>')
            svg.append(f'<path d="{pt_path}" fill="url(#boxGrad)"/>')
            if use_bevel:
                svg.append(_render_bevel(pt_path, 0.25))
            svg.append(f'</g>')
            svg.append(f'<path d="{pt_path}" fill="none" stroke="{border_color}" stroke-width="2.5"/>')
            pt_cx = pt_x + PT_W / 2
            pt_cy = pt_y + PT_H / 2 + PT_FONT * 0.35
            svg.append(
                f'<text x="{pt_cx}" y="{pt_cy}" text-anchor="middle" '
                f'font-family="{PT_FONT_FAMILY}" font-size="{PT_FONT}" font-weight="bold" '
                f'fill="{text_color}">{card.power}/{card.toughness}</text>'
            )

    # ── Starting Loyalty counter — authentic cardconjurer shield sprite,
    # anchored straddling the textbox's bottom-right corner (like a P/T box).
    # Same helper/geometry as the image styles so the number is always
    # plate-centered; vector fallback lives inside the helper. ──
    if card.loyalty:
        loy_size = 102  # 114px (pw frame plate width) in VB units
        g = _LOYALTY_SHIELD_GEOM
        loy_cx = (VB_W - art_m) + 8 - loy_size * (1 - g['cx'])
        # center ON the textbox bottom edge (straddle, like the pw frame's
        # baked plate), print-safe capped (1014px scaled to VB units)
        _limit_vb = 1014 * VB_H / CARD_HEIGHT
        loy_cy = min(rules_bottom + 8,
                     _limit_vb - (loy_size * g['aspect']) * (1 - g['cy']))
        _sx, _sy = CARD_WIDTH / VB_W, CARD_HEIGHT / VB_H
        fs['_pw_shield_bbox'] = ((loy_cx - g['cx'] * loy_size) * _sx,
                                 (loy_cy - g['cy'] * loy_size * g['aspect']) * _sy,
                                 (loy_cx + (1 - g['cx']) * loy_size) * _sx,
                                 (loy_cy + (1 - g['cy']) * loy_size * g['aspect']) * _sy)
        svg.extend(_start_loyalty_badge_svg(card.loyalty, loy_cx, loy_cy,
                                            size=loy_size))

    # ── Layer: Info bar ──
    if _layer_visible(layers, 'info_bar'):
        svg.append(_render_info_bar_layer(_layer_opacity(layers, 'info_bar'),
                                          render))

    svg.append('</svg>')
    return '\n'.join(svg)


# ===========================================================================
# Render and composite
# ===========================================================================
def render_card_frame(card_dict: dict, output_path, deck_frame_settings: dict = None) -> None:
    """Render a card frame as a transparent PNG overlay."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fs = resolve_frame_settings(card_dict, deck_frame_settings)
    card = _build_card_data(card_dict, fs)
    png_data = _render_frame_png(card, fs)

    if png_data:
        with open(output_path, 'wb') as f:
            f.write(png_data)


def _autocrop_dark_borders(img, threshold=5, max_scan=8):
    """Crop near-black borders from edges of AI-generated art.

    AI models sometimes produce thin black borders. Scans inward from each
    edge and trims contiguous rows/columns with avg brightness < threshold.
    Conservative: only trims truly black rows (threshold=5) and scans at
    most 8 pixels deep to avoid eating into intentionally dark scenes.
    Won't crop more than 3% of either dimension.
    """
    import numpy as np
    arr = np.array(img)
    h, w = arr.shape[:2]
    brightness = arr.mean(axis=2)  # per-pixel avg across RGB

    max_crop_v = h // 33
    max_crop_h = w // 33

    # Top
    top = 0
    for row in range(min(max_scan, max_crop_v)):
        if brightness[row].mean() < threshold:
            top = row + 1
        else:
            break

    # Bottom
    bottom = h
    for row in range(h - 1, max(h - max_scan, h - max_crop_v) - 1, -1):
        if brightness[row].mean() < threshold:
            bottom = row
        else:
            break

    # Left
    left = 0
    for col in range(min(max_scan, max_crop_h)):
        if brightness[:, col].mean() < threshold:
            left = col + 1
        else:
            break

    # Right
    right = w
    for col in range(w - 1, max(w - max_scan, w - max_crop_h) - 1, -1):
        if brightness[:, col].mean() < threshold:
            right = col
        else:
            break

    if top or left or bottom < h or right < w:
        return img.crop((left, top, right, bottom))
    return img


def _cover_crop(img, target_w, target_h):
    """Scale image to cover target dimensions, then center-crop.

    Like CSS object-fit: cover — no gaps, no distortion, excess is cropped.
    """
    scale = max(target_w / img.width, target_h / img.height)
    scaled_w = round(img.width * scale)
    scaled_h = round(img.height * scale)
    img = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)

    # Center-crop to exact target size
    cx = (scaled_w - target_w) // 2
    cy = (scaled_h - target_h) // 2
    return img.crop((cx, cy, cx + target_w, cy + target_h))


def _cover_crop_with_offset(img, target_w, target_h, offset_x=0, offset_y=0, zoom=1.0):
    """Scale image with user pan/zoom, place on target canvas.

    Mirrors the Frame Designer canvas EXACTLY (dx = (W - sw)/2 + offset):
    the scaled art is pasted at the precise pan position over a dark
    background, with NO clamping. If the user drags the art past the cover
    boundary in the editor, the final composite shows the same dark gap the
    WYSIWYG preview showed — the editor is the single source of truth.
    (The old version clamped the crop to keep art covering the card, which
    made saved positions silently snap back in the composite.)

    offset_x, offset_y: pixel offsets at the target resolution (+ = right/down)
    zoom: multiplier on top of the base cover-fit scale (1.0 = no extra zoom)
    """
    base_scale = max(target_w / img.width, target_h / img.height)
    scale = base_scale * max(zoom, 0.3)  # clamp zoom floor
    scaled_w = round(img.width * scale)
    scaled_h = round(img.height * scale)
    img = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)

    # Same math as FrameCompositor.render(): center, then apply user offset.
    canvas = Image.new('RGB', (target_w, target_h), (10, 10, 10))
    dx = round((target_w - scaled_w) / 2 + offset_x)
    dy = round((target_h - scaled_h) / 2 + offset_y)
    canvas.paste(img, (dx, dy))
    return canvas


def _build_card_data(card_dict: dict, frame_settings: dict = None) -> CardData:
    """Build CardData from card_dict, applying text_overrides from frame_settings."""
    text_ovr = (frame_settings or {}).get('text_overrides', {})

    # Single-art multi-part layouts (adventure, split/room) carry both halves
    # so the rules box can render two columns.
    layout = card_dict.get('layout') or 'normal'
    faces = card_dict.get('card_faces') or []
    split_faces = faces if layout in ('adventure', 'split') and len(faces) >= 2 else None

    name = text_ovr.get('name') or card_dict.get('name', 'Unknown')
    mana_cost = text_ovr.get('mana_cost') if 'mana_cost' in text_ovr else card_dict.get('mana_cost', '')
    if split_faces:
        if layout == 'adventure' and not text_ovr.get('name'):
            # Real adventure cards title the creature half only
            name = split_faces[0].get('name') or name
        if layout == 'split' and 'mana_cost' not in text_ovr:
            # Each half's cost renders in its column header; a single cost in
            # the title bar would be misleading
            mana_cost = ''

    return CardData(
        name=name,
        mana_cost=mana_cost,
        layout=layout,
        split_faces=split_faces,
        defense=card_dict.get('defense'),
        type_line=text_ovr.get('type_line') or card_dict.get('type_line', ''),
        oracle_text=text_ovr.get('oracle_text') if 'oracle_text' in text_ovr else card_dict.get('oracle_text', ''),
        power=text_ovr.get('power') if 'power' in text_ovr else card_dict.get('power'),
        toughness=text_ovr.get('toughness') if 'toughness' in text_ovr else card_dict.get('toughness'),
        loyalty=card_dict.get('loyalty'),
        colors=card_dict.get('colors', []),
        color_identity=card_dict.get('color_identity', []),
        flavor_text=card_dict.get('flavor_text'),
        is_commander=card_dict.get('is_commander', False),
        card_type=card_dict.get('card_type', 'creature'),
        showcase_name=text_ovr.get('showcase_name') or card_dict.get('showcase_name'),
    )


def _render_frame_png(card: CardData, frame_settings: dict = None):
    """Render card frame SVG to PNG bytes. Returns PNG bytes or None for frameless."""
    fs = frame_settings or {}
    if fs.get('no_frame'):
        return None

    svg_content = create_card_frame_svg(card, frame_settings=fs)
    return cairosvg.svg2png(
        bytestring=svg_content.encode('utf-8'),
        output_width=CARD_WIDTH,
        output_height=CARD_HEIGHT,
    )


# ===========================================================================
# Image-based frame rendering — uses pre-rendered PNG frame overlays
# ===========================================================================
FRAMES_DIR = Path(__file__).parent / "shared" / "frames"

# M15 layout positions (viewBox coordinates matching the CardConjurer frame PNG)
# These positions align text with the pre-rendered M15 frame fields.
# Measured from actual M15 frame PNGs (750×1050 → 672×936 viewBox).
# Pixel-to-SVG: svg = pixel * 936/1050
M15_LAYOUT = {
    'name_y': 47,         # title bar inner field: pixel 53→107, SVG 47→95
    'name_h': 48,         # title bar inner height: 54px → SVG 48
    'type_y': 522,        # type bar inner field: pixel 585→647, SVG 522→577
    'type_h': 55,         # type bar inner height: 62px → SVG 55
    'rules_y': 582,       # text box inner field: pixel 653→973, SVG 582→867
    'rules_bottom': 867,  # text box inner bottom
    'art_margin': 52,     # side border: pixel 58 → SVG 52
    'pt_w': 80,           # P/T collision area width (SVG)
    'pt_h': 50,           # P/T collision area height (SVG)
    'pt_font': 22,        # smaller font for scaled-down PT box
    # P/T box visual interior center (accounts for drop shadow + bevel)
    # PT PNG 282×154 scaled 0.42× → 118×64, placed at pixel (600, 928)
    # Shadow on bottom-right shifts visual center left+up from PNG center
    'pt_center_x': 591,   # visual interior center x (SVG) — measured from composite
    'pt_center_y': 851,   # visual interior center y (SVG)
}


def _determine_color_key(card_dict: dict) -> str:
    """Map card color_identity to frame asset color key (w/u/b/r/g/m/a/c/l)."""
    type_line = card_dict.get('type_line', '')
    colors = card_dict.get('color_identity', []) or card_dict.get('colors', [])

    # Artifact without color identity
    if 'Artifact' in type_line and not colors:
        return 'a'
    # Land without color identity
    if 'Land' in type_line and not colors:
        return 'l'
    # Colorless
    if not colors:
        return 'c'
    # Multi-color
    if len(colors) >= 2:
        return 'm'
    # Single color
    color_map = {'W': 'w', 'U': 'u', 'B': 'b', 'R': 'r', 'G': 'g'}
    return color_map.get(colors[0], 'c')


def _load_frame_image(frame_set: str, component: str) -> Optional[Image.Image]:
    """Load a pre-rendered frame PNG from shared/frames/{frame_set}/{component}.png"""
    path = FRAMES_DIR / frame_set / f'{component}.png'
    if path.exists():
        return Image.open(path).convert('RGBA')
    return None


# --- Two-color gradient frames (multi-type lands & gold cards) --------------
_WUBRG = ['W', 'U', 'B', 'R', 'G']
_COLOR_TO_KEY = {'W': 'w', 'U': 'u', 'B': 'b', 'R': 'r', 'G': 'g'}


def _two_color_keys(card_dict: dict) -> Optional[tuple]:
    """If the card is exactly two colors, return the (left, right) frame keys in
    WUBRG order (e.g. a W/U card -> ('w', 'u')); else None."""
    colors = card_dict.get('color_identity', []) or card_dict.get('colors', [])
    ordered = [c for c in _WUBRG if c in colors]
    if len(ordered) == 2:
        return _COLOR_TO_KEY[ordered[0]], _COLOR_TO_KEY[ordered[1]]
    return None


def _gradient_mode(card_dict: dict, fs: dict) -> Optional[str]:
    """Decide whether/how to render a two-color gradient frame.

    Returns 'gradient' (smooth left->right blend), 'split' (hard vertical seam),
    or None (fall back to the flat gold 'm' frame). Controlled by the frame
    setting `frame_gradient`: 'auto'/True (default) -> smooth for any 2-color
    card, 'gradient'/'split' -> forced, 'off'/False -> disabled.
    """
    setting = fs.get('frame_gradient', 'auto')
    if setting in (None, False, 'off'):
        return None
    if _two_color_keys(card_dict) is None:
        return None
    if setting in ('gradient', 'split'):
        return setting
    return 'gradient'  # 'auto' / True


def _horizontal_blend_mask(w: int, h: int, band_frac: float) -> Image.Image:
    """L-mode mask: 0 on the left (first image) ramping to 255 on the right
    (second image), with a smooth transition band `band_frac` of the width
    centered on the midline. Small band_frac ≈ a hard split."""
    band = max(1, int(round(w * band_frac)))
    start = w // 2 - band // 2
    row = Image.new('L', (w, 1))
    px = row.load()
    for x in range(w):
        if x <= start:
            v = 0
        elif x >= start + band:
            v = 255
        else:
            v = int(round((x - start) / band * 255))
        px[x, 0] = v
    return row.resize((w, h))


def _gradient_frame_image(frame_set: str, key1: str, key2: str,
                          subdir: str = '', blend: str = 'gradient') -> Optional[Image.Image]:
    """Composite two single-color frame PNGs into one left(key1)->right(key2)
    frame. `subdir` targets a sub-component set (e.g. 'pt/' for the P/T box)."""
    img1 = _load_frame_image(frame_set, f'{subdir}{key1}')
    img2 = _load_frame_image(frame_set, f'{subdir}{key2}')
    if img1 is None or img2 is None:
        return None
    if img2.size != img1.size:
        img2 = img2.resize(img1.size, Image.Resampling.LANCZOS)
    band_frac = 0.44 if blend == 'gradient' else 0.015
    mask = _horizontal_blend_mask(img1.width, img1.height, band_frac)
    # mask=255 -> img2 (right), mask=0 -> img1 (left)
    return Image.composite(img2, img1, mask)



def _create_text_only_svg(card: CardData, fs: dict) -> str:
    """Create SVG with ONLY text elements — no backgrounds, borders, or fills.

    Used for image-based frame mode where a pre-rendered PNG provides
    the frame chrome and this SVG provides the text overlay.
    """
    layout = fs.get('layout') or M15_LAYOUT
    theme = dict(get_color_theme(card))
    # Colors > Text override applies to all card text on image frames
    text_color = (fs.get('color_overrides', {}) or {}).get('text') or theme['text']
    mana_pips = parse_mana_cost(card.mana_cost)

    # Layout positions from M15 layout
    name_y = layout.get('name_y', M15_LAYOUT['name_y'])
    name_h = layout.get('name_h', M15_LAYOUT['name_h'])
    type_y = layout.get('type_y', M15_LAYOUT['type_y'])
    type_h = layout.get('type_h', M15_LAYOUT['type_h'])
    rules_y = layout.get('rules_y', M15_LAYOUT['rules_y'])
    rules_bottom = layout.get('rules_bottom', M15_LAYOUT['rules_bottom'])
    art_m = layout.get('art_margin', M15_LAYOUT['art_margin'])
    pt_w = layout.get('pt_w', M15_LAYOUT['pt_w'])
    pt_h = layout.get('pt_h', M15_LAYOUT['pt_h'])

    fw = VB_W - 2 * art_m  # field width
    rules_inner_w = fw - 2 * RULES_PADDING

    svg = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg width="{CARD_WIDTH}" height="{CARD_HEIGHT}" '
        f'viewBox="0 0 {VB_W} {VB_H}" '
        f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
    ]

    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    # ── Name text ──
    escaped_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    name_text_x = art_m + 16
    pips_right = art_m + fw - 12
    pips_total_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 12 if mana_pips else 0
    name_avail_w = fw - 32 - pips_total_w
    name_est_w = len(card.name) * NAME_FONT * 0.62
    actual_name_font = NAME_FONT
    if name_est_w > name_avail_w and name_avail_w > 0:
        actual_name_font = max(22, int(NAME_FONT * name_avail_w / name_est_w))
    name_text_y = name_y + name_h / 2 + actual_name_font * 0.35
    svg.append(
        f'<text x="{name_text_x}" y="{name_text_y}" font-family="{NAME_FONT_FAMILY}" '
        f'font-size="{actual_name_font}" font-weight="bold" fill="{text_color}">'
        f'{escaped_name}</text>'
    )

    # ── Mana pips ──
    if mana_pips:
        pip_cy = name_y + name_h / 2
        px = pips_right
        for pip in reversed(mana_pips):
            pip_x = px - MANA_PIP_SIZE
            pip_y = pip_cy - MANA_PIP_SIZE / 2
            svg.append(
                f'<circle cx="{pip_x + MANA_PIP_SIZE/2 + 0.5}" cy="{pip_y + MANA_PIP_SIZE/2 + 1}" '
                f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>'
            )
            svg.append(_pip_image_tag(pip, pip_x, pip_y, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # ── Type text ──
    escaped_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_text_y = type_y + type_h / 2 + TYPE_FONT * 0.35
    svg.append(
        f'<text x="{art_m + 16}" y="{type_text_y}" font-family="{TYPE_FONT_FAMILY}" '
        f'font-size="{TYPE_FONT}" fill="{text_color}">'
        f'{escaped_type}</text>'
    )

    # ── Oracle text ──
    # For creatures with P/T, reduce text area so it doesn't overlap the P/T box
    has_pt = card.power is not None and card.toughness is not None
    effective_rules_bottom = rules_bottom
    if has_pt and 'pt_center_y' in layout:
        # P/T box top edge in SVG coords — leave margin above it
        effective_rules_bottom = layout['pt_center_y'] - pt_h / 2 - 4
    rules_h = effective_rules_bottom - rules_y
    rules_max_h = max(rules_h - RULES_PADDING * 2, 0)
    show_oracle = fs.get('show_oracle', True)
    show_flavor = fs.get('show_flavor', True)

    is_planeswalker = _is_planeswalker(card)
    if show_oracle and rules_max_h > 0 and is_planeswalker:
        # Fitting loop: shrink until every ability renders inside the box
        # (abilities must never truncate).
        pw_font = int(RULES_FONT * 0.82)
        while True:
            pw_line_h = int(RULES_LINE_H * pw_font / RULES_FONT)
            _badge_above = _BADGE_GEOM['planeswalkerPlus']['cy'] * \
                _BADGE_GEOM['planeswalkerPlus']['aspect'] * (pw_font * 3.48)
            _start_off = max(pw_font * 0.8, _badge_above + pw_font * 0.32 + 3)
            rules_text_y_start = rules_y + RULES_PADDING + _start_off
            rules_svg, rules_used_h = render_planeswalker_abilities(
                card.oracle_text or "",
                art_m + RULES_PADDING, rules_text_y_start,
                rules_inner_w, rules_max_h,
                pw_font, pw_line_h,
                text_color=text_color,
                theme=theme,
            )
            if _start_off + rules_used_h <= rules_max_h or pw_font <= RULES_FONT_FLOOR:
                break
            pw_font -= 1
        if _start_off + rules_used_h > rules_max_h + 2:  # only at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: planeswalker needs {_start_off + rules_used_h:.0f}px '
                f'but box is {rules_max_h:.0f}px (font {pw_font})')
    elif show_oracle and rules_max_h > 0 and card.split_faces:
        # Adventure / split / room: two-column rules area
        rules_svg = _render_split_rules_svg(
            card, fs, art_m + RULES_PADDING, rules_y + RULES_PADDING,
            rules_inner_w, rules_max_h, text_color,
            int(fs.get('rules_font_size') or RULES_FONT))
        rules_used_h = rules_max_h
        rules_text_y_start = rules_y + RULES_PADDING
    elif show_oracle and rules_max_h > 0:
        oracle = card.oracle_text or ""
        desired = int(fs.get('rules_font_size') or RULES_FONT)
        flavor_reserve = 0
        if card.flavor_text and card.flavor_text.strip():
            flavor_reserve = _measure_rules_text(
                card.flavor_text.strip(), rules_inner_w, FLAVOR_FONT, FLAVOR_LINE_H
            ) + FLAVOR_LINE_H

        # Rules Text Size is a CEILING: find the max font that fits so text
        # always renders completely and can never overflow the box.
        def _msr(f):
            return _measure_rules_text(oracle, rules_inner_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT)) + flavor_reserve

        r_font = _max_fitting_rules_font(_msr, rules_max_h, desired)
        r_line_h = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rules_max_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rules_max_h:.0f}px (font {r_font})')

        rules_text_y_start = rules_y + RULES_PADDING + r_font * 0.8
        rules_svg, rules_used_h = render_rules_text_svg(
            oracle,
            art_m + RULES_PADDING, rules_text_y_start,
            rules_inner_w, rules_max_h,
            r_font, r_line_h,
            text_color=text_color
        )
    else:
        rules_svg = []
        rules_used_h = 0
        rules_text_y_start = rules_y + RULES_PADDING
    svg.extend(rules_svg)

    # ── Flavor text (skipped for split-text cards) ──
    if show_flavor and card.flavor_text and rules_h > 0 and not card.split_faces:
        ft = card.flavor_text.strip()
        if not ft.startswith('"') and not ft.startswith('\u201c'):
            ft = f'\u201c{ft}\u201d'

        flavor_h = _measure_rules_text(ft, rules_inner_w, FLAVOR_FONT, FLAVOR_LINE_H)
        flavor_bottom = rules_y + rules_h - RULES_BOTTOM_PAD
        flavor_y_start = flavor_bottom - flavor_h + FLAVOR_FONT * 0.8

        render_flavor = True
        if has_pt and flavor_h > 0:
            if 'pt_center_x' in layout:
                pt_x = layout['pt_center_x'] - pt_w / 2
                pt_y_check = layout['pt_center_y'] - pt_h / 2
            else:
                pt_x = VB_W - art_m - pt_w - 6
                pt_y_check = rules_bottom - pt_h + 12
            for paragraph in ft.split('\n'):
                if not paragraph.strip():
                    continue
                tokens = tokenize_oracle_text(paragraph)
                lines = _wrap_paragraph_with_pips(tokens, rules_inner_w, FLAVOR_FONT)
                for line_idx, line_items in enumerate(lines):
                    line_y = flavor_y_start + line_idx * FLAVOR_LINE_H
                    if line_y < pt_y_check or line_y > pt_y_check + pt_h:
                        continue
                    line_w = sum(
                        _measure_text(seg['value'], FLAVOR_FONT) if seg['type'] == 'text'
                        else seg.get('width', _rules_pip_size(FLAVOR_FONT) + 2)
                        for seg in line_items
                    )
                    if art_m + RULES_PADDING + line_w > pt_x:
                        render_flavor = False
                        break
                if not render_flavor:
                    break

        if render_flavor and flavor_h > 0:
            sep_y = flavor_y_start - FLAVOR_FONT * 0.8 - 6
            if sep_y > rules_text_y_start + rules_used_h + 4:
                sep_x1 = art_m + RULES_PADDING + 40
                sep_x2 = art_m + fw - RULES_PADDING - 40
                svg.append(
                    f'<line x1="{sep_x1}" y1="{sep_y}" x2="{sep_x2}" y2="{sep_y}" '
                    f'stroke="{text_color}" stroke-width="0.6" opacity="0.2"/>'
                )
            flavor_svg, _ = render_rules_text_svg(
                ft,
                art_m + RULES_PADDING, flavor_y_start,
                rules_inner_w, flavor_h + FLAVOR_LINE_H,
                FLAVOR_FONT, FLAVOR_LINE_H,
                text_color=text_color,
                font_family=FLAVOR_FONT_FAMILY,
                italic=True
            )
            svg.append('<g opacity="0.85">')
            svg.extend(flavor_svg)
            svg.append('</g>')

    # ── P/T text (no box — the frame PNG or PT overlay provides the box) ──
    if has_pt:
        pt_font = layout.get('pt_font', PT_FONT)
        # Prefer dynamically computed center from actual PT box bbox
        if '_pt_center_x_svg' in fs:
            pt_cx = fs['_pt_center_x_svg']
            # Manual baseline offset (CairoSVG doesn't support dominant-baseline)
            pt_cy = fs['_pt_center_y_svg'] + pt_font * 0.35
            svg.append(
                f'<text x="{pt_cx}" y="{pt_cy}" text-anchor="middle" '
                f'font-family="{PT_FONT_FAMILY}" font-size="{pt_font}" font-weight="bold" '
                f'fill="{text_color}">{card.power}/{card.toughness}</text>'
            )
        elif 'pt_center_x' in layout:
            pt_cx = layout['pt_center_x']
            pt_cy = layout['pt_center_y'] + pt_font * 0.35
            svg.append(
                f'<text x="{pt_cx}" y="{pt_cy}" text-anchor="middle" '
                f'font-family="{PT_FONT_FAMILY}" font-size="{pt_font}" font-weight="bold" '
                f'fill="{text_color}">{card.power}/{card.toughness}</text>'
            )
        else:
            pt_x = VB_W - art_m - pt_w - 6
            pt_y = rules_bottom - pt_h + 12
            pt_cx = pt_x + pt_w / 2
            pt_cy = pt_y + pt_h / 2 + pt_font * 0.35
            svg.append(
                f'<text x="{pt_cx}" y="{pt_cy}" text-anchor="middle" '
                f'font-family="{PT_FONT_FAMILY}" font-size="{pt_font}" font-weight="bold" '
                f'fill="{text_color}">{card.power}/{card.toughness}</text>'
            )

    # ── Loyalty counter ──
    if card.loyalty:
        loy_size = 96
        loy_font = 38
        start_path_d = _LOYALTY_SVG_PATHS.get('loyalty_start')
        if start_path_d:
            loy_w = loy_size
            loy_h = loy_size * 0.82
            loy_x = VB_W - art_m - loy_w / 2 - loy_w / 2 + 4
            loy_y = rules_bottom - loy_h * 0.40
            sx = loy_w / 32.0
            sy = loy_h / 32.0
            svg.append(
                f'<g transform="translate({loy_x + 2.5},{loy_y + 2.5}) scale({sx:.4f},{sy:.4f})">'
                f'<path d="{start_path_d}" fill="rgba(0,0,0,0.45)"/></g>'
            )
            svg.append(
                f'<g transform="translate({loy_x},{loy_y}) scale({sx:.4f},{sy:.4f})">'
                f'<path d="{start_path_d}" fill="#1a1410"/></g>'
            )
            font_baseline_offset = loy_font * 0.35
            text_x = loy_x + loy_w / 2
            text_y = loy_y + loy_h * 0.494 + font_baseline_offset
            svg.append(
                f'<text x="{text_x}" y="{text_y}" text-anchor="middle" '
                f'font-family="{PT_FONT_FAMILY}" font-size="{loy_font}" font-weight="bold" '
                f'fill="white">{card.loyalty}</text>'
            )

    svg.append('</svg>')
    return '\n'.join(svg)


# Ikoria showcase layout — pixel coords in the 750x1050 output space, measured
# from the vendored iko frame PNGs (rules box interior really ends ~y972, so text
# must stay above that or it spills below the box onto the art).
IKO_LAYOUT = {  # measured from the cardconjurer iko asset's real boxes
    'title_y0': 43, 'title_y1': 117,
    'type_y0': 644, 'type_y1': 721,   # relocated type bar (was 726-803)
    # Type bar is RELOCATED 82px up at composite time (see the iko branch of
    # _compose_image_frame_base) and the cream box fills the vacated space —
    # the text band is ~277px, in parity with the other styles. Bottom is
    # capped by the 3mm print-safe zone (36px at 11.9px/mm).
    'rules_y0': 719, 'rules_y1': 996,
    'x_margin': 62, 'x_right': 690,
    'pt_y': 1012,
}


def _create_iko_text_svg(card: CardData, fs: dict) -> str:
    """Text overlay for the Ikoria showcase (Godzilla) frame.

    Two-line title (big display name over the real name) in white on the dark
    gold-trimmed title bar; white type line; dark rules text on the light box;
    gold P/T. Rendered in 750x1050 pixel space to match the measured frame.
    """
    L = IKO_LAYOUT
    W, H = CARD_WIDTH, CARD_HEIGHT
    mana_pips = parse_mana_cost(card.mana_cost)
    # Colors > Text override applies to ALL card text (title/type/rules/PT),
    # consistent with the other frame styles; defaults otherwise.
    _ovr = (fs.get('color_overrides', {}) or {}).get('text')
    white = _ovr or '#f6f1e6'
    dark = _ovr or '#1a1712'
    pt_col = _ovr or '#f4e4a8'

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    tx = L['x_margin']
    esc_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    big = card.showcase_name or card.name
    big_esc = big.replace('&', '&amp;').replace('<', '&lt;')
    has_sub = bool(card.showcase_name) and card.showcase_name.strip() != card.name.strip()
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    avail = (L['x_right'] - tx) - pip_w

    def fit_font(text, base_font, ratio=0.58):
        est = len(text) * base_font * ratio
        if est > avail and avail > 0:
            return max(22, int(base_font * avail / est))
        return base_font

    # ── Title — the big showcase name, with the REAL card name small beneath
    # it when a showcase name is set (like the printed Godzilla cards, and as
    # the style description/tooltip promise). Single centered row otherwise. ──
    if has_sub:
        bf = fit_font(big, 34)
        big_y = L['title_y0'] + bf * 0.92
        svg.append(f'<text x="{tx}" y="{big_y}" font-family="{NAME_FONT_FAMILY}" '
                   f'font-size="{bf}" font-weight="bold" fill="{white}">{big_esc}</text>')
        sf = fit_font(card.name, 16, ratio=0.52)
        sub_y = L['title_y1'] - 6
        svg.append(f'<text x="{tx}" y="{sub_y}" font-family="{TYPE_FONT_FAMILY}" '
                   f'font-size="{sf}" fill="{white}" opacity="0.9">{esc_name}</text>')
    else:
        bf = fit_font(big, 42)
        cy = (L['title_y0'] + L['title_y1']) / 2 + bf * 0.35
        svg.append(f'<text x="{tx}" y="{cy}" font-family="{NAME_FONT_FAMILY}" '
                   f'font-size="{bf}" font-weight="bold" fill="{white}">{big_esc}</text>')

    # ── Mana pips (right-aligned in the title bar) ──
    if mana_pips:
        pcy = (L['title_y0'] + L['title_y1']) / 2
        px = L['x_right']
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            svg.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                       f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>')
            svg.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE/2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # ── Type line (white on the dark type bar), fit to width ──
    esc_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_w_avail = L['x_right'] - tx
    type_font = 31
    type_est = len(card.type_line) * type_font * 0.50
    if type_est > type_w_avail and type_w_avail > 0:
        type_font = max(19, int(type_font * type_w_avail / type_est))
    tcy = (L['type_y0'] + L['type_y1']) / 2 + type_font * 0.35
    svg.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
               f'font-size="{type_font}" font-weight="bold" fill="{white}">{esc_type}</text>')

    # ── Rules text (dark on the light box), measured + shrunk to fit ──
    # NOTE: render_rules_text_svg's 5th arg is LINE HEIGHT (RULES_LINE_H=37 for
    # font 29), not a small gap — passing a tiny value crams every line on top
    # of the next. Use the real line height and shrink the font for long oracles.
    if card.oracle_text and _is_planeswalker(card):
        svg.extend(_render_pw_content_svg(card, fs, tx, L['rules_y0'] + 8,
                                          L['x_right'], L['rules_y1'], dark))
    elif card.split_faces:
        # Adventure / split / room: two-column rules area
        _av = ((956.0, 532.0) if card.power is not None
               and card.toughness is not None else None)
        svg.extend(_render_split_rules_svg(
            card, fs, tx, L['rules_y0'] + 8, L['x_right'] - tx,
            (L['rules_y1'] - L['rules_y0']) - 16, dark,
            int(fs.get('rules_font_size') or 30), avoid=_av))
    elif card.oracle_text:
        rbox_w = L['x_right'] - tx
        rbox_top = L['rules_y0'] + 8
        rbox_h = (L['rules_y1'] - L['rules_y0']) - 16
        # Creatures: wrap the bottom lines narrower so text flows AROUND the
        # gold P/T plate (x596-706, y986-1034) instead of running under it.
        avoid_abs = None
        if card.power is not None and card.toughness is not None:
            avoid_abs = (956.0, 532.0)  # (plate top - pad, narrow line width)
        # Rules Text Size is a CEILING: find the max font that fits so text
        # always renders completely and can never overflow the box.
        desired = int(fs.get('rules_font_size') or 30)

        def _msr(f):
            av = ((avoid_abs[0] - (rbox_top + f * 0.8), avoid_abs[1])
                  if avoid_abs else None)
            return _measure_rules_text(card.oracle_text, rbox_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT), avoid=av)

        r_font = _max_fitting_rules_font(_msr, rbox_h, desired)
        r_line = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rbox_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rbox_h:.0f}px (font {r_font})')
        rules_lines, _ = render_rules_text_svg(
            card.oracle_text, tx, rbox_top + r_font * 0.8,
            rbox_w, rbox_h, r_font, r_line, text_color=dark, avoid=avoid_abs)
        svg.extend(rules_lines)

    # ── P/T (gold, centered in the gold plate drawn by the frame compositor) ──
    if card.power is not None and card.toughness is not None:
        svg.append(f'<text x="659" y="1002" text-anchor="middle" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="34" font-weight="bold" '
                   f'fill="{pt_col}">{card.power}/{card.toughness}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


def _is_planeswalker(card: CardData) -> bool:
    # Back faces of transform planeswalkers (e.g. "Arlinn, Embraced by the
    # Moon") have loyalty ABILITIES but no starting loyalty value — Scryfall
    # omits `loyalty` on them, so the type line matters too. Shield rendering
    # stays guarded by `if card.loyalty:` everywhere.
    return ((card.loyalty is not None
             or 'planeswalker' in (card.type_line or '').lower())
            and bool(_LOYALTY_RE.search(card.oracle_text or '')))


def _render_pw_content_svg(card: CardData, fs: dict, x0: float, y0: float,
                           x1: float, y1: float, text_color: str,
                           shield_center=None) -> list:
    """ALL planeswalker content for a badge-treatment style — ability badges,
    ability text, and the starting-loyalty shield — laid out to fit strictly
    INSIDE the content rect (x0,y0)-(x1,y1). The invariants, for any style:

    - badges anchor at x0 and never extend left of it
    - the first ability starts low enough that the tallest badge's plate
      (Plus, whose arrow extends the sprite above the plate) stays below y0
    - the loyalty shield tucks fully inside the bottom-right corner and
      ability text wraps around it via the avoid mechanism
    - the fitting loop shrinks the font until start offset + content height
      fits the rect (content never truncates and never crosses y1)
    """
    rect_h = y1 - y0
    fs['_pw_rect'] = (x0, y0, x1, y1)  # consumed by the quality gate
    # Starting-loyalty shield: anchored ON the rect's bottom-right corner
    # (straddling the box padding like a P/T plate), or at an explicit
    # per-style anchor (e.g. lotr's P/T zone, clear of its holo stamp).
    SHIELD_OVERHANG = 8
    # Consistent rule across ALL styles (matches the Planeswalker frame's
    # baked plate): the shield's vertical CENTER sits on the visual box
    # bottom edge (rect y1 + inset), straddling half-in/half-out, with the
    # right edge kissing the box border. Deep boxes are capped so the shield
    # bottom stays inside the 3mm print-safe zone (y<=1014, like P/T plates).
    SHIELD_PRINT_LIMIT = 1014
    shield = None
    if card.loyalty:
        g = _LOYALTY_SHIELD_GEOM
        sw = 114.0  # match the pw frame's baked plate width (113.75px)
        sh = sw * g['aspect']
        if shield_center is not None:
            s_cx, s_cy = shield_center
        else:
            s_cx = x1 + SHIELD_OVERHANG - sw * (1 - g['cx'])
            s_cy = min(y1 + SHIELD_OVERHANG,
                       SHIELD_PRINT_LIMIT - sh * (1 - g['cy']))
        shield = (s_cx, s_cy, sw, sh)
        g = _LOYALTY_SHIELD_GEOM
        fs['_pw_shield_bbox'] = (s_cx - g['cx'] * sw, s_cy - g['cy'] * sh,
                                 s_cx + (1 - g['cx']) * sw, s_cy + (1 - g['cy']) * sh)

    pw_font = int(RULES_FONT * 0.82)
    while True:
        pw_line_h = int(RULES_LINE_H * pw_font / RULES_FONT)
        # Tallest badge (Plus) extends 0.525*aspect above its plate center;
        # plate center anchors 0.32em above the first baseline. Start the
        # first line low enough that the badge top stays inside the rect.
        badge_w = pw_font * 3.48
        badge_above = _BADGE_GEOM['planeswalkerPlus']['cy'] * \
            _BADGE_GEOM['planeswalkerPlus']['aspect'] * badge_w
        # badge top = y0 + start_off - 0.32em (line center) - badge_above,
        # so start_off must exceed badge_above + 0.32em for the badge to
        # stay inside the rect.
        start_off = max(pw_font * 0.8, badge_above + pw_font * 0.32 + 3)
        avoid = None
        if shield is not None:
            text_x = x0 + badge_w + round(pw_font * 0.52)  # matches text_indent
            avoid = (shield[1] - shield[3] * _LOYALTY_SHIELD_GEOM['cy'] - 4,
                     max(60.0, (shield[0] - shield[2] * _LOYALTY_SHIELD_GEOM['cx']) - 8 - text_x))
        elems, used_h = render_planeswalker_abilities(
            card.oracle_text or "", x0, y0 + start_off,
            x1 - x0, rect_h, pw_font, pw_line_h, text_color=text_color,
            avoid=avoid)
        if start_off + used_h <= rect_h - 2 or pw_font <= RULES_FONT_FLOOR:
            break
        pw_font -= 1
    if start_off + used_h > rect_h + 2:  # only possible at the hard floor
        fs.setdefault('_quality', []).append(
            f'rules_overflow: planeswalker needs {start_off + used_h:.0f}px '
            f'but box is {rect_h:.0f}px (font {pw_font})')
    if shield is not None:
        elems = list(elems) + _start_loyalty_badge_svg(
            card.loyalty, shield[0], shield[1], size=shield[2])
    return elems


# Starting-loyalty shield sprite (extracted from the cardconjurer pw frame
# via maskLoyalty.png) — plate geometry measured from its alpha channel.
_LOYALTY_SHIELD_GEOM = {'aspect': 143 / 228, 'cy': 0.465, 'cx': 0.498}


def _start_loyalty_badge_svg(loyalty: str, cx: float, cy: float,
                             size: float = 100) -> list:
    """Starting-loyalty shield (authentic cardconjurer sprite + white
    number), plate-centered at (cx, cy) in 750x1050 pixel space — used where
    a creature's P/T would go. Falls back to the vector shape if the sprite
    asset is missing."""
    import base64
    parts = []
    loy_font = size * 0.32  # breathing room inside the shield plate
    uri = _BADGE_URI_CACHE.get('loyaltyStart')
    if uri is None:
        path = FRAMES_DIR / 'planeswalker' / 'loyaltyStart.png'
        uri = ('data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()
               if path.exists() else '')
        _BADGE_URI_CACHE['loyaltyStart'] = uri
    if uri:
        g = _LOYALTY_SHIELD_GEOM
        w = size
        h = w * g['aspect']
        x = cx - g['cx'] * w
        y = cy - g['cy'] * h
        parts.append(f'<image x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                     f'xlink:href="{uri}"/>')
        # The plate's alpha center sits above the shield body's visual center
        # of mass (the bottom point pulls it down) — drop the number ~10% of
        # the shield height so it reads centered.
        text_y = cy + h * 0.10 + loy_font * 0.35
    else:
        start_path_d = _LOYALTY_SVG_PATHS.get('loyalty_start')
        loy_w, loy_h = size, size * 0.82
        loy_x, loy_y = cx - loy_w / 2, cy - loy_h / 2
        if start_path_d:
            sx, sy = loy_w / 32.0, loy_h / 32.0
            parts.append(f'<g transform="translate({loy_x + 2.5},{loy_y + 2.5}) '
                         f'scale({sx:.4f},{sy:.4f})">'
                         f'<path d="{start_path_d}" fill="rgba(0,0,0,0.45)"/></g>')
            parts.append(f'<g transform="translate({loy_x},{loy_y}) scale({sx:.4f},{sy:.4f})">'
                         f'<path d="{start_path_d}" fill="#1a1410"/></g>')
            text_y = loy_y + loy_h * 0.494 + loy_font * 0.35
        else:
            parts.append(f'<rect x="{loy_x}" y="{loy_y}" width="{loy_w}" height="{loy_h}" '
                         f'rx="{loy_h/4}" fill="#1a1410"/>')
            text_y = cy + loy_font * 0.35
    parts.append(f'<text x="{cx}" y="{text_y}" text-anchor="middle" '
                 f'font-family="{PT_FONT_FAMILY}" font-size="{loy_font:.0f}" '
                 f'font-weight="bold" fill="white">{loyalty}</text>')
    return parts


def _create_bar_box_text_svg(card: CardData, fs: dict, L: dict,
                             bar_color: str, rules_color: str, pt_color: str,
                             title_font: int = 40, type_font: int = 34,
                             pt_font: int = 36) -> str:
    """Shared text overlay for image frames with the standard bar/box layout:
    title bar + mana pips, type bar, rules box (fit-to-box with P/T avoid
    wrap), P/T centered on the style's plate. Colors > Text override applies
    to all text. Layout dict L needs: title_y0/1, type_y0/1, rules_y0/1,
    x_margin, x_right, pt_cx, pt_cy, and optionally avoid (y_top, narrow_w)."""
    W, H = CARD_WIDTH, CARD_HEIGHT
    mana_pips = parse_mana_cost(card.mana_cost)
    _ovr = (fs.get('color_overrides', {}) or {}).get('text')
    bar_col = _ovr or bar_color
    rules_col = _ovr or rules_color
    pt_col = _ovr or pt_color

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    tx = L['x_margin']
    esc_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    name_avail = (L['x_right'] - tx) - pip_w
    nf = title_font
    if len(card.name) * nf * 0.58 > name_avail and name_avail > 0:
        nf = max(22, int(nf * name_avail / (len(card.name) * nf * 0.58)))
    ncy = (L['title_y0'] + L['title_y1']) / 2 + nf * 0.35
    svg.append(f'<text x="{tx}" y="{ncy}" font-family="{NAME_FONT_FAMILY}" '
               f'font-size="{nf}" font-weight="bold" fill="{bar_col}">{esc_name}</text>')

    if mana_pips:
        pcy = (L['title_y0'] + L['title_y1']) / 2
        px = L['x_right']
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            svg.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                       f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>')
            svg.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE/2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    esc_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_w_avail = L['x_right'] - tx
    tf = type_font
    if len(card.type_line) * tf * 0.50 > type_w_avail and type_w_avail > 0:
        tf = max(19, int(tf * type_w_avail / (len(card.type_line) * tf * 0.50)))
    tcy = (L['type_y0'] + L['type_y1']) / 2 + tf * 0.35
    svg.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
               f'font-size="{tf}" font-weight="bold" fill="{bar_col}">{esc_type}</text>')

    if card.oracle_text and _is_planeswalker(card):
        # Planeswalker: badges + text + loyalty shield, all strictly inside
        # the rules content rect.
        svg.extend(_render_pw_content_svg(card, fs, tx, L['rules_y0'] + 8,
                                          L['x_right'], L['rules_y1'] - 8,
                                          rules_col))
    elif card.split_faces:
        # Adventure / split / room: two-column rules area
        avoid_abs = None
        if card.power is not None and card.toughness is not None and 'avoid' in L:
            avoid_abs = L['avoid']
        svg.extend(_render_split_rules_svg(
            card, fs, tx, L['rules_y0'] + 8, L['x_right'] - tx,
            (L['rules_y1'] - L['rules_y0']) - 16, rules_col,
            int(fs.get('rules_font_size') or 30), avoid=avoid_abs))
    elif card.oracle_text:
        rbox_w = L['x_right'] - tx
        rbox_top = L['rules_y0'] + 8
        rbox_h = (L['rules_y1'] - L['rules_y0']) - 16
        avoid_abs = None
        if card.power is not None and card.toughness is not None and 'avoid' in L:
            avoid_abs = L['avoid']
        desired = int(fs.get('rules_font_size') or 30)

        def _msr(f):
            av = ((avoid_abs[0] - (rbox_top + f * 0.8), avoid_abs[1])
                  if avoid_abs else None)
            return _measure_rules_text(card.oracle_text, rbox_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT), avoid=av)

        r_font = _max_fitting_rules_font(_msr, rbox_h, desired)
        r_line = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rbox_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rbox_h:.0f}px (font {r_font})')
        rules_lines, _ = render_rules_text_svg(
            card.oracle_text, tx, rbox_top + r_font * 0.8,
            rbox_w, rbox_h, r_font, r_line, text_color=rules_col, avoid=avoid_abs)
        svg.extend(rules_lines)

    if card.power is not None and card.toughness is not None:
        svg.append(f'<text x="{L["pt_cx"]}" y="{L["pt_cy"] + pt_font * 0.35}" text-anchor="middle" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="{pt_font}" font-weight="bold" '
                   f'fill="{pt_col}">{card.power}/{card.toughness}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


# Authentic planeswalker frame — packPlaneswalkerRegular.js bounds in
# 750x1050. Bright bars -> dark title/type; ability bands drawn by us
# (alternating translucent light/dark, black text); loyalty white on the
# frame's baked shield.
PW_FRAME_LAYOUT = {
    'title_y0': 39, 'title_y1': 96,
    'type_y0': 591, 'type_y1': 648,
    'x_margin': 65, 'x_right': 690,
    'band_x0': 90, 'band_x1': 691,     # ability band span (frame window x)
    'text_x': 135, 'text_w': 545,      # ability text (indented past badges)
    'area_y0': 655, 'area_y1': 920,    # text-safe ability area (above the plate)
    'band_draw_y1': 962,               # bands visually extend behind the plate
    # Baked loyalty plate center from the maskLoyalty-isolated sprite bbox
    # (x600-713.75, y923.5-994.5 with plate cx/cy ratios 0.498/0.465) —
    # NOT from raw in-frame alpha, which is polluted by the bottom bar.
    'loy_cx': 657, 'loy_cy': 956,
}


def _pw_frame_band_layout(card: CardData):
    """Deterministic ability-band layout for the planeswalker frame, shared
    by the chrome compositor and the text overlay so they always agree.

    Returns (font, line_h, bands) where bands is a list of
    (y0, y1, cost, text) filling the whole ability area — band heights are
    proportional to each ability's measured text, with a minimum for the
    badge, and the font shrinks until everything fits (never truncates)."""
    L = PW_FRAME_LAYOUT
    abilities = _parse_loyalty_abilities(card.oracle_text or '')
    if not abilities:
        return None
    area_h = L['area_y1'] - L['area_y0']
    font = 24
    while True:
        line_h = int(RULES_LINE_H * font / RULES_FONT)
        # Minimum band height holds the loyalty badge, which scales with the
        # font (badge width font*3.1, plate height ~72% of that). A fixed
        # minimum made 5+-ability walkers unfittable at ANY font, driving
        # the loop straight to the unreadable floor.
        min_band = font * 2.35 + 4
        heights = []
        for ab in abilities:
            h = _measure_rules_text(ab['text'], L['text_w'], font, line_h)
            heights.append(max(h + line_h * 0.55, min_band))
        if sum(heights) <= area_h or font <= RULES_FONT_FLOOR:
            break
        font -= 1
    # scale bands to fill the full area (real cards' bands tile the box)
    scale = area_h / sum(heights)
    bands, y = [], float(L['area_y0'])
    for ab, h in zip(abilities, heights):
        h2 = h * scale
        bands.append((y, y + h2, ab['cost'], ab['text']))
        y += h2
    return font, line_h, bands


def _create_pw_frame_text_svg(card: CardData, fs: dict) -> str:
    """Text overlay for the authentic planeswalker frame: dark title/type on
    the bright bars, BLACK ability text on the translucent bands, white cost
    numbers on the badges, white starting loyalty on the baked shield."""
    L = PW_FRAME_LAYOUT
    W, H = CARD_WIDTH, CARD_HEIGHT
    mana_pips = parse_mana_cost(card.mana_cost)
    _ovr = (fs.get('color_overrides', {}) or {}).get('text')
    ink = _ovr or '#1a1712'

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    tx = L['x_margin']
    esc_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    name_avail = (L['x_right'] - tx) - pip_w
    nf = 40
    if len(card.name) * nf * 0.58 > name_avail and name_avail > 0:
        nf = max(22, int(nf * name_avail / (len(card.name) * nf * 0.58)))
    ncy = (L['title_y0'] + L['title_y1']) / 2 + nf * 0.35
    svg.append(f'<text x="{tx}" y="{ncy}" font-family="{NAME_FONT_FAMILY}" '
               f'font-size="{nf}" font-weight="bold" fill="{ink}">{esc_name}</text>')

    if mana_pips:
        pcy = (L['title_y0'] + L['title_y1']) / 2
        px = L['x_right']
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            svg.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                       f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>')
            svg.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE/2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    esc_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    tf = 34
    type_avail = L['x_right'] - tx
    if len(card.type_line) * tf * 0.50 > type_avail and type_avail > 0:
        tf = max(19, int(tf * type_avail / (len(card.type_line) * tf * 0.50)))
    tcy = (L['type_y0'] + L['type_y1']) / 2 + tf * 0.35
    svg.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
               f'font-size="{tf}" font-weight="bold" fill="{ink}">{esc_type}</text>')

    layout = _pw_frame_band_layout(card)
    if layout:
        font, line_h, bands = layout
        bw = min(74, round(font * 3.1))          # matches chrome badge width
        bf = max(10, min(21, round(font * 0.875)))
        for (y0, y1, cost, text) in bands:
            # vertically center the measured text inside its band
            h_text = _measure_rules_text(text, L['text_w'], font, line_h)
            if h_text > (y1 - y0) + 2:  # only possible at the hard floor
                fs.setdefault('_quality', []).append(
                    f'pw_band_overflow: ability needs {h_text:.0f}px '
                    f'but band is {y1 - y0:.0f}px (font {font})')
            ty = y0 + max((y1 - y0 - h_text) / 2, 4) + font * 0.8
            # max_height never clips below the measured text: bands must
            # render complete abilities even when over-tall (flagged above)
            lines, _ = render_rules_text_svg(text, L['text_x'], ty,
                                             L['text_w'],
                                             max(y1 - y0, h_text + line_h),
                                             font, line_h,
                                             text_color=ink)
            svg.extend(lines)
            if cost:
                # white cost number on the badge plate (chrome anchors the
                # plate center on the band center at x56, width bw)
                disp = cost.replace('−', '-')
                gx = 56 + _BADGE_GEOM[_badge_icon_for(cost)]['cx'] * bw
                svg.append(f'<text x="{gx:.1f}" y="{(y0 + y1) / 2 + bf * 0.35}" '
                           f'text-anchor="middle" font-family="{PT_FONT_FAMILY}" '
                           f'font-size="{bf}" font-weight="bold" fill="white">{disp}</text>')
    elif card.oracle_text:
        # not actually ability-structured — plain rules text on a light band
        lines, _ = render_rules_text_svg(card.oracle_text, L['text_x'],
                                         L['area_y0'] + 28, L['text_w'],
                                         L['area_y1'] - L['area_y0'], 24, 30,
                                         text_color=ink)
        svg.extend(lines)

    if card.loyalty:
        svg.append(f'<text x="{L["loy_cx"]}" y="{L["loy_cy"] + 13}" text-anchor="middle" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="38" font-weight="bold" '
                   f'fill="white">{card.loyalty}</text>')
    elif card.power is not None and card.toughness is not None:
        # non-planeswalker creature in this frame: P/T on the baked shield
        svg.append(f'<text x="{L["loy_cx"]}" y="{L["loy_cy"] + 11}" text-anchor="middle" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="30" font-weight="bold" '
                   f'fill="white">{card.power}/{card.toughness}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


# New Capenna Art Deco — pack SNCArtDeco.js bounds in 750x1050. Dark bars ->
# white title/type; light geometric rules panel -> dark rules text.
ARTDECO_LAYOUT = {
    'title_y0': 55, 'title_y1': 112,
    'type_y0': 595, 'type_y1': 652,
    'rules_y0': 662, 'rules_y1': 946,
    'x_margin': 64, 'x_right': 690,
    'pt_x': 576, 'pt_y': 930, 'pt_w': 145, 'pt_h': 61,
    'pt_cx': 648, 'pt_cy': 964,
    'crown': (21, 21, 708, 102),
    'avoid': (922.0, 502.0),
}


def _create_artdeco_text_svg(card: CardData, fs: dict) -> str:
    """Art Deco: white title/type on the dark bars, dark rules on the light
    panel, white P/T."""
    return _create_bar_box_text_svg(card, fs, ARTDECO_LAYOUT,
                                    bar_color='#f6f1e6', rules_color='#1a1712',
                                    pt_color='#f6f1e6')


# Kamigawa Samurai showcase — packNeoSamurai.js bounds. Dark brushed frame ->
# LIGHT text everywhere.
SAMURAI_LAYOUT = {
    'title_y0': 72, 'title_y1': 129,
    'type_y0': 595, 'type_y1': 652,
    'rules_y0': 662, 'rules_y1': 946,
    'x_margin': 64, 'x_right': 690,
    'pt_x': 573, 'pt_y': 930, 'pt_w': 147, 'pt_h': 71,
    'pt_cx': 646, 'pt_cy': 971,
    'crown': (22, 17, 707, 79),
    'stamp': (326, 952, 97, 44),
    'avoid': (922.0, 499.0),
}


def _create_samurai_text_svg(card: CardData, fs: dict) -> str:
    """Samurai: light text everywhere on the dark brushed frame."""
    w = '#f2f3f5'
    return _create_bar_box_text_svg(card, fs, SAMURAI_LAYOUT,
                                    bar_color=w, rules_color=w, pt_color=w)


# Commander Etched foil — packEtched.js bounds. Dark engraved metal -> LIGHT
# text everywhere.
ETCHED_LAYOUT = {
    'title_y0': 55, 'title_y1': 112,
    'type_y0': 595, 'type_y1': 652,
    'rules_y0': 662, 'rules_y1': 955,
    'x_margin': 70, 'x_right': 685,
    'pt_x': 568, 'pt_y': 929, 'pt_w': 141, 'pt_h': 77,
    'pt_cx': 646, 'pt_cy': 966,
    'crown': (23, 20, 704, 97),
    'stamp': (315, 951, 120, 48),
    'avoid': (921.0, 488.0),
}


def _create_etched_text_svg(card: CardData, fs: dict) -> str:
    """Etched: light text everywhere on the dark engraved frame."""
    w = '#f2f3f5'
    return _create_bar_box_text_svg(card, fs, ETCHED_LAYOUT,
                                    bar_color=w, rules_color=w, pt_color=w)


# 8th Edition layout — pixel coords in 750x1050 from pack8th.js, confirmed
# against asset alpha bounds (title y58.5-118, type y589.5-647, rules
# y653-943.5). Bright metallic bars + white box -> BLACK text everywhere.
EIGHTH_LAYOUT = {
    'title_y0': 58, 'title_y1': 118,
    'type_y0': 590, 'type_y1': 647,
    'rules_y0': 659, 'rules_y1': 938,
    'x_margin': 75, 'x_right': 680,
    'pt_x': 542, 'pt_y': 924, 'pt_w': 161, 'pt_h': 88,
    'pt_cx': 626, 'pt_cy': 963,
    'avoid': (916.0, 457.0),   # P/T box top - pad, narrow line width
}


def _create_8th_text_svg(card: CardData, fs: dict) -> str:
    """8th Edition text overlay: BLACK text on the bright metallic bars and
    near-white rules box, black P/T."""
    ink = '#1a1712'
    return _create_bar_box_text_svg(card, fs, EIGHTH_LAYOUT,
                                    bar_color=ink, rules_color=ink, pt_color=ink,
                                    title_font=42, type_font=36, pt_font=40)


# Mystical Archive layout — pixel coords in 750x1050 from
# packMysticalArchive.js. Parchment banners and panel -> DARK text.
MSA_LAYOUT = {
    'title_y0': 55, 'title_y1': 112,
    'type_y0': 595, 'type_y1': 652,
    'rules_y0': 662, 'rules_y1': 955,
    'x_margin': 70, 'x_right': 680,
    'pt_x': 567, 'pt_y': 924, 'pt_w': 159, 'pt_h': 80,
    'pt_cx': 646, 'pt_cy': 966,
    'crown_h': 49,
    'avoid': (916.0, 487.0),
}


def _create_msa_text_svg(card: CardData, fs: dict) -> str:
    """Mystical Archive text overlay: dark text on the parchment banners,
    panel, and P/T plate."""
    ink = '#211a10'
    return _create_bar_box_text_svg(card, fs, MSA_LAYOUT,
                                    bar_color=ink, rules_color=ink, pt_color=ink)


# LOTR "Ring" frame layout — pixel coords in 750x1050, from packRing.js bounds
# confirmed against the assets' alpha bounds (title bar y56-116.5, type bar
# y590.5-650.5, rules panel y656.5-965.5). Dark bars -> WHITE title/type text;
# light parchment panel -> DARK rules text. The holo stamp sits bottom-center
# INSIDE the panel footprint, so the text band ends above it (y940).
LOTR_LAYOUT = {
    'title_y0': 56, 'title_y1': 117,
    'type_y0': 590, 'type_y1': 650,
    'rules_y0': 662, 'rules_y1': 940,
    'x_margin': 64, 'x_right': 690,
    # P/T plate (packRing.js: 1148,1857 268x134 at 1500x2100)
    'pt_x': 574, 'pt_y': 928, 'pt_w': 134, 'pt_h': 67,
    'pt_cx': 643, 'pt_cy': 963,          # P/T text center (pack pt bounds)
    # holo stamp (packRing.js: 644,1893 212x95)
    'stamp_x': 322, 'stamp_y': 946, 'stamp_w': 106, 'stamp_h': 48,
    'crown_h': 136,                       # wavy legendary crown (1500x272)
}


def _create_lotr_text_svg(card: CardData, fs: dict) -> str:
    """Text overlay for the LOTR "Ring" frame: white beleren title/type on the
    dark blue bars, DARK rules text on the light parchment panel, white P/T.
    Rendered in 750x1050 pixel space to match the measured frame."""
    L = LOTR_LAYOUT
    W, H = CARD_WIDTH, CARD_HEIGHT
    mana_pips = parse_mana_cost(card.mana_cost)
    # Colors > Text override applies to ALL card text, like the other styles.
    _ovr = (fs.get('color_overrides', {}) or {}).get('text')
    white = _ovr or '#f6f1e6'
    dark = _ovr or '#1a1712'

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    tx = L['x_margin']
    esc_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    name_avail = (L['x_right'] - tx) - pip_w
    nf = 40  # packRing.js title size 0.0381 * 1050
    if len(card.name) * nf * 0.58 > name_avail and name_avail > 0:
        nf = max(22, int(nf * name_avail / (len(card.name) * nf * 0.58)))
    ncy = (L['title_y0'] + L['title_y1']) / 2 + nf * 0.35
    svg.append(f'<text x="{tx}" y="{ncy}" font-family="{NAME_FONT_FAMILY}" '
               f'font-size="{nf}" font-weight="bold" fill="{white}">{esc_name}</text>')

    # ── Mana pips (right-aligned in the title bar) ──
    if mana_pips:
        pcy = (L['title_y0'] + L['title_y1']) / 2
        px = L['x_right']
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            svg.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                       f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>')
            svg.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE/2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # ── Type line (white on the dark type bar), fit to width ──
    esc_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_w_avail = L['x_right'] - tx
    tf = 34  # packRing.js type size 0.0324 * 1050
    if len(card.type_line) * tf * 0.50 > type_w_avail and type_w_avail > 0:
        tf = max(19, int(tf * type_w_avail / (len(card.type_line) * tf * 0.50)))
    tcy = (L['type_y0'] + L['type_y1']) / 2 + tf * 0.35
    svg.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
               f'font-size="{tf}" font-weight="bold" fill="{white}">{esc_type}</text>')

    # ── Rules text (DARK on the parchment panel) ──
    if card.oracle_text and _is_planeswalker(card):
        svg.extend(_render_pw_content_svg(card, fs, tx, L['rules_y0'] + 8,
                                          L['x_right'], L['rules_y1'] - 8, dark,
                                          shield_center=(L['pt_cx'], L['pt_cy'])))
    elif card.split_faces:
        # Adventure / split / room: two-column rules area
        _av = ((920.0, 502.0) if card.power is not None
               and card.toughness is not None else None)
        svg.extend(_render_split_rules_svg(
            card, fs, tx, L['rules_y0'] + 8, L['x_right'] - tx,
            (L['rules_y1'] - L['rules_y0']) - 16, dark,
            int(fs.get('rules_font_size') or 30), avoid=_av))
    elif card.oracle_text:
        rbox_w = L['x_right'] - tx
        rbox_top = L['rules_y0'] + 8
        rbox_h = (L['rules_y1'] - L['rules_y0']) - 16
        # Creatures: wrap bottom lines narrower around the P/T plate (x574, y928).
        avoid_abs = None
        if card.power is not None and card.toughness is not None:
            avoid_abs = (920.0, 502.0)  # (plate top - pad, narrow line width)
        # Rules Text Size is a CEILING: find the max font that fits so text
        # always renders completely and can never overflow the box.
        desired = int(fs.get('rules_font_size') or 30)

        def _msr(f):
            av = ((avoid_abs[0] - (rbox_top + f * 0.8), avoid_abs[1])
                  if avoid_abs else None)
            return _measure_rules_text(card.oracle_text, rbox_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT), avoid=av)

        r_font = _max_fitting_rules_font(_msr, rbox_h, desired)
        r_line = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rbox_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rbox_h:.0f}px (font {r_font})')
        rules_lines, _ = render_rules_text_svg(
            card.oracle_text, tx, rbox_top + r_font * 0.8,
            rbox_w, rbox_h, r_font, r_line, text_color=dark, avoid=avoid_abs)
        svg.extend(rules_lines)

    # ── P/T (white, centered on the plate drawn by the frame compositor) ──
    if card.power is not None and card.toughness is not None:
        svg.append(f'<text x="{L["pt_cx"]}" y="{L["pt_cy"] + 36 * 0.35}" text-anchor="middle" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="36" font-weight="bold" '
                   f'fill="{white}">{card.power}/{card.toughness}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


# Crystal frame layout — pixel coords in 750x1050, converted from cardconjurer's
# packCrystal.js bounds (fractions of 1500x2100) and confirmed against the
# assets' alpha bounds. All bars and the rules box are dark stone -> LIGHT text.
CRYSTAL_LAYOUT = {
    'title_y0': 53, 'title_y1': 110,
    'type_y0': 594, 'type_y1': 648,
    'rules_y0': 671, 'rules_y1': 957,
    'x_margin': 64, 'x_right': 690,
    # P/T box (packCrystal.js: x 1157/1500, y 1847/2100, 294x170 asset at 1:2 scale)
    'pt_x': 578, 'pt_y': 923, 'pt_w': 147, 'pt_h': 85,
    'pt_cx': 652, 'pt_cy': 970,          # P/T text center (ice box interior)
    'crown_h': 54,                        # crown strip height (107/2100)
}


def _create_crystal_text_svg(card: CardData, fs: dict) -> str:
    """Text overlay for the Crystal frame: light beleren text on the dark stone
    title/type bars, LIGHT rules text on the dark scratched-stone rules box
    (unlike m15/iko's cream boxes), white P/T over the ice box. Rendered in
    750x1050 pixel space to match the measured frame."""
    L = CRYSTAL_LAYOUT
    W, H = CARD_WIDTH, CARD_HEIGHT
    mana_pips = parse_mana_cost(card.mana_cost)
    # Colors > Text override applies to ALL card text (title/type/rules/PT),
    # consistent with the other frame styles; light default otherwise.
    white = (fs.get('color_overrides', {}) or {}).get('text') or '#f2f3f5'
    rules_col = white

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    tx = L['x_margin']
    esc_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    name_avail = (L['x_right'] - tx) - pip_w
    nf = 40  # packCrystal.js title size 0.0381 * 1050
    if len(card.name) * nf * 0.58 > name_avail and name_avail > 0:
        nf = max(22, int(nf * name_avail / (len(card.name) * nf * 0.58)))
    ncy = (L['title_y0'] + L['title_y1']) / 2 + nf * 0.35
    svg.append(f'<text x="{tx}" y="{ncy}" font-family="{NAME_FONT_FAMILY}" '
               f'font-size="{nf}" font-weight="bold" fill="{white}">{esc_name}</text>')

    # ── Mana pips (right-aligned in the title bar) ──
    if mana_pips:
        pcy = (L['title_y0'] + L['title_y1']) / 2
        px = L['x_right']
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            svg.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                       f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>')
            svg.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE/2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # ── Type line (light on the dark stone type bar), fit to width ──
    esc_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_w_avail = L['x_right'] - tx
    tf = 34  # packCrystal.js type size 0.0324 * 1050
    if len(card.type_line) * tf * 0.50 > type_w_avail and type_w_avail > 0:
        tf = max(19, int(tf * type_w_avail / (len(card.type_line) * tf * 0.50)))
    tcy = (L['type_y0'] + L['type_y1']) / 2 + tf * 0.35
    svg.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
               f'font-size="{tf}" font-weight="bold" fill="{white}">{esc_type}</text>')

    # ── Rules text (LIGHT on the dark stone box), measured + shrunk to fit ──
    if card.oracle_text and _is_planeswalker(card):
        svg.extend(_render_pw_content_svg(card, fs, tx, L['rules_y0'] + 8,
                                          L['x_right'], L['rules_y1'] - 8, rules_col))
    elif card.split_faces:
        # Adventure / split / room: two-column rules area
        _av = ((915.0, 506.0) if card.power is not None
               and card.toughness is not None else None)
        svg.extend(_render_split_rules_svg(
            card, fs, tx, L['rules_y0'] + 8, L['x_right'] - tx,
            (L['rules_y1'] - L['rules_y0']) - 16, rules_col,
            int(fs.get('rules_font_size') or 30), avoid=_av))
    elif card.oracle_text:
        rbox_w = L['x_right'] - tx
        rbox_top = L['rules_y0'] + 8
        rbox_h = (L['rules_y1'] - L['rules_y0']) - 16
        # Creatures: wrap bottom lines narrower around the ice P/T box
        # (x578, y923) instead of running under it.
        avoid_abs = None
        if card.power is not None and card.toughness is not None:
            avoid_abs = (915.0, 506.0)  # (P/T box top - pad, narrow line width)
        # Rules Text Size is a CEILING: find the max font that fits so text
        # always renders completely and can never overflow the box.
        desired = int(fs.get('rules_font_size') or 30)

        def _msr(f):
            av = ((avoid_abs[0] - (rbox_top + f * 0.8), avoid_abs[1])
                  if avoid_abs else None)
            return _measure_rules_text(card.oracle_text, rbox_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT), avoid=av)

        r_font = _max_fitting_rules_font(_msr, rbox_h, desired)
        r_line = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rbox_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rbox_h:.0f}px (font {r_font})')
        rules_lines, _ = render_rules_text_svg(
            card.oracle_text, tx, rbox_top + r_font * 0.8,
            rbox_w, rbox_h, r_font, r_line, text_color=rules_col, avoid=avoid_abs)
        svg.extend(rules_lines)

    # ── P/T (white, centered in the ice box drawn by the frame compositor) ──
    if card.power is not None and card.toughness is not None:
        svg.append(f'<text x="{L["pt_cx"]}" y="{L["pt_cy"] + 36 * 0.35}" text-anchor="middle" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="36" font-weight="bold" '
                   f'fill="{white}">{card.power}/{card.toughness}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


# Original ABU (1993) layout — pixel coords in 750x1050, measured from the abu
# frame PNGs. Art window is inset (y110-571); text is black serif on beige.
ABU_LAYOUT = {
    'name_y0': 34, 'name_y1': 100,
    'type_y0': 574, 'type_y1': 618,
    'rules_y0': 628, 'rules_y1': 992,
    'x_margin': 62, 'x_right': 688,
    'pt_y': 1016,
}


def _create_abu_text_svg(card: CardData, fs: dict) -> str:
    """Text overlay for the original 1993 (ABU) border: black serif name, type,
    rules (in the textured box) and P/T, over the beige frame."""
    L = ABU_LAYOUT
    W, H = CARD_WIDTH, CARD_HEIGHT
    mana_pips = parse_mana_cost(card.mana_cost)
    ink = '#1a1712'

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    tx = L['x_margin']
    esc_name = card.name.replace('&', '&amp;').replace('<', '&lt;')
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    name_avail = (L['x_right'] - tx) - pip_w
    nf = NAME_FONT
    if len(card.name) * nf * 0.58 > name_avail and name_avail > 0:
        nf = max(20, int(nf * name_avail / (len(card.name) * nf * 0.58)))
    ncy = (L['name_y0'] + L['name_y1']) / 2 + nf * 0.35
    svg.append(f'<text x="{tx}" y="{ncy}" font-family="{NAME_FONT_FAMILY}" '
               f'font-size="{nf}" font-weight="bold" fill="{ink}">{esc_name}</text>')

    # Mana pips (right-aligned in the name row)
    if mana_pips:
        pcy = (L['name_y0'] + L['name_y1']) / 2
        px = L['x_right']
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            svg.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                       f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.28)"/>')
            svg.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE/2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # Type line
    esc_type = card.type_line.replace('&', '&amp;').replace('<', '&lt;')
    type_w = L['x_right'] - tx
    tf = TYPE_FONT
    if len(card.type_line) * tf * 0.50 > type_w and type_w > 0:
        tf = max(18, int(tf * type_w / (len(card.type_line) * tf * 0.50)))
    tcy = (L['type_y0'] + L['type_y1']) / 2 + tf * 0.35
    svg.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
               f'font-size="{tf}" font-weight="bold" fill="{ink}">{esc_type}</text>')

    # Rules text (black serif), shrink to fit the text box
    if card.split_faces:
        # Adventure / split / room: two-column rules area
        svg.extend(_render_split_rules_svg(
            card, fs, tx, L['rules_y0'] + 8, L['x_right'] - tx,
            (L['rules_y1'] - L['rules_y0']) - 12, ink,
            int(fs.get('rules_font_size') or RULES_FONT)))
    elif card.oracle_text:
        rbox_w = L['x_right'] - tx
        rbox_h = (L['rules_y1'] - L['rules_y0']) - 12
        desired = int(fs.get('rules_font_size') or RULES_FONT)

        def _msr(f):
            return _measure_rules_text(card.oracle_text, rbox_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT))

        r_font = _max_fitting_rules_font(_msr, rbox_h, desired)
        r_line = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rbox_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: needs {_msr(r_font):.0f}px but box is {rbox_h:.0f}px (font {r_font})')
        rules_lines, _ = render_rules_text_svg(
            card.oracle_text, tx, L['rules_y0'] + 8 + r_font * 0.8,
            rbox_w, rbox_h, r_font, r_line, text_color=ink)
        svg.extend(rules_lines)

    # P/T (black, bottom-right)
    if card.power is not None and card.toughness is not None:
        svg.append(f'<text x="{L["x_right"] - 2}" y="{L["pt_y"]}" text-anchor="end" '
                   f'font-family="{PT_FONT_FAMILY}" font-size="34" font-weight="bold" '
                   f'fill="{ink}">{card.power}/{card.toughness}</text>')

    svg.append('</svg>')
    return '\n'.join(svg)


_IKO_ACCENT_MASK = None  # (mask 0..1, shade) — computed once, see below


def _iko_accent_mask():
    """Soft mask of the iko frame's baked accent trim (title/type bar
    outlines and rails), derived from the U frame whose blue accent chroma
    is unambiguous. All iko color frames share identical geometry, so the
    mask transfers to any of them — including grayscale-accent frames
    (W/B/A) where color matching alone can't isolate the trim. Returns
    (mask, shade) float arrays, or None if the asset is unavailable.
    'shade' preserves the trim's own shading/anti-aliasing when painting
    a new color over it."""
    global _IKO_ACCENT_MASK
    if _IKO_ACCENT_MASK is None:
        import numpy as np
        u = _load_frame_image('iko', 'u')
        if u is None:
            return None
        if u.size != (CARD_WIDTH, CARD_HEIGHT):
            u = u.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
        arr = np.asarray(u, dtype=float)
        rgb, alpha = arr[..., :3], arr[..., 3]
        accent = np.array((0.0, 119.0, 195.0))  # the U trim blue
        a_dir = accent / np.linalg.norm(accent)
        norms = np.linalg.norm(rgb, axis=-1)
        cos = (rgb @ a_dir) / (norms + 1e-6)
        sat = rgb.max(-1) - rgb.min(-1)
        # Soft thresholds keep the anti-aliased trim edges smooth
        m_cos = np.clip((cos - 0.96) / (0.985 - 0.96), 0, 1)
        m_sat = np.clip((sat - 20) / (60 - 20), 0, 1)
        mask = m_cos * m_sat * (alpha / 255.0)
        shade = np.clip(norms / np.linalg.norm(accent), 0, 1.6)
        _IKO_ACCENT_MASK = (mask, shade)
    return _IKO_ACCENT_MASK


def _compose_image_frame_base(card_dict: dict, card: CardData, fs: dict) -> Image.Image:
    """Frame PNG + P/T box (gradient-aware), WITHOUT text.

    Shared by both the final composite (`_render_image_frame`) and the WYSIWYG
    designer's frame layer (`render_frame_layer`), so both paths render identical
    chrome — including two-color gradients. (CLAUDE.md warns these were separate
    code paths that drifted; this helper keeps them in lockstep.)
    """
    color_key = _determine_color_key(card_dict)
    frame_set = fs.get('frame_set', 'm15')

    # Two-color cards/lands: build a left->right gradient frame from the two
    # single-color frames (the goal's "left/right color gradients"), instead of
    # the flat gold 'm' frame. Returns 'gradient'|'split'|None.
    grad_mode = _gradient_mode(card_dict, fs)
    two_keys = _two_color_keys(card_dict) if grad_mode else None

    # Samurai's artifact frame is a borderless variant with a TRANSPARENT
    # rules region — light rules text over arbitrary art is illegible. Route
    # artifact/colorless/land to the opaque gold frame instead.
    if frame_set == 'neoSamurai' and color_key in ('a', 'c', 'l'):
        color_key = 'm'

    # Planeswalker frames live at regular/planeswalkerFrame<K>.png (uppercase);
    # colorless/land fall back to the artifact frame.
    if frame_set == 'planeswalker':
        if color_key in ('c', 'l'):
            color_key = 'a'
        def _pwk(k):
            return f'regular/planeswalkerFrame{k.upper()}' if k in 'wubrgma' else k
        color_key = _pwk(color_key)
        if two_keys:
            two_keys = (_pwk(two_keys[0]), _pwk(two_keys[1]))

    # 8th Edition ships dedicated COLORED LAND frames (wl/ul/bl/rl/gl/ml):
    # lands with a color identity use their color's land variant instead of
    # the plain colored (spell) frame; colorless lands keep 'l'.
    if frame_set == '8th' and 'Land' in (card_dict.get('type_line') or ''):
        _land_colors = card_dict.get('color_identity') or card_dict.get('colors') or []
        if two_keys:
            two_keys = (two_keys[0] + 'l', two_keys[1] + 'l')
        elif len(_land_colors) == 1 and color_key in 'wubrg':
            color_key = color_key + 'l'
        elif len(_land_colors) >= 2:
            color_key = 'ml'

    # Load main frame PNG (750×1050, RGBA — transparent art window)
    frame_img = None
    if two_keys:
        frame_img = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                          blend=grad_mode)
    if frame_img is None:
        frame_img = _load_frame_image(frame_set, color_key)
    if frame_img is None:
        # Fallback to colorless if specific color not found
        frame_img = _load_frame_image(frame_set, 'c')
    if frame_img is None and frame_set in ('abu', 'crystal', 'lotr', 'iko',
                                           'sncArtDeco', 'neoSamurai'):
        # These sets have no colorless frame — fall back to artifact then land.
        # (iko included: colorless non-artifact cards otherwise rendered a
        # fully transparent frame in the Showcase style.)
        frame_img = _load_frame_image(frame_set, 'a') or _load_frame_image(frame_set, 'l')
        # (sncArtDeco/neoSamurai also have no land frame; artifact covers both.)
    if frame_img is None:
        # No frame images available — return empty
        return Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))

    # Resize frame if not already at card dimensions
    if frame_img.size != (CARD_WIDTH, CARD_HEIGHT):
        frame_img = frame_img.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)

    result = frame_img.copy()

    # Ikoria showcase (Godzilla): a tall CREAM rules box drawn over the frame's
    # rules area (the asset's own box is short with a dead black bar below), plus
    # a gold P/T plate. Supersampled 4x for smooth edges; border sampled from the
    # frame's own accent colour (blue for U, gold for M, ...). This is the version
    # the user approved ("much much better").
    if frame_set == 'iko':
        import numpy as np
        co = fs.get('color_overrides', {}) or {}

        def _rgb(hexstr, default):
            if not hexstr:
                return default
            h = hexstr.lstrip('#')
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except (ValueError, IndexError):
                return default

        # Colors > Border recolors the frame's BAKED accent trim too (title
        # and type bar outlines), not just the dynamically drawn rules box —
        # otherwise the bars keep the card color's trim (e.g. blue) and
        # mismatch the overridden rules border. Applied BEFORE the type-bar
        # relocation below so the mask (derived from the unshifted asset)
        # stays aligned.
        _bar_target = _rgb(co.get('border'), None)
        if _bar_target is not None:
            _am = _iko_accent_mask()
            if _am is not None:
                _mask, _shade = _am
                _fa = np.asarray(result, dtype=float)
                _overlay = np.clip(
                    np.array(_bar_target, dtype=float) * _shade[..., None], 0, 255)
                _mm = _mask[..., None]
                _fa[..., :3] = _overlay * _mm + _fa[..., :3] * (1 - _mm)
                result = Image.fromarray(_fa.astype(np.uint8), 'RGBA')

        # Taller text area (user request: parity with the other styles): the
        # asset's baked type bar (y726-804) is relocated 82px up over the art
        # and the cream box extends into the vacated space — text band grows
        # from 196px to ~277px. Same cut-and-move technique as LOTR's bottom
        # mask; works for gradient frames since it operates on the loaded
        # (possibly blended) frame.
        _TYPE_SHIFT = 82
        _strip = result.crop((0, 726, CARD_WIDTH, 804))
        _ra = np.array(result)
        _ra[726:804, :, 3] = 0
        result = Image.fromarray(_ra, 'RGBA')
        result.paste(_strip, (0, 726 - _TYPE_SHIFT), _strip)
        # Accent sampled from BOTH sides of the frame: on a two-color gradient
        # frame the left and right accents differ (e.g. blue|red), and the box
        # border / P/T plate outline must follow — a single left-side sample
        # painted the right-hanging P/T plate the LEFT color.
        accent_l = result.getpixel((54, 850))[:3]
        accent_r = result.getpixel((CARD_WIDTH - 54, 850))[:3]
        # Honor the frame designer's settings, falling back to the approved
        # defaults when nothing is set: Colors > Textbox (box fill), Colors >
        # Border (box border), and the text-box layer opacity (transparency).
        box_fill = _rgb(co.get('textbox'), (245, 239, 225))
        box_border = _rgb(co.get('border'), tuple(accent_l))
        box_border_r = _rgb(co.get('border'), tuple(accent_r))
        # box transparency: 'box_opacity' 0..1 (default ~0.93 = the approved look)
        bop = fs.get('box_opacity')
        box_alpha = int(round(max(0.0, min(1.0, bop)) * 255)) if bop is not None else 236

        # by0 meets the RELOCATED type bar bottom (722) with the same 11px
        # seam overlap the approved design had; by1 keeps the box border
        # ~4mm inside the cut line for print-safe proxy trimming.
        bx0, by0, bx1, by1, rad = 47, 711, 703, 1004, 22
        SS = 4
        big = Image.new('RGBA', (CARD_WIDTH * SS, CARD_HEIGHT * SS), (0, 0, 0, 0))
        bd = ImageDraw.Draw(big)
        R = [bx0 * SS, by0 * SS, bx1 * SS, by1 * SS]
        # clear the frame's own short box + border under our box so it doesn't
        # bleed through as a faint line (only art shows faintly)
        clear = Image.new('L', (CARD_WIDTH * SS, CARD_HEIGHT * SS), 0)
        ImageDraw.Draw(clear).rounded_rectangle(
            [(bx0 + 5) * SS, (by0 + 5) * SS, (bx1 - 5) * SS, (by1 - 5) * SS],
            radius=rad * SS, fill=255)
        cmask = np.array(clear.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)) > 128
        rarr = np.array(result)
        rarr[..., 3] = np.where(cmask, 0, rarr[..., 3])
        result = Image.fromarray(rarr, 'RGBA')
        bd.rounded_rectangle(R, radius=rad * SS, fill=box_fill + (box_alpha,))
        bd.rounded_rectangle(R, radius=rad * SS, outline=(20, 18, 14, 255), width=7 * SS)
        bd.rounded_rectangle(R, radius=rad * SS, outline=box_border + (255,), width=4 * SS)
        if card.power is not None and card.toughness is not None:
            # Offset down-right so the plate hangs off the box's bottom-right
            # corner (like the real showcase stamps) instead of sitting inside
            # it — while keeping 3mm (36px) print-safe margins to the cut line.
            pr = [604 * SS, 966 * SS, 714 * SS, 1014 * SS]
            bd.rounded_rectangle(pr, radius=10 * SS, fill=(30, 24, 16, 240),
                                 outline=box_border + (255,), width=4 * SS)
        if box_border_r != box_border:
            # Two-color frame: repaint the accent strokes in the right-side
            # color and blend left→right with the same card-wide mask as the
            # frame itself, so the box border and P/T plate follow the gradient.
            big_r = Image.new('RGBA', (CARD_WIDTH * SS, CARD_HEIGHT * SS), (0, 0, 0, 0))
            bdr = ImageDraw.Draw(big_r)
            bdr.rounded_rectangle(R, radius=rad * SS, fill=box_fill + (box_alpha,))
            bdr.rounded_rectangle(R, radius=rad * SS, outline=(20, 18, 14, 255), width=7 * SS)
            bdr.rounded_rectangle(R, radius=rad * SS, outline=box_border_r + (255,), width=4 * SS)
            if card.power is not None and card.toughness is not None:
                bdr.rounded_rectangle(pr, radius=10 * SS, fill=(30, 24, 16, 240),
                                      outline=box_border_r + (255,), width=4 * SS)
            band = 0.44 if (grad_mode or 'gradient') == 'gradient' else 0.015
            mask = _horizontal_blend_mask(CARD_WIDTH * SS, CARD_HEIGHT * SS, band)
            big = Image.composite(big_r, big, mask)
        result = Image.alpha_composite(result, big.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS))

    if frame_set == 'abu':
        # ABU colored frames tint the text box per color (green = dark brown wood),
        # which makes black rules text illegible. Real old cards use a light
        # parchment box for every color, so overlay the WHITE frame's light
        # text-box region onto whatever colored border we loaded.
        white = _load_frame_image('abu', 'w')
        if white is not None:
            if white.size != (CARD_WIDTH, CARD_HEIGHT):
                white = white.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
            L = ABU_LAYOUT
            x0, y0 = L['x_margin'] - 16, L['rules_y0'] - 14
            x1, y1 = L['x_right'] + 16, L['rules_y1'] + 14
            box = white.crop((x0, y0, x1, y1))
            result.alpha_composite(box, (x0, y0))

    if frame_set == 'crystal':
        # The asset's scratched-stone rules box is only ~45% opaque — bright art
        # bleeding through washes out the LIGHT rules text (the old Godzilla box
        # defect). Self-composite the frame's own box region (selected by the
        # rules.png mask) to deepen opacity while keeping the stone texture.
        # 'box_opacity' (0..1) sets the target; default 0.84 = the approved look.
        # Floor is the asset's own ~0.45 (can't make the baked box thinner).
        rules_mask = _load_frame_image(frame_set, 'rules')
        if rules_mask is not None:
            if rules_mask.size != (CARD_WIDTH, CARD_HEIGHT):
                rules_mask = rules_mask.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
            a0 = 116 / 255.0  # measured alpha of the asset's stone box
            bop = fs.get('box_opacity')
            target = max(a0, min(0.98, bop)) if bop is not None else 0.84
            # out = k + a0*(1-k) = target  ->  k = (target - a0) / (1 - a0)
            k = max(0.0, min(1.0, (target - a0) / (1.0 - a0)))
            if k > 0:
                box_layer = result.copy()
                box_layer.putalpha(rules_mask.getchannel('A').point(
                    lambda v: int(v * k)))
                result = Image.alpha_composite(result, box_layer)

    if frame_set == 'lotr':
        L = LOTR_LAYOUT
        import numpy as np

        # ── Bottom mask toggle: the asset's black rounded bottom (border.png
        # marks exactly those pixels) is drawn as ONE silhouette with the
        # colored side-border taper, so it cannot be repositioned without
        # breaking that marriage (tried; left an exposed-art seam). Default ON
        # keeps the asset's native geometry untouched; OFF erases the black so
        # art runs to the card bottom (stamp/P/T still composite on top). ──
        if not fs.get('bottom_mask', True):
            bmask = _load_frame_image(frame_set, 'border')
            if bmask is not None:
                if bmask.size != (CARD_WIDTH, CARD_HEIGHT):
                    bmask = bmask.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
                ma = np.array(bmask.getchannel('A'), dtype=np.float32) / 255.0
                rarr = np.array(result)
                rarr[..., 3] = (rarr[..., 3] * (1.0 - ma)).astype('uint8')
                result = Image.fromarray(rarr, 'RGBA')

        def _lotr_piece(subdir, key):
            """Load a lotr sub-asset, gradient-aware for two-color cards."""
            img = None
            if two_keys:
                img = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                            subdir=subdir, blend=grad_mode)
            if img is None:
                img = _load_frame_image(frame_set, f'{subdir}{key}')
            if img is None:  # colorless fallback mirrors the base frame's
                img = (_load_frame_image(frame_set, f'{subdir}a')
                       or _load_frame_image(frame_set, f'{subdir}m'))
            return img

        # Wavy legendary crown across the very top
        if 'Legendary' in (card.type_line or ''):
            crown = _lotr_piece('crown/', color_key)
            if crown is not None:
                crown = crown.resize((CARD_WIDTH, L['crown_h']), Image.Resampling.LANCZOS)
                lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
                lay.paste(crown, (0, 0))
                result = Image.alpha_composite(result, lay)

        # Holo stamp, bottom center (always — per-color foil triangle)
        stamp = _lotr_piece('stamp/', color_key)
        if stamp is not None:
            stamp = stamp.resize((L['stamp_w'], L['stamp_h']), Image.Resampling.LANCZOS)
            lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
            lay.paste(stamp, (L['stamp_x'], L['stamp_y']))
            result = Image.alpha_composite(result, lay)

        # P/T plate at pack bounds (text drawn by _create_lotr_text_svg)
        if card.power is not None and card.toughness is not None:
            pt_img = _lotr_piece('pt/', color_key)
            if pt_img is not None:
                pt_img = pt_img.resize((L['pt_w'], L['pt_h']), Image.Resampling.LANCZOS)
                lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
                lay.paste(pt_img, (L['pt_x'], L['pt_y']))
                result = Image.alpha_composite(result, lay)

    if frame_set == '8th':
        # P/T box at pack bounds. Land variants ('ul' etc.) have no pt assets —
        # use the base color key for the plate.
        if card.power is not None and card.toughness is not None:
            L = EIGHTH_LAYOUT
            base_keys = tuple(k.rstrip('l') or 'l' for k in two_keys) if two_keys else None
            pt_img = None
            if base_keys:
                pt_img = _gradient_frame_image(frame_set, base_keys[0], base_keys[1],
                                               subdir='pt/', blend=grad_mode)
            if pt_img is None:
                pt_img = _load_frame_image(frame_set, f'pt/{color_key.rstrip("l") or "l"}')
            if pt_img is None:
                pt_img = (_load_frame_image(frame_set, 'pt/a')
                          or _load_frame_image(frame_set, 'pt/l'))
            if pt_img is not None:
                pt_img = pt_img.resize((L['pt_w'], L['pt_h']), Image.Resampling.LANCZOS)
                lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
                lay.paste(pt_img, (L['pt_x'], L['pt_y']))
                result = Image.alpha_composite(result, lay)

    if frame_set == 'mysticalArchive':
        L = MSA_LAYOUT

        def _msa_piece(subdir, key):
            img = None
            if two_keys:
                img = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                            subdir=subdir, blend=grad_mode)
            if img is None:
                img = _load_frame_image(frame_set, f'{subdir}{key}')
            if img is None:  # no land assets — colorless 'c' doubles as land
                img = _load_frame_image(frame_set, f'{subdir}c')
            return img

        # Arched crown strip across the top, legendary-gated
        if 'Legendary' in (card.type_line or ''):
            crown = _msa_piece('crowns/', color_key)
            if crown is not None:
                crown = crown.resize((CARD_WIDTH, L['crown_h']), Image.Resampling.LANCZOS)
                lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
                lay.paste(crown, (0, 0))
                result = Image.alpha_composite(result, lay)

        if card.power is not None and card.toughness is not None:
            pt_img = _msa_piece('pt/', color_key)
            if pt_img is not None:
                pt_img = pt_img.resize((L['pt_w'], L['pt_h']), Image.Resampling.LANCZOS)
                lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
                lay.paste(pt_img, (L['pt_x'], L['pt_y']))
                result = Image.alpha_composite(result, lay)

    if frame_set == 'planeswalker':
        # Ability bands: alternating translucent light/dark fills over the
        # frame's transparent ability window (art ghosts through), gradient
        # transition strips at band boundaries, loyalty-cost badge art on
        # the left. Layout shared with the text overlay via
        # _pw_frame_band_layout so chrome and text always agree.
        _pw = _pw_frame_band_layout(card)
        Lp = PW_FRAME_LAYOUT
        # Bands (and their transition strips) composite UNDER the frame: the
        # frame's opaque pixels (borders, baked loyalty plate, bottom bar)
        # mask them cleanly, so bands can extend behind the plate like real
        # cards without ever washing over the frame art.
        under = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        drw = ImageDraw.Draw(under)
        x0, x1 = Lp['band_x0'], Lp['band_x1']
        if _pw is None:
            # Non-planeswalker card in this frame: one light band across the
            # whole ability area so plain rules text stays legible.
            drw.rectangle([x0, Lp['area_y0'], x1, Lp['band_draw_y1']],
                          fill=(255, 255, 255, 224))
        else:
            _font, _line_h, bands = _pw
            for i, (y0, y1, cost, text) in enumerate(bands):
                y1d = Lp['band_draw_y1'] if i == len(bands) - 1 else y1
                fill = (255, 255, 255, 224) if i % 2 == 0 else (203, 203, 203, 224)
                drw.rectangle([x0, round(y0), x1, round(y1d)], fill=fill)
            for i in range(1, len(bands)):
                strip = _load_frame_image(
                    frame_set, 'abilityLineEven' if i % 2 == 0 else 'abilityLineOdd')
                if strip is not None:
                    strip = strip.resize((x1 - x0, 13), Image.Resampling.LANCZOS)
                    under.paste(strip, (x0, round(bands[i][0]) - 6), strip)
        result = Image.alpha_composite(under, result)
        if _pw:
            for (y0, y1, cost, text) in bands:
                if not cost:
                    continue
                icon = _badge_icon_for(cost)
                img = _load_frame_image(frame_set, icon)
                if img is not None:
                    bw = min(74, round(_font * 3.1))  # scales with band font
                    bh = round(img.height * bw / img.width)
                    img = img.resize((bw, bh), Image.Resampling.LANCZOS)
                    lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
                    # anchor the PLATE center (not the image center) on the
                    # band center — arrows extend the bbox asymmetrically
                    plate_cy = _BADGE_GEOM[icon]['cy']
                    lay.paste(img, (56, round((y0 + y1) / 2 - plate_cy * bh)))
                    result = Image.alpha_composite(result, lay)

    # Data-driven overlays (crown/stamp/P/T at pack bounds) for the newer
    # image sets that all share the same structure. 'crown_dir' pieces are
    # legendary-gated; 'stamp' composites always; all gradient-aware.
    _OVERLAY_SETS = {
        'sncArtDeco': {'crown_dir': 'crowns/', 'layout': ARTDECO_LAYOUT},
        'neoSamurai': {'crown_dir': 'crown/', 'layout': SAMURAI_LAYOUT,
                       'stamp_flat': 'stamp'},
        'etched': {'crown_dir': 'crowns/', 'layout': ETCHED_LAYOUT,
                   'stamp_dir': 'holo/'},
    }
    if frame_set in _OVERLAY_SETS:
        spec = _OVERLAY_SETS[frame_set]
        L = spec['layout']

        def _set_piece(subdir, key):
            img = None
            if two_keys:
                img = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                            subdir=subdir, blend=grad_mode)
            if img is None:
                img = _load_frame_image(frame_set, f'{subdir}{key}')
            if img is None:  # per-set gaps (e.g. samurai has no artifact crown)
                img = (_load_frame_image(frame_set, f'{subdir}m')
                       or _load_frame_image(frame_set, f'{subdir}a'))
            return img

        def _paste(img, box):
            x, y, w, h = box
            img = img.resize((w, h), Image.Resampling.LANCZOS)
            lay = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
            lay.paste(img, (x, y))
            return Image.alpha_composite(result, lay)

        if 'crown' in L and 'Legendary' in (card.type_line or ''):
            crown = _set_piece(spec['crown_dir'], color_key)
            if crown is not None:
                result = _paste(crown, L['crown'])
        if 'stamp' in L:
            stamp = (_load_frame_image(frame_set, spec['stamp_flat'])
                     if 'stamp_flat' in spec else _set_piece(spec['stamp_dir'], color_key))
            if stamp is not None:
                result = _paste(stamp, L['stamp'])
        if card.power is not None and card.toughness is not None:
            pt_img = _set_piece('pt/', color_key)
            if pt_img is not None:
                result = _paste(pt_img, (L['pt_x'], L['pt_y'], L['pt_w'], L['pt_h']))

    if frame_set == 'crystal' and 'Legendary' in (card.type_line or ''):
        # Legendary crown of ice shards across the very top (separate per-color
        # strip asset, 1500x107; gradient-aware like the main frame).
        crown = None
        if two_keys:
            crown = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                          subdir='crowns/', blend=grad_mode)
        if crown is None:
            crown = _load_frame_image(frame_set, f'crowns/{color_key}')
        if crown is None:  # no crowns/c — artifact crown reads neutral
            crown = _load_frame_image(frame_set, 'crowns/a')
        if crown is not None:
            ch = CRYSTAL_LAYOUT['crown_h']
            crown = crown.resize((CARD_WIDTH, ch), Image.Resampling.LANCZOS)
            crown_layer = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
            crown_layer.paste(crown, (0, 0))
            result = Image.alpha_composite(result, crown_layer)

    # Composite P/T box overlay for creatures (these sets draw their own above)
    has_pt = (card.power is not None and card.toughness is not None
              and frame_set not in ('lotr', '8th', 'mysticalArchive',
                                    'sncArtDeco', 'neoSamurai', 'etched'))
    if has_pt and frame_set == 'crystal':
        # Crystal's pack defines exact P/T bounds — place the ice box there,
        # no m15-style rescale. Text is drawn by _create_crystal_text_svg.
        pt_img = None
        if two_keys:
            pt_img = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                           subdir='pt/', blend=grad_mode)
        if pt_img is None:
            pt_img = _load_frame_image(frame_set, f'pt/{color_key}')
        if pt_img is None:
            pt_img = _load_frame_image(frame_set, 'pt/c')
        if pt_img is not None:
            L = CRYSTAL_LAYOUT
            pt_resized = pt_img.resize((L['pt_w'], L['pt_h']), Image.Resampling.LANCZOS)
            pt_layer = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
            pt_layer.paste(pt_resized, (L['pt_x'], L['pt_y']))
            result = Image.alpha_composite(result, pt_layer)
    elif has_pt:
        pt_img = None
        if two_keys:
            pt_img = _gradient_frame_image(frame_set, two_keys[0], two_keys[1],
                                           subdir='pt/', blend=grad_mode)
        if pt_img is None:
            pt_img = _load_frame_image(frame_set, f'pt/{color_key}')
        if pt_img is None:
            pt_img = _load_frame_image(frame_set, 'pt/c')  # fallback to colorless
        if pt_img is not None:
            # Scale PT box to match authentic M15 card proportions.
            # Raw PNG is 282×154 (37.6% of card width); real cards ~16%.
            pt_base = CARD_WIDTH / 750.0
            pt_box_scale = 0.42
            pt_w = int(pt_img.width * pt_base * pt_box_scale)
            pt_h = int(pt_img.height * pt_base * pt_box_scale)
            pt_resized = pt_img.resize((pt_w, pt_h), Image.Resampling.LANCZOS)
            # Position at bottom-right, centered on textbox bottom border
            textbox_bottom_px = int(M15_LAYOUT['rules_bottom'] * CARD_HEIGHT / VB_H)
            pt_x = CARD_WIDTH - pt_w - int(32 * pt_base)
            pt_y = textbox_bottom_px - pt_h // 2
            pt_layer = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
            pt_layer.paste(pt_resized, (pt_x, pt_y))
            result = Image.alpha_composite(result, pt_layer)
            # Compute text center from PT box interior center ratio.
            # Measured from gray fill region of isolated scaled PT PNGs.
            # Y ratio adjusted upward to account for baseline offset rendering.
            _icx = pt_x + pt_w * 0.551
            _icy = pt_y + pt_h * 0.48
            fs['_pt_center_x_svg'] = _icx * VB_W / CARD_WIDTH
            fs['_pt_center_y_svg'] = _icy * VB_H / CARD_HEIGHT

    return result


def _render_image_frame(card_dict: dict, card: CardData, fs: dict) -> Image.Image:
    """Render frame using pre-rendered PNG assets + text-only SVG overlay.

    Returns an RGBA image (750×1050) with frame chrome + text, ready to
    composite onto art.
    """
    result = _compose_image_frame_base(card_dict, card, fs)

    # Render text-only SVG and composite on top (each image frame set can carry
    # its own layout/text renderer)
    if fs.get('layout') == 'iko' or fs.get('frame_set') == 'iko':
        text_svg = _create_iko_text_svg(card, fs)
    elif fs.get('layout') == 'crystal' or fs.get('frame_set') == 'crystal':
        text_svg = _create_crystal_text_svg(card, fs)
    elif fs.get('layout') == 'lotr' or fs.get('frame_set') == 'lotr':
        text_svg = _create_lotr_text_svg(card, fs)
    elif fs.get('layout') == '8th' or fs.get('frame_set') == '8th':
        text_svg = _create_8th_text_svg(card, fs)
    elif fs.get('layout') == 'msa' or fs.get('frame_set') == 'mysticalArchive':
        text_svg = _create_msa_text_svg(card, fs)
    elif fs.get('layout') == 'planeswalker' or fs.get('frame_set') == 'planeswalker':
        text_svg = _create_pw_frame_text_svg(card, fs)
    elif fs.get('layout') == 'artdeco' or fs.get('frame_set') == 'sncArtDeco':
        text_svg = _create_artdeco_text_svg(card, fs)
    elif fs.get('layout') == 'samurai' or fs.get('frame_set') == 'neoSamurai':
        text_svg = _create_samurai_text_svg(card, fs)
    elif fs.get('layout') == 'etched' or fs.get('frame_set') == 'etched':
        text_svg = _create_etched_text_svg(card, fs)
    elif fs.get('layout') == 'abu' or fs.get('frame_set') == 'abu':
        text_svg = _create_abu_text_svg(card, fs)
    else:
        text_svg = _create_text_only_svg(card, fs)
    text_png_data = cairosvg.svg2png(
        bytestring=text_svg.encode('utf-8'),
        output_width=CARD_WIDTH,
        output_height=CARD_HEIGHT,
    )
    text_img = Image.open(io.BytesIO(text_png_data)).convert('RGBA')
    result = Image.alpha_composite(result, text_img)

    return result


def render_frame_layer(card_dict: dict, frame_settings: dict) -> bytes:
    """Render just the frame chrome (no art, no text) as transparent PNG bytes.

    For image-mode: returns frame PNG + crown + P/T box overlays.
    For SVG-mode: returns the SVG frame shapes rendered as PNG.
    Used by the WYSIWYG designer for the frame canvas layer.
    """
    fs = frame_settings
    card = _build_card_data(card_dict, fs)

    if fs.get('no_frame'):
        # Empty transparent image
        buf = io.BytesIO()
        Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0)).save(buf, 'PNG')
        return buf.getvalue()

    # Battles: chrome + text render as one rotated layer (the text overlay
    # returns empty for battles)
    if _is_battle(card):
        chrome = _battle_overlay_landscape(card_dict, card, fs).rotate(90, expand=True)
        buf = io.BytesIO()
        chrome.save(buf, 'PNG')
        return buf.getvalue()

    if fs.get('mode') == 'image':
        # Image-based: frame + P/T box (without text), gradient-aware. Shares the
        # exact chrome path with the final composite so the WYSIWYG preview matches.
        result = _compose_image_frame_base(card_dict, card, fs)
        buf = io.BytesIO()
        result.save(buf, 'PNG')
        return buf.getvalue()
    else:
        # SVG-mode: render frame shapes only (create_card_frame_svg renders everything)
        png_data = _render_frame_png(card, fs)
        return png_data


def render_text_overlay(card_dict: dict, frame_settings: dict) -> bytes:
    """Render just the text overlay as transparent PNG bytes.

    Used by the WYSIWYG designer for the text canvas layer.
    Only applicable for image-mode frames that use _create_text_only_svg.
    For SVG-mode, text is integrated into the frame SVG.
    """
    fs = frame_settings
    card = _build_card_data(card_dict, fs)

    # Battles: text is baked into the rotated chrome layer (render_frame_layer)
    if _is_battle(card):
        buf = io.BytesIO()
        Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0)).save(buf, 'PNG')
        return buf.getvalue()

    if fs.get('mode') == 'image':
        if fs.get('layout') == 'iko' or fs.get('frame_set') == 'iko':
            text_svg = _create_iko_text_svg(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        if fs.get('layout') == 'crystal' or fs.get('frame_set') == 'crystal':
            text_svg = _create_crystal_text_svg(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        if fs.get('layout') == 'lotr' or fs.get('frame_set') == 'lotr':
            text_svg = _create_lotr_text_svg(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        if fs.get('layout') == '8th' or fs.get('frame_set') == '8th':
            text_svg = _create_8th_text_svg(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        if fs.get('layout') == 'msa' or fs.get('frame_set') == 'mysticalArchive':
            text_svg = _create_msa_text_svg(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        _new_sets = {'artdeco': _create_artdeco_text_svg, 'sncArtDeco': _create_artdeco_text_svg,
                     'samurai': _create_samurai_text_svg, 'neoSamurai': _create_samurai_text_svg,
                     'etched': _create_etched_text_svg,
                     'planeswalker': _create_pw_frame_text_svg}
        _fn = _new_sets.get(fs.get('layout')) or _new_sets.get(fs.get('frame_set'))
        if _fn:
            text_svg = _fn(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        if fs.get('layout') == 'abu' or fs.get('frame_set') == 'abu':
            text_svg = _create_abu_text_svg(card, fs)
            return cairosvg.svg2png(bytestring=text_svg.encode('utf-8'),
                                    output_width=CARD_WIDTH, output_height=CARD_HEIGHT)
        # Compute dynamic PT center if not already set
        if '_pt_center_x_svg' not in fs and card.power is not None:
            pt_base = CARD_WIDTH / 750.0
            pt_box_scale = 0.42
            pt_w = int(282 * pt_base * pt_box_scale)
            pt_h = int(154 * pt_base * pt_box_scale)
            textbox_bottom_px = int(M15_LAYOUT['rules_bottom'] * CARD_HEIGHT / VB_H)
            pt_x = CARD_WIDTH - pt_w - int(32 * pt_base)
            pt_y = textbox_bottom_px - pt_h // 2
            fs['_pt_center_x_svg'] = (pt_x + pt_w * 0.551) * VB_W / CARD_WIDTH
            fs['_pt_center_y_svg'] = (pt_y + pt_h * 0.48) * VB_H / CARD_HEIGHT
        text_svg = _create_text_only_svg(card, fs)
        text_png_data = cairosvg.svg2png(
            bytestring=text_svg.encode('utf-8'),
            output_width=CARD_WIDTH,
            output_height=CARD_HEIGHT,
        )
        return text_png_data
    else:
        # SVG mode: text is part of the frame render, return empty
        buf = io.BytesIO()
        Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0)).save(buf, 'PNG')
        return buf.getvalue()


# ===========================================================================
# Rotated split cards (Fire // Ice) — two mini cards, rotated to portrait
# ===========================================================================
def composite_split_card(half_dicts, art_paths, output_path,
                         deck_frame_settings: dict = None) -> None:
    """Authentic classic-split composite: each half renders as a normal card
    through the full frame pipeline, scales to half size, and the pair is
    rotated 90° CCW into the standard 750x1050 portrait — matching how real
    split cards are printed (turn the card sideways: left half = first face).

    half_dicts: [left, right] clean card dicts (one half each, no layout).
    art_paths:  [left, right] art file paths; a missing right falls back to
                the left art so the card is never blank.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    half_w, half_h = CARD_HEIGHT // 2, CARD_WIDTH  # 525 x 750 readable minis
    canvas = Image.new('RGBA', (CARD_HEIGHT, CARD_WIDTH))  # 1050 x 750 readable

    left_art = art_paths[0]
    for i, half in enumerate(half_dicts):
        art = art_paths[i] or left_art
        fs = resolve_frame_settings(half, deck_frame_settings)
        png = composite_card_preview(half, art, fs)
        mini = Image.open(io.BytesIO(png)).convert('RGBA')
        mini = mini.resize((half_w, half_h), Image.Resampling.LANCZOS)
        canvas.paste(mini, (i * half_w, 0))

    # Rotate 90° CCW: half titles read bottom-to-top like printed splits
    canvas.rotate(90, expand=True).save(output_path, 'PNG')


# ===========================================================================
# Battle (siege) frame — landscape, rotated into the portrait composite
# ===========================================================================
# Real battles are printed as portrait cards whose content is rotated 90°
# (you turn the card sideways to play it). We render the frame on a landscape
# canvas, composite the art, then rotate 90° CCW so the stored file is a
# standard 750x1050 portrait — the grid, exports, printing, and the edhplay
# extension all keep working unchanged.
BATTLE_W, BATTLE_H = CARD_HEIGHT, CARD_WIDTH  # 1050 x 750 landscape


def _is_battle(card: CardData) -> bool:
    return 'battle' in (card.type_line or '').lower()


def _battle_defense_shield_svg(defense: str, cx: float, cy: float,
                               size: float = 96) -> list:
    """Defense shield (heater-shield shape) with the defense number."""
    w = size
    h = size * 1.08
    x0, y0 = cx - w / 2, cy - h / 2
    # Heater shield: flat top, curved sides meeting in a bottom point
    path = (f"M {x0} {y0} L {x0 + w} {y0} "
            f"C {x0 + w} {y0 + h * 0.52} {x0 + w * 0.78} {y0 + h * 0.82} {cx} {y0 + h} "
            f"C {x0 + w * 0.22} {y0 + h * 0.82} {x0} {y0 + h * 0.52} {x0} {y0} Z")
    font = size * 0.46
    return [
        f'<path d="{path}" transform="translate(3,3)" fill="rgba(0,0,0,0.45)"/>',
        f'<path d="{path}" fill="#1a1410" stroke="#cfd2d6" stroke-width="4"/>',
        f'<text x="{cx}" y="{cy + h * 0.02 + font * 0.35}" text-anchor="middle" '
        f'font-family="{PT_FONT_FAMILY}" font-size="{font:.0f}" font-weight="bold" '
        f'fill="white">{defense}</text>',
    ]


def _battle_text_svg_parts(card: CardData, fs: dict, BL: dict,
                           bar_text: str, rules_col: str) -> list:
    """Title/type/rules text + defense shield for the landscape battle frame.

    BL: battle-space layout (title_y0/y1, type_y0/y1, rules_y0/y1,
    x_margin, x_right) in the 1050x750 landscape canvas."""
    parts = []
    tx, xr = BL['x_margin'], BL['x_right']

    # ── Title: front-face name + mana pips (real battles title the front) ──
    mana_pips = parse_mana_cost(card.mana_cost)
    pip_w = len(mana_pips) * (MANA_PIP_SIZE + MANA_PIP_GAP) + 14 if mana_pips else 0
    disp_name = card.name.split(' // ')[0]
    esc_name = disp_name.replace('&', '&amp;').replace('<', '&lt;')
    nf = 40
    name_avail = (xr - tx) - pip_w
    if len(disp_name) * nf * 0.58 > name_avail and name_avail > 0:
        nf = max(22, int(nf * name_avail / (len(disp_name) * nf * 0.58)))
    ncy = (BL['title_y0'] + BL['title_y1']) / 2 + nf * 0.35
    parts.append(f'<text x="{tx}" y="{ncy}" font-family="{NAME_FONT_FAMILY}" '
                 f'font-size="{nf}" font-weight="bold" fill="{bar_text}">{esc_name}</text>')
    if mana_pips:
        pcy = (BL['title_y0'] + BL['title_y1']) / 2
        px = xr + 4
        for pip in reversed(mana_pips):
            pxx = px - MANA_PIP_SIZE
            parts.append(f'<circle cx="{pxx + MANA_PIP_SIZE/2 + 0.5}" cy="{pcy + 1}" '
                         f'r="{MANA_PIP_SIZE/2}" fill="rgba(0,0,0,0.3)"/>')
            parts.append(_pip_image_tag(pip, pxx, pcy - MANA_PIP_SIZE / 2, MANA_PIP_SIZE))
            px -= (MANA_PIP_SIZE + MANA_PIP_GAP)

    # ── Type line ──
    esc_type = (card.type_line or '').replace('&', '&amp;').replace('<', '&lt;')
    tf = 32
    type_avail = (xr - tx) - 10
    if len(card.type_line or '') * tf * 0.50 > type_avail and type_avail > 0:
        tf = max(18, int(tf * type_avail / (len(card.type_line) * tf * 0.50)))
    tcy = (BL['type_y0'] + BL['type_y1']) / 2 + tf * 0.35
    parts.append(f'<text x="{tx}" y="{tcy}" font-family="{TYPE_FONT_FAMILY}" '
                 f'font-size="{tf}" font-weight="bold" fill="{bar_text}">{esc_type}</text>')

    # ── Rules text, wrapping around the defense shield bottom-right ──
    shield_size = 92
    if card.oracle_text:
        rb_x = tx
        rb_w = xr - tx
        rb_top = BL['rules_y0'] + 12
        rb_h = (BL['rules_y1'] - 4) - rb_top
        avoid = None
        if card.defense is not None:
            avoid = (BL['rules_y1'] - shield_size * 1.08 - 14,
                     rb_w - shield_size - 22)
        desired = int(fs.get('rules_font_size') or RULES_FONT)

        def _msr(f):
            av = ((avoid[0] - (rb_top + f * 0.8), avoid[1]) if avoid else None)
            return _measure_rules_text(card.oracle_text, rb_w, f,
                                       int(RULES_LINE_H * f / RULES_FONT), avoid=av)

        r_font = _max_fitting_rules_font(_msr, rb_h, desired)
        r_line = int(RULES_LINE_H * r_font / RULES_FONT)
        if _msr(r_font) > rb_h + 2:  # only possible at the hard floor
            fs.setdefault('_quality', []).append(
                f'rules_overflow: battle needs {_msr(r_font):.0f}px but box is {rb_h:.0f}px (font {r_font})')
        rules_lines, _ = render_rules_text_svg(
            card.oracle_text, rb_x, rb_top + r_font * 0.8, rb_w, rb_h,
            r_font, r_line, text_color=rules_col, avoid=avoid)
        parts.extend(rules_lines)

    if card.defense is not None:
        parts.extend(_battle_defense_shield_svg(
            str(card.defense), xr - 40, BL['rules_y1'] - 54, size=shield_size))
    return parts


def _create_battle_frame_svg(card: CardData, fs: dict) -> str:
    """Dedicated landscape battle chrome (used by the SVG-mode styles and as
    the fallback when a style has no sliceable assets): full-bleed art with a
    title bar on top and a full-width type bar + rules band at the bottom."""
    W, H = BATTLE_W, BATTLE_H
    theme = get_color_theme(card)
    _ovr = (fs.get('color_overrides', {}) or {}).get('text')
    bar_text = _ovr or '#f4f2ec'
    rules_col = _ovr or '#141210'

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
    if _FONT_FACE_CSS:
        svg.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')

    # Black border ring with a theme pinline
    svg.append(f'<rect x="0" y="0" width="{W}" height="{H}" rx="34" fill="none" '
               f'stroke="#0b0b0c" stroke-width="52"/>')
    svg.append(f'<rect x="27" y="27" width="{W - 54}" height="{H - 54}" rx="18" '
               f'fill="none" stroke="{theme["bg"]}" stroke-width="5"/>')

    # Title bar chrome
    tb_x, tb_y0, tb_y1 = 44, 40, 100
    svg.append(f'<rect x="{tb_x}" y="{tb_y0}" width="{W - 2 * tb_x}" height="{tb_y1 - tb_y0}" '
               f'rx="14" fill="rgba(12,12,14,0.88)" stroke="{theme["border"]}" stroke-width="2.5"/>')

    # Bottom band chrome: full-width type bar + wide rules box
    pn_x0, pn_x1 = 44, W - 44
    pn_y0, pn_y1 = 430, H - 40
    ty_h = 50
    svg.append(f'<rect x="{pn_x0}" y="{pn_y0}" width="{pn_x1 - pn_x0}" height="{pn_y1 - pn_y0}" '
               f'rx="12" fill="{hex_with_alpha(theme["textbox"], 0.93)}" '
               f'stroke="{theme["border"]}" stroke-width="2.5"/>')
    svg.append(f'<rect x="{pn_x0}" y="{pn_y0}" width="{pn_x1 - pn_x0}" height="{ty_h}" '
               f'rx="12" fill="rgba(12,12,14,0.88)"/>')

    BL = {'title_y0': tb_y0, 'title_y1': tb_y1,
          'type_y0': pn_y0, 'type_y1': pn_y0 + ty_h,
          'rules_y0': pn_y0 + ty_h + 2, 'rules_y1': pn_y1 - 12,
          'x_margin': tb_x + 22, 'x_right': pn_x1 - 22}
    svg.extend(_battle_text_svg_parts(card, fs, BL, bar_text, rules_col))

    svg.append('</svg>')
    return '\n'.join(svg)


# Per-style band metadata for slicing each style's PORTRAIT frame assets into
# the landscape battle geometry. Bands are pixel coords in the 750x1050 frame
# PNGs; text colors of None fall back to the theme text color (m15).
_BATTLE_STYLE_BANDS = {
    'm15': ({'title_y0': 53, 'title_y1': 107, 'type_y0': 585, 'type_y1': 647,
             'rules_y0': 653, 'rules_y1': 973, 'x_margin': 58, 'x_right': 692},
            None, None),
    'iko': ({'title_y0': 43, 'title_y1': 117, 'type_y0': 644, 'type_y1': 721,
             'rules_y0': 719, 'rules_y1': 996, 'x_margin': 62, 'x_right': 690},
            '#f6f1e6', '#1a1712'),
    'crystal': ({'title_y0': 53, 'title_y1': 110, 'type_y0': 594, 'type_y1': 648,
                 'rules_y0': 671, 'rules_y1': 957, 'x_margin': 64, 'x_right': 690,
                 'cap_extra': 80},
                '#f2f3f5', '#f2f3f5'),
    'lotr': ({'title_y0': 56, 'title_y1': 117, 'type_y0': 590, 'type_y1': 650,
              'rules_y0': 662, 'rules_y1': 940, 'x_margin': 64, 'x_right': 690},
             '#f6f1e6', '#1a1712'),
    'abu': ({'title_y0': 34, 'title_y1': 100, 'type_y0': 574, 'type_y1': 618,
             'rules_y0': 628, 'rules_y1': 992, 'x_margin': 62, 'x_right': 688},
            '#1a1712', '#1a1712'),
    '8th': ({'title_y0': 58, 'title_y1': 118, 'type_y0': 590, 'type_y1': 647,
             'rules_y0': 659, 'rules_y1': 938, 'x_margin': 75, 'x_right': 680},
            '#1a1712', '#1a1712'),
    'mysticalArchive': ({'title_y0': 55, 'title_y1': 112, 'type_y0': 595, 'type_y1': 652,
                         'rules_y0': 662, 'rules_y1': 955, 'x_margin': 70, 'x_right': 680,
                         'cap_extra': 60},
                        '#211a10', '#211a10'),
    'sncArtDeco': ({'title_y0': 55, 'title_y1': 112, 'type_y0': 595, 'type_y1': 652,
                    'rules_y0': 662, 'rules_y1': 946, 'x_margin': 64, 'x_right': 690},
                   '#f6f1e6', '#1a1712'),
    'neoSamurai': ({'title_y0': 72, 'title_y1': 129, 'type_y0': 595, 'type_y1': 652,
                    'rules_y0': 662, 'rules_y1': 946, 'x_margin': 64, 'x_right': 690},
                   '#f2f3f5', '#f2f3f5'),
    'etched': ({'title_y0': 55, 'title_y1': 112, 'type_y0': 595, 'type_y1': 652,
                'rules_y0': 662, 'rules_y1': 955, 'x_margin': 70, 'x_right': 685},
               '#f2f3f5', '#f2f3f5'),
}

_BATTLE_STYLE_ALIASES = {'msa': 'mysticalArchive', 'artdeco': 'sncArtDeco',
                         'samurai': 'neoSamurai'}


def _battle_frame_dir(fs: dict):
    """Resolve the frame-asset directory for a battle, or None when the
    style has no band metadata (SVG styles, planeswalker, unknown) — those
    fall back to the dedicated battle chrome. m15 band coordinates are only
    used for the actual default m15 frame, never to slice another style's
    very different chrome."""
    v = fs.get('frame_set') or fs.get('layout') or ''
    v = _BATTLE_STYLE_ALIASES.get(v, v)
    if v in _BATTLE_STYLE_BANDS:
        return v
    if fs.get('mode') == 'image' and not v:
        return 'm15'  # image mode with no explicit frame set = m15 default
    return None


def _hslice_to(img: Image.Image, out_w: int, cap_l: int, cap_r: int) -> Image.Image:
    """Resize img horizontally to out_w, preserving the left/right end caps
    (borders, bar caps) and stretching only the middle."""
    w, h = img.size
    mid_w = max(1, out_w - cap_l - cap_r)
    out = Image.new('RGBA', (out_w, h))
    out.paste(img.crop((cap_l, 0, w - cap_r, h)).resize((mid_w, h), Image.Resampling.LANCZOS),
              (cap_l, 0))
    out.paste(img.crop((0, 0, cap_l, h)), (0, 0))
    out.paste(img.crop((w - cap_r, 0, w, h)), (out_w - cap_r, 0))
    return out


def _compose_battle_frame_from_style(card_dict: dict, card: CardData, fs: dict):
    """Slice a style's portrait frame PNG into landscape battle chrome.

    Vertical recomposition: [top border + title bar] unscaled, [art-window
    side borders] stretched to fill, [type bar] unscaled, [rules texture]
    stretched, [bottom border] unscaled — each band horizontally 3-sliced so
    corners and bar end caps keep their shapes.

    Returns (chrome RGBA 1050x750, battle-space layout dict, bar_col,
    rules_col) or None when the style has no sliceable assets.
    """
    frame_dir = _battle_frame_dir(fs)
    if frame_dir is None:
        return None
    L, bar_col, rules_col = _BATTLE_STYLE_BANDS[frame_dir]
    # The layout bands are measured against the COMPOSITED portrait chrome
    # (crowns applied, bars relocated, box textures merged) — slice that,
    # not the raw color PNG.
    try:
        frame = _compose_image_frame_base(card_dict, card, fs)
    except Exception:
        return None
    if frame is None:
        return None

    W, H = BATTLE_W, BATTLE_H
    pw, ph = frame.size
    pad = 8
    extra = L.get('cap_extra', 16)
    cap_l = L['x_margin'] + extra
    cap_r = (pw - L['x_right']) + extra

    top_h = L['title_y1'] + pad
    type_h = (L['type_y1'] - L['type_y0']) + 2 * pad
    bottom_h = ph - L['rules_y1']
    bot_target = 335
    rules_target = max(40, bot_target - type_h - bottom_h)
    mid_target = H - top_h - bot_target

    top = _hslice_to(frame.crop((0, 0, pw, top_h)), W, cap_l, cap_r)
    mid = frame.crop((0, top_h, pw, L['type_y0'] - pad))
    mid = mid.resize((pw, max(1, mid_target)), Image.Resampling.LANCZOS)
    mid = _hslice_to(mid, W, cap_l, cap_r)
    typebar = _hslice_to(frame.crop((0, L['type_y0'] - pad, pw, L['type_y1'] + pad)),
                         W, cap_l, cap_r)
    rules = frame.crop((0, L['type_y1'] + pad, pw, L['rules_y1']))
    rules = rules.resize((pw, rules_target), Image.Resampling.LANCZOS)
    rules = _hslice_to(rules, W, cap_l, cap_r)
    bottom = _hslice_to(frame.crop((0, L['rules_y1'], pw, ph)), W, cap_l, cap_r)

    chrome = Image.new('RGBA', (W, H))
    y = 0
    for band in (top, mid, typebar, rules, bottom):
        chrome.paste(band, (0, y))
        y += band.size[1]

    bot_top = top_h + mid_target
    BL = {
        'title_y0': L['title_y0'], 'title_y1': L['title_y1'],
        'type_y0': bot_top + pad, 'type_y1': bot_top + type_h - pad,
        'rules_y0': bot_top + type_h, 'rules_y1': bot_top + type_h + rules_target,
        'x_margin': L['x_margin'], 'x_right': W - (pw - L['x_right']),
    }
    return chrome, BL, bar_col, rules_col


def _battle_overlay_landscape(card_dict: dict, card: CardData, fs: dict) -> Image.Image:
    """Full battle chrome + text as a landscape RGBA overlay (no art)."""
    if fs.get('mode') == 'image':
        res = _compose_battle_frame_from_style(card_dict, card, fs)
        if res is not None:
            chrome, BL, bar_col, rules_col = res
            theme = get_color_theme(card)
            _ovr = (fs.get('color_overrides', {}) or {}).get('text')
            bar_col = _ovr or bar_col or theme['text']
            rules_col = _ovr or rules_col or theme['text']
            parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                     f'<svg width="{BATTLE_W}" height="{BATTLE_H}" '
                     f'viewBox="0 0 {BATTLE_W} {BATTLE_H}" '
                     f'xmlns="http://www.w3.org/2000/svg" '
                     f'xmlns:xlink="http://www.w3.org/1999/xlink">']
            if _FONT_FACE_CSS:
                parts.append(f'<style type="text/css">\n{_FONT_FACE_CSS}\n</style>')
            parts.extend(_battle_text_svg_parts(card, fs, BL, bar_col, rules_col))
            parts.append('</svg>')
            png = cairosvg.svg2png(bytestring='\n'.join(parts).encode('utf-8'),
                                   output_width=BATTLE_W, output_height=BATTLE_H)
            text_img = Image.open(io.BytesIO(png)).convert('RGBA')
            return Image.alpha_composite(chrome, text_img)

    svg_content = _create_battle_frame_svg(card, fs)
    png = cairosvg.svg2png(bytestring=svg_content.encode('utf-8'),
                           output_width=BATTLE_W, output_height=BATTLE_H)
    return Image.open(io.BytesIO(png)).convert('RGBA')


def _render_battle_composite(card_dict: dict, card: CardData, fs: dict,
                             art: Image.Image) -> Image.Image:
    """Battle front: landscape art + battle chrome, rotated to portrait."""
    art_offset = fs.get('art_offset', {})
    art_zoom = fs.get('art_zoom', 1.0)
    if art_offset or art_zoom != 1.0:
        art_l = _cover_crop_with_offset(art, BATTLE_W, BATTLE_H,
                                        offset_x=art_offset.get('x', 0),
                                        offset_y=art_offset.get('y', 0),
                                        zoom=art_zoom)
    else:
        art_l = _cover_crop(art, BATTLE_W, BATTLE_H)

    canvas = Image.new('RGBA', (BATTLE_W, BATTLE_H))
    canvas.paste(art_l, (0, 0))

    if not fs.get('no_frame'):
        canvas = Image.alpha_composite(canvas, _battle_overlay_landscape(card_dict, card, fs))

    # Rotate 90° CCW: the title ends up reading bottom-to-top along the left
    # edge, exactly like a printed battle card.
    return canvas.rotate(90, expand=True)


def composite_card(card_dict: dict, art_path, frame_path_or_none, output_path,
                   deck_frame_settings: dict = None) -> None:
    """Composite art image with card frame overlay.

    Supports two modes:
    - 'svg': Renders frame from SVG (programmatic shapes, filters, text)
    - 'image': Uses pre-rendered PNG frame + text-only SVG overlay

    Card Back entries skip the frame entirely — just the art resized to card dimensions.
    deck_frame_settings: optional deck-level frame settings for preset/color/alpha overrides.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve frame settings first (needed for art_offset/art_zoom)
    fs = resolve_frame_settings(card_dict, deck_frame_settings)

    # Load art, cover-crop to card dimensions (with user offset/zoom)
    art = Image.open(art_path).convert('RGB')

    # Battles (sieges): landscape frame rotated into the portrait composite
    if 'battle' in (card_dict.get('type_line') or '').lower():
        card = _build_card_data(card_dict, fs)
        _render_battle_composite(card_dict, card, fs, art).save(output_path, 'PNG')
        return

    art_offset = fs.get('art_offset', {})
    art_zoom = fs.get('art_zoom', 1.0)
    if art_offset or art_zoom != 1.0:
        art_resized = _cover_crop_with_offset(
            art, CARD_WIDTH, CARD_HEIGHT,
            offset_x=art_offset.get('x', 0), offset_y=art_offset.get('y', 0),
            zoom=art_zoom)
    else:
        art_resized = _cover_crop(art, CARD_WIDTH, CARD_HEIGHT)

    # Card Back: pure art, no frame overlay
    if card_dict.get('type_line') == 'Card Back' or (card_dict.get('name') or '').lower().startswith('card back'):
        art_resized.save(output_path, 'PNG')
        return

    card = _build_card_data(card_dict, fs)

    # Frameless preset: just save art, no frame overlay
    if fs.get('no_frame'):
        art_resized.save(output_path, 'PNG')
        return

    art_rgba = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT))
    art_rgba.paste(art_resized, (0, 0))

    if fs.get('mode') == 'image':
        # Image-based: pre-rendered frame PNG + text SVG
        frame = _render_image_frame(card_dict, card, fs)
    else:
        # SVG-based: programmatic frame rendering
        png_data = _render_frame_png(card, fs)
        frame = Image.open(io.BytesIO(png_data)).convert('RGBA')

    composite = Image.alpha_composite(art_rgba, frame)
    composite.save(output_path, 'PNG')


def composite_card_preview(card_dict: dict, art_path, frame_settings: dict) -> bytes:
    """Render a preview composite and return PNG bytes (no disk write).

    Used by the live preview endpoint. frame_settings is already fully resolved.
    """
    art = Image.open(art_path).convert('RGB')

    # Battles preview exactly like the final composite: landscape → rotated
    if 'battle' in (card_dict.get('type_line') or '').lower():
        card = _build_card_data(card_dict, frame_settings)
        buf = io.BytesIO()
        _render_battle_composite(card_dict, card, frame_settings, art).save(buf, 'PNG')
        return buf.getvalue()

    art_offset = frame_settings.get('art_offset', {})
    art_zoom = frame_settings.get('art_zoom', 1.0)
    if art_offset or art_zoom != 1.0:
        art_resized = _cover_crop_with_offset(
            art, CARD_WIDTH, CARD_HEIGHT,
            offset_x=art_offset.get('x', 0), offset_y=art_offset.get('y', 0),
            zoom=art_zoom)
    else:
        art_resized = _cover_crop(art, CARD_WIDTH, CARD_HEIGHT)

    if frame_settings.get('no_frame'):
        buf = io.BytesIO()
        art_resized.save(buf, 'PNG')
        return buf.getvalue()

    card = _build_card_data(card_dict, frame_settings)

    art_rgba = Image.new('RGBA', (CARD_WIDTH, CARD_HEIGHT))
    art_rgba.paste(art_resized, (0, 0))

    if frame_settings.get('mode') == 'image':
        frame = _render_image_frame(card_dict, card, frame_settings)
    else:
        png_data = _render_frame_png(card, frame_settings)
        frame = Image.open(io.BytesIO(png_data)).convert('RGBA')

    composite = Image.alpha_composite(art_rgba, frame)
    buf = io.BytesIO()
    composite.save(buf, 'PNG')
    return buf.getvalue()
