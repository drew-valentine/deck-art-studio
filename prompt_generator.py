#!/usr/bin/env python3
"""
Art prompt generator for Deck Art Studio.

Generates descriptive art prompts for MTG cards based on their name,
type, oracle text, and creature types. Supports both rule-based and
AI-enhanced prompt generation.
"""

import os
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
    # everyday object nouns Magic names artifacts after (card-generic, not deck
    # tables): the writer's Object line and the inspector's object check key on
    # these. A "Fellwar Stone" with no entry rendered as a red loop.
    'stone': 'a rough fist-sized stone', 'cloak': 'a hooded cloak', 'boots': 'a pair of boots',
    'tome': 'a heavy bound book', 'book': 'a heavy bound book', 'scroll': 'a rolled parchment scroll',
    'map': 'an unrolled map', 'cauldron': 'an iron cauldron', 'cup': 'a drinking cup',
    'cup': 'a drinking cup', 'bottle': 'a stoppered bottle', 'vial': 'a small glass vial',
    'flask': 'a glass flask', 'urn': 'a tall urn', 'chest': 'a wooden chest', 'box': 'a small box',
    'cage': 'an iron cage', 'chain': 'a heavy chain', 'anchor': 'an iron anchor',
    'compass': 'a brass compass', 'hourglass': 'an hourglass', 'clock': 'a clock',
    'candle': 'a lit candle', 'torch': 'a burning torch', 'quill': 'a feather quill',
    'bow': 'a wooden bow', 'arrow': 'an arrow', 'gem': 'a cut gemstone', 'jewel': 'a cut jewel',
    'pendant': 'a pendant on a cord', 'necklace': 'a necklace', 'collar': 'a collar',
    'bracelet': 'a bracelet', 'belt': 'a belt', 'gloves': 'a pair of gloves', 'armor': 'a suit of armour',
    'armour': 'a suit of armour', 'plate': 'a plate of armour', 'saddle': 'a saddle',
    'wheel': 'a wheel', 'cannon': 'a cannon', 'engine': 'an engine of brass and iron',
    'machine': 'a machine of brass and iron', 'lamp': 'a lamp', 'drum': 'a drum', 'flute': 'a flute',
    'harp': 'a harp', 'lute': 'a lute', 'egg': 'an egg', 'feather': 'a feather', 'seed': 'a seed',
    'coffin': 'a coffin', 'throne': 'a throne', 'altar': 'a stone altar', 'anvil': 'an anvil',
    'forge': 'a forge', 'hammer': 'a hammer', 'sphere': 'a sphere', 'cube': 'a cube', 'disc': 'a disc',
    'lens': 'a glass lens', 'monocle': 'a monocle', 'spectacles': 'a pair of spectacles',
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


_DICT_WORDS = None


def _dictionary():
    """Lower-case system word list (macOS/Linux /usr/share/dict/words), loaded
    once; an empty set where none exists (CI) — splitting then simply does
    nothing."""
    global _DICT_WORDS
    if _DICT_WORDS is None:
        try:
            with open('/usr/share/dict/words', encoding='utf-8', errors='ignore') as f:
                _DICT_WORDS = {w.strip().lower() for w in f if len(w.strip()) >= 3}
        except OSError:
            _DICT_WORDS = set()
    return _DICT_WORDS


def _split_compound(word: str, words=None) -> list:
    """'dragonstorm' -> ['dragon', 'storm'] when the word itself is not in the
    dictionary but splits into two dictionary words (each 4+ letters). Coined
    card-name compounds are the writer's hardest case ("Breaching Dragonstorm"
    became a roller coaster); the parts name the picture. Generic — no card
    knowledge, just the system word list."""
    d = _dictionary() if words is None else words
    w = word.lower()
    if not d or len(w) < 8 or w in d:
        return [word]
    for i in range(4, len(w) - 3):
        a, b = w[:i], w[i:]
        if a in d and b in d:
            return [a, b]
    return [word]


