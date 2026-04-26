#!/usr/bin/env python3
"""
Backend configuration for Deck Art Studio.

Manages switching between OpenAI (cloud) and local (Ollama/Diffusers) backends
for LLM prompt generation, vision analysis, and image generation. Persists
settings to backend_config.json. Handles Ollama server lifecycle automatically.
"""

import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "backend_config.json"

DEFAULTS = {
    "llm_backend": "openai",          # "openai" or "local"
    "ollama_model": "llama3.2:3b",     # model for prompt generation
    "ollama_vision_model": "llava:7b", # model for vision/style analysis
    "local_image_model": "sdxl-turbo", # local image generation model
    "local_image_autoload": False,     # auto-load local image model on startup
}

_ollama_process = None  # track the subprocess we started


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
    return cfg


def save_config(cfg: dict):
    """Persist backend config to disk."""
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def check_ollama_installed() -> bool:
    """Check if the ollama binary is available on PATH."""
    return shutil.which("ollama") is not None


def is_ollama_running() -> bool:
    """Check if the Ollama server is responding."""
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama_running() -> bool:
    """Start the Ollama server if it's not already running.

    Returns True if the server is available after this call.
    """
    global _ollama_process

    if is_ollama_running():
        return True

    if not check_ollama_installed():
        print("[backend] Ollama is not installed. Install with: brew install ollama")
        return False

    print("[backend] Starting Ollama server...")
    try:
        _ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[backend] Failed to start Ollama: {e}")
        return False

    # Wait for server to be ready (up to 10 seconds)
    for _ in range(20):
        time.sleep(0.5)
        if is_ollama_running():
            print("[backend] Ollama server started successfully")
            return True

    print("[backend] Ollama server did not start within 10 seconds")
    return False


def list_installed_models() -> list[str]:
    """Return list of model names currently installed in Ollama."""
    try:
        import ollama
        response = ollama.list()
        return [m.model for m in response.models]
    except Exception as e:
        print(f"[backend] Could not list Ollama models: {e}")
        return []


def ensure_models_pulled(models: list[str], progress_callback=None) -> dict:
    """Pull any missing models. Returns status dict per model.

    Args:
        models: List of model names to ensure are available.
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        Dict like {"llama3.1:8b": "already_installed", "llava:7b": "pulled"}
    """
    import ollama

    installed = set(list_installed_models())
    result = {}

    for model in models:
        if model in installed:
            result[model] = "already_installed"
            continue

        msg = f"Pulling {model} (this may take a few minutes on first run)..."
        print(f"[backend] {msg}")
        if progress_callback:
            progress_callback(msg)

        try:
            ollama.pull(model)
            result[model] = "pulled"
            print(f"[backend] {model} pulled successfully")
        except Exception as e:
            result[model] = f"error: {e}"
            print(f"[backend] Failed to pull {model}: {e}")

    return result


def get_ollama_status() -> dict:
    """Get comprehensive Ollama status for the UI."""
    installed = check_ollama_installed()
    running = is_ollama_running() if installed else False
    models = list_installed_models() if running else []

    cfg = load_config()
    needed_models = [cfg["ollama_model"], cfg["ollama_vision_model"]]
    models_ready = all(m in models for m in needed_models)

    return {
        "installed": installed,
        "running": running,
        "models": models,
        "needed_models": needed_models,
        "models_ready": models_ready,
    }


def activate_local_backend(progress_callback=None) -> tuple[bool, str]:
    """Activate the local backend: start Ollama and ensure models are pulled.

    Returns (success: bool, message: str).
    """
    if not check_ollama_installed():
        return False, "Ollama is not installed. Install with: brew install ollama"

    if not ensure_ollama_running():
        return False, "Could not start Ollama server"

    cfg = load_config()
    needed = [cfg["ollama_model"], cfg["ollama_vision_model"]]
    results = ensure_models_pulled(needed, progress_callback=progress_callback)

    failures = {m: r for m, r in results.items() if r.startswith("error")}
    if failures:
        return False, f"Failed to pull models: {failures}"

    cfg["llm_backend"] = "local"
    save_config(cfg)
    return True, "Local backend activated"


# ---------------------------------------------------------------------------
# Local image generation (Diffusers + MPS)
# ---------------------------------------------------------------------------

def check_diffusers_installed() -> bool:
    """Check if torch + diffusers are available."""
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
