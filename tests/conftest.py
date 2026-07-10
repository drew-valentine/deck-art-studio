"""Shared fixtures for Deck Art Studio test suite."""

import copy
import pytest

import deck_studio


# ---------------------------------------------------------------------------
# Global state snapshot/restore — prevents test pollution
# ---------------------------------------------------------------------------
_GLOBALS_TO_SAVE = [
    'cards_db', 'prompts_map', 'generation_status',
    'active_deck_id', 'active_deck_meta', 'active_model_key',
    'openai_client', 'ref_image_b64',
    'cards_revision', 'style_analysis_progress',
    'active_inspiration_path', 'active_inspiration_paths',
    # Path globals mutated by deck activation — restore so a test that switches
    # decks (or monkeypatches these) can't leak paths into later tests.
    'ART_PROMPTS_PATH', 'RAW_ART_DIR', 'COMPOSITE_DIR',
    'CARD_DB_PATH', 'VERSIONS_DIR',
]


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Snapshot and restore all mutable globals between tests."""
    saved = {}
    for name in _GLOBALS_TO_SAVE:
        val = getattr(deck_studio, name)
        if isinstance(val, (dict, list)):
            saved[name] = copy.deepcopy(val)
        else:
            saved[name] = val

    # Also snapshot the cancel set
    cancel_set = set(deck_studio._cancel_single)

    yield

    for name, val in saved.items():
        setattr(deck_studio, name, val)
    deck_studio._cancel_single.clear()
    deck_studio._cancel_single.update(cancel_set)


# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    """Flask test client with TESTING mode enabled."""
    deck_studio.app.config['TESTING'] = True
    with deck_studio.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Sample card data — covers all major card types
# ---------------------------------------------------------------------------
SAMPLE_CREATURE = {
    'name': 'Goblin Guide',
    'mana_cost': '{R}',
    'type_line': 'Creature \u2014 Goblin Scout',
    'oracle_text': 'Haste\nWhenever Goblin Guide attacks, defending player reveals the top card of their library.',
    'power': '2', 'toughness': '2',
    'colors': ['R'], 'color_identity': ['R'],
    'card_type': 'creature', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_BIG_CREATURE = {
    'name': 'Ghalta, Primal Hunger',
    'mana_cost': '{10}{G}{G}',
    'type_line': 'Legendary Creature \u2014 Elder Dinosaur',
    'oracle_text': "This spell costs {X} less to cast, where X is the total power of creatures you control.\nTrample",
    'power': '12', 'toughness': '12',
    'colors': ['G'], 'color_identity': ['G'],
    'card_type': 'creature', 'quantity': 1,
    'is_commander': True, 'flavor_text': '',
}

SAMPLE_ARTIFACT = {
    'name': 'Sol Ring',
    'mana_cost': '{1}',
    'type_line': 'Artifact',
    'oracle_text': '{T}: Add {C}{C}.',
    'power': None, 'toughness': None,
    'colors': [], 'color_identity': [],
    'card_type': 'artifact', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_PLANESWALKER = {
    'name': 'Jace, the Mind Sculptor',
    'mana_cost': '{2}{U}{U}',
    'type_line': 'Legendary Planeswalker \u2014 Jace',
    'oracle_text': '+2: Look at the top card of target player\'s library.\n0: Draw three cards, then put two cards from your hand on top of your library.\n\u22121: Return target creature to its owner\'s hand.\n\u221212: Exile all cards from target player\'s library, then that player shuffles their hand into their library.',
    'power': None, 'toughness': None, 'loyalty': '3',
    'colors': ['U'], 'color_identity': ['U'],
    'card_type': 'planeswalker', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_LAND = {
    'name': 'Command Tower',
    'mana_cost': '',
    'type_line': 'Land',
    'oracle_text': "{T}: Add one mana of any color in your commander's color identity.",
    'power': None, 'toughness': None,
    'colors': [], 'color_identity': [],
    'card_type': 'land', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_ENCHANTMENT = {
    'name': 'Rhystic Study',
    'mana_cost': '{2}{U}',
    'type_line': 'Enchantment',
    'oracle_text': 'Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.',
    'power': None, 'toughness': None,
    'colors': ['U'], 'color_identity': ['U'],
    'card_type': 'enchantment', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_INSTANT = {
    'name': 'Swords to Plowshares',
    'mana_cost': '{W}',
    'type_line': 'Instant',
    'oracle_text': "Exile target creature. Its controller gains life equal to its power.",
    'power': None, 'toughness': None,
    'colors': ['W'], 'color_identity': ['W'],
    'card_type': 'instant', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_SORCERY = {
    'name': 'Demonic Tutor',
    'mana_cost': '{1}{B}',
    'type_line': 'Sorcery',
    'oracle_text': 'Search your library for a card, put that card into your hand, then shuffle.',
    'power': None, 'toughness': None,
    'colors': ['B'], 'color_identity': ['B'],
    'card_type': 'sorcery', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

SAMPLE_MULTICOLOR = {
    'name': "Assassin's Trophy",
    'mana_cost': '{B}{G}',
    'type_line': 'Instant',
    'oracle_text': "Destroy target nonland permanent an opponent controls. Its controller may search their library for a basic land card, put it onto the battlefield, then shuffle.",
    'power': None, 'toughness': None,
    'colors': ['B', 'G'], 'color_identity': ['B', 'G'],
    'card_type': 'instant', 'quantity': 1,
    'is_commander': False, 'flavor_text': '',
}

ALL_SAMPLE_CARDS = [
    SAMPLE_CREATURE, SAMPLE_BIG_CREATURE, SAMPLE_ARTIFACT,
    SAMPLE_PLANESWALKER, SAMPLE_LAND, SAMPLE_ENCHANTMENT,
    SAMPLE_INSTANT, SAMPLE_SORCERY, SAMPLE_MULTICOLOR,
]


@pytest.fixture
def sample_cards():
    """Return list of sample cards covering all major types."""
    return copy.deepcopy(ALL_SAMPLE_CARDS)


@pytest.fixture
def populated_state(sample_cards):
    """Set up deck_studio globals with sample card data."""
    deck_studio.cards_db = sample_cards
    deck_studio.prompts_map = {c['name']: f"Art prompt for {c['name']}" for c in sample_cards}
    deck_studio.active_deck_id = 'test-deck'
    deck_studio.active_deck_meta = {'name': 'Test Deck'}
    deck_studio.active_model_key = 'local-flux-schnell'
    for card in sample_cards:
        name = card['name']
        slug = deck_studio.name_to_slug(name)
        deck_studio.generation_status[name] = {
            'status': 'complete',
            'message': '',
            'has_raw_art': True,
            'has_composite': True,
        }
    return sample_cards
