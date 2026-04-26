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
        assert result['style'] == 'classic'
