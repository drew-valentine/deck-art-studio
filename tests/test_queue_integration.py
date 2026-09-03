"""Integration tests: endpoints enqueue jobs; executor is deck-scoped."""

import json

import pytest
from PIL import Image

import deck_studio as ds


@pytest.fixture(autouse=True)
def _clear_queue():
    ds.gen_queue.clear_completed()
    # cancel anything left queued so tests don't leak jobs into each other
    for j in ds.gen_queue._snapshot_jobs():
        ds.gen_queue.cancel(j.id)
    ds.gen_queue.clear_completed()
    yield
    for j in ds.gen_queue._snapshot_jobs():
        ds.gen_queue.cancel(j.id)
    ds.gen_queue.clear_completed()


class TestEnqueueEndpoints:
    def test_generate_enqueues_art_job(self, client, populated_state):
        name = ds.cards_db[0]['name']
        resp = client.post('/api/generate', json={'card_name': name})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['queued'] == 1
        job = ds.gen_queue.get(data['job_ids'][0])
        assert job.type == ds.ART and job.card_name == name
        assert job.deck_id == ds.active_deck_id
        assert job.status == 'queued'   # worker isn't running in tests

    def test_generate_unknown_card_404(self, client, populated_state):
        resp = client.post('/api/generate', json={'card_name': 'Nope McNope'})
        assert resp.status_code == 404

    def test_batch_enqueues_one_per_card(self, client, populated_state):
        names = [c['name'] for c in ds.cards_db[:2]]
        resp = client.post('/api/generate-batch',
                           json={'card_names': names, 'skip_existing': False})
        data = resp.get_json()
        assert data['queued'] == 2
        for jid in data['job_ids']:
            assert ds.gen_queue.get(jid).type == ds.ART

    def test_status_exposes_queue_and_overlays_badges(self, client, populated_state):
        name = ds.cards_db[0]['name']
        client.post('/api/generate', json={'card_name': name})
        data = client.get('/api/status').get_json()
        assert 'queue' in data
        assert data['queue']['counts']['queued'] >= 1
        # the queued card's badge is overlaid as 'queued'
        assert data['statuses'][name]['status'] == 'queued'


class TestQueueManagementApi:
    def _enqueue(self, client, populated_state):
        name = ds.cards_db[0]['name']
        return client.post('/api/generate', json={'card_name': name}).get_json()['job_ids'][0]

    def test_cancel_endpoint(self, client, populated_state):
        jid = self._enqueue(client, populated_state)
        assert client.post(f'/api/queue/{jid}/cancel').get_json()['success'] is True
        assert ds.gen_queue.get(jid).status == 'cancelled'

    def test_bump_endpoint(self, client, populated_state):
        names = [c['name'] for c in ds.cards_db[:2]]
        ids = client.post('/api/generate-batch',
                          json={'card_names': names, 'skip_existing': False}
                          ).get_json()['job_ids']
        assert client.post(f'/api/queue/{ids[1]}/bump').get_json()['success'] is True
        with ds.gen_queue._lock:
            assert ds.gen_queue._next_locked().id == ids[1]

    def test_pause_resume(self, client):
        assert client.post('/api/queue/pause').get_json()['paused'] is True
        assert ds.gen_queue.paused is True
        assert client.post('/api/queue/resume').get_json()['paused'] is False
        assert ds.gen_queue.paused is False

    def test_clear_completed(self, client, populated_state):
        jid = self._enqueue(client, populated_state)
        ds.gen_queue.cancel(jid)
        assert client.post('/api/queue/clear-completed').get_json()['removed'] >= 1


