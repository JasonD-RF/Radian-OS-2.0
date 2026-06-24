# Radian OS 2.0

Real-time telemetry collection from KUKA KRC4/KRC5 robots and Fronius welders via OPC UA, stored in TimescaleDB and served on a live web dashboard. Includes a **Vision Bridge** edge computer (Jetson Orin Nano) for GigE Vision camera capture and TensorRT AI inference — together these form the foundation of a closed-loop weld quality control system.

---

## What it does

### Data Collection Stack (`Data collect/`)
- Connects to KUKA KRC4 and KRC5 controllers and Fronius TPS welders over OPC UA
- Streams 60+ variables per robot (TCP position, joint angles, motion state, ArcTech globals, welder current/voltage/power)
- Writes time-series data to TimescaleDB (PostgreSQL extension for time-series)
- Serves a live dashboard at `http://localhost:8765` with:
  - **Machine tab bar** — one tab per robot cell, auto-generated from config; switching tabs shows only that cell's data
  - **Device cards** — live KUKA and Fronius OPC UA nodes, updating in real time via SSE
  - **Active coordinate frames** — base and tool frame XYZ/ABC for each KUKA
  - **ArcTech globals** — KRL process-control variables polled every 2 seconds
  - **3D toolpath visualizer** — playback and live-follow of recorded TCP paths, color-coded by arc state
  - **Live event log** — per-machine SSE change stream, buffered on tab switch
- Records TCP XYZ positions to a toolpath database during active program execution (`P_Active`)
- Buffers data locally in SQLite (`spool.db`) if the database is unreachable

Adding a new robot cell requires **no code changes** — edit `collectors.local.yaml` and restart.

### Vision Bridge / Edge Computer (`vision_bridge/`)
- Runs on a **Jetson Orin Nano** (192.168.1.230) at the welding cell
- Captures frames from a **Basler GigE Vision camera** (acA2040-55gm) at up to 30fps
- Runs **TensorRT AI inference** on each frame (FP16, hardware-accelerated)
- Streams live MJPEG video at `http://192.168.1.230:8765/stream`
- Future role: evaluates weld quality in real time and posts parameter adjustments to the Action API (`POST /api/krl-var`) to close the control loop

See [`vision_bridge/README.md`](vision_bridge/README.md) for full setup and deployment instructions.

---

## Network map (Radian Forge lab)

| Device                           | IP              | Port | Protocol     |
|----------------------------------|-----------------|------|--------------|
| Chesty — KUKA KRC4               | 192.168.1.44    | 4840 | OPC UA       |
| Chesty — Fronius welder          | 192.168.1.193   | 4840 | OPC UA       |
| Mattis — KUKA KRC4               | 192.168.1.151   | 4840 | OPC UA       |
| Mattis — Fronius welder          | 192.168.1.152   | 4840 | OPC UA       |
| ESP32 sensor (pending)           | 192.168.1.169   | 80   | HTTP/JSON    |
| Schneider PLC (pending)          | 192.168.1.132   | 502  | Modbus TCP   |
| **Jetson Orin Nano** (Vision Bridge) | **192.168.1.230** | **8765** | **HTTP / MJPEG** |
| TimescaleDB                      | localhost       | 5432 | PostgreSQL   |
| Web dashboard                    | localhost       | 8765 | HTTP         |

---

## Prerequisites

- **Docker Desktop** (Windows) or **Docker Engine** (Linux)
- **Python 3.11+**
- **OPC UA client certificate** (`client_cert.pem` + `client_key.pem`) — get from the dev machine or regenerate with asyncua's `generate_certificates` tool

---

## First-time setup on a new machine

### 1. Clone the repo

```bash
git clone https://github.com/JasonD-RF/Radian-OS-2.0.git
cd "Radian-OS-2.0"
```

### 2. Create the Python environment

```bash
cd ..                      # one level above "Data collect"
python -m venv venv
venv\Scripts\activate      # Windows
# or: source venv/bin/activate   (Linux)
pip install -r "Data collect/requirements.txt"
```

### 3. Create the config file

```bash
cd "Data collect"
cp config/collectors.local.yaml.example config/collectors.local.yaml
```

Edit `config/collectors.local.yaml`:
- Set robot IPs (see network map above)
- Set KUKA OPC UA password
- Set the DB DSN (see docker-compose.yml for credentials)

### 4. Copy OPC UA certificates

```bash
cp /path/to/client_cert.pem "Data collect/config/client_cert.pem"
cp /path/to/client_key.pem  "Data collect/config/client_key.pem"
```

The certs must also be trusted on the KUKA controller (done once via KUKA smartHMI → Certificate Manager). Works on both KRC4 and KRC5.

### 5. Start everything

**On Windows (PowerShell):**
```powershell
cd "Data collect"
.\start.ps1
```

**On Linux:**
```bash
cd "Data collect"
chmod +x start.sh
./start.sh
```

