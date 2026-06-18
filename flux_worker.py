#!/usr/bin/env python3
"""FLUX image-generation worker — runs in a SEPARATE process from Flask.

Why this exists
---------------
On an 18 GB Apple-Silicon Mac the GPU working-set limit is ~13.3 GB and FLUX
schnell uses ~all of it. When FLUX and the style-analysis models (mlx-vlm /
mlx-lm) churn through the same long-lived process, the Metal heap fragments and
freed FLUX memory is NOT fully returned (mx.clear_cache + process-footprint
management both proved insufficient — the OS still hard-killed the process while
reporting memory "free"). Running FLUX in its own process makes eviction a
PROCESS DEATH: when the parent terminates this worker, macOS tears down the
whole Metal context and reclaims every byte, guaranteeing a pristine GPU heap
for the analysis models that load next.

Protocol (newline-delimited JSON over stdin/stdout)
---------------------------------------------------
Parent -> worker (stdin):
  {"cmd":"generate", "model_key":..., "prompt":..., "width":w, "height":h,
   "steps":n, "seed":s|null, "control_image_path":path|null,
   "controlnet_strength":cs|null, "out_path":"/tmp/....png"}
  {"cmd":"shutdown"}

Worker -> parent (stdout) — protocol lines are prefixed with SENTINEL so they
are unambiguous amid any library stdout noise; everything else on stdout/stderr
is passed through to the Flask log:
  <SENTINEL> {"progress":{"step":s,"total":t}}
  <SENTINEL> {"done":true,"out_path":...,"seconds":f,"seed":s}
  <SENTINEL> {"error":"..."}

All MLX/mflux imports are lazy (inside functions) so this file stays importable
on non-Mac CI — though in practice it is only ever spawned on Apple Silicon.
"""

import sys
import json
import time

SENTINEL = "@@FLUX@@"


def _emit(obj):
    """Write one protocol message to stdout (sentinel-prefixed, flushed)."""
    sys.stdout.write(f"{SENTINEL} {json.dumps(obj)}\n")
    sys.stdout.flush()


def _log(msg):
    """Worker-side log — goes to stderr so it never pollutes the stdout protocol."""
    sys.stderr.write(f"[flux-worker] {msg}\n")
    sys.stderr.flush()


class _Engine:
    """Holds ONE resident FLUX variant (txt2img or Canny ControlNet) and swaps
    between them on demand. Only one is ever resident, mirroring the in-process
    single-resident rule — but here the parent guarantees no mlx-lm/mlx-vlm model
    is loaded in ITS process while we hold FLUX."""

    def __init__(self):
        self._flux = None
        self._controlnet = None
        self._model_key = None

    def _model_config(self, model_key):
        import os
        # Mirror LOCAL_MODELS in local_image_generator (kept in sync deliberately;
        # the worker must not import that module to avoid a circular spawn).
        repo = os.environ.get("MFLUX_SCHNELL_REPO") or "dhairyashil/FLUX.1-schnell-mflux-4bit"
        return {"quantize": 4, "repo": repo}

    def _ensure_txt2img(self, model_key):
        if self._flux is not None and self._model_key == model_key:
            return
        self._controlnet = None  # can't co-reside
        self._free()
        from mflux.models.flux.variants.txt2img.flux import Flux1
        from mflux.models.common.config.model_config import ModelConfig
        cfg = self._model_config(model_key)
        _log(f"loading FLUX txt2img ({model_key}) ...")
        self._flux = Flux1(model_config=ModelConfig.schnell(),
                           quantize=cfg["quantize"], model_path=cfg["repo"])
        self._model_key = model_key
        _log("FLUX txt2img ready")

    def _ensure_controlnet(self, model_key):
        if self._controlnet is not None and self._model_key == model_key:
            return
        self._flux = None  # can't co-reside
        self._free()
        from mflux.models.flux.variants.controlnet.flux_controlnet import Flux1Controlnet
        from mflux.models.common.config.model_config import ModelConfig
        cfg = self._model_config(model_key)
        _log(f"loading FLUX Canny ControlNet ({model_key}) ...")
        self._controlnet = Flux1Controlnet(
            model_config=ModelConfig.schnell_controlnet_canny(),
            quantize=cfg["quantize"], model_path=cfg["repo"])
        self._model_key = model_key
        _log("FLUX ControlNet ready")

    def _free(self):
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.synchronize()
            mx.clear_cache()
        except Exception:
            pass

    def _register_progress(self, model, total_steps):
        class _P:
            def call_in_loop(self, t, seed, prompt, latents, config, time_steps):
                try:
                    _emit({"progress": {"step": int(t) + 1, "total": total_steps}})
                except Exception:
                    pass
        try:
            model.callbacks.register(_P())
        except Exception:
            pass

    def generate(self, req):
        import random
        model_key = req.get("model_key") or "flux-schnell-4bit"
        prompt = req["prompt"]
        w = int(req["width"])
        h = int(req["height"])
        seed = req.get("seed")
        if seed is None:
            seed = random.randint(0, 2**31 - 1)
        seed = int(seed)
        out_path = req["out_path"]
        control = req.get("control_image_path")

        start = time.time()
        if control:
            self._ensure_controlnet(model_key)
            n_steps = int(req.get("steps") or 14)
            cs = float(req.get("controlnet_strength") if req.get("controlnet_strength") is not None else 0.45)
            self._register_progress(self._controlnet, n_steps)
            _log(f"controlnet {w}x{h} steps={n_steps}: {prompt[:80]}")
            result = self._controlnet.generate_image(
                seed=seed, prompt=prompt, controlnet_image_path=str(control),
                num_inference_steps=n_steps, width=w, height=h, controlnet_strength=cs)
        else:
            self._ensure_txt2img(model_key)
            n_steps = int(req.get("steps") or 4)
            self._register_progress(self._flux, n_steps)
            _log(f"txt2img {w}x{h} steps={n_steps}: {prompt[:80]}")
            result = self._flux.generate_image(
                seed=seed, prompt=prompt, num_inference_steps=n_steps, width=w, height=h)

        result.image.save(out_path)
        secs = time.time() - start
        _log(f"generated in {secs:.1f}s -> {out_path}")
        _emit({"done": True, "out_path": out_path, "seconds": secs, "seed": seed})


def _start_parent_watchdog():
    """Exit if the parent (Flask) dies, so we never orphan and hold ~13 GB.

    stdin-EOF normally signals parent death, but a worker busy inside a long
    generate() isn't reading stdin and would orphan. Polling getppid() (==1 once
    reparented to init/launchd) catches that regardless of what we're doing.
    """
    import os
    import threading
    import time

    def _watch():
        while True:
            try:
                if os.getppid() == 1:
                    _log("parent died — exiting")
                    os._exit(0)
            except Exception:
                pass
            time.sleep(2)
    threading.Thread(target=_watch, daemon=True).start()


def main():
    _start_parent_watchdog()
    engine = _Engine()
    _log("started; waiting for requests")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            _emit({"error": f"bad request json: {e}"})
            continue
        cmd = req.get("cmd")
        if cmd == "shutdown":
            _log("shutdown requested")
            break
        if cmd != "generate":
            _emit({"error": f"unknown cmd: {cmd}"})
            continue
        try:
            engine.generate(req)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            _emit({"error": str(e)})
    _log("exiting")


if __name__ == "__main__":
    main()
