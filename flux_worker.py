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
   "steps":n, "seed":s|null, "out_path":"/tmp/....png"}
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

import os
import sys
import json
import time

SENTINEL = "@@FLUX@@"

# FLUX Redux = the FLUX-native successor to SDXL's IP-Adapter: SigLIP embeds the
# deck's inspiration IMAGES and the Redux projector appends them to the T5 text
# tokens, so the reference art itself steers every render. The official weights
# are gated; Runware hosts an ungated full mirror with the identical layout.
REDUX_REPO = os.environ.get("MFLUX_REDUX_REPO") or "Runware/FLUX.1-Redux-dev"


def _emit(obj):
    """Write one protocol message to stdout (sentinel-prefixed, flushed)."""
    sys.stdout.write(f"{SENTINEL} {json.dumps(obj)}\n")
    sys.stdout.flush()


def _log(msg):
    """Worker-side log — goes to stderr so it never pollutes the stdout protocol."""
    sys.stderr.write(f"[flux-worker] {msg}\n")
    sys.stderr.flush()


def _pool_token_grid(emb, k):
    """Average-pool a (B, N, D) reference-token grid (N = g*g) down to k*k tokens.

    This is the real style dial. Redux's own "strength" is a scalar multiply on
    all 729 tokens — attention renormalizes, so any strength still clones the
    reference's CONTENT (its figures and layout). Pooling to a coarse grid keeps
    the style statistics (medium, palette, rendering) and drops the layout:
    measured on a papyrus deck, 729 tokens = a variation of the reference,
    9 = palette only, 81 = reference style AND the card's own scene. Works on
    mlx and numpy arrays alike (reshape/mean only)."""
    b, n, d = emb.shape
    g = int(round(n ** 0.5))
    if not k or k >= g or g * g != n:
        return emb
    step = g // k
    grid = emb.reshape(b, g, g, d)[:, : step * k, : step * k, :]
    grid = grid.reshape(b, k, step, k, step, d).mean(axis=(2, 4))
    return grid.reshape(b, k * k, d)


_REDUX_STATE = {"k": 0, "cache": {}, "average": True}


def _redux_weights_cached() -> bool:
    """True when the Redux weights are already in the local HF cache (so loading
    the Redux variant costs no download)."""
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(REDUX_REPO, "image_embedder/diffusion_pytorch_model.safetensors"), str)
    except Exception:
        return False


def _install_redux_pooling():
    """Patch mflux's per-image Redux embedder ONCE: pool tokens to the requested
    grid and cache embeddings per (path, mtime, k) — the references are per-deck,
    so across a batch every card reuses the same embeddings (zero per-card cost)."""
    from mflux.models.flux.variants.redux import redux_util as RU
    if getattr(RU.ReduxUtil, "_das_pooled", False):
        return
    original = RU.ReduxUtil._embed_single_image

    def _embed_pooled(image_path, image_encoder, image_embedder, strength=1.0):
        k = _REDUX_STATE["k"]
        try:
            key = (str(image_path), os.path.getmtime(image_path), k)
        except OSError:
            key = (str(image_path), 0, k)
        emb = _REDUX_STATE["cache"].get(key)
        if emb is None:
            emb = _pool_token_grid(original(image_path, image_encoder, image_embedder, 1.0), k)
            if len(_REDUX_STATE["cache"]) > 32:
                _REDUX_STATE["cache"].clear()
            _REDUX_STATE["cache"][key] = emb
        return emb * strength if strength != 1.0 else emb

    RU.ReduxUtil._embed_single_image = staticmethod(_embed_pooled)

    # A resident Redux model must also serve decks with NO references: mflux
    # normalizes an empty reference list to None and then iterates it. With no
    # images there are simply no reference tokens to append.
    original_embed_images = RU.ReduxUtil.embed_images

    def _embed_images(image_paths, image_encoder, image_embedder, image_strengths=None):
        if not image_paths:
            return []
        embs = original_embed_images(image_paths, image_encoder, image_embedder,
                                     image_strengths=image_strengths)
        if _REDUX_STATE.get("average") and len(embs) > 1:
            # STYLE ESTIMATOR: a deck's references differ in CONTENT but share
            # a STYLE. The element-wise mean over references cancels what is
            # specific to each image (its figures, layout) and keeps what they
            # have in common (medium, palette, stroke) — measured cleaner and
            # leak-free versus any single reference at the same budget, and it
            # costs one reference's worth of tokens no matter how many are used.
            return [sum(embs) / len(embs)]
        return embs

    RU.ReduxUtil.embed_images = staticmethod(_embed_images)
    RU.ReduxUtil._das_pooled = True


