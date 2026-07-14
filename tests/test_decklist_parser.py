"""Tests for decklist parsing (scryfall_client)."""

import pytest
from scryfall_client import parse_decklist, _parse_card_line, normalize_card_type, scryfall_to_card_entry


# ---------------------------------------------------------------------------
# _parse_card_line
# ---------------------------------------------------------------------------
class TestParseCardLine:
    def test_mtgo_format(self):
        entry = _parse_card_line('1 Sol Ring')
        assert entry['name'] == 'Sol Ring'
        assert entry['quantity'] == 1

    def test_archidekt_format(self):
        entry = _parse_card_line('1x Sol Ring (C21) 62 [Artifact]')
        assert entry['name'] == 'Sol Ring'
        assert entry['quantity'] == 1
        assert entry['set_code'] == 'c21'
        assert entry['collector_number'] == '62'
        assert entry['category'] == 'Artifact'

    def test_arena_format(self):
        entry = _parse_card_line('4 Lightning Bolt (STA) 62')
        assert entry['name'] == 'Lightning Bolt'
        assert entry['quantity'] == 4
        assert entry['set_code'] == 'sta'

    def test_simple_name_only(self):
        entry = _parse_card_line('Sol Ring')
        assert entry['name'] == 'Sol Ring'
        assert entry['quantity'] == 1

    def test_quantity_with_x(self):
        entry = _parse_card_line('3x Command Tower')
        assert entry['quantity'] == 3
        assert entry['name'] == 'Command Tower'

    def test_foil_marker(self):
        entry = _parse_card_line('1 Sol Ring *F*')
        assert entry['is_foil'] is True
        assert entry['name'] == 'Sol Ring'

    def test_label_tag_stripped(self):
        entry = _parse_card_line('1x Sol Ring ^Ramp,#green^')
        assert entry['name'] == 'Sol Ring'
        assert '^' not in entry['name']

    def test_doubled_name_deduplicated(self):
        entry = _parse_card_line("1 Krark's Thumb // Krark's Thumb")
        assert entry['name'] == "Krark's Thumb"

    def test_real_split_name_preserved(self):
        entry = _parse_card_line('1 Bonecrusher Giant // Stomp')
        assert entry['name'] == 'Bonecrusher Giant // Stomp'

    def test_empty_returns_none(self):
        assert _parse_card_line('') is None

    def test_apostrophe_in_name(self):
        entry = _parse_card_line("1 Assassin's Trophy")
        assert entry['name'] == "Assassin's Trophy"


# ---------------------------------------------------------------------------
# parse_decklist
# ---------------------------------------------------------------------------
class TestParseDecklist:
    def test_simple_list(self):
        text = "1 Sol Ring\n1 Command Tower\n1 Lightning Bolt"
        entries = parse_decklist(text)
        assert len(entries) == 3
        names = {e['name'] for e in entries}
        assert names == {'Sol Ring', 'Command Tower', 'Lightning Bolt'}

    def test_commander_header(self):
        text = "Commander\n1 Kenrith, the Returned King\n\nCreature (10)\n1 Sol Ring"
        entries = parse_decklist(text)
        commander = next(e for e in entries if e['name'] == 'Kenrith, the Returned King')
        assert commander['is_commander'] is True

    def test_comments_and_blanks_skipped(self):
        text = "# My deck\n\n// Comment\n1 Sol Ring\n\n1 Lightning Bolt"
        entries = parse_decklist(text)
        assert len(entries) == 2

    def test_section_headers_not_cards(self):
        text = "Creature (15)\n1 Goblin Guide\nInstant (5)\n1 Lightning Bolt"
        entries = parse_decklist(text)
        assert len(entries) == 2
        assert all(e['name'] not in ('Creature', 'Instant') for e in entries)

    def test_empty_input(self):
        assert parse_decklist('') == []
        assert parse_decklist('   \n  \n  ') == []

    def test_archidekt_full(self):
        text = """Commander
1x Kenrith, the Returned King (ELD) 303 [Commander] ^Commander^

Creature (2)
1x Goblin Guide (ZEN) 126 [Creature]
1x Sol Ring (C21) 62 [Artifact]"""
        entries = parse_decklist(text)
        assert len(entries) == 3
        kenrith = next(e for e in entries if 'Kenrith' in e['name'])
        assert kenrith['is_commander'] is True


