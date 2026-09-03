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
        assert cfg == {'enabled': True, 'tokens': 729, 'strength': 1.0, 'max_images': 4, 'average': True}

    def test_zero_tokens_disables(self):
        cfg = ds._style_reference_settings({'style_reference': {'tokens': 0}})
        assert cfg['enabled'] is False

    def test_tokens_capped_at_full_grid(self):
        cfg = ds._style_reference_settings({'style_reference': {'tokens': 5000}})
        assert cfg['tokens'] == 729

    def test_bad_values_fall_back(self):
        cfg = ds._style_reference_settings({'style_reference': {'tokens': 'lots', 'strength': 'x'}})
        assert cfg['tokens'] == 729 and cfg['strength'] == 1.0

    def test_reference_images_resolve_and_cap(self, tmp_path):
        for i in range(6):
            (tmp_path / f'i{i}.png').write_bytes(b'x')
        meta = {'inspiration_images': [{'filename': f'i{i}.png'} for i in range(6)]
                + [{'filename': 'missing.png'}]}
        paths = ds._style_reference_images(meta, tmp_path)
        assert len(paths) == ds.STYLE_REFERENCE_MAX_IMAGES   # all refs (averaged), hard cap
        assert paths[0].endswith('i0.png')
        one = ds._style_reference_images({**meta, 'style_reference': {'max_images': 1}}, tmp_path)
        assert len(one) == 1
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
        assert sent['redux'] == {'images': [str(ref)], 'tokens': 25, 'strength': 0.9, 'average': True}

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
        assert g['style_reference']['tokens'] == 729 and g['reference_count'] == 1
        p = client.post('/api/decks/deckR/style-reference', json={'tokens': 9}).get_json()
        assert p['success'] and p['style_reference']['tokens'] == 9
        saved = json.loads((d / 'deck.json').read_text())['style_reference']
        assert saved['tokens'] == 9
        high = client.post('/api/decks/deckR/style-reference', json={'tokens': 5000}).get_json()
        assert high['style_reference']['tokens'] == 729         # clamped to the full grid
        off = client.post('/api/decks/deckR/style-reference', json={'tokens': 0}).get_json()
        assert off['style_reference']['enabled'] is False
        # raising the dial from Off re-enables without an explicit flag
        back = client.post('/api/decks/deckR/style-reference', json={'tokens': 25}).get_json()
        assert back['style_reference']['enabled'] is True and back['style_reference']['tokens'] == 25
        assert client.get('/api/decks/deckR/deck-info').get_json()['style_reference']['tokens'] == 25

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
        # several references AVERAGE into one style token set
        ref2 = tmp_path / 'r2.png'; ref2.write_bytes(b'y')
        fw._REDUX_STATE['average'] = True
        avg = mod.ReduxUtil.embed_images([str(ref), str(ref2)], None, None, [1.0, 1.0])
        assert len(avg) == 1 and avg[0].shape == (1, 81, 4)
        fw._REDUX_STATE['average'] = False
        assert len(mod.ReduxUtil.embed_images([str(ref), str(ref2)], None, None, [1.0, 1.0])) == 2


