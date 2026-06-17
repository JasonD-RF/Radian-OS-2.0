"""
QueueLogHandler — forwards Python log records into the shared DataRecord queue
so they appear in the live browser dashboard under device_id='system'.

Attach to the root logger in supervisor.py after the queue is created:
    handler = QueueLogHandler(record_queue, level=logging.INFO)
    logging.getLogger().addHandler(handler)
"""
from __future__ import annotations

import asyncio
import logging

from .base import DataRecord
from ..clock import epoch_ns, now_ns


class QueueLogHandler(logging.Handler):

    def __init__(self, queue: asyncio.Queue, level: int = logging.INFO):
        super().__init__(level=level)
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            dr = DataRecord(
                ts_epoch_ns=int(record.created * 1e9),
                ts_mono_ns=now_ns(),
                device_id="system",
                source="log",
                changed_key=record.levelname,
                values={
                    "message": record.getMessage(),
                    "logger": record.name,
                    "level": record.levelname,
                    "levelno": record.levelno,
                },
            )
            self._queue.put_nowait(dr)
        except asyncio.QueueFull:
            pass  # never block or raise from inside a log handler
        except Exception:
            self.handleError(record)
