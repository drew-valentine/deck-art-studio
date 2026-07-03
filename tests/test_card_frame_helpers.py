"""Tests for card frame rendering helper functions."""

import pytest
from card_frame_renderer import (
    parse_mana_cost,
    tokenize_oracle_text,
    hex_with_alpha,
    _blend_hex,
    _parse_loyalty_abilities,
    _is_v2_format,
    _migrate_v1_to_v2,
    resolve_frame_settings,
    _determine_color_key,
)


# ---------------------------------------------------------------------------
# parse_mana_cost
# ---------------------------------------------------------------------------
class TestParseMana:
    def test_simple(self):
        assert parse_mana_cost('{R}') == ['R']

    def test_generic_plus_colors(self):
        assert parse_mana_cost('{2}{W}{U}') == ['2', 'W', 'U']

    def test_hybrid(self):
        assert parse_mana_cost('{W/U}') == ['W/U']

    def test_colorless(self):
        assert parse_mana_cost('{C}{C}') == ['C', 'C']

    def test_empty(self):
        assert parse_mana_cost('') == []
        assert parse_mana_cost(None) == []

    def test_high_generic(self):
        assert parse_mana_cost('{10}{G}{G}') == ['10', 'G', 'G']

    def test_x_cost(self):
        assert parse_mana_cost('{X}{R}{R}') == ['X', 'R', 'R']

    def test_phyrexian(self):
        assert parse_mana_cost('{W/P}') == ['W/P']


# ---------------------------------------------------------------------------
# tokenize_oracle_text
# ---------------------------------------------------------------------------
class TestTokenizeOracle:
    def test_text_only(self):
        tokens = tokenize_oracle_text('Draw a card.')
        assert len(tokens) == 1
        assert tokens[0] == {'type': 'text', 'value': 'Draw a card.'}

    def test_symbol_only(self):
        tokens = tokenize_oracle_text('{T}')
        assert tokens == [{'type': 'symbol', 'value': 'T'}]

    def test_mixed(self):
        tokens = tokenize_oracle_text('{T}: Add {C}{C}.')
        types = [t['type'] for t in tokens]
        assert 'symbol' in types
        assert 'text' in types

    def test_empty(self):
        assert tokenize_oracle_text('') == []
        assert tokenize_oracle_text(None) == []


# ---------------------------------------------------------------------------
# hex_with_alpha
# ---------------------------------------------------------------------------
class TestHexWithAlpha:
    def test_full_opacity(self):
        assert hex_with_alpha('#ff0000', 1.0) == 'rgba(255,0,0,1.0)'

    def test_half_opacity(self):
        assert hex_with_alpha('#00ff00', 0.5) == 'rgba(0,255,0,0.5)'

    def test_black(self):
        assert hex_with_alpha('#000000', 0.8) == 'rgba(0,0,0,0.8)'


# ---------------------------------------------------------------------------
# _blend_hex
# ---------------------------------------------------------------------------
class TestBlendHex:
    def test_ratio_zero_returns_first(self):
        assert _blend_hex('#ff0000', '#0000ff', 0.0) == '#ff0000'

    def test_ratio_one_returns_second(self):
        assert _blend_hex('#ff0000', '#0000ff', 1.0) == '#0000ff'

    def test_midpoint(self):
        result = _blend_hex('#000000', '#ffffff', 0.5)
        # Should be approximately #7f7f7f
        r, g, b = int(result[1:3], 16), int(result[3:5], 16), int(result[5:7], 16)
        assert 125 <= r <= 129
        assert 125 <= g <= 129
        assert 125 <= b <= 129

    def test_invalid_input_returns_first(self):
        assert _blend_hex('#ff0000', 'invalid', 0.5) == '#ff0000'


