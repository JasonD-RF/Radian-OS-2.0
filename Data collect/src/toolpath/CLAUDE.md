---
module: src/toolpath
purpose: "Standalone TCP position recorder — polls the latest telemetry snapshots from TimescaleDB and writes filtered XYZ positions to radian_os.toolpath_points."
layer: storage
key_files:
  writer.py: "ToolpathWriter: per-cell state machine (idle/active/paused), deduplication, print_jobs lifecycle, asyncpg connection"
db_tables:
  radian_os.toolpath_points: "Hypertable: one row per sampled TCP position during P_Active"
  radian_os.print_jobs: "One row per robot program run; tracks resumed_count, status, total_points"
entry_point: "python -m src.toolpath.writer --config config/collectors.local.yaml"
collection_gate: "program_state == 'P_Active'"
poll_interval: "50ms (default)"
dedup_threshold: "0.5mm (default)"
---

- This is a completely separate process from the supervisor. It does not share memory or queues with the collector layer — reads from `radian_os.telemetry` via its own asyncpg connection.
- The collection gate is `program_state == 'P_Active'`. No TCP points are recorded during `P_Stop`, interpass cleaning, or any pause state.
- Each robot cell has a `CellState` instance tracking: state (`idle | active | paused`), the current `job_id`, `program_name`, and last TCP position. State transitions are logged at INFO level.
- Deduplication: a point is skipped if its distance from the last recorded point is less than `dedup_mm` (0.5mm). Prevents duplicate points when the robot is stationary with `P_Active` true.
- Job resume logic: if the same `program_name` restarts within `resume_window_h` (default 24h), the existing `print_jobs` row is reused and `resumed_count` incremented. A new job is created only if the program name changes or the window elapses.
- Fronius arc state (`arc_on`, `seam_number`) is read from the latest Fronius telemetry row. If Fronius data is older than 30 seconds, `arc_on` defaults to `False` (stale data guard).
- KUKA telemetry data older than 10 seconds causes the state machine to transition to `paused`. Handles controller disconnects gracefully.
- Common bug: toolpath points stop recording while `P_Active` is clearly true — check Fronius data freshness and whether `telemetry` has recent rows for the expected `device_id`.
- To debug: run with `--log-level DEBUG` to see every tick, state transition, and point write.
