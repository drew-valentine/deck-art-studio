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


class TestFranchiseRenderLead:
    """Franchise names never reach a model-facing prompt verbatim — they are
    translated at USE time (pure function, no deck-data migration) into a
    de-named genre phrase + original-characters guard. Root cause of literal
    Ricks in card art: 'in the style of Rick & Morty' led every render."""

    def test_franchise_denamed_in_render_lead(self):
        from prompt_generator import render_style_lead
        lead = render_style_lead('Rick & Morty')
        assert 'rick' not in lead.lower() and 'morty' not in lead.lower()
        assert lead == ("in the style of an adult animated sci-fi cartoon "
                        "series, original character designs")

    def test_artist_names_pass_through(self):
        from prompt_generator import render_style_lead
        assert render_style_lead('Moebius') == 'in the style of Moebius'
        assert render_style_lead('Victo Ngai') == 'in the style of Victo Ngai'
        assert render_style_lead('ligne claire clean-line illustration') == \
            'in the style of ligne claire clean-line illustration'

    def test_other_franchises_mapped(self):
        from prompt_generator import franchise_style_phrase
        assert franchise_style_phrase('SpongeBob SquarePants') is not None
        assert franchise_style_phrase('Studio Ghibli') is not None
        assert franchise_style_phrase('surrealism') is None
        assert franchise_style_phrase('') is None

    def test_firewall_uses_original_name_not_denamed_hint(self):
        # The scene writer receives a DE-NAMED hint; the firewall must still
        # derive {rick, morty} from the original style_source name.
        from prompt_generator import _strip_franchise_sentences
        out = _strip_franchise_sentences(
            "All roads lead to Rick's garage. Fortune favors the bold.",
            "Rick & Morty")
        assert out == "Fortune favors the bold."

    def test_empty_style_source_empty_lead(self):
        from prompt_generator import render_style_lead
        assert render_style_lead('') == ''


class TestSteerOverridesReference:
    """A steer must override the reference anchor and rules wherever they
    conflict — including the subject's APPEARANCE. The scene-scoped steer
    ("change the setting, action, framing...") let the model silently discard
    'a beautiful traitorous zombie woman' and render the anchor's skeletal
    monster instead."""

    CARD = {'name': 'Glissa, the Traitor',
            'type_line': 'Legendary Creature — Phyrexian Zombie Elf',
            'oracle_text': 'First strike, deathtouch', 'flavor_text': '',
            'card_type': 'creature', 'colors': ['B', 'G']}

    def _capture(self, monkeypatch, steer):
        import sys, types
        from prompt_generator import generate_subject_with_ai
        captured = {}
        fake = types.ModuleType('mlx_llm')
        def _chat(messages=None, **kw):
            captured['system'] = messages[0]['content']
            captured['user'] = messages[1]['content']
            return 'Glissa, the Traitor, a Phyrexian Zombie Elf, strides forth.'
        fake.chat = _chat
        monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
        generate_subject_with_ai(self.CARD, backend='local', local_model='m',
                                 steer=steer)
        return captured

    def test_steer_carries_override_authority(self, monkeypatch):
        cap = self._capture(monkeypatch, 'a beautiful traitorous zombie woman')
        assert 'a beautiful traitorous zombie woman' in cap['system']
        assert 'OVERRIDES every rule above' in cap['system']
        assert 'APPEARANCE' in cap['system']
        # the old scene-only scoping that suppressed appearance steers is gone
        assert 'time, or mood as needed' not in cap['system']

    def test_steer_line_outranks_reference_in_user_msg(self, monkeypatch):
        cap = self._capture(monkeypatch, 'a beautiful traitorous zombie woman')
        assert 'OVERRIDES the reference description' in cap['user']
        # steer appears before the reference anchor it overrides
        assert cap['user'].index('User steer') < cap['user'].index('Reference description')

    def test_no_steer_no_override_block(self, monkeypatch):
        cap = self._capture(monkeypatch, '')
        assert 'USER DIRECTION' not in cap['system']
        assert 'User steer' not in cap['user']



