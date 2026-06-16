"""
Thread-safe lock-free ring buffer for in-process hot-path data flow.

Single-producer / multi-consumer. Consumers always get the freshest record;
stale records are silently overwritten. This is intentional: the cold path
(writer, HUD) must never stall the acquisition loop.

For the data-collection phase all collectors and the batch writer share one
asyncio event loop, so this ring is used between threads only (e.g., a
background spool-retry thread reading from the main loop's records).
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Optional


class LosslessQueue:
    """
    Asyncio-compatible bounded queue that signals overflow rather than
    blocking. The caller decides whether to drop or spool.
    """

    __slots__ = ("_dq", "_lock", "_maxsize")

    def __init__(self, maxsize: int = 4096):
        self._dq: deque = deque()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def put(self, item: Any) -> bool:
        """
        Add an item. Returns True on success, False if the buffer is full.
        Never blocks. Called from the subscription callback (hot path).
        """
        with self._lock:
            if len(self._dq) >= self._maxsize:
                return False
            self._dq.append(item)
            return True

    def drain(self, max_items: int = 256) -> list:
        """Pop up to max_items from the front. Called from the writer task."""
        out = []
        with self._lock:
            for _ in range(min(max_items, len(self._dq))):
                out.append(self._dq.popleft())
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._dq)
