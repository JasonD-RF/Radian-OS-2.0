---
module: "inference"
purpose: "TensorRT GPU inference engine wrapper and capture/inference pipeline orchestration for the Jetson Orin Nano."
layer: inference
files:
  engine.py:
    purpose: "TensorRT engine lifecycle — loads a pre-built .trt file OR builds from ONNX via trtexec on first run"
    key_method: "Engine.load_or_build(onnx_path, trt_path) → Engine"
    inference: "infer(input_array: np.ndarray) → list[np.ndarray] — synchronous; uses CUDA streams internally"
    memory: "Allocates pinned CPU + GPU buffers at load time; no per-call allocation"
  pipeline.py:
    purpose: "Runs CaptureWorker and InferenceWorker as daemon threads; coordinates via ring buffer"
    fps_tracking: "Rolling 1-second window; exposed on /health"
threads:
  CaptureWorker:
    role: "Continuously grabs frames from camera provider; writes to ring buffer; holds _latest_frame for MJPEG broadcaster"
    blocking: "grab_frame() blocks up to timeout_ms; camera timeout is logged and retried"
  InferenceWorker:
    role: "Reads latest frame from ring buffer; calls task.preprocess() → engine.infer() → task.postprocess(); stores in _latest_result"
    decoupling: "Never blocks the capture thread — inference falling behind drops frames, not vice versa"
precision:
  fp16:
    description: "FP16 inference is 2x faster than FP32 on Jetson Orin Nano with negligible accuracy loss for vision tasks"
    enable: "INFERENCE_FP16=true in .env"
    requirement: "ONNX model must be exported with FP16 support (standard for YOLO, EfficientDet, RT-DETR)"
trt_cache:
  description: "ONNX → .trt compilation via trtexec takes several minutes on first run; result is written to ENGINE_PATH and reused"
  pattern: "Check ENGINE_PATH exists → load; else compile ONNX_PATH → save to ENGINE_PATH → load"
  implication: "First production startup is slow; all subsequent starts are seconds"
---

- The ring buffer is the only coupling between CaptureWorker and InferenceWorker. If inference is slower than capture, old frames are overwritten — never the reverse.
- New AI tasks (seam tracking, weld defect detection, spatter monitoring) are added via `inference/tasks/` without any changes to `pipeline.py` or `engine.py`.
- FP16 requires the ONNX model to declare FP16 support at export time. Use `torch.onnx.export(..., opset_version=17)` with `half()` model weights.
- `engine.py` uses CUDA streams for async H2D/D2H transfers — `infer()` is synchronous from the caller's perspective but non-blocking on the GPU.
- To run without a real TRT model during development: set `TASK_CLASS=passthrough`; the PassthroughTask skips `engine.infer()` entirely.
