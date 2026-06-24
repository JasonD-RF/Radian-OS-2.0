"""
api.py — FastAPI HTTP and WebSocket routes for the camera bridge.

Endpoints
---------
GET  /stream        MJPEG multipart stream — open in browser or VLC.
GET  /snapshot      Single JPEG frame — one HTTP response per call.
GET  /inference     Latest AI inference result as JSON.
WS   /ws            WebSocket — server pushes a new result on every frame.
GET  /health        System status: FPS, inference latency, camera info.
POST /config        Runtime config update (exposure, quality, etc.).

All video data flows through MjpegBroadcaster — frames are encoded once
and fanned out to however many /stream clients are connected.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

# These are injected at startup by main.py — see app.state.*
# Using app.state avoids module-level circular imports.
app = FastAPI(
    title="Jetson Camera Bridge",
    description=(
        "Real-time GigE Vision camera capture with TensorRT AI inference. "
        "Standalone edge service for Jetson Orin Nano."
    ),
    version="1.0.0",
)

# ── MJPEG Stream ──────────────────────────────────────────────────────────────

@app.get(
    "/stream",
    summary="Live MJPEG camera stream",
    responses={200: {"content": {"multipart/x-mixed-replace": {}}}},
)
async def stream():
    """Continuous MJPEG stream — open in any browser or media player.

    Frames are encoded once and delivered to all connected clients.
    Disconnect by closing the browser tab or HTTP connection.
    """
    broadcaster = app.state.broadcaster
    return StreamingResponse(
        broadcaster.subscribe(),
        media_type="multipart/x-mixed-replace; boundary=jpgboundary",
        headers={"Cache-Control": "no-store, no-cache"},
    )


# ── Snapshot ──────────────────────────────────────────────────────────────────

@app.get(
    "/snapshot",
    summary="Single JPEG frame",
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def snapshot():
    """Return the latest camera frame as a single JPEG image.

    Useful for thumbnails, periodic logging, or clients that cannot consume
    a multipart stream.
    """
    pipeline = app.state.pipeline
    quality = app.state.config.stream_jpeg_quality

    frame = pipeline.latest_frame()
    if frame is None:
        return Response(status_code=503, content="No frame available yet.")

    ok, jpeg_buf = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        return Response(status_code=500, content="JPEG encode failed.")

    return Response(
        content=jpeg_buf.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


# ── Inference result ──────────────────────────────────────────────────────────

@app.get(
    "/inference",
    summary="Latest AI inference result",
)
async def inference():
    """Return the most recent inference result as JSON.

    For the passthrough task this includes frame timing and image statistics.
    For real AI tasks it will include model-specific output (detections,
    measurements, classifications, etc.).

    Poll this endpoint OR use the WebSocket /ws for push-based updates.
    """
    pipeline = app.state.pipeline
    result = pipeline.latest_result()
    if not result:
        return JSONResponse({"ok": False, "error": "no_result_yet"}, status_code=503)
    return JSONResponse(result)


# ── WebSocket push ────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket that pushes a new inference result on every inferred frame.

    Connect from the dashboard:
        const ws = new WebSocket("ws://192.168.1.230:8765/ws");
        ws.onmessage = (e) => { const result = JSON.parse(e.data); ... };

    The server closes the connection cleanly when the bridge shuts down.
    """
    await websocket.accept()
    pipeline = app.state.pipeline
    logger.info("WebSocket client connected from %s", websocket.client)

    try:
        while True:
            # Block until InferenceWorker produces a new result.
            # wait_for_result runs in a thread pool so it does not block the
            # event loop.
            result = await asyncio.get_event_loop().run_in_executor(
                None, pipeline.wait_for_result, 1.0
            )
            if result:
                await websocket.send_json(result)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    summary="System health and performance metrics",
)
async def health():
    """Return current system status for monitoring and the Radian Forge dashboard.

    Fields
    ------
    ok:              True when the camera is open and frames are flowing.
    camera:          Camera metadata (model, serial, resolution, SDK).
    fps_actual:      Measured capture frame rate over the last second.
    inference_ms:    Latency of the last inference call in milliseconds.
    stream_clients:  Number of active /stream connections.
    uptime_s:        Seconds since the bridge started.
    """
    pipeline = app.state.pipeline
    broadcaster = app.state.broadcaster
    start_time = app.state.start_time
    latest = pipeline.latest_result()

    return JSONResponse({
        "ok": pipeline.latest_frame() is not None,
        "camera": pipeline.camera_info(),
        "fps_actual": round(pipeline.fps(), 1),
        "inference_ms": latest.get("inference_ms", 0.0),
        "stream_clients": broadcaster.subscriber_count,
        "frames_encoded": broadcaster.frames_encoded,
        "last_encode_ms": round(broadcaster.last_encode_ms, 2),
        "uptime_s": round(time.time() - start_time, 1),
        "task": app.state.config.task_class,
        "engine_loaded": app.state.engine.ready,
    })


# ── Runtime config update ─────────────────────────────────────────────────────

@app.post(
    "/config",
    summary="Update runtime settings",
)
async def update_config(body: dict):
    """Adjust settings without restarting the bridge.

    Supported keys
    --------------
    stream_jpeg_quality (int 1-95):  JPEG quality for /stream and /snapshot.
    camera_exposure_us  (float):     Camera exposure in microseconds (0 = auto).
    camera_gain         (float):     Analogue gain (0 = auto).

    Changes to exposure and gain are applied to the live camera immediately.
    Other config changes require a bridge restart.
    """
    cfg = app.state.config
    provider = app.state.pipeline._provider
    changed = []

    if "stream_jpeg_quality" in body:
        q = int(body["stream_jpeg_quality"])
        cfg.stream_jpeg_quality = max(1, min(95, q))
        changed.append(f"stream_jpeg_quality={cfg.stream_jpeg_quality}")

    if "camera_exposure_us" in body:
        us = float(body["camera_exposure_us"])
        try:
            if hasattr(provider, "_camera") and provider._camera:
                if us > 0:
                    provider._camera.ExposureAuto.SetValue("Off")
                    provider._camera.ExposureTime.SetValue(us)
                else:
                    provider._camera.ExposureAuto.SetValue("Continuous")
                changed.append(f"exposure_us={us}")
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    if "camera_gain" in body:
        gain = float(body["camera_gain"])
        try:
            if hasattr(provider, "_camera") and provider._camera:
                if gain > 0:
                    provider._camera.GainAuto.SetValue("Off")
                    provider._camera.Gain.SetValue(gain)
                else:
                    provider._camera.GainAuto.SetValue("Continuous")
                changed.append(f"gain={gain}")
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return JSONResponse({"ok": True, "changed": changed})