The start script will:
1. Ensure Docker is running and TimescaleDB container is healthy
2. Apply the schema (safe to re-run — all statements use `IF NOT EXISTS`)
3. Start the supervisor (OPC UA collectors + BatchWriter)
4. Start the web server
5. Start the toolpath writer
6. Open `http://localhost:8765` in the browser

---

## Windows dev machine notes

### Docker Desktop + WSL2

The dev machine uses Docker Desktop with a standard WSL2 kernel (the `kernel=` line in `~/.wslconfig` must remain **commented out**). A custom kernel compiled without `iso9660` will prevent Docker Desktop from starting.

If Docker Desktop fails:
```powershell
wsl --shutdown
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

### Starting services manually

```powershell
$venv = "C:\Users\thatb\radian OS 2_0\venv\Scripts\python.exe"
Set-Location "C:\Users\thatb\radian OS 2_0\Data collect"

# Each in a separate terminal:
& $venv -m src.supervisor      --config config/collectors.local.yaml
& $venv -m src.toolpath.writer --config config/collectors.local.yaml
& $venv -m src.web.server      --config config/collectors.local.yaml
```

### Stopping services

```powershell
cd "Data collect"
.\start.ps1 -Stop
```

> Note: `start.ps1` uses `Get-CimInstance Win32_Process` (not `Get-Process`) to find Python processes by `CommandLine`. This is required because PowerShell 5.1 does not expose `CommandLine` via `Get-Process`.

---

## Daily operation

```powershell
.\start.ps1           # start everything
.\start.ps1 -Status   # check what's running
.\start.ps1 -Stop     # stop all Python services (leaves DB running)
```

Dashboard: `http://localhost:8765`

---

## Database

TimescaleDB runs in the `radianos-db-1` Docker container. Credentials are set in `docker-compose.yml` and mirrored in `config/collectors.local.yaml` (gitignored).

Useful queries:
```sql
-- Last 5 minutes of KUKA data
SELECT ts, changed_key, values
FROM radian_os.telemetry
WHERE device_id = 'chesty_kuka'
  AND ts > now() - interval '5 minutes'
ORDER BY ts DESC;

-- Toolpath points for a job
SELECT ts, x, y, z, arc_on
FROM radian_os.toolpath_points
WHERE job_id = <job_id>
ORDER BY ts;

-- All completed print jobs
SELECT id, robot_id, program_name, started_at, completed_at, total_points
FROM radian_os.print_jobs
WHERE status = 'complete'
ORDER BY started_at DESC;
```

Schema files (safe to re-run):
- `Data collect/schema.sql` — telemetry hypertable
- `Data collect/schema_toolpath.sql` — print_jobs + toolpath_points hypertable

---

## Adding a new robot cell

1. Add a new entry under `robots:` in `collectors.local.yaml` (copy an existing block, give it a unique `id`)
2. Set `kuka.url` and/or `fronius.url` for the new cell, set `enabled: true`
3. Add a portrait image at `static/assets/robots/{id}.png`
4. Restart the supervisor

The dashboard automatically generates a new machine tab, device cards, frame panel, and ArcTech globals section — no code changes needed.

---

## Adding a new ArcTech KRL variable

1. Open `src/web/server.py` and find `ARCTECH_GROUPS` in the first `<script>` block
2. Add the variable name to the relevant group's `vars` array
3. If it's a boolean, also add it to `AG_BOOL`
4. Restart the web server only — no backend changes needed

---

## Adding a new sensor type

- **ESP32 (HTTP/JSON)**: Add to `esp32_devices:` in config — see `src/collectors/esp32_collector.py`
- **Schneider PLC (Modbus)**: Add to `schneider_devices:` in config — see `src/collectors/schneider_collector.py`

---

## AI-assisted development context

Every folder in the project has a `CLAUDE.md` file with YAML frontmatter describing its purpose, key files, invariants, and common failure modes. These are loaded automatically by Claude Code when working in any subdirectory — no extra context is needed.

The root `Data collect/project_map.yaml` is a single machine-readable index of every folder, file, device, database table, and OPC UA writable node in the project. Read this file first when orienting in the codebase.

The root `Data collect/config/safety_bounds.yaml.example` defines the allowed parameter ranges for any AI-initiated OPC UA writes. Copy to `safety_bounds.yaml` and fill in cell-specific limits before enabling AI control.

---

## Edge AI roadmap

Radian OS is designed to evolve into a closed-loop system where the Jetson Orin Nano edge computer observes the welding process through the Vision Bridge camera and makes real-time parameter adjustments via the Action API.

| Layer | Status | Description |
|---|---|---|
| **1 — Context** | ✅ Complete | `CLAUDE.md` per folder + `project_map.yaml` in both `Data collect/` and `vision_bridge/` give AI a full mental model of both codebases |
| **2 — Observation** | Planned | Computed quality metrics in TimescaleDB: weld quality score, seam deviation, arc stability |
| **3 — Action API** | Planned | REST endpoints in `src/web/server.py` for config patching, OPC UA writes, and service restarts |
| **4 — Safety** | Planned | Parameter bounds validation (`safety_bounds.yaml`), audit log in DB, rollback on quality drop |
| **5 — Inference** | Planned | Jetson (192.168.1.230) reads quality metrics + camera frames → inference → `POST /api/krl-var` to adjust robot/welder in real time |

