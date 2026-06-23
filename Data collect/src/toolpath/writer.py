"""
Radian OS 2.0 - Toolpath Writer

Independent process that polls the latest telemetry snapshots from TimescaleDB
and writes filtered TCP positions to radian_os.toolpath_points.

Collection gate: program_state = 'P_Active'
Metadata stored per point: arc_on, tool_num, seam_number, layer, in_motion,
on_path, speed_override - all available as view filters in the browser.

Job lifecycle (per robot cell):
  IDLE   -> P_Active starts       -> ensure_job() -> ACTIVE
  ACTIVE -> P_Active stops        ->                 PAUSED
  PAUSED -> same program resumes  ->                 ACTIVE  (resumed_count++)
  PAUSED -> program changes / 24h stale -> complete job -> IDLE

Usage:
    python -m src.toolpath --config config/collectors.local.yaml
    python -m src.toolpath --config config/collectors.local.yaml --log-level DEBUG
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import asyncpg
import yaml

logger = logging.getLogger("toolpath")

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_LATEST_SNAPSHOT = """
    SELECT ts, values::text AS vj
    FROM radian_os.telemetry
    WHERE device_id = $1
    ORDER BY ts DESC
    LIMIT 1
"""

_FIND_OPEN_JOB = """
    SELECT job_id, resumed_count
    FROM radian_os.print_jobs
    WHERE robot_id = $1
      AND program_name = $2
      AND status = 'active'
      AND last_active_at > NOW() - ($3 || ' hours')::interval
    ORDER BY last_active_at DESC
    LIMIT 1
"""

_CREATE_JOB = """
    INSERT INTO radian_os.print_jobs (robot_id, program_name)
    VALUES ($1, $2)
    RETURNING job_id
"""

_TOUCH_JOB = """
    UPDATE radian_os.print_jobs
    SET last_active_at = NOW(), total_points = total_points + 1
    WHERE job_id = $1
"""

_RESUME_JOB = """
    UPDATE radian_os.print_jobs
    SET resumed_count = resumed_count + 1, last_active_at = NOW()
    WHERE job_id = $1
"""

_COMPLETE_JOB = """
    UPDATE radian_os.print_jobs
    SET status = 'complete', last_active_at = NOW()
    WHERE job_id = $1
"""

_INSERT_POINT = """
    INSERT INTO radian_os.toolpath_points
        (ts, job_id, robot_id, tcp_x, tcp_y, tcp_z,
         tool_num, arc_on, seam_number, layer,
         in_motion, on_path, speed_override, program_state)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
