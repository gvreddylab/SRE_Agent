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

## Running the Agent

Choose **one** path — local or Kubernetes. Both reach the same UI at the end.

---

### Option 1 — Run Locally

After completing Setup above:

```bash
# Start both API and UI
./scripts/start.sh
```

That's it. Open http://localhost:8501, sign up, and start querying your cluster.

If `start.sh` is unavailable, run manually:

```bash
source .venv/bin/activate

# Terminal 1 — API
PYTHONPATH=. uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — UI
PYTHONPATH=. streamlit run app/ui/streamlit_app.py --server.port 8501
```

Your kubeconfig context is used automatically — the agent talks directly to whatever cluster `kubectl` is pointed at.

---

### Option 2 — Run on Kubernetes (Optional)

Requires: `kubectl` with cluster access, `k3d` (if using local cluster), ArgoCD installed.

**Step 1 — Build and import the image**

```bash
docker build -t sre-agent:latest .
k3d image import sre-agent:latest -c <your-cluster-name>
```

**Step 2 — Apply manifests**

```bash
kubectl apply -f k8s/sre-agent/
```

This creates the `sre-agent` namespace, RBAC, ConfigMap, PVCs, Deployments, and Services.

**Step 3 — (Optional) Deploy via ArgoCD**

```bash
kubectl apply -f argocd/application.yaml
```

ArgoCD will sync `k8s/sre-agent/` from your repo and keep it in sync automatically.

**Step 4 — Access the UI**

NodePort access depends on your cluster network. Use port-forward if NodePort is not reachable:

```bash
kubectl port-forward svc/sre-agent 8501:8501 -n sre-agent
```

Then open http://localhost:8501.

**Step 5 — First login**

Open the UI → **Sign Up** → create your account. User data is stored in the PVC and persists across pod restarts.

> **Data persistence:** All data (SQLite DBs, ChromaDB vectors, reports) is stored on the `sre-agent-data` PVC (5Gi). Data survives pod restarts and redeployments. It is only lost if the PVC is explicitly deleted.

---

### Adapting the Pipeline to Your Own Machine

If you cloned this repo and want to run the GitLab CI → k3d → ArgoCD pipeline on your own laptop, change the following before pushing:

**1. `.gitlab-ci.yml` — runner tag and cluster name**

The `tags` value and the k3d cluster name are environment-specific:

```yaml
# Change this tag to match your registered runner's tag
tags:
  - wsl          # ← replace with your runner tag

# Change "lab" to whatever you named your k3d cluster
- k3d image import sre-agent:latest -c lab    # ← replace "lab"
```

Create your k3d cluster first:
```bash
k3d cluster create <your-cluster-name> --agents 1
```

**2. `argocd/application.yaml` — repo URL**

The `repoURL` is  to the original GitLab instance:

```yaml
source:
    repoURL: https://<git-server>/<organization>/<repository>.git   # ← replace with your GitLab/GitHub URL
```

Set it to wherever you pushed the repo (local GitLab, GitHub, etc.).

**3. Register a GitLab runner on your machine**

Use a shell executor so the runner can call `docker` and `kubectl` directly:

```bash
gitlab-runner register \
  --url <your-gitlab-url> \
  --token <your-registration-token> \
  --executor shell \
  --description "my-local-runner"
```

Then in `~/.gitlab-runner/config.toml` ensure the runner's environment has:
```toml
environment = [
  "KUBECONFIG=/home/<your-user>/.kube/config",
  "HOME=/home/<your-user>",
  "USER=<your-user>",
  "DOCKER_HOST=unix:///var/run/docker.sock"
]
```

**4. ArgoCD — add your repo as a source**

```bash
kubectl create secret generic sre-agent-repo \
  --from-literal=type=git \
  --from-literal=url=<your-repo-url> \
  --from-literal=username=<git-user> \
  --from-literal=password=<git-token> \
  -n argocd

kubectl label secret sre-agent-repo \
  argocd.argoproj.io/secret-type=repository \
  -n argocd
```

**5. `k8s/sre-agent/configmap.yaml` — only if your Prometheus URL differs**

If you don't have Prometheus, remove or comment out `PROMETHEUS_URL`. Everything else in the ConfigMap works as-is for a k3d deployment.

**Summary of what must change vs what stays the same:**

| File | What to change | What stays the same |
|---|---|---|
| `.gitlab-ci.yml` | Runner tag, k3d cluster name | Stage names, script logic |
| `argocd/application.yaml` | `repoURL` | Path, sync policy, destination |
| `~/.gitlab-runner/config.toml` | User paths, runner token | Executor type (shell), env var keys |
| `k8s/sre-agent/configmap.yaml` | `PROMETHEUS_URL` if not installed | All other env vars |
| Everything else | Nothing | Manifests, Dockerfile, app code |

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
