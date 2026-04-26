"""Tests for slug generation and path traversal security."""

import pytest
from deck_studio import name_to_slug, deck_id_from_name, _is_safe_deck_id, _safe_inspiration_path
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
