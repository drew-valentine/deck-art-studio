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
def _is_card_back(card: dict) -> bool:
    name = (card.get('name') or '').strip().lower()
    return name.startswith('card back') or (card.get('type_line') or '').strip().lower() == 'card back'


def card_back_scene(card: dict) -> str:
    """The card back is a DESIGN, not a subject: the scene writer read 'Card
    Back' as a creature ('Card Back, a Back, strides across the dunes') and
    rendered two figures on a pattern. Deterministic, style-agnostic — the
    style's own motifs fill an ornamental frame."""
    return ("An ornamental card-back design: a symmetrical framed pattern built from this style's own "
            "motifs and colours, filling the frame edge to edge, a central emblem in the middle, "
            "flat and decorative like the back of a playing card. No figures, no characters, no "
            "creatures, no faces, no scene, no text.")


def generate_subject_description(card: dict) -> str:
    """Generate a vivid subject description from card data (rule-based).

    Uses the card's name, type, oracle text, and color identity to
    craft a descriptive scene for the art generator.
    """
    if _is_card_back(card):
        return card_back_scene(card)
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
    # coined compounds: "Shadowspear" -> shadow + spear -> a spear (it rendered
    # as a pair of daggers and a sword with no literal object to hold it)
    for w in reversed(words):
        parts = _split_compound(w)
        if len(parts) == 2 and parts[1] in _LITERAL_OBJECT_NOUNS:
            return _LITERAL_OBJECT_NOUNS[parts[1]]
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
    from vision_analyzer import _COLOR_WORDS
    modifiers = {'deep', 'dusty', 'muted', 'pale', 'bright', 'soft', 'rich', 'dark', 'light', 'vivid',
                 'warm', 'cool', 'faded', 'washed', 'bold', 'desaturated', 'acid', 'neon', 'earthy', 'burnt'}
    def _is_hue(part):
        toks = _re.findall(r'[a-z]+', part.lower())
        return bool(toks) and all(t in _COLOR_WORDS or t in modifiers for t in toks) and any(t in _COLOR_WORDS for t in toks)
    parts = [p.strip() for p in block.split(',')]
    out, skipping = [], False
    for p in parts:
        if p.lower().startswith('palette of'):
            skipping = True          # the clause runs across several commas
            continue
        if skipping and _is_hue(p):
            continue                 # still inside the hue list: only colour words
        skipping = False             # the first non-hue part ends the clause (idiom phrases follow it)
        out.append(p)
    return ', '.join(x for x in out if x)


_UNPAINTABLE_RE = re.compile(
    r'(?:,\s*(?:its|his|her|their) [^,.;]*?)?(?:,\s*)?\b(?:an? (?:[a-z-]+ ){0,2}?(?:testament|reminder|symbol|beacon|echo|metaphor|nod|homage|tribute) (?:to|of|for)[^,.;]*'
    r'|as if [^,.;]*|seem(?:s|ing)? to [^,.;]*|symboli[sz]ing [^,.;]*'
    r'|an? [a-z]+ that (?:belies|speaks|hints|suggests|betrays|hides|recalls|promises)[^,.;]*'
    r'|(?:hinting|speaking|whispering) (?:at|of) [^,.;]*'
    # H83b: similes to abstractions and idioms of waiting/meaning that the
    # writer keeps producing ("unfold like a tapestry", "secrets wait to be
    # unlocked", "a canvas of chance", "fills the air with anticipation")
    r'|(?:like|as) a (?:tapestry|canvas|dream|memory|whisper|promise|symphony|melody|prayer|sigh)\b[^,.;]*'
    r'|(?:secrets?|mysteries|stories|memories|echoes) (?:wait|waiting|linger|hide|whisper|await)[^,.;]*'
    r'|wait(?:s|ing)? to be [a-z]+[^,.;]*'
    r'|a canvas of [a-z]+'
    r'|(?:fill(?:s|ing)?|charg(?:es|ing)) the air with [^,.;]*'
    r'|creating a (?:[a-z-]+ ){0,2}(?:melody|atmosphere|mood|backdrop|symphony|harmony)[^,.;]*'
    r'|in a (?:[a-z-]+,? ){0,2}(?:dance|ballet|symphony) of [a-z]+[^,.;]*)', re.IGNORECASE)


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
    # a copula left hanging by a clause strip: "The table is, with five coins"
    out = re.sub(r'\s+\b(?:is|are|was|were|becomes?|remains?|seems?)\s*(?=[,;.!?])', '', out)
    return out


_SINGLE_EYE_RE = re.compile(r'\b(?:a |her |his |its )?(?:single|one|lone|solitary|sole)(?:,? [a-z-]+){0,3}? eye\b(?!s)', re.IGNORECASE)
_SINGLE_STARE_RE = re.compile(r'\b(?:single|one|lone|solitary|sole),?\s+(?=(?:[a-z-]+,?\s+){0,3}(?:stare|gaze|orb)\b)', re.IGNORECASE)


_SINGLE_LIMB_RE = re.compile(r"\b((?:a |his |her |its |their )?)(?:single|one|lone|solitary|sole) ((?:arm|leg|hand|foot|wing|horn|claw|ear)s?\b)", re.IGNORECASE)


def _fix_invented_cyclops(text: str, anchor: str) -> str:
    """The anatomy-preservation rule ("a cyclops has ONE eye") gets over-applied:
    a faerie gained "a single, piercing emerald eye". Unless the reference
    anchor itself speaks of one eye, restore the plural."""
    if not text:
        return text
    # the one-eye rule bleeds into limbs whatever the anchor says: "his
    # single arm raised" drew a one-armed cyclops with a hand merged into a leg
    text = _SINGLE_LIMB_RE.sub(lambda m: m.group(1) + m.group(2), text)
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
    r'burnished|glint(?:s|ing)?|cast(?:s|ing)? (?:a |an |the )?(?:[a-z]+,? ){0,3}?(?:glow|light|shadow|tone|beam|hue)s?\b|'
    r'silhouetted against|backlit|candlelit|moonlit|sunlit|lamplight|candlelight|firelight|torchlight|'
    r'sunlight|sunshine|sparkl\w*|shin(?:e|es|ing|y)|glossy|polished|lustrous|metallic sheen|highlights?|'
    r'glisten\w*|(?:fading|failing|dying|first|last|morning|evening|late) light|in the light of|'
    r'(?:dimly|brightly|softly|warmly|harshly|faintly)[- ]lit|dim light|'
    r'brightly lit|(?:afternoon|morning|evening|midday) sun)\b', re.IGNORECASE)


