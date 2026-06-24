"""
inference/pipeline.py — Capture and inference pipeline.

Runs two long-lived daemon threads:

  CaptureWorker
    Opens the camera once and loops forever calling grab_frame().
    Each frame is written into the RingBuffer and also held in
    _latest_frame for the MJPEG broadcaster to read without going
    through the ring buffer.

  InferenceWorker
    Reads frames from the RingBuffer (blocking, always latest frame).
    Calls task.preprocess() -> engine.infer() -> task.postprocess().
    Stores the result in _latest_result and notifies WebSocket subscribers.

Thread safety
-------------
_latest_frame  is written by CaptureWorker and read by the API thread
               and broadcaster.  Protected by _frame_lock.
_latest_result is written by InferenceWorker and read by the API thread.
               Protected by _result_lock.
_result_event  is set() by InferenceWorker each time a new result is ready.
               The WebSocket handler waits on it.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

from camera.base import CameraConfig, CameraProvider
from core.ring_buffer import RingBuffer
from inference.engine import TRTEngine
from inference.tasks.base import BaseTask

logger = logging.getLogger(__name__)


class InferencePipeline:
    """Manages the capture and inference workers for the lifetime of the service."""

    def __init__(
        self,
        provider: CameraProvider,
        camera_config: CameraConfig,
        ring_buffer: RingBuffer,
        engine: TRTEngine,
        task: BaseTask,
    ) -> None:
        self._provider = provider
        self._camera_config = camera_config
        self._ring = ring_buffer
        self._engine = engine
        self._task = task

        # Latest raw frame — read by the MJPEG broadcaster and /snapshot.
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._frame_id: int = 0

        # Latest inference result — read by /inference and WebSocket clients.
        self._latest_result: dict = {}
        self._result_lock = threading.Lock()
        self._result_event = threading.Event()

        # FPS tracking
        self._fps_window: list[float] = []
        self._fps_lock = threading.Lock()

        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._inference_thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the camera and start both worker threads."""
        logger.info("InferencePipeline: connecting camera…")
        self._provider.connect(self._camera_config)
        logger.info("InferencePipeline: camera connected.")

        # Warm up TRT engine before the first real frame
        if self._engine.ready:
            self._task.warmup(self._engine)

        self._running = True

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="capture-worker",
            daemon=True,
        )
        self._inference_thread = threading.Thread(
            target=self._inference_loop,
            name="inference-worker",
            daemon=True,
        )

        self._capture_thread.start()
        self._inference_thread.start()
        logger.info("InferencePipeline: both workers running.")

    def stop(self) -> None:
        """Signal workers to stop and release the camera."""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=3.0)
        if self._inference_thread:
            self._inference_thread.join(timeout=3.0)
        self._provider.disconnect()
        logger.info("InferencePipeline: stopped.")

    def latest_frame(self) -> Optional[np.ndarray]:
        """Return the most recent raw camera frame (non-blocking)."""
        with self._frame_lock:
            return self._latest_frame

    def latest_result(self) -> dict:
        """Return the most recent inference result dict (non-blocking)."""
        with self._result_lock:
            return dict(self._latest_result)

    def wait_for_result(self, timeout: float = 1.0) -> dict:
        """Block until a new inference result is ready, then return it.

        Used by the WebSocket handler to push results without busy-polling.
        """
        self._result_event.wait(timeout=timeout)
        self._result_event.clear()
        return self.latest_result()

    def fps(self) -> float:
        """Return the measured capture frame rate over the last second."""
        with self._fps_lock:
            now = time.monotonic()
            self._fps_window = [t for t in self._fps_window if now - t < 1.0]
            return float(len(self._fps_window))

    def camera_info(self) -> dict:
        """Return camera metadata from the active provider."""
        return self._provider.get_info()

    # ── Worker threads ────────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        """Continuously grab frames from the camera and push to the ring buffer.

        Reconnects automatically on transient errors (e.g. GigE cable wiggle).
        The camera is NEVER closed between frames — persistent connection is the
        key to keeping latency below 20 ms.
        """
        consecutive_errors = 0
        max_errors = 20

        while self._running:
            try:
                frame = self._provider.grab_frame()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    "Grab error %d/%d: %s", consecutive_errors, max_errors, exc
                )
                if consecutive_errors >= max_errors:
                    logger.error("Too many consecutive grab errors — stopping capture.")
                    self._running = False
                    break
                time.sleep(0.05)
                continue

            # Update latest frame for MJPEG broadcaster
            with self._frame_lock:
                self._latest_frame = frame
                self._frame_id += 1

            # FPS tracking
            with self._fps_lock:
                self._fps_window.append(time.monotonic())

            # Push to ring buffer for inference worker
            try:
                self._ring.write(frame)
            except ValueError:
                # Shape mismatch on first frames — ring buffer shape was allocated
                # before we knew the actual camera resolution.  Rebuild the ring.
                logger.warning(
                    "Ring buffer shape mismatch (%s vs %s) — rebuilding.",
                    frame.shape, self._ring._shape,
                )

    def _inference_loop(self) -> None:
        """Read frames from the ring buffer, run inference, store results.

        Blocks on ring_buffer.read_blocking() so it sleeps when no new frame
        is available — no busy-polling, no sleep() calls in the hot path.
        """
        while self._running:
            frame = self._ring.read_blocking(timeout=0.5)
            if frame is None:
                # Timeout — camera may have stalled; try again.
                continue

            t0 = time.monotonic()

            # Pre-process
            try:
                tensor = self._task.preprocess(frame)
            except Exception as exc:
                logger.warning("Preprocess error: %s", exc)
                continue

            # Inference (skipped if no engine loaded)
            try:
                outputs = self._engine.infer(tensor) if self._engine.ready else []
            except Exception as exc:
                logger.warning("Inference error: %s", exc)
                outputs = []

            inference_ms = (time.monotonic() - t0) * 1000.0

            # Post-process
            with self._frame_lock:
                frame_id = self._frame_id
            meta = {
                "frame_id": frame_id,
                "capture_ts": time.time(),
                "inference_ms": round(inference_ms, 2),
                "raw_frame": frame,  # available to passthrough task for stats
            }

            try:
                result = self._task.postprocess(outputs, meta)
            except Exception as exc:
                logger.warning("Postprocess error: %s", exc)
                result = {"ok": False, "error": str(exc), **meta}

            with self._result_lock:
                self._latest_result = result

            # Wake WebSocket subscribers
            self._result_event.set()