class TestOpeningExampleIsThisCard:
    """The opening-rule example must be built from the card itself. A fixed
    example name ('Okaun, Eye of Chaos') was parroted into other cards'
    prompts across seven decks."""

    CARD = {'name': 'Palace Jailer', 'type_line': 'Creature — Human Soldier',
            'oracle_text': '', 'flavor_text': '', 'card_type': 'creature', 'colors': ['W']}

    def test_example_uses_the_cards_own_name(self, monkeypatch):
        import sys, types
        from prompt_generator import generate_subject_with_ai
        captured = {}
        fake = types.ModuleType('mlx_llm')
        def _chat(messages=None, **kw):
            captured['system'] = messages[0]['content']
            return 'Palace Jailer, a Human Soldier, stands at the gate.'
        fake.chat = _chat
        monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
        generate_subject_with_ai(self.CARD, backend='local', local_model='m')
        assert "'Palace Jailer, a Human Soldier, ...'" in captured['system']
        assert 'Okaun' not in captured['system']

    def test_backstop_replaces_a_leaked_example_name(self):
        from prompt_generator import _strip_example_leak
        out = _strip_example_leak('Okaun, Human Soldier, stands tall in the throne room.', self.CARD)
        assert out.startswith('Palace Jailer, Human Soldier')
        out2 = _strip_example_leak("Okaun, Eye of Chaos's Whispersilk Cloak floats.", {'name': 'Whispersilk Cloak'})
        assert out2.startswith("Whispersilk Cloak's Whispersilk Cloak") or out2.startswith('Whispersilk Cloak')
        # the real Okaun keeps his name
        same = _strip_example_leak('Okaun, Eye of Chaos, a Cyclops Berserker, storms in.', {'name': 'Okaun, Eye of Chaos'})
        assert same.startswith('Okaun, Eye of Chaos')

    def test_non_creature_example(self):
        from prompt_generator import _opening_example
        assert _opening_example({'name': 'Maze of Ith', 'type_line': 'Land', 'card_type': 'land'}) == 'Maze of Ith, ...'



class TestChatPreambleStripped:
    def test_preamble_removed(self):
        from prompt_generator import _strip_chat_preamble
        assert _strip_chat_preamble("Here's a rewritten description for Bountiful Landscape:\n\nA weathered dock juts out.") == 'A weathered dock juts out.'
        assert _strip_chat_preamble("Sure! Here is the scene:\nKeiga soars.") == 'Keiga soars.'
        assert _strip_chat_preamble("```\nKeiga soars above the waves.\n```") == 'Keiga soars above the waves.'

    def test_normal_prompt_untouched(self):
        from prompt_generator import _strip_chat_preamble
        p = 'Keiga, the Tide Star, a Dragon Spirit, soars above the waves: foam and spray everywhere.'
        assert _strip_chat_preamble(p) == p


# ── H11: composition discipline ─────────────────────────────────────────────

def test_limit_scene_sentences_keeps_three():
    from prompt_generator import _limit_scene_sentences
    txt = ("A dragon rears over the waves. Lightning splits the sky behind it. "
           "A shark leaps beside it! Fish scatter.")
    assert _limit_scene_sentences(txt) == "A dragon rears over the waves. Lightning splits the sky behind it. A shark leaps beside it!"
    assert _limit_scene_sentences(txt, 2) == "A dragon rears over the waves. Lightning splits the sky behind it."
    assert _limit_scene_sentences("One sentence only.") == "One sentence only."
    assert _limit_scene_sentences("") == ""


def test_scene_writer_prompt_has_composition_rule():
    import inspect, prompt_generator as pg
    src = inspect.getsource(pg)
    assert 'ONE focal subject, ONE setting, ONE action' in src


def test_hint_without_palette_strips_hue_list():
    from prompt_generator import hint_without_palette
    blk = ("cel animation, thick black outlines, palette of bright yellow, vivid orange, "
           "desaturated green, dusty coral, bold lines, wobbly eyes, flat shading")
    out = hint_without_palette(blk)
    assert 'palette' not in out and 'coral' not in out and 'yellow' not in out
    assert out.startswith('cel animation, thick black outlines')
    assert 'wobbly eyes' in out and 'flat shading' in out
    assert hint_without_palette('') == ''


def test_writer_system_prompt_carries_staging(monkeypatch):
    import sys, types
    import prompt_generator as pg
    seen = {}
    def chat(messages, **kw):
        seen['sys'] = messages[0]['content']; return "A signet ring sits on a bench. It glows."
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Arcane Signet', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                style_hint='an adult animated sci-fi cartoon series — cel animation',
                                staging='Scenes are staged in cluttered garages. The register is deadpan absurd.')
    assert 'STAGING AND REGISTER' in seen['sys'] and 'deadpan absurd' in seen['sys']
    assert 'calm, artful film still' not in seen['sys']


def test_scene_writer_prompt_treats_zones_as_game_terms():
    import inspect, prompt_generator as pg
    src = inspect.getsource(pg.generate_subject_with_ai)
    assert "game ZONES, not places" in src


