"""A photographed or rendered style must never carry drawing vocabulary, keeps
the hues its reads agree on, and a posed register stills the scene grammar."""
import re

import deck_studio as ds
import prompt_generator as pg
import vision_analyzer as va


def test_unsaturated_light_red_is_pink():
    assert va._hue_name(5.0, 0.3, 0.7) == 'dusty pink'      # pink film set walls
    assert va._hue_name(5.0, 0.3, 0.5) == 'dusty red'       # a dark brick stays red
    assert va._hue_name(5.0, 0.25, 0.85) == 'pale pink'


def test_coverage_phrase_speaks_the_medium():
    muted = {'paper': 0.02, 'saturation': 0.3}
    assert va.pixel_coverage_phrase(muted, 'photograph') == 'full colour, soft muted tones'
    assert va.pixel_coverage_phrase(muted, 'ink illustration').startswith('fully coloured with soft muted fills')
    assert va.pixel_coverage_phrase({'paper': 0.0, 'saturation': 0.5}, '3d render') == 'full colour, rich saturated tones'
    mono = {'paper': 0.1, 'saturation': 0.05}
    assert va.pixel_coverage_phrase(mono, 'photograph') == 'monochrome black and white'
    assert 'paper' in va.pixel_coverage_phrase(mono, 'comic book')
    for medium in ('photograph', '3d render'):
        for st in (muted, mono, {'paper': 0.5, 'saturation': 0.3}):
            assert not va.drawing_vocabulary(va.pixel_coverage_phrase(st, medium))


def test_non_drawn_medium_detection():
    assert va.is_non_drawn_medium('photograph')
    assert va.is_non_drawn_medium('cinematic photograph, Wes Anderson Film, photographic lighting')
    assert va.is_non_drawn_medium('3d render')
    assert not va.is_non_drawn_medium('comic book')
    assert not va.is_non_drawn_medium('painted illustration')


def test_drawing_vocabulary_items():
    assert va.drawing_vocabulary('precise ruler-straight lines')
    assert va.drawing_vocabulary('clean lines')
    assert va.drawing_vocabulary('no bare white paper')
    assert va.drawing_vocabulary('visible brushwork')
    assert not va.drawing_vocabulary('meticulous symmetrical composition')
    assert not va.drawing_vocabulary('highly detailed miniature sets')
    assert not va.drawing_vocabulary('shallow depth of field')
    assert not va.drawing_vocabulary('often with the horizon line at an unusual position')
    assert not va.drawing_vocabulary('composition with close-up shots that fill the frame')
    assert not va.drawing_vocabulary('full colour, soft muted tones')


def test_read_majority_hues():
    stored = ("Source: X\nColors: Pink, white, blue, brown\n"
              "Source: X\nColors: Pink, black, white, gray\n"
              "Source: X\nColors: Yellow, Blue, Pink\n"
              "Source: X\nColors: Warm, muted tones, sepia hues\n")
    assert va._read_majority_hues(stored) == ['pink', 'blue']
    assert va._read_majority_hues("Colors: pink\n") == []       # one read is not a majority


def test_render_lead_coverage_clause_accepts_photographic_phrases():
    out = ds._assemble_flux_prompt(
        ['in the style of X', 'cinematic photograph, full colour, soft muted tones, palette of pink, blue'],
        'A badger, plain. It stands in a hall.', card_type='creature')
    head = out.split('. ')[0]
    assert 'full colour, soft muted tones' in head
    assert 'A badger' not in head


def test_idiom_verb_follows_the_medium():
    assert ds._idiom_verb('cinematic photograph, photographic lighting') == 'staged with'
    assert ds._idiom_verb('3D render, volumetric lighting') == 'rendered with'
    assert ds._idiom_verb('painterly digital painting, soft edges') == 'painted with'
    assert ds._idiom_verb('comic book art, bold ink outlines') == 'drawn with'
    out = ds._with_figure_idiom('Keiga, a Dragon, rises. Sea behind.', ['wide eyes'], verb='staged with')
    assert out.startswith('Keiga, a Dragon, rises, staged with wide eyes.')


def test_posed_register_from_staging_and_idiom():
    assert pg._posed_register('Scenes are staged frontally.', '')
    assert pg._posed_register('', 'meticulous symmetrical composition, miniature sets')
    assert not pg._posed_register('Scenes are staged with a wide-angle lens, the horizon low.', 'soft glow')


