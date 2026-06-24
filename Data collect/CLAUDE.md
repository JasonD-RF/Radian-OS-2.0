---
module: "."
purpose: "Project root for Radian OS 2.0 — a manufacturing data OS that collects telemetry from robotic welding cells and stores it in TimescaleDB."
layer: utility
entry_points:
  supervisor: "python -m src.supervisor --config config/collectors.local.yaml"
  web_server: "python -m src.web.server --config config/collectors.local.yaml"
  toolpath_writer: "python -m src.toolpath.writer --config config/collectors.local.yaml"
key_files:
  start.ps1: "Windows start/stop/status; uses Get-CimInstance (not Get-Process) to filter by CommandLine"
  schema.sql: "TimescaleDB DDL: radian_os.telemetry hypertable + kuka_state and fronius_state views"
  schema_toolpath.sql: "DDL for radian_os.print_jobs and radian_os.toolpath_points hypertables"
  requirements.txt: "asyncua, asyncpg, aiohttp, pymodbus, aiosqlite, pyyaml"
  project_map.yaml: "Machine-readable index of all files, devices, ports, and writable OPC UA nodes"
db:
  host: "localhost:5432"
  container: radianos-db-1
  database: radian_forge
  schema: radian_os
  tables: [telemetry, toolpath_points, print_jobs]
ports:
  dashboard: 8765
  timescaledb: 5432
devices:
  chesty_kuka: "192.168.1.44:4840 (OPC-UA, KRC4)"
  chesty_fronius: "192.168.1.193:4840 (OPC-UA)"
  mattis_kuka: "192.168.1.151:4840 (OPC-UA, KRC4)"
  mattis_fronius: "192.168.1.152:4840 (OPC-UA)"
  esp32_cell_sensor: "192.168.1.169:80 (HTTP)"
  schneider_plc: "192.168.1.132:502 (Modbus-TCP)"
gitignored:
  - config/collectors.local.yaml
  - "config/*.pem"
  - spool.db / spool.db-wal / spool.db-shm
  - logs/
---

- Three separate Python processes share nothing at the process level — all coordination is through TimescaleDB. They can be started/stopped independently.
- `start.ps1` uses `Get-CimInstance Win32_Process` (not `Get-Process`) to filter on `CommandLine -like "*radian OS 2_0*"`. PowerShell 5.1's `Get-Process` does not expose the `CommandLine` property.
- Docker runs in WSL2 (`Ubuntu-24.04`). Schema application uses `wsl -d Ubuntu-24.04 -u root -- bash -c "docker exec -i radianos-db-1 psql ..."`. Both schema files are idempotent (`IF NOT EXISTS`).
- `config/collectors.local.yaml` is gitignored and contains live IPs and credentials. Never commit it. `config/collectors.yaml` is the committed template.
- Common first-stop for "nothing is collecting": check `logs/supervisor.err` and `logs/webserver.err`. The supervisor logs `"No collectors configured"` if the YAML fails to parse.
- To start manually without the script: activate venv at `../venv/Scripts/python.exe`, then run each of the three modules in separate terminals with `--config config/collectors.local.yaml`.
- The single shared `asyncio.Queue` (maxsize 8192) is the only coupling between the acquisition layer and the storage layer. Collectors use `put_nowait()` — they drop and log a warning on overflow, never block.

## Edge AI Integration Notes
- `project_map.yaml` in this folder is the primary context file for edge AI agents — read it first to orient in the codebase.
- `config/safety_bounds.yaml.example` defines the allowed parameter ranges for any AI-initiated OPC UA writes.
- The Action API (Layer 3, future) will be exposed from `src/web/server.py` at `/api/config`, `/api/opc-write`, and `/api/restart`.
- `ai_control_enabled` flag in `collectors.local.yaml` (future) will gate all AI-initiated writes.
