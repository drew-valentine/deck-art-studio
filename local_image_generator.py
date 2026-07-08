#!/usr/bin/env python3
"""
MLX-native image generation for Deck Art Studio (Apple Silicon only).

Backed by **mflux** running **FLUX.1-schnell** (4-bit) on the Apple GPU. This
replaces the previous PyTorch-MPS Stable Diffusion (SDXL Turbo / Lightning /
Hyper-SD) + IP-Adapter pipeline.

Why the API looks the way it does
---------------------------------
`deck_studio.py` drives this module through a stable surface — `get_generator()`,
`LocalImageGenerator.{load_model, unload, generate, get_status, is_loaded,
active_model}`, `LOCAL_MODELS`, `check_dependencies()` — so those names are
preserved even though the engine underneath changed entirely.

FLUX vs. SDXL, the important differences
----------------------------------------
* **Prompts:** FLUX uses a T5 text encoder with a large (~512) token budget and
  follows natural-language prompts far better than SDXL's 77-token CLIP. The old
  aggressive prompt truncation and the IP-Adapter cross-attention style transfer
  are gone — style rides entirely in the text prompt (the distilled `style_tags`).
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
import time
from typing import Optional

from gpu_coord import GPU_LOCK, InactivityWatchdog

# Worker is killed if it produces no output for this long (seconds). FLUX streams
# a progress line per step, so the only silent window is the pre-first-step model
# load (~10-30s cached); 300s catches a true hang without false-killing real work.
WORKER_READ_TIMEOUT = 300
# The first load may DOWNLOAD weights (~6-9 GB), which is silent on stdout — allow
# much longer before declaring the eager load hung.
WORKER_LOAD_TIMEOUT = 1800


def free_mlx_memory():
    """Release this process's freed MLX/Metal buffers back to the OS.

    Now that the heavy models live in worker SUBPROCESSES, this only clears the
    Flask process's own (tiny) MLX allocator cache — the real reclaim happens
    when a worker process is killed and the OS tears down its Metal context. The
    old footprint-`settle` loop here measured the Flask process, which no longer
    holds the heavy models, so it was a no-op that gave false OOM protection and
    has been removed (callers now kill the worker synchronously and pause briefly
    in _spawn_worker instead). Lazy MLX import keeps this importable on non-Mac CI.
    """
    gc.collect()
    gc.collect()
    try:
        import mlx.core as mx
        mx.synchronize()
        mx.clear_cache()
    except Exception:
        pass


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
        "memory_gb": 12,
        "description": "FLUX.1 schnell (4-bit). High quality txt2img, ~40-70s on M3 Pro.",
    },
    # NB: no 8-bit variant — there's no non-gated 8-bit mflux mirror (the official
    # repo is gated), and 8-bit won't fit alongside everything on 18 GB. It only
    # ever produced a gated 401 on load, so it's intentionally omitted.
}

DEFAULT_LOCAL_MODEL = "flux-schnell-4bit"


# Historical constant some call sites still import; no IP-Adapter under FLUX.
IP_ADAPTER_STYLE_SCALE_REDUCED = 0.0


class LocalImageGenerator:
    """Drives the FLUX.1-schnell txt2img worker subprocess (see flux_worker.py)."""

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
            mlx_llm.unload()  # synchronous: proc.wait() inside, so it's dead on return
        except Exception:
            pass
        # The evicted MLX worker is now a dead process; the OS reclaims its
        # ~5 GB. (The old free_mlx_memory(settle_below_gb=7.0) here measured THIS
        # Flask process's footprint, but the heavy models live in subprocesses
        # now, so it was a no-op giving false OOM protection — removed.) Clear our
        # own tiny MLX cache and give the kernel a beat to finish reclaiming the
        # killed worker's GPU pages before the new worker wires ~13 GB.
        free_mlx_memory()
        time.sleep(0.5)
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

    def _read_result(self, progress_callback, timeout=WORKER_READ_TIMEOUT):
        """Read the worker's stdout until a terminal message. Forwards progress.

        Returns the parsed terminal dict ('done' for a generation, 'loaded' for an
        eager load). Raises RuntimeError on worker error/death. An inactivity
        watchdog kills the worker if it goes silent past `timeout` so a hung
        inference can't hold GPU_LOCK forever.
        """
        import flux_worker
        sentinel = flux_worker.SENTINEL
        watchdog = InactivityWatchdog(self._proc, timeout)
        try:
            while True:
                line = self._proc.stdout.readline()
                watchdog.kick()
                if line == "":
                    # EOF — the worker died (OS OOM kill, or our watchdog killed a
                    # hung worker). Surface it and reset so the next call respawns.
                    code = self._proc.poll()
                    self._proc = None
                    self._active_model = None
                    if watchdog.fired:
                        raise RuntimeError(
                            f"FLUX worker timed out (no output for {timeout}s) and was killed")
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
                elif msg.get("done") or msg.get("loaded"):
                    return msg
        finally:
            watchdog.stop()

    # --- load / unload ----------------------------------------------------
    def load_model(self, model_key: str, progress_callback=None,
                   download_progress_callback=None) -> tuple[bool, str]:
        """Spawn the FLUX worker AND eagerly load the weights. Returns (ok, message).

        We send an explicit 'load' command and wait for the worker to confirm the
        weights loaded, so a genuinely broken/gated repo fails fast here with one
        clear message — instead of every card in a batch failing later at first
        generate with a confusing 'worker exited unexpectedly'. Weights stay in
        the HF cache between runs; the worker reuses them for subsequent generates.
        """
        with GPU_LOCK, self._lock:
            if model_key not in LOCAL_MODELS:
                return False, f"Unknown model: {model_key}. Available: {list(LOCAL_MODELS.keys())}"
            available, dep_msg = check_dependencies()
            if not available:
                return False, dep_msg
            if progress_callback:
                progress_callback(f"Loading FLUX ({model_key}) — first run downloads weights...")
            try:
                self._spawn_worker()
                self._send({"cmd": "load", "model_key": model_key})
                # Long timeout: the first load may download ~6-9 GB.
                self._read_result(None, timeout=WORKER_LOAD_TIMEOUT)
                self._active_model = model_key
                if progress_callback:
                    progress_callback(f"{model_key} ready")
                return True, f"FLUX worker ready ({model_key})"
            except Exception as e:
                # Tear the worker down so we don't leave a half-loaded process.
                try:
                    self.unload()  # reentrant: we already hold GPU_LOCK + self._lock
                except Exception:
                    pass
                self._active_model = None
                return False, f"Image model failed to load: {e}"

    def unload(self):
        """Terminate the FLUX worker — the OS reclaims its ENTIRE Metal context.

        This is the crux of the subprocess design: process death guarantees the
        ~13 GB of GPU/wired FLUX memory is fully returned (no fragmentation
        residue), so the mlx-vlm/mlx-lm models that load next during style
        analysis get a pristine GPU heap. Called by mlx_llm._free_image_model()
        before any LLM/VLM load, and before (re)loading FLUX.
        """
        with GPU_LOCK, self._lock:
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
                        proc.wait(timeout=5)
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
                 progress_callback=None, **_ignored):
        """Generate an image with FLUX txt2img (in the worker subprocess). Returns a PIL.Image.

        schnell ignores guidance / negative prompt (accepted but unused). Legacy
        kwargs are accepted via **_ignored for call-site compatibility.
        """
        import os
        import tempfile
        from PIL import Image
        with GPU_LOCK, self._lock:
            model_key = self._active_model or DEFAULT_LOCAL_MODEL
            cfg = LOCAL_MODELS.get(model_key, {})
            rw, rh = cfg.get("recommended_size", (1024, 1024))
            w = int(width or rw)
            h = int(height or rh)
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
                "out_path": out_path,
            }
            print(f"[flux] -> worker txt2img {w}x{h}, steps={n_steps}: {prompt[:80]}")
            try:
                try:
                    self._send(req)
                except Exception as e:
                    # The write failed — the worker may be wedged/half-dead but is
                    # still holding ~13 GB. Kill and reap it before dropping the ref
                    # so a respawn doesn't leave two workers co-resident (OOM).
                    proc = self._proc
                    self._proc = None
                    self._active_model = None
                    if proc is not None:
                        try:
                            proc.kill()
                            proc.wait(timeout=5)
                        except Exception:
                            pass
                    raise RuntimeError(f"FLUX worker write failed: {e}")
                done = self._read_result(progress_callback, timeout=WORKER_READ_TIMEOUT)
                print(f"[flux] worker done in {done.get('seconds', 0):.1f}s (seed {done.get('seed')})")
                # Load the PNG fully into memory so the temp file can be removed.
                img = Image.open(out_path)
                img.load()
                return img
            finally:
                # mkstemp created the file, so it leaks on ANY failure after this
                # point (write, read, worker death, decode) unless we remove it here.
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass

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
