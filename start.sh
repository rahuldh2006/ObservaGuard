#!/usr/bin/env bash
# ObservaGuard — Quick Start Script (Linux / macOS)
# Run from the project root directory.

set -e
cd "$(dirname "$0")"

echo ""
echo "=========================================="
echo "  ObservaGuard — SRE Watchdog"
echo "=========================================="

# 1. Install dependencies
echo ""
echo "[1/3] Installing Python dependencies..."
pip install -r requirements.txt -q
echo "      ✓ Dependencies installed"

# 2. Port selection: prefer 8080, fall back to 8081
PORT=8080
if lsof -iTCP:8080 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "      Port 8080 in use — trying 8081..."
    PORT=8081
fi
export OBSERVAGUARD_PORT=$PORT
echo "      Using port $PORT"

# 3. Start the server in the background
echo ""
echo "[2/3] Starting ObservaGuard API server..."
python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload &
SERVER_PID=$!
echo "      ✓ Server started (PID $SERVER_PID)"
echo "      Dashboard: http://localhost:$PORT/dashboard"
echo "      API docs:  http://localhost:$PORT/docs"

# 4. Wait until the server is ready (poll up to 20 s)
echo ""
echo "[3/3] Waiting for server to be ready..."
for i in $(seq 1 20); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/health', timeout=1)" 2>/dev/null; then
        echo "      ✓ Server ready!"
        break
    fi
    sleep 1
done

# Run the demo simulator
echo ""
echo "Running demo simulation (normal + spike + chaos)..."
python3 simulator.py --scenario demo --port "$PORT"

echo ""
echo "=========================================="
echo "  ObservaGuard is running!"
echo "  → Dashboard: http://localhost:$PORT/dashboard"
echo "  → Alerts:    http://localhost:$PORT/alerts"
echo "  → API Docs:  http://localhost:$PORT/docs"
echo ""
echo "  Press Ctrl+C to stop the server."
echo "=========================================="

# Keep running until Ctrl+C
wait $SERVER_PID
