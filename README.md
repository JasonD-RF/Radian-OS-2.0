# Radian OS 2.0 — Data Collection Stack

Real-time telemetry collection from KUKA KRC4 robots and Fronius welders via OPC UA, stored in TimescaleDB and served on a live web dashboard.

---

## What it does

- Connects to KUKA KRC4 controllers and Fronius TPS welders over OPC UA
- Streams 60+ variables per robot (TCP position, joint angles, motion state, ArcTech print globals, welder current/voltage/power)
- Writes time-series data to TimescaleDB (PostgreSQL extension for time-series)
- Serves a live dashboard at `http://localhost:8765`
- Buffers data locally in SQLite (`spool.db`) if the database is unreachable

---

## Network map (Radian Forge lab)

| Device           | IP              | Port | Protocol     |
|------------------|-----------------|------|--------------|
| Chesty KUKA KRC4 | 192.168.1.44    | 4840 | OPC UA       |
| Chesty Fronius   | 192.168.1.193   | 4840 | OPC UA       |
| Mattis KUKA KRC4 | 192.168.1.151   | 4840 | OPC UA       |
| Mattis Fronius   | 192.168.1.152   | 4840 | OPC UA       |
| ESP32 (pending)  | 192.168.1.169   | 80   | HTTP/JSON    |
| Schneider PLC (pending) | 192.168.1.132 | 502 | Modbus TCP |
| TimescaleDB      | localhost       | 5432 | PostgreSQL   |
| Web dashboard    | localhost       | 8765 | HTTP         |

---

## Prerequisites

- **Docker** (Docker Engine on Linux, or Docker Engine in WSL2 on Windows)
- **Python 3.11+**
- **OPC UA client certificate** (`client_cert.pem` + `client_key.pem`) — get these from the dev machine or regenerate with asyncua's `generate_certificates` tool

---

## First-time setup on a new machine

### 1. Clone the repo

```bash
git clone https://github.com/JasonD-RF/Radian-Forge.git
cd Radian-Forge
```

### 2. Create the config file

```bash
cd "Data collect"
cp config/collectors.local.yaml.example config/collectors.local.yaml
```

Edit `config/collectors.local.yaml`:
- Replace `<ABSOLUTE_PATH_TO_REPO>` with the full path to this folder
- Set robot IPs (see network map above)
- Set KUKA OPC UA password (default: `kuka`)
- The DSN can stay as-is if running TimescaleDB locally with the default docker-compose

### 3. Copy OPC UA certificates

```bash
# Copy from dev machine or generate fresh ones
cp /path/to/client_cert.pem "Data collect/client_cert.pem"
cp /path/to/client_key.pem  "Data collect/client_key.pem"
```

The certs are used for encrypted OPC UA connections to KUKA KRC4.
The cert must also be trusted on the KRC4 controller (done once via KUKA smartHMI).

### 4. Start everything

**On Linux:**
```bash
cd "Data collect"
chmod +x start.sh
./start.sh
```

**On Windows (PowerShell):**
```powershell
cd "Data collect"
.\start.ps1
```

The start script will:
1. Ensure Docker is running
2. Start the TimescaleDB container
3. Apply the schema (safe to re-run, uses `IF NOT EXISTS`)
4. Start the supervisor (OPC UA collectors)
5. Start the web server
6. Open the dashboard in your browser

---

## Linux server setup (Docker Engine, no WSL2)

On a fresh Ubuntu 22.04 / 24.04 server:

```bash
# Install Docker Engine
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Install Python 3.11+
sudo apt install python3 python3-venv -y

# Clone and start
git clone https://github.com/JasonD-RF/Radian-Forge.git
cd Radian-Forge/"Data collect"
cp config/collectors.local.yaml.example config/collectors.local.yaml
# --- edit collectors.local.yaml ---
chmod +x start.sh
./start.sh
```

No WSL2, no bridge module workaround needed — Docker runs natively on Linux.

---

## Windows dev machine notes (WSL2)

The WSL2 kernel on some machines doesn't auto-load the `bridge` module that Docker needs.
This has been fixed on the dev machine (Jason's machine) with:
- `/etc/modules-load.d/docker-bridge.conf` — auto-loads `bridge` and `br_netfilter` at boot
- `systemctl enable docker` — Docker starts under systemd automatically
- `radianos-db.service` — TimescaleDB starts after Docker

After a reboot on the dev machine, Docker and TimescaleDB come up on their own.
Only the Python supervisor and web server need to be started manually (via `start.ps1`).

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

TimescaleDB connection:
```
Host:     localhost:5432
Database: radian_forge
User:     radian
Password: forge_local
Schema:   radian_os
```

Useful queries:
```sql
-- Last 5 minutes of data for chesty KUKA
SELECT ts, changed_key, values
FROM radian_os.telemetry
WHERE device_id = 'chesty_kuka'
  AND ts > now() - interval '5 minutes'
ORDER BY ts DESC;

-- Current weld state
SELECT * FROM radian_os.fronius_state
WHERE ts > now() - interval '1 minute'
ORDER BY ts DESC LIMIT 10;
```

The schema is in `Data collect/schema.sql`. Safe to re-run at any time — all statements use `IF NOT EXISTS`.

---

## Adding a new robot cell

1. Add a new entry under `robots:` in `collectors.local.yaml` (copy an existing block)
2. Set the new `id`, KUKA IP, and Fronius IP
3. Restart the supervisor: `./start.sh stop && ./start.sh`

No code changes needed for additional KUKA + Fronius pairs.

---

## Adding a new sensor type

- **ESP32 (HTTP/JSON)**: Add to `esp32_devices:` in config — see `src/collectors/esp32_collector.py`
- **Schneider PLC (Modbus)**: Add to `schneider_devices:` in config — see `src/collectors/schneider_collector.py`
- Both need their payload/register map confirmed before enabling

---

## File structure

```
radian OS 2_0/
├── docker-compose.yml          # TimescaleDB container
├── README.md                   # this file
└── Data collect/
    ├── start.sh                # Linux start script
    ├── start.ps1               # Windows start script
    ├── requirements.txt        # Python dependencies
    ├── schema.sql              # Database schema (safe to re-run)
    ├── config/
    │   ├── collectors.local.yaml.example   # template — copy and fill in
    │   └── collectors.local.yaml           # GITIGNORED — your local config
    ├── src/
    │   ├── supervisor.py       # starts and manages all collectors
    │   ├── collectors/         # opc_collector, esp32, schneider
    │   ├── storage/            # writer.py (TimescaleDB), spool.py (SQLite buffer)
    │   └── web/server.py       # live dashboard web server
    ├── static/dashboard.html   # frontend
    └── logs/                   # supervisor.log, webserver.log (gitignored)
```