def literal_name_words(name: str, words=None) -> list:
    """The name's words with coined compounds split ('Breaching Dragonstorm'
    -> ['breaching', 'dragon', 'storm']); articles and possessives dropped."""
    out = []
    for tok in re.findall(r"[A-Za-z]+", name or ''):
        if tok.lower() in ('the', 'and', 'of', 'a', 'an', 's'):
            continue
        out.extend(w.lower() for w in _split_compound(tok, words))
    return out


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
    # With no flavor text the name is the strongest imagery cue: a card called
    # "Breaching Dragonstorm" is a storm of dragons breaking through, not "a
    # familiar over a library". Say so, or the writer invents a subject.
    # no illustrative example here: with nothing concrete in the card, the
    # writer parrots the example ("a whirlwind of wolves" for Chance Encounter)
    parts = literal_name_words(name)
    words_hint = (f" (its words: {', '.join(parts)})" if parts else '')
    name_line = (f" Its name is the scene: read '{name}' LITERALLY{words_hint} — every "
                 f"concrete noun in it is depicted and a verb in it is the action shown; "
                 f"if the name is abstract, depict the moment it describes happening to "
                 f"real people or creatures"
                 + ("; the story sets the mood, not the subject." if story else "."))
    return (
        f"A concrete illustrated scene representing the enchantment {name} — "
        f"depict the people, creatures, place, or event it embodies (not abstract "
        f"energy), set in an atmosphere of {atmosphere}.{story_line}{name_line}{coin_desc}"
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
# The opening-rule example must be built from THIS card. A fixed example
# ("Okaun, Eye of Chaos, a Cyclops Berserker, storms...") taught the 3B model
# to parrot the example's NAME: 'Okaun, Human Soldier' for Palace Jailer,
# 'Okaun, the labyrinth' for Maze of Ith — twelve prompts across seven decks.
# With the card's own name in the example, parroting it is exactly right.
_EXAMPLE_LEAK_NAMES = ('Okaun, Eye of Chaos', 'Okaun')


def _opening_example(card: dict) -> str:
    name = card.get('name', 'The subject').split(' // ')[0]
    type_line = card.get('type_line', '') or ''
    subtypes = ''
    if '—' in type_line or '\u2014' in type_line:
        subtypes = re.split(r'[—\u2014]', type_line, 1)[1].strip()
    if card.get('card_type') == 'creature' and subtypes:
        return f"{name}, {_article(subtypes)} {subtypes}, ..."
    return f"{name}, ..."


def _article(word: str) -> str:
    return 'an' if word[:1].lower() in 'aeiou' else 'a'


_PREAMBLE_RE = re.compile(
    r"^\s*(?:(?:sure|certainly|of course|okay|ok)[,!.]?\s*)?"
    r"(?:here(?:'s| is| are)|below is|this is|i(?:'ve| have) (?:rewritten|written|created))"
    r"[^\n:]{0,120}:\s*", re.IGNORECASE)


def hint_without_palette(block: str) -> str:
    """The style block minus its 'palette of ...' clause. Hue names are for
    the image model; handed to the scene writer they become scene content
    ('dusty coral background', 'coral-colored stone')."""
    import re as _re
    if not block:
        return ''
    parts = [p.strip() for p in block.split(',')]
    out, skipping = [], False
    for p in parts:
        if p.lower().startswith('palette of'):
            skipping = True          # the clause runs across several commas
            continue
        if skipping and p and p == p.lower() and len(p.split()) <= 3 and not _re.search(r'\b(lines?|ink|shading|outlines?|textures?|brush|pen|strokes?|detail|anatomy|eyes|faces|forms|hair)\b', p):
            continue                 # still inside the hue list
        skipping = False
        out.append(p)
    return ', '.join(x for x in out if x)


_UNPAINTABLE_RE = re.compile(
    r'(?:,\s*(?:its|his|her|their) [^,.;]*?)?(?:,\s*)?\b(?:an? (?:[a-z-]+ ){0,2}?(?:testament|reminder|symbol|beacon|echo|metaphor|nod|homage|tribute) (?:to|of|for)[^,.;]*'
    r'|as if [^,.;]*|seem(?:s|ing)? to [^,.;]*|symboli[sz]ing [^,.;]*'
    r'|an? [a-z]+ that (?:belies|speaks|hints|suggests|betrays|hides|recalls|promises)[^,.;]*'
    r'|(?:hinting|speaking|whispering) (?:at|of) [^,.;]*)', re.IGNORECASE)


_DANGLING_RE = re.compile(r'\s*\b(that|which|and|as|while|with|of|the|a|an|but|or|to|whose|where|for|in|on|at|by|'
                          r'is|are|was|were|has|have|had|its|his|her|their)\s*([.!?])\s*$', re.IGNORECASE)


def _fix_dangling_tail(text: str) -> str:
    """A draft cut off mid-clause ends "...a warm, golden light that." Drop the
    dangling function word(s) so the sentence closes on its last content word."""
    if not text:
        return text
    out = text
    for _ in range(3):
        m = _DANGLING_RE.search(out)
        if not m:
            break
        out = out[:m.start()].rstrip(' ,;') + m.group(2)
    return out


_SINGLE_EYE_RE = re.compile(r'\b(?:a |her |his |its )?(?:single|one|lone|solitary|sole)(?:,? [a-z-]+){0,3}? eye\b(?!s)', re.IGNORECASE)
_SINGLE_STARE_RE = re.compile(r'\b(?:single|one|lone|solitary|sole),?\s+(?=(?:[a-z-]+,?\s+){0,3}(?:stare|gaze|orb)\b)', re.IGNORECASE)


def _fix_invented_cyclops(text: str, anchor: str) -> str:
    """The anatomy-preservation rule ("a cyclops has ONE eye") gets over-applied:
    a faerie gained "a single, piercing emerald eye". Unless the reference
    anchor itself speaks of one eye, restore the plural."""
    if not text:
        return text
    a = (anchor or '').lower()
    if any(w in a for w in ('one eye', 'single eye', 'cyclops', 'one-eyed', 'lone eye')):
        return text
    out = _SINGLE_EYE_RE.sub(lambda m: re.sub(r'\b(single|one|lone|solitary|sole),?\s*', '', m.group(0), flags=re.IGNORECASE) + 's', text)
    return _SINGLE_STARE_RE.sub('', out)


_LIGHT_WORD_RE = re.compile(
    r'\b(?:glow(?:s|ing)?|beams? of (?:light|sun)\w*|light ?beams?|sunbeams?|shafts? of|rays? of|shimmer(?:s|ing)?|illuminat\w*|'
    r'gleam(?:s|ing)?|sheen|(?:warm|soft|golden|pale|dim|harsh|hard|rim|back)[- ]lit|'
    r'(?:warm|soft|golden|pale|dim|harsh|hard|rim|back|side|low)[- ]light\w*|lit by|lighting|'
    r'shadows?|halo|luminous|radiant|radiating|bathed in|sun ?sets?|sunset|sunrise|dawn|dusk|'
    r'burnished|glint(?:s|ing)?|cast(?:s|ing)? (?:a |an |the |long |deep |soft )?(?:glow|light|shadow|tone|beam)\w*|'
    r'silhouetted against|backlit|candlelit|moonlit|sunlit|lamplight|candlelight|firelight|torchlight|'
    r'sunlight|sunshine|sparkl\w*|shin(?:e|es|ing|y)|glossy|polished|lustrous|metallic sheen|highlights?|'
    r'glisten\w*|(?:fading|failing|dying|first|last|morning|evening|late) light|in the light of|'
    r'(?:dimly|brightly|softly|warmly|harshly|faintly)[- ]lit|dim light|'
    r'brightly lit|(?:afternoon|morning|evening|midday) sun)\b', re.IGNORECASE)


def _strip_light_words(text: str) -> str:
    """Last resort for flat media: drop whole sentences that still carry light
    words (the rewrite request below handles the normal case). Never leaves
    fragments behind."""
    if not text:
        return text
    sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+', text.strip()) if x.strip()]
    kept = [x for x in sentences if not _LIGHT_WORD_RE.search(x)]
    total = sum(len(x.split()) for x in sentences)
    kept_words = sum(len(x.split()) for x in kept)
    # Dropping sentences is only safe when it keeps the SUBJECT sentence and
    # most of the scene; otherwise ("74 -> 10 words", a prompt reduced to
    # sawdust drifting behind some vials) cut the light phrases out of every
    # sentence instead.
    if kept and sentences and kept[0] == sentences[0] and kept_words >= 0.6 * total:
        return ' '.join(kept)
    return ' '.join(_cut_light_phrases(x) for x in sentences if _cut_light_phrases(x))


def _cut_light_phrases(sentence: str) -> str:
    """Remove the light from one sentence: a comma clause that carries a light
    word is dropped whole (", its gemstone polished to a warm sheen"), except
    the first clause, which keeps the subject and loses only the words."""
    end = sentence.strip()[-1] if sentence.strip() and sentence.strip()[-1] in '.!?' else '.'
    clauses = re.split(r'(,\s*)', sentence.strip().rstrip('.!?'))
    parts = []
    tail = re.compile(r'^(?:its|his|her|their|with|as|while|where|casting|bathed|lit|glowing|'
                      r'illuminated|shimmering|gleaming)\b', re.IGNORECASE)
    for i, c in enumerate(clauses):
        if i > 0 and _LIGHT_WORD_RE.search(c) and (tail.match(c.strip()) or _LIGHT_WORD_RE.match(c.strip())):
            parts.append('')                    # a descriptive tail: drop it and its comma
        else:
            parts.append(c)
    out = ''.join(parts)
    out = re.sub(r'(,\s*)+(?=,|$)', '', out).strip()
    out = _LIGHT_WORD_RE.sub('', out) + end
    out = re.sub(r'\b(?:in|under|by|with|of|from|against|into)\s+(?:the |a |an |its |his |her )?(?=[,.;]|$)', '', out)
    out = re.sub(r'\s*,\s*,', ',', out)
    out = re.sub(r',\s*(?=[.!?])', '', out)
    out = re.sub(r'\s+([.!?,;])', r'\1', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    return out if len(out.split()) >= 2 else ''


_LETTERING_RE = re.compile(
    r"(?:,\s*)?\b(?:with|bearing|showing|displaying|marked with|engraved with|stamped with|etched with)\s+"
    r"(?:a |an |the )?[^.;]{0,60}?(?:\b(?:letters?|initials?|monogram|inscription|lettering|numerals?|"
    r"words?|glyphs? of text)\b|the (?:letter|word) '?[A-Za-z]'?|['\u2018\u2019][A-Za-z]['\u2018\u2019])"
    r"(?:\s+(?!(?:rests?|sits?|lies?|stands?|hangs?|floats?|rises?|glows?)\b)"
    r"[A-Za-z'\u2018\u2019-]+){0,8}", re.IGNORECASE)


# The writer copies instruction phrases into scenes ("The copper ring fills
# the frame, centered and large, with nothing cropped."). Clauses made of
# those phrases are dropped; the scene keeps its actual content.
_INSTRUCTION_ECHO_RE = re.compile(
    r"(?:,\s*)?\b(?:(?:with )?nothing cropped|cent(?:er|re)?e?d and large|fill(?:s|ing)? (?:most of )?the frame|"
    r"(?:the whole )?face clearly visible|never a close-up[^,.;]*|(?:a )?wide establishing view[^,.;]*|"
    r"(?:with )?a clear focal landmark|(?:as )?the (?:obvious |single )?focal (?:point|subject)|"
    r"(?:remains|stays|is) the focal point|cent(?:re|er) stage(?: as the focal subject)?)\b", re.IGNORECASE)


_META_LINE_RE = re.compile(
    r'\s*(?:^|\n|(?<=[.!?]))\s*(?:[-*•]\s*)?(?:The |This )?(?:focal (?:subject|point)|main subject|'
    r'subject of the (?:scene|image)|note|scene description|composition)\b\s*(?:is|are|should|must|remains|:)[^.!?\n]*[.!?]?',
    re.IGNORECASE)


def _tidy_prompt(text: str) -> str:
    """Strip stray quotes, doubled punctuation and the writer's own notes
    ("- The focal subject is Krark, the Goblin Wizard" tailed a Krark prompt;
    such notes read as instructions the image model then illustrates)."""
    if not text:
        return text
    out = _META_LINE_RE.sub(' ', text)
    out = re.sub(r'[*#_]+', '', out)                 # markdown bold / headings
    out = re.sub(r'(?:(?<=[.!?])\s*|^)(?:The |A |Bold |Cinematic |Strong )?(?:Scene|Title|Prompt|Description|Caption|Moment|Colou?r contrast|'
                 r'Contrast|Camera|Framing|Light(?:ing)?|Mood|Atmosphere|World|Body|Setting|Subject|Detail|Note|'
                 r'Composition|Palette|Style)\s*:\s+(?=[A-Za-z])', ' ', out, flags=re.IGNORECASE)  # my own labels
    out = _INSTRUCTION_ECHO_RE.sub('', out)          # parroted framing instructions
    # a stripped clause can strand its conjunction: ", while the ring." -> "."
    out = re.sub(r',?\s*\b(?:while|as|and|but|where|with)\s+(?:the |a |an |its |his |her )?[A-Za-z-]+\s*(?=[.!?]|$)', '', out)
    # a trailing fragment after a comma ("Caught in a moment of repose, the ring.")
    out = re.sub(r',\s*(?:the|a|an|its|his|her)\s+[A-Za-z-]+\s*(?=[.!?]\s*$)', '', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    out = _LETTERING_RE.sub('', out)                 # "a small silver 'A' on its face"
    out = re.sub(r'["\u201c\u201d]+', '', out)
    out = re.sub(r'\.{2,}', '.', out)
    out = re.sub(r'\s+([.!?,;])', r'\1', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    return out


def _strip_unpaintable(text: str) -> str:
    """Drop trailing abstractions the image model cannot draw ("..., a grim
    reminder of the flip to be ignored", "..., as if the very thought...").
    They spend tokens and occasionally summon literal props for metaphors."""
    if not text:
        return text
    out = _UNPAINTABLE_RE.sub('', text)
    out = re.sub(r'\s*,\s*([.!?])', r'\1', out)      # "settles, ." -> "settles."
    out = re.sub(r'\s+([.!?,;])', r'\1', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    return out


def _limit_scene_sentences(text: str, max_sentences: int = 3, max_words: int = 64) -> str:
    """Composition backstop: keep the first ``max_sentences`` sentences. The
    scene writer is asked for two; a third almost always introduces a second
    focal element (a shark beside the dragon, a gravestone beside the dock)."""
    import re as _re
    if not text:
        return text
    parts = _re.split(r'(?<=[.!?])\s+', text.strip())
    # drop a trailing fragment (max_tokens cut mid-sentence: "..., a small")
    if len(parts) > 1 and (not parts[-1].rstrip().endswith(('.', '!', '?'))
                           or len(parts[-1].split()) < 4):
        parts = parts[:-1]
    out = ' '.join(parts[:max_sentences]).strip()
    # word cap: the writer front-loads the focal subject, so trailing clauses
    # are where the second turtle / cat pile / soldier crowd arrives — cut at
    # the last clause boundary before the cap
    words = out.split()
    if len(words) > max_words:
        # prefer whole sentences: drop the second sentence rather than cutting
        # it mid-clause ("winding towards a distant."); only a lone over-long
        # sentence gets a clause-boundary cut
        sents = _re.split(r'(?<=[.!?])\s+', out)
        if len(sents) > 1 and len(sents[0].split()) >= 12:
            # keep as many whole sentences as fit under the cap
            kept, n = [], 0
            for sent in sents:
                w = len(sent.split())
                if n + w > max_words:
                    break
                kept.append(sent); n += w
            out = ' '.join(kept).strip() if kept else sents[0].strip()
        else:
            head = ' '.join(words[:max_words])
            cut = max(head.rfind(', '), head.rfind('; '), head.rfind('. '))
            head = head[:cut] if cut > len(head) // 2 else head
            out = head.rstrip(' ,;.') + '.'
    return out


_PERSON_WORD_RE = re.compile(
    r"\b(?:he|she|her|his|him|man|woman|men|women|king|queen|lord|lady|figures?|person|people|"
    r"soldiers?|warriors?|priests?|priestess|scribes?|hands?|onlookers?|crowd|leaders?|child|children|"
    r"servants?|guards?|travell?ers?|villagers?|monks?|scholars?|wizards?|mages?|sages?|elders?|"
    r"knights?|merchants?|farmers?|hunters?|sailors?|pilgrims?|worshippers?)\b", re.IGNORECASE)


def _person_problems(draft: str, card: dict) -> str:
    """Deterministic half of the scene check: an artifact or land scene must
    not contain a person (the language-model checker passed "King Celestia
    stands tall atop Command Tower, her imposing form" on a land). Words that
    are part of the card's own name are allowed. Generic — word list only."""
    if not draft or card.get('card_type') not in ('artifact', 'land'):
        return ''
    name_words = {w.lower() for w in re.findall(r"[A-Za-z]+", card.get('name') or '')}
    hits = sorted({m.group(0).lower() for m in _PERSON_WORD_RE.finditer(draft)} - name_words)
    return f"a person in an {card['card_type']} scene ({', '.join(hits)})" if hits else ''


_FLAT_WORDS = ('ink', 'line', 'drawn', 'pen', 'woodblock', 'etching', 'cel', 'animation',
               'cartoon', 'comic', 'papyrus', 'fresco', 'hieroglyph', 'pixel', 'flat')


def _names_a_colour(text: str) -> bool:
    """True when the scene names at least one colour word."""
    from vision_analyzer import _COLOR_WORDS
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", text or '')}
    return bool(words & set(_COLOR_WORDS))


def _is_coloured_style(style_hint: str) -> bool:
    """The block says the style is coloured (a palette clause or a coloured
    coverage clause), as opposed to monochrome ink."""
    h = (style_hint or '').lower()
    if 'monochrome' in h or 'black ink only' in h or 'black and white' in h:
        return False
    return 'palette of' in h or 'colour' in h or 'color' in h


def _is_flat_medium(style_hint: str) -> bool:
    """Flat media (no rendered light) from the block's medium anchor."""
    medium_word = (style_hint.split(' — ')[-1].split(',')[0].strip().lower() if style_hint else '')
    hint_low = (style_hint or '').lower()
    return bool(medium_word) and (any(w in medium_word for w in _FLAT_WORDS)
                                  or 'flat opaque' in hint_low or 'flat cel' in hint_low)


def _scene_problems(draft: str, card: dict, local_model: str) -> str:
    """Checklist judgement of a scene draft against its card, by the same
    language model at temperature 0: is the card's subject (with its creature
    type / object) the single focal thing, with no invented creature, person
    or prop competing, and no game-zone place? Returns '' when fine, else a
    short list of problems. Generic — the card's own name and type only."""
    if not draft:
        return ''
    name = card.get('name', '')
    ctype = card.get('card_type', '')
    type_line = card.get('type_line', '')
    try:
        import mlx_llm
        reply = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content':
                    "You check card-art scene descriptions. Answer OK if ALL hold, "
                    "otherwise list the failures in under 20 words. Rules: (1) the "
                    "first sentence's focal subject is the card's own subject; (2) for "
                    "a creature the creature itself is described (not only its "
                    "surroundings); for an artifact the named object is present and "
                    "central; for a land the location is the whole scene; (3) no "
                    "invented second creature, person or prop competes for focus — for "
                    "an artifact, enchantment or land ANY person, hand, passer-by or "
                    "onlooker counts as a failure; (4) no library, graveyard or "
                    "battlefield as a place."},
                {'role': 'user', 'content':
                    f"Card: {name}\nType: {type_line or ctype}\nDraft: {draft}\nAnswer:"},
            ],
            model=local_model, max_tokens=60, temperature=0.0)
    except Exception as e:
        print(f"  [prompt_gen] scene check failed: {e}")
        return ''
    text = (reply or '').strip()
    return '' if text.upper().startswith('OK') else text[:160]


def _subject_words(card: dict) -> set:
    """Words that identify the card's subject for the opening check: the
    name's own words (minus articles and possessives) plus, for artifacts,
    the literal object noun. Generic — no per-card knowledge."""
    import re as _re
    name = (card.get('name') or '').split(' // ')[0]
    words = {w.lower() for w in _re.findall(r"[A-Za-z]{3,}", name)} - {'the', 'and', 'from'}
    lit = _literal_object_from_name(name) if card.get('card_type') == 'artifact' else None
    if lit:
        words |= {w.lower() for w in _re.findall(r"[A-Za-z]{3,}", lit)} - {'the', 'and'}
    return words


def _opens_with_subject(text: str, card: dict) -> bool:
    """True when the FIRST sentence names the subject (any of its words). The
    opening rule is the writer's #1 rule and the one it breaks most: "Ink
    flows from a delicate quill held by a nearby scribe..." for Sol Ring."""
    import re as _re
    if not text:
        return False
    # the OPENING is the first few words, not the whole first sentence — "Ink
    # flows from a quill ... as the ring glows" mentions the ring but does not
    # open with it
    first = _re.split(r'(?<=[.!?])\s+', text.strip())[0]
    opening = ' '.join(first.split()[:8]).lower()
    return bool(_subject_words(card) & set(_re.findall(r"[a-z]{3,}", opening)))


def _strip_chat_preamble(text: str) -> str:
    """Small chat models sometimes answer like a chat turn — "Here is a
    rewritten description for Bountiful Landscape:" — and that line was
    landing in the art prompt verbatim. Drop a leading conversational lead-in
    (anything up to the first colon that reads like an announcement) and any
    markdown fences."""
    if not text:
        return text
    out = text.strip().strip('`').strip()
    # a bold or labelled heading ("**Scene: The Signet of Power**", "Title: ...")
    # is the writer naming its own picture; the image model letters it
    out = re.sub(r'^(?:\*\*[^*\n]{0,80}\*\*|#+ [^\n]{0,80}|(?:Scene|Title|Prompt|Description)\s*:\s*[^\n.]{0,60})\s*[\n:]\s*',
                 '', out, count=1)
    out = re.sub(r'^\*\*[^*\n]{0,80}\*\*\s*', '', out, count=1)
    out = _PREAMBLE_RE.sub('', out, count=1)
    return out.strip()


def _strip_example_leak(text: str, card: dict) -> str:
    """Backstop: if a leaked example name opens the scene and this card is not
    that card, substitute the card's own name."""
    name = card.get('name', '')
    if not text or any(n.split(',')[0] in name for n in _EXAMPLE_LEAK_NAMES):
        return text
    out = text
    for leak in _EXAMPLE_LEAK_NAMES:            # longest first
        if out.lstrip().startswith(leak):
            out = out.lstrip()
            rest = out[len(leak):]
            # drop a grafted "'s" possessive or an appositive that duplicates the type
            out = name.split(' // ')[0] + rest
            break
    return out


def generate_subject_with_ai(card: dict, openai_client=None, backend: str = 'openai',
                              local_model: str = 'llama3.1:8b',
                              style_hint: str = '', steer: str = '',
                              style_source_name: str = '', staging: str = '',
                              figure_idiom: str = '', style_source_kind: str = '') -> str:
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
        'artifact': 'Depict the artifact OBJECT itself, filling the frame. If the card NAME literally names a physical thing or body part (e.g. "Krark\'s Thumb" = a thumb, "Sol Ring" = a ring, "Sword of X" = a sword), depict THAT literal object as the relic — do NOT substitute a generic glowing disc, amulet, or runed orb. In the FIRST sentence say what physical object it is in plain everyday words the image model knows (a signet is "a signet ring", a phylactery is "a small ornate box", a bauble is "a glass trinket"), then describe it. If the object is a BODY PART, the image model will draw the whole limb unless told otherwise: say it is ONE single part, detached, cut cleanly at its base, with no hand, arm or body anywhere, and present it as a kept relic at rest. Any other object is shown large and whole, resting where it belongs or in use, with nothing hanging above or beside it. NOT a landscape, NOT a person.',
        'enchantment': 'The card name is an event, effect or blessing, never a character: do not write the name as a person who stands or acts. Depict the SCENE the enchantment represents — the people, creatures, place, ritual, or event drawn from its flavor and rules text (e.g. an army of warriors growing stronger under a hopeful dawn, a blessing settling over a battlefield). Do NOT default to abstract swirling energy, a glowing aura, or a magical vortex — give it concrete subject matter.',
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
        "cinematic three-sentence scene that an art director would frame and hang. "
        "THE #1 RULE: the card's own subject (from the reference description) MUST "
        "be the single, unmistakable, dominant focal point that fills the frame. "
        "Enhance the imagery; do NOT change WHAT is depicted and do NOT introduce a "
        "different focal subject. Setting and atmosphere are BACKGROUND only — they "
        "must never replace, crowd out, or upstage the card's subject. "
        "OPENING RULE (critical): the FIRST sentence must open with the subject "
        "itself — name it, and for creatures state its CREATURE TYPE as an "
        f"appositive right after the name (e.g. '{_opening_example(card)}'), "
        "then place it in the scene. NEVER open with the "
        "setting, weather, or atmosphere ('In the heart of the swirling mist...') "
        "— the image model paints whatever comes first, and setting-first "
        "openings produce subjectless art. "
        "GAME TERMS: in rules text, 'library', 'graveyard', 'hand', 'exile', "
        "'battlefield', 'stack' and 'deck' are game ZONES, not places — NEVER depict "
        "a library, a graveyard or a battlefield because the rules mention one. "
        "COMPOSITION RULE (critical): ONE focal subject, ONE setting, ONE action. The "
        "focal subject is the largest thing in the frame, in the foreground, clearly "
        "visible — never buried behind props or scenery. "
        "Do not add secondary creatures, characters, or props unless the card's own "
        "text names them — a 4-step image model cannot resolve competing focal points, "
        "so every extra element muddies the picture. "
        "SCENE GRAMMAR (what makes it art rather than a catalogue photo), in three "
        "sentences of about sixty words total: (1) the subject, opened as the OPENING "
        "RULE says, caught at a MOMENT — mid-action, or the instant before something "
        "happens; (2) a deliberate CAMERA and SCALE — a low angle so it fills the frame, "
        "an extreme close-up, or the subject tiny against something vast — plus, if the "
        "medium renders light at all, ONE strong LIGHT with a named quality (rim-lit from "
        "behind, a single shaft through dust, hard side light, glow from below); (3) ONE "
        "atmospheric detail that "
        "carries the setting (spray, embers, drifting dust, rain on the lens) and "
        "nothing else. PAINTABLE ONLY: every clause must be something a painter can "
        "put on the canvas — no 'a testament to', 'a reminder of', 'symbolizing', "
        "'as if', 'seems to', no feelings, no meanings; if it cannot be drawn, cut it. "
        "Be inventive and VARY it each time: a fresh setting, camera "
        "angle, distance, time of day, weather, and composition so re-rolls feel "
        "distinct — but always the same single focal subject. "
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
        elif staging and staging.strip():
            is_flat = _is_flat_medium(style_hint)
            # the register comes from the style itself (see style_staging_recall),
            # not from the calm-film-still default below
            system_msg += (
                f"\n\nCRITICAL — The art style is: {style_hint}. "
                "Describe specific, concrete visual details — composition, posture, "
                "objects" + (", lighting. Cinematic framing and light are REQUIRED" if not is_flat
                             else ". Bold framing is REQUIRED") + "; generic "
                "magic filler is BANNED: never 'maelstrom', 'volcanic fury', 'arcane "
                "energy', 'swirling vortex', 'mystical aura', 'otherworldly glow'."
                f"\n\nSTAGING AND REGISTER — stage the scene the way this artist would, and "
                f"write in their tone: {staging.strip()} Apply this to the setting, props, "
                "posture and mood ONLY — the card's subject stays exactly what it is. "
                "For a LAND the card's own location IS the setting (a landscape, not a "
                "prop in a room), but build it from THIS style's world: its plants, skies, "
                "rock, architecture and weather, so the terrain the card names is the "
                "version of it that exists in the style's world, never a generic one."
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

    # The image model renders "a dragon" as ITS default dragon unless the
    # prompt says how this artist draws one; the block's idiom words are
    # global, this puts them on the creature itself. It goes in the USER
    # message next to the task — at the tail of the long system message the
    # 8B writer dropped it.
    figure_line = ''
    # H32: cinematic light vocabulary pulls the render toward photographic
    # rendering ("shaft of sunlight, dust motes" made a papyrus deck's ring
    # smooth digital). Light is described the way THIS medium renders it.
    light_line = ''
    medium_word = (style_hint.split(' — ')[-1].split(',')[0].strip().lower() if style_hint else '')
    hint_low = (style_hint or '').lower()
    # FLAT media (ink, cel, comic, papyrus/fresco/hieroglyph, woodblock, pixel,
    # flat opaque paint) have no rendered light: glow, beams, shafts and soft
    # shadows pull the image model toward smooth digital painting. On these
    # media drama comes from pose, scale, colour contrast and pattern, and the
    # word "light" is banned from the scene entirely.
    is_flat = _is_flat_medium(style_hint)
    if medium_word and is_flat:
        light_line = (f"This medium ({medium_word}) is FLAT: no rendered light at all — do not write "
                      "glow, beam, shaft, ray, shimmer, soft light, warm light, shadow or lighting. "
                      "Make the drama with pose, scale, silhouette, colour contrast and pattern; "
                      "state each thing's colour directly as flat local colour.\n")
    elif medium_word:
        if any(w in medium_word for w in ('paint', 'watercolor', 'watercolour', 'oil')):
            how = 'painted light: opaque fills, soft brushed glow, no photographic realism'
        else:
            how = 'one strong light with a named quality'
        light_line = (f"Light in this medium ({medium_word}): describe light and shadow as {how}; "
                      "never lens, bokeh, volumetric, HDR or photographic terms.\n")

    if figure_idiom and figure_idiom.strip() and card_type in ('creature', 'planeswalker'):
        figure_line = (f"Figure idiom (REQUIRED in the first sentence): describe the creature's "
                       f"eyes, face and body in this artist's terms — {figure_idiom.strip()} — "
                       "keeping its identity and creature type exactly as given.\n")

    if steer and steer.strip():
        # The steer OVERRIDES the rules and the reference anchor wherever they
        # conflict — including the subject's APPEARANCE. Scoping it to
        # scene-level attributes ("setting, action, framing, mood") made the
        # model silently discard appearance steers: "a beautiful traitorous
        # zombie woman" produced yet another skeletal monster because the
        # anchor's imagery and the preserve-anatomy rule outranked the user.
        # Only the subject's IDENTITY is fixed.
        system_msg += (
            f"\n\nUSER DIRECTION (HIGHEST PRIORITY) — Re-imagine the scene to satisfy "
            f"this request: \"{steer.strip()}\". The user's direction OVERRIDES every "
            f"rule above and the reference description wherever they conflict — "
            f"including the subject's APPEARANCE, anatomy, mood, setting, action, and "
            f"framing. Only the subject's IDENTITY is fixed: the focal point must "
            f"still be this card's subject, re-imagined as the user directs. Words "
            f"from the reference that contradict the direction must not appear."
        )

    # FRANCHISE FIREWALL: flavor text is written in the deck style's voice and
    # can literally quote its characters ("All roads may lead to Rick's
    # garage..."). Anchoring the SCENE on such flavor smuggles the franchise's
    # cast into the art — combined with the style name at render time, actual
    # show characters appear in card art. Sentences naming style-source tokens
    # are stripped from the anchor before the scene writer ever sees them.
    # Only a FRANCHISE's own name yields cast tokens. Passing the style hint
    # here turned every word of the style block ("smoke", "bold", "deep",
    # "hand") into a forbidden token on unnamed and artist decks, and scene
    # sentences containing them were silently deleted.
    franchise_name = (style_source_name
                      if franchise_style_phrase(style_source_name, style_source_kind) else '')
    safe_flavor = _strip_franchise_sentences(flavor, franchise_name)

    # Rules text is game mechanics, not imagery: "exile cards from the top of
    # your library" made an enchantment a library twice. Only creatures and
    # planeswalkers get it (keywords like flying / menace are visual).
    rules_line = f"Rules: {oracle}\n" if card_type in ('creature', 'planeswalker') and oracle else ""
    user_msg = (
        f"Card: {name}\nType: {type_line}\n{rules_line}"
        + (f"Flavor text (use this as the THEMATIC ANCHOR for the scene): {safe_flavor}\n" if safe_flavor else "")
        + f"Direction: {guidance}\n"
        + figure_line
        + _body_line(card)
        + _object_line(card, local_model)
        + _camera_line(card_type)
        + (f"World (REQUIRED): this {card_type} exists in the style's own world — {staging.strip()} "
           "Build the setting from that world's plants, skies, rock and buildings, and put one of "
           "its signature features in the first sentence. Never a generic version of the terrain.\n"
           if staging and staging.strip() and card_type in ('land', 'enchantment', 'instant', 'sorcery') else "")
        + light_line
        + ("One subject. Then camera and scale, one strong colour contrast, one moment, "
           "one atmospheric detail. Three sentences, about sixty words.\n" if is_flat else
           "One subject. Then camera and scale, one strong light, one moment, one "
           "atmospheric detail. Three sentences, about sixty words.\n")
        + (f"User steer (OVERRIDES the reference description wherever they "
           f"conflict): {steer.strip()}\n" if steer and steer.strip() else "")
        + f"Reference description: {base_desc}\n"
        f"Ground the scene in this card's name and flavor — concrete subjects, not "
        f"abstract energy. Rewrite into a scene description (two short sentences):"
    )

    try:
        import mlx_llm
        out = mlx_llm.chat(
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            model=local_model,
            max_tokens=220,
            temperature=0.8,  # varied between re-rolls; 0.95 made the 3B model
                              # degenerate into word-salad tails ("waveform GS cave
                              # events super intend impact"), so keep it lower.
        )
        out = _strip_chat_preamble(out)
        print(f"  [prompt_gen] draft for {name}: {out[:200]}")
        if not _opens_with_subject(out, card):
            # one strict retry: the draft buried the subject; ask for the
            # same scene opening on it (generic check — the name's own words)
            retry = mlx_llm.chat(
                messages=[
                    {'role': 'system', 'content': system_msg},
                    {'role': 'user', 'content': user_msg},
                    {'role': 'assistant', 'content': out},
                    {'role': 'user', 'content':
                        f"Your draft does not open with the subject. Rewrite it so the "
                        f"FIRST words name '{name}' itself, as the dominant foreground "
                        "subject, then the setting. Three sentences: subject at a moment, "
                        "camera and light, one atmospheric detail."},
                ],
                model=local_model, max_tokens=140, temperature=0.6)
            retry = _strip_chat_preamble(retry)
            if _opens_with_subject(retry, card):
                out = retry
            print(f"  [prompt_gen] opening rule retry for {name}: {'kept' if out is retry else 'draft kept'}")
        out = _strip_franchise_sentences(out, franchise_name)   # output backstop
        out = _strip_example_leak(out, card)
        out = _strip_unpaintable(out)
        if is_flat and _LIGHT_WORD_RE.search(out):
            # ask for the same scene without rendered light, naming the words
            # that broke the rule (a second pass quotes the survivors); strip
            # sentences only if the rewrites still carry light words, since a
            # dropped sentence is what shrinks a scene to one line
            try:
                for _attempt in range(2):
                    bad = sorted({m.group(0).lower() for m in _LIGHT_WORD_RE.finditer(out)})
                    relit = mlx_llm.chat(
                        messages=[
                            {'role': 'system', 'content': system_msg},
                            {'role': 'user', 'content': user_msg},
                            {'role': 'assistant', 'content': out},
                            {'role': 'user', 'content':
                                "This medium is flat and has no rendered light. Rewrite the same scene "
                                "keeping every subject, pose, colour and setting, but remove every word "
                                "about light, glow, beams, shadows or shine. Same length, same number of "
                                f"sentences. These words must not appear: {', '.join(bad)}."},
                        ],
                        model=local_model, max_tokens=220, temperature=0.4)
                    relit = _limit_scene_sentences(_strip_chat_preamble(relit), 3)
                    if len(relit.split()) >= 5 and _opens_with_subject(relit, card):
                        out = relit
                        print(f"  [prompt_gen] flat-media rewrite for {name} (pass {_attempt + 1})")
                    if not _LIGHT_WORD_RE.search(out):
                        break
            except Exception as e:
                print(f"  [prompt_gen] flat-media rewrite failed: {e}")
            out = _strip_unpaintable(out)       # the rewrite reintroduces "a testament to"
            before = out
            out = _strip_light_words(out)
            if out != before:
                print(f"  [prompt_gen] flat-media strip for {name}: "
                      f"{len(before.split())} -> {len(out.split())} words")
        if card_type in ('creature', 'planeswalker') and _is_static_opening(out) \
                and os.environ.get('MOMENT_REWRITE', '1') != '0':
            # H61: "stands tall / rests serenely" openings are the writer's
            # default and read as plain; the grammar asks for a MOMENT. One
            # rewrite asks for a decisive action in the first sentence.
            try:
                act = mlx_llm.chat(
                    messages=[
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                        {'role': 'assistant', 'content': out},
                        {'role': 'user', 'content':
                            "The subject just stands there. Rewrite the same scene, same length, "
                            "same colours and setting, but catch the subject mid-action at a "
                            "decisive moment — a verb of motion, force or intent in the first "
                            "sentence (lunges, rears, hurls, tears, wheels, crouches to spring). "
                            "Keep the face visible. No light words."},
                    ],
                    model=local_model, max_tokens=220, temperature=0.6)
                act = _limit_scene_sentences(_strip_chat_preamble(act), 3)
                if len(act.split()) >= 12 and _opens_with_subject(act, card) and not _is_static_opening(act):
                    out = _strip_unpaintable(act)
                    if is_flat:
                        out = _strip_light_words(out)
                    print(f"  [prompt_gen] moment rewrite for {name}")
            except Exception as e:
                print(f"  [prompt_gen] moment rewrite failed: {e}")
        if is_flat and _is_coloured_style(style_hint) and not _names_a_colour(
                re.split(r'(?<=[.!?])\s+', out.strip())[0] if out.strip() else ''):
            # the SUBJECT sentence must carry a colour: a figure with only its
            # forest coloured renders as bare line art in a coloured forest
            # H46: on coloured flat media (coloured figures on white paper, cel,
            # comic) a scene with no colour word renders as bare line art —
            # three of four cards on a picture-book deck came out uncoloured.
            try:
                recol = mlx_llm.chat(
                    messages=[
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                        {'role': 'assistant', 'content': out},
                        {'role': 'user', 'content':
                            "Rewrite the same scene, same length, naming the flat colour of the "
                            "subject ITSELF in the first sentence (its skin, fur, clothing or "
                            "material) and of each main object in plain colour words. No light words."},
                    ],
                    model=local_model, max_tokens=220, temperature=0.4)
                recol = _limit_scene_sentences(_strip_chat_preamble(recol), 3)
                if len(recol.split()) >= 5 and _opens_with_subject(recol, card) and _names_a_colour(
                        re.split(r'(?<=[.!?])\s+', recol.strip())[0]):
                    out = _strip_unpaintable(recol)
                    if is_flat and _LIGHT_WORD_RE.search(out):
                        # ask once more, naming the light words, before any
                        # sentence is dropped — dropping shrank prompts to 14 words
                        bad = sorted({m.group(0).lower() for m in _LIGHT_WORD_RE.finditer(out)})
                        relit2 = mlx_llm.chat(
                            messages=[
                                {'role': 'system', 'content': system_msg},
                                {'role': 'user', 'content': user_msg},
                                {'role': 'assistant', 'content': out},
                                {'role': 'user', 'content':
                                    "Rewrite the same scene, same length and colours, with these words "
                                    f"removed and nothing about light or shine: {', '.join(bad)}."},
                            ],
                            model=local_model, max_tokens=220, temperature=0.4)
                        relit2 = _limit_scene_sentences(_strip_chat_preamble(relit2), 3)
                        if len(relit2.split()) >= 5 and _opens_with_subject(relit2, card):
                            out = _strip_unpaintable(relit2)
                        out = _strip_light_words(out)
                    print(f"  [prompt_gen] colour rewrite for {name}")
            except Exception as e:
                print(f"  [prompt_gen] colour rewrite failed: {e}")
        out = _fix_invented_cyclops(out, base_desc)
        out = _limit_scene_sentences(out, 3)
        out = _fix_dangling_tail(out)
        out = _tidy_prompt(out)
        # H21: writer variance is the dominant failure now (a chair inside a
        # ring, a bird for a faerie). A cheap checklist pass judges the draft
        # against the card; one lower-temperature re-roll if it fails.
        if os.environ.get('SCENE_CHECK', '1') != '0':
            problems = _person_problems(out, card) or _scene_problems(out, card, local_model)
            if problems:
                print(f"  [prompt_gen] scene check for {name}: {problems}")
                redo = mlx_llm.chat(
                    messages=[
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                        {'role': 'assistant', 'content': out},
                        {'role': 'user', 'content':
                            f"Problems with that draft: {problems} Rewrite it so the ONLY focal "
                            f"subject is {name} exactly as the card describes it, nothing invented "
                            "beside it. Three sentences: subject at a moment, camera and light, "
                            "one atmospheric detail."},
                    ],
                    model=local_model, max_tokens=140, temperature=0.5)
                redo = _limit_scene_sentences(_strip_chat_preamble(redo), 3)
                if len(redo.split()) >= 5 and _opens_with_subject(redo, card) \
                        and not _scene_problems(redo, card, local_model):
                    out = redo
        # final cleanup: every rewrite path above (flat, colour, scene-check
        # redo) can reintroduce what an earlier strip removed
        out = _strip_unpaintable(out)
        out = _strip_invented_names(out, card, safe_flavor)
        out = _fix_invented_cyclops(out, base_desc)
        if is_flat:
            out = _strip_light_words(out)
        out = _tidy_prompt(_fix_dangling_tail(out))
        if len(out.split()) < 5:
            # the backstops can strip a draft down to nothing (a franchise
            # sentence, a fragment); never persist an empty prompt
            print(f"  [prompt_gen] AI draft for {name} emptied by backstops, using rule-based")
            return generate_subject_description(card)
        return _ensure_creature_type_in_prompt(out, card)
    except Exception as e:
        print(f"  [prompt_gen] AI failed for {name}: {e}, using rule-based")
        return generate_subject_description(card)


# Franchise -> de-named genre phrase. A franchise NAME in any model-facing
# prompt summons its cast (a literal Rick in card art); the phrase carries the
# genre's look without the character identity. Keyed on DISTINCTIVE tokens only
# (never generic words), matched against the style-source name at USE time — a
# pure function, so every existing deck benefits with no data migration.
_FRANCHISE_PHRASES = {
    'morty': 'an adult animated sci-fi cartoon series',
    'simpsons': 'a classic adult animated sitcom',
    'futurama': 'a retro-futuristic animated sci-fi sitcom',
    'spongebob': 'a zany undersea cartoon series',
    'ghibli': 'a hand-painted Japanese anime film',
    'disney': 'a classic hand-drawn animated fairy-tale film',
    'pixar': 'a polished 3D animated family film',
    'pokemon': 'a colorful Japanese monster anime',
    'pokémon': 'a colorful Japanese monster anime',
    'batman': 'a noir animated superhero series',
    'marvel': 'a dynamic superhero comic book',
    'naruto': 'a high-energy shonen anime',
    'looney': 'a slapstick golden-age cartoon',
}


_GENERIC_FRANCHISE_PHRASE = 'an animated series or film with original characters'


def franchise_style_phrase(style_source: str, kind: str = ''):
    """De-named phrase for a character franchise, or None for artist /
    movement / unknown names (which are safe to use verbatim).

    ``kind`` is the recalled classification stored at distillation
    (vision_analyzer.style_source_kind): 'franchise' de-names any source, known
    or not; 'artist' / 'movement' pass verbatim even if a keyword matches. The
    keyword table is only the offline fallback when no kind is stored."""
    if not style_source:
        return None
    tokens = re.findall(r"[a-zé]+", style_source.lower().replace('&', ' '))
    table = next((_FRANCHISE_PHRASES[t] for t in tokens if t in _FRANCHISE_PHRASES), None)
    if kind == 'franchise':
        return table or _GENERIC_FRANCHISE_PHRASE
    if kind in ('artist', 'movement'):
        return None
    return table


def render_style_lead(style_source: str, lineage: str = '', kind: str = '') -> str:
    """The style lead for the image-model prompt. Franchise names are replaced
    with a de-named phrase plus an original-characters guard — the name
    itself is the strongest character summons there is. The recalled
    production LINEAGE (see vision_analyzer.style_lineage_recall) is the
    preferred phrase when known; the hand-written genre phrase is the
    fallback. Artist and movement names pass through verbatim (no cast to
    leak)."""
    if not style_source:
        return ''
    phrase = franchise_style_phrase(style_source, kind)
    if phrase:
        return f"in the style of {(lineage or '').strip() or phrase}, original character designs"
    return f"in the style of {style_source}"


_FRANCHISE_STOPWORDS = frozenset({
    'style', 'studio', 'film', 'series', 'show', 'animated', 'animation',
    'movie', 'comic', 'book', 'game',
})


def _franchise_tokens(style_hint: str) -> set:
    """Character-name tokens from the style hint's NAME segment ("Rick & Morty
    — 3D render, ..." -> {'rick', 'morty'}). Generic media words are excluded
    so 'Studio Ghibli' doesn't flag the word 'studio' in ordinary flavor."""
    if not style_hint:
        return set()
    name_part = re.split(r'\s+[—–-]{1,2}\s+', style_hint, 1)[0]
    return {w for w in re.findall(r"[a-z]+", name_part.lower())
            if len(w) > 3 and w not in _FRANCHISE_STOPWORDS}


def _strip_franchise_sentences(text: str, style_hint: str) -> str:
    """Drop sentences that name the style source's characters/tokens.

    Flavor text written in a franchise's voice can quote its cast ("...lead to
    Rick's garage"); anchoring scenes on it — or letting the scene writer echo
    it — puts the actual cast into card art once the style name is applied at
    render time. Deterministic: sentence out, no model in the loop."""
    if not text:
        return text
    tokens = _franchise_tokens(style_hint)
    if not tokens:
        return text
    kept = []
    for sent in re.split(r'(?<=[.!?])\s+', text.strip()):
        words = set(re.findall(r"[a-z]+", sent.lower()))
        if words & tokens:
            continue
        kept.append(sent)
    return ' '.join(kept).strip()


def _body_line(card: dict) -> str:
    """H45: the first creature subtype names WHAT the body is — Magic writes
    race/animal first, class second ("Bat God" is a bat, "Human Wizard" a
    human). The writer otherwise gives a Bat God a woman's face with wings.
    Deterministic, no creature tables."""
    if card.get('card_type') != 'creature':
        return ''
    type_line = card.get('type_line', '') or ''
    if '—' not in type_line:
        return ''
    subtypes = type_line.split('—', 1)[1].strip().split()
    if not subtypes:
        return ''
    kind = subtypes[0].lower()
    return (f"Body: this creature is a {kind} — give it a {kind}'s head, face, eyes and limbs "
            f"(not a human face with {kind} parts). Say so in the first sentence.\n")


_OBJECT_GLOSS = {}


def _object_gloss(literal: str, local_model: str) -> str:
    """H59: what the literal object LOOKS like, in plain words the image model
    knows ("a signet ring" -> "a finger ring with a flat engraved top"). The
    term alone is not enough: signets rendered as a goblet and a jewelled
    box. Model knowledge, memoised per phrase — no object tables."""
    key = (literal or '').strip().lower()
    if not key or os.environ.get('OBJECT_GLOSS', '1') == '0':
        return ''
    if key in _OBJECT_GLOSS:
        return _OBJECT_GLOSS[key]
    gloss = ''
    try:
        import mlx_llm
        reply = mlx_llm.chat(
            messages=[{'role': 'user', 'content':
                       f"In at most 12 plain words, say what {key} looks like — its shape, size "
                       "and where it is worn or used — for someone who has never heard the term. "
                       "No name, no colour, no sentence, just the description."}],
            model=local_model, max_tokens=30, temperature=0.0)
        gloss = _tidy_prompt(_strip_chat_preamble(reply or '')).strip().rstrip('.')
        gloss = re.split(r'[.\n]', gloss)[0].strip()          # first clause only
        if len(gloss.split()) > 24 or len(gloss.split()) < 3:
            print(f"  [prompt_gen] object gloss rejected for {key!r}: {reply!r}")
            gloss = ''
    except Exception as e:
        print(f"  [prompt_gen] object gloss failed: {e}")
    _OBJECT_GLOSS[key] = gloss
    print(f"  [prompt_gen] object gloss for {key!r}: {gloss!r}")
    return gloss


_OBJECT_SYNONYMS = {}


def _object_synonyms(literal: str, local_model: str) -> list:
    """One-word names an artist or a vision model might use for the object
    ("a talisman" -> medallion, amulet, pendant, charm). Memoised per phrase;
    model knowledge, no synonym tables. Used by the inspector's object check
    so a talisman drawn as a medallion is not a miss."""
    key = (literal or '').strip().lower()
    if not key or os.environ.get('OBJECT_GLOSS', '1') == '0':
        return []
    if key in _OBJECT_SYNONYMS:
        return _OBJECT_SYNONYMS[key]
    syns = []
    try:
        import mlx_llm
        reply = mlx_llm.chat(
            messages=[{'role': 'user', 'content':
                       f"List six single-word nouns that {key} could also be called or mistaken for "
                       "in a picture (for example medallion, amulet). Comma-separated, lowercase, "
                       "nothing else."}],
            model=local_model, max_tokens=40, temperature=0.0)
        syns = [w for w in re.findall(r'[a-z]{3,}', (reply or '').lower())
                if w not in ('and', 'the', 'for', 'also', 'could', 'called', 'mistaken', 'picture')][:8]
    except Exception as e:
        print(f"  [prompt_gen] object synonyms failed: {e}")
    _OBJECT_SYNONYMS[key] = syns
    print(f"  [prompt_gen] object synonyms for {key!r}: {syns}")
    return syns


def _object_line(card: dict, local_model: str) -> str:
    if card.get('card_type') != 'artifact':
        return ''
    lit = _literal_object_from_name((card.get('name') or '').split(' // ')[0])
    if not lit:
        return ''
    gloss = _object_gloss(lit, local_model)
    _object_synonyms(lit, local_model)          # warm the inspector's synonym memo
    return (f"Object (REQUIRED): {lit}" + (f" — {gloss}" if gloss else '') +
            ". The first sentence says what it is in these plain words, shown whole.\n")


_STATIC_VERB_RE = re.compile(
    r"\b(?:stands?|standing|rests?|resting|sits?|sitting|lies|lying|poses?|posing|towers?|looms?|"
    r"waits?|gazes?|stares?|is (?:seen|shown|depicted|pictured)|floats?|hovers?|perche[sd])\b", re.IGNORECASE)
_ACTION_VERB_RE = re.compile(
    r"\b(?:lunge|leap|charge|hurl|swing|strike|tear|rear|wheel|dive|spring|crouch|slash|roar|"
    r"snarl|grip|clutch|drag|haul|burst|shatter|smash|claw|bite|snap|pounce|sprint|dash|stride|"
    r"march|storm|surge|plunge|stab|thrust|fling|cast|conjure|summon|raise|lift|draw|unfurl|"
    r"spread|beat|flap|soar|swoop|crash|scream|howl|bellow|grasp|seize|shove|kick|vault)\w*\b",
    re.IGNORECASE)


def _is_static_opening(text: str) -> bool:
    """True when the FIRST sentence has a posture verb and no action verb."""
    if not text:
        return False
    first = re.split(r'(?<=[.!?])\s+', text.strip())[0]
    return bool(_STATIC_VERB_RE.search(first)) and not _ACTION_VERB_RE.search(first)


def _strip_invented_names(text: str, card: dict, flavor: str = '') -> str:
    """Drop the clause around an invented proper name ("as Benzir's voice
    echoes") — a capitalised word that is neither a dictionary word nor part
    of the card's name, type line or flavor text. The image model turns a
    stray name into a stray person."""
    if not text:
        return text
    words = _dictionary()
    if not words:
        return text
    known = set(w.lower() for w in re.findall(r"[A-Za-z]+", ' '.join([
        card.get('name') or '', card.get('type_line') or '', flavor or ''])))
    out_sents = []
    for sent in re.split(r'(?<=[.!?])\s+', text.strip()):
        clauses = re.split(r'(,\s*)', sent)
        kept = []
        for c in clauses:
            bad = False
            for m in re.finditer(r"\b([A-Z][a-z]{3,})(?:'s)?\b", c):
                w = m.group(1).lower()
                if w in known or w in words or w.rstrip('s') in words:
                    continue
                bad = True
                break
            if not bad:
                kept.append(c)
        sent2 = ''.join(kept)
        sent2 = re.sub(r'(?:,\s*)+$', '', sent2).strip()
        sent2 = re.sub(r'^(?:,\s*)+', '', sent2)
        if sent2 and not re.search(r'[.!?]$', sent2):
            sent2 += '.'
        if sent2 and len(sent2.split()) >= 2:
            out_sents.append(sent2)
    return ' '.join(out_sents)


def _camera_line(card_type: str) -> str:
    """H53: a deterministic framing requirement per Magic card type, in the
    user message (where the writer obeys). Loose "camera and scale" wording
    gave a goblin as a giant fist with no face and a dragon cropped to a
    wing. Card types are Magic's own, not deck tables."""
    if card_type in ('creature', 'planeswalker'):
        return ("Framing (REQUIRED): full figure or head-to-hip, the whole face clearly visible, "
                "the subject filling most of the frame — never a close-up of a hand, a weapon or a back.\n")
    if card_type == 'artifact':
        return "Framing (REQUIRED): the whole object, centred and large, nothing cropped.\n"
    if card_type == 'land':
        return "Framing (REQUIRED): a wide establishing view of the place with a clear focal landmark.\n"
    return ''


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
