#!/usr/bin/env python3
"""
MLX-native image generation for Deck Art Studio (Apple Silicon only).

Backed by **mflux** running **FLUX.1-schnell** (4-bit) on the Apple GPU. This
replaces the previous PyTorch-MPS Stable Diffusion (SDXL Turbo / Lightning /
Hyper-SD) + IP-Adapter pipeline.

Why the API looks the way it does
---------------------------------
`deck_studio.py` drives this module through a stable surface — `get_generator()`,
`LocalImageGenerator.{load_model, unload, generate, generate_with_reference,
get_status, is_loaded, active_model}`, `LOCAL_MODELS`, `check_dependencies()` —
so those names are preserved even though the engine underneath changed entirely.

FLUX vs. SDXL, the important differences
----------------------------------------
* **Prompts:** FLUX uses a T5 text encoder with a large (~512) token budget and
  follows natural-language prompts far better than SDXL's 77-token CLIP. The old
  aggressive prompt truncation and the IP-Adapter cross-attention style transfer
  are gone — style now rides in the text (the distilled `style_tags`), optionally
  reinforced by img2img off the Scryfall crop.
* **Memory (18 GB):** the FLUX transformer cannot be co-resident with the
  mlx-lm/mlx-vlm models. `load_model()` unloads them (via mlx_llm) before loading
  FLUX. All MLX imports are lazy so this module imports on non-Mac CI.
* **schnell:** guidance-distilled — `guidance` is effectively ignored and it needs
  no negative prompt; 2-4 steps is the sweet spot.

Dependencies (Mac only): pip install -r requirements-mac.txt
"""

import gc
import threading
from typing import Optional


# ---------------------------------------------------------------------------
# Lazy dependency check
# ---------------------------------------------------------------------------
_DEPS_AVAILABLE: Optional[bool] = None


def check_dependencies() -> tuple[bool, str]:
    """Return (available, message). True when mflux (MLX) can be imported."""
    global _DEPS_AVAILABLE
    if _DEPS_AVAILABLE is not None:
        msg = ("mflux (MLX) available" if _DEPS_AVAILABLE
               else "mflux not installed — pip install -r requirements-mac.txt")
        return _DEPS_AVAILABLE, msg
    try:
        import mflux  # noqa: F401
        import mlx.core  # noqa: F401
        _DEPS_AVAILABLE = True
        return True, "mflux (MLX) available"
    except Exception as e:
        _DEPS_AVAILABLE = False
        return False, ("mflux not available — install the Mac deps: "
                       f"pip install -r requirements-mac.txt ({e})")


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
# Keys here are referenced by MODEL_OPTIONS[...]['model'] in deck_studio.py.
LOCAL_MODELS = {
    "flux-schnell-4bit": {
        "mflux_name": "schnell",
        "quantize": 4,
        # The official black-forest-labs/FLUX.1-schnell repo is GATED (needs an
        # HF login + license acceptance) and ships full fp16 weights (~24 GB) that
        # mflux would quantize on the fly — a memory spike that's tight on 18 GB.
        # Default instead to a non-gated, already-4-bit mflux mirror: ~6-9 GB
        # download, no auth, no quantization spike. Override via env if desired.
        "repo": "dhairyashil/FLUX.1-schnell-mflux-4bit",
        "default_steps": 4,
        "recommended_size": (1024, 1024),
        "supports_img2img": True,
        "default_strength": 0.45,   # img2img: lower = freer, higher = closer to ref
        "memory_gb": 12,
        "description": "FLUX.1 schnell (4-bit). High quality, ~40-70s on M3 Pro. img2img + txt2img.",
    },
    "flux-schnell-8bit": {
        "mflux_name": "schnell",
        "quantize": 8,
        # No non-gated 8-bit mirror — uses the gated official repo (needs HF auth).
        "repo": None,
        "default_steps": 4,
        "recommended_size": (1024, 1024),
        "supports_img2img": True,
        "default_strength": 0.45,
        "memory_gb": 16,
        "description": "FLUX.1 schnell (8-bit). Best quality, heavier — tight on 18GB. Needs HF login.",
    },
}

