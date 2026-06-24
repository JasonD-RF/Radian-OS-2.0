---
module: "inference/tasks"
purpose: "Pluggable, model-agnostic task interface. Each task wraps one AI model with model-specific pre/postprocessing; the pipeline and engine are unaware of model details."
layer: inference
interface:
  base_class: "base.py — abstract BaseTask"
  methods:
    input_shape: "property → tuple (H, W, C) — tells engine what buffer shape to allocate"
    input_dtype: "property → np.dtype — tells engine what precision to allocate (float32 or float16)"
    preprocess: "preprocess(frame: np.ndarray) → np.ndarray — resize, normalize, transpose to model input format"
    postprocess: "postprocess(outputs: list[np.ndarray], meta: dict) → dict — convert raw TRT output to JSON-safe result dict"
    warmup: "warmup(engine) → None — optional; run one dummy inference pass before production use"
tasks:
  passthrough:
    file: passthrough.py
    purpose: "Development placeholder — no TRT engine required; computes basic frame statistics"
    output: "{mean_intensity, frame_width, frame_height, encode_ms} — useful for verifying pipeline end-to-end"
    use_when: "Verifying full pipeline works (camera → ring buffer → inference thread → /inference endpoint) before a real model is ready"
factory:
  file: __init__.py
  function: "build_task(TASK_CLASS: str) → BaseTask"
  registration: "Add new task name → class mapping in __init__.py registry dict"
---

- To add a new task (e.g., `weld_monitor`): create `tasks/weld_monitor.py`, subclass `BaseTask`, implement `preprocess`, `postprocess`, and optionally `warmup`; register `"weld_monitor"` in `__init__.py`.
- `postprocess()` output is what flows to the `/inference` JSON endpoint, the `/ws` WebSocket, and eventually the Radian OS 2.0 Action API — it must be fully JSON-serializable (no raw numpy arrays, no non-primitive types).
- `meta` dict passed to `postprocess` contains timing info (`capture_ts_ns`, `infer_ms`) for latency tracking.
- `preprocess` is responsible for channel ordering (RGB/BGR), normalization range (0–1 vs 0–255), and batch dimension (add axis 0 for single-image inference).
