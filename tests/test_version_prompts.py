"""Tests for art-version prompt snapshots and restore-on-revert."""

import pytest
from PIL import Image

import deck_studio as ds


@pytest.fixture
def version_dirs(tmp_path, monkeypatch):
    raw = tmp_path / 'raw_art'
    comp = tmp_path / 'composites'
    vers = tmp_path / 'art_versions'
    raw.mkdir()
    comp.mkdir()
    monkeypatch.setattr(ds, 'RAW_ART_DIR', raw)
    monkeypatch.setattr(ds, 'COMPOSITE_DIR', comp)
    monkeypatch.setattr(ds, 'VERSIONS_DIR', vers)
    monkeypatch.setattr(ds, 'active_deck_id', None)  # persist becomes a no-op
    monkeypatch.setattr(ds, 'cards_db', [])
    monkeypatch.setattr(ds, 'prompts_map', {})
    return raw


def _seed_art(raw_dir, name):
    slug = ds.name_to_slug(name)
    Image.new('RGB', (8, 8), (200, 40, 40)).save(raw_dir / f'{slug}.png')


class TestVersionPromptSnapshot:
    def test_archive_snapshots_card_prompt(self, version_dirs):
        ds.prompts_map['Test Card'] = 'a goblin juggling meteors'
        _seed_art(version_dirs, 'Test Card')
        info = ds.archive_current_art('Test Card')
        assert info['version'] == 1
        assert info['card_prompt'] == 'a goblin juggling meteors'
        assert ds.list_versions('Test Card')[0]['card_prompt'] == \
            'a goblin juggling meteors'

    def test_archive_without_prompt_stores_empty(self, version_dirs):
        _seed_art(version_dirs, 'Test Card')
        info = ds.archive_current_art('Test Card')
        assert info['card_prompt'] == ''


class TestRevertRestoresPrompt:
    def test_revert_restores_archived_prompt(self, version_dirs):
        ds.prompts_map['Test Card'] = 'prompt v1'
        ds.cards_db.append({'name': 'Test Card', 'prompt': 'prompt v1'})
        _seed_art(version_dirs, 'Test Card')
        ds.archive_current_art('Test Card')          # v1 with 'prompt v1'

        ds.prompts_map['Test Card'] = 'prompt v2 — totally different'
        ds.cards_db[0]['prompt'] = 'prompt v2 — totally different'
        ok, msg = ds.revert_to_version('Test Card', 1)
        assert ok, msg
        assert ds.prompts_map['Test Card'] == 'prompt v1'
        assert ds.cards_db[0]['prompt'] == 'prompt v1'
        assert 'prompt restored' in msg

    def test_revert_archives_current_prompt_first(self, version_dirs):
        # Flipping back and forth loses nothing: the revert snapshots the
        # CURRENT prompt as a new version before restoring the old one
        ds.prompts_map['Test Card'] = 'prompt v1'
        _seed_art(version_dirs, 'Test Card')
        ds.archive_current_art('Test Card')          # v1
        ds.prompts_map['Test Card'] = 'prompt v2'
        ds.revert_to_version('Test Card', 1)         # archives v2 first
        versions = ds.list_versions('Test Card')
        assert versions[-1]['card_prompt'] == 'prompt v2'
        # ...and reverting to that snapshot brings v2 back
        ok, _ = ds.revert_to_version('Test Card', versions[-1]['version'])
        assert ok
        assert ds.prompts_map['Test Card'] == 'prompt v2'

    def test_revert_pre_feature_version_leaves_prompt_alone(self, version_dirs):
        # Versions archived before this feature have no card_prompt key —
        # the current prompt must survive the revert untouched
        _seed_art(version_dirs, 'Test Card')
        info = ds.archive_current_art('Test Card')
        # simulate an old manifest entry
        import json
        mpath = ds.VERSIONS_DIR / ds.name_to_slug('Test Card') / 'manifest.json'
        manifest = json.loads(mpath.read_text())
        del manifest['versions'][0]['card_prompt']
        mpath.write_text(json.dumps(manifest))

        ds.prompts_map['Test Card'] = 'current prompt'
        ok, msg = ds.revert_to_version('Test Card', info['version'])
        assert ok
        assert ds.prompts_map['Test Card'] == 'current prompt'
        assert 'prompt restored' not in msg

    def test_back_face_prompt_key_roundtrip(self, version_dirs):
        # "<name> [back]" version keys snapshot/restore THEIR prompt entry
        key = 'Test DFC [back]'
        ds.prompts_map[key] = 'back face prompt v1'
        _seed_art(version_dirs, key)
        ds.archive_current_art(key)
        ds.prompts_map[key] = 'back face prompt v2'
        ok, _ = ds.revert_to_version(key, 1)
        assert ok
        assert ds.prompts_map[key] == 'back face prompt v1'


class TestGenerationTimePromptStamp:
    def _seed_art_with_meta(self, raw_dir, name, card_prompt):
        import json
        slug = ds.name_to_slug(name)
        Image.new('RGB', (8, 8), (200, 40, 40)).save(raw_dir / f'{slug}.png')
        (raw_dir / f'{slug}.meta.json').write_text(json.dumps({
            'card': name, 'prompt_sent': f'styled({card_prompt})',
            'card_prompt': card_prompt,
        }))

    def test_archive_prefers_generation_time_prompt(self, version_dirs):
        # The user's off-by-one repro: art was generated with prompt A, then
        # the prompt was regenerated to B BEFORE the next generation archives
        # the old art. The version must carry A (what produced the art), not
        # B (what prompts_map holds at archive time).
        self._seed_art_with_meta(version_dirs, 'Test Card', 'prompt A')
        ds.prompts_map['Test Card'] = 'prompt B — already edited for next gen'
        info = ds.archive_current_art('Test Card')
        assert info['card_prompt'] == 'prompt A'

    def test_full_iterate_and_restore_flow(self, version_dirs):
        # gen A -> regen prompt to B -> gen B (archives A's art) ->
        # restore n-1 must bring back prompt A, and the flip-forward version
        # must carry B (from current art's meta), not any in-between edit
        self._seed_art_with_meta(version_dirs, 'Test Card', 'prompt A')
        ds.prompts_map['Test Card'] = 'prompt B'
        ds.archive_current_art('Test Card')             # v1: A's art + prompt A
        self._seed_art_with_meta(version_dirs, 'Test Card', 'prompt B')  # "gen B"
        ok, msg = ds.revert_to_version('Test Card', 1)  # archives B, restores A
        assert ok
        assert ds.prompts_map['Test Card'] == 'prompt A'
        versions = ds.list_versions('Test Card')
        assert versions[-1]['card_prompt'] == 'prompt B'

    def test_pre_stamp_art_falls_back_to_prompts_map(self, version_dirs):
        # Art generated before the stamp existed (no card_prompt in meta)
        # keeps the archive-time snapshot as a best effort
        _seed_art(version_dirs, 'Test Card')  # no meta at all
        ds.prompts_map['Test Card'] = 'current prompt'
        info = ds.archive_current_art('Test Card')
        assert info['card_prompt'] == 'current prompt'
