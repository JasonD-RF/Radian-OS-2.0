---
module: "streaming"
purpose: "Single-encode MJPEG fan-out broadcaster. Encodes one JPEG per frame regardless of how many HTTP clients are connected."
layer: presentation
files:
  broadcaster.py:
    class: MjpegBroadcaster
    publish: "publish_sync(frame: np.ndarray) → None — called from sync capture thread; encodes JPEG once"
    subscribe: "subscribe() → AsyncGenerator[bytes, None] — async generator; each HTTP client calls this"
    slow_clients: "Oldest frame dropped from a slow client's queue — never blocks the capture thread"
    subscriber_queues: "Each connected HTTP client gets its own asyncio.Queue fed from the single encode"
pattern: "Encode once O(1) regardless of client count, vs naive O(n) encode-per-client"
stats:
  - frames_encoded
  - last_encode_ms
  - subscriber_count
config:
  STREAM_JPEG_QUALITY: "0–100; controls bandwidth vs quality tradeoff (default ~85); set in .env"
---

- `publish_sync()` is called from the sync CaptureWorker thread; it uses `loop.call_soon_threadsafe()` to deliver bytes to the asyncio event loop without blocking capture.
- `subscribe()` is an `async for` generator for use in FastAPI route handlers — each connected browser tab gets its own subscriber.
- If inference is expensive and the camera thread slows, the broadcaster still runs at full camera FPS independent of inference FPS.
- Adding WebSocket frame push: subscribe to the same broadcaster and forward bytes over the WS connection.
- The `/stream` endpoint delivers `multipart/x-mixed-replace; boundary=frame` — compatible with `<img src="/stream">` in HTML (no JS needed for basic display).
