#!/usr/bin/env bash
set -euo pipefail

echo "Starting FastAPI backend on :8000..."
PYTHONPATH=/app uvicorn app.api.main:app \
    --host 0.0.0.0 --port 8000 --log-level info &

echo "Starting Streamlit UI on :8501..."
PYTHONPATH=/app streamlit run app/ui/streamlit_app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --theme.base dark \
    --theme.primaryColor "#6366f1" \
    --theme.backgroundColor "#0f172a" \
    --theme.secondaryBackgroundColor "#1e293b" \
    --theme.textColor "#e2e8f0" &

wait -n