def _protect_light(text: str, protect) -> str:
    """Mask card-name words (Radiant, Dawn, Halo...) so the light strip cannot
    remove them; unmasked afterwards."""
    for w in protect or ():
        # letters on both sides so the light regex's word boundaries cannot land inside
        text = re.sub(r'\b' + re.escape(w) + r'\b', lambda m: 'zqk' + m.group(0) + 'kqz', text, flags=re.IGNORECASE)
    return text


def _name_words_for_protect(card) -> tuple:
    """Words of the card's name long enough to matter (Radiant, Dawn, Halo)."""
    return tuple(w for w in re.findall(r"[A-Za-z]{3,}", ((card or {}).get('name') or '').split(' // ')[0])
                 if w.lower() not in ('the', 'and', 'from', 'with'))


def _strip_light_words(text: str, protect=()) -> str:
    """Last resort for flat media: drop whole sentences that still carry light
    words (the rewrite request below handles the normal case). Never leaves
    fragments behind."""
    if not text:
        return text
    if protect:
        masked = _protect_light(text, protect)
        return _strip_light_words(masked).replace('zqk', '').replace('kqz', '')
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


_LIGHT_NP_RE = re.compile(
    r"\b(?:the|a|an|its|his|her|their)\s+(?:[a-z-]+\s+){0,2}?" + _LIGHT_WORD_RE.pattern +
    r"\s+of\s+(?:(?:the|a|an)\s+)?[a-z-]+(?:\s+" + _LIGHT_WORD_RE.pattern + r")?"
    r"(?:\s+(?:through|over|across|on|from)\s+(?:the\s+)?[a-z-]+(?:\s+[a-z-]+)?)?",
    re.IGNORECASE)


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
        if _LIGHT_WORD_RE.search(c) and (tail.match(c.strip()) or _LIGHT_WORD_RE.match(c.strip())):
            # a descriptive tail: drop it and its comma (a LEADING tail too —
            # "Its gemstone polished to a warm sheen, the ring rests" — the
            # subject is in the next clause)
            parts.append('')
        else:
            parts.append(c)
    out = ''.join(parts)
    out = re.sub(r'(,\s*)+(?=,|$)', '', out).strip()
    # a light word inside a noun phrase takes the phrase with it: "the soft
    # glow of afternoon sunlight through the beams" — cutting just the words
    # left "the soft of afternoon through the beams"
    out = _LIGHT_NP_RE.sub('', out)
    out = re.sub(r'\s+(?:and|or|as|while|amidst|amid)\s*(?=[,.;]|$)', '', out)
    out = _LIGHT_WORD_RE.sub('', out) + end
    out = re.sub(r'\b(?:in|under|by|with|of|from|against|into)\s+(?:the |a |an |its |his |her )?(?=[,.;]|$)', '', out)
    out = re.sub(r'\s*,\s*,', ',', out)
    out = re.sub(r'^(?:\s*,\s*)+', '', out)
    out = re.sub(r',\s*(?=[.!?])', '', out)
    out = re.sub(r'\s+([.!?,;])', r'\1', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    return out if len(out.split()) >= 2 else ''


_SCRIPT_CLAUSE_RE = re.compile(
    r"(?:,\s*)?\b(?:the |a |an |its |his |her )?[a-z' ]{0,30}?\b(?:fine |tiny |dense |flowing |elegant |cramped )?"
    r"(?:scripts?|handwriting|calligraphy|fine print|lettering|writing|inscriptions?)\b[^,.;]*", re.IGNORECASE)
_LETTERING_RE = re.compile(
    r"(?:,\s*)?\b(?:with|bearing|showing|displaying|marked with|engraved with|stamped with|etched with)\s+"
    r"(?:a |an |the )?[^.;]{0,60}?(?:\b(?:letters?|initials?|monogram|inscriptions?|lettering|numerals?|runes?|glyphs?|sigils?|symbols?|"
    r"words?|glyphs? of text|scripts?|writing|handwriting|calligraphy|fine print|text)\b|the (?:letter|word) '?[A-Za-z]'?|['\u2018\u2019][A-Za-z]['\u2018\u2019])"
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
    # quoted speech or slogans render as lettering ("as 'Wubba lubba dub dub, ...'")
    out = re.sub(r"(?:,\s*)?(?:\b(?:as|saying|reading|with the words?|inscribed|captioned)\s+)?"
                 r"(?:[\"\u201c\u2018][^\"\u201d\u2019]{6,}?[\"\u201d\u2019]|(?<!\w)'[^']{6,}?'(?!\w))", '', out)
    # camera directions are the writer talking to itself, not a scene
    out = re.sub(r'(?:,\s*)?\b(?:the )?camera (?:cuts|zooms|pans|pulls|tilts|dollies|sweeps)[^.!?;]*', '', out, flags=re.IGNORECASE)
    out = re.sub(r',\s*,+', ',', out)               # doubled commas
    out = re.sub(r'\s*\.(?:\s*\.)+', '.', out)      # ".." left by a removed sentence
    out = re.sub(r'(?:(?<=[.!?])\s*|^)(?:The |A |Bold |Cinematic |Strong )?(?:Scene|Title|Prompt|Description|Caption|Moment|Colou?r contrast|'
                 r'Contrast|Camera|Framing|Light(?:ing)?|Mood|Atmosphere|World|Body|Setting|Subject|Detail|Note|'
                 r'Composition|Palette|Style)\s*:\s+(?=[A-Za-z])', ' ', out, flags=re.IGNORECASE)  # my own labels
    out = _INSTRUCTION_ECHO_RE.sub('', out)          # parroted framing instructions
    # a stripped clause can strand its conjunction: ", while the ring." -> "."
    out = re.sub(r',?\s*\b(?:while|as|and|but|where|with)\s+(?:the |a |an |its |his |her )?[A-Za-z-]+\s*(?=[.!?]|$)', '', out)
    # a trailing fragment after a comma ("Caught in a moment of repose, the ring.")
    out = re.sub(r',\s*(?:the|a|an|its|his|her)\s+[A-Za-z-]+\s*(?=[.!?]\s*$)', '', out)
    out = re.sub(r'\s{2,}', ' ', out).strip()
    out = _SCRIPT_CLAUSE_RE.sub('', out)          # "the contract's fine script etched into" drew a written page
    out = re.sub(r'\.\s*\.', '.', out)              # a whole clause removed leaves '..'
    out = _LETTERING_RE.sub('', out)                 # "a small silver 'A' on its face"
    out = re.sub(r',\s*(?:its|his|her|their)\s+[a-z]+\s*,', ',', out)   # ", its surface," left behind
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


SCENE_MIN_WORDS = int(os.environ.get('SCENE_MIN_WORDS', '35') or 35)


def _is_thin_scene(text: str) -> bool:
    """H79: a scene under the word floor, or a single sentence, has lost its
    setting to the strips (measured: new prompts averaged 42-48 words against
    90 before, and the shortest rendered as a subject on a blank ground)."""
    if not text or not text.strip():
        return True
    import re as _re
    sents = [s for s in _re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    return len(text.split()) < SCENE_MIN_WORDS or len(sents) < 2


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
    if len(parts) > 1:
        # H79: stubs anywhere ("The altar's presence.") and a sentence that
        # restarts the previous one ("A stone altar, plain. A stone altar, its
        # …") came from rewrite passes and rendered as nothing
        kept = []
        for i, s in enumerate(parts):
            ws = s.split()
            if len(ws) < 4:
                continue
            nxt = parts[i + 1].split() if i + 1 < len(parts) else []
            if len(nxt) >= 3 and [w.lower().strip(',') for w in ws[:3]] == [w.lower().strip(',') for w in nxt[:3]] \
                    and len(nxt) >= len(ws):
                continue        # the next sentence is the fuller restart of this one
            kept.append(s)
        parts = kept or parts[:1]
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
    r"(?<![\w-])(?:he|she|her|his|him|man|woman|men|women|king|queen|lord|lady|figures?|person|people|"
    r"soldiers?|warriors?|priests?|priestess|scribes?|hands?|onlookers?|crowd|leaders?|child|children|"
    r"servants?|guards?|travell?ers?|villagers?|monks?|scholars?|wizards?|mages?|sages?|elders?|"
    r"knights?|merchants?|farmers?|hunters?|sailors?|pilgrims?|worshippers?|smiths?|blacksmiths?|artisans?)(?![\w-])", re.IGNORECASE)


def _person_problems(draft: str, card: dict) -> str:
    """Deterministic half of the scene check: an artifact or land scene must
    not contain a person (the language-model checker passed "King Celestia
    stands tall atop Command Tower, her imposing form" on a land). Words that
    are part of the card's own name are allowed. Generic — word list only."""
    if not draft or card.get('card_type') not in ('artifact', 'land'):
        return ''
    name_words = {w.lower() for w in re.findall(r"[A-Za-z]+", card.get('name') or '')}
    hits = sorted({m.group(0).lower() for m in _PERSON_WORD_RE.finditer(draft)} - name_words)
    article = 'an' if card['card_type'][:1] in 'aeiou' else 'a'
    return f"a person in {article} {card['card_type']} scene ({', '.join(hits)})" if hits else ''


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
        # the HEAD noun only ("altar" of "a stone altar"): counting "stone"
        # let "sits atop a pedestal of dark, weathered stone" pass as an
        # opening for Phyrexian Altar, and the render was a goblet
        heads = [w.lower() for w in _re.findall(r"[A-Za-z]{3,}", lit)]
        if heads:
            words.add(heads[-1])
    return words


def _scene_score(draft: str, card: dict, local_model: str = ''):
    """Deterministic 'how striking' score for a scene draft. The 8B scored
    every draft 9/10 (H87 run: 12/12 ties), so the judge is arithmetic on
    what the grammar asks for: action verbs and colour words up, static
    verbs, abstractions and a buried subject down. No model call."""
    if not draft:
        return -99
    action = len(_ACTION_VERB_RE.findall(draft))
    static = len(_STATIC_VERB_RE.findall(draft))
    colours = len(_re_colour_words(draft))
    abstractions = len(_UNPAINTABLE_RE.findall(draft))
    words = len(draft.split())
    score = 2 * action + colours + min(words, 60) / 20.0 - 2 * static - 3 * abstractions
    if not _opens_with_subject(draft, card):
        score -= 5
    # the name's head noun in the first sentence: a "Footfall Crater" draft
    # about a tree and an "Okaun, Eye of Chaos" draft about a furry beast
    # both opened with the name and then drifted
    name = (card.get('name') or '').split(' // ')[0]
    head = re.findall(r"[A-Za-z]{3,}", name.split(',')[0])
    first = re.split(r'(?<=[.!?])\s+', draft.strip())[0].lower().replace(name.lower(), '', 1)
    if head and head[-1].lower() in first and card.get('card_type') in ('land', 'artifact', 'enchantment'):
        score += 3
    return round(score, 2)


def _re_colour_words(text: str) -> list:
    try:
        from vision_analyzer import _COLOR_WORDS
    except Exception:
        return []
    return [w for w in re.findall(r"[a-z]+", (text or '').lower()) if w.rstrip('s') in _COLOR_WORDS]


def _names_the_thing(draft: str, card: dict) -> bool:
    """For a place or object card, the first sentence must carry the name's
    head noun (tower, crater) or the literal object (a talisman) beyond the
    name itself — a second draft that scored higher on colour and action had
    turned Command Tower into a tree and a talisman into a box."""
    if card.get('card_type') not in ('land', 'artifact', 'enchantment'):
        return True
    name = (card.get('name') or '').split(' // ')[0]
    first = re.split(r'(?<=[.!?])\s+', (draft or '').strip())[0].lower().replace(name.lower(), '', 1)
    heads = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", name.split(',')[0])]
    wanted = {heads[-1]} if heads else set()
    lit = _literal_object_from_name(name) if card.get('card_type') == 'artifact' else ''
    if lit:
        lw = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", lit)]
        if lw:
            wanted.add(lw[-1])
    if not wanted:
        return True
    return any(re.search(r"\b" + re.escape(w.rstrip('s')) + r"s?\b", first) for w in wanted)


def _pick_scene(a: str, b: str, card: dict, local_model: str = ''):
    """'b' when the second draft scores strictly higher AND still names the
    thing (places and objects), else 'a'."""
    sa, sb = _scene_score(a, card, local_model), _scene_score(b, card, local_model)
    print(f"  [prompt_gen] scene scores: first {sa}, second {sb}")
    if sb is not None and sa is not None and sb > sa and not _names_the_thing(b, card) and _names_the_thing(a, card):
        print("  [prompt_gen] second draft dropped: it no longer names the thing")
        return 'a'
    return 'b' if (sa is not None and sb is not None and sb > sa) else 'a'


def _ensure_subject_opening(text: str, card: dict) -> str:
    """H83: after every strip, the scene must still OPEN with the card's
    subject for every type — an artifact whose first clause was cut lost its
    name and rendered as a different object. Prepend the name (and, for an
    artifact, its literal object) when the opening no longer carries it."""
    if not text or _opens_with_subject(text, card):
        return text
    name = (card.get('name') or '').split(' // ')[0].strip()
    if not name:
        return text
    lit = _literal_object_from_name(name) if card.get('card_type') == 'artifact' else ''
    head = f"{name}, {lit} —" if lit else f"{name} —"
    body = text.strip()
    return f"{head} {body[0].lower() + body[1:] if body[:1].isupper() and not body.split()[0].istitle() else body}"


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
    if _is_card_back(card):
        return card_back_scene(card)          # a design, never a written scene
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
        'artifact': 'Depict the artifact OBJECT itself, filling the frame. If the card NAME literally names a physical thing or body part (e.g. "Krark\'s Thumb" = a thumb, "Sol Ring" = a ring, "Sword of X" = a sword), depict THAT literal object as the relic — do NOT substitute a generic glowing disc, amulet, or runed orb. In the FIRST sentence say what physical object it is in plain everyday words the image model knows — the ordinary name of that kind of thing, not a rare term — then describe it. If the name is a word for a place, a space or an idea rather than a thing, depict the one physical object that name most plainly suggests and say what it is. If the object is a BODY PART, the image model will draw the whole limb unless told otherwise: say it is ONE single part, detached, cut cleanly at its base, with no hand, arm or body anywhere, and present it as a kept relic at rest. Any other object is shown large and whole, resting where it belongs or in use, with nothing hanging above or beside it. NOT a landscape, NOT a person.',
        'enchantment': 'The card name is an event, effect or blessing, never a character: do not write the name as a person who stands or acts. Depict the SCENE the enchantment represents — the people, creatures, place, ritual, or event drawn from its flavor and rules text (e.g. an army of warriors growing stronger under a hopeful dawn, a blessing settling over a battlefield). Do NOT default to abstract swirling energy, a glowing aura, or a magical vortex — give it concrete subject matter.',
        'instant': 'Depict the dramatic moment of the spell being cast — the action and energy itself.',
        'sorcery': 'Depict the spell being cast — the ritual, the gathering of power.',
        'land': 'Depict the LOCATION — terrain, architecture, or natural formation. NO central character. If the NAME names an event or a force (a blast, a storm, a flood, an eruption, a fumarole, a rift), show that event HAPPENING NOW at its peak — never the calm before or the aftermath.',
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
    if os.environ.get('SCENE_MODE', 'filmstill') == 'filmstill':
        # H92: v1.49.0's composition recipe — a calm, artful film still built
        # from posture, composition, objects and light. The owner judged 1.49.0's
        # compositions more cohesive; measured 2026-09-08 the layout follows the
        # prompt (not the reference strength or the prompt order), and this
        # recipe put the subject back inside a place with depth on 9/12 cards.
        # Default since then; SCENE_MODE=moment restores the moment grammar.
        # Event-named cards keep the event at full force (H74).
        system_msg += (
            "\n\nCOMPOSITION OVERRIDE (replaces sentences 2 and 3 of the scene grammar): write "
            "the scene as a calm, artful film still. (1) The subject, opened as the OPENING RULE "
            + ("says, caught at a MOMENT — mid-action, a decisive instant that shows what it is, "
               "LARGE in the frame (at least a third of it, face visible). "
               if card_type in ('creature', 'planeswalker') else
               "says, shown whole and LARGE in the frame (at least a third of it), resting where it belongs. "
               if card_type == 'artifact' else
               "says, in a clear posture doing one plain thing. ")
            + ("(2) The composition — a deliberate camera (a low angle or a high vantage) with one "
               "vast or towering feature of the place dominating the frame, what stands behind it "
               "and beside it at what distance, the colours of each, and, if the medium renders "
               "light at all, how the scene is lit. "
               if card_type in ('land', 'enchantment', 'instant', 'sorcery') else
               "(2) The composition — where the subject sits in the frame, what stands behind it "
               "and beside it at what distance, the colours of each, and, if the medium renders "
               "light at all, how the scene is lit. ")
            + "(3) One concrete detail of the setting that ties subject and background together. "
            "Calm, specific, concrete visual details — no dramatic fantasy language, no energy, "
            "no vortex."
            + (" EXCEPTION: this card is named for an event — show that event happening at full "
               "force, filling the frame, and build the composition around it."
               if _event_in_name(name) else "")
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

    if figure_idiom and figure_idiom.strip() and card_type in ('creature', 'planeswalker') \
            and not (steer and steer.strip()):
        # a user direction owns the subject's appearance; the style's figure
        # idiom ("exaggerated features, chunky anatomy") would fight it
        fig_items = _figure_idiom_items(figure_idiom)
        figure_line = ((f"Figure idiom (REQUIRED in the first sentence): describe the creature's "
                        f"eyes, face and body in this artist's terms — {', '.join(fig_items)} — "
                        "keeping its identity and creature type exactly as given.\n") if fig_items else '')

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
    has_steer = bool(steer and steer.strip())
    user_msg = (
        (f"USER DIRECTION (HIGHEST PRIORITY — every line below yields to it, including Body, "
         f"Object, Framing and World; the subject's appearance follows it exactly): {steer.strip()}\n"
         "Render that direction as concrete visual detail in the FIRST sentence — face, skin, "
         "hair, build, bearing, dress — not as a bare adjective; if it says beautiful, describe a "
         "beautiful face and figure in plain words and drop anything grotesque.\n"
         if has_steer else "")
        + f"Card: {name}\nType: {type_line}\n{rules_line}"
        + (f"Flavor text (use this as the THEMATIC ANCHOR for the scene): {safe_flavor}\n" if safe_flavor else "")
        + f"Direction: {guidance}\n"
        + figure_line
        + _body_line(card, local_model, steer_present=has_steer)
        + _object_line(card, local_model)
        + _camera_line(card_type, name)
        + _setting_line(is_flat, has_steer, card_type)
        + (f"World: this {card_type} exists in the style's own world — {staging.strip()} "
           "Let that world colour the plants, sky, rock and light of the card's OWN subject; use at "
           "most one of its signature features, and only where the card's name allows it — a forest "
           "stays a forest, a swamp a swamp. Never add buildings, flags or props the card does not "
           "imply, and never repeat the same backdrop from card to card.\n"
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
        f"abstract energy. Rewrite into a scene description (three sentences, about sixty words):"
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
        if os.environ.get('SCENE_TAKES', '2') == '2':
            # H86b: two seeds of the SAME prompt render near-identical pictures
            # under Redux (12/12 measured), so variety has to come from the
            # scene text. A second, deliberately different draft and a blind
            # pick (asked twice, sides swapped) of the more striking scene.
            try:
                alt = mlx_llm.chat(
                    messages=[
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                        {'role': 'assistant', 'content': out},
                        {'role': 'user', 'content':
                            "Now write a DIFFERENT scene for the same card: another specific kind of "
                            "place, another decisive moment, another camera distance — same single "
                            "subject, same rules, three sentences of about sixty words."},
                    ],
                    model=local_model, max_tokens=220, temperature=0.9)
                alt = _strip_chat_preamble(alt)
                if _opens_with_subject(alt, card) and len(alt.split()) >= 20:
                    picked = _pick_scene(out, alt, card, local_model)
                    if picked == 'b' or (not _names_the_thing(out, card) and _names_the_thing(alt, card)):
                        out = alt          # a first draft that lost the thing yields to one that has it
                    print(f"  [prompt_gen] scene pick for {name}: {'second' if out is alt else 'first'} draft")
            except Exception as e:
                print(f"  [prompt_gen] second draft failed: {e}")
        if not _names_the_thing(out, card):
            # a place or object card whose opening lost its literal thing
            # ("Command Tower, a majestic squatting tree"): one retry that
            # opens on the thing itself, kept only if it does
            try:
                head = re.findall(r"[A-Za-z]{3,}", name.split(',')[0])
                thing = _literal_object_from_name(name) if card_type == 'artifact' else (head[-1].lower() if head else '')
                fixed = mlx_llm.chat(
                    messages=[
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                        {'role': 'assistant', 'content': out},
                        {'role': 'user', 'content':
                            f"That draft is not about the thing itself. Rewrite it so the FIRST sentence "
                            f"shows '{name}' as {thing if card_type == 'artifact' else 'a ' + thing} — the "
                            "literal thing the name says — large and unmistakable, then the same setting."},
                    ],
                    model=local_model, max_tokens=220, temperature=0.4)
                fixed = _strip_chat_preamble(fixed)
                if _opens_with_subject(fixed, card) and _names_the_thing(fixed, card):
                    out = fixed
                    print(f"  [prompt_gen] literal-thing retry for {name}: kept")
                else:
                    print(f"  [prompt_gen] literal-thing retry for {name}: draft kept")
            except Exception as e:
                print(f"  [prompt_gen] literal-thing retry failed: {e}")
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
                                "about light, glow, beams, shadows or shine. Three full sentences, about sixty "
                                f"words. These words must not appear: {', '.join(bad)}."},
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
            out = _strip_light_words(out, protect=_name_words_for_protect(card))
            if out != before:
                print(f"  [prompt_gen] flat-media strip for {name}: "
                      f"{len(before.split())} -> {len(out.split())} words")
        if _is_anticipation_or_past(out) and os.environ.get('MOMENT_REWRITE', '1') != '0':
            # H74: the picture is a single present instant; a threat, a
            # "soon", or a past-tense narration renders as the calm version
            try:
                now = mlx_llm.chat(
                    messages=[
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                        {'role': 'assistant', 'content': out},
                        {'role': 'user', 'content':
                            "A picture shows ONE instant. Rewrite the same scene in the PRESENT tense "
                            "with the event happening right now at full force — not threatening, "
                            "about to, or afterwards. Same subject, colours and setting; three full sentences, about sixty words."},
                    ],
                    model=local_model, max_tokens=220, temperature=0.5)
                now = _limit_scene_sentences(_strip_chat_preamble(now), 3)
                if len(now.split()) >= 12 and _opens_with_subject(now, card) and not _is_anticipation_or_past(now):
                    out = _strip_unpaintable(now)
                    if is_flat:
                        out = _strip_light_words(out, protect=_name_words_for_protect(card))
                    print(f"  [prompt_gen] present-moment rewrite for {name}")
            except Exception as e:
                print(f"  [prompt_gen] present-moment rewrite failed: {e}")
        if card_type in ('creature', 'planeswalker') and _is_static_opening(out) \
                and os.environ.get('MOMENT_REWRITE', '1') != '0' \
                and not (steer and steer.strip()):
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
                            "The subject just stands there. Rewrite the same scene in three full sentences of about sixty words, "
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
                        out = _strip_light_words(out, protect=_name_words_for_protect(card))
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
                            "Rewrite the same scene in three full sentences of about sixty words, naming the flat colour of the "
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
                                    "Rewrite the same scene in three full sentences of about sixty words, same colours, with these words "
                                    f"removed and nothing about light or shine: {', '.join(bad)}."},
                            ],
                            model=local_model, max_tokens=220, temperature=0.4)
                        relit2 = _limit_scene_sentences(_strip_chat_preamble(relit2), 3)
                        if len(relit2.split()) >= 5 and _opens_with_subject(relit2, card):
                            out = _strip_unpaintable(relit2)
                        out = _strip_light_words(out, protect=_name_words_for_protect(card))
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
        def _final_pass(txt: str) -> str:
            txt = _strip_unpaintable(txt)
            txt = _strip_wings(txt, card)
            txt = _strip_invented_names(txt, card, safe_flavor)
            txt = _fix_invented_cyclops(txt, base_desc)
            if is_flat:
                txt = _strip_light_words(txt, protect=_name_words_for_protect(card))
            txt = _tidy_prompt(_fix_dangling_tail(txt))
            return _ensure_steer_in_prompt(txt, steer, card)
        out = _final_pass(out)
        if _is_thin_scene(out) and os.environ.get('SCENE_FLOOR', '1') != '0':
            # H79: the strips only ever remove words; a scene that ends up as
            # one line renders as the subject on nothing. One growth pass asks
            # for the missing setting and atmosphere, then the same cleanup.
            try:
                for _temp in (0.5, 0.8):            # a second try at a higher temperature
                    grown = mlx_llm.chat(
                        messages=[
                            {'role': 'system', 'content': system_msg},
                            {'role': 'user', 'content': user_msg},
                            {'role': 'assistant', 'content': out},
                            {'role': 'user', 'content':
                                "That scene is too thin. Rewrite it in three full sentences of about "
                                "sixty words: keep the first sentence's subject and moment exactly, add "
                                "a second sentence that places it — the ground under it, what surrounds "
                                "it at scale, the weather or air — and a third with one atmospheric "
                                "detail. Nothing new in the foreground"
                                + (", and no words about light or shine." if is_flat else ".")},
                        ],
                        model=local_model, max_tokens=220, temperature=_temp)
                    grown = _limit_scene_sentences(_strip_chat_preamble(grown), 3)
                    raw_len = len(grown.split())
                    if _opens_with_subject(grown, card) and raw_len > len(out.split()):
                        grown = _final_pass(grown)
                        if len(grown.split()) > len(out.split()):
                            print(f"  [prompt_gen] thin-scene growth for {name}: "
                                  f"{len(out.split())} -> {len(grown.split())} words")
                            out = grown
                            break
                        print(f"  [prompt_gen] thin-scene growth for {name} rejected after cleanup "
                              f"({raw_len} -> {len(grown.split())} words)")
                    else:
                        print(f"  [prompt_gen] thin-scene growth for {name} rejected: "
                              f"{'no subject opening' if not _opens_with_subject(grown, card) else 'not longer'}")
            except Exception as e:
                print(f"  [prompt_gen] thin-scene growth failed: {e}")
        if len(out.split()) < 5:
            # the backstops can strip a draft down to nothing (a franchise
            # sentence, a fragment); never persist an empty prompt
            print(f"  [prompt_gen] AI draft for {name} emptied by backstops, using rule-based")
            return generate_subject_description(card)
        return _ensure_creature_type_in_prompt(_ensure_subject_opening(out, card), card)
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


def render_style_lead(style_source: str, lineage: str = '', kind: str = '', card_type: str = '') -> str:
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
        # "original character designs" is right for a creature; on an artifact,
        # land or saga it INVITES a cast (a lookalike boy appeared in a garage
        # for a saga, an elf and a dwarf around a goblin's thumb)
        if card_type in ('', 'creature', 'planeswalker'):
            guard = 'original character designs'
        elif card_type in ('artifact', 'land'):
            guard = 'no people, no characters'
        else:                                   # enchantment / instant / sorcery: people are scene content
            guard = 'original unnamed figures only'
        return f"in the style of {(lineage or '').strip() or phrase}, {guard}"
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


_BODY_GLOSS = {}


def _body_gloss(kind: str, local_model: str) -> str:
    """What a creature of this kind looks like, in plain words ("a faerie" ->
    "a small slender winged humanoid with pointed ears"). Memoised per kind;
    model knowledge, no creature tables. A faerie on a papyrus deck rendered
    bird-legged when the Body line only named the kind."""
    key = (kind or '').strip().lower()
    if not key or not local_model or os.environ.get('OBJECT_GLOSS', '1') == '0':
        return ''
    if key in _BODY_GLOSS:
        return _BODY_GLOSS[key]
    gloss = ''
    try:
        import mlx_llm
        reply = mlx_llm.chat(
            messages=[{'role': 'user', 'content':
                       f"In at most 12 plain words, describe the body of a {key} (a fantasy creature "
                       "type): its build, head, limbs and any wings or tail. No name, no colour, "
                       "no sentence, just the description."}],
            model=local_model, max_tokens=30, temperature=0.0)
        gloss = _tidy_prompt(_strip_chat_preamble(reply or '')).strip().rstrip('.')
        gloss = re.split(r'[.\n]', gloss)[0].strip()
        if not 3 <= len(gloss.split()) <= 24:
            gloss = ''
    except Exception as e:
        print(f"  [prompt_gen] body gloss failed: {e}")
    _BODY_GLOSS[key] = gloss
    print(f"  [prompt_gen] body gloss for {key!r}: {gloss!r}")
    return gloss


def _card_flies(card: dict) -> bool:
    """Flying (or flash-in-the-air keywords that imply wings) in the RULES text."""
    text = ((card.get('oracle_text') or '') + ' ' + (card.get('keywords') and ' '.join(card.get('keywords')) or '')).lower()
    return bool(re.search(r'\b(?:flying|flies)\b', text))


_WING_CLAUSE_RE = re.compile(
    r"(?:,\s*)?(?:\b(?:with|by|on|from|its|her|his|their)\s+)?[^,.;]*\b(?:wings?|winged|wingspan|flies|flying|"
    r"soars?|soaring|hovers?|hovering|airborne|aloft|in flight|takes? (?:to the|flight))\b[^,.;]*", re.IGNORECASE)


def _strip_wings(text: str, card: dict) -> str:
    """A creature whose rules do not say flying gets no wings and no flight."""
    if not text or card.get('card_type') not in ('creature', 'planeswalker') or _card_flies(card):
        return text
    if not _WING_CLAUSE_RE.search(text):
        return text
    sents = []
    for sent in re.split(r'(?<=[.!?])\s+', text.strip()):
        cleaned = _WING_CLAUSE_RE.sub('', sent)
        cleaned = re.sub(r',\s*,', ',', cleaned)
        cleaned = re.sub(r'\s+([.!?,;])', r'\1', cleaned)
        cleaned = re.sub(r'^\s*,\s*', '', cleaned).strip()
        if cleaned and len(cleaned.split()) >= 3:
            if not re.search(r'[.!?]$', cleaned):
                cleaned += '.'
            sents.append(cleaned)
    return ' '.join(sents) if sents else text


def _body_line(card: dict, local_model: str = '', steer_present: bool = False) -> str:
    """H45: the first creature subtype names WHAT the body is — Magic writes
    race/animal first, class second ("Bat God" is a bat, "Human Wizard" a
    human). The writer otherwise gives a Bat God a woman's face with wings.
    Deterministic, no creature tables."""
    if card.get('card_type') != 'creature' or steer_present:
        # with a user direction the user owns the appearance entirely; the
        # Body line's own anatomy ideas (wings for a corrupted elf) fought it
        return ''
    type_line = card.get('type_line', '') or ''
    if '—' not in type_line:
        return ''
    subtypes = type_line.split('—', 1)[1].strip().split()
    if not subtypes:
        return ''
    # the WHOLE subtype phrase, not its first word: "Phyrexian Zombie Elf" is
    # an elf corrupted by Phyrexia, and 'phyrexian' alone glossed as a winged
    # skull-headed monster that overrode the user's own direction
    kind = ' '.join(subtypes).lower()
    # with a user direction the user has described the appearance: no model
    # gloss (a gloss for a corrupted elf added horns and a tail over the steer)
    head = "Body (yields to the USER DIRECTION above): " if steer_present else "Body: "
    if kind.lower().startswith('human'):
        # the type word says it: a human is drawn as a human — the 8B gave a
        # Human Soldier claws and a Human Cleric a tail and pointed ears
        return (f"{head}this creature is a {kind} — a human being with a human face and body, two arms, "
                "two legs, no claws, fangs, tail, fur, scales or pointed ears"
                + (" and NO wings" if not _card_flies(card) else "") + ". Say so in the first sentence.\n")
    gloss = '' if steer_present else _body_gloss(kind, local_model)
    # wings follow the RULES: a fox with no flying grew "retractable yellow wings"
    wings = ("It has wings and is in the air." if _card_flies(card)
             else "It has NO wings and stays on the ground; never give it wings or let it fly.")
    return (f"{head}this creature is a {kind}" + (f" — {gloss}" if gloss else '') +
            f" — give it that creature's head, face, eyes and limbs. {wings} Say so in the first sentence.\n")


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


_ANTICIPATION_RE = re.compile(
    r"\b(?:threaten(?:s|ing|ed)? to|about to|on the (?:verge|brink|edge) of|poised to|ready to|"
    r"waiting to|prepar(?:es|ing) to|set to|soon to|will (?:soon )?\w+|is going to|the calm before|"
    r"(?:ominous|tense|quiet|breathless|eager|silent) anticipation|anticipation of)\b",
    re.IGNORECASE)
_PAST_OPENING_RE = re.compile(
    r"^(?:[^.!?]{0,80}?\b(?:stood|rose|loomed|towered|sat|lay|rested|hung|stretched|sprawled|glowed|"
    r"tumbled|trembled|shook|erupted|burst|roared|raged|blazed|churned|surged|fell|flew|swept|"
    r"cascaded|swirled|rushed|crashed|exploded|spread|crumbled)\b)", re.IGNORECASE)


def _is_anticipation_or_past(text: str) -> bool:
    """The scene describes what is ABOUT to happen, or narrates in the past
    ('stood tall… threatening to unleash another blast' drew a calm mesa)."""
    if not text:
        return False
    first = re.split(r'(?<=[.!?])\s+', text.strip())[0]
    return bool(_ANTICIPATION_RE.search(text)) or bool(_PAST_OPENING_RE.search(first))


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
    def _in_dict(w):
        if w in words:
            return True
        for suf, rep in (('ies', 'y'), ('ing', ''), ('ing', 'e'), ('ed', ''), ('ed', 'e'), ('es', ''), ('s', ''), ('er', ''), ('est', '')):
            if w.endswith(suf) and len(w) - len(suf) >= 3 and (w[:-len(suf)] + rep) in words:
                return True
        return False
    out_sents = []
    for si, sent in enumerate(re.split(r'(?<=[.!?])\s+', text.strip())):
        clauses = re.split(r'(,\s*)', sent)
        kept = []
        for ci, c in enumerate(clauses):
            bad = False
            # a clause carrying the card's own name is never invented
            if any(w in known for w in re.findall(r"[a-z]+", c.lower()) if len(w) > 3):
                kept.append(c)
                continue
            for m in re.finditer(r"\b([A-Z][a-z]{3,})(?:'s)?\b", c):
                if si == 0 and ci == 0 and m.start() == 0:
                    continue            # the opening word of the scene is capitalised by position
                w = m.group(1).lower()
                if w in known or _in_dict(w):
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


_EVENT_NOUN_RE = re.compile(
    r"\b(?:blast|explosion|eruption|storm|tempest|hurricane|tornado|cyclone|flood|deluge|tsunami|"
    r"quake|earthquake|avalanche|landslide|wildfire|inferno|blaze|rift|fumarole|geyser|maelstrom|"
    r"cataclysm|collapse|surge|wave|lightning|thunder)s?\b", re.IGNORECASE)


def _event_in_name(name: str) -> str:
    """The event or force a card NAME is built on ('Blast Zone' -> 'blast'), else ''."""
    m = _EVENT_NOUN_RE.search((name or '').split(' // ')[0])
    return m.group(0).lower() if m else ''


_FIGURE_WORD_RE = re.compile(
    r"\b(?:face|faces|facial|feature|features|anatomy|anatomies|pose|poses|posture|expression|expressions|"
    r"eye|eyes|limb|limbs|body|bodies|figure|figures|proportion|proportions|head|heads|hand|hands|"
    r"silhouette|silhouettes|gesture|gestures|build|torso|snout|jaw|brow|nose|mouth|creature|creatures|"
    r"character|characters|physique|stance|shoulders|neck|necks|legs|arms|wings|tail|tails|fur|skin|scales)\b",
    re.IGNORECASE)


def _figure_idiom_items(idiom: str) -> list:
    """H88: the Figure idiom line describes the creature's eyes, face and body,
    so only idiom items ABOUT figures belong on it. A rendering term there is
    read as anatomy — 'flat shaded forms' became a demon with a 'flat head',
    'delicate hatching' a hatched face. Word categories, no style tables."""
    items = [x.strip() for x in (idiom or '').split(',') if x.strip()]
    return [x for x in items if _FIGURE_WORD_RE.search(x)]


def _setting_line(is_flat: bool, steer_present: bool = False, card_type: str = '') -> str:
    """H79: the second sentence must PLACE the subject. Every strip in the
    backstop chain removes words and nothing put the setting back, so scenes
    shrank to a subject on nothing (an altar as a blank slab, a ring on a
    cushion). Category words only — an example noun would be parroted."""
    head = ("Setting (REQUIRED, yields to the USER DIRECTION above): "
            if steer_present else "Setting (REQUIRED): ")
    tail = (" — in flat colour and pattern, never light." if is_flat else ".")
    if card_type == 'artifact':
        # H89: left to itself the writer presents every object for display on a
        # soft stand; a thing belongs where it is found or used
        return (head + "the second sentence places the object where such a thing is found or "
                "used — a real place with the ground, what surrounds it at scale and the weather or "
                "air, in plain visual words; never presented for display on a stand or a soft "
                "surface" + tail + "\n")
    return (head + "the second sentence places the subject somewhere specific — the ground "
            "under it, what surrounds it at scale, and the weather or air around it, in plain "
            "visual words" + tail + "\n")


def _camera_line(card_type: str, name: str = '') -> str:
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
        ev = _event_in_name(name)
        if ev:
            # a land named for an event: the event, not the terrain, is the picture
            return (f"Framing (REQUIRED): the {ev} itself fills the frame at full force, seen close — "
                    "the terrain is what it happens to, not the subject.\n")
        return "Framing (REQUIRED): a wide establishing view of the place with a clear focal landmark.\n"
    return ''


_STEER_STOP = frozenset({'a', 'an', 'the', 'is', 'are', 'was', 'be', 'with', 'and', 'of', 'in', 'on',
                         'to', 'as', 'very', 'make', 'her', 'his', 'its', 'their', 'she', 'he', 'it',
                         'this', 'that', 'should', 'looks', 'look', 'like', 'more', 'less', 'not'})


def _steer_phrase(steer: str, card: dict) -> str:
    """The steer minus a leading '<Name> is' / 'make her' lead-in."""
    name = (card.get('name') or '').split(' // ')[0]
    t = (steer or '').strip().rstrip('.')
    first = name.split(',')[0].strip()
    t = re.sub(r'^(?:' + re.escape(name) + '|' + re.escape(first) + r')\s*(?:is|should be|looks like|as)\s+', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^(?:make|render|draw|show)\s+(?:her|him|it|them|' + re.escape(first) + r')\s+(?:as\s+)?', '', t, flags=re.IGNORECASE)
    return t.strip()


def _ensure_steer_in_prompt(text: str, steer: str, card: dict) -> str:
    """Deterministic guarantee that the user's direction survives every
    rewrite: if fewer than half of its content words appear in the prompt,
    the direction is injected as an appositive right after the card's name
    (or prepended). Rewrites had kept 'lunges forward' and dropped
    'beautiful' — the one word the user typed."""
    if not text or not steer or not steer.strip():
        return text
    phrase = _steer_phrase(steer, card)
    # words the card already carries ("zombie", "elf" from the type line) do
    # not count as the direction surviving; "beautiful" is the user's word
    own = set(re.findall(r'[a-z]+', ((card.get('name') or '') + ' ' + (card.get('type_line') or '')).lower()))
    words = [w for w in re.findall(r'[a-z]+', phrase.lower()) if w not in _STEER_STOP and len(w) > 2 and w not in own]
    if not words:
        return text
    low = text.lower()
    present = sum(1 for w in words if re.search(r'\b' + re.escape(w[:5]) + r'\w*', low))
    if present * 2 >= len(words):
        return text
    name = (card.get('name') or '').split(' // ')[0]
    if name and name in text:
        return text.replace(name, f"{name} — {phrase} —", 1)
    return f"{phrase[:1].upper()}{phrase[1:]}: {text}"


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
