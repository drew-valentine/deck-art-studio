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
        # A running analysis for an INACTIVE deck must not write the global
        # style progress (which drives the active deck's UI).
        ds.style_analysis_progress = {}
        job = ds.Job(type=ds.ANALYZE, deck_id='some-other-deck', card_name='',
                     params={'mode': 'distill'})
        ds._analysis_job_ctx.job = job
        try:
            ds._style_progress_update('analyzing', 1, 5, 'working...')
            assert ds.style_analysis_progress == {}          # global untouched
            assert job.progress['message'] == 'working...'   # job carries it
            ds._style_progress_clear()
            assert ds.style_analysis_progress == {}
        finally:
            ds._analysis_job_ctx.job = None

    def test_progress_mirrors_for_active_deck(self, populated_state):
        ds.style_analysis_progress = {}
        job = ds.Job(type=ds.ANALYZE, deck_id=ds.active_deck_id, card_name='',
                     params={'mode': 'distill'})
        ds._analysis_job_ctx.job = job
        try:
            ds._style_progress_update('analyzing', 2, 5, 'active work')
            assert ds.style_analysis_progress.get('message') == 'active work'
        finally:
            ds._analysis_job_ctx.job = None
            ds.style_analysis_progress = {}

    def test_deck_delete_cancels_analysis_jobs(self, client, populated_state):
        job = ds.gen_queue.enqueue(ds.Job(
            type=ds.ANALYZE, deck_id='doomed-deck', card_name='',
            params={'mode': 'reanalyze'}))
        ds.gen_queue.cancel_deck('doomed-deck')
        assert ds.gen_queue.get(job.id).status == 'cancelled'
