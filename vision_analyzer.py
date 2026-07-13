#!/usr/bin/env python3
"""
Vision-based style analysis for inspiration images.

Uses an MLX vision model (Qwen2.5-VL via mlx-vlm) to analyze an uploaded
inspiration image and extract a structured art style description that drives
prompt generation.
"""

from pathlib import Path


def build_flux_style_descriptors(image_path, style_source: str = '',
                                 backend: str = 'local',
                                 vision_model: str = 'llava:7b',
                                 text_model: str = 'llama3.1:8b') -> str:
    """Derive FLUX-ready style descriptors, image-first and source-agnostic.

    This is the general, *dynamically determined* style path: the vision model
    looks at the actual inspiration image and writes FLUX-friendly style
    descriptors (medium, composition, palette, lighting, mood, technique) — so it
    works for ANY uploaded style, named or not.

    If `style_source` is provided, a second LLM pass reconciles the image read
    with the model's knowledge of that named style — trusting the named style's
    medium and signature techniques (which fixes vision mislabels, e.g. tagging a
    live-action film still as "digital painting") while keeping the image's own
    palette and mood.

    Returns one comma-separated descriptor line, or '' on failure.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        return ''

    vlm_prompt = (
        "Describe ONLY the VISUAL STYLE of this image (ignore the subject/content) "
        "as 10-16 comma-separated descriptors for a text-to-image model. Cover, in "
        "order: medium (be precise — photograph, live-action film still, oil "
        "painting, watercolor, 3D render, cel animation, ink illustration, pixel "
        "art, etc.), composition and framing, color palette (name the actual hues), "
        "lighting, mood, and signature technique. Use concrete multi-word phrases, "
        "not single vague words. Output ONLY the comma-separated descriptors."
    )
    try:
        import mlx_llm
        img_desc = mlx_llm.vision(str(image_path), vlm_prompt, model=vision_model,
                                  max_tokens=160, temperature=0.2)
    except Exception as e:
        print(f"  [style] VLM style read failed: {e}")
        img_desc = ''
    img_desc = (img_desc or '').strip().splitlines()
    img_desc = img_desc[0].strip() if img_desc else ''

    if not style_source:
        return _clean_descriptors(img_desc, style_source)

    # Reconcile the image read with knowledge of the named style.
    system_msg = (
        "You produce style descriptors for the FLUX text-to-image model. You are "
        "given (a) a visual-style read of reference images and (b) the NAME of the "
        "intended style. Output ONE line of 10-16 comma-separated descriptors that "
        "best reproduce the intended style.\n"
        "Trust the NAMED style's true medium and signature techniques — use your "
        "knowledge to CORRECT any medium error in the image read (e.g. a live-action "
        "film still wrongly called 'digital painting'). Keep the specific color "
        "palette and mood from the image read. Use concrete multi-word phrases. "
        "Describe ONLY visual style — no subject, no proper nouns, no character or "
        "place names. Output ONLY the comma-separated descriptors."
    )
    user_msg = (f"Image read: {img_desc or '(none)'}\n"
                f"Intended style name: {style_source}\nDescriptors:")
    try:
        import mlx_llm
        out = mlx_llm.chat(
            messages=[{'role': 'system', 'content': system_msg},
                      {'role': 'user', 'content': user_msg}],
            model=text_model, max_tokens=140, temperature=0.2,
        )
    except Exception as e:
        print(f"  [style] descriptor reconcile failed: {e}")
        out = img_desc
    return _ensure_medium_floor(_clean_descriptors(out, style_source),
                                style_source)


def _clean_descriptors(text: str, style_source: str = '',
                       max_descriptors: int = 16) -> str:
    """Tidy a descriptor line: first line only, strip source-name leakage + labels,
    then de-duplicate repeated descriptors and cap the count.

    Small VLM/LLM models sometimes fall into a repetition loop and emit the same
    phrase over and over (e.g. ``soft focus, muted pastel hues, subtle gradient
    effects`` fifteen times). Left in ``flux_style_prompt`` this both poisons the
    style signal — every card inherits a runaway descriptor — and drowns out the
    subject. So after the label/source cleanup we split on commas, drop exact
    repeats (case-insensitive, order-preserving) and keep at most
    ``max_descriptors`` unique phrases. This is the single choke point every
    descriptor line flows through, so the guard covers both the image-only and
    reconciled paths.
    """
    import re as _re
    lines = (text or '').strip().splitlines()
    if not lines:
        return ''
    out = lines[0].strip()
    # Drop a leading "Descriptors:"/"Style:" label if the model echoed it.
    out = _re.sub(r'^\s*(descriptors|style|visual style)\s*:\s*', '', out, flags=_re.IGNORECASE)
    # Strip the source name if it leaked into the values.
    if style_source:
        for word in style_source.split():
            if len(word) > 3:
                out = _re.sub(r'\b' + _re.escape(word) + r'\b', '', out, flags=_re.IGNORECASE)
    # Split into descriptors, de-duplicate (case-insensitive, first-seen order),
    # and cap the count — collapses any model repetition loop to its unique set.
    seen = set()
    unique = []
    for part in out.split(','):
        phrase = _re.sub(r'\s{2,}', ' ', part).strip(' .')
        # strip orphaned leading conjunctions left by label/source stripping
        phrase = _re.sub(r'^(?:and|or|with|the)\s+', '', phrase,
                         flags=_re.IGNORECASE).strip()
        if not phrase:
            continue
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(phrase)
        if len(unique) >= max_descriptors:
            break
    return ', '.join(_reorder_descriptors(unique))


# ---------------------------------------------------------------------------
# Canonical descriptor ordering — determinism where it matters most.
#
# FLUX weights early tokens heavily. Two distill rolls with nearly IDENTICAL
# vocabulary render very differently depending on ORDER: a roll that buries
# "3D render, cel animation" behind thirteen mood words renders visibly weaker
# than one that leads with them (observed side-by-side on the same deck). The
# model's output order is a dice roll; this sort is the house rule. Any roll's
# vocabulary is emitted in a fixed category order — medium first, then
# linework/shading, palette, mood/theme, composition framing last — with the
# original order preserved WITHIN each category. Same words in, same order
# out, every time.
# ---------------------------------------------------------------------------
_ORDER_MEDIUM = (
    'render', 'animation', 'anime', 'cartoon', 'cel ', 'ink', 'illustration',
    'watercolor', 'watercolour', 'gouache', 'painting', 'painterly',
    'photograph', 'photo', 'pixel', 'comic', 'manga', 'woodblock', 'etching',
    'engraving', 'sketch', 'drawing', 'cgi', 'claymation', 'stop-motion',
    'digital art', 'concept art',
)
_ORDER_LINE_SURFACE = (
    'line', 'outline', 'linework', 'shading', 'shaded', 'hatch', 'stipple',
    'brush', 'stroke', 'texture', 'contrast', 'edge', 'flat color', 'flat fill',
    'cel-shaded', 'gradient', 'matte', 'glossy', 'halftone', 'impasto',
)
_ORDER_COLOR = (
    'palette', 'hue', 'tone', 'pastel', 'neon', 'saturat', 'muted', 'vibrant',
    'vivid', 'colorful', 'colourful', 'monochrom', 'bright', 'tint', 'duotone',
    'red', 'orange', 'yellow', 'green', 'blue', 'teal', 'purple', 'pink',
    'magenta', 'coral', 'beige', 'cream', 'maroon', 'turquoise', 'lavender',
)
_ORDER_COMPOSITION = (
    'close-up', 'closeup', 'wide shot', 'framing', 'composition', 'angle',
    'view', 'panoram', 'perspective', 'symmetr', 'centered', 'isometric',
    'shot', 'crop',
)


# Deterministic medium floor. An analysis roll sometimes emits pure theme/mood
# vocabulary with NO medium term at all ("gritty, dark humor, retro-futuristic")
# — ordering can't fix words that aren't there, and a medium-less style prompt
# renders off-style. When the roll lacks any medium-rank descriptor and the
# style name matches a known category, these canonical terms are PREPENDED.
# Keyword-mapped only — no LLM in the loop, so the floor never varies.
_MEDIUM_FLOOR = {
    ('morty', 'cartoon', 'animated', 'animation', 'anime', 'ghibli',
     'spongebob', 'simpsons', 'futurama', 'disney', 'looney'):
        ['cel animation', 'cartoonish'],
    ('ligne', 'claire', 'ink', 'moebius', 'ngai', 'woodblock', 'ukiyo',
     'tintin', 'linework', 'illustration'):
        ['ink illustration', 'clean linework'],
    ('pixar', '3d', 'cgi', 'render'): ['3D render'],
    ('photo', 'photograph', 'film', 'cinematic', 'noir'):
        ['cinematic photograph'],
    ('watercolor', 'watercolour', 'gouache'): ['watercolor painting'],
    ('oil', 'impressionist', 'baroque', 'rembrandt', 'renaissance'):
        ['oil painting'],
    ('comic', 'manga'): ['comic book art'],
    ('pixel', '8-bit', '16-bit'): ['pixel art'],
}


def _ensure_medium_floor(line: str, style_source: str) -> str:
    """Prepend whichever canonical medium terms are missing when the style name
    deterministically maps to a known medium. Every distill of a given deck
    therefore shares an identical, known-good opening ("cel animation,
    cartoonish, ...") regardless of what the analysis roll emitted."""
    if not line or not style_source:
        return line
    tokens = set(style_source.lower().replace('&', ' ').replace('-', ' ').split())
    for keys, floor in _MEDIUM_FLOOR.items():
        if tokens & set(keys):
            floor_lower = {t.lower() for t in floor}
            rest = [p.strip() for p in line.split(',')
                    if p.strip() and p.strip().lower() not in floor_lower]
            return ', '.join(floor + rest)
    return line


def _descriptor_rank(phrase: str) -> int:
    p = ' ' + phrase.lower() + ' '
    if any(k in p for k in _ORDER_COMPOSITION):
        return 4
    if any(k in p for k in _ORDER_MEDIUM):
        return 0
    if any(k in p for k in _ORDER_LINE_SURFACE):
        return 1
    if any(k in p for k in _ORDER_COLOR):
        return 2
    return 3          # mood / energy / theme


def _reorder_descriptors(phrases: list) -> list:
    """Stable-sort descriptors into canonical category order (see above)."""
    return sorted(phrases, key=lambda p: _descriptor_rank(p))


def analyze_inspiration_style(image_path: str | Path, openai_client=None,
                               backend: str = 'openai',
                               local_model: str = 'llava:7b') -> str:
    """Analyze an inspiration image's art style.

    Supports both OpenAI GPT-4o vision (cloud) and Ollama vision models (local).

    Returns a 2-3 sentence style description suitable for use in
    art generation prompts. Focuses on visual characteristics:
    colors, composition, medium, line work, mood, texture.

    Falls back to empty string if analysis fails.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        print(f"[vision] Inspiration image not found: {image_path}")
        return ""

    system_msg = (
        "You are an art style analyst. Your job is to describe the EXACT visual "
        "rendering technique of an image with ruthless accuracy.\n\n"
        "OBSERVATION FIRST: Before writing anything, study the image carefully. "
        "Ask yourself:\n"
        "- What MEDIUM created this? (oil paint, watercolor, digital painting, "
        "photograph, 3D render, pencil, ink, pixel art, etc.)\n"
        "- How are EDGES formed? (painted transitions, drawn outlines, photographic "
        "depth of field, hard silhouettes, etc.)\n"
        "- How is DEPTH achieved? (atmospheric perspective, layered glazing, "
        "chiaroscuro, flat graphic space, etc.)\n"
        "- What does the SURFACE look like? (brushstrokes, smooth blending, "
        "canvas texture, clean vectors, film grain, etc.)\n\n"
        "Your answers must describe ONLY what is visually present. Art styles are "
        "infinitely varied — do NOT force the image into a category. A detailed "
        "digital painting is NOT cel-shaded. A photograph is NOT a painting. "
        "Atmospheric brushwork is NOT flat colors. Describe what IS there.\n\n"
        "Output EXACTLY this format:\n\n"
        "Source: [Name the specific artist, franchise, show, or art movement if you "
        "recognize it. If you can't identify one with confidence, write 'Original'.]\n"
        "Art Style: [3-6 word description of the rendering approach. Describe the "
        "TECHNIQUE you see, not the subject matter. Be precise — 'atmospheric digital "
        "painting with layered brushwork' not 'fantasy art'.]\n"
        "Colors: [Comma-separated dominant colors with PRECISE modifiers. "
        "Don't say 'bright green' — say 'sickly yellow-green' or 'desaturated "
        "portal-green'. Be specific about saturation and tone.]\n"
        "Vibe: [3-6 mood words. Be honest about the emotional register. If the "
        "image is dark or horrific, use words like dread, sinister, ominous, macabre. "
        "Do NOT soften horror into 'ethereal' or 'mystical'.]\n"
        "Themes: [What CATEGORIES of visual content appear? Use specific motif names, "
        "not vague genre labels. WRONG: 'fantasy, mythology'. RIGHT: 'cosmic body "
        "horror, undead masses, oppressive skies'. These motifs will appear on EVERY "
        "card in the deck.]\n"
        "Technique: [Describe the rendering with PRECISION. Cover each of these "
        "ONLY if you can identify them with confidence:\n"
        "  - Medium: What created this image?\n"
        "  - Edges: How are forms separated from their backgrounds?\n"
        "  - Shading/Lighting: How are depth and volume achieved?\n"
        "  - Surface texture: What does the image surface look and feel like?\n"
        "  - Detail & depth: How does detail change from foreground to background?]\n\n"
        "[Then 2-3 sentences of art direction precise enough that another artist "
        "could reproduce this style. Reference the actual medium and technique.]\n\n"
        "CRITICAL RULES:\n"
        "- ACCURACY OVER CATEGORIZATION. Describe what you see, not what category "
        "it fits. If you're unsure about a technique, describe the visual evidence "
        "rather than guessing a label.\n"
        "- Do NOT describe characters, scenes, or game elements — only technique.\n"
        "- EXCEPTION: Themes field MUST describe categories of content (horror motifs, "
        "nature elements, etc.) that should appear on every card.\n"
        "- If it's a PHOTOGRAPH, say photograph — not painting.\n"
        "- Do NOT mention card frames, text, or game mechanics."
    )

    try:
        # Qwen2.5-VL (via mlx-vlm) reads medium/technique accurately in a single
        # pass — no llava-style anti-hallucination pre-classification needed, and
        # PIL handles WebP/PNG/JPG natively so no format conversion is required.
        import mlx_llm
        prompt = (
            system_msg +
            "\n\nAnalyze this image's art style for use as a reference "
            "in MTG card art generation:"
        )
        description = mlx_llm.vision(
            image_path=str(image_path),
            prompt=prompt,
            model=local_model,
            max_tokens=400,
            temperature=0.7,
        )
        print(f"[vision] Style analysis complete: {description[:80]}...")
        return description
    except Exception as e:
        print(f"[vision] Style analysis failed: {e}")
        return ""


def merge_style_descriptions(descriptions: list[str]) -> str:
    """Merge multiple style descriptions into one combined description.

    Parses structured fields (Source, Art Style, Colors, Vibe, Faces, Technique)
    from each description, merges them, and appends prose sections.

    Args:
        descriptions: List of style description strings from analyze_inspiration_style()

    Returns:
        A single merged description string, or empty string if no valid input.
    """
    if not descriptions:
        return ""
    descriptions = [d for d in descriptions if d and d.strip()]
    if not descriptions:
        return ""
    if len(descriptions) == 1:
        return descriptions[0]

    # Structured fields to parse and merge
    field_names = ['Source', 'Art Style', 'Colors', 'Vibe', 'Themes', 'Faces', 'Technique']
    # Collect per-field values across all descriptions
    field_values = {f: [] for f in field_names}
    prose_sections = []

    for desc in descriptions:
        lines = desc.split('\n')
        current_field = None
        current_value = []
        prose_lines = []
        in_structured = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- '):
                stripped = stripped[2:].lstrip()

            matched_field = None
            for fname in field_names:
                if stripped.startswith(f'{fname}:'):
                    matched_field = fname
                    break

            if matched_field:
                # Save previous field
                if current_field and current_value:
                    val = ' '.join(current_value).strip()
                    if val:
                        field_values[current_field].append(val)
                current_field = matched_field
                current_value = [stripped[len(matched_field) + 1:].strip()]
                in_structured = True
            elif in_structured and current_field and stripped:
                # Continuation of current field (multi-line like Technique)
                if stripped.startswith('-') or stripped.startswith('•'):
                    current_value.append(stripped)
                elif not any(stripped.startswith(f'{f}:') for f in field_names):
                    current_value.append(stripped)
                else:
                    in_structured = False
            elif not in_structured and stripped:
                # Not part of structured fields — this is prose
                prose_lines.append(stripped)
            elif not stripped and current_field and current_value:
                # Blank line ends current structured field, starts prose
                val = ' '.join(current_value).strip()
                if val:
                    field_values[current_field].append(val)
                current_field = None
                current_value = []
                in_structured = False

        # Save last field
        if current_field and current_value:
            val = ' '.join(current_value).strip()
            if val:
                field_values[current_field].append(val)

        if prose_lines:
            prose_sections.append(' '.join(prose_lines))

    # Build merged description
    merged_parts = []

    # Sources: joined with " + "
    if field_values['Source']:
        unique_sources = []
        for s in field_values['Source']:
            if s.lower() != 'original' and s not in unique_sources:
                unique_sources.append(s)
        if unique_sources:
            merged_parts.append(f"Source: {' + '.join(unique_sources)}")
        else:
            merged_parts.append("Source: Original")

    # Art Style, Faces, Technique: joined with " | "
    for fname in ['Art Style', 'Faces', 'Technique']:
        if field_values[fname]:
            unique = []
            for v in field_values[fname]:
                if v not in unique:
                    unique.append(v)
            merged_parts.append(f"{fname}: {' | '.join(unique)}")

    # Colors, Vibe, Themes: comma-merged (deduplicated)
    for fname in ['Colors', 'Vibe', 'Themes']:
        if field_values[fname]:
            all_items = []
            for v in field_values[fname]:
                for item in v.split(','):
                    item = item.strip()
                    if item and item not in all_items:
                        all_items.append(item)
            merged_parts.append(f"{fname}: {', '.join(all_items)}")

    # Append prose (take first 2 to avoid excessive length)
    if prose_sections:
        merged_parts.append('')
        for prose in prose_sections[:2]:
            # Cap each prose section
            words = prose.split()
            if len(words) > 60:
                prose = ' '.join(words[:60]) + '...'
            merged_parts.append(prose)

    result = '\n'.join(merged_parts)

    # Cap total length for token budgets (~800 chars)
    if len(result) > 800:
        result = result[:797] + '...'

    return result


def distill_style_tokens(descriptions: list[str], style_source: str = '',
                          openai_client=None, backend: str = 'openai',
                          local_model: str = 'llama3.2:3b') -> dict:
    """Distill per-image style descriptions into SDXL-optimized style tokens.

    Takes the raw vision analysis from each inspiration image and uses an LLM
    to extract concise, structured rendering tokens that SDXL's CLIP encoder
    responds to effectively.  Tokens describe visual technique only — never
    character names, show names, or content.

    Returns a dict with keys: line_style, coloring, palette, proportions,
    rendering, mood.  Each value is a short comma-separated string of 3-6
    descriptors (2-4 words each).  Returns {} on failure.
    """
    import json as _json

    descriptions = [d for d in descriptions if d and d.strip()]
    if not descriptions:
        return {}

    system_msg = (
        "You are a style tokenizer for Stable Diffusion XL.  Your job is to "
        "distill art style descriptions into SHORT, PRECISE rendering tokens "
        "that SDXL's CLIP text encoder responds to.\n\n"
        "Rules:\n"
        "- Each category gets 3-6 descriptors, each 2-4 words\n"
        "- Use comma-separated lists\n"
        "- Describe ONLY visual rendering technique — NEVER name characters, "
        "shows, franchises, or artists\n"
        "- ONLY describe what the source art ACTUALLY contains. "
        "If there are no outlines, edges must say 'no outlines'. "
        "If shading uses gradients, do NOT say 'flat colors'.\n"
        "- Use terms SDXL understands: 'visible brushstrokes', 'oil painting', "
        "'atmospheric lighting', 'impasto texture', 'smooth digital blend', "
        "'layered glazing', 'chiaroscuro', 'rim lighting', etc.\n"
        "- Be SPECIFIC: 'thick 3-4px black outlines' not 'bold lines'\n"
        "- Describe what TO render, not what to avoid\n"
        "- Output valid JSON only — no markdown fences, no explanation\n\n"
        "Output EXACTLY this JSON format:\n"
        "{\n"
        '  "tradition": "Describe the rendering tradition in 2-5 words. '
        "Be accurate — don't force into a category. Examples: "
        "'painterly fantasy illustration', 'atmospheric digital painting', "
        "'Japanese anime', 'western 2D cartoon', 'realistic digital art', "
        "'live-action cinematography', '3D CG animation', 'watercolor illustration', "
        "'pencil drawing', 'pixel art', 'woodblock print', 'oil painting'. "
        'Describe what the art ACTUALLY is.",\n'
        '  "edges": "How are forms defined? Options: visible outlines (describe weight/color), '
        'soft painted edges, lost-and-found edges, hard silhouettes, color transitions, no outlines. '
        'Describe what ACTUALLY exists.",\n'
        '  "coloring": "shading and lighting method: describe what you actually see. '
        'e.g. atmospheric chiaroscuro, layered glazing, soft gradients, impasto, '
        'smooth digital blending, dramatic rim lighting, ambient occlusion",\n'
        '  "palette": "3-5 specific color descriptors with saturation modifiers",\n'
        '  "surface": "surface texture: visible brushstrokes, smooth digital, clean vector, '
        'canvas grain, palette knife, etc.",\n'
        '  "rendering": "overall medium and technique",\n'
        '  "mood": "5-8 atmospheric and tonal descriptors — this is CRITICAL for '
        'generation quality. Capture the emotional tone, atmosphere, and feeling. '
        'Be specific: use words like sinister, foreboding, ominous, macabre, eerie, '
        'serene, whimsical, melancholic, triumphant, oppressive, haunting, etc. '
        'Dark/horror moods need extra precision — do NOT soften to just ethereal or epic.",\n'
        '  "themes": "3-6 thematic motifs from the source art that should appear in EVERY card. '
        "Use SPECIFIC visual categories, not vague genre labels. "
        "WRONG: 'fantasy, mythology, epic'. "
        "RIGHT: 'cosmic horror, undead masses, decay and rot, chains and bondage, "
        "celestial bodies merged with flesh'. "
        "WRONG: 'nature, cute'. "
        "RIGHT: 'whimsical mushroom houses, tiny woodland creatures, flower crowns'. "
        'These motifs are the content DNA — they make cards BELONG together."\n'
        "}"
    )

    user_msg = ""
    if style_source:
        user_msg += (
            f"The visual style comes from: {style_source}. Use your knowledge "
            f"of this source to accurately determine the tradition and all "
            f"rendering tokens. Do NOT include the source name in any token "
            f"values — describe the VISUAL TECHNIQUE only.\n\n"
        )
    user_msg += "Distill these art style analyses into SDXL-optimized rendering tokens:\n\n"
    for i, desc in enumerate(descriptions):
        user_msg += f"--- Image {i + 1} ---\n{desc}\n\n"

    try:
        import mlx_llm
        raw = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            model=local_model,
            max_tokens=400,
            temperature=0.3,
        )

        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1] if '\n' in raw else raw[3:]
        if raw.endswith('```'):
            raw = raw[:-3].strip()
        if raw.startswith('json'):
            raw = raw[4:].strip()

        # Extract just the JSON object if LLM added extra text
        brace_start = raw.find('{')
        brace_end = raw.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            raw = raw[brace_start:brace_end + 1]

        tokens = _json.loads(raw)

        # Validate: keep only expected keys, ensure string values
        expected_keys = ('tradition', 'edges', 'coloring', 'palette',
                         'surface', 'rendering', 'mood', 'themes')
        result = {}
        for key in expected_keys:
            val = tokens.get(key, '')
            if isinstance(val, list):
                val = ', '.join(str(v) for v in val)
            elif not isinstance(val, str):
                val = str(val) if val else ''
            # Cap each value at 40 words
            words = val.split()
            if len(words) > 40:
                val = ' '.join(words[:40])
            # Strip source name leakage (case-insensitive)
            if style_source:
                import re as _re
                val = _re.sub(_re.escape(style_source), '', val, flags=_re.IGNORECASE).strip()
                val = _re.sub(r'\s{2,}', ' ', val)  # collapse double spaces
            result[key] = val.strip(', ')

        # Post-process tradition: ensure it's a clean 2-4 word tradition anchor.
        # If LLM gave multiple traditions or garbage, derive from rendering + context.
        tradition = result.get('tradition', '')
        if ',' in tradition or len(tradition.split()) > 5:
            # LLM gave multiple or verbose — simplify
            tradition = tradition.split(',')[0].strip()
        # Determine if this is a non-art tradition that should NOT get cultural qualifiers
        rendering = result.get('rendering', '')
        _trad_lower = tradition.lower()
        _non_art_traditions = ('photograph', 'cinematograph', 'live-action', 'film',
                               '3d', 'cg ', 'cgi', 'game render', 'sculpt', 'figurine',
                               'pixel art', 'retro game', 'woodblock', 'linocut',
                               'screen print', 'etching', 'engrav', 'stained glass',
                               'mosaic', 'embroid', 'textile', 'pencil', 'charcoal',
                               'ink wash', 'pen and ink')
        _is_non_art = any(w in _trad_lower for w in _non_art_traditions)
        # Only add cultural qualifiers for painterly/graphic/animation traditions
        if not _is_non_art:
            has_cultural = any(w in _trad_lower for w in
                              ('western', 'japanese', 'american', 'european', 'asian'))
            if not has_cultural:
                # Infer from rendering/coloring keywords
                if any(w in (rendering + ' ' + result.get('coloring', '')).lower()
                       for w in ('anime', 'manga', 'chibi')):
                    tradition = 'Japanese ' + tradition if tradition else 'Japanese anime'
                elif any(w in (rendering + ' ' + tradition).lower()
                         for w in ('cartoon', 'comic', 'cel', 'animated')):
                    tradition = 'western ' + tradition if tradition else 'western 2D cartoon'
                elif any(w in (rendering + ' ' + tradition).lower()
                         for w in ('oil', 'paint', 'impasto',
                                   'atmospheric', 'fantasy illustration')):
                    tradition = 'European ' + tradition if tradition else 'European oil painting'
        # Cross-reference tradition with style_source to catch misclassification.
        # If the source doesn't suggest anime/manga but the LLM output does,
        # pick a smarter fallback based on rendering tokens.
        if style_source:
            _src_lower = style_source.lower()
            _trad_lower = tradition.lower()
            if ('anime' in _trad_lower or 'manga' in _trad_lower) and \
               not any(w in _src_lower for w in ('anime', 'manga')):
                _rend_lower = result.get('rendering', '').lower()
                _surf_lower = result.get('surface', '').lower()
                _combined = _rend_lower + ' ' + _surf_lower
                if any(w in _combined for w in ('photo', 'film', 'cinemat',
                                                 'lens', 'camera')):
                    tradition = 'live-action cinematography'
                elif any(w in _combined for w in ('3d', 'cg', 'render',
                                                   'sculpt', 'polygon')):
                    tradition = '3D CG animation'
                elif any(w in _combined for w in ('pixel', 'retro', '8-bit',
                                                   '16-bit')):
                    tradition = 'pixel art'
                elif any(w in _combined for w in ('woodblock', 'linocut',
                                                   'screen print', 'etch',
                                                   'engrav')):
                    tradition = 'woodblock print'
                elif any(w in _rend_lower for w in ('oil', 'paint', 'impasto',
                                                     'atmospheric')):
                    tradition = 'European oil painting'
                elif any(w in _rend_lower for w in ('digital paint', 'matte',
                                                     'concept art')):
                    tradition = 'digital painting'
                else:
                    tradition = 'western 2D cartoon'
                print(f"  [distill] Corrected tradition: anime → {tradition} (source: {style_source})")
        result['tradition'] = tradition.strip(', ')[:60]

        print(f"[vision] Style tokens distilled: { {k: v[:50] for k, v in result.items() if v} }")
        return result

    except Exception as e:
        import traceback
        print(f"[vision] Style token distillation failed: {e}")
        traceback.print_exc()
        return {}


def build_clip_directives(style_tokens: dict, descriptions: list[str],
                          style_source: str = '',
                          openai_client=None, backend: str = 'openai',
                          local_model: str = 'llama3.2:3b') -> dict:
    """Build SDXL CLIP prompt style tags and negative prompt via LLM.

    Uses an LLM to convert style tokens and descriptions into a compact,
    CLIP-optimized style_tags string (~25 words) and a negative prompt
    (~20 words) that work for ANY visual style — no hardcoded elif chains.

    Computed once per deck during style distillation and cached in deck.json.

    Returns dict with keys: style_tags, negative, is_painterly, is_anime.
    Returns {} on failure.
    """
    import json as _json

    if not style_tokens:
        return {}

    system_msg = (
        "You convert art style analysis into Stable Diffusion XL prompt tokens.\n\n"
        "CLIP has a 77-token limit. Your output must be SHORT and use terms SDXL responds to.\n\n"
        "FIRST determine the rendering tradition from the tokens:\n"
        "- PAINTERLY: oil painting, watercolor, visible brushstrokes, atmospheric depth, "
        "chiaroscuro, glazing, impasto, soft edges, no outlines\n"
        "- GRAPHIC: flat colors, cel-shading, visible outlines, vector art, cartoon\n"
        "- PHOTOGRAPHIC/CINEMATIC: photograph, film still, live-action, depth of field, "
        "film grain, color grading, lens effects, bokeh\n"
        "- 3D/CGI: 3D render, CG animation, sculpted forms, global illumination, "
        "subsurface scattering, polygon modeling\n"
        "- PIXEL ART: pixel grid, limited palette, retro game art\n"
        "- PRINTMAKING: woodblock, linocut, etching, engraving, screen print\n"
        "- DRAWN: pencil, charcoal, ink wash, pen and ink, crosshatch\n"
        "- CRAFT: stained glass, mosaic, embroidery, textile\n"
        "- REALISTIC: photorealistic digital painting, matte painting, concept art\n\n"
        "Rules for style_tags:\n"
        "- 6-12 comma-separated descriptors, each 1-3 words\n"
        "- Start with the tradition/medium (e.g. 'oil painting style', 'cel-shaded cartoon')\n"
        "- INCLUDE 2-4 mood/atmosphere words from the mood token — these are CRITICAL. "
        "For dark/horror moods, include words like 'dark atmosphere', 'ominous lighting', "
        "'foreboding', 'sinister mood'. Do NOT omit mood — it drives the emotional tone.\n"
        "- Do NOT include thematic content words (e.g. 'cosmic horror', 'undead', "
        "'chains', 'decay') in style_tags — those belong in the subject description, "
        "not the style anchor. style_tags is ONLY for rendering technique.\n"
        "- ONLY include descriptors that match the actual style. "
        "Do NOT add 'outlines' or 'flat colors' to painterly art. "
        "Do NOT add 'brushstrokes' or 'impasto' to cartoon/vector art.\n"
        "- NEVER use proper nouns, character names, show names, or artist names\n"
        "- NEVER use vague terms like 'artistic' or 'creative' — be PRECISE\n"
        "- Use terms SDXL understands: 'visible brushstrokes', 'oil painting', "
        "'atmospheric lighting', 'thick outlines', 'flat colors', 'cel-shaded', "
        "'impasto texture', 'smooth shading', 'highly detailed', etc.\n\n"
        "Rules for negative:\n"
        "- 8-15 comma-separated terms describing what to AVOID\n"
        "- Always include quality guards: 'blurry, low quality'\n"
        "- CONDITIONAL negatives based on tradition:\n"
        "  - Only negate '3D render' if the tradition is NOT 3D/CGI\n"
        "  - Only negate 'photograph' if the tradition is NOT photographic/cinematic\n"
        "  - Only negate 'pixel art' if the tradition is NOT pixel art\n"
        "- Negate the OPPOSITE of the desired style:\n"
        "  - For painterly/oil: negate 'flat colors, thick outlines, cel-shaded, "
        "cartoon, simple shading, vector art'\n"
        "  - For flat/cartoon: negate 'photorealistic, realistic, photograph, "
        "smooth skin texture, oil painting'\n"
        "  - For photographic/cinematic: negate 'oil painting, brushstrokes, "
        "cartoon, anime, flat colors, illustration'\n"
        "  - For 3D/CGI: negate 'oil painting, brushstrokes, flat colors, "
        "photograph, film grain'\n"
        "  - For pixel art: negate 'smooth shading, photograph, oil painting, "
        "realistic, high resolution'\n"
        "  - For printmaking/drawn: negate 'photograph, 3D render, smooth digital, "
        "oil painting'\n"
        "  - For anime: do NOT negate 'anime' (it's the desired style)\n"
        "  - For non-anime: negate 'anime, manga'\n"
        "- Also negate the OPPOSITE of the desired mood:\n"
        "  - For dark/horror/ominous mood: negate 'cute, pretty, cheerful, "
        "bright colors, whimsical, friendly, happy, saturated'\n"
        "  - For whimsical/cute mood: negate 'dark, gloomy, horror, grim'\n"
        "  - For epic/majestic mood: negate 'mundane, boring, flat'\n\n"
        "Output EXACTLY this JSON — no markdown fences, no explanation:\n"
        "{\n"
        '  "style_tags": "comma-separated CLIP descriptors, max 25 words",\n'
        '  "negative": "comma-separated negative prompt, max 20 words",\n'
        '  "is_painterly": true/false,\n'
        '  "is_anime": true/false,\n'
        '  "is_photographic": true/false,\n'
        '  "is_3d": true/false\n'
        "}"
    )

    user_msg = ""
    if style_source:
        user_msg += (
            f"Style source (DO NOT include this name in any output): {style_source}\n\n"
        )
    user_msg += "Style tokens:\n"
    for key in ('tradition', 'edges', 'coloring', 'palette',
                'surface', 'rendering', 'mood'):
        val = style_tokens.get(key, '')
        if val:
            user_msg += f"- {key}: {val}\n"

    # Add truncated descriptions for extra context
    desc_text = '\n'.join(descriptions)[:400]
    if desc_text:
        user_msg += f"\nFull style descriptions:\n{desc_text}\n"

    try:
        import mlx_llm
        # MLX has no native JSON mode, but the system prompt demands raw JSON and
        # the parsing below strips fences + has a regex fallback for stray prose.
        raw = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            model=local_model,
            max_tokens=300,
            temperature=0.3,
        )

        # Strip markdown code fences if present
        if raw.startswith('```'):
            lines = raw.split('\n')
            lines = [l for l in lines if not l.startswith('```')]
            raw = '\n'.join(lines).strip()

        try:
            result = _json.loads(raw)
        except _json.JSONDecodeError:
            # Attempt lenient parse: extract key-value pairs via regex
            import re as _re
            result = {}
            for key in ('style_tags', 'negative'):
                m = _re.search(rf'"{key}"\s*:\s*"([^"]*)"', raw)
                if m:
                    result[key] = m.group(1)
            for key in ('is_painterly', 'is_anime', 'is_photographic', 'is_3d'):
                m = _re.search(rf'"{key}"\s*:\s*(true|false)', raw, _re.IGNORECASE)
                if m:
                    result[key] = m.group(1).lower() == 'true'
            if not result.get('style_tags'):
                print(f"[clip_directives] JSON parse failed and regex fallback found nothing")
                return {}

        # Validate expected keys
        style_tags = str(result.get('style_tags', '')).strip()
        negative = str(result.get('negative', '')).strip()
        is_painterly = bool(result.get('is_painterly', False))
        is_anime = bool(result.get('is_anime', False))
        is_photographic = bool(result.get('is_photographic', False))
        is_3d = bool(result.get('is_3d', False))

        if not style_tags:
            print("[clip_directives] LLM returned empty style_tags")
            return {}

        # Cap word counts
        tag_words = style_tags.split()
        if len(tag_words) > 25:
            style_tags = ' '.join(tag_words[:25])
        neg_words = negative.split()
        if len(neg_words) > 20:
            negative = ' '.join(neg_words[:20])

        # Strip style_source name leakage (case-insensitive)
        import re
        if style_source:
            for part in style_source.split():
                if len(part) > 2:
                    style_tags = re.sub(re.escape(part), '', style_tags,
                                        flags=re.IGNORECASE).strip()
                    negative = re.sub(re.escape(part), '', negative,
                                      flags=re.IGNORECASE).strip()
            # Clean up double commas from removal
            style_tags = re.sub(r',\s*,', ',', style_tags).strip(', ')
            negative = re.sub(r',\s*,', ',', negative).strip(', ')

        # Cross-reference is_anime against style_source
        if is_anime and style_source:
            _src_lower = style_source.lower()
            if not any(w in _src_lower for w in ('anime', 'manga')):
                is_anime = False
                # Ensure anime is in negative since it's not the desired style
                if 'anime' not in negative.lower():
                    negative = negative.rstrip(', ') + ', anime, manga'

        out = {
            'style_tags': style_tags,
            'negative': negative,
            'is_painterly': is_painterly,
            'is_anime': is_anime,
            'is_photographic': is_photographic,
            'is_3d': is_3d,
        }
        print(f"[clip_directives] Built: tags='{style_tags[:80]}', neg='{negative[:80]}', "
              f"painterly={is_painterly}, anime={is_anime}, "
              f"photographic={is_photographic}, 3d={is_3d}")
        return out

    except Exception as e:
        print(f"[clip_directives] Failed: {e}")
        return {}


def _article(word: str) -> str:
    """Return 'an' if word starts with a vowel sound, else 'a'."""
    return 'an' if word and word[0].lower() in 'aeiou' else 'a'


def _build_base_subject(card: dict) -> str:
    """Build a reliable visual subject from card metadata alone (no LLM).

    Returns a short phrase like 'a massive wurm', 'an ooze',
    'a forest landscape'.  Always succeeds — provides the fallback
    when LLM enhancement fails.
    """
    name = card.get('name', '')
    type_line = card.get('type_line', '')
    tl = type_line.lower()
    power = card.get('power')
    toughness = card.get('toughness')

    # Creatures: card name + size hint + creature subtype
    if 'creature' in tl and ('—' in type_line or '\u2014' in type_line):
        import re
        subtypes = re.split(r'[—\u2014]', type_line, 1)[1].strip().lower()
        size = ''
        if power and toughness:
            try:
                p = int(power)
                if p >= 7:
                    size = 'massive '
                elif p >= 5:
                    size = 'large '
                elif p <= 1:
                    size = 'small '
            except ValueError:
                pass
        return f"{name}, a {size}{subtypes}"

    # Lands — use full card name
    if 'land' in tl:
        return f"{name}, a magical landscape"

    # Artifacts / Equipment
    if 'artifact' in tl:
        return f"{name}, a magical artifact"

    # Instants / Sorceries — action names, describe as a scene
    if 'instant' in tl or 'sorcery' in tl:
        return f"a scene depicting {name}"

    # Enchantments / fallback — use card name
    return f"{name}, an enchantment" if 'enchantment' in tl else name


def distill_one_card_subject(card: dict, art_prompt: str,
                              backend: str = 'local',
                              local_model: str = 'llama3.2:3b',
                              openai_client=None,
                              style_source: str = '',
                              temperature: float = 1.2) -> str:
    """Distill a single card's art prompt into a vivid CLIP-optimized subject.

    Returns a 10-25 word image prompt, or '' if the LLM fails or output is
    out of bounds. Pure function — no globals, no side effects.
    """
    import re as _re

    if not art_prompt:
        return ''

    base = _build_base_subject(card)

    # Take the first ~150 chars of the scene body (after the style preamble)
    parts = art_prompt.split('\n\n', 1)
    body = parts[1] if len(parts) > 1 else parts[0]
    snippet = body.strip()[:150].rsplit(' ', 1)[0]

    system_msg = (
        "Condense this art description into a vivid image prompt (10-25 words).\n"
        "Keep the subject type. Include setting, mood, lighting, and key visual details.\n"
        "Be creative — vary the setting, pose, and atmosphere from previous versions.\n\n"
        f"Subject type: {base}\n"
        f"Description: {snippet}\n\n"
        "Reply with ONLY the image prompt, nothing else."
    )

    try:
        import mlx_llm
        result = mlx_llm.chat(
            messages=[{'role': 'user', 'content': system_msg}],
            model=local_model,
            max_tokens=60,
            temperature=temperature,
        )
    except Exception as e:
        print(f"[vision] distill_one_card_subject failed for {card.get('name','?')}: {e}")
        return ''

    # Cleanup: leading numbering, surrounding quotes, trailing punctuation
    result = _re.sub(r'^\d+[\.\)]\s*', '', result)
    result = result.strip('"\'').rstrip('.')

    # Strip style-source name leakage (e.g. "Picasso", "Akira")
    if style_source:
        for word in style_source.split():
            if len(word) > 3:
                result = _re.sub(r'\b' + _re.escape(word) + r'\b',
                                 '', result, flags=_re.IGNORECASE)
        result = _re.sub(r'\s{2,}', ' ', result).strip(', ')

    words = result.split()
    if 3 <= len(words) <= 30 and result:
        return result
    return ''


def distill_card_subjects(cards: list[dict], art_prompts: dict,
                           style_tokens: dict, style_source: str = '',
                           openai_client=None, backend: str = 'openai',
                           local_model: str = 'llama3.1:8b',
                           progress_callback=None) -> dict:
    """Build CLIP-optimized subjects for each card by calling
    distill_one_card_subject in parallel.

    Falls back to _build_base_subject when the LLM call fails or returns
    out-of-bounds output. Every card with an art prompt gets a subject.

    Returns {card_name: "short subject", "_distill_stats": {...}}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not art_prompts:
        return {}

    # Filter to cards with art prompts (skip Card Back)
    work = []
    base_subjects = {}
    for card in cards:
        name = card.get('name', '')
        if not name or name == 'Card Back' or name not in art_prompts:
            continue
        base_subjects[name] = _build_base_subject(card)
        work.append((name, card))

    if not work:
        return {}

    enhanced = {}
    total = len(work)
    completed = [0]
    import threading
    lock = threading.Lock()

    def distill_one(name_card):
        name, card = name_card
        out = distill_one_card_subject(
            card=card,
            art_prompt=art_prompts.get(name, ''),
            backend=backend,
            local_model=local_model,
            openai_client=openai_client,
            style_source=style_source,
        )
        return name, out

    # Local Ollama serializes through one GPU; cloud OpenAI tolerates parallelism.
    # Use 1 worker for local (queueing helps nothing), 4 for cloud.
    workers = 1 if backend == 'local' else 4

    if progress_callback:
        progress_callback(1, 1, 0, total)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(distill_one, nc) for nc in work]
        for fut in as_completed(futures):
            name, out = fut.result()
            if out:
                enhanced[name] = out
            with lock:
                completed[0] += 1
                if progress_callback:
                    progress_callback(1, 1, completed[0], total)

    # Merge: use enhanced where available, base subject as fallback
    result = {}
    for name in base_subjects:
        result[name] = enhanced.get(name, base_subjects[name])

    metadata_only = [n for n in base_subjects if n not in enhanced]
    enhanced_count = len(base_subjects) - len(metadata_only)
    print(f"[vision] Card subjects: {len(result)} total, "
          f"{enhanced_count} LLM-enhanced, "
          f"{len(metadata_only)} metadata-only")
    if metadata_only:
        print(f"[vision] WARNING: LLM failed for {len(metadata_only)} cards "
              f"(using basic metadata): {metadata_only[:5]}")
    if result:
        samples = list(result.items())[:3]
        print(f"[vision] Examples: {samples}")
    result['_distill_stats'] = {
        'total': len(result) - 1,
        'enhanced': enhanced_count,
        'metadata_only': len(metadata_only),
        'metadata_only_cards': metadata_only,
    }
    return result


