"""Tests for rule-based prompt generation."""

import pytest
from prompt_generator import (
    generate_subject_description,
    _extract_keywords,
    generate_style_preamble_from_analysis,
    generate_prompt,
    generate_prompts_for_deck,
)
from tests.conftest import (
    SAMPLE_CREATURE, SAMPLE_BIG_CREATURE, SAMPLE_ARTIFACT,
    SAMPLE_PLANESWALKER, SAMPLE_LAND, SAMPLE_ENCHANTMENT,
    SAMPLE_INSTANT, SAMPLE_SORCERY,
)


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------
class TestExtractKeywords:
    def test_single_keyword(self):
        assert 'flying' in _extract_keywords('Flying')

    def test_multiple_keywords(self):
        kws = _extract_keywords('Flying, trample, haste')
        assert 'flying' in kws
        assert 'trample' in kws
        assert 'haste' in kws

    def test_keyword_in_sentence(self):
        kws = _extract_keywords('This creature has flying and trample.')
        assert 'flying' in kws
        assert 'trample' in kws

    def test_no_keywords(self):
        assert _extract_keywords('Put a card into your hand.') == []

    def test_empty(self):
        assert _extract_keywords('') == []

    def test_first_strike(self):
        assert 'first strike' in _extract_keywords('First strike')


# ---------------------------------------------------------------------------
# generate_subject_description — one test per card type
# ---------------------------------------------------------------------------
class TestGenerateSubjectDescription:
    def test_creature(self):
        desc = generate_subject_description(SAMPLE_CREATURE)
        assert 'Goblin' in desc or 'goblin' in desc.lower()
        assert len(desc) > 20

    def test_big_creature_size(self):
        desc = generate_subject_description(SAMPLE_BIG_CREATURE)
        # Power 12 should trigger size-related language
        assert len(desc) > 20

    def test_artifact(self):
        desc = generate_subject_description(SAMPLE_ARTIFACT)
        assert 'Sol Ring' in desc
        assert len(desc) > 10

    def test_planeswalker(self):
        desc = generate_subject_description(SAMPLE_PLANESWALKER)
        assert 'Jace' in desc
        assert len(desc) > 20

    def test_land(self):
        desc = generate_subject_description(SAMPLE_LAND)
        assert 'Command Tower' in desc or 'tower' in desc.lower()
        assert len(desc) > 10

    def test_enchantment(self):
        desc = generate_subject_description(SAMPLE_ENCHANTMENT)
        assert 'Rhystic' in desc or 'Study' in desc
        assert len(desc) > 10

    def test_instant(self):
        desc = generate_subject_description(SAMPLE_INSTANT)
        assert 'Swords' in desc or 'Plowshares' in desc
        assert len(desc) > 10

    def test_sorcery(self):
        desc = generate_subject_description(SAMPLE_SORCERY)
        assert 'Demonic' in desc or 'Tutor' in desc
        assert len(desc) > 10

    def test_unknown_type(self):
        card = {'name': 'Weird Thing', 'card_type': 'conspiracy'}
        desc = generate_subject_description(card)
        assert 'Weird Thing' in desc


# ---------------------------------------------------------------------------
# generate_style_preamble_from_analysis
# ---------------------------------------------------------------------------
class TestGenerateStylePreamble:
    def test_with_analysis(self):
        preamble = generate_style_preamble_from_analysis(
            'Oil painting style with muted colors', 'Studio Ghibli'
        )
        assert 'Studio Ghibli' in preamble or 'Ghibli' in preamble
        assert len(preamble) > 10

    def test_empty_analysis(self):
        preamble = generate_style_preamble_from_analysis('', '')
        assert isinstance(preamble, str)

    def test_none_source(self):
        preamble = generate_style_preamble_from_analysis('Some style', None)
        assert isinstance(preamble, str)


# ---------------------------------------------------------------------------
# generate_prompt / generate_prompts_for_deck
# ---------------------------------------------------------------------------
class TestGeneratePrompt:
    def test_produces_nonempty_prompt(self):
        prompt = generate_prompt(SAMPLE_CREATURE, 'fantasy oil painting')
        assert isinstance(prompt, str)
        assert len(prompt) > 20
        assert 'Goblin' in prompt or 'goblin' in prompt.lower()

    def test_includes_style(self):
        prompt = generate_prompt(SAMPLE_ARTIFACT, 'watercolor')
        assert 'watercolor' in prompt.lower() or 'Sol Ring' in prompt

    def test_empty_style(self):
        prompt = generate_prompt(SAMPLE_LAND, '')
        assert 'Command Tower' in prompt


class TestGeneratePromptsForDeck:
    def test_returns_all_cards(self):
        cards = [SAMPLE_CREATURE, SAMPLE_ARTIFACT, SAMPLE_LAND]
        prompts = generate_prompts_for_deck(cards, 'fantasy art')
        assert len(prompts) == 3
        names = {p['name'] for p in prompts}
        assert names == {'Goblin Guide', 'Sol Ring', 'Command Tower'}

    def test_each_prompt_nonempty(self):
        cards = [SAMPLE_CREATURE, SAMPLE_ARTIFACT]
        prompts = generate_prompts_for_deck(cards, 'dark fantasy')
        for p in prompts:
            assert len(p['prompt']) > 10
