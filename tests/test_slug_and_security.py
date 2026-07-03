"""Tests for slug generation and path traversal security."""

import pytest
from deck_studio import (name_to_slug, deck_id_from_name, _is_safe_deck_id,
                         _safe_inspiration_path, face_key, is_dfc, back_face_card)
from pathlib import Path


# ---------------------------------------------------------------------------
# name_to_slug
# ---------------------------------------------------------------------------
class TestNameToSlug:
    def test_basic(self):
        assert name_to_slug('Sol Ring') == 'sol_ring'

    def test_apostrophe_stripped(self):
        assert name_to_slug("Assassin's Trophy") == 'assassins_trophy'

    def test_comma_stripped(self):
        assert name_to_slug('Jace, the Mind Sculptor') == 'jace_the_mind_sculptor'

    def test_hyphen_to_underscore(self):
        assert name_to_slug('Well-laid Plans') == 'well_laid_plans'

    def test_dfc_split_name(self):
        assert name_to_slug('Bonecrusher Giant // Stomp') == 'bonecrusher_giant__stomp'

    def test_slash_to_underscore(self):
        assert name_to_slug('Fire/Ice') == 'fire_ice'

    def test_path_traversal_stripped(self):
        slug = name_to_slug('../etc/passwd')
        assert '..' not in slug
        assert '/' not in slug

    def test_dots_stripped(self):
        slug = name_to_slug('...')
        assert slug == ''

    def test_lowercase(self):
        assert name_to_slug('AETHERFLUX RESERVOIR') == 'aetherflux_reservoir'

    def test_back_face_suffix(self):
        assert (name_to_slug('Accursed Witch // Infectious Curse [back]')
                == 'accursed_witch__infectious_curse__back')


# ---------------------------------------------------------------------------
# Double-faced card helpers
# ---------------------------------------------------------------------------
class TestDfcHelpers:
    TRANSFORM_CARD = {
        'name': 'Accursed Witch // Infectious Curse',
        'layout': 'transform',
        'mana_cost': '{3}{B}',
        'type_line': 'Creature — Human Shaman',
        'oracle_text': 'Spells your opponents cast...',
        'power': '4', 'toughness': '2',
        'colors': ['B'],
        'color_identity': ['B'],
        'frame_overrides': {'frame_set': 'm15',
                            'text_overrides': {'name': 'Front Name'},
                            'art_offset': {'x': 0, 'y': -176}, 'art_zoom': 0.5},
        'card_faces': [
            {'name': 'Accursed Witch', 'mana_cost': '{3}{B}',
             'type_line': 'Creature — Human Shaman',
             'oracle_text': 'Spells your opponents cast...',
             'power': '4', 'toughness': '2', 'colors': ['B'],
             'card_type': 'creature', 'flavor_text': '', 'art_crop_url': 'f'},
            {'name': 'Infectious Curse', 'mana_cost': '',
             'type_line': 'Enchantment — Aura Curse',
             'oracle_text': 'Enchant player', 'power': None, 'toughness': None,
             'colors': ['B'], 'card_type': 'enchantment',
             'flavor_text': 'flavor', 'art_crop_url': 'b'},
        ],
    }

    def test_face_key(self):
        assert face_key('Sol Ring') == 'Sol Ring'
        assert face_key('Sol Ring', 'front') == 'Sol Ring'
        assert face_key('A // B', 'back') == 'A // B [back]'

    def test_is_dfc(self):
        assert is_dfc(self.TRANSFORM_CARD) is True
        assert is_dfc({'name': 'Sol Ring'}) is False
        # Adventures have faces but a single shared art — not DFC
        assert is_dfc({'name': 'X // Y', 'layout': 'adventure',
                       'card_faces': [{}, {}]}) is False

    def test_back_face_card_merges_face_over_card(self):
        back = back_face_card(self.TRANSFORM_CARD)
        assert back['name'] == 'Infectious Curse'
        assert back['type_line'] == 'Enchantment — Aura Curse'
        assert back['card_type'] == 'enchantment'
        assert back['power'] is None
        assert back['colors'] == ['B']
        # Style-level overrides inherited; front-face-specific ones stripped
        assert back['frame_overrides'] == {'frame_set': 'm15'}
        assert back['color_identity'] == ['B']

    def test_back_face_card_none_for_single_faced(self):
        assert back_face_card({'name': 'Sol Ring'}) is None