**How the control loop will work (Layer 5):**
```
Basler camera → Vision Bridge inference → quality score
  └─► if threshold exceeded → POST /api/krl-var on this server
        └─► KRLReader writes OPC UA node on KUKA controller
              └─► Robot/welder parameter adjusted in real time
```

The existing `POST /api/krl-var` endpoint in `src/web/server.py` is the seed of Layer 3 — it already supports OPC UA writes to KUKA KRL variables. The full Action API will extend this with config management and safety validation.

---

## File structure

```
radian OS 2_0/
├── docker-compose.yml              # TimescaleDB container definition
├── README.md                       # this file
├── vision_bridge/                  # Jetson Orin Nano edge computer (see vision_bridge/README.md)
│   ├── README.md                   # Jetson setup, endpoints, deployment guide
│   ├── main.py                     # FastAPI app factory
│   ├── api.py                      # HTTP/WebSocket routes
│   ├── project_map.yaml            # Machine-readable index for Vision Bridge
│   ├── .env.example                # Config template
│   ├── jetson_cam_bridge.service   # systemd unit
│   ├── install.sh                  # One-time Jetson setup
│   ├── camera/                     # GigE Vision drivers (Basler pypylon, Aravis)
│   ├── inference/                  # TensorRT engine + capture/inference pipeline
│   │   └── tasks/                  # Pluggable AI task interface
│   └── streaming/                  # MJPEG single-encode broadcaster
└── Data collect/
    ├── CLAUDE.md                   # AI context: project root
    ├── project_map.yaml            # Machine-readable index of all files, devices, and writable nodes
    ├── start.sh                    # Linux start script
    ├── start.ps1                   # Windows start script
    ├── requirements.txt            # Python dependencies
    ├── schema.sql                  # Main telemetry schema (safe to re-run)
    ├── schema_toolpath.sql         # Toolpath schema (safe to re-run)
    ├── config/
    │   ├── collectors.local.yaml.example   # template — copy and fill in
    │   ├── collectors.local.yaml           # GITIGNORED — local credentials
    │   ├── safety_bounds.yaml.example      # AI parameter limits template
    │   └── CLAUDE.md                       # AI context: config layer
    ├── src/
    │   ├── CLAUDE.md               # AI context: package root
    │   ├── supervisor.py           # Starts and manages all collectors + BatchWriter
    │   ├── clock.py                # Monotonic and wall-clock helpers
    │   ├── collectors/
    │   │   ├── CLAUDE.md           # AI context: acquisition layer
    │   │   ├── opc_collector.py    # OPC UA subscription collector (KUKA + Fronius)
    │   │   ├── esp32_collector.py  # HTTP polling collector
    │   │   ├── schneider_collector.py  # Modbus collector
    │   │   ├── base.py             # DataRecord dataclass + BaseCollector ABC
    │   │   └── log_handler.py      # Async log handler
    │   ├── storage/
    │   │   ├── CLAUDE.md           # AI context: storage layer
    │   │   ├── writer.py           # BatchWriter → TimescaleDB
    │   │   └── spool.py            # SQLite offline buffer
    │   ├── toolpath/
    │   │   ├── CLAUDE.md           # AI context: toolpath recorder
    │   │   └── writer.py           # Reads telemetry, writes toolpath_points to DB
    │   ├── web/
    │   │   ├── CLAUDE.md           # AI context: dashboard + Action API
    │   │   └── server.py           # Live dashboard — all HTML/CSS/JS embedded, zero static files
    │   ├── ipc/
    │   │   ├── CLAUDE.md           # AI context: IPC primitives
    │   │   └── ring_buffer.py      # Thread-safe bounded deque
    │   └── hud/
    │       └── CLAUDE.md           # AI context: reserved for future HUD
    ├── static/
    │   ├── CLAUDE.md               # AI context: static assets
    │   ├── assets/
    │   │   ├── brand/              # Logo files
    │   │   ├── robots/             # Robot portrait images (chesty.png, mattis.png)
    │   │   └── people/             # Staff avatar SVGs
    │   └── vendor/three/           # Three.js r128 (3D visualizer)
    ├── scripts/
    │   ├── CLAUDE.md               # AI context: discovery utilities
    │   ├── discover_kuka.py        # OPC UA address space scanner
    │   └── discover_fronius.py     # Fronius OPC UA endpoint browser
    ├── tests/
    │   ├── CLAUDE.md               # AI context: connectivity tests
    │   └── test_connectivity.py    # Network reachability probes
    ├── logs/                       # GITIGNORED — runtime log output
    │   └── CLAUDE.md               # AI context: log files
    ├── config/client_cert.pem      # GITIGNORED — OPC UA client certificate
    └── config/client_key.pem       # GITIGNORED — OPC UA client key
```