DEFAULT_LOCAL_MODEL = "flux-schnell-4bit"

# Faithful-composition mode: Canny ControlNet locks each card's composition from
# the Scryfall art's edges while the prompt owns the style. The dev-trained Canny
# adapter needs more than schnell's 4 steps to render fully — 14 is the sweet spot
# (under-renders below ~10; diminishing returns above). Non-gated weights
# (InstantX/FLUX.1-dev-Controlnet-Canny + the schnell base mirror).
CONTROLNET_STEPS = 14
CONTROLNET_DEFAULT_STRENGTH = 0.45

# Historical constant some call sites still import; no IP-Adapter under FLUX.
IP_ADAPTER_STYLE_SCALE_REDUCED = 0.0


class LocalImageGenerator:
    """Owns ONE resident FLUX model — either the txt2img variant (fast, default)
    or the Canny ControlNet variant (faithful composition). They can't co-reside
    on 18 GB, so requesting the other swaps it in."""

    def __init__(self):
        self._flux = None          # txt2img Flux1
        self._controlnet = None    # Flux1Controlnet (Canny)
        self._active_model: Optional[str] = None  # LOCAL_MODELS key
        self._device: Optional[str] = "gpu"
        self._lock = threading.RLock()
        # Retained for call-site compatibility (no IP-Adapter under FLUX).
        self._ip_adapter_loaded = False

    # --- status -----------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._flux is not None or self._controlnet is not None

    @property
    def active_model(self) -> Optional[str]:
        return self._active_model

    def get_device(self) -> str:
        return "gpu"  # MLX runs on the Apple GPU

    def is_model_cached(self, model_key: str) -> bool:
        """Best-effort: whether FLUX schnell weights are already in the HF cache.

        Lets the UI warn that loading will trigger a large first download.
        Returns False (assume download) if it can't tell.
        """
        if model_key not in LOCAL_MODELS:
            return False
        try:
            from huggingface_hub import scan_cache_dir
            repos = {r.repo_id for r in scan_cache_dir().repos}
            return any("FLUX.1-schnell" in r for r in repos)
        except Exception:
            return False

    def get_status(self) -> dict:
        """Status dict for the UI (keeps the legacy key shape)."""
        available, dep_msg = check_dependencies()
        models_cached = {}
        if available:
            for key in LOCAL_MODELS:
                models_cached[key] = self.is_model_cached(key)
        return {
            "available": available,
            "dependencies_installed": available,
            "dependencies_message": dep_msg,
            "device": self._device or "gpu",
            "active_model": self._active_model,
            "is_loaded": self.is_loaded,
            "models": {
                key: {
                    "description": info["description"],
                    "memory_gb": info["memory_gb"],
                    "cached": models_cached.get(key, False),
                    "supports_img2img": info["supports_img2img"],
                    "recommended_size": f"{info['recommended_size'][0]}x{info['recommended_size'][1]}",
                }
                for key, info in LOCAL_MODELS.items()
            },
        }

    # --- load / unload ----------------------------------------------------
    def load_model(self, model_key: str, progress_callback=None,
                   download_progress_callback=None) -> tuple[bool, str]:
        """Load a FLUX model. Returns (success, message).

        First run downloads FLUX.1-schnell weights (~24 GB) from the HF hub and
        quantizes them; they stay cached for subsequent runs. The mlx-lm/mlx-vlm
        text/vision models are unloaded first so FLUX gets the full memory budget.
        """
        with self._lock:
            if model_key not in LOCAL_MODELS:
                return False, f"Unknown model: {model_key}. Available: {list(LOCAL_MODELS.keys())}"

            if self._active_model == model_key and self._flux is not None:
                if progress_callback:
                    progress_callback(f"{model_key} already loaded")
                return True, f"{model_key} already loaded"

            available, dep_msg = check_dependencies()
            if not available:
                return False, dep_msg

            cfg = LOCAL_MODELS[model_key]

            # Free unified memory: drop any other FLUX model AND the MLX LLM/VLM.
            self.unload()
            try:
                import mlx_llm
                mlx_llm.unload()
            except Exception:
                pass

            try:
                import os
                from mflux.models.flux.variants.txt2img.flux import Flux1
                from mflux.models.common.config.model_config import ModelConfig
                repo = os.environ.get("MFLUX_SCHNELL_REPO") or cfg.get("repo")
                if progress_callback:
                    progress_callback(
                        f"Loading FLUX ({cfg['mflux_name']}, {cfg['quantize']}-bit) — "
                        + ("first run downloads ~6-9 GB (pre-quantized)."
                           if repo else
                           "first run downloads ~24 GB and quantizes; needs HF login.")
                    )
                if repo:
                    # Pre-quantized mflux mirror (or local dir) loaded via model_path.
                    self._flux = Flux1(
                        model_config=ModelConfig.schnell(),
                        quantize=cfg["quantize"],
                        model_path=repo,
                    )
                else:
                    # Official (gated) repo; quantizes on the fly. Needs HF auth.
                    self._flux = Flux1.from_name(cfg["mflux_name"], quantize=cfg["quantize"])
                self._active_model = model_key
                msg = f"Loaded {model_key}"
                print(f"[flux] {msg}")
                if progress_callback:
                    progress_callback(f"{model_key} ready")
                return True, msg
            except Exception as e:
                self._flux = None
                self._active_model = None
                import traceback
                traceback.print_exc()
                msg = f"Failed to load {model_key}: {e}"
                print(f"[flux] {msg}")
                return False, msg

    def unload(self):
        """Free the resident FLUX model(s) and clear the Metal cache."""
        with self._lock:
            if self._flux is None and self._controlnet is None:
                return
            self._flux = None
            self._controlnet = None
            self._active_model = None
            gc.collect()
            try:
                import mlx.core as mx
                mx.clear_cache()
            except Exception:
                pass
            print("[flux] Unloaded image model")

    def _ensure_controlnet(self, model_key):
        """Load the Canny ControlNet variant, swapping out the txt2img model."""
        if self._controlnet is not None:
            return
        # Free the txt2img model + MLX LLM/VLM first (can't co-reside on 18GB).
        self._flux = None
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass
        try:
            import mlx_llm
            mlx_llm.unload()
        except Exception:
            pass
        cfg = LOCAL_MODELS[model_key]
        import os
        from mflux.models.flux.variants.controlnet.flux_controlnet import Flux1Controlnet
        from mflux.models.common.config.model_config import ModelConfig
        repo = os.environ.get("MFLUX_SCHNELL_REPO") or cfg.get("repo")
        print(f"[flux] Loading Canny ControlNet for {model_key} (first run downloads ~3 GB) ...")
        self._controlnet = Flux1Controlnet(
            model_config=ModelConfig.schnell_controlnet_canny(),
            quantize=cfg["quantize"],
            model_path=repo,
        )
        self._active_model = model_key

    # --- generation -------------------------------------------------------
    def _register_progress(self, model, progress_callback, total_steps):
        """Attach an in-loop progress subscriber to a FLUX model, if possible."""
        if not progress_callback or model is None:
            return
        cb = progress_callback

        class _Progress:
            def call_in_loop(self, t, seed, prompt, latents, config, time_steps):
                try:
                    cb(int(t) + 1, total_steps)
                except Exception:
                    pass

        try:
            model.callbacks.register(_Progress())
        except Exception:
            pass

    def generate(self, prompt: str, negative_prompt: str = "",
                 width: Optional[int] = None, height: Optional[int] = None,
                 steps: Optional[int] = None, guidance: Optional[float] = None,
                 seed: Optional[int] = None,
                 control_image_path=None, controlnet_strength: Optional[float] = None,
                 reference_image_path=None, image_strength: Optional[float] = None,
                 progress_callback=None, **_ignored):
        """Generate an image with FLUX. Returns a PIL.Image.

        Two modes:
        * **txt2img** (default, fast ~4 steps): style + composition from the prompt.
        * **Canny ControlNet** (when `control_image_path` is given): locks the
          composition to the reference image's edges while the prompt owns the
          style. Renders at CONTROLNET_STEPS (~14) so it's slower but faithful.

        schnell ignores guidance / negative prompt (accepted but unused). Legacy
        img2img + IP-Adapter kwargs are accepted for compatibility but unused.
        """
        import time
        with self._lock:
            model_key = self._active_model or DEFAULT_LOCAL_MODEL
            cfg = LOCAL_MODELS.get(model_key, {})
            rw, rh = cfg.get("recommended_size", (1024, 1024))
            w = int(width or rw)
            h = int(height or rh)

            # ---- Faithful mode: Canny ControlNet off the reference edges ----
            if control_image_path:
                self._ensure_controlnet(model_key)
                n_steps = int(steps or CONTROLNET_STEPS)
                cs = float(controlnet_strength if controlnet_strength is not None
                           else CONTROLNET_DEFAULT_STRENGTH)
                if seed is None:
                    seed = abs(hash((prompt, w, h, n_steps, "cn"))) % (2**31)
                self._register_progress(self._controlnet, progress_callback, n_steps)
                print(f"[flux] controlnet(canny, strength={cs}) {w}x{h}, steps={n_steps}: {prompt[:80]}")
                start = time.time()
                result = self._controlnet.generate_image(
                    seed=int(seed), prompt=prompt,
                    controlnet_image_path=str(control_image_path),
                    num_inference_steps=n_steps, width=w, height=h,
                    controlnet_strength=cs,
                )
                print(f"[flux] Generated (controlnet) in {time.time() - start:.1f}s")
                return result.image

            # ---- Fast mode: txt2img ----
            if self._flux is None:
                # A controlnet model may be resident; swap back to txt2img.
                # (No progress_callback here — it's the step callback, not a
                # load-message callback.)
                ok, msg = self.load_model(model_key)
                if not ok:
                    raise RuntimeError(f"No FLUX model loaded: {msg}")
            n_steps = int(steps or cfg.get("default_steps", 4))
            if seed is None:
                seed = abs(hash((prompt, w, h, n_steps))) % (2**31)
            self._register_progress(self._flux, progress_callback, n_steps)
            print(f"[flux] txt2img {w}x{h}, steps={n_steps}: {prompt[:90]}")
            start = time.time()
            result = self._flux.generate_image(
                seed=int(seed), prompt=prompt,
                num_inference_steps=n_steps, width=w, height=h,
            )
            print(f"[flux] Generated in {time.time() - start:.1f}s")
            return result.image

    def generate_with_reference(self, prompt: str, reference_image_path,
                                strength: Optional[float] = None,
                                negative_prompt: str = "",
                                width: Optional[int] = None, height: Optional[int] = None,
                                steps: Optional[int] = None, guidance: Optional[float] = None,
                                seed: Optional[int] = None,
                                progress_callback=None, **_ignored):
        """img2img convenience wrapper around generate()."""
        from pathlib import Path
        if reference_image_path and not Path(reference_image_path).exists():
            print(f"[flux] Reference not found: {reference_image_path}, using txt2img")
            reference_image_path = None
        return self.generate(
            prompt=prompt,
            width=width, height=height, steps=steps, seed=seed,
            reference_image_path=reference_image_path,
            image_strength=strength,
            progress_callback=progress_callback,
        )

    def encode_style_image(self, pil_image, cache_key=None):
        """No-op under FLUX (IP-Adapter removed). Kept for call-site compatibility."""
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_generator: Optional[LocalImageGenerator] = None
_generator_lock = threading.Lock()


def get_generator() -> LocalImageGenerator:
    """Get or create the module-level generator singleton."""
    global _generator
    with _generator_lock:
        if _generator is None:
            _generator = LocalImageGenerator()
    return _generator
