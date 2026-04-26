"""Tests for Flask API endpoints — read-only, security, and state mutations."""

import json
import pytest
import deck_studio


# ===========================================================================
# Read-only endpoints
# ===========================================================================
class TestApiStatus:
    def test_returns_200(self, client):
        resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'is_generating' in data
        assert 'statuses' in data

    def test_is_generating_false_by_default(self, client):
        data = client.get('/api/status').get_json()
        assert data['is_generating'] is False

    def test_statuses_reflect_globals(self, client, populated_state):
        data = client.get('/api/status').get_json()
        assert 'Goblin Guide' in data['statuses']
        assert data['statuses']['Goblin Guide']['status'] == 'complete'


class TestApiCards:
    def test_returns_200(self, client):
        resp = client.get('/api/cards')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_returns_card_fields(self, client, populated_state):
        cards = client.get('/api/cards').get_json()
        assert len(cards) > 0
        card = cards[0]
        for field in ('name', 'slug', 'mana_cost', 'type_line', 'status', 'prompt', 'prompt_stale'):
            assert field in card, f"Missing field: {field}"

    def test_slug_matches_name(self, client, populated_state):
        cards = client.get('/api/cards').get_json()
        sol = next(c for c in cards if c['name'] == 'Sol Ring')
        assert sol['slug'] == 'sol_ring'


class TestApiModelConfig:
    def test_returns_200(self, client):
        resp = client.get('/api/model-config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'active' in data
        assert 'options' in data

    def test_options_have_required_fields(self, client):
        data = client.get('/api/model-config').get_json()
        for key, opt in data['options'].items():
            assert 'label' in opt
            assert 'cost_per_image' in opt


class TestApiDecks:
    def test_returns_200(self, client):
        resp = client.get('/api/decks')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'decks' in data


class TestApiIndex:
    def test_homepage_returns_200(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'Deck Art Studio' in resp.data


# ===========================================================================
# Security — path traversal
# ===========================================================================
class TestApiSecurity:
    @pytest.mark.parametrize('malicious_id', [
        '..',
        'foo..bar',
    ])
    def test_deck_activate_rejects_traversal(self, client, malicious_id):
        """before_request hook rejects deck IDs with path traversal chars."""
        resp = client.post(f'/api/decks/{malicious_id}/activate')
        assert resp.status_code == 400

    @pytest.mark.parametrize('malicious_id', [
        '..',
        'foo..bar',
    ])
    def test_deck_delete_rejects_traversal(self, client, malicious_id):
        resp = client.delete(f'/api/decks/{malicious_id}')
        assert resp.status_code == 400

    @pytest.mark.parametrize('malicious_id', [
        '../etc/passwd',
        'foo/bar',
        'foo/../bar',
    ])
    def test_slash_paths_rejected_by_router(self, client, malicious_id):
        """Flask router rejects paths with slashes (404 — no route match)."""
        resp = client.post(f'/api/decks/{malicious_id}/activate')
        assert resp.status_code == 404

    def test_raw_image_rejects_traversal(self, client):
        resp = client.get('/api/image/raw/../../../etc/passwd')
        # Flask may return 404 for the route or 400 from our validation
        assert resp.status_code in (400, 404)

    def test_composite_image_rejects_traversal(self, client):
        resp = client.get('/api/image/composite/../../../etc/passwd')
        assert resp.status_code in (400, 404)


# ===========================================================================
# State mutation endpoints
# ===========================================================================
class TestApiModelSwitch:
    def test_switch_model(self, client):
        resp = client.post('/api/model-config',
                           json={'model_key': 'dall-e-3-standard'})
        assert resp.status_code == 200
        assert deck_studio.active_model_key == 'dall-e-3-standard'

    def test_invalid_model_rejected(self, client):
        resp = client.post('/api/model-config',
                           json={'model_key': 'nonexistent-model'})
        assert resp.status_code == 400


class TestApiStopBatch:
    def test_stop_when_not_generating(self, client):
        resp = client.post('/api/stop-batch')
        assert resp.status_code == 200

    def test_stop_clears_flag(self, client):
        deck_studio.is_generating = True
        resp = client.post('/api/stop-batch')
        assert resp.status_code == 200
        assert deck_studio.is_generating is False


class TestApiCancelSingle:
    def test_cancel_card(self, client, populated_state):
        resp = client.post('/api/cancel-single',
                           json={'card_name': 'Sol Ring'})
        assert resp.status_code == 200
        assert 'Sol Ring' in deck_studio._cancel_single


class TestApiSavePrompt:
    def test_save_prompt(self, client, populated_state):
        resp = client.post('/api/save-prompt',
                           json={'card_name': 'Sol Ring', 'prompt': 'A glowing golden ring'})
        assert resp.status_code == 200
        assert deck_studio.prompts_map['Sol Ring'] == 'A glowing golden ring'

    def test_save_prompt_missing_name(self, client):
        resp = client.post('/api/save-prompt', json={'prompt': 'test'})
        assert resp.status_code == 400


class TestApiScryfallRef:
    def test_get_current(self, client):
        resp = client.get('/api/scryfall-ref')
        assert resp.status_code == 200
        assert 'enabled' in resp.get_json()

    def test_toggle(self, client):
        resp = client.post('/api/scryfall-ref', json={'enabled': False})
        assert resp.status_code == 200
        assert deck_studio.use_scryfall_ref is False
