"""
Radian OS 2.0 — Live Data Dashboard
Run:  python -m src.web.server --config config/collectors.local.yaml
Open: http://localhost:8765
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import yaml
from aiohttp import web

PORT = 8765

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radian OS 2.0</title>
<style>
:root {
  --bg: #0d1117; --card: #161b22; --border: #30363d;
  --green: #3fb950; --yellow: #e3b341; --red: #f85149;
  --text: #c9d1d9; --dim: #8b949e; --blue: #58a6ff; --orange: #ffa657;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
       font-family: 'Segoe UI', system-ui, sans-serif; }

header {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 24px; border-bottom: 1px solid var(--border);
  background: var(--card); position: sticky; top: 0; z-index: 10;
}
.logo { font-size: 1rem; font-weight: 700; color: var(--blue); letter-spacing: -0.02em; }
.logo span { color: var(--dim); font-weight: 400; }
#conn-status { margin-left: auto; font-size: 0.78rem; display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dim); }
.dot.live { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot.dead { background: var(--red); }
#event-count { font-size: 0.75rem; color: var(--dim); }

main { padding: 20px 24px; display: grid; gap: 20px; }
.device-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden;
}
.card-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
}
.card-name { font-weight: 600; font-size: 0.9rem; }
.badge {
  font-size: 0.7rem; font-weight: 700; padding: 2px 9px;
  border-radius: 20px; letter-spacing: 0.04em;
}
.badge-kuka    { background: #1a2f4a; color: var(--blue); }
.badge-fronius { background: #3a2510; color: var(--orange); }

table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
td { padding: 5px 16px; border-bottom: 1px solid #1c2128; vertical-align: middle; }
td:first-child { color: var(--dim); width: 46%; }
td.v-bool-true  { color: var(--green); font-weight: 600; }
td.v-bool-false { color: #4a5568; }
td.v-num  { color: var(--blue); font-variant-numeric: tabular-nums; }
td.v-str  { color: var(--yellow); }
td.flash  { animation: flash 0.5s ease; }
@keyframes flash { from { background: rgba(63,185,80,.18); } to {} }

.arc-on { border-left: 3px solid var(--green); }
.arc-off { border-left: 3px solid var(--border); }

section.log-section h2 {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--dim); margin-bottom: 10px;
}
#log {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; height: 260px; overflow-y: auto;
  font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 0.76rem;
}
.log-row {
  display: grid;
  grid-template-columns: 95px 145px 165px 1fr;
  padding: 3px 14px; border-bottom: 1px solid #1c2128; line-height: 1.7;
}
.log-row:hover { background: #1c2128; }
.l-ts  { color: var(--dim); }
.l-dev { }
.l-dev.kuka    { color: var(--blue); }
.l-dev.fronius { color: var(--orange); }
.l-key { color: var(--green); }
.l-val { color: var(--text); }
.l-hb  { color: #3a3f4a; font-style: italic; grid-column: 3 / 5; }
</style>
</head>
<body>
<header>
  <span class="logo">Radian OS <span>2.0</span></span>
  <span id="event-count"></span>
  <div id="conn-status">
    <div class="dot" id="dot"></div>
    <span id="conn-label">Connecting…</span>
  </div>
</header>

<main>
  <div class="device-grid">
    <div class="card arc-off" id="card-chesty_kuka">
      <div class="card-head">
        <span class="card-name">chesty &mdash; KUKA</span>
        <span class="badge badge-kuka">OPC UA · 28 nodes</span>
      </div>
      <table id="tbl-chesty_kuka"></table>
    </div>
    <div class="card arc-off" id="card-mattis_kuka">
      <div class="card-head">
        <span class="card-name">mattis &mdash; KUKA</span>
        <span class="badge badge-kuka">OPC UA · 28 nodes</span>
      </div>
      <table id="tbl-mattis_kuka"></table>
    </div>
    <div class="card arc-off" id="card-chesty_fronius">
      <div class="card-head">
        <span class="card-name">chesty &mdash; Fronius</span>
        <span class="badge badge-fronius">OPC UA · 19 nodes</span>
      </div>
      <table id="tbl-chesty_fronius"></table>
    </div>
    <div class="card arc-off" id="card-mattis_fronius">
      <div class="card-head">
        <span class="card-name">mattis &mdash; Fronius</span>
        <span class="badge badge-fronius">OPC UA · 19 nodes</span>
      </div>
      <table id="tbl-mattis_fronius"></table>
    </div>
  </div>

  <section class="log-section">
    <h2>Live Event Log</h2>
    <div id="log"></div>
  </section>
</main>

<script>
const KUKA_ROWS = [
  // -- Program / motion -------------------------------------------------------
  ['program_state',    'Program State'],
  ['in_motion',        'In Motion'],
  ['on_path',          'On Path'],
  ['speed_override',   'Speed Override'],
  ['ov_pro',           'Program Override %'],
  ['peri_rdy',         'Periphery Ready'],
  ['drives_off',       'Drives Off'],
  ['task_program_name','Program File'],
  ['project_name',     'Project'],
  // -- TCP position -----------------------------------------------------------
  ['tcp_x','TCP X (mm)'], ['tcp_y','TCP Y (mm)'], ['tcp_z','TCP Z (mm)'],
  // -- Joint axes -------------------------------------------------------------
  ['a1','A1°'], ['a2','A2°'], ['a3','A3°'],
  ['a4','A4°'], ['a5','A5°'], ['a6','A6°'],
  // -- Pyrometer --------------------------------------------------------------
  ['pyrometer_temp_c',    'Pyrometer Filt (°C)'],
  ['pyrometer_temp_raw_c','Pyrometer Raw (°C)'],
  // -- ArcTech print state (live when program running) -----------------------
  ['print_active',     'Print Active'],
  ['print_resume',     'Print Resume'],
  ['print_complete',   'Print Complete'],
  ['stop_cycle',       'Stop Cycle'],
  ['new_print',        'New Print'],
  ['active_layer',     'Active Layer'],
  ['next_layer',       'Next Layer'],
  ['layer_count',      'Layer Count'],
  ['active_layer_seam','Layer Seam'],
  ['next_seam',        'Next Seam'],
  ['active_total_seam','Total Seam #'],
  ['total_seam_count', 'Total Seams Done'],
  ['seams_in_layer',   'Seams In Layer'],
  ['layer_rerun',      'Layer Rerun'],
  ['skip_layer',       'Skip Layer'],
  ['interpass_cleaning','Interpass Clean'],
  ['last_error',       'Last Error'],
  ['vel_cp',           'Cart Vel (mm/s)'],
  // -- Cabinet health ---------------------------------------------------------
  ['fan_speed_outside','Fan Outside (RPM)'],
  ['fan_speed_kpc',    'Fan KPC (RPM)'],
  ['ups_state',        'UPS State'],
  // -- Safety -----------------------------------------------------------------
  ['operational_mode', 'Op Mode'],
  ['emergency_stop',   'E-Stop'],
  ['protective_stop',  'Prot. Stop'],
];
const FRONIUS_ROWS = [
  ['process_active',   'Arc On'],
  ['arc_stable',       'Arc Stable'],
  ['current_flow',     'Current Flowing'],
  ['voltage_v',        'Voltage (V)'],
  ['current_a',        'Current (A)'],
  ['wire_feed_mpm',    'Wire Feed (m/min)'],
  ['power_w',          'Power (W)'],
  ['job_name',         'Job Name'],
  ['job_number',       'Job #'],
  ['seam_number',      'Seam #'],
  ['display_status',   'Status'],
];
const DEVICE_ROWS = {
  chesty_kuka: KUKA_ROWS, mattis_kuka: KUKA_ROWS,
  chesty_fronius: FRONIUS_ROWS, mattis_fronius: FRONIUS_ROWS,
};

const snaps = {};
// Last numeric value written to each cell — keyed as "deviceId:key"
const lastNum = {};
let evCount = 0;

// Keys that carry high-frequency analog noise — deadband applies, no flash.
const ANALOG_KEYS = new Set([
  'a1','a2','a3','a4','a5','a6',
  'tcp_x','tcp_y','tcp_z','tcp_a','tcp_b','tcp_c',
  'pyrometer_temp_c','pyrometer_temp_raw_c',
  'fan_speed_outside','fan_speed_inside','fan_speed_kpc',
  'vel_cp','speed_override',
]);
// Minimum change required to update an analog cell (degrees / mm / RPM)
const DEADBAND = 0.05;

function fmt(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function vClass(v) {
  if (typeof v === 'boolean') return v ? 'v-bool-true' : 'v-bool-false';
  if (typeof v === 'number')  return 'v-num';
  if (typeof v === 'string' && v !== '—') return 'v-str';
  return '';
}

function renderTable(deviceId, values, changedKey) {
  const rows = DEVICE_ROWS[deviceId] || [];
  const tbl  = document.getElementById('tbl-' + deviceId);
  if (!tbl) return;

  // First paint — build the table once, tag each row with data-key.
  if (tbl.rows.length === 0) {
    tbl.innerHTML = rows.map(([k, label]) => {
      const v = values[k];
      if (typeof v === 'number') lastNum[deviceId + ':' + k] = v;
      return `<tr data-key="${k}"><td>${label}</td><td class="${vClass(v)}">${fmt(v)}</td></tr>`;
    }).join('');
    return;
  }

  // Subsequent updates: only touch the cells that actually need to change.
  for (const row of tbl.rows) {
    const k    = row.dataset.key;
    const v    = values[k];
    const cell = row.cells[1];
    const numKey = deviceId + ':' + k;

    if (ANALOG_KEYS.has(k) && typeof v === 'number') {
      // For high-frequency analog keys apply a deadband — ignore servo noise.
      const prev = lastNum[numKey];
      if (prev !== undefined && Math.abs(v - prev) < DEADBAND) continue;
      lastNum[numKey] = v;
      cell.textContent = fmt(v);
      cell.className   = vClass(v);
      // No flash for continuously-changing analog values.
    } else {
      // For state/discrete keys: exact string comparison, flash on change.
      const text = fmt(v);
      if (cell.textContent === text) continue;
      cell.textContent = text;
      cell.className   = vClass(v);
      if (k === changedKey) {
        cell.classList.add('flash');
        setTimeout(() => cell.classList.remove('flash'), 600);
      }
    }
  }

  // Arc-on border for Fronius
  if (deviceId.includes('fronius')) {
    const card = document.getElementById('card-' + deviceId);
    card.classList.toggle('arc-on',  !!values['process_active']);
    card.classList.toggle('arc-off', !values['process_active']);
  }
}

// Track which devices have had their initial paint so heartbeats
// don't trigger a redundant re-render.
const initialized = {};


const logEl = document.getElementById('log');
function addLog(ts, deviceId, changedKey, values) {
  const t = ts.split('T')[1]?.slice(0, 12) ?? ts;
  const isKuka = deviceId.includes('kuka');
  const devClass = isKuka ? 'kuka' : 'fronius';
  const row = document.createElement('div');
  row.className = 'log-row';
  if (!changedKey) {
    row.innerHTML = `<span class="l-ts">${t}</span>`
      + `<span class="l-dev ${devClass}">${deviceId}</span>`
      + `<span class="l-hb">— heartbeat —</span>`;
  } else {
    const val = fmt(values[changedKey]);
    row.innerHTML = `<span class="l-ts">${t}</span>`
      + `<span class="l-dev ${devClass}">${deviceId}</span>`
      + `<span class="l-key">${changedKey}</span>`
      + `<span class="l-val">${val}</span>`;
  }
  logEl.prepend(row);
  while (logEl.children.length > 300) logEl.removeChild(logEl.lastChild);
}

const dot   = document.getElementById('dot');
const label = document.getElementById('conn-label');
const cntEl = document.getElementById('event-count');

const es = new EventSource('/events');
es.onopen = () => { dot.className = 'dot live'; label.textContent = 'Live'; };
es.onerror = () => { dot.className = 'dot dead'; label.textContent = 'Reconnecting…'; };
es.onmessage = e => {
  const d = JSON.parse(e.data);
  snaps[d.device_id] = Object.assign(snaps[d.device_id] || {}, d.values);

  // Always render on first paint (d.changed_key may be null for seed records).
  // After that, skip heartbeats (changed_key === null) — the card is already
  // up to date from individual change events and we don't want a mass DOM
  // update every 5 seconds causing visible flicker on stable rows.
  if (!initialized[d.device_id] || d.changed_key !== null) {
    renderTable(d.device_id, snaps[d.device_id], d.changed_key);
    initialized[d.device_id] = true;
  }

  addLog(d.ts, d.device_id, d.changed_key, d.values);
  evCount++;
  if (evCount % 10 === 0)
    cntEl.textContent = evCount.toLocaleString() + ' events received';
};
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    return web.Response(text=_HTML, content_type="text/html")


async def events(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })
    await resp.prepare(request)

    pool: asyncpg.Pool = request.app["pool"]

    # Seed with latest snapshot per device so the page populates immediately
    seed = await pool.fetch("""
        SELECT DISTINCT ON (device_id)
            ts, device_id, source, changed_key, values::text AS vj
        FROM radian_os.telemetry
        ORDER BY device_id, ts DESC
    """)
    for row in seed:
        payload = json.dumps({
            "ts": row["ts"].isoformat(),
            "device_id": row["device_id"],
            "source": row["source"],
            "changed_key": row["changed_key"],
            "values": json.loads(row["vj"]),
        })
        await resp.write(f"data: {payload}\n\n".encode())

    last_ts = datetime.now(timezone.utc)

    try:
        while True:
            await asyncio.sleep(0.25)
            rows = await pool.fetch("""
                SELECT ts, device_id, source, changed_key, values::text AS vj
                FROM radian_os.telemetry
                WHERE ts > $1
                ORDER BY ts ASC
                LIMIT 200
            """, last_ts)

            if rows:
                last_ts = rows[-1]["ts"]
                for row in rows:
                    payload = json.dumps({
                        "ts": row["ts"].isoformat(),
                        "device_id": row["device_id"],
                        "source": row["source"],
                        "changed_key": row["changed_key"],
                        "values": json.loads(row["vj"]),
                    })
                    await resp.write(f"data: {payload}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass

    return resp


# ---------------------------------------------------------------------------
# App factory + entrypoint
# ---------------------------------------------------------------------------

async def create_app(dsn: str) -> web.Application:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    app = web.Application()
    app["pool"] = pool
    app.router.add_get("/", index)
    app.router.add_get("/events", events)

    async def _close(a: web.Application) -> None:
        await a["pool"].close()
    app.on_shutdown.append(_close)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Radian OS 2.0 live dashboard")
    parser.add_argument("--config", default="config/collectors.local.yaml")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    dsn = cfg["storage"]["dsn"]

    async def _run() -> None:
        app = await create_app(dsn)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", args.port)
        await site.start()
        print(f"\n  Radian OS 2.0 dashboard ->  http://localhost:{args.port}\n")
        await asyncio.Event().wait()  # run forever

    asyncio.run(_run())


if __name__ == "__main__":
    main()