class TestCancelSingleFixes:
    """Regressions for the cross-deck cancel bugs (findings 2 & 3)."""

    def test_cancel_single_cancels_the_queue_job(self, client, populated_state):
        # /api/cancel-single must actually drop the underlying queue job, not
        # merely flip a badge the status overlay would then revert.
        name = ds.cards_db[0]['name']
        jid = client.post('/api/generate', json={'card_name': name}
                          ).get_json()['job_ids'][0]
        assert ds.gen_queue.get(jid).status == 'queued'
        client.post('/api/cancel-single', json={'card_name': name})
        assert ds.gen_queue.get(jid).status == 'cancelled'

    def test_cancel_flag_is_deck_scoped(self, client, populated_state):
        # Cancelling the active deck's card flags only THAT (deck, card) — a
        # same-named card on another deck is untouched.
        client.post('/api/cancel-single', json={'card_name': 'Sol Ring'})
        assert ds._is_cancel_flagged(ds.active_deck_id, 'Sol Ring')
        assert not ds._is_cancel_flagged('some-other-deck', 'Sol Ring')


class TestExecutorDeckScoping:
    def test_prompt_executor_writes_only_its_deck(self, tmp_path, monkeypatch):
        # Two decks on disk; run a PROMPT job for deck B while deck A is active.
        decks = tmp_path / 'decks'
        for did, cardname in (('deckA', 'Sol Ring'), ('deckB', 'Mox Ruby')):
            d = decks / did
            (d).mkdir(parents=True)
            (d / 'deck.json').write_text(json.dumps({
                'name': did, 'cards': [{'name': cardname, 'type_line': 'Artifact',
                                        'oracle_text': '', 'colors': []}]}))
            (d / 'art_prompts.json').write_text(json.dumps([]))
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        monkeypatch.setattr(ds, 'active_deck_id', 'deckA')

        # Force rule-based prompt (no LLM) for determinism.
        import backend_config
        monkeypatch.setattr(backend_config, 'load_config',
                            lambda: {'llm_backend': 'none', 'ollama_model': ''})
        monkeypatch.setattr(ds, 'openai_client', None)

        job = ds.Job(type=ds.PROMPT, deck_id='deckB', card_name='Mox Ruby',
                     use_ai=False)
        ds._execute_job(job)   # run synchronously

        # deck B's art_prompts.json got the prompt; deck A's is untouched.
        b = json.loads((decks / 'deckB' / 'art_prompts.json').read_text())
        a = json.loads((decks / 'deckA' / 'art_prompts.json').read_text())
        assert any(e['name'] == 'Mox Ruby' and e['prompt'] for e in b)
        assert a == []