"""


# ---------------------------------------------------------------------------
# Cell state machine
# ---------------------------------------------------------------------------

@dataclass
class CellState:
    robot_id: str
    fronius_id: str
    state: str = "idle"           # idle | active | paused
    job_id: Optional[int] = None
    program_name: Optional[str] = None
    last_x: float = 0.0
    last_y: float = 0.0
    last_z: float = 0.0
    points_this_session: int = 0


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _f(values: dict, key: str, default=None):
    v = values.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _b(values: dict, key: str, default=False) -> bool:
    v = values.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _i(values: dict, key: str, default=None):
    v = values.get(key)
    if v is None:
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _dist(ax, ay, az, bx, by, bz) -> float:
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


# ---------------------------------------------------------------------------
# Toolpath Writer
# ---------------------------------------------------------------------------

class ToolpathWriter:
    """
    Polls the DB at poll_interval_s, drives per-cell state machines, writes
    toolpath points to radian_os.toolpath_points.
    """

    def __init__(self, cfg: dict):
        storage = cfg.get("storage", {})
        self._dsn: str = storage["dsn"]
        self._poll_interval: float = float(cfg.get("toolpath", {}).get("poll_interval_s", 0.05))
        self._dedup_mm: float = float(cfg.get("toolpath", {}).get("dedup_mm", 0.5))
        self._resume_window_h: int = int(cfg.get("toolpath", {}).get("resume_window_h", 24))
        self._conn: asyncpg.Connection | None = None

        # Build cell list from robots config
        self._cells: list[CellState] = []
        for robot in cfg.get("robots", []):
            rid = robot["id"]
            if robot.get("kuka", {}).get("enabled", True) and robot.get("fronius", {}).get("enabled", True):
                self._cells.append(CellState(
                    robot_id=f"{rid}_kuka",
                    fronius_id=f"{rid}_fronius",
                ))
                logger.info("Tracking cell: %s_kuka + %s_fronius", rid, rid)

    async def start(self) -> None:
        self._conn = await asyncpg.connect(dsn=self._dsn)
        logger.info("ToolpathWriter connected to DB (poll=%.0fms dedup=%.1fmm resume=%dh)",
                    self._poll_interval * 1000, self._dedup_mm, self._resume_window_h)

    async def stop(self) -> None:
        if self._conn:
            await self._conn.close()

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            for cell in self._cells:
                try:
                    await self._tick(cell)
                except Exception as exc:
                    logger.exception("Tick error [%s]: %s", cell.robot_id, exc)
            await asyncio.sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # Per-cell tick
    # ------------------------------------------------------------------

    async def _tick(self, cell: CellState) -> None:
        kuka_row = await self._latest(cell.robot_id)
        if kuka_row is None:
            return

        kv: dict = kuka_row["values"]
        kts: datetime = kuka_row["ts"]

        # Stale guard: ignore data older than 10 s
        ts_utc = kts if kts.tzinfo else kts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts_utc).total_seconds()
        if age > 10:
            if cell.state == "active":
                logger.debug("%s: KUKA data stale (%.0fs) -> paused", cell.robot_id, age)
                cell.state = "paused"
            return

        program_state: str = kv.get("program_state", "")
        program_name: str = kv.get("task_program_name", "") or ""
        tcp_x = _f(kv, "tcp_x")
        tcp_y = _f(kv, "tcp_y")
        tcp_z = _f(kv, "tcp_z")

        is_active = program_state == "P_Active" and tcp_x is not None

        if cell.state == "idle":
            if is_active and program_name:
                cell.job_id = await self._ensure_job(cell.robot_id, program_name)
                cell.program_name = program_name
                cell.state = "active"
                cell.points_this_session = 0
                logger.info("%s: -> ACTIVE job_id=%d program='%s'",
                            cell.robot_id, cell.job_id, program_name)

        elif cell.state == "active":
            if not is_active:
                cell.state = "paused"
                logger.info("%s: -> PAUSED (program_state=%s)", cell.robot_id, program_state)
            elif program_name and program_name != cell.program_name:
                # Program changed mid-run - complete old job, start new
                await self._complete_job(cell.job_id)
                logger.info("%s: program changed '%s'->'%s' - completing job %d",
                            cell.robot_id, cell.program_name, program_name, cell.job_id)
                cell.job_id = await self._ensure_job(cell.robot_id, program_name)
                cell.program_name = program_name
                cell.points_this_session = 0
                logger.info("%s: -> ACTIVE (new) job_id=%d", cell.robot_id, cell.job_id)
            else:
                await self._write_point(cell, kts, kv, tcp_x, tcp_y, tcp_z)

        elif cell.state == "paused":
            if is_active:
                if program_name == cell.program_name:
                    # Resume same job
                    await self._resume_job(cell.job_id)
                    cell.state = "active"
                    cell.points_this_session = 0
                    logger.info("%s: -> ACTIVE (resumed) job_id=%d", cell.robot_id, cell.job_id)
                elif program_name:
                    # Different program - complete old, start new
                    await self._complete_job(cell.job_id)
                    cell.job_id = await self._ensure_job(cell.robot_id, program_name)
                    cell.program_name = program_name
                    cell.state = "active"
                    cell.points_this_session = 0
                    logger.info("%s: -> ACTIVE (new program) job_id=%d", cell.robot_id, cell.job_id)

    # ------------------------------------------------------------------
    # Point writing
    # ------------------------------------------------------------------

    async def _write_point(
        self,
        cell: CellState,
        ts: datetime,
        kv: dict,
        tcp_x: float,
        tcp_y: float,
        tcp_z: float,
    ) -> None:
        dist = _dist(tcp_x, tcp_y, tcp_z, cell.last_x, cell.last_y, cell.last_z)
        if cell.points_this_session > 0 and dist < self._dedup_mm:
            return

        # Get Fronius state — stale guard: data older than 30s means arc is off
        fronius_row = await self._latest(cell.fronius_id)
        fv: dict = {}
        if fronius_row is not None:
            f_ts = fronius_row["ts"]
            f_ts_utc = f_ts if f_ts.tzinfo else f_ts.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - f_ts_utc).total_seconds() < 30.0:
                fv = fronius_row["values"]

        arc_on = _b(fv, "current_flow")
        seam_number = _i(fv, "seam_number")

        await self._conn.execute(
            _INSERT_POINT,
            ts,
            cell.job_id,
            cell.robot_id,
            tcp_x,
            tcp_y,
            tcp_z,
            _i(kv, "tool_num"),
            arc_on,
            seam_number,
            _i(kv, "gActiveLayer"),
            _b(kv, "in_motion", None),
            _b(kv, "on_path", None),
            _f(kv, "speed_override"),
            kv.get("program_state", ""),
        )
        await self._conn.execute(_TOUCH_JOB, cell.job_id)

        cell.last_x, cell.last_y, cell.last_z = tcp_x, tcp_y, tcp_z
        cell.points_this_session += 1

        if cell.points_this_session % 100 == 1:
            logger.info("%s: job=%d pts=%d arc=%s dist=%.1fmm",
                        cell.robot_id, cell.job_id, cell.points_this_session, arc_on, dist)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _latest(self, device_id: str) -> Optional[Dict[str, Any]]:
        row = await self._conn.fetchrow(_LATEST_SNAPSHOT, device_id)
        if row is None:
            return None
        return {"ts": row["ts"], "values": json.loads(row["vj"])}

    async def _ensure_job(self, robot_id: str, program_name: str) -> int:
        row = await self._conn.fetchrow(
            _FIND_OPEN_JOB, robot_id, program_name, str(self._resume_window_h)
        )
        if row:
            logger.info("Resuming existing job %d for %s '%s'",
                        row["job_id"], robot_id, program_name)
            return row["job_id"]
        row = await self._conn.fetchrow(_CREATE_JOB, robot_id, program_name)
        logger.info("Created new job %d for %s '%s'",
                    row["job_id"], robot_id, program_name)
        return row["job_id"]

    async def _complete_job(self, job_id: int) -> None:
        await self._conn.execute(_COMPLETE_JOB, job_id)
        logger.info("Job %d marked complete", job_id)

    async def _resume_job(self, job_id: int) -> None:
        await self._conn.execute(_RESUME_JOB, job_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())

    writer = ToolpathWriter(cfg)
    await writer.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _shutdown(sig_name: str) -> None:
        logger.info("Signal %s - shutting down", sig_name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            pass

    logger.info("Toolpath writer started - %d cell(s)", len(writer._cells))
    await writer.run(stop_event)
    await writer.stop()
    logger.info("Toolpath writer stopped.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Radian OS 2.0 Toolpath Writer")
    p.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent.parent / "config" / "collectors.yaml",
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(main(args.config))