# ---------------------------------------------------------------------------
# normalize_card_type
# ---------------------------------------------------------------------------
class TestNormalizeCardType:
    @pytest.mark.parametrize('type_line,expected', [
        ('Creature \u2014 Goblin Scout', 'creature'),
        ('Legendary Creature \u2014 Elder Dinosaur', 'creature'),
        ('Artifact Creature \u2014 Golem', 'creature'),  # creature takes precedence
        ('Legendary Planeswalker \u2014 Jace', 'planeswalker'),
        ('Instant', 'instant'),
        ('Sorcery', 'sorcery'),
        ('Enchantment', 'enchantment'),
        ('Artifact', 'artifact'),
        ('Land', 'land'),
        ('Artifact Land', 'artifact'),  # artifact before land
        ('Tribal Instant \u2014 Goblin', 'instant'),
        ('Legendary Enchantment Artifact', 'enchantment'),
        ('Snow Land', 'land'),
        ('Conspiracy', 'other'),
    ])
    def test_type_classification(self, type_line, expected):
        assert normalize_card_type(type_line) == expected


# ---------------------------------------------------------------------------
# scryfall_to_card_entry
# ---------------------------------------------------------------------------
class TestScryfallToCardEntry:
    def test_basic_card(self):
        sf = {
            'name': 'Sol Ring',
            'mana_cost': '{1}',
            'type_line': 'Artifact',
            'oracle_text': '{T}: Add {C}{C}.',
            'colors': [],
            'color_identity': [],
        }
        entry = scryfall_to_card_entry(sf)
        assert entry['name'] == 'Sol Ring'
        assert entry['card_type'] == 'artifact'
        assert entry['quantity'] == 1
        assert entry['is_commander'] is False

    def test_dfc_uses_front_face(self):
        sf = {
            'name': 'Delver of Secrets // Insectile Aberration',
            'mana_cost': '',
            'type_line': '',
            'colors': ['U'],
            'color_identity': ['U'],
            'card_faces': [
                {
                    'name': 'Delver of Secrets',
                    'mana_cost': '{U}',
                    'type_line': 'Creature \u2014 Human Wizard',
                    'oracle_text': 'At the beginning of your upkeep...',
                    'power': '1', 'toughness': '1',
                },
                {
                    'name': 'Insectile Aberration',
                    'mana_cost': '',
                    'type_line': 'Creature \u2014 Human Insect',
                    'oracle_text': 'Flying',
                    'power': '3', 'toughness': '2',
                },
            ],
        }
        entry = scryfall_to_card_entry(sf)
        assert entry['oracle_text'] == 'At the beginning of your upkeep...'
        assert entry['power'] == '1'
        assert entry['card_type'] == 'creature'

    def test_reversed_card_deduplicates_name(self):
        sf = {
            'name': "Okaun, Eye of Chaos // Okaun, Eye of Chaos",
            'mana_cost': '{3}{R}{R}',
            'type_line': 'Legendary Creature',
            'oracle_text': 'text',
            'colors': ['R'],
            'color_identity': ['R'],
        }
        entry = scryfall_to_card_entry(sf)
        assert entry['name'] == 'Okaun, Eye of Chaos'

    def test_transform_card_stores_layout_and_faces(self):
        sf = {
            'name': 'Accursed Witch // Infectious Curse',
            'layout': 'transform',
            'mana_cost': '',
            'type_line': '',
            'colors': ['B'],
            'color_identity': ['B'],
            'card_faces': [
                {
                    'name': 'Accursed Witch',
                    'mana_cost': '{3}{B}',
                    'type_line': 'Creature — Human Shaman',
                    'oracle_text': 'Spells your opponents cast...',
                    'power': '4', 'toughness': '2',
                    'image_uris': {'art_crop': 'https://cards.scryfall.io/art_crop/front/x.jpg'},
                },
                {
                    'name': 'Infectious Curse',
                    'mana_cost': '',
                    'type_line': 'Enchantment — Aura Curse',
                    'oracle_text': 'Enchant player',
                    'color_indicator': ['B'],
                    'image_uris': {'art_crop': 'https://cards.scryfall.io/art_crop/back/x.jpg'},
                },
            ],
        }
        entry = scryfall_to_card_entry(sf)
        assert entry['layout'] == 'transform'
        assert len(entry['card_faces']) == 2
        back = entry['card_faces'][1]
        assert back['name'] == 'Infectious Curse'
        assert back['card_type'] == 'enchantment'
        # Back face colors fall back to the color_indicator
        assert back['colors'] == ['B']
        assert back['art_crop_url'].endswith('back/x.jpg')

    def test_single_face_card_has_no_faces(self):
        sf = {
            'name': 'Sol Ring',
            'layout': 'normal',
            'mana_cost': '{1}',
            'type_line': 'Artifact',
            'oracle_text': '{T}: Add {C}{C}.',
            'colors': [],
            'color_identity': [],
        }
        entry = scryfall_to_card_entry(sf)
        assert 'layout' not in entry
        assert 'card_faces' not in entry

    def test_adventure_card_stores_faces_but_shared_art(self):
        sf = {
            'name': 'Murderous Rider // Swift End',
            'layout': 'adventure',
            'mana_cost': '{2}{B}{B}',
            'type_line': 'Creature — Zombie Knight // Instant — Adventure',
            'colors': ['B'],
            'color_identity': ['B'],
            'image_uris': {'art_crop': 'https://cards.scryfall.io/art_crop/front/m.jpg'},
            'card_faces': [
                {'name': 'Murderous Rider', 'mana_cost': '{2}{B}{B}',
                 'type_line': 'Creature — Zombie Knight',
                 'oracle_text': 'Lifelink', 'power': '2', 'toughness': '3'},
                {'name': 'Swift End', 'mana_cost': '{1}{B}{B}',
                 'type_line': 'Instant — Adventure',
                 'oracle_text': 'Destroy target creature or planeswalker.'},
            ],
        }
        entry = scryfall_to_card_entry(sf)
        assert entry['layout'] == 'adventure'
        assert len(entry['card_faces']) == 2
        # Adventure faces share one art — no per-face art_crop
        assert entry['card_faces'][1]['art_crop_url'] == ''
        assert entry['art_crop_url'].endswith('front/m.jpg')

    def test_commander_flag(self):
        sf = {
            'name': 'Kenrith',
            'mana_cost': '{4}{W}',
            'type_line': 'Legendary Creature',
            'oracle_text': '',
            'colors': ['W'],
            'color_identity': ['W', 'U', 'B', 'R', 'G'],
        }
        entry = scryfall_to_card_entry(sf, quantity=1, is_commander=True)
        assert entry['is_commander'] is True

    def test_battle_card_stores_defense(self):
        sf = {
            'name': 'Invasion of Zendikar // Awakened Skyclave',
            'layout': 'transform',
            'colors': ['G'],
            'color_identity': ['G'],
            'card_faces': [
                {'name': 'Invasion of Zendikar', 'mana_cost': '{3}{G}',
                 'type_line': 'Battle — Siege', 'defense': '3',
                 'oracle_text': 'When this Siege enters...',
                 'image_uris': {'art_crop': 'https://cards.scryfall.io/art_crop/front/z.jpg'}},
                {'name': 'Awakened Skyclave', 'mana_cost': '',
                 'type_line': 'Creature — Elemental',
                 'oracle_text': 'Vigilance', 'power': '4', 'toughness': '4',
                 'image_uris': {'art_crop': 'https://cards.scryfall.io/art_crop/back/z.jpg'}},
            ],
        }
        entry = scryfall_to_card_entry(sf)
        assert entry['layout'] == 'transform'
        assert entry['defense'] == '3'          # flattened front-face defense
        assert entry['card_type'] == 'battle'
        assert entry['card_faces'][0]['defense'] == '3'
        assert entry['card_faces'][1]['defense'] is None