# ---------------------------------------------------------------------------
# _parse_loyalty_abilities
# ---------------------------------------------------------------------------
class TestParseLoyalty:
    def test_jace(self):
        oracle = (
            "+2: Look at the top card of target player's library.\n"
            "0: Draw three cards, then put two cards from your hand on top of your library.\n"
            "\u22121: Return target creature to its owner's hand.\n"
            "\u221212: Exile all cards from target player's library."
        )
        abilities = _parse_loyalty_abilities(oracle)
        assert len(abilities) == 4
        assert abilities[0]['cost'] is not None
        assert '+2' in abilities[0]['cost']
        assert '0' in abilities[1]['cost']

    def test_static_ability(self):
        oracle = "Creatures you control have haste.\n+1: Draw a card."
        abilities = _parse_loyalty_abilities(oracle)
        # First paragraph is static (no cost)
        assert abilities[0]['cost'] is None
        assert abilities[1]['cost'] is not None

    def test_empty(self):
        assert _parse_loyalty_abilities('') == []

    def test_x_cost(self):
        # X loyalty costs (e.g. Chandra Ablaze) must parse as costed abilities
        abilities = _parse_loyalty_abilities(
            "−X: Deal X damage to each of up to X targets.\n+1: Draw a card.")
        assert abilities[0]['cost'] == '−X'
        assert abilities[1]['cost'] == '+1'

    def test_static_first_detection(self):
        # Detection regex must find loyalty abilities even when the oracle
        # OPENS with a static ability (e.g. Nissa, Who Shakes the World)
        from card_frame_renderer import _LOYALTY_RE
        oracle = ("Whenever you tap a Forest for mana, add an additional {G}.\n"
                  "+1: Untap up to three target lands.")
        assert _LOYALTY_RE.search(oracle) is not None

    def test_plain_text_not_detected(self):
        from card_frame_renderer import _LOYALTY_RE
        assert _LOYALTY_RE.search("Draw a card. Then discard a card.") is None
        # Costs mid-sentence must not match (line-anchored)
        assert _LOYALTY_RE.search("Creatures get +1: a bonus somehow") is None


# ---------------------------------------------------------------------------
# _is_v2_format / _migrate_v1_to_v2
# ---------------------------------------------------------------------------
class TestFrameSettingsFormat:
    def test_v2_detected(self):
        settings = {'layers': {'art': {'visible': True}}}
        assert _is_v2_format(settings) is True

    def test_v1_detected(self):
        settings = {'show_art': True, 'show_name': True}
        assert _is_v2_format(settings) is False

    def test_empty_is_not_v2(self):
        assert _is_v2_format({}) is False

    def test_migrate_v1_to_v2(self):
        v1 = {'show_art': True, 'show_name': False, 'art_opacity': 0.8}
        v2 = _migrate_v1_to_v2(v1)
        assert 'layers' in v2
        assert _is_v2_format(v2) is True


# ---------------------------------------------------------------------------
# _determine_color_key
# ---------------------------------------------------------------------------
class TestDetermineColorKey:
    def test_mono_red(self):
        card = {'colors': ['R'], 'color_identity': ['R'], 'card_type': 'creature', 'type_line': 'Creature'}
        assert _determine_color_key(card) == 'r'

    def test_colorless_artifact(self):
        card = {'colors': [], 'color_identity': [], 'card_type': 'artifact', 'type_line': 'Artifact'}
        assert _determine_color_key(card) == 'a'

    def test_land(self):
        card = {'colors': [], 'color_identity': [], 'card_type': 'land', 'type_line': 'Land'}
        assert _determine_color_key(card) == 'l'

    def test_multicolor(self):
        card = {'colors': ['W', 'U'], 'color_identity': ['W', 'U'], 'card_type': 'instant', 'type_line': 'Instant'}
        assert _determine_color_key(card) == 'm'