class TestBlockMask:
    """Reference tokens are visible only to the style blocks: the attention
    patch masks key positions [T, T+R) with -1e9 in every other block."""

    def _install(self, monkeypatch):
        import sys, types
        calls = []
        au = types.ModuleType('mflux.models.flux.model.flux_transformer.common.attention_utils')
        class AttentionUtils:
            @staticmethod
            def compute_attention(query, key, value, batch_size, num_heads, head_dim, mask=None):
                calls.append(mask); return 'out'
        au.AttentionUtils = AttentionUtils
        jb = types.ModuleType('mflux.models.flux.model.flux_transformer.joint_transformer_block')
        class JointTransformerBlock:
            def __init__(self, layer): self.layer = layer
            def __call__(self, x): return x
        jb.JointTransformerBlock = JointTransformerBlock
        sb = types.ModuleType('mflux.models.flux.model.flux_transformer.single_transformer_block')
        class SingleTransformerBlock:
            def __init__(self, layer): self.layer = layer
            def __call__(self, x): return x
        sb.SingleTransformerBlock = SingleTransformerBlock
        for name in ('mflux', 'mflux.models', 'mflux.models.flux', 'mflux.models.flux.model',
                     'mflux.models.flux.model.flux_transformer', 'mflux.models.flux.model.flux_transformer.common'):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        monkeypatch.setitem(sys.modules, au.__name__, au)
        monkeypatch.setitem(sys.modules, jb.__name__, jb)
        monkeypatch.setitem(sys.modules, sb.__name__, sb)
        # a tiny stand-in for mlx.core with the ops the mask builder uses
        mxmod = types.ModuleType('mlx'); core = types.ModuleType('mlx.core')
        core.zeros = lambda shape, dtype=None: np.zeros(shape, dtype=np.float32)
        core.full = lambda shape, val, dtype=None: np.full(shape, val, dtype=np.float32)
        core.concatenate = lambda arrs: np.concatenate(arrs)
        monkeypatch.setitem(sys.modules, 'mlx', mxmod); monkeypatch.setitem(sys.modules, 'mlx.core', core)
        import flux_worker as fw
        monkeypatch.setattr(fw, '_REDUX_STATE', {'k': 27, 'cache': {}, 'average': True, 'n_ref_tokens': 0,
                                                 'txt_tokens': 0, 'allow_double': None, 'allow_single': None, 'cur': None})
        fw._install_block_mask()
        return fw, au, jb, sb, calls

    def test_masks_reference_tokens_outside_style_blocks(self, monkeypatch):
        fw, au, jb, sb, calls = self._install(monkeypatch)
        st = fw._REDUX_STATE
        st.update(txt_tokens=4, n_ref_tokens=3, allow_double={0, 1}, allow_single=set())
        q = np.zeros((1, 1, 10, 2), dtype=np.float32); k = np.zeros((1, 1, 10, 2), dtype=np.float32)
        jb.JointTransformerBlock(0)('x'); au.AttentionUtils.compute_attention(q, k, k, 1, 1, 2)
        assert calls[-1] is None                              # allowed block: untouched
        jb.JointTransformerBlock(5)('x'); au.AttentionUtils.compute_attention(q, k, k, 1, 1, 2)
        m = calls[-1]; assert m.shape == (1, 1, 1, 10)
        assert (m[0, 0, 0, 4:7] == -1e9).all() and (m[0, 0, 0, :4] == 0).all() and (m[0, 0, 0, 7:] == 0).all()
        sb.SingleTransformerBlock(3)('x'); au.AttentionUtils.compute_attention(q, k, k, 1, 1, 2)
        assert calls[-1] is not None                          # single blocks: masked by default

    def test_no_references_no_mask(self, monkeypatch):
        fw, au, jb, sb, calls = self._install(monkeypatch)
        fw._REDUX_STATE.update(txt_tokens=4, n_ref_tokens=0, allow_double=set(), allow_single=set())
        q = np.zeros((1, 1, 6, 2), dtype=np.float32)
        jb.JointTransformerBlock(7)('x'); au.AttentionUtils.compute_attention(q, q, q, 1, 1, 2)
        assert calls[-1] is None



class TestRecognizedSourceFallback:
    """With no declared style source, the analyst's own 'Source:' line fills in
    (medium classification + render lead); a declaration always wins."""

    DESCS = ["Source: Rick and Morty\nArt Style: Digital illustration with sharp outlines\n- Medium: Digital illustration",
             "Source: Original\nArt Style: something"]

    def test_recognized_source(self):
        from vision_analyzer import recognized_style_source
        assert recognized_style_source(self.DESCS) == 'Rick and Morty'
        assert recognized_style_source(["Source: Original\nArt Style: x"]) == ''
        assert recognized_style_source([]) == ''

    def test_source_line_drives_medium_vote(self):
        from vision_analyzer import _classify_medium_from_evidence
        assert _classify_medium_from_evidence(self.DESCS[0], '', 'm') == 'cel animation'

    def test_effective_source_prefers_declaration(self):
        meta = {'style_source': '', 'inspiration_images': [{'style_description': self.DESCS[0]}]}
        assert ds._effective_style_source(meta) == 'Rick and Morty'
        assert ds._effective_style_source({**meta, 'style_source': 'Moebius'}) == 'Moebius'
        assert ds._effective_style_source({}) == ''