def test_composition_line_and_figure_items_split():
    idiom = 'meticulous symmetrical composition, exaggerated facial expressions, highly detailed miniature sets'
    assert pg._figure_idiom_items(idiom) == ['exaggerated facial expressions']
    line = pg._composition_line(idiom)
    assert line.startswith('Composition (REQUIRED)')
    assert 'symmetrical composition' in line
    assert pg._composition_line('soft glow, wobbly anatomy') == ''
    assert 'yields to the USER DIRECTION' in pg._composition_line(idiom, steer_present=True)


def test_scene_score_is_verb_neutral_when_posed():
    card = {'name': 'Badger', 'card_type': 'creature'}
    still = 'Badger, a Badger Druid, stands facing the camera in a pink hall, blue tiles beneath.'
    action = 'Badger, a Badger Druid, lunges hard far across the wide pink hall, blue tiles beneath.'
    assert pg._scene_score(action, card) > pg._scene_score(still, card)
    assert pg._scene_score(action, card, posed=True) == pg._scene_score(still, card, posed=True)


def test_setting_line_carries_the_styles_world():
    world = 'Places are built indoor and outdoor sets with pastel painted walls and a designed look.'
    line = pg._setting_line(False, False, 'creature', 'Scenes are staged with the horizon low. The tone is calm.', world)
    assert "the place is one of this style's own — built indoor and outdoor sets" in line
    assert 'Places are' not in line
    assert 'places the subject somewhere specific' in line
    obj = pg._setting_line(False, True, 'artifact', '', world)
    assert obj.startswith('Setting (REQUIRED, yields to the USER DIRECTION above): The place is one of this style')
    assert 'never presented for display' in obj
    assert pg._setting_line(False, False, 'creature', '', '') == pg._setting_line(False, False, 'creature', '', None)


def test_slogans_are_lettering():
    out = pg._tidy_prompt('Garruk stands in a metal silo, its surface covered in rusty stains and faded slogans, the doors shudder.')
    assert 'slogan' not in out.lower()
    assert 'metal silo' in out
    assert ds._DOCUMENT_RE.search('a wall of faded slogans')


def test_lighting_key_by_majority():
    stored = ("- Shading/Lighting: Subtle, warm tones with a soft, diffused light.\n"
              "- Shading/Lighting: Flat graphic space with minimal shading and lighting\n"
              "- Shading/Lighting: Flat, even lighting with no strong shadows or highlights.\n"
              "- Shading/Lighting: Depth through shadows and highlights on stripes\n")
    assert va.lighting_key(stored) == 'even'
    hard = "- Shading/Lighting: dramatic chiaroscuro, deep shadows\n- Lighting: hard rim light\n"
    assert va.lighting_key(hard) == 'dramatic'
    assert va.lighting_key('Colors: red\n') == ''
    assert va.lighting_key("- Lighting: soft diffused\n- Lighting: hard directional\n") == ''


def test_world_reads_merge_by_category_majority():
    reads = ["built, outdoors, glass, metal, orange, 1960s, artificial",
             "built, outdoors, stone, pink, 19th century, artificial",
             "built, indoors, walls and ground made of painted surfaces, black and white, modern, highly designed",
             "natural, outdoors, water, blue, modern, artificial",
             "built, indoors, painted wood, pink, modern, designed",
             "built, indoors, painted surfaces, pink and black, modern, designed"]
    out = va.merge_world_reads(reads)
    assert out.startswith('built')
    assert 'indoors' not in out                                 # 3 of 6 is not a strict majority
    assert 'pink' in out and 'designed and artificial' in out
    assert 'modern' not in out and 'metal' not in out            # no majority, no ambiguous era
    assert 'lighthouse' not in out and ' or ' not in out
    assert va.merge_world_reads([]) == ''
    one = va.merge_world_reads(["built, indoors, stone, pink, victorian, artificial"])
    assert one == 'built, indoors, surfaces made of stone, in pink, victorian, designed and artificial'


