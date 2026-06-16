"""
Durable local spool using SQLite WAL mode.

When the TimescaleDB writer fails (network down, DB overloaded), records are
written here. A background retry task drains the spool when connectivity
recovers. No data is ever silently dropped — overflow is surfaced as a log
warning with a count.

SQLite WAL mode gives concurrent readers+writer without blocking; journals
survive process crashes; the file lives on local disk even if the network
partition lasts for hours.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import aiosqlite

logger = logging.getLogger("spool")

SCHEMA = """
CREATE TABLE IF NOT EXISTS spool (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch_ns INTEGER NOT NULL,
    device_id   TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    payload     TEXT    NOT NULL,   -- JSON blob of the full DataRecord
    retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS spool_id_idx ON spool(id);
"""


class Spool:
    """Async durable spool backed by SQLite WAL."""

    def __init__(self, path: str | Path):
        self._path = str(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        count = await self.pending_count()
        if count:
            logger.info("Spool opened — %d records pending retry", count)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def write(self, records: List[Dict[str, Any]]) -> None:
        """Append records. Called when the primary writer fails."""
        if not records:
            return
        rows = [
            (
                r["ts_epoch_ns"],
                r["device_id"],
                r["source"],
                json.dumps(r, default=str),
            )
            for r in records
        ]
        await self._db.executemany(
            "INSERT INTO spool(ts_epoch_ns, device_id, source, payload) VALUES(?,?,?,?)",
            rows,
        )
        await self._db.commit()
        logger.warning("Spooled %d records to disk (DB unavailable)", len(records))

    async def drain(self, max_batch: int = 500) -> List[Dict[str, Any]]:
        """
        Read up to max_batch oldest records without deleting them yet.
        Call confirm_drain() with the returned IDs after a successful write.
        """
        async with self._db.execute(
            "SELECT id, payload FROM spool ORDER BY id ASC LIMIT ?", (max_batch,)
        ) as cur:
            rows = await cur.fetchall()
        return [{"_spool_id": r[0], **json.loads(r[1])} for r in rows]

    async def confirm_drain(self, ids: List[int]) -> None:
        """Delete successfully written records."""
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self._db.execute(f"DELETE FROM spool WHERE id IN ({placeholders})", ids)
        await self._db.commit()

    async def pending_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM spool") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def purge_old(self, older_than_s: float = 86400 * 7) -> int:
        """Remove records older than N seconds. Prevents unbounded disk growth."""
        cutoff = int((time.time() - older_than_s) * 1e9)
        async with self._db.execute(
            "DELETE FROM spool WHERE ts_epoch_ns < ?", (cutoff,)
        ) as cur:
            count = cur.rowcount
        await self._db.commit()
        if count:
            logger.info("Purged %d old spool records (> %.0f days)", count, older_than_s / 86400)
        return count
