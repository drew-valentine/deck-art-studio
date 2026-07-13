#!/usr/bin/env python3
"""
Vision-based style analysis for inspiration images.

Uses an MLX vision model (Qwen2.5-VL via mlx-vlm) to analyze an uploaded
inspiration image and extract a structured art style description that drives
prompt generation.
"""

from pathlib import Path


def build_flux_style_paragraph(image_path, style_source: str = '',
                               vision_model: str = 'llava:7b',
                               max_words: int = 65) -> str:
    """Derive a rich art-director style paragraph from an inspiration image.

    Empirically (queen-marchesa / heads-i-win validation decks), FLUX's style
    fidelity is bounded by descriptor QUALITY, not by the model: a specific
    ~60-word art-director paragraph ("fine flowing black ink linework, flat
    muted pastel colors of dusty coral, sage green..., ukiyo-e swirling clouds")
    renders near-uncanny transfer where 10-16 word tag soup gives only a distant
    echo. The local VLM writes genuinely good LONG prose about technique when
    asked like an art director — but every model-based compression pass
    (LLM or VLM) shreds it back into tag soup, and fill-in templates get
    parroted. So: one long-form vision pass, then DETERMINISTIC compression in
    code (_prose_to_style_prompt) — strip scaffolding, drop subject leakage,
    cap words. Returns '' on failure (caller falls back to the tag path).
    """
    image_path = Path(image_path)
    if not image_path.exists():
        return ''
    prompt = (
        "You are a senior art director writing a STYLE TRANSFER instruction for "
        "a text-to-image model. Study this image's rendering technique — NOT its "
        "subject. Describe, in order: medium and overall look; linework character "
        "(weight, flow, density); how surfaces and shading are handled; the color "
        "palette with 4-6 SPECIFIC hue names (say 'dusty coral', never just "
        "'red'); recurring decorative motifs or texture patterns; art-historical "
        "influences if visible. Be concrete and technical — never vague words "
        "like 'beautiful' alone. Never mention the subject. Output ONLY the "
        "description."
    )
    # Best-of-2 sampling: the VLM's roll-to-roll variance is the failure mode
    # (one roll writes "vermilion, chartreuse, saffron... ukiyo-e clouds", the
    # next writes markdown headers and "a mix of primary and secondary colors").
    # Two samples scored by deterministic style density picks the strong roll.
    candidates = []
    for _ in range(2):
        try:
            import mlx_llm
            prose = mlx_llm.vision(str(image_path), prompt, model=vision_model,
                                   max_tokens=260, temperature=0.3)
        except Exception as e:
            print(f"  [style] VLM paragraph read failed: {e}")
            continue
        line = _prose_to_style_prompt(prose or '', max_words=max_words)
        if line:
            candidates.append(line)
    if not candidates:
        return ''
    return max(candidates, key=_style_density_score)


