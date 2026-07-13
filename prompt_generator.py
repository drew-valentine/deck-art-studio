#!/usr/bin/env python3
"""
Art prompt generator for Deck Art Studio.

Generates descriptive art prompts for MTG cards based on their name,
type, oracle text, and creature types. Supports both rule-based and
AI-enhanced prompt generation.
"""

import re


# ---------------------------------------------------------------------------
# Color association for atmosphere hints
# ---------------------------------------------------------------------------
COLOR_VIBES = {
    'W': 'bright light, open skies, warm glow',
    'U': 'deep water, cool mist, flowing currents',
    'B': 'deep shadows, dim light, muted tones',
    'R': 'warm light, intense color, bold energy',
    'G': 'dense foliage, rich earth, living growth',
}


# ---------------------------------------------------------------------------
# Rule-based prompt generation
# ---------------------------------------------------------------------------
def generate_subject_description(card: dict) -> str:
    """Generate a vivid subject description from card data (rule-based).

    Uses the card's name, type, oracle text, and color identity to
    craft a descriptive scene for the art generator.
    """
    name = card.get('name', 'Unknown')
    card_type = card.get('card_type', 'other')
    type_line = card.get('type_line', '')
    oracle = card.get('oracle_text', '')
    flavor = card.get('flavor_text', '')
    colors = card.get('color_identity', card.get('colors', []))
    power = card.get('power')
    toughness = card.get('toughness')
    loyalty = card.get('loyalty')

    # Build color atmosphere
    color_hints = [COLOR_VIBES.get(c, '') for c in colors if c in COLOR_VIBES]
    atmosphere = ', '.join(color_hints) if color_hints else 'mysterious magical energy'

    # Extract creature subtypes
    subtypes = ''
    if '—' in type_line or '\u2014' in type_line:
        sub_part = re.split(r'[—\u2014]', type_line, 1)[1].strip()
        subtypes = sub_part

    # Extract keywords from oracle text
    keywords = _extract_keywords(oracle)

    # Generate based on card type
    if card_type == 'creature':
        return _describe_creature(name, subtypes, oracle, power, toughness, keywords, atmosphere)
    elif card_type == 'planeswalker':
        return _describe_planeswalker(name, subtypes, oracle, loyalty, atmosphere)
    elif card_type == 'land':
        return _describe_land(name, type_line, oracle, atmosphere)
    elif card_type == 'artifact':
        return _describe_artifact(name, type_line, oracle, keywords, atmosphere)
    elif card_type == 'enchantment':
        return _describe_enchantment(name, oracle, keywords, atmosphere, flavor)
    elif card_type == 'instant':
        return _describe_spell(name, oracle, keywords, atmosphere, 'instant')
    elif card_type == 'sorcery':
        return _describe_spell(name, oracle, keywords, atmosphere, 'sorcery')
    else:
        return f"{name} — a magical entity surrounded by {atmosphere}."


def _extract_keywords(oracle: str) -> list[str]:
    """Extract MTG keywords and ability words from oracle text."""
    keyword_list = [
        'flying', 'trample', 'haste', 'vigilance', 'deathtouch', 'lifelink',
        'menace', 'reach', 'first strike', 'double strike', 'hexproof',
        'indestructible', 'flash', 'defender', 'prowess', 'partner',
        'cascade', 'storm', 'flashback', 'overload', 'coin flip',
        'treasure', 'token', 'counter', 'sacrifice', 'exile', 'destroy',
        'draw', 'scry', 'mill', 'burn', 'damage', 'copy',
    ]
    found = []
    oracle_lower = oracle.lower()
    for kw in keyword_list:
        if kw in oracle_lower:
            found.append(kw)
    return found