# ---------------------------------------------------------------------------
# resolve_frame_settings
# ---------------------------------------------------------------------------
class TestResolveFrameSettings:
    def test_defaults_without_overrides(self):
        card = {'name': 'Test', 'frame_overrides': {}}
        result = resolve_frame_settings(card)
        assert 'layers' in result
        assert 'border' in result['layers']

    def test_deck_settings_applied(self):
        card = {'name': 'Test', 'frame_overrides': {}}
        deck = {'layers': {'border': {'opacity': 0.5}}}
        result = resolve_frame_settings(card, deck)
        assert result['layers']['border']['opacity'] == 0.5

    def test_card_overrides_deck(self):
        card = {
            'name': 'Test',
            'frame_overrides': {'layers': {'border': {'opacity': 0.3}}},
        }
        deck = {'layers': {'border': {'opacity': 0.8}}}
        result = resolve_frame_settings(card, deck)
        assert result['layers']['border']['opacity'] == 0.3

    def test_style_key_propagated(self):
        card = {'name': 'Test', 'frame_overrides': {}}
        result = resolve_frame_settings(card)
        assert result['style'] == 'basic'


# ---------------------------------------------------------------------------
# Split text layouts (adventure / split / room)
# ---------------------------------------------------------------------------
class TestSplitTextLayouts:
    ADVENTURE = {
        'name': 'Murderous Rider // Swift End',
        'layout': 'adventure',
        'mana_cost': '{1}{B}{B}',
        'type_line': 'Creature — Zombie Knight',
        'oracle_text': 'Lifelink',
        'power': '2', 'toughness': '3',
        'colors': ['B'], 'color_identity': ['B'],
        'card_type': 'creature',
        'card_faces': [
            {'name': 'Murderous Rider', 'mana_cost': '{1}{B}{B}',
             'type_line': 'Creature — Zombie Knight', 'oracle_text': 'Lifelink'},
            {'name': 'Swift End', 'mana_cost': '{1}{B}{B}',
             'type_line': 'Instant — Adventure',
             'oracle_text': 'Destroy target creature or planeswalker. You lose 2 life.'},
        ],
    }
    ROOM = {
        'name': 'Smoky Lounge // Misty Salon',
        'layout': 'split',
        'mana_cost': '{2}{R}',
        'type_line': 'Enchantment — Room',
        'oracle_text': 'At the beginning of your first main phase, add {R}{R}.',
        'colors': ['R', 'U'], 'color_identity': ['R', 'U'],
        'card_type': 'enchantment',
        'card_faces': [
            {'name': 'Smoky Lounge', 'mana_cost': '{2}{R}',
             'type_line': 'Enchantment — Room',
             'oracle_text': 'At the beginning of your first main phase, add {R}{R}.'},
            {'name': 'Misty Salon', 'mana_cost': '{3}{U}',
             'type_line': 'Enchantment — Room',
             'oracle_text': 'When you unlock this door, create a Spirit token.'},
        ],
    }

    def test_adventure_card_data(self):
        from card_frame_renderer import _build_card_data
        cd = _build_card_data(self.ADVENTURE, {})
        assert cd.layout == 'adventure'
        assert cd.split_faces is not None
        # Adventure cards title the creature half only
        assert cd.name == 'Murderous Rider'
        assert cd.mana_cost == '{1}{B}{B}'

    def test_room_card_data_blanks_title_mana(self):
        from card_frame_renderer import _build_card_data
        cd = _build_card_data(self.ROOM, {})
        assert cd.layout == 'split'
        assert cd.split_faces is not None
        # Per-half costs render in the column headers, not the title bar
        assert cd.mana_cost == ''
        assert cd.name == 'Smoky Lounge // Misty Salon'

    def test_normal_card_has_no_split_faces(self):
        from card_frame_renderer import _build_card_data
        cd = _build_card_data({'name': 'Sol Ring', 'mana_cost': '{1}',
                               'type_line': 'Artifact', 'oracle_text': 'x'}, {})
        assert cd.split_faces is None

    def test_transform_card_has_no_split_faces(self):
        from card_frame_renderer import _build_card_data
        cd = _build_card_data({'name': 'A // B', 'layout': 'transform',
                               'mana_cost': '{1}', 'type_line': 'Creature',
                               'oracle_text': 'x',
                               'card_faces': [{'name': 'A'}, {'name': 'B'}]}, {})
        assert cd.split_faces is None

    def test_split_rules_svg_renders_both_halves(self):
        from card_frame_renderer import _build_card_data, _render_split_rules_svg
        cd = _build_card_data(self.ADVENTURE, {})
        parts = _render_split_rules_svg(cd, {}, 60, 700, 620, 280, '#000', 30,
                                        avoid=(920.0, 502.0))
        blob = '\n'.join(parts)
        assert 'Swift End' in blob                    # adventure header
        assert 'Instant — Adventure' in blob          # adventure type
        assert 'Lifelink' in blob                     # creature rules
        assert 'Destroy' in blob                      # adventure rules (may wrap mid-phrase)
        assert '<line' in blob                        # column divider

    def test_split_rules_svg_room_headers(self):
        from card_frame_renderer import _build_card_data, _render_split_rules_svg
        cd = _build_card_data(self.ROOM, {})
        parts = _render_split_rules_svg(cd, {}, 60, 700, 620, 280, '#000', 30)
        blob = '\n'.join(parts)
        assert 'Smoky Lounge' in blob
        assert 'Misty Salon' in blob
        assert 'unlock' in blob


