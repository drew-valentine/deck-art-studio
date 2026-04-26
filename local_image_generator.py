#!/usr/bin/env python3
"""
Local image generation using Stable Diffusion via HuggingFace Diffusers.

Supports multiple models optimized for different speed/quality tradeoffs:
  - SDXL Lightning 4-step (recommended: fast + high quality at 768x1024)
  - Hyper-SD 2-step (fastest SDXL option)
  - SDXL Turbo (4-step, 512x768)
  - SD 1.5 (30-step fallback, 512x512)

Optimized for Apple Silicon (MPS) with memory-efficient settings.

Dependencies (optional, not in requirements.txt):
    pip install torch torchvision diffusers transformers accelerate peft
"""

import gc
import os
import time
from pathlib import Path
from typing import Optional

# Must be set before any torch import
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


# ---------------------------------------------------------------------------
# Lazy dependency check
# ---------------------------------------------------------------------------
_DEPS_AVAILABLE: Optional[bool] = None


def check_dependencies() -> tuple[bool, str]:
    """Check if torch + diffusers are installed. Caches result after first call."""
    global _DEPS_AVAILABLE
    if _DEPS_AVAILABLE is not None:
        msg = ("Dependencies available" if _DEPS_AVAILABLE
               else "Missing: pip install torch diffusers transformers accelerate peft")
        return _DEPS_AVAILABLE, msg
    try:
        import torch
        import diffusers
        _DEPS_AVAILABLE = True
        return True, f"torch {torch.__version__}, diffusers {diffusers.__version__}"
    except ImportError as e:
        _DEPS_AVAILABLE = False
        return False, (f"Missing dependency: {e}. "
                       "Install with: pip install torch diffusers transformers accelerate peft")


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
LOCAL_MODELS = {
    "sdxl-lightning-4step": {
        "repo_id": "ByteDance/SDXL-Lightning",
        "base_model": "stabilityai/stable-diffusion-xl-base-1.0",
        "lora_file": "sdxl_lightning_4step_lora.safetensors",
        "scheduler": "euler_trailing",
        "default_steps": 4,
        "ip_adapter_steps": 10,  # More steps when IP-Adapter active — subject fidelity needs steps
        "default_guidance": 1.5,  # Non-zero improves prompt adherence
        "ip_adapter_guidance": 3.5,  # High CFG: text drives subject + style, IP-Adapter adds texture
        "default_strength": 0.65,  # Lightning 4-step: 0.65 ≈ 3 denoising steps (enough to restyle)
        "img2img_guidance": 2.5,   # Boosted guidance for img2img style adherence
        "recommended_size": (768, 1024),
        "dtype": "float16",  # float16 safe on MPS since PyTorch ~2.3+
        "memory_gb": 6,
        "description": "Fast + high quality at 768x1024. Best balance for Apple Silicon.",
    },
    "hyper-sdxl-2step": {
        "repo_id": "ByteDance/Hyper-SD",
        "base_model": "stabilityai/stable-diffusion-xl-base-1.0",
        "lora_file": "Hyper-SDXL-2steps-lora.safetensors",
        "scheduler": "ddim_trailing",
        "default_steps": 2,
        "ip_adapter_steps": 6,  # More steps when IP-Adapter active — cleans up linework
        "default_guidance": 1.0,  # Conservative for 2-step, improves prompt adherence
        "ip_adapter_guidance": 2.5,  # Boosted for text control when IP-Adapter is active
        "default_strength": 0.50,  # Hyper 2-step: 0.50 is safest
        "recommended_size": (768, 1024),
        "dtype": "float16",
        "memory_gb": 6,
        "description": "Fastest SDXL option — 2 steps at 768x1024.",
    },
    "sdxl-turbo": {
        "repo_id": "stabilityai/sdxl-turbo",
        "default_steps": 4,
        "ip_adapter_steps": 10,  # More steps when IP-Adapter active — subject fidelity needs steps
        "default_guidance": 1.5,  # Non-zero improves prompt adherence
        "ip_adapter_guidance": 3.5,  # High CFG: text drives subject + style, IP-Adapter adds texture
        "default_strength": 0.65,  # 0.65 ≈ 3 denoising steps (enough to restyle)
        "img2img_guidance": 2.5,   # Boosted guidance for img2img style adherence
        "recommended_size": (512, 768),
        "dtype": "float16",  # float16 safe on MPS since PyTorch ~2.3+
        "memory_gb": 5,
        "description": "Fast (~45-60s) at 512x768. Original fast model.",
    },
}

