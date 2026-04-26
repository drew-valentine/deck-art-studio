"""Tests for CLIP directive builders (deck_studio)."""

import pytest
from deck_studio import _build_clip_directives_fallback, _build_negative_fallback


# ---------------------------------------------------------------------------
# _build_clip_directives_fallback
# ---------------------------------------------------------------------------
class TestBuildClipDirectivesFallback:
    def test_empty_tokens(self):
        anchor, tradition = _build_clip_directives_fallback({}, False)
        assert anchor == ''
        assert tradition == ''

    def test_none_tokens(self):
        anchor, tradition = _build_clip_directives_fallback(None, False)
        assert anchor == ''

    def test_thick_outlines(self):
        tokens = {'edges': 'thick bold outlines', 'coloring': '', 'tradition': '', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'thick outlines' in anchor

    def test_fine_lines(self):
        tokens = {'edges': 'thin fine lines', 'coloring': '', 'tradition': '', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'fine lines' in anchor

    def test_no_outlines(self):
        tokens = {'edges': 'soft transitions, no outlines', 'coloring': '', 'tradition': '', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'no outlines' in anchor

    def test_flat_colors(self):
        tokens = {'edges': '', 'coloring': 'flat cel-shaded', 'tradition': '', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'flat colors' in anchor

    def test_photographic_tradition(self):
        tokens = {'edges': '', 'coloring': '', 'tradition': 'Photography', 'rendering': ''}
        anchor, tradition = _build_clip_directives_fallback(tokens, False)
        assert 'cinematic' in anchor.lower() or 'photography' in anchor.lower()
        assert 'Photography' in tradition

    def test_3d_tradition(self):
        tokens = {'edges': '', 'coloring': '', 'tradition': '3D CG render', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert '3D' in anchor or '3d' in anchor.lower()

    def test_pixel_art_tradition(self):
        tokens = {'edges': '', 'coloring': '', 'tradition': 'pixel art retro game', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'pixel' in anchor.lower()

    def test_oil_painting_rendering(self):
        tokens = {'edges': '', 'coloring': '', 'tradition': '', 'rendering': 'oil painting impasto'}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'oil painting' in anchor.lower()

    def test_mood_prepended(self):
        tokens = {'edges': '', 'coloring': '', 'tradition': '', 'rendering': '', 'mood': 'dark, mysterious'}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        # Mood words should appear at the start
        assert anchor.startswith('dark') or anchor.startswith('mysterious')

    def test_legacy_line_style_key(self):
        tokens = {'line_style': 'thick bold', 'coloring': '', 'tradition': '', 'rendering': ''}
        anchor, _ = _build_clip_directives_fallback(tokens, False)
        assert 'thick outlines' in anchor


# ---------------------------------------------------------------------------
# _build_negative_fallback
# ---------------------------------------------------------------------------
class TestBuildNegativeFallback:
    def test_default_negatives(self):
        neg = _build_negative_fallback({}, {})
        assert 'photorealistic' in neg.lower() or 'photograph' in neg.lower()
        assert 'anime' in neg.lower()

    def test_photographic_excludes_photo(self):
        tokens = {'rendering': '', 'tradition': 'Photography cinematography'}
        neg = _build_negative_fallback(tokens, {})
        # Should NOT include photograph in negatives for photographic style
        assert 'oil painting' in neg.lower() or 'brushstrokes' in neg.lower()

    def test_anime_style_not_negated(self):
        tokens = {'rendering': 'anime cel-shaded', 'tradition': 'anime'}
        neg = _build_negative_fallback(tokens, {'style_source': 'anime'})
        assert 'anime' not in neg.lower()

    def test_painterly_negates_photo(self):
        tokens = {'rendering': 'oil painting', 'tradition': ''}
        neg = _build_negative_fallback(tokens, {})
        assert 'photograph' in neg.lower()

    def test_pixel_art_negates_smooth(self):
        tokens = {'rendering': '', 'tradition': 'pixel art 8-bit'}
        neg = _build_negative_fallback(tokens, {})
        assert 'smooth' in neg.lower()

    def test_flat_coloring_negates_gradient(self):
        tokens = {'rendering': '', 'tradition': '', 'coloring': 'flat bold'}
        neg = _build_negative_fallback(tokens, {})
        assert 'gradient' in neg.lower()