def test_mlx_request_retries_once_on_worker_death(monkeypatch):
    import mlx_llm
    calls = []
    def once(req):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('MLX worker exited unexpectedly (code -6)')
        return 'ok'
    monkeypatch.setattr(mlx_llm, '_request_once', once)
    monkeypatch.setattr('time.sleep', lambda s: None)
    assert mlx_llm._request({'cmd': 'chat'}) == 'ok' and len(calls) == 2
    def always(req):
        raise RuntimeError('MLX worker error: bad prompt')
    monkeypatch.setattr(mlx_llm, '_request_once', always)
    import pytest
    with pytest.raises(RuntimeError):
        mlx_llm._request({'cmd': 'chat'})


def test_flux_prompt_puts_the_subject_right_after_the_style_lead(monkeypatch):
    import deck_studio as ds
    monkeypatch.setenv('FLUX_PROMPT_ORDER', 'subject-early')
    bits = ['in the style of a 2010s adult animated sitcom, original character designs',
            'cel animation, thick outlines, palette of yellow, orange']
    out = ds._assemble_flux_prompt(bits, 'Aclazotz, a Bat God, unfurls her wings over the temple. Moonlight below.', 'more teeth')
    assert out.startswith('in the style of a 2010s adult animated sitcom, original character designs. Aclazotz, a Bat God, unfurls her wings over the temple. cel animation')
    assert out.index('Moonlight below') > out.index('cel animation')
    assert out.rstrip().endswith('no card frame, no borders.') and 'more teeth' in out
    # no style at all: scene, then guard
    assert ds._assemble_flux_prompt([], 'A ring on a table.').startswith('A ring on a table.')


def test_flux_prompt_medium_subject_idiom_order(monkeypatch):
    import deck_studio as ds
    monkeypatch.setenv('FLUX_PROMPT_ORDER', 'medium-subject-idiom')
    bits = ['in the style of X, original character designs',
            'cel animation, fully coloured with saturated flat colour fills, no bare white paper, '
            'palette of bright yellow, dusty coral, exaggerated body proportions, wobbly eyes']
    out = ds._assemble_flux_prompt(bits, 'Keiga, a Dragon Spirit, bursts from the sea. Mist below.')
    assert out.startswith('in the style of X, original character designs, cel animation, fully coloured with saturated flat colour fills, no bare white paper, palette of bright yellow, dusty coral. Keiga, a Dragon Spirit, bursts from the sea. exaggerated body proportions, wobbly eyes. Mist below.')


def test_flux_prompt_coverage_subject_block_order(monkeypatch):
    import deck_studio as ds
    monkeypatch.delenv('FLUX_PROMPT_ORDER', raising=False)
    bits = ['in the style of X, original character designs',
            'cel animation, fully coloured with saturated flat colour fills, no bare white paper, '
            'palette of bright yellow, dusty coral, wobbly eyes']
    out = ds._assemble_flux_prompt(bits, 'Glissa, a Zombie Elf, leans on a tree. A worm wriggles.')
    assert out.startswith('in the style of X, original character designs, fully coloured with saturated flat colour fills, no bare white paper. Glissa, a Zombie Elf, leans on a tree. cel animation, palette of bright yellow, dusty coral, wobbly eyes. A worm wriggles.')


def test_with_figure_idiom_appends_to_first_sentence_only():
    import deck_studio as ds
    out = ds._with_figure_idiom('Keiga, a Dragon Spirit, rises from the sea. Mist hangs low.',
                                ['wobbly eyes', 'lumpy anatomy', 'thick chaotic lines', 'extra'])
    assert out == 'Keiga, a Dragon Spirit, rises from the sea, drawn with wobbly eyes, lumpy anatomy, thick chaotic lines. Mist hangs low.'
    # already present -> untouched; no idiom -> untouched
    assert ds._with_figure_idiom('Keiga, drawn with wobbly eyes, rises.', ['wobbly eyes']) == 'Keiga, drawn with wobbly eyes, rises.'
    assert ds._with_figure_idiom('Keiga rises.', []) == 'Keiga rises.'


def test_enqueue_art_stores_seed_in_params(monkeypatch):
    import deck_studio as ds
    captured = {}
    monkeypatch.setattr(ds.gen_queue, 'enqueue', lambda job: captured.__setitem__('job', job))
    monkeypatch.setattr(ds, '_deck_display_name', lambda d: 'Deck')
    ds._enqueue_art('deck-a', 'Sol Ring', seed=1234)
    assert captured['job'].params == {'seed': 1234}
    ds._enqueue_art('deck-a', 'Sol Ring')
    assert captured['job'].params == {}