DEFAULT_NEGATIVE_PROMPT = (
    "text, words, letters, card frame, border, watermark, "
    "low quality, blurry, deformed"
)

# ---------------------------------------------------------------------------
# HuggingFace download progress interception
# ---------------------------------------------------------------------------

def _load_with_download_progress(from_pretrained_fn, kwargs, download_progress_callback=None):
    """Call a HuggingFace from_pretrained() with download progress reporting.

    Monkey-patches huggingface_hub's tqdm class to intercept download progress.
    The callback receives (downloaded_bytes, total_bytes, description).
    Thread-safe: only one model load happens at a time (guarded by model_load_progress).
    """
    if not download_progress_callback:
        return from_pretrained_fn(**kwargs)

    # Track cumulative progress across multiple file downloads
    cumulative = {'downloaded': 0, 'total': 0, 'bars': {}}

    try:
        from tqdm.auto import tqdm as base_tqdm
        import huggingface_hub.utils._tqdm as hf_tqdm_mod

        class ProgressTqdm(base_tqdm):
            def __init__(self, *args, **tqdm_kwargs):
                super().__init__(*args, **tqdm_kwargs)
                self._bar_id = id(self)
                self._last_report_pct = -5  # force first report
                if self.total and self.total > 1024 * 1024:  # Only track files > 1MB
                    cumulative['bars'][self._bar_id] = {'n': 0, 'total': self.total}
                    cumulative['total'] = sum(b['total'] for b in cumulative['bars'].values())

            def update(self, n=1):
                super().update(n)
                if self._bar_id not in cumulative['bars']:
                    return
                cumulative['bars'][self._bar_id]['n'] = self.n
                agg_downloaded = sum(b['n'] for b in cumulative['bars'].values())
                agg_total = cumulative['total']
                if agg_total > 0:
                    pct = agg_downloaded / agg_total * 100
                    if pct - self._last_report_pct >= 2 or agg_downloaded >= agg_total:
                        self._last_report_pct = pct
                        try:
                            download_progress_callback(agg_downloaded, agg_total, self.desc or '')
                        except Exception:
                            pass

            def close(self):
                super().close()
                cumulative['bars'].pop(self._bar_id, None)

        original_tqdm = hf_tqdm_mod.tqdm
        hf_tqdm_mod.tqdm = ProgressTqdm
        try:
            return from_pretrained_fn(**kwargs)
        finally:
            hf_tqdm_mod.tqdm = original_tqdm

    except (ImportError, AttributeError):
        # If tqdm interception fails, fall back to normal loading
        return from_pretrained_fn(**kwargs)


# ---------------------------------------------------------------------------
# Generator class
# ---------------------------------------------------------------------------
IP_ADAPTER_REPO = "h94/IP-Adapter"
IP_ADAPTER_SUBFOLDER = "sdxl_models"
IP_ADAPTER_WEIGHT = "ip-adapter-plus_sdxl_vit-h.safetensors"
IP_ADAPTER_ENCODER_SUBFOLDER = "models/image_encoder"

# InstantStyle: target style blocks — not composition/layout
# up.block_0 = style (color, texture, mood).
#
# REVERSED gradient: the 3 cross-attention layers in up.block_0 go from
# deepest (most semantic — character identity) to shallowest (most textural —
# color, linework, patterns). Old code used strong→weak which maximized
# character cloning. Reversed gradient uses weak→strong to suppress character
# identity while maximizing texture/style transfer.
#
# Combined with rich text prompts that carry style via CLIP semantics,
# IP-Adapter now provides subtle texture/palette reference only.
IP_ADAPTER_STYLE_SCALE = {"up": {"block_0": [0.15, 0.30, 0.50]}}
IP_ADAPTER_STYLE_SCALE_REDUCED = {"up": {"block_0": [0.10, 0.20, 0.40]}}


