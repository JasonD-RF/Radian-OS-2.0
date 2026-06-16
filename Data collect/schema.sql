-- Radian OS 2.0 — Data Collect schema
-- Run against the existing radian_forge TimescaleDB instance:
--   psql postgresql://radian:forge_local@localhost:5432/radian_forge -f schema.sql

-- Isolate all new tables in a dedicated schema to avoid colliding with
-- the existing Radian Forge HUD tables.
CREATE SCHEMA IF NOT EXISTS radian_os;

-- ---------------------------------------------------------------------------
-- Main telemetry hypertable
-- ---------------------------------------------------------------------------
-- ts           : wall-clock timestamp of the record (partition key)
-- ts_mono_ns   : monotonic timestamp for latency math
-- device_id    : e.g. 'chesty_kuka', 'mattis_fronius', 'esp32_cell_sensor'
-- source       : collector type — 'opc_kuka', 'opc_fronius', 'esp32_http', 'schneider_modbus'
-- changed_key  : which variable triggered this emit (NULL = periodic heartbeat)
-- values       : JSONB snapshot of ALL known variables for this device at this moment

CREATE TABLE IF NOT EXISTS radian_os.telemetry (
    ts          TIMESTAMPTZ NOT NULL,
    ts_mono_ns  BIGINT      NOT NULL,
    device_id   TEXT        NOT NULL,
    source      TEXT        NOT NULL,
    changed_key TEXT,
    values      JSONB       NOT NULL DEFAULT '{}'
);

-- Convert to TimescaleDB hypertable partitioned by time
SELECT create_hypertable(
    'radian_os.telemetry',
    'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS telemetry_device_ts
    ON radian_os.telemetry (device_id, ts DESC);

CREATE INDEX IF NOT EXISTS telemetry_source_ts
    ON radian_os.telemetry (source, ts DESC);

-- GIN index for JSONB queries (e.g. filter by gActiveLayer value)
CREATE INDEX IF NOT EXISTS telemetry_values_gin
    ON radian_os.telemetry USING GIN (values);

-- ---------------------------------------------------------------------------
-- Convenience view: KUKA ArcTech state variables only
-- Surfaces the most frequently queried SOP variables as columns.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW radian_os.kuka_state AS
SELECT
    ts,
    device_id,
    changed_key,
    (values->>'gActiveLayer')::int      AS active_layer,
    (values->>'gNextLayer')::int        AS next_layer,
    (values->>'gActiveLayerSeam')::int  AS active_layer_seam,
    (values->>'gNextSeam')::int         AS next_seam,
    (values->>'gActiveTotalSeam')::int  AS active_total_seam,
    (values->>'gLayerCount')::int       AS layer_count,
    (values->>'gLayerSeamCount')::int   AS layer_seam_count,
    (values->>'gTotalSeamCount')::int   AS total_seam_count,
    (values->>'gPrintActive')::boolean  AS print_active,
    (values->>'gPrintResume')::boolean  AS print_resume,
    (values->>'gNewPrint')::boolean     AS new_print,
    (values->>'gPrintComplete')::boolean AS print_complete,
    (values->>'gStopCycle')::boolean    AS stop_cycle,
    (values->>'program_state')::text    AS program_state,
    (values->>'operational_mode')::text AS operational_mode,
    (values->>'in_motion')::boolean     AS in_motion,
    (values->>'on_path')::boolean       AS on_path,
    (values->>'speed_override')::float  AS speed_override,
    (values->>'pyrometer_temp_c')::float AS pyrometer_temp_c,
    (values->>'tcp_x')::float           AS tcp_x,
    (values->>'tcp_y')::float           AS tcp_y,
    (values->>'tcp_z')::float           AS tcp_z,
    (values->>'task_program_name')::text AS task_program_name
FROM radian_os.telemetry
WHERE source = 'opc_kuka';

-- ---------------------------------------------------------------------------
-- Convenience view: Fronius weld parameters
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW radian_os.fronius_state AS
SELECT
    ts,
    device_id,
    (values->>'current_a')::float      AS current_a,
    (values->>'voltage_v')::float      AS voltage_v,
    (values->>'power_w')::float        AS power_w,
    (values->>'wire_speed')::float     AS wire_speed,
    (values->>'gas_flow')::float       AS gas_flow,
    (values->>'process_active')::boolean AS process_active,
    (values->>'current_flow')::boolean AS current_flow,
    (values->>'arc_stable')::boolean   AS arc_stable,
    (values->>'job_name')::text        AS job_name,
    (values->>'job_number')::int       AS job_number,
    (values->>'job_revision')::int     AS job_revision
FROM radian_os.telemetry
WHERE source = 'opc_fronius';

-- ---------------------------------------------------------------------------
-- Retention policy: keep 90 days of data (adjust as needed)
-- ---------------------------------------------------------------------------
-- SELECT add_retention_policy('radian_os.telemetry', INTERVAL '90 days');

-- ---------------------------------------------------------------------------
-- Compression policy: compress chunks older than 7 days
-- ---------------------------------------------------------------------------
-- ALTER TABLE radian_os.telemetry SET (
--     timescaledb.compress,
--     timescaledb.compress_segmentby = 'device_id,source'
-- );
-- SELECT add_compression_policy('radian_os.telemetry', INTERVAL '7 days');