class _Engine:
    """Holds the resident FLUX.1-schnell model — plain txt2img, or the Redux
    variant (same schnell weights + SigLIP + projector) once any request carries
    reference images. The parent guarantees no mlx-lm/mlx-vlm model is loaded in
    ITS process while we hold FLUX."""

    def __init__(self):
        self._flux = None
        self._model_key = None
        self._kind = None            # 'txt2img' | 'redux'

    def _model_config(self, model_key):
        import os
        # Mirror LOCAL_MODELS in local_image_generator (kept in sync deliberately;
        # the worker must not import that module to avoid a circular spawn).
        repo = os.environ.get("MFLUX_SCHNELL_REPO") or "dhairyashil/FLUX.1-schnell-mflux-4bit"
        return {"quantize": 4, "repo": repo}

    def _ensure_model(self, model_key, want_redux=False):
        """Load the right variant. A resident Redux model serves BOTH kinds of
        request (no references -> the text tokens alone, same as txt2img), so
        once Redux is loaded we never reload for a reference-less deck; a
        resident txt2img model is replaced only when references first arrive."""
        if self._flux is not None and self._model_key == model_key and (
                self._kind == "redux" or not want_redux):
            return
        self._free()
        from mflux.models.common.config.model_config import ModelConfig
        cfg = self._model_config(model_key)
        if want_redux:
            _install_redux_pooling()
            # mflux hardcodes the gated official repo for the Redux weights;
            # point its config at the mirror (or whatever MFLUX_REDUX_REPO says).
            _orig = ModelConfig.dev_redux

            def _redux_cfg():
                c = _orig()
                c.model_name = REDUX_REPO
                return c
            ModelConfig.dev_redux = staticmethod(_redux_cfg)
            from mflux.models.flux.variants.redux.flux_redux import Flux1Redux
            _log(f"loading FLUX schnell + Redux ({model_key}; redux weights {REDUX_REPO}) ...")
            self._flux = Flux1Redux(model_config=ModelConfig.schnell(),
                                    quantize=cfg["quantize"], model_path=cfg["repo"])
            self._kind = "redux"
        else:
            from mflux.models.flux.variants.txt2img.flux import Flux1
            _log(f"loading FLUX txt2img ({model_key}) ...")
            self._flux = Flux1(model_config=ModelConfig.schnell(),
                               quantize=cfg["quantize"], model_path=cfg["repo"])
            self._kind = "txt2img"
        self._model_key = model_key
        # Register the progress callback EXACTLY ONCE per loaded model. mflux's
        # CallbackRegistry.register() just appends (no dedupe), so registering per
        # generate would accumulate callbacks for the worker's lifetime — a slow
        # leak that makes the progress line flap and stdout grow unbounded. The
        # single callback reads the current step total from the Config mflux passes
        # each loop, so it stays correct across requests with different step counts.
        self._register_progress(self._flux)
        _log(f"FLUX {self._kind} ready")

    def _ensure_txt2img(self, model_key):
        self._ensure_model(model_key, want_redux=False)

    def _free(self):
        import gc
        # Drop the resident model reference BEFORE collecting, so the old ~13 GB
        # model can actually be freed during a reload — otherwise it stays
        # referenced and two models briefly co-reside (OOM on 18 GB).
        self._flux = None
        self._model_key = None
        self._kind = None
        _REDUX_STATE["cache"].clear()
        gc.collect()
        try:
            import mlx.core as mx
            mx.synchronize()
            mx.clear_cache()
        except Exception:
            pass

    def _register_progress(self, model):
        def _read_total(config, fallback):
            try:
                total = getattr(config, "num_inference_steps", None)
                return int(total) if total else fallback
            except Exception:
                return fallback

        class _P:
            def call_in_loop(self, t, seed, prompt, latents, config, time_steps):
                try:
                    step = int(t) + 1
                    _emit({"progress": {"step": step, "total": _read_total(config, step)}})
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

        start = time.time()
        redux = req.get("redux") or {}
        ref_images = [p for p in (redux.get("images") or []) if p and os.path.exists(p)]
        n_steps = int(req.get("steps") or 4)
        self._ensure_model(model_key, want_redux=bool(ref_images))
        if self._kind == "redux":
            tokens = int(redux.get("tokens") or 25)
            strength = float(redux.get("strength") or 1.0)
            _REDUX_STATE["average"] = bool(redux.get("average", True))
            _REDUX_STATE["k"] = max(1, int(round(tokens ** 0.5))) if ref_images else 0
            _log(f"redux {w}x{h} steps={n_steps} refs={len(ref_images)}"
                 f"{' (averaged)' if len(ref_images) > 1 and _REDUX_STATE['average'] else ''} "
                 f"tokens={_REDUX_STATE['k'] ** 2 if ref_images else 0} "
                 f"strength={strength}: {prompt[:80]}")
            result = self._flux.generate_image(
                seed=seed, prompt=prompt, num_inference_steps=n_steps, width=w, height=h,
                redux_image_paths=ref_images,
                redux_image_strengths=[strength] * len(ref_images))
        else:
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
        if cmd == "load":
            # Eagerly load the txt2img weights so the parent can fail fast on a
            # broken/gated repo instead of discovering it per-card at generate.
            try:
                # Preload the Redux variant when its weights are already cached:
                # it serves reference-less requests identically, and avoids a
                # second ~13 GB load the moment a deck with references renders.
                engine._ensure_model(req.get("model_key") or "flux-schnell-4bit",
                                     want_redux=bool(req.get("redux")) or _redux_weights_cached())
                _emit({"loaded": True})
            except Exception as e:
                import traceback
                traceback.print_exc(file=sys.stderr)
                _emit({"error": str(e)})
            continue
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