class TestCreatureTypeInPrompt:
    """A creature's generated prompt must name its creature type — the LLM is
    instructed to include it but often drops it; injection is the guarantee."""

    CARD = {'name': 'Okaun, Eye of Chaos', 'card_type': 'creature',
            'type_line': 'Legendary Creature — Cyclops Berserker'}

    def test_missing_type_injected_after_name(self):
        from prompt_generator import _ensure_creature_type_in_prompt
        text = "Okaun, Eye of Chaos sits majestically, his eye blazing."
        out = _ensure_creature_type_in_prompt(text, self.CARD)
        assert out.startswith("Okaun, Eye of Chaos, a Cyclops Berserker, sits")

    def test_present_type_untouched(self):
        from prompt_generator import _ensure_creature_type_in_prompt
        text = "Okaun, Eye of Chaos, a towering Cyclops Berserker, charges."
        assert _ensure_creature_type_in_prompt(text, self.CARD) == text

    def test_name_absent_prepends(self):
        from prompt_generator import _ensure_creature_type_in_prompt
        out = _ensure_creature_type_in_prompt("A one-eyed brute rampages.", self.CARD)
        assert out.startswith("Okaun, Eye of Chaos, a Cyclops Berserker — A one-eyed")

    def test_vowel_article(self):
        from prompt_generator import _ensure_creature_type_in_prompt
        card = {'name': 'Ghalta', 'card_type': 'creature',
                'type_line': 'Legendary Creature — Elder Dinosaur'}
        out = _ensure_creature_type_in_prompt("Ghalta stomps.", card)
        assert "Ghalta, an Elder Dinosaur, stomps." == out

    def test_non_creature_untouched(self):
        from prompt_generator import _ensure_creature_type_in_prompt
        card = {'name': 'Sol Ring', 'card_type': 'artifact', 'type_line': 'Artifact'}
        assert _ensure_creature_type_in_prompt("A ring.", card) == "A ring."