def _describe_creature(name, subtypes, oracle, power, toughness, keywords, atmosphere):
    """Generate description for a creature card."""
    # Size interpretation
    try:
        p, t = int(power or 0), int(toughness or 0)
    except (ValueError, TypeError):
        p, t = 3, 3  # default for */* creatures

    if p >= 7:
        size = 'colossal, towering'
    elif p >= 5:
        size = 'massive, powerful'
    elif p >= 3:
        size = 'imposing, strong'
    elif p >= 1:
        size = 'agile, fierce'
    else:
        size = 'small but cunning'

    # Ability flavor
    ability_flavor = ''
    if 'flying' in keywords:
        ability_flavor += ' with wings spread wide, soaring through the air'
    if 'trample' in keywords:
        ability_flavor += ', crushing everything underfoot'
    if 'haste' in keywords:
        ability_flavor += ', blazing with speed and urgency'
    if 'double strike' in keywords:
        ability_flavor += ', striking with devastating twin blows'
    if 'deathtouch' in keywords:
        ability_flavor += ', dripping with lethal venom'
    if 'coin flip' in keywords:
        ability_flavor += ', surrounded by spinning coins and chaotic fortune'

    # Defining anatomy — must survive the LLM rewrite. A Cyclops has exactly ONE
    # eye; creatures named "...Eye of..." (e.g. Okaun/Zndrsplt) are one-eyed by
    # flavor. Without this the model defaults to a normal two-eyed face.
    anatomy = ''
    sub_low = (subtypes or '').lower()
    name_low = (name or '').lower()
    if 'cyclops' in sub_low or re.search(r'\beye of\b', name_low) or 'one-eyed' in name_low:
        anatomy = ' with a SINGLE large central eye (exactly one eye, cyclopean — never two eyes)'

    subtype_desc = f" {subtypes}" if subtypes else ''
    return (
        f"A {size}{subtype_desc} called {name}{anatomy}{ability_flavor}, "
        f"{atmosphere}."
    )


def _describe_planeswalker(name, subtypes, oracle, loyalty, atmosphere):
    """Generate description for a planeswalker card."""
    return (
        f"The planeswalker {name}, a powerful mage figure radiating with "
        f"{atmosphere}. They stand in a dramatic pose channeling immense "
        f"magical energy, their form surrounded by swirling mana and "
        f"otherworldly power. Loyalty {loyalty}."
    )


def _describe_land(name, type_line, oracle, atmosphere):
    """Generate description for a land card."""
    # Check for basic land types
    basic_types = {
        'Plains': 'sweeping golden plains under a radiant sky',
        'Island': 'a mystical island with crystalline waters and arcane spires',
        'Swamp': 'a dark, misty swamp with twisted trees and eerie lights',
        'Mountain': 'a dramatic volcanic mountain with rivers of lava and jagged peaks',
        'Forest': 'a primeval forest with towering ancient trees and bioluminescent flora',
    }
    for basic, desc in basic_types.items():
        if basic.lower() in type_line.lower():
            return f"{desc}, infused with {atmosphere}. The landscape of {name}."

    # Non-basic lands
    has_tap = '{T}' in (oracle or '')
    mana_hint = ''
    if oracle:
        if '{W}' in oracle: mana_hint = 'white mana'
        elif '{U}' in oracle: mana_hint = 'blue mana'
        elif '{B}' in oracle: mana_hint = 'black mana'
        elif '{R}' in oracle: mana_hint = 'red mana'
        elif '{G}' in oracle: mana_hint = 'green mana'
        elif '{C}' in oracle: mana_hint = 'colorless mana'

    mana_desc = f', pulsing with {mana_hint}' if mana_hint else ''
    return (
        f"A fantastical landscape depicting {name} — a magical location "
        f"of power and wonder{mana_desc}. The terrain radiates with "
        f"{atmosphere}, creating an otherworldly vista."
    )


def _describe_artifact(name, type_line, oracle, keywords, atmosphere):
    """Generate description for an artifact card."""
    is_equipment = 'equipment' in type_line.lower()
    is_vehicle = 'vehicle' in type_line.lower()

    if is_equipment:
        return (
            f"A legendary piece of equipment — {name} — gleaming with "
            f"magical enchantment and {atmosphere}. The weapon or armor "
            f"floats in the air, radiating power and ancient craftsmanship."
        )
    elif is_vehicle:
        return (
            f"A fantastical magical vehicle — {name} — powered by "
            f"{atmosphere}. An imposing machine or vessel of wonder "
            f"and arcane engineering."
        )
    else:
        coin_desc = ''
        if 'coin flip' in keywords or 'coin' in (oracle or '').lower():
            coin_desc = ' Spinning coins and elements of chance surround it.'
        literal = _literal_object_from_name(name)
        if literal:
            # The name literally names a physical object/body part (e.g. "Krark's
            # Thumb" -> a thumb). Depict THAT, not a generic runed amulet.
            return (
                f"{name} — depicted as {literal}, treated as a prized magical "
                f"relic glowing with {atmosphere}.{coin_desc}"
            )
        return (
            f"A powerful magical artifact — {name} — hovering and glowing "
            f"with {atmosphere}. An intricate object of arcane craftsmanship "
            f"with runes and energy emanating from its form.{coin_desc}"
        )


