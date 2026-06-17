#!/usr/bin/env python3
"""
Backend configuration for Deck Art Studio (MLX-native, Apple Silicon only).

The pipeline is fully local and MLX-based:
  - text  → mlx-lm   (prompt generation, style/subject distillation)
  - vision → mlx-vlm  (inspiration image style analysis)
  - image → mflux    (FLUX.1 image generation)

This module just persists model selections to backend_config.json. The old
OpenAI-cloud / Ollama-server lifecycle has been removed — MLX models are loaded
in-process and downloaded lazily from the HuggingFace hub on first use (see
mlx_llm.py and local_image_generator.py).
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "backend_config.json"

DEFAULTS = {
    # Retained for call-site compatibility; the pipeline is always MLX/local now.
    "llm_backend": "local",
    "ollama_model": "llama3.1:8b",      # mapped to an MLX repo by mlx_llm.py
    "ollama_vision_model": "llava:7b",  # mapped to an MLX repo by mlx_llm.py
    "local_image_model": "flux-schnell-4bit",
    "local_image_autoload": False,      # auto-load image model on startup
}


def load_config() -> dict:
    """Load backend config from disk, filling in defaults for missing keys."""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception as e:
            print(f"[backend] Could not load config: {e}")
    # The cloud backend is gone — force any legacy "openai" value to local.
    if cfg.get("llm_backend") != "local":
        cfg["llm_backend"] = "local"
    return cfg


def save_config(cfg: dict):
    """Persist backend config to disk."""
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def get_mlx_status() -> dict:
    """Report MLX text/vision availability for the UI.

    Keeps the legacy key shape (installed/running/models/models_ready) so the
    frontend's backend panel keeps working; on Apple Silicon the MLX runtime is
    always "installed and running" in-process, with models pulled lazily.
    """
    try:
        import mlx_llm
        available = mlx_llm.is_available()
    except Exception:
        available = False

    cfg = load_config()
    needed_models = [cfg["ollama_model"], cfg["ollama_vision_model"]]
    return {
        "backend": "mlx",
        "installed": available,
        "running": available,
        "models": needed_models if available else [],
        "needed_models": needed_models,
        "models_ready": available,
    }


# ---------------------------------------------------------------------------
# Local image generation (mflux / FLUX)
# ---------------------------------------------------------------------------

def check_diffusers_installed() -> bool:
    """Check if the MLX image backend (mflux) is available."""
    try:
        from local_image_generator import check_dependencies
        available, _ = check_dependencies()
        return available
    except ImportError:
        return False


def get_local_image_status() -> dict:
    """Get status of local image generation capabilities."""
    try:
        from local_image_generator import get_generator, check_dependencies, LOCAL_MODELS
        available, dep_msg = check_dependencies()
        if not available:
            return {
                "available": False,
                "message": dep_msg,
                "models": {},
            }

        gen = get_generator()
        return gen.get_status()
    except ImportError:
        return {
            "available": False,
            "message": "local_image_generator module not found",
            "models": {},
        }


def activate_local_image_model(model_key: str, progress_callback=None, download_progress_callback=None) -> tuple[bool, str]:
    """Load a local image model. Returns (success, message).

    Triggers model download (~5GB) on first use.
    """
    from local_image_generator import get_generator, check_dependencies

    available, dep_msg = check_dependencies()
    if not available:
        return False, dep_msg

    gen = get_generator()
    success, msg = gen.load_model(
        model_key,
        progress_callback=progress_callback,
        download_progress_callback=download_progress_callback,
    )

    if success:
        cfg = load_config()
        cfg["local_image_model"] = model_key
        save_config(cfg)

    return success, msg
