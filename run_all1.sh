#!/usr/bin/env bash
# Manages (Starts, Stops, or Restarts) every Schoolers microservice + the API gateway,
# and the Vite dev server the browser talks to.
# Run from anywhere; paths are resolved relative to this script's own location.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT"
LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

# The frontend lives in a separate repo. Both of these are overridable, e.g.
#   FRONTEND_DIR=/path/to/schoolers-web FRONTEND_PORT=5173 ./run_all1.sh restart
FRONTEND_DIR="${FRONTEND_DIR:-/Users/ravi/Downloads/schoolers-web}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

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
    media_service
    accounts_service
    support_service
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
    8016
    8017
    8018
)

stop_services() {
    echo "Stopping Schoolers gateway and microservices on their ports (8000-8018)..."
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

stop_frontend() {
    if [ ! -d "$FRONTEND_DIR" ]; then
        echo "Frontend directory not found at $FRONTEND_DIR -- skipping."
        return 0
    fi

    echo "Stopping the frontend dev server on :$FRONTEND_PORT..."
    pids=$(lsof -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null || true)
    if [ -z "$pids" ]; then
        echo "  nothing listening on :$FRONTEND_PORT"
        return 0
    fi

    for pid in $pids; do
        # 5173 is a popular port and other projects may be holding it. Only stop
        # a listener that is actually a Vite dev server, so an unrelated app the
        # user is running is left alone -- same rule the backend teardown uses.
        if ! ps -p "$pid" -o command= 2>/dev/null | grep -q vite; then
            echo "  :$FRONTEND_PORT is held by another program (pid $pid) -- left running"
            continue
        fi
        kill "$pid" 2>/dev/null || true
        echo "  stopped pid $pid on :$FRONTEND_PORT"
    done
    sleep 1
}

start_frontend() {
    if [ ! -d "$FRONTEND_DIR" ]; then
        echo "Frontend directory not found at $FRONTEND_DIR -- skipping."
        echo "  point it at your checkout with FRONTEND_DIR=/path/to/schoolers-web"
        return 0
    fi
    if [ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]; then
        echo "Frontend dependencies are not installed -- skipping."
        echo "  run: cd \"$FRONTEND_DIR\" && npm install"
        return 0
    fi

    # "Something is on the port" is not the same as "the frontend is running".
    # Treating any listener as ours would report success while the app is down.
    existing=$(lsof -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null || true)
    if [ -n "$existing" ]; then
        for pid in $existing; do
            if ps -p "$pid" -o command= 2>/dev/null | grep -q vite; then
                echo "  frontend -> :$FRONTEND_PORT (already running)"
                return 0
            fi
        done
        echo "  frontend -> :$FRONTEND_PORT is occupied by another program (pid $existing)."
        echo "  Stop that program, or use a different port: FRONTEND_PORT=5174 $0 restart"
        return 0
    fi

    echo "Starting the frontend dev server on :$FRONTEND_PORT..."
    # --strictPort so the port can never drift to 5174/5175 the way plain `vite`
    # does when 5173 is busy. This script stops the frontend by port, so a port
    # that moved would leave it running after `stop`.
    #
    # nohup + exec + </dev/null so the dev server outlives the shell that ran
    # this script. Without it, closing the terminal -- or this script being
    # interrupted -- takes the frontend down with it, which looks exactly like
    # "the application is down" the next time you come back to it.
    ( cd "$FRONTEND_DIR" && exec nohup npm run dev -- --port "$FRONTEND_PORT" --strictPort \
        > "$LOG_DIR/frontend.log" 2>&1 < /dev/null ) &
    disown 2>/dev/null || true

    waited=0
    while [ "$waited" -lt 30 ]; do
        if lsof -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "  frontend -> :$FRONTEND_PORT (started)"
            echo ""
            echo "Open http://localhost:$FRONTEND_PORT"
            return 0
        fi
        waited=$((waited + 1))
        sleep 1
    done

    # Say so loudly and show why: a frontend that silently fails to come up is
    # exactly the "the application is down" case with nothing to go on.
    echo "  frontend -> :$FRONTEND_PORT FAILED to start."
    echo "  Last lines of $LOG_DIR/frontend.log:"
    tail -n 15 "$LOG_DIR/frontend.log" 2>/dev/null || true
}

# Main routing logic based on user input
ACTION="${1:-start}"

case "$ACTION" in
    start)
        start_services
        echo ""
        start_frontend
        ;;
    stop)
        stop_services
        echo ""
        stop_frontend
        ;;
    restart)
        echo "Restarting ecosystem..."
        stop_services
        echo ""
        stop_frontend
        echo ""
        start_services
        echo ""
        start_frontend
        ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        exit 1
        ;;
esac