# Artifact names that literally name a physical object/body part — map the head
# noun to a concrete depiction so the art shows the actual thing, not a generic
# glowing amulet. The trailing noun of the name is the object.
_LITERAL_OBJECT_NOUNS = {
    'thumb': 'a severed goblin thumb kept as a lucky talisman, leathery and ringed',
    'hand': 'a preserved severed hand',
    'eye': 'a single disembodied eye',
    'skull': 'an ornate skull',
    'heart': 'a glowing preserved heart',
    'horn': 'a great curved horn',
    'claw': 'a massive curved claw',
    'fang': 'a long curved fang',
    'tooth': 'a large tooth',
    'crown': 'an ornate crown',
    'ring': 'a single ornate ring',
    'sword': 'a sword', 'blade': 'a blade', 'axe': 'an axe', 'dagger': 'a dagger',
    'spear': 'a spear', 'shield': 'a shield', 'hammer': 'a war hammer',
    'staff': 'a staff', 'wand': 'a wand', 'orb': 'a glowing orb',
    'amulet': 'an amulet', 'talisman': 'a talisman', 'medallion': 'a medallion',
    'mask': 'a mask', 'helm': 'a helm', 'gauntlet': 'a gauntlet',
    'chalice': 'a chalice', 'goblet': 'a goblet', 'lantern': 'a lantern',
    'mirror': 'an ornate mirror', 'bell': 'a bell', 'key': 'an ornate key',
    'banner': 'a banner', 'scepter': 'a scepter', 'signet': 'a signet ring',
    'coin': 'a large ornate coin', 'die': 'a die', 'idol': 'an idol',
}


def _literal_object_from_name(name: str):
    """If the artifact's name ends in a concrete object/body-part noun, return a
    short literal depiction of it (e.g. "Krark's Thumb" -> a severed thumb)."""
    words = re.findall(r"[A-Za-z]+", (name or '').lower())
    for w in reversed(words):  # the head noun is usually last ("...'s Thumb")
        if w in _LITERAL_OBJECT_NOUNS:
            return _LITERAL_OBJECT_NOUNS[w]
    return None


def _describe_enchantment(name, oracle, keywords, atmosphere, flavor=''):
    """Generate description for an enchantment card.

    Enchantments have no physical object, so the OLD anchor defaulted to
    "swirling abstract magical energy / flowing shapes" — which made every
    enchantment render as a generic glowing vortex. Instead, anchor on the
    card's actual STORY (flavor + rules) so the art depicts a concrete scene
    (the warriors, ritual, place, or event the enchantment represents).
    """
    coin_desc = ''
    if 'coin flip' in keywords or 'coin' in (oracle or '').lower():
        coin_desc = ' Elements of chance and spinning coins feature in the scene.'
    # Use ONLY flavor text (clean prose) as the story anchor — NOT raw oracle,
    # which is rules syntax with mana symbols ('{T}', '{2}', reminder text). This
    # string is the fallback returned to FLUX verbatim when the prompt LLM is
    # unavailable, so any rules text here would be baked into the art as garbled
    # symbols. Strip stray '{...}' tokens defensively.
    story = re.sub(r'\{[^}]*\}', '', flavor or '').strip()
    story_line = f" The scene is drawn from its story: {story}" if story else ''
    return (
        f"A concrete illustrated scene representing the enchantment {name} — "
        f"depict the people, creatures, place, or event it embodies (not abstract "
        f"energy), set in an atmosphere of {atmosphere}.{story_line}{coin_desc}"
    )


def _describe_spell(name, oracle, keywords, atmosphere, spell_type):
    """Generate description for an instant or sorcery."""
    if spell_type == 'instant':
        timing = 'A sudden burst of'
    else:
        timing = 'A grand invocation of'

    action_hint = ''
    if 'damage' in keywords or 'destroy' in keywords:
        action_hint = ' Destructive energy erupts across the scene.'
    elif 'draw' in keywords or 'scry' in keywords:
        action_hint = ' Knowledge and visions flow through crystalline light.'
    elif 'counter' in keywords:
        action_hint = ' Opposing magical forces collide and shatter.'
    elif 'copy' in keywords:
        action_hint = ' Mirrors and reflections multiply through the air.'
    elif 'coin flip' in keywords:
        action_hint = ' Spinning coins tumble through the magical energy.'

    return (
        f"{timing} magical power — {name} — unleashing {atmosphere} "
        f"in a dramatic display of arcane force.{action_hint}"
    )