class TestPlaneswalkerDetection:
    def test_back_face_planeswalker_without_loyalty(self):
        from card_frame_renderer import _is_planeswalker, CardData
        # Transform PW back faces have loyalty abilities but no loyalty value
        cd = CardData(name='Arlinn, Embraced by the Moon', mana_cost='',
                      type_line='Legendary Planeswalker — Arlinn',
                      oracle_text='+1: Creatures you control get +1/+1.\n−1: Deal 3 damage.',
                      loyalty=None, card_type='planeswalker')
        assert _is_planeswalker(cd) is True

    def test_normal_creature_not_planeswalker(self):
        from card_frame_renderer import _is_planeswalker, CardData
        cd = CardData(name='Sol Ring', mana_cost='{1}', type_line='Artifact',
                      oracle_text='{T}: Add {C}{C}.', card_type='artifact')
        assert _is_planeswalker(cd) is False


class TestBattleFrame:
    BATTLE = {
        'name': 'Invasion of Zendikar // Awakened Skyclave',
        'layout': 'transform',
        'mana_cost': '{3}{G}',
        'type_line': 'Battle — Siege',
        'oracle_text': 'When this Siege enters, search your library for up to two basic land cards.',
        'defense': '3',
        'colors': ['G'], 'color_identity': ['G'],
        'card_type': 'battle',
        'card_faces': [
            {'name': 'Invasion of Zendikar', 'type_line': 'Battle — Siege',
             'defense': '3', 'mana_cost': '{3}{G}'},
            {'name': 'Awakened Skyclave', 'type_line': 'Creature — Elemental',
             'power': '4', 'toughness': '4', 'defense': None, 'mana_cost': ''},
        ],
    }

    def test_battle_detection_and_defense(self):
        from card_frame_renderer import _build_card_data, _is_battle
        cd = _build_card_data(self.BATTLE, {})
        assert _is_battle(cd) is True
        assert cd.defense == '3'
        # Normal cards are not battles
        cd2 = _build_card_data({'name': 'Sol Ring', 'type_line': 'Artifact',
                                'mana_cost': '{1}', 'oracle_text': 'x'}, {})
        assert _is_battle(cd2) is False

    def test_battle_svg_contents(self):
        from card_frame_renderer import _build_card_data, _create_battle_frame_svg
        cd = _build_card_data(self.BATTLE, {})
        svg = _create_battle_frame_svg(cd, {})
        # Titles only the front face name
        assert 'Invasion of Zendikar<' in svg
        assert 'Awakened Skyclave' not in svg
        assert 'Battle — Siege' in svg
        assert '>3</text>' in svg  # defense shield number

    def test_battle_composite_is_rotated_portrait(self, tmp_path):
        from card_frame_renderer import composite_card, CARD_WIDTH, CARD_HEIGHT
        from PIL import Image
        art = tmp_path / 'art.png'
        Image.new('RGB', (896, 672), (40, 90, 40)).save(art)
        out = tmp_path / 'out.png'
        composite_card(self.BATTLE, str(art), None, str(out))
        img = Image.open(out)
        assert img.size == (CARD_WIDTH, CARD_HEIGHT)  # portrait, like real prints
