"""
inference/engine.py — TensorRT engine wrapper for Jetson Orin Nano.

Handles the full model lifecycle:
  * Loading a pre-built .trt engine file, OR
  * Building a .trt engine from an ONNX file (runs trtexec, saves result).
  * Allocating pinned CPU + GPU memory buffers once at load time.
  * Running synchronous and asynchronous (CUDA stream) inference.

Why TensorRT?
-------------
TensorRT compiles an ONNX model into a GPU-optimised execution plan for the
specific Jetson hardware.  On Orin Nano with FP16:
  * YOLOv8n: ~23 ms per frame  (>40 FPS headroom alongside 60 FPS capture)
  * MobileNetV3-small: ~8 ms per frame

The compiled .trt engine is hardware-specific — a model built on Orin Nano
will NOT run on a desktop GPU.  Rebuild when changing hardware.

Workflow for bringing up a new model
-------------------------------------
1. Train your model in PyTorch, export to ONNX:
       torch.onnx.export(model, dummy_input, "model.onnx", opset_version=17)
2. Place model.onnx on the Jetson.
3. Set ONNX_PATH=/path/to/model.onnx in .env.
4. On first run, the bridge calls trtexec to build model.trt (may take minutes).
5. On subsequent runs, ENGINE_PATH=model.trt is loaded directly (< 1 s).
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class TRTEngine:
    """Loads and runs a TensorRT engine for real-time inference.

    Usage
    -----
        engine = TRTEngine(engine_path="model.trt", fp16=True)
        # or build from ONNX on first run:
        engine = TRTEngine(onnx_path="model.onnx", fp16=True)

        output_arrays = engine.infer(input_array)
    """

    def __init__(
        self,
        engine_path: str = "",
        onnx_path: str = "",
        fp16: bool = True,
        batch_size: int = 1,
    ) -> None:
        """
        Parameters
        ----------
        engine_path:
            Path to a pre-built .trt engine file.  If supplied and exists,
            the engine is loaded directly without invoking trtexec.
        onnx_path:
            Path to an ONNX model.  Used when engine_path is empty or does
            not exist yet.  trtexec is called to build the .trt file, which
            is then saved next to the ONNX file for future runs.
        fp16:
            Enable FP16 precision.  Orin Nano Ampere supports FP16 natively;
            expect ~2x speedup vs FP32 with negligible accuracy loss for most
            vision models.
        batch_size:
            Inference batch size.  Keep at 1 for real-time single-frame use.
        """
        self._engine = None
        self._context = None
        self._inputs: list[dict] = []
        self._outputs: list[dict] = []
        self._stream = None
        self._fp16 = fp16
        self._batch_size = batch_size

        if engine_path and Path(engine_path).exists():
            self._load_engine(engine_path)
        elif onnx_path and Path(onnx_path).exists():
            built_path = self._build_from_onnx(onnx_path, fp16)
            self._load_engine(built_path)
        else:
            logger.info(
                "TRTEngine: no engine or ONNX path provided — "
                "inference will be skipped (passthrough mode)."
            )

    # ── Build from ONNX ───────────────────────────────────────────────────────

    def _build_from_onnx(self, onnx_path: str, fp16: bool) -> str:
        """Call trtexec to compile ONNX -> TRT engine.  Can take several minutes."""
        onnx_path = Path(onnx_path)
        engine_path = onnx_path.with_suffix(".trt")

        logger.info(
            "Building TRT engine from %s (fp16=%s).  This may take several minutes…",
            onnx_path,
            fp16,
        )

        cmd = [
            "trtexec",
            f"--onnx={onnx_path}",
            f"--saveEngine={engine_path}",
            "--explicitBatch",
        ]
        if fp16:
            cmd.append("--fp16")

        t0 = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.monotonic() - t0

        if result.returncode != 0:
            raise RuntimeError(
                f"trtexec failed after {elapsed:.1f}s:\n{result.stderr[-2000:]}"
            )

        logger.info("TRT engine built in %.1f s → %s", elapsed, engine_path)
        return str(engine_path)

    # ── Load engine ───────────────────────────────────────────────────────────

    def _load_engine(self, engine_path: str) -> None:
        """Deserialise a .trt engine file and allocate I/O buffers."""
        try:
            import tensorrt as trt
            import pycuda.driver as cuda
            import pycuda.autoinit  # noqa: F401 — initialises CUDA context
        except ImportError as exc:
            raise RuntimeError(
                "TensorRT or PyCUDA not installed.  "
                "On JetPack 6: sudo apt install python3-libnvinfer python3-pycuda"
            ) from exc

        logger.info("Loading TRT engine: %s", engine_path)
        t0 = time.monotonic()

        trt_logger = trt.Logger(trt.Logger.WARNING)
        runtime = trt.Runtime(trt_logger)

        with open(engine_path, "rb") as f:
            engine_data = f.read()

        self._engine = runtime.deserialize_cuda_engine(engine_data)
        if self._engine is None:
            raise RuntimeError(f"Failed to deserialise TRT engine: {engine_path}")

        self._context = self._engine.create_execution_context()
        self._stream = cuda.Stream()

        # Allocate pinned host memory and device memory for each I/O binding.
        # This is done once at load time — never during inference.
        self._inputs = []
        self._outputs = []
        self._bindings = []

        for i in range(self._engine.num_io_tensors):
            name = self._engine.get_tensor_name(i)
            shape = tuple(self._engine.get_tensor_shape(name))
            dtype_trt = self._engine.get_tensor_dtype(name)
            dtype_np = trt.nptype(dtype_trt)

            # Replace dynamic batch dimension with our fixed batch size
            shape = tuple(self._batch_size if d == -1 else d for d in shape)

            host_mem = cuda.pagelocked_empty(shape, dtype_np)  # pinned memory
            device_mem = cuda.mem_alloc(host_mem.nbytes)

            self._bindings.append(int(device_mem))

            if self._engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
                self._inputs.append({"name": name, "host": host_mem, "device": device_mem, "shape": shape})
                logger.debug("TRT input  %d: %s  shape=%s  dtype=%s", i, name, shape, dtype_np)
            else:
                self._outputs.append({"name": name, "host": host_mem, "device": device_mem, "shape": shape})
                logger.debug("TRT output %d: %s  shape=%s  dtype=%s", i, name, shape, dtype_np)

        logger.info(
            "TRT engine loaded in %.1f s  inputs=%d  outputs=%d",
            time.monotonic() - t0,
            len(self._inputs),
            len(self._outputs),
        )

    # ── Inference ─────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        """True if the engine is loaded and ready for inference."""
        return self._engine is not None

    def infer(self, input_array: np.ndarray) -> list[np.ndarray]:
        """Run synchronous inference and return output arrays.

        Parameters
        ----------
        input_array:
            Numpy array matching the first (and usually only) input tensor
            shape.  dtype must match the model's expected input dtype.

        Returns
        -------
        list of np.ndarray
            One array per model output, in ONNX output order.
            Returns an empty list if the engine is not loaded.
        """
        if not self.ready:
            return []

        import pycuda.driver as cuda

        # Copy input to pinned memory then H2D transfer
        np.copyto(self._inputs[0]["host"], input_array.reshape(self._inputs[0]["shape"]))
        cuda.memcpy_htod_async(self._inputs[0]["device"], self._inputs[0]["host"], self._stream)

        # Execute
        self._context.execute_async_v2(
            bindings=self._bindings, stream_handle=self._stream.handle
        )

        # D2H transfer for all outputs
        results = []
        for out in self._outputs:
            cuda.memcpy_dtoh_async(out["host"], out["device"], self._stream)

        # Synchronise the CUDA stream — blocks until all GPU work is done.
        self._stream.synchronize()

        for out in self._outputs:
            results.append(out["host"].copy())

        return results