# ---------------------------------------------------------------------------
# Full prompt assembly
# ---------------------------------------------------------------------------
def generate_style_preamble_from_analysis(style_description: str,
                                          style_source: str = '') -> str:
    """Build an art prompt preamble from a vision-analyzed style description.

    Takes the output of vision_analyzer.analyze_inspiration_style()
    (structured attributes + prose) and appends the no-text constraint.

    If style_source is provided (e.g. "Studio Ghibli"), it ALWAYS becomes
    the Source: line — replacing any LLM-generated source (which is often
    "Original") so _split_preamble() puts the proper noun at the front of
    the CLIP-visible style tag.

    Returns empty string if no inspiration art uploaded.
    """
    if not style_description or not style_description.strip():
        return ''

    desc = style_description.strip()

    # Ensure Source line uses the user's explicit style_source (if provided).
    # The user's label is authoritative — the LLM often outputs "Source: Original"
    # which is weaker and loses the proper noun that CLIP leverages for style.
    if style_source:
        lines = desc.split('\n')
        replaced = False
        for i, line in enumerate(lines):
            stripped = line.strip().lstrip('- ')
            if stripped.startswith('Source:'):
                lines[i] = f"Source: {style_source}"
                replaced = True
                break
        if replaced:
            desc = '\n'.join(lines)
        else:
            desc = f"Source: {style_source}\n{desc}"

    return (
        f"{desc} "
        "No text, no words, no letters, no card frame, no borders "
        "— PURE ART ONLY."
    )


_STYLE_ATTR_KEYS = ('Source:', 'Art Style:', 'Colors:', 'Vibe:', 'Faces:', 'Technique:')


def _split_preamble(preamble: str) -> tuple[str, str]:
    """Split a preamble into (style_tag, prose).

    The style tag contains the structured key-value attribute lines
    (Source, Art Style, Colors, Vibe, Faces) — compact enough for
    CLIP's ~77 token window on local models.

    The prose contains everything after — rich description, technique
    details, art direction, and "No text..." constraint for cloud models.

    Skips blank lines and continuation bullets (indented lines under
    Technique:) when scanning for attribute keys. Prose starts at the
    first non-blank line that isn't a recognized key or continuation.

    For legacy prose-only preambles, the style tag is the first 15 words
    and prose is the full preamble.
    """
    lines = preamble.split('\n')
    attr_lines = []
    last_structured_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue  # Skip blank lines between attribute sections

        # Strip bullet prefix for key matching (Ollama produces "- Colors: ...")
        match_str = stripped
        if match_str.startswith('- '):
            match_str = match_str[2:].lstrip()

        if any(match_str.startswith(k) for k in _STYLE_ATTR_KEYS):
            # Only include in style tag if there's actual content after the colon
            colon_pos = match_str.index(':')
            value = match_str[colon_pos + 1:].strip()
            if value:
                attr_lines.append(match_str)  # Use un-bulleted form
            last_structured_idx = i
        elif stripped.startswith('-') and last_structured_idx >= 0:
            # Unrecognized continuation bullet (e.g. "- Rendering Technique:")
            last_structured_idx = i
        else:
            # Non-attribute, non-continuation line — prose starts here
            break

    if attr_lines:
        # Ensure Source: is always first in the style tag (most impactful
        # for CLIP when it names a franchise like "Studio Ghibli")
        source_lines = [l for l in attr_lines if l.startswith('Source:')]
        other_lines = [l for l in attr_lines if not l.startswith('Source:')]
        attr_lines = source_lines + other_lines

        style_tag = ' '.join(attr_lines)
        # Cap at ~25 words so CLIP has room for the subject description.
        # Ollama produces verbose multi-sentence values per key; GPT-4o is
        # concise. Either way, truncating preserves the most important info
        # (Source, Art Style beginning) while leaving token budget for subject.
        words = style_tag.split()
        if len(words) > 25:
            style_tag = ' '.join(words[:25]).rstrip('.,;—-')
        # Prose = everything from after the last structured/continuation line
        prose_start = last_structured_idx + 1
        prose = '\n'.join(lines[prose_start:]).strip()
        return style_tag, prose

    # Fallback for legacy prose-style descriptions
    raw = preamble
    prefix = "Fantasy illustration in the following art style: "
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    suffix_marker = " No text, no words"
    idx = raw.find(suffix_marker)
    if idx > 0:
        raw = raw[:idx]
    style_tag = ' '.join(raw.split()[:15]).rstrip('.,;—-')
    return style_tag, preamble


