"""Regression tests for Scryfall rate-limit handling (HTTP 429).

The deck import fetches card data with several threads; without a shared
throttle + 429 retry, Scryfall returns 429 Too Many Requests and cards were
silently dropped from the imported deck (only ~72 of ~90 came through).
"""

import urllib.error

import pytest

import scryfall_client as sc


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Never actually sleep (throttle + backoff) during tests.
    monkeypatch.setattr(sc.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(sc, '_throttle', lambda: None)


def test_scryfall_get_retries_on_429_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(1)
        if len(calls) < 3:            # first two attempts are throttled
            raise urllib.error.HTTPError('u', 429, 'Too Many Requests', {}, None)
        return _FakeResp(b'{"name": "Sol Ring"}')

    monkeypatch.setattr(sc.urllib.request, 'urlopen', fake_urlopen)
    out = sc._scryfall_get('https://api.scryfall.com/cards/named?exact=Sol+Ring')
    assert out['name'] == 'Sol Ring'
    assert len(calls) == 3            # two 429s were retried, third succeeded


def test_scryfall_get_gives_up_after_max_retries(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(1)
        raise urllib.error.HTTPError('u', 429, 'Too Many Requests', {}, None)

    monkeypatch.setattr(sc.urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        sc._scryfall_get('https://api.scryfall.com/x')
    # initial attempt + SCRYFALL_MAX_RETRIES retries
    assert len(calls) == sc.SCRYFALL_MAX_RETRIES + 1


def test_scryfall_get_does_not_retry_non_429(monkeypatch):
    # A 404 (unknown card) must propagate immediately so the caller can fall
    # back to fuzzy search — retrying it would waste time and mask the miss.
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(1)
        raise urllib.error.HTTPError('u', 404, 'Not Found', {}, None)

    monkeypatch.setattr(sc.urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        sc._scryfall_get('https://api.scryfall.com/x')
    assert len(calls) == 1            # no retries on a non-429 error


def test_scryfall_get_honors_retry_after_header(monkeypatch):
    slept = []
    monkeypatch.setattr(sc.time, 'sleep', lambda s: slept.append(s))
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                'u', 429, 'Too Many Requests', {'Retry-After': '2'}, None)
        return _FakeResp(b'{"name": "Command Tower"}')

    monkeypatch.setattr(sc.urllib.request, 'urlopen', fake_urlopen)
    out = sc._scryfall_get('https://api.scryfall.com/x')
    assert out['name'] == 'Command Tower'
    # Backoff waited at least the server-provided Retry-After (2s).
    assert any(s >= 2 for s in slept)
