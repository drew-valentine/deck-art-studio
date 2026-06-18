#!/usr/bin/env python3
"""
MLX-native LLM + vision inference for Deck Art Studio (Apple Silicon only).

Replaces the Ollama/OpenAI text + vision backends with in-process MLX models:
  - text  → mlx-lm   (Llama 3.1/3.2 Instruct, 4-bit)
  - vision → mlx-vlm  (Qwen2.5-VL Instruct, 4-bit)

Design notes
------------
* **Lazy imports.** `mlx`, `mlx_lm`, and `mlx_vlm` are Apple-Silicon-only and are
  NOT installed on the Ubuntu CI runner. Every import of them lives inside a
  function so this module (and its callers, prompt_generator / vision_analyzer)
  still import cleanly on CI.
* **Single-resident cache.** On an 18 GB machine only one heavy model may sit in
  unified memory at a time — and none of them may be co-resident with FLUX. We
  cache exactly one (model, tokenizer/processor) keyed by model id; requesting a
  different id unloads the current one first. `unload()` is also called by the
  image generator before it loads FLUX.
* **GPU lock.** MLX generation is not reentrant; a module-level lock serializes
  load+generate so concurrent callers queue instead of corrupting GPU state.
"""

import threading

# --- ollama model name -> MLX (HuggingFace) repo id ------------------------
# Callers still pass the historical ollama names; map them to MLX 4-bit repos.
_TEXT_MODEL_MAP = {
    "llama3.2:3b": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "llama3.1:8b": "mlx-community/Llama-3.1-8B-Instruct-4bit",
}
_VISION_MODEL_MAP = {
    "llava:7b": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
}

DEFAULT_TEXT_MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"
DEFAULT_VISION_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"

_lock = threading.RLock()
# The text/vision models run in a SUBPROCESS (mlx_worker.py), not here — see
# mlx_worker.py for why (repeated in-process model swaps fragment the Metal heap
# and OOM-kill Flask). `_proc` is the live worker, or None.
_proc = None


def is_available() -> bool:
    """True if the MLX runtime can be imported (i.e. running on Apple Silicon)."""
    try:
        import mlx.core  # noqa: F401
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


def resolve_text_model(name: str | None) -> str:
    if not name:
        return DEFAULT_TEXT_MODEL
    if "/" in name:  # already an MLX repo id
        return name
    return _TEXT_MODEL_MAP.get(name, DEFAULT_TEXT_MODEL)


def resolve_vision_model(name: str | None) -> str:
    if not name:
        return DEFAULT_VISION_MODEL
    if "/" in name:
        return name
    return _VISION_MODEL_MAP.get(name, DEFAULT_VISION_MODEL)


def unload():
    """Terminate the MLX worker subprocess — the OS reclaims its full memory.

    This is the eviction primitive: like the FLUX worker, killing the process
    guarantees the text/vision models' GPU/wired memory is fully returned (no
    fragmentation residue). Called (a) before FLUX loads, and (b) by
    deck_studio's _ollama_work_done when a work session ends — so each style
    analysis runs in a FRESH worker process and never inherits the heap
    fragmentation that previously OOM-killed the 2nd analysis. Safe when nothing
    is running.
    """
    global _proc
    with _lock:
        if _proc is None:
            return
        proc = _proc
        _proc = None
        import json as _json
        try:
            proc.stdin.write(_json.dumps({"cmd": "shutdown"}) + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        print("[mlx] Terminated MLX worker (memory reclaimed by OS)")


def _free_image_model():
    """Evict the FLUX worker before loading the text/vision worker.

    Single-resident is symmetric: the FLUX worker (~13 GB) and this MLX worker
    (~5 GB) cannot co-reside on an 18 GB machine, so we kill FLUX first. Killing
    its process fully reclaims the GPU memory. Lazy import avoids a circular
    dependency.
    """
    try:
        import local_image_generator
        local_image_generator.get_generator().unload()
    except Exception:
        pass


def _worker_alive():
    return _proc is not None and _proc.poll() is None


def _ensure_worker():
    """Spawn the MLX worker subprocess (idempotent). Caller holds _lock."""
    global _proc
    if _worker_alive():
        return
    import os
    import subprocess
    import sys
    # The MLX worker is about to wire ~5 GB; evict the FLUX worker (~13 GB) first
    # so they don't co-reside and OOM.
    _free_image_model()
    here = os.path.dirname(os.path.abspath(__file__))
    _proc = subprocess.Popen(
        [sys.executable, "-u", os.path.join(here, "mlx_worker.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=None,  # worker logs flow to the Flask log
        text=True, bufsize=1, cwd=here, env=dict(os.environ),
    )
    print(f"[mlx] Spawned MLX worker subprocess (pid {_proc.pid})")


def _request(req: dict) -> str:
    """Send one request to the worker and return its text reply.

    Raises RuntimeError if the worker errors or dies. Resets the worker on death
    so the next call respawns a fresh process.
    """
    global _proc
    import json as _json
    import mlx_worker
    sentinel = mlx_worker.SENTINEL
    with _lock:
        _ensure_worker()
        try:
            _proc.stdin.write(_json.dumps(req) + "\n")
            _proc.stdin.flush()
        except Exception as e:
            _proc = None
            raise RuntimeError(f"MLX worker write failed: {e}")
        while True:
            line = _proc.stdout.readline()
            if line == "":
                code = _proc.poll()
                _proc = None
                raise RuntimeError(f"MLX worker exited unexpectedly (code {code})")
            line = line.rstrip("\n")
            if not line.startswith(sentinel):
                if line.strip():
                    print(line)  # library noise -> Flask log
                continue
            try:
                msg = _json.loads(line[len(sentinel):].strip())
            except Exception:
                continue
            if msg.get("error"):
                raise RuntimeError(f"MLX worker error: {msg['error']}")
            return (msg.get("text") or "").strip()


def chat(messages: list[dict], model: str | None = None,
         max_tokens: int = 512, temperature: float = 0.7) -> str:
    """Run a chat completion with an MLX text model (in the worker subprocess).

    `messages` is the OpenAI/ollama-style list of {role, content} dicts.
    `model` accepts an ollama name (mapped) or an MLX repo id.
    """
    return _request({
        "cmd": "chat", "messages": messages,
        "model": resolve_text_model(model),
        "max_tokens": int(max_tokens), "temperature": float(temperature),
    })


def vision(image_path: str, prompt: str, model: str | None = None,
           max_tokens: int = 400, temperature: float = 0.7) -> str:
    """Analyze an image with an MLX vision-language model (in the worker subprocess).

    Qwen2.5-VL (via PIL) handles PNG/JPG/WebP natively — no format conversion
    needed (unlike the old llava path).
    """
    return _request({
        "cmd": "vision", "image_path": str(image_path), "prompt": prompt,
        "model": resolve_vision_model(model),
        "max_tokens": int(max_tokens), "temperature": float(temperature),
    })