def generate_prompt(card: dict, style_preamble: str = None) -> str:
    """Generate a complete art prompt for a card.

    When a style preamble exists (from inspiration art), prepends a short
    style tag (~15 words of raw style essence) so local models see both
    style and subject within CLIP's ~77 token window, then appends the
    full preamble after --- for cloud models.
    Without inspiration, returns subject-only prompts.
    """
    preamble = style_preamble or ''

    # Card Back gets a special decorative prompt instead of a scene.
    # IMPORTANT: Describe the ART ITSELF — ornamental pattern, central motif,
    # border filigree — NOT "a card back" which AI interprets as a photo of
    # the physical back side of a card.
    if card.get('type_line') == 'Card Back' or (card.get('name') or '').lower().startswith('card back'):
        card_back_subject = (
            "An ornate symmetrical decorative pattern filling the entire image. "
            "Central medallion surrounded by intricate border filigree and "
            "repeating geometric motifs. Rich, detailed ornamental illustration "
            "with no text, no card frame, no characters."
        )
        if preamble:
            style_tag, prose = _split_preamble(preamble)
            return f"{style_tag}.\n\n{card_back_subject}\n\n---\n\n{prose}"
        return card_back_subject

    subject = generate_subject_description(card)
    if preamble:
        style_tag, prose = _split_preamble(preamble)
        return f"{style_tag}.\n\n{subject}\n\n---\n\n{prose}"
    return subject


def generate_prompts_for_deck(cards: list[dict], style_preamble: str = None) -> list[dict]:
    """Generate art prompts for a full deck.

    Returns list of {name, prompt} entries.
    """
    prompts = []
    for card in cards:
        prompt = generate_prompt(card, style_preamble)
        prompts.append({
            'name': card['name'],
            'prompt': prompt,
        })
    return prompts