class TestAnalysisJobs:
    """Inspiration/style analysis is a first-class queue job: deck-scoped,
    deck-switch-proof, and never polluting unrelated decks' UI progress."""

    def test_distill_style_enqueues(self, client, tmp_path, monkeypatch):
        decks = tmp_path / 'decks'
        d = decks / 'deckX'; d.mkdir(parents=True)
        (d / 'deck.json').write_text(json.dumps({
            'name': 'X', 'cards': [],
            'inspiration_images': [{'filename': 'i.png',
                                    'style_description': 'Colors: teal'}]}))
        (d / 'i.png').write_bytes(b'x')
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        resp = client.post('/api/decks/deckX/distill-style')
        assert resp.status_code == 200
        jobs = [j for j in ds.gen_queue._snapshot_jobs() if j.type == ds.ANALYZE]
        assert jobs and jobs[-1].deck_id == 'deckX'
        assert jobs[-1].params['mode'] == 'distill'

    def test_status_overlay_skips_analysis_jobs(self, client, populated_state):
        job = ds.gen_queue.enqueue(ds.Job(
            type=ds.ANALYZE, deck_id=ds.active_deck_id, card_name='',
            label='Style analysis', params={'mode': 'distill'}))
        data = client.get('/api/status').get_json()
        assert '' not in data['statuses']          # no phantom card badge
        assert data['queue']['counts']['queued'] >= 1
        ds.gen_queue.cancel(job.id)

    def test_progress_never_pollutes_other_decks(self, populated_state):
        # A running analysis for an INACTIVE deck must not appear in the
        # active deck's progress (which drives the UI).
        ds.style_analysis_progress.clear()
        job = ds.Job(type=ds.ANALYZE, deck_id='some-other-deck', card_name='',
                     params={'mode': 'distill'})
        ds._analysis_job_ctx.job = job
        try:
            ds._style_progress_update('analyzing', 1, 5, 'working...')
            assert ds._style_progress_for_active() == {}     # active deck clean
            assert job.progress['message'] == 'working...'   # job carries it
            ds._style_progress_clear()
            assert ds.style_analysis_progress == {}
        finally:
            ds._analysis_job_ctx.job = None

    def test_progress_mirrors_for_active_deck(self, populated_state):
        ds.style_analysis_progress.clear()
        job = ds.Job(type=ds.ANALYZE, deck_id=ds.active_deck_id, card_name='',
                     params={'mode': 'distill'})
        ds._analysis_job_ctx.job = job
        try:
            ds._style_progress_update('analyzing', 2, 5, 'active work')
            assert ds._style_progress_for_active().get('message') == 'active work'
        finally:
            ds._analysis_job_ctx.job = None
            ds.style_analysis_progress.clear()

    def test_deck_switch_mid_analysis_leaves_no_stale_progress(self, populated_state, monkeypatch):
        # The observed bug: analysis started on deck A (active), the user
        # switched to deck B before it finished, and A's last mirrored step
        # ("Analyzing image 2/2... 1/7") was served forever on switching back.
        ds.style_analysis_progress.clear()
        deck_a = ds.active_deck_id
        job = ds.Job(type=ds.ANALYZE, deck_id=deck_a, card_name='',
                     params={'mode': 'reanalyze'})
        ds._analysis_job_ctx.job = job
        try:
            ds._style_progress_update('analyzing', 1, 7, 'Analyzing image 2/2...')
            monkeypatch.setattr(ds, 'active_deck_id', 'deck-b')   # user switches
            assert ds._style_progress_for_active() == {}          # B shows idle
            ds._style_progress_update('distilling', 5, 7, 'Distilling...')
            ds._style_progress_clear()                            # job finishes
            monkeypatch.setattr(ds, 'active_deck_id', deck_a)     # user returns
            assert ds._style_progress_for_active() == {}          # no stale entry
        finally:
            ds._analysis_job_ctx.job = None
            ds.style_analysis_progress.clear()

    def test_deck_delete_cancels_analysis_jobs(self, client, populated_state):
        job = ds.gen_queue.enqueue(ds.Job(
            type=ds.ANALYZE, deck_id='doomed-deck', card_name='',
            params={'mode': 'reanalyze'}))
        ds.gen_queue.cancel_deck('doomed-deck')
        assert ds.gen_queue.get(job.id).status == 'cancelled'


