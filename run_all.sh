#!/usr/bin/env bash
# Launches every Schoolers microservice + the API gateway, all reading the
# single shared common/.env. Run from anywhere; paths are resolved relative
# to this script's own location.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT"
LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

declare -A SERVICES=(
  [auth_service]=8001
  [schools_service]=8002
  [academics_service]=8003
  [people_service]=8004
  [attendance_service]=8005
  [marks_service]=8006
  [timetable_service]=8007
  [transport_service]=8008
  [leave_service]=8009
  [communication_service]=8010
  [barter_service]=8011
  [activities_service]=8012
  [website_service]=8013
  [notifications_service]=8014
  [reports_service]=8015
)

echo "Starting 15 microservices..."
for svc in "${!SERVICES[@]}"; do
  port="${SERVICES[$svc]}"
  (cd "$ROOT/services/$svc" && python3 -m uvicorn main:app --host 127.0.0.1 --port "$port" > "$LOG_DIR/$svc.log" 2>&1 &)
  echo "  $svc -> :$port"
  sleep 0.3
done

sleep 3
echo "Starting API gateway on :8000 (this is the single URL the frontend talks to)..."
(cd "$ROOT/gateway" && python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 > "$LOG_DIR/gateway.log" 2>&1 &)

sleep 2
echo ""
echo "All services launched. Logs in $LOG_DIR/"
echo "Check http://127.0.0.1:8000/health/services to see which are up."
echo "Stop everything with: pkill -f 'uvicorn main:app'"
