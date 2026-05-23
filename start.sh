#!/usr/bin/env bash
# ObservaGuard — Quick Start Script
# Run this from inside the ObservaGuard/ directory

set -e
echo ""
echo "=========================================="
echo "  ObservaGuard — SRE Watchdog"
echo "=========================================="

# 1. Install dependencies
echo ""
echo "[1/3] Installing Python dependencies..."
pip install -r requirements.txt --break-system-packages -q
echo "      ✓ Dependencies installed"

# 2. Start the server in the background
echo ""
echo "[2/3] Starting ObservaGuard API server..."
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
SERVER_PID=$!
echo "      ✓ Server started (PID $SERVER_PID)"
echo "      Dashboard: http://localhost:8000/dashboard"
echo "      API docs:  http://localhost:8000/docs"

# 3. Wait for server to be ready
echo ""
echo "[3/3] Waiting for server to be ready..."
sleep 3

# Run the demo simulator
echo ""
echo "Running demo simulation (normal + spike + chaos)..."
python3 simulator.py --scenario demo

echo ""
echo "=========================================="
echo "  ObservaGuard is running!"
echo "  → Dashboard: http://localhost:8000/dashboard"
echo "  → Alerts:    http://localhost:8000/alerts"
echo "  → API Docs:  http://localhost:8000/docs"
echo ""
echo "  Press Ctrl+C to stop the server."
echo "=========================================="

# Keep running
wait $SERVER_PID
