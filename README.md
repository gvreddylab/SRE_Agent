# SRE Agent — Kubernetes Root Cause Analysis Platform

<p align="center">
  <img src="data/assets/RCA Image.png" alt="SRE Agent Dashboard" width="900"/>
</p>

Autonomous SRE agent that detects Kubernetes failures, runs root cause analysis using a local LLM, and lets you execute remediation — all from a conversational UI. No cloud APIs. No data leaves your environment.

---

## Stack

`LangGraph` · `Ollama (llama3.1:8b)` · `Kubernetes Python Client` · `ChromaDB` · `SQLite` · `FastAPI` · `Streamlit` · `ReportLab`

---

## Architecture

```
  Streamlit UI  ──►  LangGraph Workflow  ──►  K8s API / Prometheus / Ollama
       │                    │                          │
  Auth (bcrypt)       SQLite (incidents)         ChromaDB (memory)
```

**Workflow:** `gather_pods → [healthy? → END] → gather_logs → gather_events → gather_metrics → gather_nodes → query_memory → build_context → call_llm → persist → generate_report`

Short-circuits immediately when cluster is healthy — zero LLM calls.

---

## Features

| | |
|---|---|
| **Failure Detection** | CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending, Failed, NodeNotReady, Evicted |
| **Evidence Collection** | Pod status, container logs (current + previous), K8s events, Prometheus metrics, node state |
| **LLM Analysis** | Confidence-scored RCA: root cause, contributing factors, impact, remediation, prevention |
| **Semantic Memory** | ChromaDB stores past RCAs; top-3 similar incidents auto-surfaced on each new analysis |
| **Report Generation** | Management-ready PDF + Markdown — 9 sections, sign-off table, priority action items |
| **Conversational Actions** | Query cluster state or execute actions via chat with Allow/Deny confirmation |
| **Authentication** | Multi-user login with bcrypt hashing, role-based access (viewer / operator / admin) |

---

## Executable Actions

All actions require explicit **Allow / Deny** confirmation before execution.

| Say | Action |
|---|---|
| `restart <pod>` / `reboot` / `bounce` | Delete pod — controller recreates it |
| `rollout restart <deployment>` | Patch deployment with `restartedAt` annotation |
| `scale <deployment> to N` | Set replica count |
| `delete <pod>` / `remove` / `kill` | Permanently delete pod |
| `get logs <pod>` / `show logs` / `tail logs` | Fetch last 60 lines (falls back to previous container) |

Resource names are fuzzy-matched — "argocd repo pod" resolves to `argocd-repo-server-b957bdbd9-pnc2t` automatically.

---

## Setup

**Prerequisites:** Python ≥ 3.12, Ollama, kubectl configured against your cluster

```bash
# 1. Clone and install
git clone https://github.com/gvreddylab/SRE_Agent.git && cd SRE_Agent
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env   # set OLLAMA_DEFAULT_MODEL, K8S_CONTEXT if needed

# 3. Pull LLM model
ollama serve &
ollama pull llama3.1:8b

# 4. (Optional) Spin up local k3d cluster + monitoring stack
./scripts/setup.sh

# 5. Start
./scripts/start.sh
```

| Service | URL |
|---|---|
| UI | http://localhost:8501 |
| API Docs | http://localhost:8000/docs |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |

On first launch, open the UI → **Sign Up** → create your account.

---

## Key Configuration

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.1:8b
K8S_CONTEXT=                          # blank = current kubeconfig context
PROMETHEUS_URL=http://localhost:9090
SQLITE_DB_PATH=./data/sqlite/rca_incidents.db
CHROMA_PERSIST_DIR=./data/chroma
```

Full reference in `.env.example`.

---

## Project Structure

```
app/
├── auth/          # bcrypt login, SQLite user store
├── graph/         # LangGraph workflow (state + 10-node DAG)
├── tools/         # K8s API, Prometheus, log analysis
├── llm/           # Ollama client, RCAOutput schema, JSON normalisation
├── memory/        # ChromaDB semantic search
├── database/      # SQLAlchemy models, async + sync SQLite CRUD
├── reports/       # Markdown + PDF generation (ReportLab)
├── api/           # FastAPI routes (/analysis, /incidents)
└── ui/            # Streamlit multi-page app

data/
├── sqlite/        # Incident DB + user DB
├── chroma/        # Vector store
└── reports/       # Generated MD + PDF files

k8s/               # k3d config, Prometheus, Grafana, demo broken apps
scripts/           # setup.sh · start.sh · demo.sh
tests/             # Unit + integration tests
```

---

## API

```
POST  /api/v1/analysis/run              Trigger full RCA
POST  /api/v1/analysis/stream           SSE stream of workflow steps
GET   /api/v1/analysis/cluster-health   Cluster health snapshot
GET   /api/v1/analysis/status           K8s / Ollama / Prometheus connectivity

GET   /api/v1/incidents                 List incidents
GET   /api/v1/incidents/{id}            Incident + RCA detail
GET   /api/v1/incidents/{id}/report/markdown
GET   /api/v1/incidents/{id}/report/pdf
PATCH /api/v1/incidents/{id}/status
```

---

## Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```
