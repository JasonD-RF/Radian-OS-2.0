"""
Async batch writer: drains the shared record queue and writes to TimescaleDB.

Design:
  - Pulls records from the asyncio queue in batches to amortize INSERT overhead.
  - Uses asyncpg (native async PostgreSQL) — no blocking calls, no thread pool.
  - On failure, spools records to SQLite so nothing is lost.
  - A separate spool-retry coroutine periodically re-attempts spooled records.

The hot path (collectors → queue) is completely decoupled from this writer.
If the DB is slow or down the collectors keep running; records back up in the
queue (bounded) and overflow goes to the spool.

Schema: see schema.sql
Table: radian_os.telemetry (hypertable partitioned by ts)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List

import asyncpg

from .spool import Spool
from ..collectors.base import DataRecord
from ..clock import now_ns

logger = logging.getLogger("writer")

# SQL for a single-statement batch insert (asyncpg unnest trick for bulk performance)
_INSERT_SQL = """
INSERT INTO radian_os.telemetry
    (ts, ts_mono_ns, device_id, source, changed_key, values)
SELECT
    to_timestamp(v.ts_epoch_ns / 1e9),
    v.ts_mono_ns,
    v.device_id,
    v.source,
    v.changed_key,
    v.values::jsonb
FROM unnest(
    $1::bigint[], $2::bigint[], $3::text[], $4::text[], $5::text[], $6::text[]
) AS v(ts_epoch_ns, ts_mono_ns, device_id, source, changed_key, values)
ON CONFLICT DO NOTHING
"""


def _record_to_row(r: Dict[str, Any]) -> tuple:
    return (
        int(r["ts_epoch_ns"]),
        int(r["ts_mono_ns"]),
        str(r["device_id"]),
        str(r["source"]),
        r.get("changed_key"),          # nullable
        json.dumps(r.get("values", {}), default=str),
    )


class BatchWriter:
    """
    Drains record_queue into TimescaleDB with store-and-forward on failure.

    Config keys (from collectors.yaml, storage section):
      dsn                  : postgresql://radian:forge_local@localhost:5432/radian_forge
      batch_size           : int, default 100
      flush_interval_s     : float, default 0.2  (max latency before flush)
      spool_path           : str, default ./spool.db
      spool_retry_interval_s : float, default 30.0
      pool_min             : int, default 1
      pool_max             : int, default 4
    """

    def __init__(self, cfg: dict, record_queue: asyncio.Queue):
        self._dsn: str = cfg["dsn"]
        self._batch_size: int = int(cfg.get("batch_size", 100))
        self._flush_interval: float = float(cfg.get("flush_interval_s", 0.2))
        self._spool_path: str = cfg.get("spool_path", "./spool.db")
        self._retry_interval: float = float(cfg.get("spool_retry_interval_s", 30.0))
        self._pool_min: int = int(cfg.get("pool_min", 1))
        self._pool_max: int = int(cfg.get("pool_max", 4))
        self._queue = record_queue
        self._pool: asyncpg.Pool | None = None
        self._spool: Spool | None = None
        self._running = False

    async def start(self) -> None:
        self._spool = Spool(self._spool_path)
        await self._spool.open()
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._pool_min,
            max_size=self._pool_max,
            command_timeout=10,
        )
        logger.info("BatchWriter connected to DB, spool at %s", self._spool_path)

    async def stop(self) -> None:
        self._running = False
        if self._pool:
            await self._pool.close()
        if self._spool:
            await self._spool.close()

    async def run(self) -> None:
        """Main drain loop. Run as an asyncio task."""
        self._running = True
        batch: List[Dict[str, Any]] = []
        last_flush = now_ns()
        flush_ns = int(self._flush_interval * 1e9)

        while self._running:
            # Drain queue up to batch_size
            try:
                while len(batch) < self._batch_size:
                    record = self._queue.get_nowait()
                    # DataRecord dataclass or plain dict (from spool retry)
                    if hasattr(record, "__dataclass_fields__"):
                        batch.append(asdict(record))
                    else:
                        batch.append(record)
            except asyncio.QueueEmpty:
                pass

            elapsed = now_ns() - last_flush
            should_flush = len(batch) >= self._batch_size or (
                batch and elapsed >= flush_ns
            )

            if should_flush:
                await self._flush(batch)
                batch.clear()
                last_flush = now_ns()
            else:
                # Brief yield so other coroutines can run
                await asyncio.sleep(0.01)

        # Final drain on shutdown
        if batch:
            await self._flush(batch)

    async def _flush(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        rows = [_record_to_row(r) for r in records]
        ts_list        = [r[0] for r in rows]
        mono_list      = [r[1] for r in rows]
        device_list    = [r[2] for r in rows]
        source_list    = [r[3] for r in rows]
        changed_list   = [r[4] for r in rows]
        values_list    = [r[5] for r in rows]

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    _INSERT_SQL,
                    ts_list, mono_list, device_list, source_list,
                    changed_list, values_list,
                )
            logger.debug("Wrote %d records to DB", len(records))
        except Exception as exc:
            logger.error("DB write failed (%s) — spooling %d records", exc, len(records))
            await self._spool.write(records)

    async def run_spool_retry(self) -> None:
        """Background task: replay spooled records when DB is back."""
        while self._running:
            await asyncio.sleep(self._retry_interval)
            try:
                pending = await self._spool.pending_count()
                if pending == 0:
                    continue
                logger.info("Spool retry: %d records pending", pending)
                batch = await self._spool.drain(max_batch=500)
                if not batch:
                    continue
                ids = [r.pop("_spool_id") for r in batch]
                rows = [_record_to_row(r) for r in batch]
                ts_list      = [r[0] for r in rows]
                mono_list    = [r[1] for r in rows]
                device_list  = [r[2] for r in rows]
                source_list  = [r[3] for r in rows]
                changed_list = [r[4] for r in rows]
                values_list  = [r[5] for r in rows]
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        _INSERT_SQL,
                        ts_list, mono_list, device_list, source_list,
                        changed_list, values_list,
                    )
                await self._spool.confirm_drain(ids)
                logger.info("Spool retry: replayed %d records successfully", len(ids))
                await self._spool.purge_old()
            except Exception as exc:
                logger.warning("Spool retry failed: %s", exc)
