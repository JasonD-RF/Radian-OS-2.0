-- Radian OS 2.0 — Toolpath / Digital Twin schema
-- Safe to run repeatedly (all statements use IF NOT EXISTS).
-- Apply against the existing radian_forge instance:
--   docker exec -i radianos-db-1 psql -U radian -d radian_forge < schema_toolpath.sql

-- ---------------------------------------------------------------------------
-- Print jobs: one row per run of a robot program
-- ---------------------------------------------------------------------------
-- A job is keyed by (robot_id, program_name).  If the same program restarts
-- within 24 h (e.g. after a maintenance stop) the toolpath writer resumes the
-- existing job rather than creating a new one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS radian_os.print_jobs (
    job_id          BIGSERIAL    PRIMARY KEY,
    robot_id        TEXT         NOT NULL,          -- 'chesty_kuka' | 'mattis_kuka'
    program_name    TEXT         NOT NULL,          -- task_program_name from KUKA
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resumed_count   INT          NOT NULL DEFAULT 0,
    status          TEXT         NOT NULL DEFAULT 'active',  -- 'active' | 'complete'
    total_points    INT          NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS print_jobs_robot_program
    ON radian_os.print_jobs (robot_id, program_name, status, last_active_at DESC);

-- ---------------------------------------------------------------------------
-- Toolpath points: hypertable — one row per sampled TCP position
-- ---------------------------------------------------------------------------
-- Collection gate: program_state = 'P_Active' (all motion during a program run).
-- All other conditions (arc_on, tool_num, seam_number, layer, in_motion, on_path)
-- are stored as metadata so the browser can filter dynamically.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS radian_os.toolpath_points (
    ts              TIMESTAMPTZ      NOT NULL,
    job_id          BIGINT           NOT NULL,
    robot_id        TEXT             NOT NULL,
    tcp_x           DOUBLE PRECISION NOT NULL,
    tcp_y           DOUBLE PRECISION NOT NULL,
    tcp_z           DOUBLE PRECISION NOT NULL,
    tool_num        INT,
    arc_on          BOOLEAN          NOT NULL DEFAULT FALSE,
    seam_number     INT,
    layer           INT,
    in_motion       BOOLEAN,
    on_path         BOOLEAN,
    speed_override  DOUBLE PRECISION,
    program_state   TEXT
);

SELECT create_hypertable(
    'radian_os.toolpath_points',
    'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS toolpath_job_ts
    ON radian_os.toolpath_points (job_id, ts);

CREATE INDEX IF NOT EXISTS toolpath_robot_ts
    ON radian_os.toolpath_points (robot_id, ts DESC);

-- ---------------------------------------------------------------------------
-- Convenience view: toolpath points joined with job metadata
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW radian_os.toolpath_with_job AS
SELECT
    p.ts,
    p.job_id,
    j.program_name,
    j.robot_id,
    p.tcp_x,
    p.tcp_y,
    p.tcp_z,
    p.tool_num,
    p.arc_on,
    p.seam_number,
    p.layer,
    p.in_motion,
    p.on_path,
    p.speed_override,
    p.program_state
FROM radian_os.toolpath_points p
JOIN radian_os.print_jobs j USING (job_id);

-- ---------------------------------------------------------------------------
-- Optional: auto-retention policy (uncomment when ready)
-- Drops chunks older than 180 days automatically — no manual purge needed.
-- ---------------------------------------------------------------------------
-- SELECT add_retention_policy('radian_os.toolpath_points', INTERVAL '180 days');