def test_writer_omits_rules_text_for_noncreatures(monkeypatch):
    import sys, types
    import prompt_generator as pg
    seen = {}
    def chat(messages, **kw):
        seen['user'] = messages[1]['content']; return "A storm of dragons breaks the gate. Sunlight."
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    ench = {'name': 'Breaching Dragonstorm', 'type_line': 'Enchantment', 'card_type': 'enchantment',
            'oracle_text': 'exile cards from the top of your library until you exile a nonland card.'}
    pg.generate_subject_with_ai(ench, None, backend='local', local_model='m')
    assert 'library' not in seen['user']
    crt = {'name': 'Keiga, the Tide Star', 'type_line': 'Legendary Creature — Dragon Spirit',
           'card_type': 'creature', 'oracle_text': 'Flying', 'subtypes': ['Dragon', 'Spirit']}
    pg.generate_subject_with_ai(crt, None, backend='local', local_model='m')
    assert 'Rules: Flying' in seen['user']


def test_writer_puts_figure_idiom_on_creatures_only(monkeypatch):
    import sys, types
    import prompt_generator as pg
    seen = {}
    def chat(messages, **kw):
        seen['user'] = messages[1]['content']; return "Keiga, a Dragon Spirit, lifts off. Mist."
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    crt = {'name': 'Keiga, the Tide Star', 'type_line': 'Legendary Creature — Dragon Spirit',
           'card_type': 'creature', 'oracle_text': 'Flying', 'subtypes': ['Dragon', 'Spirit']}
    pg.generate_subject_with_ai(crt, None, backend='local', local_model='m',
                                figure_idiom='bulging mismatched eyes, lumpy anatomy')
    assert 'Figure idiom' in seen['user'] and 'lumpy anatomy' in seen['user']
    land = {'name': 'Command Tower', 'type_line': 'Land', 'card_type': 'land', 'oracle_text': ''}
    pg.generate_subject_with_ai(land, None, backend='local', local_model='m',
                                figure_idiom='bulging mismatched eyes, lumpy anatomy')
    assert 'Figure idiom' not in seen['user']


def test_render_style_lead_prefers_lineage_for_franchises():
    from prompt_generator import render_style_lead
    assert render_style_lead('Rick and Morty') == 'in the style of an adult animated sci-fi cartoon series, original character designs'
    assert render_style_lead('Rick and Morty', lineage='late-night adult animation on a cable comedy network') == \
        'in the style of late-night adult animation on a cable comedy network, original character designs'
    assert render_style_lead('Moebius', lineage='ignored for artists') == 'in the style of Moebius'


def test_franchise_phrase_uses_recalled_kind_over_table():
    from prompt_generator import franchise_style_phrase, render_style_lead, _GENERIC_FRANCHISE_PHRASE
    # a brand-new show not in any table: the recalled kind de-names it
    assert franchise_style_phrase('Smiling Friends', kind='franchise') == _GENERIC_FRANCHISE_PHRASE
    assert render_style_lead('Smiling Friends', kind='franchise').startswith('in the style of an animated series')
    # an artist whose name happens to contain a table keyword passes verbatim
    assert franchise_style_phrase('Marvel Kowalski', kind='artist') is None
    assert render_style_lead('Marvel Kowalski', kind='artist') == 'in the style of Marvel Kowalski'
    # no kind stored: the table is the fallback
    assert franchise_style_phrase('Rick and Morty') == 'an adult animated sci-fi cartoon series'
    assert franchise_style_phrase('Moebius') is None


def test_writer_retries_once_when_the_draft_buries_the_subject(monkeypatch):
    import sys, types
    import prompt_generator as pg
    replies = iter(["Ink flows from a delicate quill held by a nearby scribe, as the ring glows. Quiet.",
                    "Sol Ring, a golden ring, rests on a papyrus sheet. A scribe's quill lies beside it."])
    calls = []
    def chat(messages, **kw):
        calls.append(len(messages)); return next(replies)
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Sol Ring', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m')
    assert out.startswith('Sol Ring') and calls == [2, 4]
    # a good first draft is not retried
    replies2 = iter(["Sol Ring, a golden ring, glows on an altar. Dust hangs in the light."])
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda messages, **kw: next(replies2)))
    assert pg.generate_subject_with_ai(card, None, backend='local', local_model='m').startswith('Sol Ring')
    assert pg._opens_with_subject("Keiga, the Tide Star, a Dragon Spirit, soars.", {'name': 'Keiga, the Tide Star', 'card_type': 'creature'})
    assert not pg._opens_with_subject("A storm gathers over the sea. Keiga appears.", {'name': 'Keiga, the Tide Star', 'card_type': 'creature'})


def test_literal_name_words_splits_coined_compounds():
    from prompt_generator import literal_name_words, _split_compound
    d = {'dragon', 'storm', 'breaching', 'chance', 'encounter', 'wolf', 'fire'}
    assert literal_name_words('Breaching Dragonstorm', d) == ['breaching', 'dragon', 'storm']
    assert literal_name_words('Chance Encounter', d) == ['chance', 'encounter']
    assert _split_compound('Wolfire', d) == ['Wolfire']          # too short to split (< 8)
    assert _split_compound('dragonstorm', set()) == ['dragonstorm']   # no dictionary: no-op


