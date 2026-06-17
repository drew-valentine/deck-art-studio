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
# Single resident model: {"id": str, "kind": "text"|"vision", "model", "aux"}
_resident = None


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
    """Free the resident MLX model and clear the Metal cache.

    Safe to call when nothing is loaded. Called before loading FLUX so the
    image transformer has the full memory budget.
    """
    global _resident
    with _lock:
        if _resident is None:
            return
        kind = _resident.get("kind")
        mid = _resident.get("id")
        _resident = None
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass
        print(f"[mlx] Unloaded {kind} model {mid}")


def _free_image_model():
    """Evict the resident FLUX image model before loading an LLM/VLM.

    Single-resident is symmetric on an 18 GB machine: loading FLUX unloads the
    text/vision model (see local_image_generator), and loading text/vision must
    unload FLUX — otherwise a preloaded FLUX (~12 GB) plus an LLM/VLM (~5 GB) would
    co-reside and OOM. Lazy import avoids a circular dependency.
    """
    try:
        import local_image_generator
        local_image_generator.get_generator().unload()
    except Exception:
        pass


def _ensure_text(model_id: str):
    """Load (and cache) an mlx-lm text model, unloading any other resident model."""
    global _resident
    if _resident and _resident["kind"] == "text" and _resident["id"] == model_id:
        return _resident["model"], _resident["aux"]
    unload()
    _free_image_model()
    from mlx_lm import load
    print(f"[mlx] Loading text model {model_id} ...")
    model, tokenizer = load(model_id)
    _resident = {"id": model_id, "kind": "text", "model": model, "aux": tokenizer}
    return model, tokenizer


def _ensure_vision(model_id: str):
    """Load (and cache) an mlx-vlm model, unloading any other resident model."""
    global _resident
    if _resident and _resident["kind"] == "vision" and _resident["id"] == model_id:
        return _resident["model"], _resident["aux"]
    unload()
    _free_image_model()
    from mlx_vlm import load
    print(f"[mlx] Loading vision model {model_id} ...")
    model, processor = load(model_id)
    _resident = {"id": model_id, "kind": "vision", "model": model, "aux": processor}
    return model, processor


def chat(messages: list[dict], model: str | None = None,
         max_tokens: int = 512, temperature: float = 0.7) -> str:
    """Run a chat completion with an MLX text model. Returns the reply string.

    `messages` is the OpenAI/ollama-style list of {role, content} dicts.
    `model` accepts an ollama name (mapped) or an MLX repo id.
    """
    model_id = resolve_text_model(model)
    with _lock:
        mdl, tok = _ensure_text(model_id)
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True)
        sampler = make_sampler(temp=float(temperature))
        text = generate(mdl, tok, prompt, max_tokens=max_tokens,
                        sampler=sampler, verbose=False)
    return (text or "").strip()


def vision(image_path: str, prompt: str, model: str | None = None,
           max_tokens: int = 400, temperature: float = 0.7) -> str:
    """Analyze an image with an MLX vision-language model. Returns the reply.

    Qwen2.5-VL (via PIL) handles PNG/JPG/WebP natively — no format conversion
    needed (unlike the old llava path).
    """
    model_id = resolve_vision_model(model)
    with _lock:
        mdl, processor = _ensure_vision(model_id)
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        formatted = apply_chat_template(processor, mdl.config, prompt, num_images=1)
        result = generate(mdl, processor, formatted, image=[str(image_path)],
                          max_tokens=max_tokens, temperature=float(temperature),
                          verbose=False)
    # mlx-vlm returns a GenerationResult; older/newer variants may return str.
    text = getattr(result, "text", result)
    return (text or "").strip()
