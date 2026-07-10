#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# RCA Agent — One-shot setup script
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

log() { echo -e "\033[1;34m[setup]\033[0m $*"; }
ok()  { echo -e "\033[1;32m[ ok ]\033[0m $*"; }
err() { echo -e "\033[1;31m[err ]\033[0m $*" >&2; }

cd "$ROOT_DIR"

# ── 1. Python virtual environment ──────────────────────────────────────────
log "Creating Python virtual environment..."
python3.12 -m venv .venv || python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
ok "Python deps installed"

# ── 2. .env file ──────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    log ".env created from template — edit as needed"
else
    ok ".env already exists"
fi

# ── 3. Data directories ────────────────────────────────────────────────────
mkdir -p data/{chroma,sqlite,reports}
ok "Data directories ready"

# ── 4. k3d cluster ─────────────────────────────────────────────────────────
if command -v k3d &>/dev/null; then
    if ! k3d cluster list 2>/dev/null | grep -q "rca-cluster"; then
        log "Creating k3d cluster..."
        k3d cluster create --config k8s/k3d-config.yaml
        ok "k3d cluster created"
    else
        ok "k3d cluster already running"
    fi
else
    log "k3d not found — skipping cluster creation (install: https://k3d.io)"
fi

# ── 5. Deploy monitoring stack ─────────────────────────────────────────────
if command -v kubectl &>/dev/null && kubectl cluster-info &>/dev/null 2>&1; then
    log "Deploying Kubernetes manifests..."
    kubectl apply -f k8s/namespaces.yaml
    kubectl apply -f k8s/prometheus/
    kubectl apply -f k8s/grafana/
    kubectl apply -f k8s/apps/
    ok "Manifests applied"
else
    log "kubectl not connected — skipping K8s manifest deployment"
fi

# ── 6. Ollama model check ──────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    DEFAULT_MODEL=$(grep OLLAMA_DEFAULT_MODEL .env | cut -d= -f2 | tr -d '"')
    DEFAULT_MODEL="${DEFAULT_MODEL:-llama3.1:8b}"
    if ! ollama list 2>/dev/null | grep -q "${DEFAULT_MODEL%%:*}"; then
        log "Pulling Ollama model: $DEFAULT_MODEL"
        ollama pull "$DEFAULT_MODEL"
        ok "Model ready"
    else
        ok "Ollama model $DEFAULT_MODEL already present"
    fi
else
    log "Ollama not found — install from https://ollama.com and run: ollama pull llama3.1:8b"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete! Start services with:"
echo ""
echo "  source .venv/bin/activate"
echo "  ./scripts/start.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