# ---------------------------------------------------------------------------
# deck_id_from_name
# ---------------------------------------------------------------------------
class TestDeckIdFromName:
    def test_basic(self):
        assert deck_id_from_name('My Cool Deck') == 'my-cool-deck'

    def test_special_chars_replaced(self):
        result = deck_id_from_name("Holly's Propaganda!")
        assert "'" not in result
        assert '!' not in result
        assert result == 'holly-s-propaganda'

    def test_truncated_to_60(self):
        long_name = 'A' * 100
        assert len(deck_id_from_name(long_name)) <= 60

    def test_leading_trailing_hyphens_stripped(self):
        assert deck_id_from_name('---test---') == 'test'

    def test_empty_string(self):
        assert deck_id_from_name('') == ''


# ---------------------------------------------------------------------------
# _is_safe_deck_id — security-critical
# ---------------------------------------------------------------------------
class TestIsSafeDeckId:
    def test_valid_slug(self):
        assert _is_safe_deck_id('my-cool-deck') is True

    def test_valid_with_numbers(self):
        assert _is_safe_deck_id('deck-v2-final') is True

    def test_rejects_empty(self):
        assert _is_safe_deck_id('') is False

    def test_rejects_none(self):
        assert _is_safe_deck_id(None) is False

    def test_rejects_dotdot(self):
        assert _is_safe_deck_id('..') is False

    def test_rejects_path_traversal(self):
        assert _is_safe_deck_id('../etc/passwd') is False

    def test_rejects_forward_slash(self):
        assert _is_safe_deck_id('foo/bar') is False

    def test_rejects_backslash(self):
        assert _is_safe_deck_id('foo\\bar') is False

    def test_rejects_embedded_dotdot(self):
        assert _is_safe_deck_id('foo/../bar') is False

    def test_rejects_dotdot_suffix(self):
        assert _is_safe_deck_id('foo/..') is False


# ---------------------------------------------------------------------------
# _safe_inspiration_path — security-critical
# ---------------------------------------------------------------------------
class TestSafeInspirationPath:
    def test_valid_filename(self, tmp_path):
        result = _safe_inspiration_path(tmp_path, 'inspiration_1.png')
        assert result is not None
        assert result.name == 'inspiration_1.png'

    def test_rejects_empty(self, tmp_path):
        assert _safe_inspiration_path(tmp_path, '') is None

    def test_rejects_none(self, tmp_path):
        assert _safe_inspiration_path(tmp_path, None) is None

    def test_rejects_dotdot(self, tmp_path):
        assert _safe_inspiration_path(tmp_path, '../secret.png') is None

    def test_rejects_slash(self, tmp_path):
        assert _safe_inspiration_path(tmp_path, 'sub/file.png') is None

    def test_rejects_backslash(self, tmp_path):
        assert _safe_inspiration_path(tmp_path, 'sub\\file.png') is None


class TestRotatedSplitHelpers:
    FIRE_ICE = {
        'name': 'Fire // Ice',
        'layout': 'split',
        'mana_cost': '{1}{R}',
        'type_line': 'Instant',
        'oracle_text': 'Fire deals 2 damage divided as you choose among one or two targets.',
        'colors': ['R', 'U'], 'color_identity': ['R', 'U'],
        'card_type': 'instant',
        'frame_overrides': {'frame_set': 'm15', 'art_zoom': 0.5},
        'card_faces': [
            {'name': 'Fire', 'mana_cost': '{1}{R}', 'type_line': 'Instant',
             'oracle_text': 'Fire deals 2 damage divided as you choose among one or two targets.'},
            {'name': 'Ice', 'mana_cost': '{1}{U}', 'type_line': 'Instant',
             'oracle_text': 'Tap target permanent.\nDraw a card.'},
        ],
    }
    ROOM = {
        'name': 'Smoky Lounge // Misty Salon',
        'layout': 'split',
        'type_line': 'Enchantment — Room',
        'card_faces': [{'name': 'A'}, {'name': 'B'}],
    }

    def test_rotated_split_detection(self):
        from deck_studio import is_rotated_split, has_second_art_face, is_dfc
        assert is_rotated_split(self.FIRE_ICE) is True
        assert is_rotated_split(self.ROOM) is False        # rooms stay portrait
        assert has_second_art_face(self.FIRE_ICE) is True
        assert is_dfc(self.FIRE_ICE) is False              # not a DFC

    def test_split_half_card(self):
        from deck_studio import split_half_card
        left = split_half_card(self.FIRE_ICE, 0)
        right = split_half_card(self.FIRE_ICE, 1)
        assert left['name'] == 'Fire' and right['name'] == 'Ice'
        # Half colors derive from each half's own mana cost
        assert left['colors'] == ['R']
        assert right['colors'] == ['U']
        # No layout/faces — halves render as normal mini cards
        assert 'layout' not in left and 'card_faces' not in left
        # Combined-card art zoom must not leak onto halves
        assert left['frame_overrides'] == {'frame_set': 'm15'}
