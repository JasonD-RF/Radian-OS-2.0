"""
inference/tasks/base.py — Abstract AI task interface.

A "task" is the bridge between the raw camera frame and the structured
inference result that gets served on /inference and pushed over WebSocket.

Each task encapsulates:
  * preprocess()  — resize/normalise the frame into a model input tensor.
  * postprocess() — convert raw model outputs into a human-readable dict.

This separation means you can swap the AI model without touching the
capture or streaming layers.

Implementing a new task
-----------------------
1. Create inference/tasks/<name>.py
2. Subclass BaseTask and implement preprocess() and postprocess().
3. Set TASK_CLASS=<name> in .env.
4. The engine.py wrapper handles all TensorRT I/O between those two calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class BaseTask(ABC):
    """Abstract base for all AI inference tasks.

    Subclasses are responsible for model-specific pre- and post-processing.
    The TensorRT engine (inference/engine.py) handles the actual GPU inference
    call between preprocess() and postprocess().
    """

    # Expected input shape for the model: (channels, height, width).
    # Used by engine.py to allocate GPU input buffers.
    # Override in subclasses to match your specific model.
    input_shape: tuple = (1, 640, 480)

    # Numpy dtype of the model input tensor.
    input_dtype: type = np.float32

    @abstractmethod
    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Convert a raw camera frame into a model input tensor.

        Parameters
        ----------
        frame:
            Raw frame from the camera provider.  Shape is (H, W) for mono
            or (H, W, 3) for colour.  dtype is uint8 or uint16.

        Returns
        -------
        np.ndarray
            A contiguous float32 array with shape matching self.input_shape,
            normalised as expected by the model (usually 0.0–1.0 or
            ImageNet-normalised).  Must be on CPU — engine.py copies it to
            GPU.
        """

    @abstractmethod
    def postprocess(self, outputs: list[np.ndarray], meta: dict) -> dict:
        """Convert raw TensorRT output tensors into a result dict.

        Parameters
        ----------
        outputs:
            List of numpy arrays, one per model output tensor, in the same
            order as the ONNX model's output nodes.
        meta:
            Metadata dict with keys: frame_id (int), capture_ts (float),
            inference_ms (float).  Include these in the result dict so the
            dashboard can correlate results with frames.

        Returns
        -------
        dict
            JSON-serialisable result.  Must at minimum contain:
              ok (bool), frame_id (int), inference_ms (float).
            Add task-specific fields (bounding boxes, scores, measurements)
            as needed.
        """

    def warmup(self, engine) -> None:
        """Optional — run one dummy inference to warm up the TRT engine.

        Called once after the engine is loaded, before the first real frame.
        Default implementation does nothing; override if your model has a
        noticeable cold-start cost.
        """