def test_writer_never_returns_an_empty_prompt(monkeypatch):
    import sys, types
    import prompt_generator as pg
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda messages, **kw: "Arcane Signet. "))
    card = {'name': 'Arcane Signet', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m')
    assert len(out.split()) >= 5 and 'signet' in out.lower()


def test_scene_check_rerolls_an_invented_subject(monkeypatch):
    import sys, types
    import prompt_generator as pg
    monkeypatch.setenv('SCENE_CHECK', '1')
    replies = iter([
        "A signet ring sits on a desk. A golden chair nestles in the ring's center.",   # draft
        "Fails: a chair competes for focus.",                                           # check 1
        "A signet ring sits alone on a desk. Lantern light glints on its band.",         # re-roll
        "OK",                                                                             # check 2
    ])
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda messages, **kw: next(replies)))
    card = {'name': 'Arcane Signet', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m')
    assert out.startswith('A signet ring sits alone')
    # a clean draft passes straight through with a single check
    replies2 = iter(["A signet ring rests on an altar. Candles flicker.", "OK"])
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda messages, **kw: next(replies2)))
    assert pg.generate_subject_with_ai(card, None, backend='local', local_model='m').startswith('A signet ring rests')


def test_franchise_stripping_only_uses_a_franchise_name(monkeypatch):
    import sys, types
    import prompt_generator as pg
    draft = ("Krark's Thumb, a severed goblin thumb, sits in deep shadow as smoke curls past. "
             "A bold shaft of light finds it.")
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda messages, **kw: draft))
    card = {'name': "Krark's Thumb", 'type_line': 'Legendary Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    # unnamed deck: the style block is the hint; nothing may be stripped
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                      style_hint='comic book art, bold ink outlines, swirling smoke, deep shadows')
    assert out.startswith("Krark's Thumb") and 'smoke' in out and 'bold' in out
    # artist deck: name words ("hand", "drawn") are not cast tokens
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                      style_hint='Dr. Seuss hand drawn illustration — ink illustration',
                                      style_source_name='Dr. Seuss hand drawn illustration', style_source_kind='artist')
    assert out.startswith("Krark's Thumb") and 'shadow' in out
    # franchise deck: a sentence naming the cast IS stripped
    draft2 = "Krark's Thumb sits on a bench. Rick grabs it from the shelf."
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda messages, **kw: draft2))
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                      style_source_name='Rick and Morty', style_source_kind='franchise')
    assert 'Rick grabs' not in out and out.startswith("Krark's Thumb")


def test_writer_describes_light_in_the_medium(monkeypatch):
    import sys, types
    import prompt_generator as pg
    seen = {}
    def chat(messages, **kw):
        seen['user'] = messages[1]['content']; return "Sol Ring, a gold ring, glows on an altar. Flat shadow. Dust."
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Sol Ring', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                style_hint='in the style of X — painted illustration, flat opaque paint, hand-painted texture')
    assert 'is FLAT: no rendered light' in seen['user']          # flat opaque paint counts as flat
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                style_hint='fine-line ink illustration, loose expressive hand-drawn linework')
    assert 'is FLAT: no rendered light' in seen['user']
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                style_hint='oil painting, visible brushstrokes, painterly texture')
    assert 'painted light' in seen['user']
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m', style_hint='')
    assert 'Light in this medium' not in seen['user']


def test_strip_unpaintable_removes_abstractions():
    from prompt_generator import _strip_unpaintable
    assert _strip_unpaintable("A ring glows, as if the very thought of hunger has become a cruel joke. Dust settles, a testament to its power.") == \
        "A ring glows. Dust settles."
    assert _strip_unpaintable("Keiga rises from the sea, seeming to shrug off the storm, spray flying.") == "Keiga rises from the sea, spray flying."
    assert _strip_unpaintable("") == ""


def test_dangling_tail_and_invented_cyclops():
    from prompt_generator import _fix_dangling_tail, _fix_invented_cyclops
    assert _fix_dangling_tail("A ring lies on a cushion, its gemstone radiating a warm light that.") == "A ring lies on a cushion, its gemstone radiating a warm light."
    assert _fix_dangling_tail("Keiga rises. Spray flies and.") == "Keiga rises. Spray flies."
    assert _fix_dangling_tail("Keiga rises from the sea.") == "Keiga rises from the sea."
    assert _fix_invented_cyclops("Alela sits, her single, piercing emerald eye shimmering.", "a faerie warlock") == \
        "Alela sits, her piercing emerald eyes shimmering."
    assert _fix_invented_cyclops("Okaun glares with his single eye.", "Okaun, a cyclops with one eye") == "Okaun glares with his single eye."
