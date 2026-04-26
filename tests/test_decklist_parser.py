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
