"""
Base collector interface and shared data model.

All collectors produce DataRecord instances and push them into an asyncio.Queue.
The queue is the only coupling between collectors and the storage layer —
neither side knows about the other's internals.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..clock import epoch_ns, now_ns


@dataclass(slots=True)
class DataRecord:
    """
    A single timestamped snapshot from any collector.

    ts_epoch_ns   : wall-clock nanoseconds — used as the DB primary timestamp.
    ts_mono_ns    : monotonic nanoseconds — use for latency math between stages.
    device_id     : human ID of the source device (e.g. 'chesty_kuka').
    source        : collector type string (e.g. 'opc_kuka', 'opc_fronius',
                    'esp32_http', 'schneider_modbus').
    changed_key   : which variable triggered this emit (None = periodic snapshot).
    values        : full snapshot of all known variables for this device.
    """

    ts_epoch_ns: int
    ts_mono_ns: int
    device_id: str
    source: str
    changed_key: Optional[str]
    values: Dict[str, Any]

    @staticmethod
    def now(device_id: str, source: str,
            changed_key: Optional[str] = None,
            values: Optional[Dict[str, Any]] = None) -> "DataRecord":
        return DataRecord(
            ts_epoch_ns=epoch_ns(),
            ts_mono_ns=now_ns(),
            device_id=device_id,
            source=source,
            changed_key=changed_key,
            values=values or {},
        )


class BaseCollector(ABC):
    """
    Async collector lifecycle contract.

    Subclasses push DataRecord instances into `out_queue`. They must be
    resilient: catch connection errors, log them, back off, and reconnect.
    The supervisor will also restart the coroutine if it raises.
    """

    def __init__(self, device_id: str, out_queue: asyncio.Queue):
        self.device_id = device_id
        self._queue = out_queue
        self._running = False

    def _emit(self, record: DataRecord) -> None:
        """Non-blocking put. Logs overflow but never blocks the hot path."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            import logging
            logging.getLogger(self.__class__.__name__).warning(
                "Output queue full for %s — cold path lagging, record dropped",
                self.device_id,
            )

    @abstractmethod
    async def run(self) -> None:
        """Start collecting. Must loop forever and handle reconnects internally."""
        ...

    async def stop(self) -> None:
        self._running = False