def build_style_preamble(style_description: str) -> str:
    """Convert a vision analysis style description into a full art prompt preamble.

    Takes the output of analyze_inspiration_style() (structured attributes +
    prose) and appends the no-text constraint for art generation.
    """
    if not style_description:
        return ""

    return (
        f"{style_description.strip()} "
        "No text, no words, no letters, no card frame, no borders "
        "— PURE ART ONLY."
    )


def build_collage_instruction(style_description: str, subject_prompt: str,
                                has_scryfall_ref: bool = False,
                                art_direction: str = '',
                                source_override: str = '') -> str:
    """Build the instruction text for image generation with reference images.

    Cloud models (gpt-image-1) support up to 4000 characters — this builds
    rich, detailed prompts that take full advantage of that capacity.

    Args:
        style_description: Vision analysis output (structured attributes + prose)
        subject_prompt: What to depict (card subject description)
        has_scryfall_ref: Whether a Scryfall art collage is being used
        art_direction: Additional prose art direction (from vision analysis)
        source_override: Manual style source (e.g. "Studio Ghibli") — takes
            precedence over Source: extracted from the description
    """
    # Build the art direction block if we have rich prose
    direction_block = ''
    if art_direction:
        direction_block = f"\n\nArt direction: {art_direction}"

    # Extract source name (e.g. "Studio Ghibli") for prominent placement
    source_prefix = ''
    if source_override:
        source_prefix = f"in the style of {source_override} — "
    else:
        for line in style_description.split('\n'):
            stripped = line.strip()
            # Handle both "Source: X" and "- Source: X" (Ollama bullet format)
            if stripped.startswith('- '):
                stripped = stripped[2:].lstrip()
            if stripped.startswith('Source:'):
                source_name = stripped[len('Source:'):].strip()
                if source_name and source_name.lower() != 'original':
                    source_prefix = f"in the style of {source_name} — "
                break

    medium_fidelity = (
        "CRITICAL: Match the MEDIUM and RENDERING TECHNIQUE exactly — "
        "if the style is painterly with visible brushstrokes, create painterly "
        "art with texture and depth. If it is flat/cel-shaded cartoon, create "
        "flat cartoon art. Do NOT flatten painterly styles into cartoon aesthetics."
    )

    if has_scryfall_ref:
        return (
            f"The LEFT image shows the target art style. The RIGHT image shows "
            f"the original card art for subject and composition reference.\n\n"
            f"Create art {source_prefix}matching this style precisely: "
            f"{style_description}\n\n"
            f"{medium_fidelity}\n\n"
            f"Reimagine the right image's subject in the left image's style. "
            f"The art depicts: {subject_prompt}"
            f"{direction_block}\n\n"
            f"Create ONLY the art — no text, no words, no letters, no card "
            f"frame, no borders. Pure art only."
        )
    else:
        return (
            f"Create fantasy art {source_prefix}in this specific style: "
            f"{style_description}\n\n"
            f"{medium_fidelity}\n\n"
            f"The art depicts: {subject_prompt}"
            f"{direction_block}\n\n"
            f"No text, no words, no letters, no card frame, no borders — "
            f"pure art only."
        )