class LocalImageGenerator:
    """Manages Stable Diffusion pipelines with lazy loading and MPS optimization."""

    def __init__(self):
        self._pipeline = None
        self._img2img_pipeline = None
        self._active_model: Optional[str] = None
        self._active_base_model: Optional[str] = None
        self._device: Optional[str] = None
        self._ip_adapter_loaded = False
        self._cached_ip_embeds = None       # Pre-encoded CLIP embeddings for IP-Adapter
        self._cached_ip_embeds_key = None   # Cache key (inspiration composite hash)

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    @property
    def active_model(self) -> Optional[str]:
        return self._active_model

    def get_device(self) -> str:
        """Detect best available device: mps > cuda > cpu."""
        if self._device:
            return self._device
        import torch
        if torch.backends.mps.is_available():
            self._device = "mps"
        elif torch.cuda.is_available():
            self._device = "cuda"
        else:
            self._device = "cpu"
        return self._device

    def is_model_cached(self, model_key: str) -> bool:
        """Check if model weights are already downloaded.

        For LoRA models, checks both the base SDXL model and the LoRA repo.
        """
        model_info = LOCAL_MODELS.get(model_key)
        if not model_info:
            return False
        try:
            from huggingface_hub import scan_cache_dir
            cache = scan_cache_dir()
            cached_repos = {repo.repo_id for repo in cache.repos}

            if "base_model" in model_info:
                # LoRA model: need both base + LoRA repo
                return (model_info["base_model"] in cached_repos
                        and model_info["repo_id"] in cached_repos)
            else:
                return model_info["repo_id"] in cached_repos
        except Exception:
            pass
        return False

    def load_model(self, model_key: str, progress_callback=None, download_progress_callback=None) -> tuple[bool, str]:
        """Load a model (downloading if needed). Returns (success, message).

        Call this explicitly before generate() — never auto-load during generation.
        Handles both standard models (SDXL Turbo, SD 1.5) and LoRA-based models
        (SDXL Lightning, Hyper-SD) which load SDXL base + apply a LoRA adapter.
        """
        if self._active_model == model_key and self._pipeline is not None:
            return True, f"{model_key} already loaded"

        model_info = LOCAL_MODELS.get(model_key)
        if not model_info:
            return False, f"Unknown model: {model_key}. Available: {list(LOCAL_MODELS.keys())}"

        available, dep_msg = check_dependencies()
        if not available:
            return False, dep_msg

        import torch

        device = self.get_device()
        use_fp16 = model_info["dtype"] == "float16"
        dtype = torch.float16 if use_fp16 else torch.float32

        # Hot-swap: if switching between LoRA models with the same base,
        # keep the base on-device and just swap the LoRA weights (~2-5s vs ~30-60s).
        new_base = model_info.get("base_model")
        can_hot_swap = (
            self._pipeline is not None
            and self._active_base_model is not None
            and new_base is not None
            and new_base == self._active_base_model
            and "lora_file" in model_info
        )

        if can_hot_swap:
            try:
                if progress_callback:
                    progress_callback(f"Hot-swapping LoRA to {model_key}...")
                self._hot_swap_lora(model_info, progress_callback)

                from diffusers import AutoPipelineForImage2Image
                self._img2img_pipeline = AutoPipelineForImage2Image.from_pipe(self._pipeline)
                self._active_model = model_key

                msg = f"Hot-swapped to {model_key} on {device}"
                print(f"[local_img] {msg}")
                if progress_callback:
                    progress_callback(msg)
                return True, msg

            except Exception as e:
                print(f"[local_img] Hot-swap failed, falling back to full reload: {e}")

        # Full load path
        self.unload()

        if progress_callback:
            progress_callback(f"Loading {model_key} (may download ~5GB on first run)...")

        try:
            if "lora_file" in model_info:
                self._load_lora_model(model_info, device, dtype, progress_callback, download_progress_callback)
            else:
                self._load_standard_model(model_info, device, dtype, progress_callback, download_progress_callback)

            # Disable NSFW safety checker (false positives on fantasy art)
            if hasattr(self._pipeline, 'safety_checker'):
                self._pipeline.safety_checker = None

            # Load IP-Adapter for style transfer BEFORE enabling attention
            # slicing — slicing sets up SlicedAttnProcessor which conflicts
            # with IP-Adapter's attention processor replacement.
            self._load_ip_adapter(progress_callback)

            # Memory optimizations (after IP-Adapter to avoid processor conflict)
            if hasattr(self._pipeline, 'enable_vae_slicing'):
                self._pipeline.enable_vae_slicing()
            # NOTE: enable_attention_slicing() is intentionally omitted —
            # it conflicts with IP-Adapter's cross-attention processors.
            # NOTE: torch.compile() and enable_vae_tiling() both produce
            # garbled output on MPS (Apple Silicon) — do not enable.

            # img2img pipeline sharing same model weights (including IP-Adapter)
            from diffusers import AutoPipelineForImage2Image
            self._img2img_pipeline = AutoPipelineForImage2Image.from_pipe(self._pipeline)

            self._active_model = model_key

            msg = f"Loaded {model_key} on {device}"
            print(f"[local_img] {msg}")
            if progress_callback:
                progress_callback(msg)

            return True, msg

        except Exception as e:
            self.unload()
            msg = f"Failed to load {model_key}: {e}"
            print(f"[local_img] {msg}")
            return False, msg

    def _load_standard_model(self, model_info, device, dtype, progress_callback=None, download_progress_callback=None):
        """Load a standard model (SDXL Turbo, SD 1.5) via AutoPipeline."""
        import torch
        from diffusers import AutoPipelineForText2Image

        print(f"[local_img] Loading from {model_info['repo_id']} (dtype={dtype})...")

        load_kwargs = {
            "pretrained_model_or_path": model_info["repo_id"],
            "torch_dtype": dtype,
        }
        if dtype == torch.float16:
            load_kwargs["variant"] = "fp16"

        self._pipeline = _load_with_download_progress(
            AutoPipelineForText2Image.from_pretrained,
            load_kwargs,
            download_progress_callback,
        )
        self._pipeline.to(device)

    def _load_lora_model(self, model_info, device, dtype, progress_callback=None, download_progress_callback=None):
        """Load SDXL base + apply a LoRA adapter (Lightning, Hyper-SD).

        Downloads the base SDXL model (~6.5GB) on first use, then applies the
        small LoRA file. Subsequent loads of different LoRA models only need the
        new adapter file.
        """
        from diffusers import StableDiffusionXLPipeline, EulerDiscreteScheduler
        from huggingface_hub import hf_hub_download

        base_model = model_info["base_model"]
        lora_repo = model_info["repo_id"]
        lora_file = model_info["lora_file"]
        scheduler_type = model_info["scheduler"]

        if progress_callback:
            progress_callback(f"Loading SDXL base model...")
        print(f"[local_img] Loading base model {base_model} (dtype={dtype})...")

        self._pipeline = _load_with_download_progress(
            StableDiffusionXLPipeline.from_pretrained,
            {"pretrained_model_name_or_path": base_model, "torch_dtype": dtype},
            download_progress_callback,
        )

        # Download and apply LoRA weights
        try:
            import peft  # noqa: F401 — required backend for load_lora_weights
        except ImportError:
            raise RuntimeError(
                "The 'peft' package is required for LoRA models (Lightning, Hyper-SD). "
                "Install with: pip install peft"
            )
        if progress_callback:
            progress_callback(f"Applying LoRA adapter ({lora_file})...")
        print(f"[local_img] Downloading LoRA: {lora_repo}/{lora_file}")
        lora_path = hf_hub_download(lora_repo, lora_file)
        self._pipeline.load_lora_weights(lora_path)
        # Don't fuse LoRA — keeps weights separate so hot-swap can
        # unload/reload without the unfuse reshape error. The perf
        # difference is negligible for 2-4 step models.

        # Configure the required scheduler
        self._apply_scheduler(scheduler_type)

        self._pipeline.to(device)
        self._active_base_model = base_model

    def _hot_swap_lora(self, model_info, progress_callback=None):
        """Swap LoRA weights on an already-loaded base model (fast path).

        Unloads the current LoRA adapter and loads the new one.
        The base pipeline stays on-device throughout. Takes ~2-5s vs ~30-60s.
        """
        from huggingface_hub import hf_hub_download

        lora_repo = model_info["repo_id"]
        lora_file = model_info["lora_file"]
        scheduler_type = model_info["scheduler"]

        print("[local_img] Unloading current LoRA adapter...")
        self._pipeline.unload_lora_weights()

        if progress_callback:
            progress_callback(f"Applying LoRA adapter ({lora_file})...")
        print(f"[local_img] Loading new LoRA: {lora_repo}/{lora_file}")
        lora_path = hf_hub_download(lora_repo, lora_file)
        self._pipeline.load_lora_weights(lora_path)

        self._apply_scheduler(scheduler_type)

    def _load_ip_adapter(self, progress_callback=None):
        """Load IP-Adapter for style transfer from inspiration images.

        Loads the CLIP ViT-H vision encoder and IP-Adapter cross-attention
        weights. Uses InstantStyle block targeting (up.block_0 only) to
        transfer style without affecting composition.

        Adds ~1.3GB VRAM (encoder + adapter weights).
        """
        if self._ip_adapter_loaded:
            return

        try:
            if progress_callback:
                progress_callback("Loading IP-Adapter vision encoder...")
            print("[local_img] Loading CLIP ViT-H encoder for IP-Adapter...")

            from transformers import CLIPVisionModelWithProjection
            import torch

            device = self.get_device()
            pipe_dtype = self._pipeline.dtype if self._pipeline else torch.float32
            image_encoder = CLIPVisionModelWithProjection.from_pretrained(
                IP_ADAPTER_REPO,
                subfolder=IP_ADAPTER_ENCODER_SUBFOLDER,
                torch_dtype=pipe_dtype,
            ).to(device)

            # Attach encoder to pipeline on the same device as the rest
            self._pipeline.image_encoder = image_encoder
            self._pipeline.register_to_config(image_encoder=[IP_ADAPTER_REPO, IP_ADAPTER_ENCODER_SUBFOLDER])

            if progress_callback:
                progress_callback("Loading IP-Adapter weights...")
            print("[local_img] Loading IP-Adapter weights...")

            self._pipeline.load_ip_adapter(
                IP_ADAPTER_REPO,
                subfolder=IP_ADAPTER_SUBFOLDER,
                weight_name=IP_ADAPTER_WEIGHT,
            )

            # InstantStyle: only inject into style blocks
            self._pipeline.set_ip_adapter_scale(IP_ADAPTER_STYLE_SCALE)
            self._ip_adapter_loaded = True
            print("[local_img] IP-Adapter loaded (InstantStyle mode)")

        except Exception as e:
            print(f"[local_img] IP-Adapter load failed (style transfer will use color only): {e}")
            self._ip_adapter_loaded = False

    def encode_style_image(self, pil_image, cache_key=None):
        """Pre-encode a style image through CLIP ViT-H for IP-Adapter.

        Returns a List[torch.Tensor] suitable for the pipeline's
        ip_adapter_image_embeds kwarg, or None if encoding fails.
        Tensors stay on device (MPS/CUDA) to avoid placeholder errors.

        cache_key: optional string. If provided and matches the previous
                   call's key, returns cached embeddings without re-encoding.
        """
        if not self._pipeline or not self._ip_adapter_loaded:
            return None

        # Cache hit
        if (cache_key is not None
                and cache_key == self._cached_ip_embeds_key
                and self._cached_ip_embeds is not None):
            print(f"[local_img] Using cached CLIP embeddings (key={cache_key[:8]})")
            return self._cached_ip_embeds

        import torch

        try:
            device = self.get_device()

            # IP-Adapter Plus uses hidden states, standard uses image embeds
            from diffusers.models.embeddings import ImageProjection
            image_proj_layer = (
                self._pipeline.unet.encoder_hid_proj.image_projection_layers[0]
            )
            output_hidden_state = not isinstance(image_proj_layer, ImageProjection)

            with torch.inference_mode():
                # encode_image runs feature_extractor + CLIP ViT-H on device
                pos_embeds, neg_embeds = self._pipeline.encode_image(
                    pil_image, device, 1, output_hidden_state
                )

            # Format for ip_adapter_image_embeds: list of tensors, each
            # chunkable into [negative, positive] along dim=0.
            # encode_image returns (1, seq_len, dim) for hidden states.
            # prepare_ip_adapter_image_embeds expects (2, 1, seq_len, dim).
            combined = torch.cat(
                [neg_embeds[None, :], pos_embeds[None, :]], dim=0
            )
            ip_embeds = [combined]

            self._cached_ip_embeds = ip_embeds
            self._cached_ip_embeds_key = cache_key

            key_label = cache_key[:8] if cache_key else 'none'
            print(f"[local_img] CLIP embeddings encoded and cached "
                  f"(key={key_label}, shape={combined.shape})")
            return ip_embeds

        except Exception as e:
            print(f"[local_img] CLIP pre-encoding failed, "
                  f"will fall back to per-call encoding: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _apply_scheduler(self, scheduler_type):
        """Configure pipeline scheduler based on model requirements."""
        if scheduler_type == "euler_trailing":
            from diffusers import EulerDiscreteScheduler
            self._pipeline.scheduler = EulerDiscreteScheduler.from_config(
                self._pipeline.scheduler.config,
                timestep_spacing="trailing",
            )
        elif scheduler_type == "ddim_trailing":
            from diffusers import DDIMScheduler
            self._pipeline.scheduler = DDIMScheduler.from_config(
                self._pipeline.scheduler.config,
                timestep_spacing="trailing",
            )

    def unload(self):
        """Unload current model and free memory."""
        self._pipeline = None
        self._img2img_pipeline = None
        self._active_model = None
        self._active_base_model = None
        self._ip_adapter_loaded = False
        self._cached_ip_embeds = None
        self._cached_ip_embeds_key = None
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            elif torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def generate(self, prompt: str, negative_prompt: str = "",
                 width: Optional[int] = None, height: Optional[int] = None,
                 steps: Optional[int] = None, guidance: Optional[float] = None,
                 seed: Optional[int] = None, style_image=None,
                 ip_adapter_scale=None,
                 ip_adapter_image_embeds=None,
                 progress_callback=None):
        """Generate an image from text prompt (txt2img). Returns a PIL Image.

        style_image: optional PIL Image of inspiration art (style via IP-Adapter)
        ip_adapter_scale: optional dict to override IP_ADAPTER_STYLE_SCALE for this call
        ip_adapter_image_embeds: optional pre-encoded CLIP embeddings (skips per-call encoding)
        """
        if not self._pipeline:
            raise RuntimeError("No model loaded. Call load_model() first.")

        import torch

        model_info = LOCAL_MODELS[self._active_model]
        w = width or model_info["recommended_size"][0]
        h = height or model_info["recommended_size"][1]
        has_style = style_image is not None or ip_adapter_image_embeds is not None
        # Cap resolution when IP-Adapter active. Higher-res models (Lightning,
        # Hyper-SD) run at 768x1024 for better quality; Turbo stays at 512x768.
        if has_style and width is None:
            if self._active_model == 'sdxl-turbo':
                w = min(w, 512)
                h = min(h, 768)
        # Boost steps and CFG when IP-Adapter active — more denoising steps
        # produce cleaner linework, and higher CFG gives text more control
        # over subject identity vs the reference image's visual features.
        default_steps = model_info["default_steps"]
        if has_style and steps is None:
            default_steps = model_info.get("ip_adapter_steps", default_steps)
        num_steps = steps or default_steps
        default_cfg = model_info["default_guidance"]
        if has_style and guidance is None:
            default_cfg = model_info.get("ip_adapter_guidance", default_cfg)
        cfg = guidance if guidance is not None else default_cfg

        generator = None
        if seed is not None:
            generator = torch.Generator(device=self.get_device()).manual_seed(seed)

        neg = negative_prompt or DEFAULT_NEGATIVE_PROMPT

        def step_cb(pipeline, step_index, timestep, callback_kwargs):
            if progress_callback:
                progress_callback(step_index + 1, num_steps)
            return callback_kwargs

        # IP-Adapter style injection via pre-encoded embeddings or raw PIL
        ip_kwargs = {}
        if has_style and self._ip_adapter_loaded:
            if ip_adapter_scale is not None:
                self._pipeline.set_ip_adapter_scale(ip_adapter_scale)
            scale_info = list((ip_adapter_scale or IP_ADAPTER_STYLE_SCALE)["up"]["block_0"])
            if ip_adapter_image_embeds is not None:
                # Pre-encoded: skip CLIP encoding entirely
                ip_kwargs["ip_adapter_image_embeds"] = ip_adapter_image_embeds
                print(f"[local_img] txt2img {w}x{h}, steps={num_steps}, cfg={cfg}, ip_embeds=cached, scale={scale_info}")
            else:
                # Fallback: pipeline encodes PIL through CLIP per-call
                ip_kwargs["ip_adapter_image"] = style_image
                print(f"[local_img] txt2img {w}x{h}, steps={num_steps}, cfg={cfg}, ip_adapter={scale_info}")
        else:
            print(f"[local_img] txt2img {w}x{h}, steps={num_steps}, cfg={cfg}")
        start = time.time()

        with torch.inference_mode():
            # Work around scheduler IndexError ("index N out of bounds for size N")
            # that occurs at certain step counts with IP-Adapter + trailing schedulers.
            # Fix: if IndexError at N steps, retry with N+1 steps (gives scheduler
            # an extra sigma entry). The visual difference is negligible.
            actual_steps = num_steps
            for attempt in range(3):
                try:
                    result = self._pipeline(
                        prompt=prompt,
                        negative_prompt=neg,
                        width=w,
                        height=h,
                        num_inference_steps=actual_steps,
                        guidance_scale=cfg,
                        generator=generator,
                        num_images_per_prompt=1,
                        callback_on_step_end=step_cb if progress_callback else None,
                        **ip_kwargs,
                    )
                    break
                except IndexError as e:
                    if "out of bounds" in str(e):
                        actual_steps += 1
                        print(f"[local_img] IndexError at {actual_steps - 1} steps, retrying with {actual_steps}")
                    else:
                        raise

        # Restore default IP scale if we overrode it
        if ip_adapter_scale is not None and self._ip_adapter_loaded:
            self._pipeline.set_ip_adapter_scale(IP_ADAPTER_STYLE_SCALE)

        # Release unused MPS cached allocations between generations.
        # Unlike gc.collect() (which causes MPS "Placeholder storage" crashes),
        # empty_cache() only returns unused memory — it doesn't invalidate
        # live tensors or trigger Python GC.
        try:
            if self.get_device() == "mps":
                torch.mps.synchronize()
                torch.mps.empty_cache()
        except Exception:
            pass

        elapsed = time.time() - start
        print(f"[local_img] Generated in {elapsed:.1f}s")
        return result.images[0]

    def generate_with_reference(self, prompt: str, reference_image_path,
                                strength: Optional[float] = None,
                                negative_prompt: str = "",
                                width: Optional[int] = None,
                                height: Optional[int] = None,
                                steps: Optional[int] = None,
                                guidance: Optional[float] = None,
                                seed: Optional[int] = None,
                                style_image=None,
                                ip_adapter_image_embeds=None,
                                progress_callback=None):
        """Generate using img2img with a reference image and optional style image.

        reference_image_path: Scryfall card art (provides subject/composition)
        style_image: PIL Image of inspiration art (provides style via IP-Adapter)
        ip_adapter_image_embeds: optional pre-encoded CLIP embeddings (skips per-call encoding)
        strength: deviation from reference (0=identical, 1=ignore ref)
        Falls back to txt2img if reference image not found.
        Returns a PIL Image.
        """
        if not self._img2img_pipeline:
            raise RuntimeError("No model loaded. Call load_model() first.")

        from PIL import Image
        import torch

        ref_path = Path(reference_image_path)
        if not ref_path.exists():
            print(f"[local_img] Reference not found: {ref_path}, falling back to txt2img")
            return self.generate(prompt, negative_prompt=negative_prompt,
                                 width=width, height=height,
                                 steps=steps, guidance=guidance, seed=seed,
                                 ip_adapter_image_embeds=ip_adapter_image_embeds,
                                 progress_callback=progress_callback)

        model_info = LOCAL_MODELS[self._active_model]
        num_steps = steps or model_info["default_steps"]
        cfg = guidance if guidance is not None else model_info["default_guidance"]
        s = strength if strength is not None else model_info["default_strength"]

        w = width or model_info["recommended_size"][0]
        h = height or model_info["recommended_size"][1]
        ref_img = Image.open(ref_path).convert("RGB")
        ref_img = ref_img.resize((w, h), Image.Resampling.LANCZOS)

        generator = None
        if seed is not None:
            generator = torch.Generator(device=self.get_device()).manual_seed(seed)

        neg = negative_prompt or DEFAULT_NEGATIVE_PROMPT

        def step_cb(pipeline, step_index, timestep, callback_kwargs):
            if progress_callback:
                progress_callback(step_index + 1, num_steps)
            return callback_kwargs

        # IP-Adapter style injection via pre-encoded embeddings or raw PIL
        has_style = style_image is not None or ip_adapter_image_embeds is not None
        ip_kwargs = {}
        if has_style and self._ip_adapter_loaded:
            if ip_adapter_image_embeds is not None:
                ip_kwargs["ip_adapter_image_embeds"] = ip_adapter_image_embeds
                print(f"[local_img] img2img {w}x{h}, strength={s}, steps={num_steps}, cfg={cfg}, ip_embeds=cached")
            else:
                ip_kwargs["ip_adapter_image"] = style_image
                print(f"[local_img] img2img {w}x{h}, strength={s}, steps={num_steps}, cfg={cfg}, ip_adapter=ON")
        else:
            print(f"[local_img] img2img {w}x{h}, strength={s}, steps={num_steps}, cfg={cfg}")
        start = time.time()

        with torch.inference_mode():
            result = self._img2img_pipeline(
                prompt=prompt,
                image=ref_img,
                strength=s,
                negative_prompt=neg,
                num_inference_steps=num_steps,
                guidance_scale=cfg,
                generator=generator,
                num_images_per_prompt=1,
                callback_on_step_end=step_cb if progress_callback else None,
                **ip_kwargs,
            )

        # Release unused MPS cached allocations (see generate() for rationale)
        try:
            if self.get_device() == "mps":
                torch.mps.synchronize()
                torch.mps.empty_cache()
        except Exception:
            pass

        elapsed = time.time() - start
        print(f"[local_img] Generated in {elapsed:.1f}s")
        return result.images[0]

    def get_status(self) -> dict:
        """Return status dict for the UI."""
        available, dep_msg = check_dependencies()
        models_cached = {}
        if available:
            for key in LOCAL_MODELS:
                models_cached[key] = self.is_model_cached(key)

        return {
            "dependencies_installed": available,
            "dependencies_message": dep_msg,
            "device": self._device or "unknown",
            "active_model": self._active_model,
            "is_loaded": self.is_loaded,
            "models": {
                key: {
                    "description": info["description"],
                    "memory_gb": info["memory_gb"],
                    "cached": models_cached.get(key, False),
                    "recommended_size": f"{info['recommended_size'][0]}x{info['recommended_size'][1]}",
                }
                for key, info in LOCAL_MODELS.items()
            },
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_generator: Optional[LocalImageGenerator] = None


def get_generator() -> LocalImageGenerator:
    """Get or create the module-level generator singleton."""
    global _generator
    if _generator is None:
        _generator = LocalImageGenerator()
    return _generator