class TestFranchiseFirewall:
    """Flavor text written in a franchise's voice quotes its cast; those
    sentences must never anchor an art scene (observed: 'Rick's garage' flavor
    -> literal Rick in card art)."""

    HINT = "Rick & Morty — 3D render, cel animation, vibrant"

    def test_tokens_from_name_segment_only(self):
        from prompt_generator import _franchise_tokens
        toks = _franchise_tokens(self.HINT)
        assert toks == {'rick', 'morty'}          # not 'render'/'animation'

    def test_offending_sentence_dropped(self):
        from prompt_generator import _strip_franchise_sentences
        flavor = ("All roads may lead to Rick's garage, but some end there. "
                  "Fortune favors the bold.")
        out = _strip_franchise_sentences(flavor, self.HINT)
        assert out == "Fortune favors the bold."

    def test_fully_offending_flavor_becomes_empty(self):
        from prompt_generator import _strip_franchise_sentences
        assert _strip_franchise_sentences(
            "Pick your battles, but always ride into the fray, Morty.",
            self.HINT) == ""

    def test_clean_flavor_untouched(self):
        from prompt_generator import _strip_franchise_sentences
        flavor = "Wubba lubba dub dub, time to spark chaos."
        assert _strip_franchise_sentences(flavor, self.HINT) == flavor

    def test_no_hint_no_change(self):
        from prompt_generator import _strip_franchise_sentences
        assert _strip_franchise_sentences("Rick rides.", "") == "Rick rides."

    def test_media_stopwords_not_tokens(self):
        from prompt_generator import _franchise_tokens
        assert _franchise_tokens("Studio Ghibli — watercolor") == {'ghibli'}
