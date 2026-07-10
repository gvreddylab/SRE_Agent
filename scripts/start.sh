#!/usr/bin/env bash
# Start FastAPI + Streamlit concurrently.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV="$ROOT_DIR/.venv/bin"

# Install missing deps if needed
if [ ! -f "$VENV/uvicorn" ] || [ ! -f "$VENV/streamlit" ]; then
    echo "[start] Installing dependencies..."
    "$VENV/pip" install -r requirements.txt --quiet
fi

log() { echo -e "\033[1;34m[start]\033[0m $*"; }

log "Starting FastAPI backend on :8000..."
PYTHONPATH="$ROOT_DIR" nohup "$VENV/uvicorn" app.api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info > /tmp/rca-api.log 2>&1 &
API_PID=$!
echo $API_PID > /tmp/rca-api.pid

sleep 2

log "Starting Streamlit UI on :8501..."
PYTHONPATH="$ROOT_DIR" nohup "$VENV/streamlit" run app/ui/streamlit_app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --theme.base dark \
    --theme.primaryColor "#6366f1" \
    --theme.backgroundColor "#0f172a" \
    --theme.secondaryBackgroundColor "#1e293b" \
    --theme.textColor "#e2e8f0" > /tmp/rca-ui.log 2>&1 &
UI_PID=$!
echo $UI_PID > /tmp/rca-ui.pid

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  RCA Agent running:"
echo "  UI  → http://localhost:8501"
echo "  API → http://localhost:8000/docs"
echo "  Logs: /tmp/rca-ui.log  /tmp/rca-api.log"
echo "  Stop: kill \$(cat /tmp/rca-ui.pid) \$(cat /tmp/rca-api.pid)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Keep alive — forward Ctrl+C to children
trap "kill $API_PID $UI_PID 2>/dev/null; exit" SIGINT SIGTERM
wait $API_PID $UI_PID
