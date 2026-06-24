---
module: src
purpose: "Top-level Python package: supervisor entry point plus sub-packages for collectors, storage, web, toolpath, ipc, and hud."
layer: utility
key_files:
  supervisor.py: "Process entry point — loads YAML config, builds all collectors, starts BatchWriter, runs asyncio event loop with resilient task wrappers"
  clock.py: "now_ns() (monotonic, for latency math) and epoch_ns() (wall-clock, for DB timestamps)"
entry_point: "python -m src.supervisor --config config/collectors.local.yaml"
---

- `supervisor.py` reads `robots[]`, `esp32_devices[]`, and `schneider_devices[]` from the YAML. Each robot entry spawns two `OpcCollector` instances: one for `kuka` and one for `fronius`, with `device_id` set to `{id}_kuka` / `{id}_fronius`.
- Every collector task is wrapped in `_resilient()`, which catches all exceptions, logs them, and restarts after 5 seconds. A single device disconnect never kills the process.
- `QueueLogHandler` is added to the root logger after queue creation — all `INFO+` log records flow into the same queue as telemetry, appearing in the dashboard's System Log panel as `device_id='system'`.
- `clock.py` separates monotonic (`time.perf_counter_ns()`) from wall-clock (`time.time_ns()`). Use `now_ns()` for inter-stage latency; use `epoch_ns()` only for the `ts_epoch_ns` field in `DataRecord`. Do not use `time.time_ns()` for latency — NTP/DST jumps corrupt the measurement.
- The `hud/` sub-package is empty and reserved for a future heads-up display process. Do not add code there without a design decision.
