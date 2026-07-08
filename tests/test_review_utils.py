"""Regression tests for code-review fixes in scryfall_client.

Covers:
  (a) the shared cache slug helper maps split-card names to a slash-free slug
  (b) a corrupt/truncated cache file is handled gracefully (deleted + refetched)
      rather than raising JSONDecodeError and permanently aborting deck imports.

No real network is used — urlopen is mocked.
"""

import json
from unittest.mock import MagicMock

import scryfall_client


# ---------------------------------------------------------------------------
# (a) Shared cache slug helper
# ---------------------------------------------------------------------------
class TestCacheSlug:
    def test_split_card_has_no_slash(self):
        slug = scryfall_client._cache_slug("Fire // Ice")
        assert "/" not in slug
        assert slug == "fire__ice"

    def test_lone_slash_replaced(self):
        # A stray '/' (not the ' // ' separator) must not create a subdirectory.
        slug = scryfall_client._cache_slug("A/B")
        assert "/" not in slug

    def test_matches_write_time_slug_for_split_cards(self):
        # The slug used for the rate-limit "was cached" check must equal the
        # slug fetch_card_by_name uses to write the cache, or split cards would
        # never register as cached.
        name = "Fire // Ice"
        assert scryfall_client._cache_slug(name) == "fire__ice"


# ---------------------------------------------------------------------------
# (b) Corrupt cache handling
# ---------------------------------------------------------------------------
class TestCorruptCache:
    def test_read_cache_returns_none_and_deletes_bad_file(self, tmp_path):
        bad = tmp_path / "broken.json"
        bad.write_text('{"name": "Sol Ring", "truncat')  # invalid JSON
        assert scryfall_client._read_cache(bad) is None
        assert not bad.exists()  # corrupt file removed

    def test_read_cache_returns_data_for_valid_file(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"name": "Sol Ring"}))
        assert scryfall_client._read_cache(good) == {"name": "Sol Ring"}

    def test_write_cache_is_atomic_and_roundtrips(self, tmp_path):
        target = tmp_path / "card.json"
        scryfall_client._write_cache(target, {"name": "Fire // Ice"})
        assert target.exists()
        assert not (tmp_path / "card.json.tmp").exists()  # temp cleaned up
        assert json.loads(target.read_text()) == {"name": "Fire // Ice"}

    def test_fetch_falls_through_on_corrupt_cache(self, tmp_path, monkeypatch):
        """A truncated cache file must not raise; fetch re-fetches from network."""
        monkeypatch.setattr(scryfall_client, "CACHE_DIR", tmp_path)

        # Seed a corrupt cache entry for the card.
        name = "Sol Ring"
        cache_path = tmp_path / f"{scryfall_client._cache_slug(name)}.json"
        cache_path.write_text('{"name": "Sol Ring", "oops')  # truncated JSON

        # Mock the network so no real request happens.
        fresh = {"name": "Sol Ring", "mana_cost": "{1}"}

        def fake_get(url):
            return fresh

        monkeypatch.setattr(scryfall_client, "_scryfall_get", fake_get)

        result = scryfall_client.fetch_card_by_name(name)
        assert result == fresh
        # Corrupt cache was overwritten with the fresh (valid) response.
        assert json.loads(cache_path.read_text()) == fresh

    def test_fetch_via_mocked_urlopen_on_corrupt_cache(self, tmp_path, monkeypatch):
        """End-to-end variant: mock urlopen itself, assert no JSONDecodeError."""
        monkeypatch.setattr(scryfall_client, "CACHE_DIR", tmp_path)

        name = "Lightning Bolt"
        cache_path = tmp_path / f"{scryfall_client._cache_slug(name)}.json"
        cache_path.write_text("not json at all")

        payload = {"name": "Lightning Bolt", "mana_cost": "{R}"}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode()

        monkeypatch.setattr(
            scryfall_client.urllib.request, "urlopen",
            lambda req, timeout=None: FakeResp(),
        )

        result = scryfall_client.fetch_card_by_name(name)
        assert result == payload