class TestRegistryReconcile:
    """decks.json is a cache of the filesystem: decks on disk that it does not
    list are re-added on load (14 decks once vanished from the dropdown with
    every file intact)."""

    def test_missing_disk_decks_are_readded(self, tmp_path, monkeypatch):
        decks = tmp_path / 'decks'
        for did, name in (('reg-deck', 'Registered'), ('lost-deck', 'Lost Deck')):
            (decks / did).mkdir(parents=True)
            (decks / did / 'deck.json').write_text(json.dumps({'name': name, 'cards': []}))
        (decks / 'junk').mkdir()                          # no deck.json -> ignored
        (decks / 'decks.json').write_text(json.dumps({'decks': [{'id': 'reg-deck', 'name': 'Registered', 'created': 'x'}], 'active': 'reg-deck'}))
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        monkeypatch.setattr(ds, 'DECK_REGISTRY_PATH', decks / 'decks.json')
        reg = ds._load_deck_registry()
        ids = [d['id'] for d in reg['decks']]
        assert ids == ['reg-deck', 'lost-deck']
        assert next(d for d in reg['decks'] if d['id'] == 'lost-deck')['name'] == 'Lost Deck'
        assert reg['active'] == 'reg-deck'
        # persisted, so the next load is a no-op
        saved = json.loads((decks / 'decks.json').read_text())
        assert [d['id'] for d in saved['decks']] == ['reg-deck', 'lost-deck']

    def test_entries_without_a_directory_are_pruned(self, tmp_path, monkeypatch):
        decks = tmp_path / 'decks'; (decks / 'real').mkdir(parents=True)
        (decks / 'real' / 'deck.json').write_text(json.dumps({'name': 'Real', 'cards': []}))
        (decks / 'decks.json').write_text(json.dumps({'decks': [
            {'id': 'real', 'name': 'Real', 'created': 'x'}, {'id': 'ghost', 'name': 'Ghost', 'created': 'x'}],
            'active': 'ghost'}))
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        monkeypatch.setattr(ds, 'DECK_REGISTRY_PATH', decks / 'decks.json')
        reg = ds._load_deck_registry()
        assert [d['id'] for d in reg['decks']] == ['real']
        assert reg['active'] == 'real'              # active moved off the ghost

    def test_corrupt_registry_rebuilt_from_disk(self, tmp_path, monkeypatch):
        decks = tmp_path / 'decks'; (decks / 'only').mkdir(parents=True)
        (decks / 'only' / 'deck.json').write_text(json.dumps({'name': 'Only', 'cards': []}))
        (decks / 'decks.json').write_text('{not json')
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        monkeypatch.setattr(ds, 'DECK_REGISTRY_PATH', decks / 'decks.json')
        reg = ds._load_deck_registry()
        assert [d['id'] for d in reg['decks']] == ['only']


def test_front_face_unit_drops_the_back_face_name():
    import deck_studio as ds
    card = {'name': 'Aclazotz, Deepest Betrayal // Temple of the Dead',
            'type_line': 'Legendary Creature — Bat God // Land',
            'oracle_text': 'Flying // {T}: Add {B}.', 'card_type': 'creature',
            'card_faces': [
                {'name': 'Aclazotz, Deepest Betrayal', 'type_line': 'Legendary Creature — Bat God',
                 'oracle_text': 'Flying, deathtouch', 'flavor_text': ''},
                {'name': 'Temple of the Dead', 'type_line': 'Land', 'oracle_text': '{T}: Add {B}.'}]}
    unit = ds._face_unit_for(card, card['name'])
    assert unit['name'] == 'Aclazotz, Deepest Betrayal'
    assert 'Temple' not in unit['name'] and unit['type_line'].startswith('Legendary Creature')
    assert unit['oracle_text'] == 'Flying, deathtouch'
    # single-faced cards pass through untouched
    plain = {'name': 'Sol Ring', 'type_line': 'Artifact', 'card_type': 'artifact'}
    assert ds._face_unit_for(plain, 'Sol Ring') is plain


def test_inspect_render_derives_defects_from_counts(monkeypatch):
    import sys, types
    import vision_analyzer as va
    answers = iter([
        'heads=1; arms=2; hands=2; copies=1; text=no; signature=no; subject=yes; hands_ok=yes',
        'heads=1; arms=3; hands=3; copies=1; text=no; signature=yes; subject=yes; hands_ok=no',
        'heads=2; arms=2; hands=2; copies=2; text=yes; signature=no; subject=yes; hands_ok=yes',
        'heads=0; arms=0; hands=0; copies=1; text=no; signature=no; subject=no; hands_ok=yes',
        'I cannot tell.',
    ])
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(vision=lambda *a, **k: next(answers)))
    assert va.inspect_render('x.png', 'Krark', 'creature', 'v') == []
    assert va.inspect_render('x.png', 'Krark', 'creature', 'v') == ['extra limbs', 'malformed hands', 'signature']
    assert va.inspect_render('x.png', 'Keiga', 'creature', 'v') == ['doubled head', 'duplicated subject', 'text']
    assert va.inspect_render('x.png', 'Glissa', 'creature', 'v') == ['subject missing']
    assert va.inspect_render('x.png', 'Glissa', 'creature', 'v') is None
    # object cards ignore anatomy counts but still flag text / signature / copies
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(
        vision=lambda *a, **k: 'heads=1; arms=3; hands=3; copies=1; text=no; signature=yes; subject=yes; hands_ok=no'))
    assert va.inspect_render('x.png', 'Sol Ring', 'artifact', 'v') == ['signature']
    assert va.inspect_render(None, 'x', 'creature', 'v') is None