# ---------------------------------------------------------------------------
# AI-enhanced prompt generation (uses OpenAI or local Ollama)
# ---------------------------------------------------------------------------
def generate_subject_with_ai(card: dict, openai_client=None, backend: str = 'openai',
                              local_model: str = 'llama3.1:8b',
                              style_hint: str = '', steer: str = '') -> str:
    """Use an LLM to generate a subject description tailored to the deck's style.

    Sends the LLM a rule-based description as a reference anchor plus
    card-type-specific guidance.  The LLM enhances the baseline rather
    than inventing from scratch, preventing category errors (e.g. Sol Ring
    depicted as a sun landscape instead of a ring artifact).

    If style_hint is provided (e.g. "Wes Anderson Film — Minimalist, Flat"),
    the LLM will tailor its tone to match the intended aesthetic.

    If `steer` is provided (free-text user direction, e.g. "at night",
    "underwater", "more whimsical, less grand"), the scene is pushed firmly in
    that direction — the lever for escaping a theme the regenerator keeps circling.

    Supports both OpenAI (cloud) and Ollama (local) backends.
    Falls back to rule-based if AI fails.
    """
    name = card.get('name', 'Unknown')
    type_line = card.get('type_line', '')
    oracle = card.get('oracle_text', '')
    flavor = card.get('flavor_text', '')
    card_type = card.get('card_type', 'other')

    # Rule-based description as anchor — ensures correct subject identity
    base_desc = generate_subject_description(card)

    # Type-specific guidance so the LLM knows WHAT to depict
    # NO_CHARACTER cards must NOT get a person/face/creature as the focal point —
    # the deck theme (e.g. sci-fi → android faces) otherwise hijacks the subject.
    _no_character = card_type in ('artifact', 'enchantment', 'land', 'instant', 'sorcery')
    type_guidance = {
        'artifact': 'Depict the artifact OBJECT itself, filling the frame. If the card NAME literally names a physical thing or body part (e.g. "Krark\'s Thumb" = a thumb, "Sol Ring" = a ring, "Sword of X" = a sword), depict THAT literal object as the relic — do NOT substitute a generic glowing disc, amulet, or runed orb. NOT a landscape, NOT a person.',
        'enchantment': 'Depict the SCENE the enchantment represents — the people, creatures, place, ritual, or event drawn from its flavor and rules text (e.g. an army of warriors growing stronger under a hopeful dawn, a blessing settling over a battlefield). Do NOT default to abstract swirling energy, a glowing aura, or a magical vortex — give it concrete subject matter.',
        'instant': 'Depict the dramatic moment of the spell being cast — the action and energy itself.',
        'sorcery': 'Depict the spell being cast — the ritual, the gathering of power.',
        'land': 'Depict the LOCATION — terrain, architecture, or natural formation. NO central character.',
        'creature': 'Depict the creature itself as the single focal point.',
        'planeswalker': 'Depict the planeswalker character in a dramatic pose.',
    }
    guidance = type_guidance.get(card_type, 'Depict the subject described by the card name.')

    system_msg = (
        "You write art descriptions for card illustrations. "
        "Given an MTG card and a reference description, rewrite it into a more "
        "creative and evocative 2-3 sentence scene. "
        "THE #1 RULE: the card's own subject (from the reference description) MUST "
        "be the single, unmistakable, dominant focal point that fills the frame. "
        "Enhance the imagery; do NOT change WHAT is depicted and do NOT introduce a "
        "different focal subject. Setting and atmosphere are BACKGROUND only — they "
        "must never replace, crowd out, or upstage the card's subject. "
        "OPENING RULE (critical): the FIRST sentence must open with the subject "
        "itself — name it, and for creatures state its CREATURE TYPE as an "
        "appositive right after the name (e.g. 'Okaun, Eye of Chaos, a Cyclops "
        "Berserker, storms...'), then place it in the scene. NEVER open with the "
        "setting, weather, or atmosphere ('In the heart of the swirling mist...') "
        "— the image model paints whatever comes first, and setting-first "
        "openings produce subjectless art. "
        "Be inventive and VARY it each time: choose a fresh setting, camera angle, "
        "distance, time of day, weather, and composition so re-rolls feel distinct "
        "rather than repeating the same scene — but always keep the same focal subject. "
        "PRESERVE the subject's defining anatomy stated in the reference: if it says "
        "a SINGLE / central / one eye (a cyclops), the creature has exactly ONE eye — "
        "write 'eye' (singular), NEVER 'eyes', and never give it two. Likewise keep "
        "any other stated defining features. "
        "ANCHOR THE SCENE IN THE CARD'S OWN STORY: draw concrete subjects, "
        "characters, places, and events from the card's flavor text and rules. "
        "AVOID generic abstract filler — do NOT default to 'swirling magical "
        "energy', a 'luminescent/ethereal aura', a 'glowing vortex', or 'flowing "
        "shapes'. Depict real, recognizable subject matter (people, creatures, "
        "settings, objects, action), even for spells and enchantments. "
        "Do NOT include any style directions — just describe the subject matter."
    )
    if _no_character:
        system_msg += (
            "\n\nThis card depicts an OBJECT or PLACE, not a character. Do NOT make a "
            "person, face, head, figure, or creature the focal point. Any incidental "
            "figures must stay small and in the background. The object/location is the star."
        )
    if style_hint:
        # Detect dark/horror mood from the style hint
        _hint_lower = style_hint.lower()
        _dark_moods = ('dark', 'horror', 'ominous', 'sinister', 'macabre', 'eerie',
                       'foreboding', 'haunting', 'grim', 'dread', 'gothic', 'oppressive')
        _is_dark = any(w in _hint_lower for w in _dark_moods)

        # Extract themes from style hint if present
        _themes = ''
        if '| Themes:' in style_hint:
            _themes = style_hint.split('| Themes:')[-1].strip()

        if _is_dark:
            system_msg += (
                f"\n\nCRITICAL — The art style is: {style_hint}. "
                "The mood is DARK and OMINOUS. Your descriptions MUST reflect this "
                "atmosphere — use foreboding, menacing, eerie, unsettling imagery. "
                "Describe shadows, decay, dread, twisted forms, oppressive skies, "
                "and sinister details. Do NOT make scenes pretty or heroic — make "
                "them haunting and disturbing."
            )
            if _themes:
                system_msg += (
                    f"\n\nTHEMATIC ELEMENTS — The deck's visual identity includes: {_themes}. "
                    "Let these motifs color the BACKGROUND and atmosphere only — they must "
                    "NOT become the focal point or replace the card's own subject. Keep the "
                    "card's subject dominant and clearly readable; the themes are set dressing."
                )
        else:
            system_msg += (
                f"\n\nCRITICAL — The art style is: {style_hint}. "
                "Your descriptions MUST match this aesthetic. Describe calm, specific, "
                "concrete visual details — colors, composition, posture, objects, lighting. "
                "NEVER use dramatic fantasy language like 'maelstrom', 'volcanic fury', "
                "'arcane energy', 'swirling vortex', 'blazing', 'exploding', 'chaotic'. "
                "Write as if describing a scene in a calm, artful film still."
            )
            if _themes:
                system_msg += (
                    f"\n\nTHEMATIC ELEMENTS — The deck's visual identity includes: {_themes}. "
                    "Let these motifs appear only in the BACKGROUND and atmosphere so cards "
                    "feel cohesive — never let them replace or upstage the card's own subject."
                )

    if steer and steer.strip():
        system_msg += (
            f"\n\nUSER DIRECTION (HIGHEST PRIORITY) — Re-imagine the scene to satisfy "
            f"this request: \"{steer.strip()}\". Treat it as a strong steer: change the "
            f"setting, action, framing, time, or mood as needed to honor it, while still "
            f"depicting the card's subject. Make the result clearly DIFFERENT from a "
            f"generic version — don't fall back to the usual scene."
        )

    user_msg = (
        f"Card: {name}\nType: {type_line}\nRules: {oracle}\n"
        + (f"Flavor text (use this as the THEMATIC ANCHOR for the scene): {flavor}\n" if flavor else "")
        + f"Direction: {guidance}\n"
        + (f"User steer (honor this above all): {steer.strip()}\n" if steer and steer.strip() else "")
        + f"Reference description: {base_desc}\n"
        f"Ground the scene in this card's flavor and rules — concrete subjects, not "
        f"abstract energy. Rewrite into a detailed scene description (2-3 sentences):"
    )

    try:
        import mlx_llm
        out = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            model=local_model,
            max_tokens=200,
            temperature=0.8,  # varied between re-rolls; 0.95 made the 3B model
                              # degenerate into word-salad tails ("waveform GS cave
                              # events super intend impact"), so keep it lower.
        )
        return _ensure_creature_type_in_prompt(out, card)
    except Exception as e:
        print(f"  [prompt_gen] AI failed for {name}: {e}, using rule-based")
        return generate_subject_description(card)


