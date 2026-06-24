---
module: src/ipc
purpose: "In-process communication primitives for hot-path data flow between acquisition and consumer tasks."
layer: utility
key_files:
  ring_buffer.py: "LosslessQueue: thread-safe bounded deque with non-blocking put() that returns bool on overflow"
---

- `LosslessQueue` is a thread-safe bounded deque. `put()` returns `False` on overflow instead of blocking or raising — callers decide whether to drop or spool. `drain()` removes up to N items atomically under a lock.
- Despite the filename (`ring_buffer.py`), the class is not a true ring buffer — it is a bounded deque that signals overflow. Old items are NOT overwritten; new items are rejected when full.
- This module exists because `asyncio.Queue` is not thread-safe across threads, and `queue.Queue` blocks. `LosslessQueue` satisfies both constraints.
- All acquisition code currently runs in one asyncio event loop, so the primary use-case is potential future threading (e.g. a background spool-retry thread or a Rust extension).
- Do not use this as a replacement for the main `asyncio.Queue` — that queue's `await queue.get()` is needed by the BatchWriter for proper backpressure.
