"""
streaming/broadcaster.py — Shared MJPEG frame broadcaster.

Problem it solves
-----------------
A naive MJPEG implementation encodes a JPEG for every connected client on
every frame.  With 3 browser tabs open at 60 fps, that is 180 JPEG encodes
per second — wasted CPU that competes with inference.

MjpegBroadcaster encodes each frame ONCE and delivers the same bytes to
every waiting HTTP client via asyncio queues.  CPU cost is O(1) with respect
to the number of connected clients.

How it integrates
-----------------
  broadcaster = MjpegBroadcaster(quality=85)

  # Called from InferencePipeline._capture_loop (sync thread):
  broadcaster.publish_sync(frame_array)

  # Called from FastAPI route handler (async):
  async def stream_endpoint():
      async for chunk in broadcaster.subscribe():
          yield chunk
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import AsyncIterator, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# MJPEG boundary string — must not appear inside any JPEG payload.
_BOUNDARY = b"--jpgboundary"
_CONTENT_TYPE = b"Content-Type: image/jpeg\r\n\r\n"


class MjpegBroadcaster:
    """Encodes frames once and fans out to all MJPEG stream subscribers."""

    def __init__(self, quality: int = 85, max_queue_depth: int = 4) -> None:
        """
        Parameters
        ----------
        quality:
            JPEG encode quality (1-95).  85 is a good balance of quality and
            bandwidth.  Lower values reduce bandwidth at cost of image fidelity.
        max_queue_depth:
            Maximum number of frames buffered per subscriber.  When a slow
            client falls behind, oldest frames are dropped rather than
            accumulating — this keeps latency bounded.
        """
        self._quality = quality
        self._max_queue_depth = max_queue_depth

        # Set of active asyncio queues — one per connected HTTP client.
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

        # Track the event loop so publish_sync (called from a sync thread) can
        # schedule coroutines onto it.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Statistics
        self._frames_encoded: int = 0
        self._last_encode_ms: float = 0.0

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the asyncio event loop.  Called from main.py at startup."""
        self._loop = loop

    # ── Producer ──────────────────────────────────────────────────────────────

    def publish_sync(self, frame: np.ndarray) -> None:
        """Encode *frame* as JPEG and deliver it to all subscribers.

        This method is called from the synchronous capture thread.  It uses
        asyncio.run_coroutine_threadsafe to safely enqueue the encoded bytes
        onto the async queues.
        """
        if not self._queues or self._loop is None:
            return  # no subscribers, skip encode

        t0 = time.monotonic()

        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._quality]
        ok, jpeg_buf = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            logger.warning("JPEG encode failed — skipping frame.")
            return

        jpeg_bytes: bytes = jpeg_buf.tobytes()
        self._last_encode_ms = (time.monotonic() - t0) * 1000.0
        self._frames_encoded += 1

        # Build the MJPEG multipart chunk once, share the same bytes object
        # with all subscribers (no per-subscriber copy).
        chunk = (
            _BOUNDARY + b"\r\n"
            + _CONTENT_TYPE
            + jpeg_bytes + b"\r\n"
        )

        asyncio.run_coroutine_threadsafe(
            self._broadcast(chunk), self._loop
        )

    async def _broadcast(self, chunk: bytes) -> None:
        """Deliver *chunk* to all subscriber queues (async, runs on event loop)."""
        with self._lock:
            queues = set(self._queues)

        for q in queues:
            if q.qsize() >= self._max_queue_depth:
                # Client is too slow — drop the oldest frame to cap latency.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    # ── Consumer ──────────────────────────────────────────────────────────────

    async def subscribe(self) -> AsyncIterator[bytes]:
        """Async generator that yields MJPEG chunks for one HTTP client.

        Yields frames as they arrive.  When the client disconnects, FastAPI
        cancels the generator and the finally block removes the queue.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_depth)
        with self._lock:
            self._queues.add(q)
        logger.debug("MJPEG subscriber added  total=%d", len(self._queues))

        try:
            while True:
                chunk = await asyncio.wait_for(q.get(), timeout=5.0)
                yield chunk
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            with self._lock:
                self._queues.discard(q)
            logger.debug("MJPEG subscriber removed  total=%d", len(self._queues))

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._queues)

    @property
    def frames_encoded(self) -> int:
        return self._frames_encoded

    @property
    def last_encode_ms(self) -> float:
        return self._last_encode_ms