def _ensure_creature_type_in_prompt(text: str, card: dict) -> str:
    """Deterministic guarantee: a creature's prompt names its creature type.

    The system prompt asks for the type as an appositive after the name
    ("Okaun, Eye of Chaos, a Cyclops Berserker, ..."), but an instruction to a
    small model is a suggestion — the creative rewrite frequently drops it.
    The type line is one of the strongest identity anchors the image model can
    get (it disambiguates WHAT the creature is), so when the full type phrase
    is missing we inject it right after the card name in the text."""
    if not text or card.get('card_type') != 'creature':
        return text
    type_line = card.get('type_line', '')
    if '—' not in type_line and '—' not in type_line:
        return text
    subtypes = re.split(r'[——]', type_line, 1)[1].strip()
    if not subtypes or subtypes.lower() in text.lower():
        return text
    name = card.get('name', '')
    article = 'an' if subtypes[:1].lower() in 'aeiou' else 'a'
    appositive = f", {article} {subtypes},"
    if name and name in text:
        # "Okaun, Eye of Chaos sits..." -> "Okaun, Eye of Chaos, a Cyclops
        # Berserker, sits..."
        return text.replace(name, f"{name}{appositive}", 1)
    if name:
        return f"{name}, {article} {subtypes} — {text}"
    return text


def generate_prompts_with_ai(
    cards: list[dict],
    openai_client=None,
    style_preamble: str = None,
    progress_callback=None,
    backend: str = 'openai',
    local_model: str = 'llama3.1:8b',
) -> list[dict]:
    """Generate AI-enhanced art prompts for a full deck.

    Supports both OpenAI (cloud) and Ollama (local) backends.
    Falls back to rule-based if AI is unavailable.
    """
    import time

    preamble = style_preamble or ''
    prompts = []
    total = len(cards)

    for i, card in enumerate(cards):
        if progress_callback:
            progress_callback(i + 1, total, card['name'])

        subject = generate_subject_with_ai(
            card, openai_client, backend=backend, local_model=local_model
        )
        if preamble:
            style_tag, prose = _split_preamble(preamble)
            prompt = f"{style_tag}.\n\n{subject}\n\n---\n\n{prose}"
        else:
            prompt = subject
        prompts.append({
            'name': card['name'],
            'prompt': prompt,
        })
        time.sleep(0.05)  # Brief rate limit

    return prompts


# ---------------------------------------------------------------------------
# Source-canonical style descriptors for FLUX
# ---------------------------------------------------------------------------
def build_source_style_prompt(style_source: str, backend: str = 'local',
                              local_model: str = 'llama3.1:8b') -> str:
    """Ask the LLM for a named style's *canonical* visual descriptors for FLUX.

    The vision model often mislabels a recognizable style's medium (e.g. tagging
    Wes Anderson live-action films as "digital painting"), and those wrong tokens
    fight the style. FLUX knows famous named styles well, and so does the LLM —
    so for a recognized source we generate accurate descriptors from the source
    NAME (composition, framing, palette, lighting, mood, signature technique)
    rather than trusting the per-image vision distillation.

    Returns a single comma-separated descriptor line, or '' on failure.
    """
    if not style_source or not style_source.strip():
        return ''
    system_msg = (
        "You are a prompt engineer for the FLUX text-to-image model. Given the name "
        "of a visual/artistic style, output ONE line of 10-16 comma-separated visual "
        "descriptors that capture that style's MOST DISTINCTIVE, RECOGNIZABLE look so "
        "FLUX reproduces it unmistakably.\n"
        "Include concrete, specific phrases for: the actual medium (e.g. 'live-action "
        "35mm film still', 'cel animation', 'oil painting'); composition and framing "
        "(e.g. 'perfectly symmetrical', 'centered head-on framing', 'flat planimetric "
        "staging'); color palette (specific hues); lighting; and mood.\n"
        "Rules: be SPECIFIC to THIS style, not generic. Use multi-word descriptor "
        "phrases, not single vague words. Do NOT output category labels like "
        "'medium' or 'composition' themselves — output the actual descriptive values. "
        "No subject matter, no proper nouns, no character/place names. Output ONLY the "
        "comma-separated descriptor phrases, nothing else."
    )
    user_msg = (
        "Style: Studio Ghibli\nDescriptors: hand-painted cel animation, lush "
        "watercolor backgrounds, soft rounded character designs, gentle naturalistic "
        "lighting, painterly clouds, warm nostalgic palette, whimsical, serene\n\n"
        f"Style: {style_source}\nDescriptors:"
    )
    try:
        import mlx_llm
        out = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            model=local_model, max_tokens=120, temperature=0.4,
        )
        # Single line, strip the source name if it leaked in.
        out = out.strip().splitlines()[0] if out.strip() else ''
        import re as _re
        for word in style_source.split():
            if len(word) > 3:
                out = _re.sub(r'\b' + _re.escape(word) + r'\b', '', out, flags=_re.IGNORECASE)
        out = _re.sub(r'\s{2,}', ' ', out).strip(' ,')
        return out
    except Exception as e:
        print(f"  [style] build_source_style_prompt failed for '{style_source}': {e}")
        return ''


