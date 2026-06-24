---
module: config
purpose: "YAML configuration for all collectors, TimescaleDB DSN, and toolpath writer settings."
layer: config
key_files:
  collectors.yaml: "Committed template — safe to share; contains placeholder NodeIds (fill in placeholders in scalar_nodes)"
  collectors.local.yaml: "Live credentials and real NodeIds — GITIGNORED, never commit this file"
  collectors.local.yaml.example: "Canonical template showing all keys and their defaults"
  safety_bounds.yaml.example: "Template defining min/max limits for all AI-writable OPC UA parameters"
---

- `collectors.local.yaml` is what all three processes actually load. It is a complete file — there is no merge with `collectors.yaml`. The local file must contain all required keys.
- Top-level keys: `storage` (DSN, batch sizes, spool path), `robots[]` (OPC UA endpoints per cell), `esp32_devices[]`, `schneider_devices[]`, and `toolpath` (poll_interval_s, dedup_mm, resume_window_h).
- Each robot entry has `id`, `kuka`, and `fronius` sub-sections. Both are `OpcCollector` instances distinguished by `source: opc_kuka` vs `source: opc_fronius`.
- `scalar_nodes` maps variable names to OPC UA NodeId strings. KUKA NodeIds are discovered via `scripts/discover_kuka.py`. Format: `ns=2;s=...` (namespace 2, string identifier).
- OPC UA security: `security_string` cert paths are relative to the config file's directory, resolved by `_resolve_security_string()` in `opc_collector.py`. Certs: `config/client_cert.pem`, `config/client_key.pem`.
- `enabled: false` on any robot/device sub-section removes it from the collector list at startup without deleting it from config. Use this to temporarily disable a device.
- After editing `collectors.local.yaml`, the supervisor must be restarted — there is no hot-reload. Use `.\start.ps1 -Stop` then `.\start.ps1`.
- `spool_path` defaults to `./spool.db` relative to the working directory (`Data collect/`). Do not move `spool.db` while any process is running.

## Edge AI Integration Notes
- Fields the edge AI is allowed to modify: values inside `robots[].kuka.scalar_nodes` (to add/remove monitored variables), `toolpath.dedup_mm`, `toolpath.poll_interval_s`.
- Fields the edge AI must NOT modify: `storage.dsn`, `robots[].kuka.url`, `robots[].fronius.url`, `security_string`, cert paths.
- After any AI-initiated config change, supervisor restart is required. The future Action API will handle this automatically.
- `safety_bounds.yaml` (future, based on `.example` template) defines the allowed parameter ranges for any AI-initiated OPC UA writes via `/api/krl-var`.
