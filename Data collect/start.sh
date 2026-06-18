#!/usr/bin/env bash
# start.sh — Launch Radian OS 2.0 on Linux (native Docker, no WSL2)
#
# Usage:
#   ./start.sh          — start all services
#   ./start.sh stop     — stop supervisor and web server
#   ./start.sh status   — show what's running
#
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config/collectors.local.yaml"
VENV="$SCRIPT_DIR/venv"
PYTHON="$VENV/bin/python"
SUPERVISOR_PID="$SCRIPT_DIR/logs/supervisor.pid"
WEBSERVER_PID="$SCRIPT_DIR/logs/webserver.pid"

# ── Helpers ────────────────────────────────────────────────────────────────────

check_config() {
    if [ ! -f "$CONFIG" ]; then
        echo "ERROR: $CONFIG not found."
        echo "Copy the example and fill in your values:"
        echo "  cp config/collectors.local.yaml.example config/collectors.local.yaml"
        exit 1
    fi
}

check_venv() {
    if [ ! -f "$PYTHON" ]; then
        echo "Creating Python virtual environment..."
        python3 -m venv "$VENV"
        "$PYTHON" -m pip install --quiet --upgrade pip
        "$PYTHON" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
        echo "Venv ready."
    fi
}

start_docker() {
    if ! docker info &>/dev/null; then
        echo "Starting Docker..."
        sudo systemctl start docker
        sleep 2
    fi
}

start_db() {
    echo "Starting TimescaleDB..."
    docker compose -f "$SCRIPT_DIR/../docker-compose.yml" up -d
    echo "Waiting for database to be healthy..."
    for i in $(seq 1 30); do
        if docker compose -f "$SCRIPT_DIR/../docker-compose.yml" ps | grep -q "healthy"; then
            break
        fi
        sleep 1
    done
    echo "Database ready."
}

apply_schema() {
    echo "Applying schema (safe to run on existing DB)..."
    docker exec -i radianos-db-1 psql -U radian -d radian_forge < "$SCRIPT_DIR/schema.sql"
}

stop_service() {
    local pidfile=$1
    local name=$2
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping $name (PID $pid)..."
            kill "$pid"
        fi
        rm -f "$pidfile"
    fi
}

# ── Commands ───────────────────────────────────────────────────────────────────

cmd_stop() {
    stop_service "$SUPERVISOR_PID" "supervisor"
    stop_service "$WEBSERVER_PID" "web server"
    echo "Services stopped."
}

cmd_status() {
    echo "=== Docker ==="
    docker compose -f "$SCRIPT_DIR/../docker-compose.yml" ps 2>/dev/null || echo "  (docker not running)"
    echo ""
    echo "=== Supervisor ==="
    if [ -f "$SUPERVISOR_PID" ] && kill -0 "$(cat "$SUPERVISOR_PID")" 2>/dev/null; then
        echo "  running (PID $(cat "$SUPERVISOR_PID"))"
    else
        echo "  stopped"
    fi
    echo ""
    echo "=== Web Server ==="
    if [ -f "$WEBSERVER_PID" ] && kill -0 "$(cat "$WEBSERVER_PID")" 2>/dev/null; then
        echo "  running (PID $(cat "$WEBSERVER_PID")) — http://localhost:8765"
    else
        echo "  stopped"
    fi
}

cmd_start() {
    check_config
    check_venv
    mkdir -p "$SCRIPT_DIR/logs"

    start_docker
    start_db
    apply_schema

    echo "Starting supervisor..."
    "$PYTHON" -m src.supervisor --config "$CONFIG" \
        >> "$SCRIPT_DIR/logs/supervisor.log" 2>&1 &
    echo $! > "$SUPERVISOR_PID"

    echo "Starting web server..."
    "$PYTHON" -m src.web.server --config "$CONFIG" \
        >> "$SCRIPT_DIR/logs/webserver.log" 2>&1 &
    echo $! > "$WEBSERVER_PID"

    sleep 2
    cmd_status
    echo ""
    echo "Dashboard: http://localhost:8765"
}

# ── Entry point ────────────────────────────────────────────────────────────────

case "${1:-start}" in
    stop)   cmd_stop   ;;
    status) cmd_status ;;
    start)  cmd_start  ;;
    *)
        echo "Usage: $0 [start|stop|status]"
        exit 1
        ;;
esac
