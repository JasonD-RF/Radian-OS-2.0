---
module: src/collectors
purpose: "Acquisition layer — three concrete collector types that push DataRecord instances into a shared asyncio.Queue."
layer: acquisition
key_files:
  base.py: "DataRecord dataclass (shared data contract) and BaseCollector ABC with non-blocking _emit()"
  opc_collector.py: "OPC UA subscription collector for KUKA (61 nodes) and Fronius welders; uses asyncua"
  esp32_collector.py: "HTTP polling collector for ESP32 at 192.168.1.169; flattens nested JSON to dotted keys"
  schneider_collector.py: "Modbus TCP polling collector for Schneider PLC at 192.168.1.132:502"
  log_handler.py: "QueueLogHandler that injects Python log records into the DataRecord queue as device_id='system'"
devices:
  chesty_kuka: "192.168.1.44:4840"
  chesty_fronius: "192.168.1.193:4840"
  mattis_kuka: "192.168.1.151:4840"
  mattis_fronius: "192.168.1.152:4840"
  esp32_cell_sensor: "192.168.1.169:80"
  schneider_plc: "192.168.1.132:502"
quality_signals:
  - "chesty_fronius / mattis_fronius: arc_voltage, wire_feed_rate, weld_current — primary weld quality indicators"
  - "chesty_kuka / mattis_kuka: gPrintActive, gActiveLayer, gActiveLayerSeam — process state for closed-loop gating"
  - "chesty_kuka / mattis_kuka: $ACT_TOOL, $ACT_FRAME — active coordinate frames"
---

- `DataRecord` is the only contract between collectors and the storage layer. Fields: `ts_epoch_ns`, `ts_mono_ns`, `device_id`, `source`, `changed_key`, `values`. The `values` dict is a full snapshot of all known variables — not just the changed one.
- OPC UA uses server-side subscriptions (not polling). Each node has `QueueSize=10, DiscardOldest=False` so rapid state transitions (e.g. `gNextSeam` changing twice within one publish cycle) are queued server-side and delivered in order — nothing is lost.
- `_ChangeHandler.datachange_notification()` runs inside the asyncio event loop. It maintains a running `_snapshot` dict and only emits when a value actually changes (`old != coerced`).
- The OpcCollector has two supplementary loops: a heartbeat emit every `snapshot_interval_s` (default 5s) and a direct `read_value()` poll every `read_interval_s` (default 2s). The direct read captures TCP position during `P_Stop` or interpass cleaning when subscriptions go quiet. If no successful read for 10 seconds, the session tears down and reconnects.
- `_coerce()` in `opc_collector.py` is the extension point for new asyncua return types. KUKA `ThreeDFrame` structs are already coerced to `{x, y, z, a, b, c}` dicts. Unknown types fall back to `str()`.
- Security strings for KUKA Basic256Sha256 use relative cert paths resolved by `_resolve_security_string(base_dir, ...)`. Certs live at `config/client_cert.pem` and `config/client_key.pem`.
- `_emit()` uses `put_nowait()` and silently drops records when the queue is full — logs a warning but never blocks the hot path. Monitor queue depth to detect collector/writer imbalance.
- Schneider `scale_map` allows raw integer registers to be stored as engineering units. Key format: `{prefix}.{address}` (e.g. `hr.0: 0.1` means register 0 × 0.1 before storage).

## Edge AI Integration Notes
- Best quality signals for closed-loop feedback: `arc_voltage`, `wire_feed_rate`, `weld_current` from Fronius devices.
- `gPrintActive` transitions (False→True) mark the start of a weld seam — use as the gate for quality measurement windows.
- To add a new monitored OPC UA variable: add its NodeId to `scalar_nodes` in `collectors.local.yaml` and restart the supervisor. No code change required.
