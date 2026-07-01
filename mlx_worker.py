#!/usr/bin/env python3
"""MLX text + vision inference worker — runs in a SEPARATE process from Flask.

Companion to flux_worker.py. Same rationale: on an 18 GB Apple-Silicon Mac the
GPU working-set limit is ~13.3 GB, and repeatedly loading/unloading the 5.3 GB
vision model + text model IN-PROCESS fragments the Metal heap until a load OOM-
kills the process — process-level memory management can't defragment it (proven
empirically; the 2nd style analysis in a long-lived process reliably crashed).

Running the analysis models here makes each analysis session a SHORT-LIVED
process: the parent kills this worker when the work session ends (mlx_llm.unload,
driven by deck_studio's _ollama_work_done at count==0), so the NEXT analysis
spawns a pristine process and never inherits accumulated fragmentation. It also
guarantees the analysis models are fully reclaimed before FLUX loads.

Protocol (newline-delimited JSON; worker->parent lines are SENTINEL-prefixed so
they're unambiguous amid library stdout noise):
  Parent -> worker (stdin):
    {"cmd":"chat",   "messages":[...], "model":id, "max_tokens":n, "temperature":t}
    {"cmd":"vision", "image_path":p, "prompt":s, "model":id, "max_tokens":n, "temperature":t}
    {"cmd":"shutdown"}
  Worker -> parent (stdout):
    <SENTINEL> {"text":"..."}
    <SENTINEL> {"error":"..."}

All MLX imports are lazy so this file imports on non-Mac CI (it is only ever
spawned on Apple Silicon).
"""

import sys
import json

SENTINEL = "@@MLX@@"


def _emit(obj):
    sys.stdout.write(f"{SENTINEL} {json.dumps(obj)}\n")
    sys.stdout.flush()


def _log(msg):
    sys.stderr.write(f"[mlx-worker] {msg}\n")
    sys.stderr.flush()


class _Engine:
    """Single-resident text/vision model, swapping on demand (mirrors the old
    in-process mlx_llm cache). Only one model is resident at a time."""

    def __init__(self):
        self._id = None
        self._kind = None
        self._model = None
        self._aux = None

    def _free(self):
        self._model = None
        self._aux = None
        self._id = None
        self._kind = None
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.synchronize()
            mx.clear_cache()
        except Exception:
            pass

    def _ensure_text(self, model_id):
        if self._kind == "text" and self._id == model_id:
            return
        self._free()
        from mlx_lm import load
        _log(f"loading text model {model_id} ...")
        self._model, self._aux = load(model_id)
        self._id, self._kind = model_id, "text"

    def _ensure_vision(self, model_id):
        if self._kind == "vision" and self._id == model_id:
            return
        self._free()
        from mlx_vlm import load
        _log(f"loading vision model {model_id} ...")
        self._model, self._aux = load(model_id)
        self._id, self._kind = model_id, "vision"

    def chat(self, req):
        self._ensure_text(req["model"])
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        prompt = self._aux.apply_chat_template(req["messages"], add_generation_prompt=True)
        sampler = make_sampler(temp=float(req.get("temperature", 0.7)))
        text = generate(self._model, self._aux, prompt,
                        max_tokens=int(req.get("max_tokens", 512)),
                        sampler=sampler, verbose=False)
        return (text or "").strip()

    def vision(self, req):
        self._ensure_vision(req["model"])
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        formatted = apply_chat_template(self._aux, self._model.config, req["prompt"], num_images=1)
        result = generate(self._model, self._aux, formatted,
                          image=[str(req["image_path"])],
                          max_tokens=int(req.get("max_tokens", 400)),
                          temperature=float(req.get("temperature", 0.7)),
                          verbose=False)
        text = getattr(result, "text", result)
        return (text or "").strip()


def _start_parent_watchdog():
    """Exit if the parent (Flask) dies, so we never orphan and hold GPU memory.

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
            break
        try:
            if cmd == "chat":
                _emit({"text": engine.chat(req)})
            elif cmd == "vision":
                _emit({"text": engine.vision(req)})
            else:
                _emit({"error": f"unknown cmd: {cmd}"})
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            _emit({"error": str(e)})
    _log("exiting")


if __name__ == "__main__":
    main()
