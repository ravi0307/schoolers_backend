#!/usr/bin/env bash
# Launches every Schoolers microservice + the API gateway, all reading the
# single shared common/.env. Run from anywhere; paths are resolved relative
# to this script's own location.
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
echo "To stop everything: pkill -f 'uvicorn main:app'"