# ---------------------------------------------------------------------------
# AI-generated flavor text
# ---------------------------------------------------------------------------
def generate_flavor_text(card: dict, inspiration_description: str = '',
                          openai_client=None, backend: str = 'openai',
                          local_model: str = 'llama3.2:3b') -> str:
    """Generate custom themed flavor text for an MTG card using an LLM.

    Uses the inspiration image's style description to drive the theme.
    Tone: light, witty, a little cheeky.

    Supports both OpenAI (cloud) and Ollama (local) backends.
    Returns empty string on failure.
    """
    name = card.get('name', 'Unknown')
    type_line = card.get('type_line', '')
    oracle = card.get('oracle_text', '')
    colors = card.get('color_identity', card.get('colors', []))

    theme_context = ''
    if inspiration_description:
        theme_context = (
            f"\n\nThe deck has a custom art theme. Use this theme to inspire the "
            f"tone and imagery of the flavor text:\n{inspiration_description}"
        )

    system_msg = (
        "You write flavor text for Magic: The Gathering cards. "
        "Flavor text is the italic text at the bottom of a card — a short quote, "
        "proverb, or narrative snippet that adds personality.\n\n"
        "Rules:\n"
        "- Keep it SHORT: 1 sentence, max 80 characters total. Brevity is key.\n"
        "- Tone: witty, light, a little cheeky — like a wry narrator\n"
        "- Match the card's color identity and creature type thematically\n"
        "- If a theme is provided, weave it into the flavor naturally\n"
        "- Do NOT repeat the card name verbatim\n"
        "- Do NOT reference game mechanics (mana, tapping, counters)\n"
        "- Do NOT use quotation marks around the text\n"
        "- Do NOT use markdown formatting (no *, _, **, __, etc.)\n"
        "- Output ONLY the plain flavor text, nothing else"
    )

    color_hints = {
        'W': 'noble, righteous',
        'U': 'clever, cerebral',
        'B': 'dark, ambitious',
        'R': 'passionate, chaotic',
        'G': 'primal, natural',
    }
    color_tone = ', '.join(color_hints.get(c, '') for c in colors if c in color_hints)
    color_note = f"\nColor tone: {color_tone}" if color_tone else ''

    user_msg = (
        f"Card: {name}\nType: {type_line}\nRules: {oracle}"
        f"{color_note}{theme_context}"
    )

    try:
        import mlx_llm
        text = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            model=local_model,
            max_tokens=100,
            temperature=0.9,
        )

        # Clean up LLM artifacts
        # Strip markdown formatting (* _ ** __)
        text = re.sub(r'[_*]+', '', text)
        # Strip surrounding quotes
        if (text.startswith('"') and text.endswith('"')) or \
           (text.startswith('\u201c') and text.endswith('\u201d')):
            text = text[1:-1]
        text = text.strip()

        return text
    except Exception as e:
        print(f"  [flavor] AI failed for {name}: {e}")
        return ''


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 prompt_generator.py <card_database.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        cards = json.load(f)

    prompts = generate_prompts_for_deck(cards)
    for p in prompts[:5]:
        print(f"=== {p['name']} ===")
        # Show just the subject part
        idx = p['prompt'].find('Subject:')
        if idx >= 0:
            print(p['prompt'][idx:])
        print()
