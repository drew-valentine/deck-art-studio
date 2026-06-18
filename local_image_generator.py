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
import json
import random
import threading
from typing import Optional


def _phys_footprint_gb():
    """This process's current physical memory footprint in GB, or None.

    Uses macOS `footprint`, which (unlike `ps` RSS) counts MLX's Metal/unified
    memory. Reads `phys_footprint:` (current), NOT `phys_footprint_peak:`.
    """
    import os
    import subprocess
    try:
        out = subprocess.run(["footprint", str(os.getpid())],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            s = line.strip().lower()
            if s.startswith("phys_footprint:"):
                # The unit VARIES by magnitude: "13 GB" / "645 MB" / "9632 KB".
                # Must handle all of them — treating KB as MB (the earlier bug)
                # under-reported by 1000x and made the settle a silent no-op.
                parts = line.split(":", 1)[1].strip().split()
                num = float(parts[0])
                unit = (parts[1].upper() if len(parts) > 1 else "B")
                factor = {"B": 1 / 1024**3, "KB": 1 / 1024**2,
                          "MB": 1 / 1024, "GB": 1.0, "TB": 1024.0}.get(unit, 1 / 1024)
                return num * factor
    except Exception:
        pass
    return None


def free_mlx_memory(settle_below_gb=None, timeout=8.0):
    """Aggressively return freed MLX/Metal buffers to the OS.

    A plain ``del model; gc.collect(); mx.clear_cache()`` is NOT enough on an
    18 GB machine: when we evict one heavy model to load the next, the old
    model's Metal buffers can linger in the allocator's cache pool while the
    new model starts allocating — a transient ~2x peak that OOM-kills the
    process. To make the eviction actually release before the next load:

      1. ``mx.synchronize()`` — wait for in-flight GPU work so the arrays are
         no longer referenced by a pending command buffer.
      2. two ``gc.collect()`` passes — the mflux/mlx-vlm models hold reference
         cycles (model <-> submodules), so a single pass may not reclaim them.
      3. ``mx.clear_cache()`` — hand the now-unreferenced buffers back.

    ``settle_below_gb``: after evicting a LARGE model (FLUX, ~13 GB), macOS
    reclaims its GPU/wired pages ASYNCHRONOUSLY. ``mx.clear_cache()`` returns
    the buffers to MLX but the process footprint can stay pinned at ~13 GB for
    a few seconds. If the next big model (the 5.3 GB vision model) is wired on
    top of that un-reclaimed footprint, the process exceeds the ~13 GB GPU
    working-set limit and the OS hard-kills it (this was the "analyze-style
    crashes almost every time" bug — a reclaim-timing RACE, which is why it
    only crashed sometimes). When set, we BLOCK until the measured footprint
    drops below this threshold (re-nudging the allocator each loop), making the
    reclaim deterministic instead of timing-dependent. Lazy MLX import keeps
    this importable on non-Mac CI.
    """
    import time
    gc.collect()
    gc.collect()
    try:
        import mlx.core as mx
        mx.synchronize()
        mx.clear_cache()
    except Exception:
        pass

    if settle_below_gb is None:
        return

    start_foot = _phys_footprint_gb()
    deadline = time.time() + timeout
    waited = False
    while time.time() < deadline:
        foot = _phys_footprint_gb()
        if foot is None:
            time.sleep(1.0)  # can't measure — give the OS a fixed beat instead
            break
        if foot <= settle_below_gb:
            if waited:
                print(f"[mlx-mem] settle ok: footprint={foot:.1f} GB "
                      f"(was {start_foot:.1f}, target <{settle_below_gb})")
            return
        # Still holding the old model's footprint — nudge the allocator and wait
        # for the OS to reclaim before we let the next big model load.
        waited = True
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass
        time.sleep(0.4)
    foot = _phys_footprint_gb()
    print(f"[mlx-mem] settle TIMEOUT: footprint={foot:.1f} GB still above "
          f"{settle_below_gb} GB after {timeout}s — proceeding (OOM risk)"
          if foot is not None else
          "[mlx-mem] settle done (footprint unmeasured)")


def mlx_mem_str(tag: str = "") -> str:
    """True MLX/Metal GPU memory in GB (active/peak/cache).

    ``ps`` RSS and ``vm_stat`` free both MISS this — MLX allocates through Metal
    unified memory, which is the dominant term for FLUX (~12 GB) and is what
    actually drives the 18 GB OOM. This reads MLX's own allocator counters.
    """
    try:
        import mlx.core as mx
        gb = 1024 ** 3
        act = mx.get_active_memory() / gb
        peak = mx.get_peak_memory() / gb
        cache = mx.get_cache_memory() / gb
        return f"[mlx-mem{(' ' + tag) if tag else ''}] active={act:.1f} peak={peak:.1f} cache={cache:.1f} GB"
    except Exception as e:
        return f"[mlx-mem] unavailable ({e})"


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
        # 672x896 (3:4): peak ~15.5 GB. 1024x1024 / 768x1024 peak ~17.5 GB, which
        # OOM-kills Flask on an 18 GB machine. deck_studio overrides via 'size',
        # but keep this fallback safe too. See MODEL_OPTIONS note in deck_studio.py.
        "recommended_size": (672, 896),
        "supports_img2img": True,
        "default_strength": 0.45,   # img2img: lower = freer, higher = closer to ref
        "memory_gb": 12,
        "description": "FLUX.1 schnell (4-bit). High quality, ~40-70s on M3 Pro. img2img + txt2img.",
    },
    # NB: no 8-bit variant — there's no non-gated 8-bit mflux mirror (the official
    # repo is gated), and 8-bit won't fit alongside everything on 18 GB. It only
    # ever produced a gated 401 on load, so it's intentionally omitted.
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
        # FLUX runs in a SUBPROCESS (flux_worker.py), not in this process — see
        # the module docstring and flux_worker.py for why. We keep the same
        # public API; `_proc` is the live worker (or None).
        self._proc = None                          # subprocess.Popen | None
        self._active_model: Optional[str] = None   # LOCAL_MODELS key the worker holds
        self._device: Optional[str] = "gpu"
        self._lock = threading.RLock()
        # Retained for call-site compatibility (no IP-Adapter under FLUX).
        self._ip_adapter_loaded = False

    # --- status -----------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._worker_alive() and self._active_model is not None

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

    # --- worker subprocess management -------------------------------------
    def _worker_path(self):
        import os
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "flux_worker.py")

    def _worker_alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _spawn_worker(self):
        """Start the FLUX worker subprocess (idempotent). Caller holds _lock."""
        if self._worker_alive():
            return
        import os
        import subprocess
        import sys
        # Unload OUR mlx-lm/mlx-vlm first: the worker is about to wire ~13 GB of
        # FLUX, which cannot co-reside with a 5 GB vision/text model in this
        # process on an 18 GB machine.
        try:
            import mlx_llm
            mlx_llm.unload()
        except Exception:
            pass
        free_mlx_memory(settle_below_gb=7.0)
        env = dict(os.environ)
        self._proc = subprocess.Popen(
            [sys.executable, "-u", self._worker_path()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None,  # worker logs (stderr) flow to the Flask log
            text=True, bufsize=1, cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
        )
        print("[flux] Spawned FLUX worker subprocess (pid "
              f"{self._proc.pid})")

    def _send(self, req: dict):
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()

    def _read_result(self, progress_callback):
        """Read the worker's stdout until a done/error message. Forwards progress.

        Returns the parsed 'done' dict. Raises RuntimeError on worker error/death.
        """
        import flux_worker
        sentinel = flux_worker.SENTINEL
        while True:
            line = self._proc.stdout.readline()
            if line == "":
                # EOF — the worker died (very likely an OS OOM kill, though the
                # whole point of isolation is that only the worker dies now, not
                # Flask). Surface it and reset so the next call respawns.
                code = self._proc.poll()
                self._proc = None
                self._active_model = None
                raise RuntimeError(f"FLUX worker exited unexpectedly (code {code})")
            line = line.rstrip("\n")
            if not line.startswith(sentinel):
                if line.strip():
                    print(line)  # library noise -> Flask log
                continue
            try:
                msg = json.loads(line[len(sentinel):].strip())
            except Exception:
                continue
            if "progress" in msg:
                if progress_callback:
                    p = msg["progress"]
                    try:
                        progress_callback(int(p["step"]), int(p["total"]))
                    except Exception:
                        pass
            elif msg.get("error"):
                raise RuntimeError(f"FLUX worker error: {msg['error']}")
            elif msg.get("done"):
                return msg

    # --- load / unload ----------------------------------------------------
    def load_model(self, model_key: str, progress_callback=None,
                   download_progress_callback=None) -> tuple[bool, str]:
        """Ensure the FLUX worker is running. Returns (success, message).

        FLUX itself is loaded lazily inside the worker on the first generate (the
        weights stay in the HF cache between runs). We just spawn the worker here
        and unload this process's mlx-lm/mlx-vlm so the worker has the memory.
        """
        with self._lock:
            if model_key not in LOCAL_MODELS:
                return False, f"Unknown model: {model_key}. Available: {list(LOCAL_MODELS.keys())}"
            available, dep_msg = check_dependencies()
            if not available:
                return False, dep_msg
            if progress_callback:
                progress_callback(f"Starting FLUX worker ({model_key})...")
            try:
                self._spawn_worker()
                self._active_model = model_key
                if progress_callback:
                    progress_callback(f"{model_key} ready")
                return True, f"FLUX worker ready ({model_key})"
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._proc = None
                self._active_model = None
                return False, f"Failed to start FLUX worker: {e}"

    def unload(self):
        """Terminate the FLUX worker — the OS reclaims its ENTIRE Metal context.

        This is the crux of the subprocess design: process death guarantees the
        ~13 GB of GPU/wired FLUX memory is fully returned (no fragmentation
        residue), so the mlx-vlm/mlx-lm models that load next during style
        analysis get a pristine GPU heap. Called by mlx_llm._free_image_model()
        before any LLM/VLM load, and before (re)loading FLUX.
        """
        with self._lock:
            if self._proc is None:
                return
            proc = self._proc
            self._proc = None
            self._active_model = None
            try:
                proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
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
            # The worker's GPU memory is gone with the process; a light local
            # reclaim clears any residue in THIS process's MLX allocator.
            free_mlx_memory()
            print("[flux] Terminated FLUX worker (GPU memory reclaimed by OS)")

    # --- generation -------------------------------------------------------
    def generate(self, prompt: str, negative_prompt: str = "",
                 width: Optional[int] = None, height: Optional[int] = None,
                 steps: Optional[int] = None, guidance: Optional[float] = None,
                 seed: Optional[int] = None,
                 control_image_path=None, controlnet_strength: Optional[float] = None,
                 reference_image_path=None, image_strength: Optional[float] = None,
                 progress_callback=None, **_ignored):
        """Generate an image with FLUX (in the worker subprocess). Returns a PIL.Image.

        Two modes:
        * **txt2img** (default, fast ~4 steps): style + composition from the prompt.
        * **Canny ControlNet** (when `control_image_path` is given): locks the
          composition to the reference image's edges while the prompt owns the
          style. Renders at CONTROLNET_STEPS (~14) so it's slower but faithful.

        schnell ignores guidance / negative prompt (accepted but unused). Legacy
        img2img + IP-Adapter kwargs are accepted for compatibility but unused.
        """
        import os
        import tempfile
        from PIL import Image
        with self._lock:
            model_key = self._active_model or DEFAULT_LOCAL_MODEL
            cfg = LOCAL_MODELS.get(model_key, {})
            rw, rh = cfg.get("recommended_size", (1024, 1024))
            w = int(width or rw)
            h = int(height or rh)
            if control_image_path:
                n_steps = int(steps or CONTROLNET_STEPS)
            else:
                n_steps = int(steps or cfg.get("default_steps", 4))

            if not self._worker_alive():
                ok, msg = self.load_model(model_key)
                if not ok:
                    raise RuntimeError(f"FLUX worker not available: {msg}")

            fd, out_path = tempfile.mkstemp(suffix=".png", prefix="flux_")
            os.close(fd)
            req = {
                "cmd": "generate", "model_key": model_key, "prompt": prompt,
                "width": w, "height": h, "steps": n_steps,
                "seed": (int(seed) if seed is not None else None),
                "control_image_path": (str(control_image_path) if control_image_path else None),
                "controlnet_strength": (float(controlnet_strength)
                                        if controlnet_strength is not None else None),
                "out_path": out_path,
            }
            mode = "controlnet" if control_image_path else "txt2img"
            print(f"[flux] -> worker {mode} {w}x{h}, steps={n_steps}: {prompt[:80]}")
            try:
                self._send(req)
            except Exception as e:
                self._proc = None
                self._active_model = None
                raise RuntimeError(f"FLUX worker write failed: {e}")
            done = self._read_result(progress_callback)
            print(f"[flux] worker done in {done.get('seconds', 0):.1f}s (seed {done.get('seed')})")
            # Load the PNG into memory, then remove the temp file.
            img = Image.open(out_path)
            img.load()
            try:
                os.remove(out_path)
            except Exception:
                pass
            return img

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
