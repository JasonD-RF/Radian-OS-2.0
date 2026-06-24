"""
main.py — Entry point for the Jetson Camera Bridge.

Startup sequence
----------------
1. Load config from environment variables (.env file).
2. Build the selected camera provider (basler_pylon or aravis).
3. Load / build the TRT inference engine (skipped in passthrough mode).
4. Allocate the ring buffer using the configured capacity.
5. Build the inference pipeline (capture thread + inference thread).
6. Build the MJPEG broadcaster (shared JPEG encoder).
7. Wire the pipeline's capture loop to the broadcaster (publish_sync callback).
8. Register startup / shutdown hooks on the FastAPI app.
9. Start uvicorn.

Running
-------
    # Development
    python main.py

    # Via systemd (production)
    sudo systemctl start jetson_cam_bridge
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import uvicorn

from api import app
from camera import build_provider
from camera.base import CameraConfig
from core.config import cfg
from core.ring_buffer import RingBuffer
from inference.engine import TRTEngine
from inference.pipeline import InferencePipeline
from inference.tasks import build_task
from streaming.broadcaster import MjpegBroadcaster

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Wiring ────────────────────────────────────────────────────────────────────

def build_camera_config() -> CameraConfig:
    return CameraConfig(
        serial=cfg.camera_serial,
        width=cfg.camera_width,
        height=cfg.camera_height,
        fps_limit=cfg.camera_fps_limit,
        pixel_format=cfg.camera_pixel_format,
        exposure_us=cfg.camera_exposure_us,
        gain=cfg.camera_gain,
        packet_size=cfg.camera_packet_size,
        inter_packet_delay_ns=cfg.camera_inter_packet_delay_ns,
    )


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Jetson Camera Bridge starting…")
    logger.info("Config: provider=%s  task=%s  port=%d", cfg.camera_provider, cfg.task_class, cfg.bridge_port)

    # Camera provider
    provider = build_provider(cfg.camera_provider)

    # TRT engine (may be no-op if no engine or onnx path)
    engine = TRTEngine(
        engine_path=cfg.engine_path,
        onnx_path=cfg.onnx_path,
        fp16=cfg.inference_fp16,
        batch_size=cfg.inference_batch_size,
    )

    # AI task
    task = build_task(cfg.task_class)

    # Ring buffer — use a placeholder shape; actual camera shape is set after connect.
    # The pipeline will handle a shape mismatch on the first write and log a warning.
    ring = RingBuffer(
        capacity=cfg.ring_buffer_capacity,
        shape=(1080, 1920),  # conservative default; overwritten on connect
        dtype="uint8",
    )

    # Broadcaster
    broadcaster = MjpegBroadcaster(quality=cfg.stream_jpeg_quality)
    broadcaster.set_loop(asyncio.get_event_loop())

    # Pipeline
    pipeline = InferencePipeline(
        provider=provider,
        camera_config=build_camera_config(),
        ring_buffer=ring,
        engine=engine,
        task=task,
    )

    # Wire capture loop → broadcaster:
    # Patch the pipeline's _capture_loop to also call broadcaster.publish_sync
    # on each successful frame.  This is done by monkey-patching _latest_frame
    # write to also publish.
    _orig_capture_loop = pipeline._capture_loop

    def _patched_capture_loop():
        import threading
        import time as _time

        consecutive_errors = 0
        max_errors = 20

        while pipeline._running:
            try:
                frame = pipeline._provider.grab_frame()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.warning("Grab error %d/%d: %s", consecutive_errors, max_errors, exc)
                if consecutive_errors >= max_errors:
                    logger.error("Too many consecutive grab errors — stopping.")
                    pipeline._running = False
                    break
                _time.sleep(0.05)
                continue

            with pipeline._frame_lock:
                pipeline._latest_frame = frame
                pipeline._frame_id += 1

            with pipeline._fps_lock:
                pipeline._fps_window.append(_time.monotonic())

            # Publish to MJPEG broadcaster (encodes once, fans out to all HTTP clients)
            broadcaster.publish_sync(frame)

            try:
                pipeline._ring.write(frame)
            except ValueError:
                logger.warning(
                    "Ring buffer shape mismatch (%s vs %s) — frame skipped.",
                    frame.shape, pipeline._ring._shape,
                )

    pipeline._capture_loop = _patched_capture_loop

    # Inject shared objects into app.state so route handlers can access them.
    app.state.config = cfg
    app.state.pipeline = pipeline
    app.state.broadcaster = broadcaster
    app.state.engine = engine
    app.state.start_time = time.time()

    # Start the pipeline (opens camera + starts threads).
    # If the camera SDK isn't installed yet, log the error but keep the
    # server running so /health still responds while the SDK is being set up.
    try:
        pipeline.start()
        logger.info("Jetson Camera Bridge ready — http://%s:%d", cfg.bridge_host, cfg.bridge_port)
    except Exception as exc:
        logger.error("Camera failed to start: %s", exc)
        logger.warning("Bridge is running without a camera. Install SDK then restart the service.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Jetson Camera Bridge shutting down…")
    if hasattr(app.state, "pipeline"):
        app.state.pipeline.stop()
    logger.info("Shutdown complete.")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=cfg.bridge_host,
        port=cfg.bridge_port,
        log_level="info",
        # Reload is disabled in production — the systemd unit handles restart.
        reload=False,
        # Single worker: the pipeline threads handle concurrency internally.
        workers=1,
    )
