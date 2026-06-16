"""
Radian OS 2.0 — HUD API Server

Serves the jycaptain/Radian-Forge dashboard.html unmodified and implements
exactly the API shape that dashboard polls:

  GET /                      → dashboard.html
  GET /health                → {"status":"ok","db":"ok"}
  GET /state[?robot_id=X]    → live twin state dict (same shape as twin_api.py)
  GET /robots                → ["chesty","mattis"]
  GET /jobs[?robot_id=X]     → job list
  GET /job-state?job_id=X    → historical state (stub)
  GET /trail[?robot_id=X]    → TCP path points
  GET /interpass[?robot_id=X]→ interpass temperature log
  GET /quality/layer-metrics → layer metrics
  GET /builds                → build list (stub)
  GET /segments              → segment list (stub)
  Camera, NCR, AI endpoints  → graceful empty stubs

Data source:  radian_os.telemetry (TimescaleDB), polled every 500ms by a
background task that builds in-memory state dicts identical in structure to
the orignal twin_api.py latest_kuka / latest_fronius dicts.

The dashboard.html was copied from jycaptain/Radian-Forge verbatim.
This file is the only connector — nothing in the original HTML is changed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("hud.server")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_STATIC = _HERE.parent.parent / "static"
_DASHBOARD = _STATIC / "dashboard.html"

# ---------------------------------------------------------------------------
# Shared in-memory state  (mirrored from twin_api.py structure)
# ---------------------------------------------------------------------------
# latest_kuka[robot_id]    → flat dict of KUKA variable name → value
# latest_fronius[robot_id] → flat dict of Fronius variable name → value
latest_kuka:    Dict[str, Dict[str, Any]] = {}
latest_fronius: Dict[str, Dict[str, Any]] = {}
_db_pool: Optional[asyncpg.Pool] = None

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(title="Radian OS 2.0 HUD", docs_url=None, redoc_url=None)

# Static assets (JS libs, images — served under /assets and /vendor)
if (_STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_STATIC / "assets")), name="assets")
if (_STATIC / "vendor").exists():
    app.mount("/vendor", StaticFiles(directory=str(_STATIC / "vendor")), name="vendor")


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _startup():
    from ..storage.writer import _INSERT_SQL  # noqa — just trigger import check
    dsn = app.state.dsn if hasattr(app.state, "dsn") else None
    if dsn:
        app.state.pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
        global _db_pool
        _db_pool = app.state.pool
        asyncio.create_task(_state_refresh_loop(), name="hud_state_refresh")
        logger.info("HUD server connected to DB")
    else:
        logger.warning("HUD server: no DSN configured — running in offline mode")


@app.on_event("shutdown")
async def _shutdown():
    if _db_pool:
        await _db_pool.close()


# ---------------------------------------------------------------------------
# Background state refresh
# ---------------------------------------------------------------------------

async def _state_refresh_loop(interval_s: float = 0.5):
    """
    Poll radian_os.telemetry for the freshest row per device every 500ms.
    Updates latest_kuka / latest_fronius in-memory dicts — same pattern as
    twin_api.py's /ingest endpoints but reading from DB instead of HTTP POST.
    """
    sql = """
        SELECT DISTINCT ON (device_id, source)
            device_id,
            source,
            values
        FROM radian_os.telemetry
        WHERE ts > NOW() - INTERVAL '60 seconds'
        ORDER BY device_id, source, ts DESC
    """
    while True:
        await asyncio.sleep(interval_s)
        if _db_pool is None:
            continue
        try:
            async with _db_pool.acquire() as conn:
                rows = await conn.fetch(sql)
            for row in rows:
                device_id: str = row["device_id"]
                source:    str = row["source"]
                values:    dict = json.loads(row["values"]) if isinstance(row["values"], str) else dict(row["values"])
                # device_id is like "chesty_kuka" or "chesty_fronius"
                # Derive robot_id by stripping the source suffix
                if "_kuka" in device_id:
                    robot_id = device_id.replace("_kuka", "")
                    latest_kuka[robot_id] = _normalise_kuka(values)
                elif "_fronius" in device_id:
                    robot_id = device_id.replace("_fronius", "")
                    latest_fronius[robot_id] = _normalise_fronius(values)
                # ESP32 and Schneider stored separately (HUD stub only)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("HUD state refresh error: %s", exc)


def _sf(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _sb(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "on", "p_active", "active", "running"}


def _normalise_kuka(v: dict) -> dict:
    """Map SOP variable names → legacy twin_api field names the HUD expects."""
    out = dict(v)
    # SOP gActiveLayer → active_layer (twin_api convention)
    if "gActiveLayer" in v and "active_layer" not in v:
        out["active_layer"] = v["gActiveLayer"]
    if "gActiveLayerSeam" in v and "active_layer_seam" not in v:
        out["active_layer_seam"] = v["gActiveLayerSeam"]
    if "gPrintActive" in v and "print_active" not in v:
        out["print_active"] = v["gPrintActive"]
    if "gPrintComplete" in v and "print_complete" not in v:
        out["print_complete"] = v["gPrintComplete"]
    return out


def _normalise_fronius(v: dict) -> dict:
    out = dict(v)
    # wire_speed → wire_feed_mpm convention used by twin_api
    if "wire_speed" in v and "wire_feed_mpm" not in v:
        out["wire_feed_mpm"] = v["wire_speed"]
    return out


# ---------------------------------------------------------------------------
# State assembly  (mirrors twin_api.py current_state())
# ---------------------------------------------------------------------------

def _current_state(robot_id: str) -> Optional[Dict[str, Any]]:
    lk = latest_kuka.get(robot_id, {})
    lf = latest_fronius.get(robot_id, {})
    if not lk and not lf:
        return None

    layer = int(lk.get("active_layer") or lk.get("gActiveLayer") or 0)
    seam  = int(lk.get("active_layer_seam") or lk.get("gActiveLayerSeam") or 0)

    robot_xyz = {
        "x": _sf(lk.get("tcp_x") or lk.get("x")),
        "y": _sf(lk.get("tcp_y") or lk.get("y")),
        "z": _sf(lk.get("tcp_z") or lk.get("z")),
    }
    robot_abc = {
        "a": _sf(lk.get("tcp_a") or lk.get("a")),
        "b": _sf(lk.get("tcp_b") or lk.get("b")),
        "c": _sf(lk.get("tcp_c") or lk.get("c")),
    }

    actual_voltage_v       = _sf(lf.get("voltage_v"))
    actual_current_a       = _sf(lf.get("current_a"))
    actual_wire_feed_mpm   = _sf(lf.get("wire_feed_mpm") or lf.get("wire_speed"))
    actual_power_w         = _sf(lf.get("power_w"))
    process_active         = _sb(lf.get("process_active"))
    process_mainphase      = _sb(lf.get("process_mainphase"))
    current_flow           = _sb(lf.get("current_flow"))
    arc_stable             = _sb(lf.get("arc_stable"))
    actual_arc_on          = bool(
        _sb(lk.get("print_active") or lk.get("gPrintActive"))
        or process_active or process_mainphase or current_flow or arc_stable
        or (actual_current_a > 5) or (actual_voltage_v > 5) or (actual_power_w > 100)
    )

    # Layer progress: use gTotalSeamCount / gLayerCount if available
    layer_count = int(lk.get("gLayerCount") or layer or 0)
    seam_count  = int(lk.get("gTotalSeamCount") or lk.get("gLayerSeamCount") or seam or 0)
    # Rough progress heuristic (no segment geometry in data-collect phase)
    progress = min(max(seam_count / max(layer_count * 4, 1), 0.0), 1.0) if layer_count else 0.0

    ts = lk.get("timestamp") or lf.get("timestamp") or datetime.now(timezone.utc).isoformat()

    return {
        "timestamp":             ts,
        "segment_id":            seam_count,
        "layer_id":              layer,
        "seam_id":               seam,
        "progress_0_1":          progress,
        "distance_to_path_mm":   0.0,
        "robot_xyz":             robot_xyz,
        "planned_xyz":           None,
        "robot_abc":             robot_abc,
        "planned_arc_on":        False,
        "actual_arc_on":         actual_arc_on,
        "planned_speed_mps":     0.0,
        "actual_robot_speed_mps": 0.0,
        "actual_voltage_v":      actual_voltage_v,
        "actual_current_a":      actual_current_a,
        "actual_wire_feed_mpm":  actual_wire_feed_mpm,
        "actual_power_w":        actual_power_w,
        "display_voltage_v":     actual_voltage_v,
        "display_current_a":     actual_current_a,
        "display_wire_feed_mpm": actual_wire_feed_mpm,
        "pyrometer_temp_c":      _sf(lk.get("pyrometer_temp_c")) or None,
        "interpass_temp_c":      _sf(lk.get("pyrometer_temp_c")) or None,
        "process_active":        process_active,
        "process_mainphase":     process_mainphase,
        "current_flow":          current_flow,
        "arc_stable":            arc_stable,
        "print_active":          _sb(lk.get("print_active") or lk.get("gPrintActive")),
        "print_complete":        _sb(lk.get("print_complete") or lk.get("gPrintComplete")),
        "job_number":            lf.get("job_number"),
        "job_name":              lf.get("job_name"),
        "job_revision":          lf.get("job_revision"),
        "heat_input_proxy":      actual_power_w / 1000.0,
        "anomaly_flags":         _anomalies(actual_voltage_v, actual_current_a),
        "built_points_count":    seam_count,
        "deposited_points":      seam_count,
        "built_path_length_mm":  0.0,
        "built_length_mm":       0.0,
        "elapsed_seconds":       0.0,
        "eta_seconds":           None,
        # SOP-specific extras (passed through for any custom HUD panels)
        "gNextLayer":            lk.get("gNextLayer"),
        "gNextSeam":             lk.get("gNextSeam"),
        "gLayerCount":           lk.get("gLayerCount"),
        "gTotalSeamCount":       lk.get("gTotalSeamCount"),
        "gLayerSeamCount":       lk.get("gLayerSeamCount"),
        "gStopCycle":            lk.get("gStopCycle"),
        "gLastError":            lk.get("gLastError"),
        "operational_mode":      lk.get("operational_mode"),
        "speed_override":        lk.get("speed_override") or lk.get("ov_act"),
        "emergency_stop":        _sb(lk.get("emergency_stop")),
        "protective_stop":       _sb(lk.get("protective_stop")),
        "task_program_name":     lk.get("task_program_name"),
    }


def _anomalies(v: float, a: float) -> list:
    flags = []
    if v and (v < 18 or v > 32):
        flags.append("voltage_out_of_band")
    if a and (a < 80 or a > 400):
        flags.append("current_out_of_band")
    return flags


def _known_robots() -> List[str]:
    return sorted(set(latest_kuka.keys()) | set(latest_fronius.keys())) or ["chesty", "mattis"]


# ---------------------------------------------------------------------------
# API endpoints — exact shape expected by dashboard.html
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(_DASHBOARD.read_text(encoding="utf-8", errors="replace"))


@app.get("/health")
def health():
    db_ok = _db_pool is not None
    robots = _known_robots()
    return {
        "status": "ok",
        "db": "ok" if db_ok else "unavailable",
        "mode": "live",
        "collectors": len(robots),
        "robots": robots,
    }


@app.get("/state")
def get_state(robot_id: Optional[str] = Query(default=None)):
    if robot_id:
        st = _current_state(robot_id)
        if st is None:
            return JSONResponse(content=None)
        return st

    # No robot_id: return {robot_id: state, ...}
    out = {}
    for rid in _known_robots():
        st = _current_state(rid)
        if st:
            out[rid] = st
    return out if out else JSONResponse(content=None)


@app.get("/robots")
def list_robots():
    return _known_robots()


@app.get("/jobs")
def list_jobs(robot_id: Optional[str] = Query(default=None)):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [
        {
            "id": "live",
            "is_live": True,
            "robot_id": robot_id,
            "label": "Live (current run)",
            "start_ts": None,
            "end_ts": None,
            "point_count": None,
        },
        {
            "id": "today",
            "is_live": False,
            "robot_id": robot_id,
            "label": f"Today's build ({today})",
            "start_ts": None,
            "end_ts": None,
            "point_count": None,
        },
    ]


@app.get("/job-state")
def job_state(
    job_id: str = Query(...),
    robot_id: Optional[str] = Query(default=None),
):
    rid = robot_id or (_known_robots()[0] if _known_robots() else "chesty")
    return _current_state(rid) or {}


@app.get("/trail")
async def trail(
    robot_id: Optional[str] = Query(default=None),
    job_id: Optional[str] = Query(default=None),
    limit: int = Query(default=500),
):
    if _db_pool is None:
        return []
    rid = robot_id or (_known_robots()[0] if _known_robots() else "chesty")
    sql = """
        SELECT
            (values->>'tcp_x')::float  AS x,
            (values->>'tcp_y')::float  AS y,
            (values->>'tcp_z')::float  AS z,
            ts
        FROM radian_os.telemetry
        WHERE source = 'opc_kuka'
          AND device_id = $1
          AND ts > NOW() - INTERVAL '4 hours'
          AND values->>'tcp_x' IS NOT NULL
          AND values->>'tcp_x' != '0'
        ORDER BY ts DESC
        LIMIT $2
    """
    try:
        async with _db_pool.acquire() as conn:
            rows = await conn.fetch(sql, f"{rid}_kuka", limit)
        return [
            {"x": r["x"], "y": r["y"], "z": r["z"],
             "ts": r["ts"].isoformat() if r["ts"] else None}
            for r in rows if r["x"] is not None
        ]
    except Exception as exc:
        logger.debug("trail query error: %s", exc)
        return []


@app.get("/interpass")
async def interpass(
    robot_id: Optional[str] = Query(default=None),
    job_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200),
):
    if _db_pool is None:
        return []
    rid = robot_id or (_known_robots()[0] if _known_robots() else "chesty")
    sql = """
        SELECT
            ts,
            (values->>'pyrometer_temp_c')::float AS temp_c,
            (values->>'gActiveLayer')::int        AS layer_id,
            (values->>'gActiveLayerSeam')::int    AS seam_id
        FROM radian_os.telemetry
        WHERE source = 'opc_kuka'
          AND device_id = $1
          AND values->>'pyrometer_temp_c' IS NOT NULL
          AND (values->>'pyrometer_temp_c')::float > 0
          AND ts > NOW() - INTERVAL '8 hours'
        ORDER BY ts DESC
        LIMIT $2
    """
    try:
        async with _db_pool.acquire() as conn:
            rows = await conn.fetch(sql, f"{rid}_kuka", limit)
        return [
            {
                "ts": r["ts"].isoformat() if r["ts"] else None,
                "temp_c": r["temp_c"],
                "layer_id": r["layer_id"],
                "seam_id": r["seam_id"],
                "trigger": "live",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("interpass query error: %s", exc)
        return []


@app.get("/quality/layer-metrics")
async def layer_metrics(
    robot_id: Optional[str] = Query(default=None),
    job_id: Optional[str] = Query(default=None),
):
    if _db_pool is None:
        return []
    rid = robot_id or (_known_robots()[0] if _known_robots() else "chesty")
    sql = """
        SELECT
            (values->>'gActiveLayer')::int    AS layer_id,
            AVG((values->>'current_a')::float)  AS avg_current_a,
            AVG((values->>'voltage_v')::float)  AS avg_voltage_v,
            AVG((values->>'power_w')::float)    AS avg_power_w,
            MAX((values->>'pyrometer_temp_c')::float) AS max_temp_c,
            COUNT(*)                           AS sample_count
        FROM radian_os.telemetry
        WHERE source IN ('opc_kuka','opc_fronius')
          AND device_id LIKE $1
          AND ts > NOW() - INTERVAL '8 hours'
          AND values->>'gActiveLayer' IS NOT NULL
          AND (values->>'gActiveLayer')::int > 0
        GROUP BY (values->>'gActiveLayer')::int
        ORDER BY layer_id
    """
    try:
        async with _db_pool.acquire() as conn:
            rows = await conn.fetch(sql, f"{rid}%")
        return [
            {
                "layer_id": r["layer_id"],
                "avg_current_a": r["avg_current_a"],
                "avg_voltage_v": r["avg_voltage_v"],
                "avg_power_w": r["avg_power_w"],
                "max_temp_c": r["max_temp_c"],
                "sample_count": r["sample_count"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("layer-metrics query error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Graceful stubs — dashboard calls these but they're non-critical
# ---------------------------------------------------------------------------

@app.get("/builds")
def builds(robot_id: Optional[str] = Query(default=None), limit: int = 10):
    return []

@app.get("/build/{build_id}/report")
def build_report(build_id: str):
    return {}

@app.get("/segments")
def segments(robot_id: Optional[str] = Query(default=None)):
    return []

@app.get("/ncr/summary/open")
def ncr_summary(robot_id: Optional[str] = Query(default=None)):
    return {"count": 0, "items": []}

@app.get("/calibration/status")
def calibration_status(machine_id: Optional[str] = Query(default=None)):
    return {"status": "unknown"}

@app.get("/ai/toolpath/alerts")
def ai_alerts(robot_id: Optional[str] = Query(default=None), limit: int = 20):
    return []

@app.post("/ai/toolpath/alerts/explain")
def ai_explain():
    return {"explanation": "AI inference not yet configured in data-collect phase."}

@app.get("/camera/snapshot")
def camera_snapshot(robot_id: Optional[str] = Query(default=None),
                    sensor: Optional[str] = Query(default=None)):
    return JSONResponse(content={"error": "no camera in data-collect phase"}, status_code=503)

@app.get("/camera/stream")
def camera_stream(robot_id: Optional[str] = Query(default=None),
                  sensor: Optional[str] = Query(default=None)):
    return JSONResponse(content={"error": "no camera in data-collect phase"}, status_code=503)

@app.get("/camera/thermography")
def camera_thermo(robot_id: Optional[str] = Query(default=None)):
    return JSONResponse(content={"error": "no camera in data-collect phase"}, status_code=503)

@app.get("/api/camera/ingestion/profile")
def camera_profile(robot_id: Optional[str] = Query(default=None)):
    return {"sensors": [], "robot_id": robot_id}

@app.get("/recent-states")
def recent_states(robot_id: Optional[str] = Query(default=None), limit: int = 500):
    return []

@app.get("/export/states.json")
def export_states():
    return []

@app.get("/debug/kuka/{robot_id}")
def debug_kuka(robot_id: str):
    return JSONResponse(content=latest_kuka.get(robot_id, {}),
                        headers={"Cache-Control": "no-store"})

@app.get("/debug/fronius/{robot_id}")
def debug_fronius(robot_id: str):
    return JSONResponse(content=latest_fronius.get(robot_id, {}),
                        headers={"Cache-Control": "no-store"})
