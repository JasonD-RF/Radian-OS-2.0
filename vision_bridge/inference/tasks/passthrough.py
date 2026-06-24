"""
inference/tasks/passthrough.py — Development placeholder task (no AI model).

PassthroughTask does not require a TensorRT engine.  It receives every frame,
computes basic statistics (mean intensity, frame timing), and returns them as
the inference result.

Use this task to:
  * Verify the full capture → inference → WebSocket pipeline works before
    a real model is ready.
  * Measure the raw throughput of the capture + encoding layer.
  * Check camera exposure / gain settings by watching mean_intensity.

Switch to a real task by setting TASK_CLASS=<your_task> in .env and
providing ENGINE_PATH or ONNX_PATH.
"""

from __future__ import annotations

import time

import numpy as np

from inference.tasks.base import BaseTask


class PassthroughTask(BaseTask):
    """No-model task that returns frame timing and basic image statistics."""

    # No model input shape needed, but we declare one so the pipeline code
    # does not have to special-case tasks without an engine.
    input_shape = (1, 480, 640)
    input_dtype = np.float32

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Resize to input_shape and normalise to [0, 1].

        Even though no model will consume this tensor, running the resize
        lets us measure the cost of the pre-processing step in isolation.
        """
        import cv2  # lazy import — only needed at runtime

        target_h, target_w = self.input_shape[1], self.input_shape[2]

        # Resize if needed
        if frame.shape[0] != target_h or frame.shape[1] != target_w:
            frame_resized = cv2.resize(
                frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR
            )
        else:
            frame_resized = frame

        # Normalise to float32 [0, 1]
        tensor = frame_resized.astype(np.float32) / 255.0

        # Add channel dim for mono: (H, W) -> (1, H, W)
        if tensor.ndim == 2:
            tensor = tensor[np.newaxis, :]

        return np.ascontiguousarray(tensor)

    def postprocess(self, outputs: list[np.ndarray], meta: dict) -> dict:
        """Return frame statistics as the inference result.

        The *outputs* list will be empty because there is no engine, so we
        compute statistics from the raw frame stored in meta by the pipeline.
        """
        raw_frame: np.ndarray = meta.get("raw_frame")

        stats: dict = {}
        if raw_frame is not None:
            stats = {
                # Mean pixel intensity — useful for checking exposure.
                "mean_intensity": float(np.mean(raw_frame)),
                # Standard deviation — low value = flat/underexposed image.
                "std_intensity": float(np.std(raw_frame)),
                # Min/max for quick sanity check.
                "min_intensity": int(raw_frame.min()),
                "max_intensity": int(raw_frame.max()),
            }

        return {
            "ok": True,
            "task": "passthrough",
            "frame_id": meta.get("frame_id", 0),
            "capture_ts": meta.get("capture_ts", time.time()),
            "inference_ms": meta.get("inference_ms", 0.0),
            **stats,
        }
