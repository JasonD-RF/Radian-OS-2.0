# Radian OS 2.0 — Data Collection Stack

Real-time telemetry collection from KUKA KRC4 robots and Fronius welders via OPC UA, stored in TimescaleDB and served on a live web dashboard.

---

## What it does

- Connects to KUKA KRC4 controllers and Fronius TPS welders over OPC UA
- Streams 60+ variables per robot (TCP position, joint angles, motion state, ArcTech globals, welder current/voltage/power)
- Writes time-series data to TimescaleDB (PostgreSQL extension for time-series)
- Serves a live dashboard at `http://localhost:8765` with:
  - **Machine tab bar** — one tab per robot cell, auto-generated from config; switching tabs shows only that cell's data
  - **Device cards** — live KUKA and Fronius OPC UA nodes, updating in real time via SSE
  - **Active coordinate frames** — base and tool frame XYZ/ABC for each KUKA
  - **ArcTech globals** — KRL process-control variables polled every 2 seconds
  - **3D toolpath visualizer** — playback and live-follow of recorded TCP paths, color-coded by arc state
  - **Live event log** — per-machine SSE change stream, buffered on tab switch
- Buffers data locally in SQLite (`spool.db`) if the database is unreachable

Adding a new robot cell requires **no code changes** — edit `collectors.local.yaml` and restart.

---

## Network map (Radian Forge lab)

| Device                  | IP              | Port | Protocol     |
|-------------------------|-----------------|------|--------------|
| Chesty — KUKA KRC4      | 192.168.1.44    | 4840 | OPC UA       |
| Chesty — Fronius welder | 192.168.1.193   | 4840 | OPC UA       |
| Mattis — KUKA KRC4      | 192.168.1.151   | 4840 | OPC UA       |
| Mattis — Fronius welder | 192.168.1.152   | 4840 | OPC UA       |
| ESP32 sensor (pending)  | 192.168.1.169   | 80   | HTTP/JSON    |
| Schneider PLC (pending) | 192.168.1.132   | 502  | Modbus TCP   |
| TimescaleDB             | localhost       | 5432 | PostgreSQL   |
| Web dashboard           | localhost       | 8765 | HTTP         |

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
cp /path/to/client_cert.pem "Data collect/client_cert.pem"
cp /path/to/client_key.pem  "Data collect/client_key.pem"
```

The certs must also be trusted on the KRC4 controller (done once via KUKA smartHMI → Certificate Manager).

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
3. Start the supervisor (OPC UA collectors + toolpath writer)
4. Start the web server
5. Open `http://localhost:8765` in the browser

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

---

## Daily operation

```bash
./start.sh            # start everything
./start.sh status     # check what's running
./start.sh stop       # stop supervisor and web server (leaves DB running)
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
```

Schema files (safe to re-run):
- `Data collect/schema.sql` — telemetry hypertable
- `Data collect/schema_toolpath.sql` — print_jobs + toolpath_points hypertable

---

## Adding a new robot cell

1. Add a new entry under `robots:` in `collectors.local.yaml` (copy an existing block, give it a unique `id`)
2. Set `kuka.url` and/or `fronius.url` for the new cell, set `enabled: true`
3. Restart the supervisor

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

## File structure

```
radian OS 2_0/
├── docker-compose.yml              # TimescaleDB container definition
├── README.md                       # this file
└── Data collect/
    ├── start.sh                    # Linux start script
    ├── start.ps1                   # Windows start script
    ├── requirements.txt            # Python dependencies
    ├── schema.sql                  # Main telemetry schema (safe to re-run)
    ├── schema_toolpath.sql         # Toolpath schema (safe to re-run)
    ├── config/
    │   ├── collectors.local.yaml.example   # template — copy and fill in
    │   └── collectors.local.yaml           # GITIGNORED — local credentials
    ├── src/
    │   ├── supervisor.py           # Starts and manages all collectors
    │   ├── collectors/             # opc_collector.py, esp32_collector.py, schneider_collector.py
    │   ├── storage/                # spool.py (SQLite offline buffer)
    │   ├── toolpath/
    │   │   └── writer.py           # Reads OPC UA events, writes toolpath_points to DB
    │   └── web/
    │       └── server.py           # Live dashboard — all HTML/CSS/JS embedded, zero static files
    ├── client_cert.pem             # GITIGNORED — OPC UA client certificate
    ├── client_key.pem              # GITIGNORED — OPC UA client key
    └── logs/                       # GITIGNORED — supervisor.log, webserver.log
```
