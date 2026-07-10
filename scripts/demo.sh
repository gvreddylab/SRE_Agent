#!/usr/bin/env bash
# Deploy broken demo apps and trigger an RCA analysis via the API.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[ -f ".venv/bin/activate" ] && source .venv/bin/activate

log() { echo -e "\033[1;35m[demo]\033[0m $*"; }
ok()  { echo -e "\033[1;32m[ ok ]\033[0m $*"; }

# ── Deploy broken apps ──────────────────────────────────────────────────────
log "Deploying demo broken applications to demo-apps namespace..."
kubectl apply -f k8s/namespaces.yaml 2>/dev/null || true
kubectl apply -f k8s/apps/

log "Waiting 30 seconds for pods to enter broken state..."
sleep 30

log "Current pod status in demo-apps:"
kubectl get pods -n demo-apps -o wide

# ── Trigger RCA via API ─────────────────────────────────────────────────────
log "Triggering RCA analysis via FastAPI..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/v1/analysis/run \
    -H "Content-Type: application/json" \
    -d '{"namespace": "demo-apps"}')

echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

INCIDENT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('incident_id',''))" 2>/dev/null || echo "")

if [ -n "$INCIDENT_ID" ]; then
    ok "RCA complete! Incident ID: $INCIDENT_ID"
    log "Downloading markdown report..."
    curl -s "http://localhost:8000/api/v1/incidents/$INCIDENT_ID/report/markdown" \
        -o "data/reports/demo_report.md"
    ok "Report saved to data/reports/demo_report.md"
fi

log "Opening Streamlit UI: http://localhost:8501"
