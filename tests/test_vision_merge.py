"""Tests for vision analyzer merge and subject building functions."""

import pytest
from vision_analyzer import merge_style_descriptions, _article, _build_base_subject


# ---------------------------------------------------------------------------
# _article
# ---------------------------------------------------------------------------
class TestArticle:
    def test_vowel(self):
        assert _article('elf') == 'an'
        assert _article('Angel') == 'an'

    def test_consonant(self):
        assert _article('goblin') == 'a'
        assert _article('Dragon') == 'a'

    def test_empty(self):
        assert _article('') == 'a'


# ---------------------------------------------------------------------------
# _build_base_subject
# ---------------------------------------------------------------------------
class TestBuildBaseSubject:
    def test_creature_with_subtypes(self):
        card = {'name': 'Goblin Guide', 'type_line': 'Creature \u2014 Goblin Scout',
                'power': '2', 'toughness': '2'}
        subj = _build_base_subject(card)
        assert 'Goblin Guide' in subj
        assert 'goblin scout' in subj.lower()

    def test_massive_creature(self):
        card = {'name': 'Ghalta', 'type_line': 'Creature \u2014 Elder Dinosaur',
                'power': '12', 'toughness': '12'}
        subj = _build_base_subject(card)
        assert 'massive' in subj.lower()

    def test_small_creature(self):
        card = {'name': 'Birds of Paradise', 'type_line': 'Creature \u2014 Bird',
                'power': '0', 'toughness': '1'}
        subj = _build_base_subject(card)
        assert 'small' in subj.lower()

    def test_land(self):
        card = {'name': 'Command Tower', 'type_line': 'Land'}
        subj = _build_base_subject(card)
        assert 'Command Tower' in subj
        assert 'landscape' in subj.lower()

    def test_artifact(self):
        card = {'name': 'Sol Ring', 'type_line': 'Artifact'}
        subj = _build_base_subject(card)
        assert 'Sol Ring' in subj
        assert 'artifact' in subj.lower()

    def test_instant(self):
        card = {'name': 'Lightning Bolt', 'type_line': 'Instant'}
        subj = _build_base_subject(card)
        assert 'Lightning Bolt' in subj

    def test_enchantment(self):
        card = {'name': 'Rhystic Study', 'type_line': 'Enchantment'}
        subj = _build_base_subject(card)
        assert 'enchantment' in subj.lower()


# ---------------------------------------------------------------------------
# merge_style_descriptions
# ---------------------------------------------------------------------------
class TestMergeStyleDescriptions:
    def test_empty_list(self):
        assert merge_style_descriptions([]) == ''

    def test_single_description(self):
        desc = "Source: Ghibli\nArt Style: Watercolor"
        assert merge_style_descriptions([desc]) == desc

    def test_none_filtered(self):
        assert merge_style_descriptions([None, '', '  ']) == ''

    def test_two_descriptions_merged(self):
        d1 = "Source: Ghibli\nArt Style: Watercolor\nColors: warm pastels"
        d2 = "Source: Miyazaki\nArt Style: Oil painting\nColors: cool blues"
        merged = merge_style_descriptions([d1, d2])
        # Sources should be combined
        assert 'Ghibli' in merged
        assert 'Miyazaki' in merged
        # Art styles should be joined with |
        assert 'Watercolor' in merged
        assert 'Oil painting' in merged

    def test_duplicate_sources_deduplicated(self):
        d1 = "Source: Original\nArt Style: Watercolor"
        d2 = "Source: Original\nArt Style: Oil"
        merged = merge_style_descriptions([d1, d2])
        # "Original" sources should result in single "Original"
        assert 'Source: Original' in merged

    def test_colors_comma_deduplicated(self):
        d1 = "Colors: red, blue, green"
        d2 = "Colors: blue, yellow, red"
        merged = merge_style_descriptions([d1, d2])
        # Count unique colors
        colors_line = [l for l in merged.split('\n') if l.startswith('Colors:')][0]
        items = [c.strip() for c in colors_line.replace('Colors:', '').split(',')]
        assert len(items) == len(set(items))  # no duplicates

    def test_technique_pipe_joined(self):
        d1 = "Technique: Soft brushwork"
        d2 = "Technique: Heavy impasto"
        merged = merge_style_descriptions([d1, d2])
        assert '|' in merged
        assert 'Soft brushwork' in merged
        assert 'Heavy impasto' in merged
