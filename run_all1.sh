#!/usr/bin/env bash
# Manages (Starts, Stops, or Restarts) every Schoolers microservice + the API gateway.
# Run from anywhere; paths are resolved relative to this script's own location.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT"
LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

# Determine which python to use (prefer venv if available)
if [ -x "$ROOT/venv/bin/python" ]; then
    PYTHON="$ROOT/venv/bin/python"
else
    PYTHON="python3"
fi

# Service names and their corresponding ports
services=(
    auth_service
    schools_service
    academics_service
    people_service
    attendance_service
    marks_service
    timetable_service
    transport_service
    leave_service
    communication_service
    barter_service
    activities_service
    website_service
    notifications_service
    reports_service
)
ports=(
    8001
    8002
    8003
    8004
    8005
    8006
    8007
    8008
    8009
    8010
    8011
    8012
    8013
    8014
    8015
)

stop_services() {
    echo "Stopping Schoolers gateway and microservices on their ports (8000-8015)..."
    # Kill only processes actually listening on this stack's ports so unrelated
    # uvicorn apps started by the user in other projects are left alone.
    for port in 8000 "${ports[@]}"; do
        pids=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
        if [ -n "$pids" ]; then
            kill $pids 2>/dev/null || true
            echo "  stopped pid(s) $pids on :$port"
        fi
    done
    sleep 1
    echo "Teardown complete."
}

start_services() {
    echo "Checking and starting microservices..."
    for i in "${!services[@]}"; do
        svc="${services[$i]}"
        port="${ports[$i]}"
        # Check if service is already listening on its port
        if ! lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            (cd "$ROOT/services/$svc" && "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$port" > "$LOG_DIR/$svc.log" 2>&1 &)
            echo "  $svc -> :$port (started)"
        else
            echo "  $svc -> :$port (already running)"
        fi
        sleep 0.3
    done

    sleep 3
    echo "Starting API gateway on :8000 (this is the single URL the frontend talks to)..."
    if ! lsof -iTCP:8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
        (cd "$ROOT/gateway" && "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port 8000 > "$LOG_DIR/gateway.log" 2>&1 &)
        echo "  gateway -> :8000 (started)"
    else
        echo "  gateway -> :8000 (already running)"
    fi

    sleep 2
    echo ""
    echo "All services processed. Logs in $LOG_DIR/"
    echo "Check http://127.0.0.1:8000/health/services to see which are up."
    echo "To stop everything: $0 stop"
    echo "To restart everything: $0 restart"
}

# Main routing logic based on user input
ACTION="${1:-start}"

case "$ACTION" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        echo "Restarting ecosystem..."
        stop_services
        echo ""
        start_services
        ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        exit 1
        ;;
esac
