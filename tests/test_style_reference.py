"""Style reference (FLUX Redux): the deck's inspiration images steer renders.

Root cause being fixed: the MLX migration dropped IP-Adapter image
conditioning; style became text-only and every reference-dependent look
(a papyrus/hieroglyph deck) rendered as generic fantasy."""

import json

import numpy as np
import pytest

import deck_studio as ds


class TestTokenPooling:
    def test_pools_27x27_grid_to_9x9(self):
        from flux_worker import _pool_token_grid
        emb = np.random.rand(1, 729, 8).astype(np.float32)
        out = _pool_token_grid(emb, 9)
        assert out.shape == (1, 81, 8)

    def test_pooling_preserves_mean(self):
        from flux_worker import _pool_token_grid
        emb = np.random.rand(1, 729, 4).astype(np.float32)
        out = _pool_token_grid(emb, 3)
        assert out.shape == (1, 9, 4)
        assert np.allclose(out.mean(axis=1), emb.mean(axis=1), atol=1e-5)

    def test_no_pooling_when_k_is_full_or_zero(self):
        from flux_worker import _pool_token_grid
        emb = np.random.rand(1, 729, 4).astype(np.float32)
        assert _pool_token_grid(emb, 0) is emb
        assert _pool_token_grid(emb, 27) is emb
        assert _pool_token_grid(emb, 40) is emb

    def test_non_square_token_count_untouched(self):
        from flux_worker import _pool_token_grid
        emb = np.random.rand(1, 100, 4).astype(np.float32)   # 10x10 fine
        assert _pool_token_grid(emb, 5).shape == (1, 25, 4)
        odd = np.random.rand(1, 50, 4).astype(np.float32)    # not a square grid
        assert _pool_token_grid(odd, 5) is odd


class TestSettings:
    def test_defaults(self):
        cfg = ds._style_reference_settings({})
        assert cfg == {'enabled': True, 'tokens': 25, 'strength': 1.0, 'max_images': 1}

    def test_zero_tokens_disables(self):
        cfg = ds._style_reference_settings({'style_reference': {'tokens': 0}})
        assert cfg['enabled'] is False

    def test_bad_values_fall_back(self):
        cfg = ds._style_reference_settings({'style_reference': {'tokens': 'lots', 'strength': 'x'}})
        assert cfg['tokens'] == 25 and cfg['strength'] == 1.0

    def test_reference_images_resolve_and_cap(self, tmp_path):
        for i in range(6):
            (tmp_path / f'i{i}.png').write_bytes(b'x')
        meta = {'inspiration_images': [{'filename': f'i{i}.png'} for i in range(6)]
                + [{'filename': 'missing.png'}]}
        paths = ds._style_reference_images(meta, tmp_path)
        assert len(paths) == 1                      # ONE reference by default
        assert paths[0].endswith('i0.png')
        more = ds._style_reference_images({**meta, 'style_reference': {'max_images': 9}}, tmp_path)
        assert len(more) == ds.STYLE_REFERENCE_MAX_IMAGES   # hard cap
        assert all(p.endswith('.png') for p in more)
        assert ds._style_reference_images({**meta, 'style_reference': {'enabled': False}}, tmp_path) == []
        assert ds._style_reference_images(meta, None) == []


class TestGeneratorRequest:
    def test_reference_fields_forwarded_to_worker(self, monkeypatch, tmp_path):
        from local_image_generator import LocalImageGenerator
        from PIL import Image
        gen = LocalImageGenerator()
        sent = {}
        monkeypatch.setattr(gen, '_worker_alive', lambda: True)
        monkeypatch.setattr(gen, '_send', lambda req: sent.update(req))
        def _fake_read(cb, timeout=None):
            Image.new('RGB', (8, 8)).save(sent['out_path'])
            return {'seconds': 0.1, 'seed': 1}
        monkeypatch.setattr(gen, '_read_result', _fake_read)
        gen._active_model = 'flux-schnell-4bit'
        ref = tmp_path / 'ref.png'; ref.write_bytes(b'x')
        gen.generate(prompt='p', width=64, height=64,
                     reference_images=[str(ref)], reference_tokens=25, reference_strength=0.9)
        assert sent['redux'] == {'images': [str(ref)], 'tokens': 25, 'strength': 0.9}

    def test_no_references_no_redux_field(self, monkeypatch):
        from local_image_generator import LocalImageGenerator
        from PIL import Image
        gen = LocalImageGenerator()
        sent = {}
        monkeypatch.setattr(gen, '_worker_alive', lambda: True)
        monkeypatch.setattr(gen, '_send', lambda req: sent.update(req))
        def _fake_read(cb, timeout=None):
            Image.new('RGB', (8, 8)).save(sent['out_path'])
            return {'seconds': 0.1, 'seed': 1}
        monkeypatch.setattr(gen, '_read_result', _fake_read)
        gen._active_model = 'flux-schnell-4bit'
        gen.generate(prompt='p', width=64, height=64)
        assert 'redux' not in sent


