"""Regression tests for the July 2026 code-review fixes in deck_studio.py."""

import json
import math

import pytest

import deck_studio as ds


# ---------------------------------------------------------------------------
# Atomic JSON persistence + corruption safety
# ---------------------------------------------------------------------------
class TestAtomicJson:
    def test_atomic_dump_roundtrip_no_temp_left(self, tmp_path):
        p = tmp_path / 'sub' / 'x.json'
        ds._atomic_json_dump(p, {'a': 1})
        assert json.loads(p.read_text()) == {'a': 1}
        assert not (p.parent / (p.name + '.tmp')).exists()

    def test_load_json_safe_quarantines_corrupt(self, tmp_path):
        p = tmp_path / 'bad.json'
        p.write_text('{ truncated')
        assert ds._load_json_safe(p, {'default': True}) == {'default': True}
        assert (tmp_path / 'bad.json.corrupt').exists()  # moved aside
        assert not p.exists()

    def test_load_json_safe_missing_returns_default(self, tmp_path):
        assert ds._load_json_safe(tmp_path / 'nope.json', []) == []


# ---------------------------------------------------------------------------
# Numeric input hardening
# ---------------------------------------------------------------------------
class TestFiniteFloat:
    def test_rejects_nan_and_inf(self):
        assert ds._finite_float(float('nan'), 1.0, 0, 10) == 1.0
        assert ds._finite_float(float('inf'), 1.0, 0, 10) == 1.0
        assert ds._finite_float('NaN', 1.0, 0, 10) == 1.0

    def test_rejects_non_numeric(self):
        assert ds._finite_float('abc', 2.0, 0, 10) == 2.0
        assert ds._finite_float(None, 2.0, 0, 10) == 2.0

    def test_clamps_to_range(self):
        assert ds._finite_float(999, 1.0, 0.1, 10.0) == 10.0
        assert ds._finite_float(-999, 1.0, 0.1, 10.0) == 0.1
        assert ds._finite_float(3.5, 1.0, 0.1, 10.0) == 3.5


# ---------------------------------------------------------------------------
# ollama guard reset (missing `global` bug)
# ---------------------------------------------------------------------------
class TestOllamaGuardReset:
    def test_force_reset_actually_resets_module_counter(self, monkeypatch):
        # Simulate a leaked work_start, then force the timeout reset path and
        # confirm the MODULE global reached 0 (the missing `global` made it
        # rebind a dead local and stall every later generation).
        monkeypatch.setattr(ds, '_unload_all_ollama_models', lambda: None)
        ds._ollama_active_count = 3
        ds.ollama_idle.clear()
        ds._wait_for_ollama_idle(timeout=0)  # times out immediately, forces reset
        assert ds._ollama_active_count == 0
        assert ds.ollama_idle.is_set()


# ---------------------------------------------------------------------------
# Endpoint validation
# ---------------------------------------------------------------------------
class TestEndpointValidation:
    def test_art_position_rejects_nan(self, client, populated_state, monkeypatch, tmp_path):
        monkeypatch.setattr(ds, 'ART_PROMPTS_PATH', tmp_path / 'p.json')
        name = ds.cards_db[0]['name']
        # NaN literal is accepted by Python's json but must not persist
        resp = client.post('/api/cards/art-position',
                           data=json.dumps({'card_name': name, 'art_zoom': float('nan')}),
                           content_type='application/json')
        assert resp.status_code == 200
        z = ds.cards_db[0].get('frame_overrides', {}).get('art_zoom')
        assert z is None or math.isfinite(z)

    def test_frame_overrides_rejects_non_dict(self, client, populated_state):
        name = ds.cards_db[0]['name']
        resp = client.post('/api/cards/frame-overrides',
                           data=json.dumps({'card_name': name, 'frame_overrides': [1, 2]}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_frame_settings_rejects_non_dict(self, client, populated_state):
        deck = ds.active_deck_id or 'x'
        resp = client.post(f'/api/decks/{deck}/frame-settings',
                           data=json.dumps([1, 2, 3]),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_revert_rejects_bad_version(self, client, populated_state):
        resp = client.post('/api/revert/Whatever',
                           data=json.dumps({'version': 'abc'}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_generate_unknown_card_404(self, client, populated_state):
        resp = client.post('/api/generate',
                           data=json.dumps({'card_name': 'Definitely Not A Real Card'}),
                           content_type='application/json')
        assert resp.status_code == 404