def build_flux_style_block(image_path, style_source: str = '',
                           vision_model: str = 'llava:7b',
                           text_model: str = 'llama3.1:8b',
                           max_words: int = 38,
                           stored_description: str = '',
                           franchise: bool = None) -> str:
    """Compact, high-signal style block: the shipping style prompt.

    FLUX-schnell has a HARD 256-token budget shared with the card's subject
    prompt, and heavy style text measurably degrades subject coherence
    (anatomy, forms) by stealing attention and truncating the scene. The
    empirically-proven winning geometry (the deck's best-era renders) is a
    COMPACT style block (~30-40 words) of pure signal riding in front of a
    full, untrimmed subject. This builder assembles exactly that:

      [canonical medium anchors]  — classified, stable across re-distills
      [specific hue phrases]      — extracted verbatim from the VLM's read
      [1-2 decorative motifs]     — the reference's signature patterns
      [art-historical influence]  — when detected

    All extraction is deterministic from the VLM's long-form prose (the only
    high-quality output it produces); no open-ended LLM rewriting.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        return ''
    prompt = (
        "You are a senior art director writing a STYLE TRANSFER instruction for "
        "a text-to-image model. Study this image's rendering technique — NOT its "
        "subject. Describe, in order: medium and overall look; linework character "
        "(weight, flow, density); how surfaces and shading are handled; the color "
        "palette with 4-6 SPECIFIC hue names (say 'dusty coral', never just "
        "'red'); recurring decorative motifs or texture patterns; art-historical "
        "influences if visible. Be concrete and technical — never vague words "
        "like 'beautiful' alone. Never mention the subject. Output ONLY the "
        "description."
    )
    # UNION extraction across rolls: hues/motifs/influences are facts about the
    # reference image, not properties of one VLM roll — some rolls emit zero
    # specific hues, so pooling the rolls is strictly better than best-of-N.
    proses = []
    for _ in range(2):
        try:
            import mlx_llm
            prose = mlx_llm.vision(str(image_path), prompt, model=vision_model,
                                   max_tokens=260, temperature=0.3) or ''
        except Exception as e:
            print(f"  [style] VLM style read failed: {e}")
            continue
        if prose.strip():
            proses.append(prose)
    if not proses:
        return ''
    best_prose = max(proses, key=_style_density_score)

    anchors = _medium_anchors(_classify_style_medium(style_source, text_model,
                                                     img_desc=best_prose[:400])) \
        if style_source else []
    # Franchise names never reach the render prompt (character leakage — a
    # literal Rick once appeared as card art). Expand into anonymous design-
    # language cues instead, plus an explicit original-designs guard.
    # Line-weight-aware ink anchors: "bold ink outlines" on fine-line
    # references (rapidograph/technical-pen work) renders COMIC BOOK instead
    # of fine-line illustration. Weight comes from the evidence, not a fixed
    # anchor.
    evidence = (' '.join(proses) + ' ' + (stored_description or '')).lower()
    if anchors and anchors[0] == 'ink illustration':
        fine = sum(evidence.count(w) for w in
                   ('fine', 'delicate', 'thin', 'technical pen', 'rapidograph',
                    'intricate line', 'hairline'))
        bold = sum(evidence.count(w) for w in
                   ('bold line', 'thick line', 'heavy line', 'bold outline',
                    'chunky'))
        if fine > bold:
            anchors = ['fine-line ink illustration',
                       'uniform fine technical-pen linework',
                       'flat color fills over black line art',
                       'dense intricate detail filling every surface']

    if franchise is None:
        franchise = bool(style_source) and is_character_franchise(style_source,
                                                                  text_model)
    design_cues = (_expand_design_language(style_source, text_model)
                   if franchise else [])
    # The stored per-image analysis (deck.json inspiration_images[].style_
    # description) carries a dedicated "Colors:" line from a prior focused
    # pass — the highest-quality hue source available, so it seeds the pool.
    hues, motifs, influence = [], [], ''
    for source in [stored_description] + proses:
        if not source:
            continue
        for h in _extract_hue_phrases(source, cap=6):
            base = h.split()[-1]
            if h not in hues and not any(base == x.split()[-1] for x in hues):
                hues.append(h)
        for m in _extract_motif_phrases(source, cap=2):
            if m not in motifs:
                motifs.append(m)
        influence = influence or _extract_influence(source)
    # Modified hues first — bare colors are attractor bait ("yellow, red,
    # pink" + cartoonish rendered a Pikachu); "dark maroon" is directional.
    modified = [h for h in hues if ' ' in h]
    bare = [h for h in hues if ' ' not in h]
    hues = (modified + bare)[:6] if len(modified) < 3 else modified[:6]
    motifs = motifs[:2]

    parts = list(anchors) + design_cues
    if hues:
        parts.append('palette of ' + ', '.join(hues))
    parts.extend(motifs)
    if influence:
        parts.append(influence)
    if franchise:
        parts.append('original creature and character designs')
    # Cap on phrase boundaries, never mid-phrase; anchors always survive.
    budget = max_words + (18 if franchise else 0)   # design cues earn headroom
    out, count = [], 0
    for p in parts:
        n = len(p.split())
        if out and count + n > budget:
            continue
        out.append(p)
        count += n
    return _clean_descriptors(', '.join(out), style_source, max_descriptors=24)


def _extract_hue_phrases(text: str, cap: int = 6) -> list:
    """Pull specific hue phrases (modifier + color word) verbatim from prose.

    'dusty coral' carries transfer signal that bare 'red' does not, so the
    preceding modifier is kept when present."""
    import re as _re
    words = _re.findall(r"[A-Za-z][A-Za-z-]*", text)
    hues, seen = [], set()
    for i, raw in enumerate(words):
        w = raw.rstrip('s') if raw.lower().rstrip('s') in _COLOR_WORDS else raw
        if w.lower() in _COLOR_WORDS:
            mod = words[i - 1].lower() if i > 0 else ''
            phrase = (f"{mod} {w.lower()}"
                      if (mod and mod not in _COLOR_WORDS
                          and mod in _HUE_MODIFIERS) else w.lower())
            if phrase not in seen and w.lower() not in seen:
                seen.add(phrase)
                seen.add(w.lower())
                hues.append(phrase)
            if len(hues) >= cap:
                break
    return hues


_HUE_MODIFIERS = frozenset({
    'dusty', 'muted', 'pale', 'soft', 'deep', 'dark', 'light', 'bright',
    'vivid', 'acid', 'neon', 'pastel', 'warm', 'cool', 'sickly', 'desaturated',
    'rich', 'burnt', 'faded', 'dusky', 'creamy', 'sage', 'forest', 'sky',
    'blood', 'rust', 'golden', 'earthy', 'smoky', 'washed-out', 'vibrant',
})

_MOTIF_KEYWORDS = ('motif', 'pattern', 'swirl', 'ornament', 'filigree',
                   'arabesque', 'hatching', 'stipple', 'halftone', 'texture')

_INFLUENCE_TERMS = ('ukiyo-e', 'art nouveau', 'art deco', 'east asian',
                    'japanese woodblock', 'pop art', 'victorian', 'baroque',
                    'retro-futurist', 'mid-century', 'gothic', 'psychedelic',
                    'bauhaus', 'impressionist', 'pre-raphaelite')


def _extract_motif_phrases(text: str, cap: int = 2) -> list:
    """Short decorative-motif noun phrases ('stylized swirling clouds')."""
    import re as _re
    out = []
    for m in _re.finditer(
            r"((?:[a-z][a-z-]*\s+){0,2}(?:swirl(?:ing|s)?|clouds?|waves?|"
            r"filigree|arabesques?|florals?|hatching|halftone|stippl\w+)\s*"
            r"(?:patterns?|motifs?|textures?)?)", text.lower()):
        phrase = ' '.join(m.group(1).split())
        words = phrase.split()
        # trim leading verbs/stopwords the capture window can pick up
        while words and words[0] in ('include', 'includes', 'including',
                                     'with', 'and', 'the', 'of', 'are', 'is'):
            words = words[1:]
        phrase = ' '.join(words)
        # keep only phrases that actually read as motifs, not lone nouns
        if len(words) >= 2 and not any(w in _SUBJECT_LEAK_WORDS for w in words):
            if phrase not in out:
                out.append(phrase)
        if len(out) >= cap:
            break
    return out


def _extract_influence(text: str) -> str:
    t = text.lower()
    for term in _INFLUENCE_TERMS:
        if term in t:
            return f"{term} influence"
    return ''


# Technique vocabulary for scoring a style prompt's information density.
_TECHNIQUE_WORDS = frozenset({
    'linework', 'outlines', 'outline', 'ink', 'cel', 'flat', 'shading',
    'hatching', 'stipple', 'brushstrokes', 'washes', 'gradient', 'matte',
    'woodblock', 'etching', 'halftone', 'impasto', 'glazing', 'vector',
    'swirl', 'swirling', 'motif', 'motifs', 'pattern', 'patterns', 'ornate',
    'ornamental', 'ukiyo-e', 'nouveau', 'deco', 'illustration', 'animation',
    'cartoonish', 'painterly', 'watercolor', 'gouache',
})


def _style_density_score(line: str) -> int:
    """Deterministic quality score for a style prompt: specific hue words count
    double (they are the rarest, most transfer-critical information), then
    technique/motif vocabulary. Used to pick the best VLM sample."""
    import re as _re
    words = _re.findall(r"[a-z0-9-]+", line.lower())
    hue = sum(1 for w in words if w in _COLOR_WORDS)
    tech = sum(1 for w in words if w in _TECHNIQUE_WORDS)
    return hue * 2 + tech


# Sentence-scaffold prefixes the VLM reliably emits ("The linework is ...") —
# stripped so the clause reads as a direct style instruction. Applied
# repeatedly, so "The overall look is reminiscent of" also collapses.
_SCAFFOLD_RE = None

# Subject words whose presence marks a leaked content sentence ("the intricate
# design of the alien device on the boy's head") — those sentences are dropped
# whole; technique sentences never contain them.
_SUBJECT_LEAK_WORDS = frozenset({
    'person', 'people', 'man', 'men', 'woman', 'women', 'boy', 'girl', 'child',
    'character', 'characters', 'figure', 'figures', 'creature', 'creatures',
    'alien', 'animal', 'animals', 'face', 'faces', 'body', 'device', 'weapon',
    'held', 'worn', 'wearing', 'holding', 'depicted', 'subject', 'scene',
})


def _prose_to_style_prompt(prose: str, max_words: int = 65) -> str:
    """Deterministically compress VLM art-director prose into a style prompt.

    Removes section-header labels ("Medium and overall look:"), splits into
    sentences, drops subject-leak sentences, strips "The <aspect> is/are"
    scaffolding and filler participial tails ("..., creating a sense of ..."),
    then joins clause bodies with commas, capping at max_words on a clause
    boundary — always keeping at least one color-palette clause.
    """
    import re as _re
    global _SCAFFOLD_RE
    aspects = (r"image(?:'s)?|medium|overall look|look|style|aesthetic|"
               r"line\s?work|lines?|surfaces?|shading|colou?r palette|"
               r"palette|dominant colou?rs?|colou?rs?|specific hues?|hues?|"
               r"texture patterns?|textures?|recurring decorative motifs?|"
               r"decorative motifs?|motifs?|art[- ]historical influences?|"
               r"influences?|composition|framing|weight|density|flow")
    if _SCAFFOLD_RE is None:
        verbs = (r"is|are|has|have|includes?|include|shows?|show|features?|"
                 r"feature|exhibits?|exhibit|appears?|appear|handled with|"
                 r"handled|reminiscent of|suggests?|suggest")
        _SCAFFOLD_RE = _re.compile(
            rf"^(?:(?:the|this|its|overall|and)\s+)*(?:{aspects})"
            rf"(?:\s+(?:and|&)\s+(?:{aspects}))*\s*:?\s*(?:{verbs})?\s*",
            _re.IGNORECASE)

    raw = prose or ''
    # Markdown normalization — some rolls emit "### Medium and Overall Look",
    # "**Color palette:**", "- **Flow**" instead of prose. Header lines are
    # pure labels: drop them; bold markers and bullets are noise: strip them.
    raw = _re.sub(r'(?m)^\s*#{1,6}[^\n]*$', '', raw)
    raw = _re.sub(r'\*\*([^*]*)\*\*', r'\1', raw).replace('**', '')
    raw = _re.sub(r'(?m)^\s*(?:[-*+]|\d+[.)])\s+', '', raw)
    text = _re.sub(r'\s+', ' ', raw).strip()
    if not text:
        return ''
    # Kill inline section-header labels wherever they appear, including
    # compound ones ("Medium and overall look:", "and shading:").
    text = _re.sub(rf"(?:^|(?<=[.;!?] ))(?:and\s+)?(?:{aspects})"
                   rf"(?:\s+(?:and|&)\s+[A-Za-z ]{{3,24}})?\s*:\s*",
                   '', text, flags=_re.IGNORECASE)
    text = _re.sub(r'\s*:\s+(?=[A-Z])', ' ', text)   # stray label colons

    clauses = []
    for sent in _re.split(r'(?<=[.;!?])\s+', text):
        sent = sent.strip().rstrip('.;!?').strip()
        if not sent:
            continue
        words_l = set(w.lower() for w in _re.findall(r"[a-z]+", sent.lower()))
        if words_l & _SUBJECT_LEAK_WORDS:
            continue
        prev = None
        while prev != sent:            # strip stacked scaffolds
            prev = sent
            sent = _SCAFFOLD_RE.sub('', sent).strip()
        # chop filler participial tails — they carry no style information
        sent = _re.split(r',\s*(?:suggesting|creating|contributing|maintaining'
                         r'|adding|evoking|giving|conveying|resulting|which'
                         r'|that)\b', sent)[0]
        # chop an end-anchored prepositional subject tail ("... on the hot air
        # balloon") — content nouns sneak into motif clauses this way
        sent = _re.sub(r'\s+(?:on|of|in)\s+the\s+[a-z][a-z\s-]{2,32}$', '', sent,
                       flags=_re.IGNORECASE)
        sent = sent.strip(' ,')
        if len(sent.split()) < 2:
            continue
        # lowercase a leading scaffold-capital so clauses flow as one prompt
        if sent[0].isupper() and not sent.split()[0].isupper():
            sent = sent[0].lower() + sent[1:]
        clauses.append(sent)

    # Priority selection under the word cap. The style-DNA carriers — palette
    # hues, decorative motifs/patterns, art-historical influences — are what
    # make a transfer read as THE reference artist rather than a generic
    # category, so they outrank generic medium/linework filler.
    def _prio(idx, clause):
        c = clause.lower()
        if idx == 0:
            return 0                       # medium/overall look leads
        if _is_color_descriptor(clause):
            return 1
        if any(k in c for k in ('motif', 'pattern', 'swirl', 'cloud', 'ornament',
                                'ornate', 'texture', 'hatching', 'stipple')):
            return 1
        if any(k in c for k in ('influence', 'inspired', 'ukiyo', 'nouveau',
                                'deco', 'pop art', 'asian', 'victorian',
                                'baroque', '-esque')):
            return 1
        return 2
    # Drop trailing truncation debris — max_tokens can cut the prose mid-phrase,
    # leaving fragments like "for example, the".
    _dangling = {'the', 'a', 'an', 'of', 'and', 'or', 'with', 'for', 'to',
                 'in', 'on', 'like', 'example', 'such', 'as'}
    while clauses and (clauses[-1].split()[-1].lower() in _dangling
                       or len(clauses[-1].split()) < 3):
        clauses.pop()

    ranked = sorted(range(len(clauses)), key=lambda i: (_prio(i, clauses[i]), i))
    chosen, count = set(), 0
    for i in ranked:
        n = len(clauses[i].split())
        if chosen and count + n > max_words:
            continue
        chosen.add(i)
        count += n
    return ', '.join(clauses[i] for i in sorted(chosen))


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
                                  max_tokens=160, temperature=0.4)
    except Exception as e:
        print(f"  [style] VLM style read failed: {e}")
        img_desc = ''
    img_desc = (img_desc or '').strip().splitlines()
    img_desc = img_desc[0].strip() if img_desc else ''

    if not style_source:
        return _clean_descriptors(img_desc, style_source)

    # Reconcile the image read with knowledge of the named style.
    #
    # Design (validated empirically on real decks): FLUX quality lives in the
    # SHAPE of this descriptor line. The best-performing line for a cartoon deck
    # was "3D render, cel animation, cartoonish, exaggerated, vibrant, pastel,
    # neon, high-contrast, dynamic, playful, ..." — strong medium anchors first,
    # then rich palette + energy words. Weak lines (generic mood clichés, or a
    # lone "3D computer-generated" with no cartoon anchor) render off-style. A
    # 3B model's open-ended output has huge variance, so we stabilize 3 ways:
    #   1. FEW-SHOT the reconcile so the model mimics the good shape.
    #   2. Classify the named style's medium via a CONSTRAINED multiple-choice
    #      question (far more reliable than open generation for small models).
    #   3. ADD canonical medium anchors + the image's palette when missing —
    #      an additive floor, never subtractive stripping (stripping provably
    #      deletes good descriptors: "3D render" was part of the best line).
    system_msg = (
        "You produce style descriptors for the FLUX text-to-image model. You are "
        "given (a) a visual-style read of reference images and (b) the NAME of the "
        "intended style. Output ONE line of 10-16 comma-separated descriptors that "
        "best reproduce the intended style: medium anchors first, then rendering "
        "technique, then color palette words, then energy/mood words. Describe "
        "ONLY visual style — no subject, no proper nouns, no character or place "
        "names. Output ONLY the comma-separated descriptors."
    )
    fewshot = [
        {'role': 'user', 'content':
            "Image read: 2D animation, cartoonish, medium close-up, vibrant colors, "
            "pastel hues, high contrast, exaggerated expressions, bold outlines\n"
            "Intended style name: Saturday-morning sci-fi cartoon\nDescriptors:"},
        {'role': 'assistant', 'content':
            "3D render, cel animation, cartoonish, exaggerated expressions, bold "
            "clean outlines, vibrant, pastel, neon, high-contrast, dynamic, "
            "playful, bright, colorful"},
        {'role': 'user', 'content':
            "Image read: ink illustration, delicate lines, warm earth tones, "
            "pastel hues, dense botanical detail\n"
            "Intended style name: ligne claire\nDescriptors:"},
        {'role': 'assistant', 'content':
            "ink illustration, clean linework, bold ink outlines, flat cel color, "
            "dense intricate detail, pastel gradient palette, warm earth tones, "
            "whimsical, serene"},
    ]
    user_msg = (f"Image read: {img_desc or '(none)'}\n"
                f"Intended style name: {style_source}\nDescriptors:")
    try:
        import mlx_llm
        out = mlx_llm.chat(
            messages=[{'role': 'system', 'content': system_msg}, *fewshot,
                      {'role': 'user', 'content': user_msg}],
            model=text_model, max_tokens=140, temperature=0.2,
        )
    except Exception as e:
        print(f"  [style] descriptor reconcile failed: {e}")
        out = img_desc

    # Additive floor: canonical anchors for the named style's medium, plus the
    # image's palette words when the line carries no color. Anchors lead so the
    # strongest tokens sit earliest and every re-distill shares a stable,
    # known-good prefix (dedup keeps the first occurrence of any repeat).
    anchors = _medium_anchors(_classify_style_medium(style_source, text_model,
                                                     img_desc=img_desc))
    parts = [d.strip() for d in _clean_descriptors(out, style_source).split(',')
             if d.strip()]
    if not any(_is_color_descriptor(d) for d in parts):
        parts += _palette_descriptors(img_desc)
    return _clean_descriptors(', '.join(anchors + parts), style_source,
                              max_descriptors=18)


# Canonical anchor descriptors per medium class. These are the FLOOR for a named
# style's flux_style_prompt — they lead the line so re-distills share a stable,
# proven prefix regardless of how the LLM's open-ended tail rolls.
_MEDIUM_ANCHORS = {
    'cel animation': ['cel animation', 'cartoonish', 'bold clean outlines',
                      'flat cel shading', 'exaggerated expressions'],
    'ink illustration': ['ink illustration', 'clean linework', 'bold ink outlines',
                         'flat color', 'dense intricate detail'],
    'watercolor': ['watercolor painting', 'soft washes', 'visible pigment texture'],
    'oil painting': ['oil painting', 'visible brushstrokes', 'painterly texture'],
    '3d render': ['3D render', 'volumetric lighting', 'detailed surface texture'],
    'photograph': ['cinematic photograph', 'photographic lighting',
                   'shallow depth of field'],
    'comic book': ['comic book art', 'bold ink outlines', 'dynamic panel energy'],
    'pixel art': ['pixel art', 'crisp pixel edges', 'limited color palette'],
}


# Franchises with iconic characters: putting their NAME in the render prompt
# injects the cast (a literal Rick appeared as card art). For these, the name
# is used only as a KNOWLEDGE KEY at distill time — expanded into anonymous
# design-language cues — and never reaches the render prompt. Artist and
# movement names (Victo Ngai, ligne claire) have no cast to leak and stay.
_FRANCHISE_KEYWORDS = frozenset({
    'morty', 'rick', 'simpsons', 'spongebob', 'futurama', 'pokemon', 'pokémon',
    'disney', 'pixar', 'ghibli', 'looney', 'marvel', 'batman', 'naruto',
    'dragonball', 'zelda', 'mario', 'adventure', 'gravity', 'archer',
    'southpark', 'south',
})


# Artists and art movements are NEVER franchises — their names are pure style
# signal with no cast to leak, and the 3B yes/no fallback misclassifies them
# (it once flagged 'Moebius' as a franchise, silently dropping the strongest
# style prior from the render prompt).
_ARTIST_KEYWORDS = frozenset({
    'moebius', 'giraud', 'ngai', 'mucha', 'ligne', 'claire', 'ukiyo',
    'hokusai', 'otomo', 'mignola', 'frazetta', 'rembrandt', 'monet',
    'illustration', 'illustrator', 'artist', 'painting', 'watercolor',
    'surrealism', 'nouveau', 'deco', 'impressionism', 'woodblock',
})


def is_character_franchise(style_source: str, text_model: str = '') -> bool:
    """True when the style name refers to a fictional franchise/show whose
    characters could leak into generated art. Keyword map first; optional LLM
    yes/no for the long tail (skipped when text_model is '')."""
    tokens = set(style_source.lower().replace('&', ' ').replace('-', ' ').split())
    if tokens & _ARTIST_KEYWORDS:
        return False
    if tokens & _FRANCHISE_KEYWORDS:
        return True
    if not text_model:
        return False
    try:
        import mlx_llm
        reply = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content':
                    "Answer with exactly 'yes' or 'no'."},
                {'role': 'user', 'content':
                    "Is 'Studio Ghibli' a fictional franchise or show with "
                    "famous recognizable characters?"},
                {'role': 'assistant', 'content': "yes"},
                {'role': 'user', 'content':
                    "Is 'Victo Ngai' a fictional franchise or show with famous "
                    "recognizable characters?"},
                {'role': 'assistant', 'content': "no"},
                {'role': 'user', 'content':
                    f"Is '{style_source}' a fictional franchise or show with "
                    "famous recognizable characters?"},
            ],
            model=text_model, max_tokens=4, temperature=0.0)
        return 'yes' in (reply or '').lower()
    except Exception:
        return False


def _expand_design_language(style_source: str, text_model: str) -> list:
    """Expand a franchise name into anonymous design-language cues — HOW things
    are drawn (line quality, eye/face conventions, shading, backgrounds) with
    no character or franchise names. This carries the school's hand into the
    prompt without the cast."""
    try:
        import mlx_llm
        out = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content':
                    "You describe the visual DESIGN LANGUAGE of animated shows "
                    "for a text-to-image model. Output ONE line of 5-7 comma-"
                    "separated cues describing HOW things are drawn: line "
                    "quality, eye and face conventions, shading, backgrounds. "
                    "NEVER name a character, show, or franchise."},
                {'role': 'user', 'content': "Style: The Simpsons"},
                {'role': 'assistant', 'content':
                    "flat yellow-tinted skin tones, large round white eyes, "
                    "overbite mouths, thin even outlines, flat television-"
                    "cartoon shading, plain suburban backgrounds"},
                {'role': 'user', 'content': "Style: Studio Ghibli"},
                {'role': 'assistant', 'content':
                    "soft painted watercolor backgrounds, round gentle faces "
                    "with small features, delicate hand-drawn linework, lush "
                    "natural scenery, warm diffuse lighting"},
                {'role': 'user', 'content': f"Style: {style_source}"},
            ],
            model=text_model, max_tokens=90, temperature=0.2)
    except Exception as e:
        print(f"  [style] design-language expansion failed: {e}")
        return []
    line = _clean_descriptors(out or '', style_source, max_descriptors=7)
    return [d.strip() for d in line.split(',') if d.strip()]


# Deterministic style-name → medium mapping for well-known cases. Curated,
# token-matched (multi-word keys substring-matched). Keeps the anchor prefix
# stable for the names users actually type; the LLM handles the long tail.
_MEDIUM_KEYWORD_MAP = {
    'cel animation': frozenset({'morty', 'cartoon', 'animated', 'animation',
                                'anime', 'ghibli', 'spongebob', 'simpsons',
                                'futurama', 'disney', 'looney'}),
    'ink illustration': frozenset({'ligne', 'claire', 'ink', 'moebius', 'ngai',
                                   'woodblock', 'ukiyo', 'ukiyo-e', 'linework',
                                   'etching', 'tintin'}),
    '3d render': frozenset({'pixar', 'dreamworks', '3d', 'cgi', 'render',
                            'octane', 'unreal'}),
    'photograph': frozenset({'photo', 'photograph', 'photography', 'cinematic',
                             'noir', 'film'}),
    'watercolor': frozenset({'watercolor', 'watercolour', 'gouache'}),
    'oil painting': frozenset({'oil', 'impressionist', 'baroque', 'rembrandt',
                               'renaissance'}),
    'comic book': frozenset({'comic', 'manga', 'graphic-novel'}),
    'pixel art': frozenset({'pixel', '8-bit', '16-bit'}),
}


def _classify_style_medium(style_source: str, text_model: str,
                           img_desc: str = '') -> str:
    """Classify a named style's primary medium via constrained multiple choice.

    Small models answer a pick-one-label question far more reliably than they
    describe a medium in open generation — but only with few-shot examples
    (otherwise a 3B model shows hard recency bias and echoes the LAST label) and
    with the VLM's image read as evidence (name recognition alone is fragile:
    'Rick & Morty' classified as pixel art until the read supplied '2D
    animation, cartoonish...'). Returns one of the _MEDIUM_ANCHORS keys, or ''
    when unknown/unparseable (no anchors applied — same behavior as before).
    """
    # Deterministic keyword map first — the LLM classifier drifts run-to-run
    # ('cel animation' one roll, 'comic book' the next) and stability of the
    # anchor prefix is the whole point. LLM only for unrecognized names.
    src_tokens = set(style_source.lower().replace('&', ' ').split())
    src_lower = style_source.lower()
    for medium, keys in _MEDIUM_KEYWORD_MAP.items():
        if src_tokens & keys or any(' ' in k and k in src_lower for k in keys):
            return medium

    labels = list(_MEDIUM_ANCHORS.keys())
    # '&' derails the small model's name recognition; normalize it.
    src = style_source.replace('&', 'and')
    try:
        import mlx_llm
        reply = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content':
                    "You classify the primary visual medium of an art style, "
                    "given its name and a visual read of reference images. "
                    f"Reply with EXACTLY one label from: {', '.join(labels)}. "
                    "Nothing else."},
                {'role': 'user', 'content':
                    "Style: Studio Ghibli\nImage read: 2D animation, painted "
                    "backgrounds, soft colors"},
                {'role': 'assistant', 'content': "cel animation"},
                {'role': 'user', 'content':
                    "Style: film noir cinematography\nImage read: black and "
                    "white photograph, dramatic shadows"},
                {'role': 'assistant', 'content': "photograph"},
                {'role': 'user', 'content':
                    "Style: Moebius ligne claire\nImage read: ink illustration, "
                    "clean lines, flat color"},
                {'role': 'assistant', 'content': "ink illustration"},
                {'role': 'user', 'content':
                    "Style: Pixar\nImage read: 3D render, smooth surfaces, soft "
                    "lighting"},
                {'role': 'assistant', 'content': "3d render"},
                {'role': 'user', 'content':
                    f"Style: {src}\nImage read: {img_desc or '(none)'}"},
            ],
            model=text_model, max_tokens=10, temperature=0.0,
        )
    except Exception as e:
        print(f"  [style] medium classification failed: {e}")
        return ''
    reply = (reply or '').lower()
    # Longest label first so 'ink illustration' can't lose to a bare substring.
    for label in sorted(labels, key=len, reverse=True):
        if label in reply:
            return label
    return ''


def _medium_anchors(medium: str) -> list:
    """Canonical anchor descriptors for a classified medium ([] if unknown)."""
    return list(_MEDIUM_ANCHORS.get(medium, []))


# Color/palette detection for palette preservation (see build_flux_style_descriptors).
_COLOR_WORDS = frozenset({
    'red', 'orange', 'yellow', 'green', 'blue', 'teal', 'cyan', 'purple',
    'violet', 'magenta', 'pink', 'brown', 'black', 'white', 'gray', 'grey',
    'gold', 'silver', 'beige', 'cream', 'coral', 'salmon', 'mustard', 'olive',
    'lavender', 'turquoise', 'crimson', 'scarlet', 'amber', 'ochre', 'indigo',
    'maroon', 'tan', 'peach', 'mint', 'navy', 'burgundy', 'sepia', 'bronze',
    'copper', 'earthy', 'earth',
    # artist's-vocabulary hues — these carry the most transfer signal
    'vermilion', 'chartreuse', 'saffron', 'cerulean', 'viridian', 'umber',
    'carmine', 'fuchsia', 'lime', 'aqua', 'slate', 'charcoal', 'ivory',
    'khaki', 'plum', 'rose', 'ruby', 'emerald', 'sapphire', 'jade', 'mauve',
    'taupe', 'terracotta', 'cobalt', 'ultramarine', 'ozone',
})
_PALETTE_KEYWORDS = ('palette', 'hue', 'tone', 'pastel', 'monochrom', 'neon',
                     'saturat', 'tint', 'duotone', 'colored', 'coloured')


def _is_color_descriptor(phrase: str) -> bool:
    """True if a descriptor is about color/palette (a color name, or a palette
    keyword like 'pastel hues', 'warm tones', 'monochromatic')."""
    import re as _re
    p = phrase.lower()
    if any(k in p for k in _PALETTE_KEYWORDS):
        return True
    return any(w in _COLOR_WORDS for w in _re.findall(r"[a-z]+", p))


def _palette_descriptors(text: str) -> list:
    """Extract the color/palette descriptors from a comma-separated line."""
    return [d.strip() for d in (text or '').split(',')
            if d.strip() and _is_color_descriptor(d)]


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
        if not phrase:
            continue
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(phrase)
        if len(unique) >= max_descriptors:
            break
    return ', '.join(unique)


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
