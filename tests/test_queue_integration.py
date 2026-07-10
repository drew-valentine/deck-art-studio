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