class TestApi:
    def test_round_trip(self, client, tmp_path, monkeypatch):
        decks = tmp_path / 'decks'
        d = decks / 'deckR'; d.mkdir(parents=True)
        (d / 'deck.json').write_text(json.dumps({'name': 'R', 'cards': [],
                                                 'inspiration_images': [{'filename': 'a.png'}]}))
        (d / 'a.png').write_bytes(b'x')
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        g = client.get('/api/decks/deckR/style-reference').get_json()
        assert g['style_reference']['tokens'] == 25 and g['reference_count'] == 1
        p = client.post('/api/decks/deckR/style-reference', json={'tokens': 9}).get_json()
        assert p['success'] and p['style_reference']['tokens'] == 9
        saved = json.loads((d / 'deck.json').read_text())['style_reference']
        assert saved['tokens'] == 9
        off = client.post('/api/decks/deckR/style-reference', json={'tokens': 0}).get_json()
        assert off['style_reference']['enabled'] is False
        assert client.get('/api/decks/deckR/deck-info').get_json()['style_reference']['tokens'] == 0

    def test_rejects_garbage(self, client, tmp_path, monkeypatch):
        decks = tmp_path / 'decks'
        d = decks / 'deckR'; d.mkdir(parents=True)
        (d / 'deck.json').write_text(json.dumps({'name': 'R', 'cards': []}))
        monkeypatch.setattr(ds, 'DECKS_DIR', decks)
        assert client.post('/api/decks/deckR/style-reference', json={'tokens': 'big'}).status_code == 400


class TestReduxPoolingHook:
    """The worker patches mflux's ReduxUtil once: pooled + cached per-image
    embeddings, and an empty reference list yields NO image tokens (mflux
    normalizes [] to None and then iterates it — the failure seen live when a
    deck without inspiration images rendered through the resident Redux model)."""

    def _fake_redux_util(self, monkeypatch):
        import sys, types
        calls = {'embed': 0}

        class ReduxUtil:
            @staticmethod
            def _embed_single_image(image_path, image_encoder, image_embedder, strength=1.0):
                calls['embed'] += 1
                return np.ones((1, 729, 4), dtype=np.float32)

            @staticmethod
            def embed_images(image_paths, image_encoder, image_embedder, image_strengths=None):
                return [ReduxUtil._embed_single_image(p, image_encoder, image_embedder,
                                                      (image_strengths or [1.0] * len(image_paths))[i])
                        for i, p in enumerate(image_paths)]

        mod = types.ModuleType('mflux.models.flux.variants.redux.redux_util')
        mod.ReduxUtil = ReduxUtil
        for name in ('mflux', 'mflux.models', 'mflux.models.flux', 'mflux.models.flux.variants',
                     'mflux.models.flux.variants.redux'):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        monkeypatch.setitem(sys.modules, 'mflux.models.flux.variants.redux.redux_util', mod)
        return mod, calls

    def test_pools_caches_and_handles_empty(self, monkeypatch, tmp_path):
        import flux_worker as fw
        mod, calls = self._fake_redux_util(monkeypatch)
        monkeypatch.setattr(fw, '_REDUX_STATE', {'k': 9, 'cache': {}})
        fw._install_redux_pooling()
        ref = tmp_path / 'r.png'; ref.write_bytes(b'x')
        out = mod.ReduxUtil.embed_images([str(ref)], None, None, [1.0])
        assert len(out) == 1 and out[0].shape == (1, 81, 4)      # pooled to 9x9
        mod.ReduxUtil.embed_images([str(ref)], None, None, [0.5])
        assert calls['embed'] == 1                                 # cached per (path, mtime, k)
        assert mod.ReduxUtil.embed_images([], None, None, None) == []     # no refs -> no tokens
        assert mod.ReduxUtil.embed_images(None, None, None, None) == []
