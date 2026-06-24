---
module: src/storage
purpose: "Storage layer — BatchWriter drains the asyncio.Queue into TimescaleDB with SQLite spool fallback for DB outages."
layer: storage
key_files:
  writer.py: "BatchWriter: asyncpg-based batch inserter using the unnest() trick; includes spool-retry coroutine"
  spool.py: "SQLite WAL-mode durable spool; write() on DB failure, drain()/confirm_drain() on retry"
db_table: radian_os.telemetry
spool_file: "./spool.db"
protocol: "asyncpg (native async PostgreSQL, no thread pool)"
---

- `BatchWriter.run()` drains the queue in batches of up to `batch_size` (default 100) records, flushing every `flush_interval_s` (default 0.2s) or when the batch fills. A brief `asyncio.sleep(0.01)` yield runs between empty polls.
- The insert uses the asyncpg `unnest()` array trick: one `conn.execute()` call inserts the entire batch in a single round-trip. **Do not rewrite this to a loop** — it will regress throughput significantly.
- On any DB exception, the entire batch is handed to `Spool.write()` — records are serialised as JSON blobs in SQLite `spool.db` using `PRAGMA journal_mode=WAL, PRAGMA synchronous=NORMAL`.
- `run_spool_retry()` runs as a separate asyncio task: wakes every 30s, reads up to 500 records via `Spool.drain()`, attempts to write them, calls `Spool.confirm_drain()` only on success. Partial failure leaves records in the spool.
- `Spool.purge_old()` removes records older than 7 days after each successful retry batch, preventing unbounded `spool.db` growth.
- Common bug: `spool.db` left open by a previous crash causes WAL lock contention. If BatchWriter fails to start, check for stale `spool.db-shm` and `spool.db-wal` files.
- The `values` column in `telemetry` is `JSONB`. The insert converts each record's `values` dict via `json.dumps(..., default=str)`. Non-serialisable types become strings rather than crashing the batch.