def test_inspect_job_rerolls_defective_cards_once(monkeypatch, tmp_path):
    import json
    import deck_studio as ds
    import vision_analyzer as va
    raw = tmp_path / 'raw_art'; raw.mkdir()
    for slug in ('keiga_the_tide_star', 'sol_ring'):
        (raw / f'{slug}.png').write_bytes(b'x')
    cards = [{'name': 'Keiga, the Tide Star', 'card_type': 'creature'}, {'name': 'Sol Ring', 'card_type': 'artifact'}]
    ctx = {'cards': cards, 'raw_art_dir': raw, 'deck_name': 'D'}
    verdicts = {'Keiga, the Tide Star': ['doubled head'], 'Sol Ring': []}
    monkeypatch.setattr(va, 'inspect_render', lambda path, name, ctype, vm: verdicts[name])
    monkeypatch.setattr(ds, 'has_second_art_face', lambda c: False)
    monkeypatch.setattr(ds, '_ollama_work_start', lambda: None)
    monkeypatch.setattr(ds, '_ollama_work_done', lambda: None)
    monkeypatch.setattr(ds.backend_config, 'load_config', lambda: {'ollama_vision_model': 'v'})
    queued = []
    monkeypatch.setattr(ds, '_enqueue_art', lambda deck_id, name, **kw: queued.append(('art', name)))
    monkeypatch.setattr(ds, '_enqueue_inspection', lambda deck_id, names, final=False, label=None: queued.append(('inspect', tuple(names), final)))
    job = ds.Job(type=ds.INSPECT, deck_id='d', card_name='', params={'card_names': [c['name'] for c in cards], 'final': False})
    ds._execute_inspect_job(job, ctx)
    assert queued == [('art', 'Keiga, the Tide Star'), ('inspect', ('Keiga, the Tide Star',), True)]
    meta = json.load(open(raw / 'keiga_the_tide_star.meta.json'))
    assert meta['inspection']['defects'] == ['doubled head']
    # the final pass records but never re-queues
    queued.clear()
    job2 = ds.Job(type=ds.INSPECT, deck_id='d', card_name='', params={'card_names': ['Keiga, the Tide Star'], 'final': True})
    ds._execute_inspect_job(job2, ctx)
    assert queued == []


def test_generate_batch_takes_enqueues_each_card_n_times(monkeypatch, client):
    import deck_studio as ds
    queued = []
    class J:  # minimal job stand-in
        def __init__(self, n): self.id = n
    monkeypatch.setattr(ds, '_enqueue_art', lambda deck_id, name, **kw: queued.append(('art', name, kw.get('label'))) or J(len(queued)))
    monkeypatch.setattr(ds, '_enqueue_inspection', lambda deck_id, names, **kw: queued.append(('inspect', tuple(names))))
    monkeypatch.setattr(ds, 'cards_db', [{'name': 'Sol Ring', 'card_type': 'artifact'}, {'name': 'Keiga, the Tide Star', 'card_type': 'creature'}])
    r = client.post('/api/generate-batch', json={'card_names': ['Sol Ring', 'Keiga, the Tide Star'], 'skip_existing': False, 'takes': 2})
    assert r.status_code == 200, r.get_json()
    arts = [q for q in queued if q[0] == 'art']
    assert [a[1] for a in arts] == ['Sol Ring', 'Keiga, the Tide Star', 'Sol Ring', 'Keiga, the Tide Star']
    assert arts[0][2].endswith('(take 1/2)') and arts[-1][2].endswith('(take 2/2)')
    assert queued[-1] == ('inspect', ('Sol Ring', 'Keiga, the Tide Star'))