def test_world_merge_strict_majority_and_drawn_media():
    reads = ["built, indoors, wood, brown, modern, artificial",
             "natural, outdoors, clouds, green and blue, modern, artificial",
             "built, outdoors, rock, red, steampunk, artificial",
             "built, indoors, metal, purple, futuristic, artificial"]
    out = va.merge_world_reads(reads, non_drawn=False)
    assert out.startswith('built')
    assert 'indoors' not in out and 'outdoors' not in out      # 2 of 4 is not a majority
    assert 'artificial' not in out                              # meaningless for a drawing
    assert va.merge_world_reads(["natural, outdoors, paper, paper, 1950s, hand-drawn"]) == ''
    assert 'designed and artificial' in va.merge_world_reads(["built, indoors, stone, pink, artificial"] * 3, non_drawn=True)


def test_land_setting_keeps_only_surfaces_and_colours_of_the_world():
    line = pg._setting_line(False, False, 'land', '', 'built, indoors, surfaces made of stone, in pink, white, designed and artificial')
    assert 'indoors' not in line and 'built' not in line
    assert 'surfaces made of stone, in pink, white, designed and artificial' in line
    assert "this style's own" not in pg._setting_line(False, False, 'land', '', 'built, indoors, artificial')


def test_world_sentence_and_cartoon_words():
    s = pg._world_sentence('built, indoors, in pink, white, designed and artificial', 'creature')
    assert s == 'The place around it is built and indoors, in pink, white, designed and artificial.'
    assert pg._world_sentence('built, indoors, in pink', 'land') == 'The place around it is in pink.'
    assert pg._world_sentence('', 'creature') == ''
    assert va.drawing_vocabulary('wobbly edges')
    assert va.drawing_vocabulary('cartoonish proportions')
    assert not va.drawing_vocabulary('exaggerated facial expressions')


def test_compound_literal_object_without_a_system_dictionary(monkeypatch):
    monkeypatch.setattr(pg, '_DICT_WORDS', set())        # the CI runner has no /usr/share/dict/words
    assert pg._literal_object_from_name('Shadowspear') == 'a spear'
    assert pg._literal_object_from_name('Sol Ring') == pg._LITERAL_OBJECT_NOUNS['ring']
    assert pg._literal_object_from_name('Beast Within') is None


def test_render_prompt_never_names_calligraphy(tmp_path):
    out = ds._assemble_flux_prompt(['in the style of X', 'ink'], 'A ring. On a table.', card_type='artifact')
    assert 'calligraphy' not in out and 'seal' not in out      # naming them summons them (3/4 vs 1/4)


def test_writing_rerolls_without_references(monkeypatch, tmp_path):
    import deck_studio as ds2
    import vision_analyzer as va
    raw = tmp_path / 'raw_art'; raw.mkdir()
    for slug in ('ponder', 'sol_ring'):
        (raw / f'{slug}.png').write_bytes(b'x')
    cards = [{'name': 'Ponder', 'card_type': 'sorcery'}, {'name': 'Sol Ring', 'card_type': 'artifact'}]
    ctx = {'cards': cards, 'raw_art_dir': raw, 'deck_name': 'D'}
    verdicts = {'Ponder': ['writing'], 'Sol Ring': ['text']}
    monkeypatch.setattr(va, 'inspect_render', lambda path, name, ctype, vm, advisory=None, subject_hint='', flies=None, limbless=None: verdicts[name])
    monkeypatch.setattr(ds2, 'has_second_art_face', lambda c: False)
    monkeypatch.setattr(ds2, '_ollama_work_start', lambda: None)
    monkeypatch.setattr(ds2, '_ollama_work_done', lambda: None)
    monkeypatch.setattr(ds2.backend_config, 'load_config', lambda: {'ollama_vision_model': 'v'})
    queued = []
    monkeypatch.setattr(ds2, '_enqueue_art', lambda deck_id, name, **kw: queued.append((name, kw.get('no_references'))))
    monkeypatch.setattr(ds2, '_enqueue_inspection', lambda deck_id, names, final=False, label=None: None)
    job = ds2.Job(type=ds2.INSPECT, deck_id='d', card_name='', params={'card_names': ['Ponder', 'Sol Ring'], 'final': False})
    ds2._execute_inspect_job(job, ctx)
    assert ('Ponder', True) in queued and ('Sol Ring', False) in queued


def test_enqueue_art_carries_the_no_references_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(ds.gen_queue, 'enqueue', lambda job: seen.append(job) or job)
    ds._enqueue_art('d', 'Ponder', deck_name='D', no_references=True)
    ds._enqueue_art('d', 'Ponder', deck_name='D', seed=7)
    assert seen[0].params == {'no_references': True}
    assert seen[1].params == {'seed': 7}
