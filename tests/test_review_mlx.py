"""Regression tests for the MLX-subprocess code-review fixes.

None of these require MLX/torch — they exercise pure-stdlib paths:
  * backend_config atomic save/load round-trip + corrupt-file fallback
  * gpu_coord InactivityWatchdog._fire is a no-op after stop()/done
"""

import importlib
import threading
import time

import pytest


# ---------------------------------------------------------------------------
# backend_config: atomic save/load
# ---------------------------------------------------------------------------

def _fresh_backend_config(tmp_path, monkeypatch):
    """Import backend_config with CONFIG_PATH redirected into tmp_path."""
    import backend_config
    importlib.reload(backend_config)
    monkeypatch.setattr(backend_config, "CONFIG_PATH", tmp_path / "backend_config.json")
    return backend_config


def test_save_load_round_trip(tmp_path, monkeypatch):
    bc = _fresh_backend_config(tmp_path, monkeypatch)
    cfg = bc.load_config()
    cfg["local_image_model"] = "flux-schnell-4bit"
    cfg["local_image_autoload"] = True
    bc.save_config(cfg)

    loaded = bc.load_config()
    assert loaded["local_image_model"] == "flux-schnell-4bit"
    assert loaded["local_image_autoload"] is True
    # Defaults still present for keys we never touched.
    assert loaded["llm_backend"] == "local"


def test_save_is_atomic_no_temp_leftovers(tmp_path, monkeypatch):
    bc = _fresh_backend_config(tmp_path, monkeypatch)
    bc.save_config(bc.load_config())
    # The atomic temp file must have been renamed away, not left behind.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "backend_config.json"]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_truncated_config_falls_back_to_defaults(tmp_path, monkeypatch):
    bc = _fresh_backend_config(tmp_path, monkeypatch)
    # Simulate an interrupted/corrupt write: invalid JSON on disk.
    (tmp_path / "backend_config.json").write_text('{"local_image_model": "flux-sch')

    cfg = bc.load_config()  # must NOT raise
    assert cfg["llm_backend"] == "local"
    assert cfg["local_image_model"] == bc.DEFAULTS["local_image_model"]


def test_concurrent_saves_do_not_corrupt(tmp_path, monkeypatch):
    bc = _fresh_backend_config(tmp_path, monkeypatch)

    def writer(model):
        for _ in range(20):
            cfg = bc.load_config()
            cfg["local_image_model"] = model
            bc.save_config(cfg)

    threads = [threading.Thread(target=writer, args=(f"model-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Whatever the last writer wrote, the file must be valid, complete JSON.
    cfg = bc.load_config()
    assert cfg["llm_backend"] == "local"
    assert cfg["local_image_model"].startswith("model-")


# ---------------------------------------------------------------------------
# gpu_coord: watchdog fire is a no-op after stop()/done
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def test_fire_after_stop_is_noop():
    from gpu_coord import InactivityWatchdog
    proc = _FakeProc()
    wd = InactivityWatchdog(proc, timeout=1000)  # long timeout; we fire manually
    wd.stop()
    # A stray timer firing after stop() must not kill a healthy worker.
    wd._fire()
    assert proc.killed is False
    assert wd.fired is False


def test_fire_before_stop_kills():
    from gpu_coord import InactivityWatchdog
    proc = _FakeProc()
    wd = InactivityWatchdog(proc, timeout=1000)
    wd._fire()
    assert proc.killed is True
    assert wd.fired is True
    wd.stop()


def test_watchdog_expiry_kills_worker():
    from gpu_coord import InactivityWatchdog
    proc = _FakeProc()
    wd = InactivityWatchdog(proc, timeout=0.05)
    time.sleep(0.2)
    assert proc.killed is True
    assert wd.fired is True
    wd.stop()


def test_stop_before_expiry_prevents_kill():
    from gpu_coord import InactivityWatchdog
    proc = _FakeProc()
    wd = InactivityWatchdog(proc, timeout=0.2)
    wd.stop()
    time.sleep(0.4)
    assert proc.killed is False
    assert wd.fired is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
