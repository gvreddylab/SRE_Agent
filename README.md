# SRE Agent — Kubernetes Root Cause Analysis Platform

<p align="center">
  <img src="data/assets/team.png" alt="SRE Team" width="720"/>
</p>

An autonomous SRE agent that monitors Kubernetes clusters, detects failures, performs structured root cause analysis using a local LLM, generates management-ready reports, and executes remediation actions — all from a conversational UI. No cloud APIs. No data leaves your environment.

---

## Table of Contents

- [What This Is](#what-this-is)
- [Architecture](#architecture)
- [RCA Workflow](#rca-workflow)
- [Capabilities](#capabilities)
- [Agent Scope](#agent-scope)
- [Executable Actions](#executable-actions)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Report Format](#report-format)
- [Running Tests](#running-tests)
- [Design Decisions](#design-decisions)

---

## What This Is

Most incident response tools either require cloud connectivity, give you dashboards without diagnosis, or tell you *what* broke without telling you *why*. This agent bridges that gap.

It watches your cluster continuously, and when something goes wrong:

1. Collects all available evidence — pod status, container logs, Kubernetes events, Prometheus metrics, node state
2. Correlates that evidence through a directed LangGraph workflow
3. Feeds it into a local Ollama LLM with a structured forensics prompt
4. Returns a confidence-scored root cause analysis with an immediate fix, validation steps, and preventive recommendations
5. Generates a PDF report formatted for engineering management sign-off
6. Lets you execute remediation directly from the chat interface

The agent is stateful. Every incident is stored in SQLite. Past RCAs are embedded in ChromaDB so similar incidents surface automatically as context for future analyses.

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║                        PRESENTATION LAYER                               ║
║                                                                          ║
║   ┌─────────────────────────────────────────────────────────────────┐   ║
║   │              Streamlit UI  (port 8501)                           │   ║
║   │                                                                  │   ║
║   │  🔍 RCA Analysis  │  ☸️ Cluster Dashboard  │  📚 Incident History │   ║
║   │  📊 RCA Reports   │  🔐 Auth (login/signup)                      │   ║
║   │                                                                  │   ║
║   │  ┌─────────────────────────────────────────────────────────┐    │   ║
║   │  │   Conversational Query Interface                         │    │   ║
║   │  │   "restart argocd repo pod"  → fuzzy match → Allow?     │    │   ║
║   │  │   "what is cluster health"   → live K8s query           │    │   ║
║   │  │   "get logs for argocd"      → tail 60 lines            │    │   ║
║   │  └─────────────────────────────────────────────────────────┘    │   ║
║   └─────────────────────────────────────────────────────────────────┘   ║
║                              │ direct Python call                        ║
╠══════════════════════════════════════════════════════════════════════════╣
║                         ORCHESTRATION LAYER                             ║
║                                                                          ║
║   ┌─────────────────────────────────────────────────────────────────┐   ║
║   │              LangGraph RCA Workflow (directed graph)             │   ║
║   │                                                                  │   ║
║   │  gather_pods ──► [healthy?] ──► END                             │   ║
║   │       │                                                          │   ║
║   │       └──► gather_logs ──► gather_events ──► gather_metrics     │   ║
║   │                │                                                 │   ║
║   │                └──► gather_nodes ──► query_memory               │   ║
║   │                               │                                  │   ║
║   │                               └──► build_context ──► call_llm   │   ║
║   │                                          │                       │   ║
║   │                                          └──► persist ──► report │   ║
║   └─────────────────────────────────────────────────────────────────┘   ║
║                              │                                           ║
╠══════════════════════════════════════════════════════════════════════════╣
║                           TOOL LAYER                                    ║
║                                                                          ║
║   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐  ║
║   │ Kubernetes   │  │  Prometheus  │  │   Log Tools  │  │  Ollama  │  ║
║   │ API (python  │  │  API Client  │  │  (pattern    │  │  LLM     │  ║
║   │  client)     │  │              │  │   matching)  │  │  llama3  │  ║
║   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬─────┘  ║
║          │                 │                  │               │         ║
╠══════════════════════════════════════════════════════════════════════════╣
║                         PERSISTENCE LAYER                               ║
║                                                                          ║
║   ┌──────────────────────┐          ┌───────────────────────────────┐   ║
║   │  SQLite              │          │  ChromaDB                     │   ║
║   │  • Incidents         │          │  • Past RCA embeddings        │   ║
║   │  • RCA Reports       │          │  • Semantic similarity search │   ║
║   │  • Evidence items    │          │  • Auto-surfaced on new RCA   │   ║
║   │  • Users & auth      │          └───────────────────────────────┘   ║
║   └──────────────────────┘                                               ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## RCA Workflow

The core analysis pipeline is a stateful directed graph built with LangGraph. Each node is independently testable and the graph short-circuits if the cluster is healthy.

```
                        ┌─────────────────┐
                        │   START (click) │
                        └────────┬────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      gather_pods        │
                    │  list_pod_all_ns()      │
                    │  classify each pod:     │
                    │  Running/Pending/Failed │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │           Route: healthy?            │
              └──────┬────────────────────┬──────────┘
                     │ YES                │ NO (unhealthy pods found)
         ┌───────────▼──────────┐         │
         │   cluster_healthy    │         │
         │  ✅ No RCA needed    │         │
         │  → END               │         │
         └──────────────────────┘         │
                                 ┌────────▼────────┐
                                 │   gather_logs   │
                                 │  current logs   │
                                 │  + prev logs on │
                                 │  CrashLoop      │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │  gather_events  │
                                 │  warning events │
                                 │  per namespace  │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │ gather_metrics  │
                                 │  Prometheus:    │
                                 │  CPU/mem/restart│
                                 │  (graceful skip)│
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │  gather_nodes   │
                                 │  node pressure  │
                                 │  + taints       │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │  query_memory   │
                                 │  ChromaDB       │
                                 │  top-3 similar  │
                                 │  past incidents │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │  build_context  │
                                 │  assemble full  │
                                 │  evidence pkg   │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │    call_llm     │
                                 │  Ollama local   │
                                 │  JSON-mode RCA  │
                                 │  + validation   │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │ persist_results │
                                 │  SQLite + Chroma│
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                                 │ generate_report │
                                 │  Markdown + PDF │
                                 └────────┬────────┘
                                          │
                                       ┌──▼──┐
                                       │ END │
                                       └─────┘
```

---

## Capabilities

### Failure Detection

The agent classifies pod health by inspecting **current container state**, not historical restart counts. A pod with 50 restarts but currently `Running` and `Ready` is healthy — restart count alone is not a signal.

| Issue Type | Detection Method |
|---|---|
| `CrashLoopBackOff` | `container.state.waiting.reason` |
| `OOMKilled` | `container.state.terminated.exit_code == 137` |
| `ImagePullBackOff` / `ErrImagePull` | `container.state.waiting.reason` |
| `CreateContainerError` | `container.state.waiting.reason` |
| `Pending` (unscheduled) | `pod.status.phase == Pending` |
| `Failed` | `pod.status.phase == Failed` |
| `NotReady` | `container.ready == False` with Running phase |
| `NodeNotReady` | node conditions, taints |
| `Evicted` | `pod.status.reason == Evicted` |

`Succeeded` phase pods are always excluded — completed jobs are not incidents.

### Evidence Collection

Every RCA run gathers:

- **Pod status** — phase, container states, restart counts, node assignment
- **Container logs** — current logs (last 200 lines) + previous container logs when crashed
- **Kubernetes events** — warning events scoped to the affected namespace
- **Prometheus metrics** — CPU/memory usage, restart rate, resource saturation (skipped gracefully if Prometheus unreachable)
- **Node state** — conditions, pressure flags, taints, allocatable resources
- **Semantic memory** — top-3 most similar past incidents from ChromaDB, injected as additional context

### LLM Analysis

The evidence package is fed to a local `llama3.1:8b` model (via Ollama) with a structured forensics prompt. The model is forced into JSON mode and must produce:

| Output Field | Description |
|---|---|
| `executive_summary` | 2-3 sentence plain-English summary for management |
| `root_cause` | Primary technical cause, not symptoms |
| `contributing_factors` | Secondary issues that amplified the impact |
| `impact_assessment` | Services affected, user-facing vs internal |
| `evidence_summary` | Key log lines and events that confirm the diagnosis |
| `confidence_score` | Float 0.0–1.0 based on evidence completeness |
| `severity` | `low` / `medium` / `high` / `critical` |
| `recommended_fix` | Concrete kubectl/config steps |
| `validation_steps` | How to verify the fix worked |
| `preventive_actions` | Infrastructure changes to prevent recurrence |
| `issue_type` | Canonical issue class (CrashLoopBackOff, OOMKilled, etc.) |

LLM responses are normalised before Pydantic validation — handles nested dicts/lists that `llama3.1:8b` sometimes returns for string fields.

### Conversational Interface

The query box handles two classes of input without any mode switching:

**Informational queries** — answered with live cluster data:
```
what is the cluster health
show me all failing pods
list deployments in argocd
show warning events
how many nodes are ready
describe argocd-server pod
```

**Action commands** — fuzzy-matched to cluster resources, confirmed before execution:
```
restart the argocd repo pod
rollout restart argocd-server deployment
scale argocd-server to 3 replicas
delete the crashed init pod
get logs for argocd-repo-server
```

Fuzzy matching tokenises resource names on `-` and scores query words against name tokens by intersection count. "argocd repo pod" matches `argocd-repo-server-b957bdbd9-pnc2t` with score 2 — the highest-scoring candidate wins. No embeddings, no ML — deterministic and explainable.

### Report Generation

Two formats generated per incident:

**Markdown** — 9-section structured document suitable for internal wikis or incident trackers.

**PDF** — Management-ready layout:
- Dark navy banner header with severity colour badge
- Per-section header bars
- Priority-coloured action items table (Immediate=red, Short-term=yellow, Long-term=green)
- Engineering sign-off table with Owner / Date / Status columns
- Footer disclaimer

No mention of the underlying LLM model anywhere in the generated report.

### Authentication

Multi-user login system with role-based access:

| Role | Permissions |
|---|---|
| `viewer` | View dashboards, incidents, reports |
| `operator` | All viewer access + execute cluster actions |
| `admin` | Full access |

Passwords hashed with bcrypt. No plaintext stored anywhere.

---

## Agent Scope

### What the agent handles

- Pod lifecycle failures across all namespaces or scoped to one
- Container-level issues: crash loops, OOM kills, image pull failures, config errors
- Node-level pressure: disk, memory, PID pressure, not-ready conditions
- Workload degradation: deployment rollout failures, replica mismatches
- Correlated multi-signal analysis: logs + events + metrics together, not in isolation
- Historical context: similar past incidents automatically retrieved and included
- Remediation execution: pod restart, deployment rollout restart, replica scaling, pod deletion, log retrieval
- Report generation formatted for management communication and post-incident review

### What the agent does not handle

- Application-level bugs (business logic, data correctness) — it sees symptoms in logs, not source code
- Network policy diagnosis — no CNI-level visibility into traffic flows
- Persistent volume failures beyond what surfaces in events and logs
- Multi-cluster analysis — one kubeconfig context per run
- Real-time alerting — triggered manually or via API; no push notification integration
- Service mesh telemetry (Istio/Linkerd distributed traces)
- Security incidents — not a SIEM; no audit log correlation

---

## Executable Actions

All actions go through an explicit **Allow / Deny** confirmation step in the UI before any K8s API call is made. No action executes on the first query — the agent presents what it found and what it intends to do, then waits.

| Command Phrase | Action | K8s API Call |
|---|---|---|
| `restart <pod-name>` | Delete pod | `core_v1.delete_namespaced_pod()` — controller recreates |
| `reboot <pod-name>` | Delete pod | Same as above |
| `bounce <pod-name>` | Delete pod | Same as above |
| `rollout restart <deployment>` | Patch deployment | Annotates `kubectl.kubernetes.io/restartedAt` |
| `rollout <deployment>` | Rollout restart | Same as above |
| `scale <deployment> to N` | Set replicas | `apps_v1.patch_namespaced_deployment(spec.replicas=N)` |
| `resize <deployment> to N` | Set replicas | Same as above |
| `delete <pod-name>` | Delete pod permanently | `core_v1.delete_namespaced_pod()` |
| `remove <pod-name>` | Delete pod permanently | Same as above |
| `kill <pod-name>` | Delete pod permanently | Same as above |
| `get logs <pod>` | Fetch last 60 lines | `core_v1.read_namespaced_pod_log(tail_lines=60)` |
| `show logs <pod>` | Fetch logs | Falls back to previous container logs on crash |
| `tail logs <pod>` | Fetch logs | Same, with previous container fallback |
| `fetch logs <pod>` | Fetch logs | Same |

Resource names are fuzzy-matched — partial names like "argocd repo", "server pod", "init" are resolved to the best-matching live resource in the cluster.

---

## Requirements

### System Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| Python | ≥ 3.12 | Runtime |
| Ollama | latest | Local LLM inference |
| kubectl | ≥ 1.28 | Cluster access via kubeconfig |
| k3d | ≥ 5.x | Local cluster (optional — any K8s works) |
| Prometheus | ≥ 2.x | Metrics source (optional — degrades gracefully) |

### Python Packages (key)

| Package | Version | Role |
|---|---|---|
| `streamlit` | 1.40.2 | UI framework |
| `langgraph` | 0.2.59 | RCA workflow orchestration |
| `langchain-ollama` | 0.2.1 | LLM integration |
| `langchain-core` | 0.3.25 | Message/chain primitives |
| `kubernetes` | 31.0.0 | K8s Python client |
| `chromadb` | 0.5.23 | Vector store for incident memory |
| `sentence-transformers` | 3.3.1 | Embeddings for ChromaDB |
| `sqlalchemy` | 2.0.36 | ORM for incident persistence |
| `fastapi` | 0.115.5 | REST API |
| `reportlab` | 4.2.5 | PDF generation |
| `pydantic` | 2.10.3 | Schema validation throughout |
| `pyarrow` | **17.0.0** | **Pinned** — 25.x segfaults on WSL2 |
| `bcrypt` | any | Password hashing for user auth |
| `tenacity` | 9.0.0 | LLM call retry with exponential backoff |

> **WSL2 Note:** `pyarrow>=18` triggers a `SIGSEGV` in `libarrow.so` on WSL2 kernels (confirmed on `6.1.x` and `6.18.x`). The version is pinned to `17.0.0`. Do not upgrade without testing on your specific kernel.

### LLM Model

```bash
ollama pull llama3.1:8b     # ~4.7 GB, minimum recommended
ollama pull llama3.1:70b    # better reasoning quality, requires ~48 GB RAM
```

The model must be running before the UI is started. The agent connects to `http://localhost:11434` by default.

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url> rca-agent
cd rca-agent
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum verify OLLAMA_DEFAULT_MODEL and K8S_CONTEXT
```

### 3. Start Ollama and pull model

```bash
# Start Ollama server (keep running in background)
ollama serve &

# Pull the inference model
ollama pull llama3.1:8b
```

Verify Ollama is reachable:
```bash
curl http://localhost:11434/api/tags | python3 -m json.tool
```

### 4. Verify cluster access

```bash
kubectl cluster-info
kubectl get nodes
```

The agent uses the **current context** from `~/.kube/config`. Switch context before starting if needed:
```bash
kubectl config use-context <your-context-name>
kubectl config current-context    # confirm
```

### 5. (Optional) Spin up a local k3d cluster

```bash
./scripts/setup.sh
```

This creates a 3-node k3d cluster, deploys Prometheus, Grafana, kube-state-metrics, and sets kubeconfig context to the new cluster.

### 6. Start the application

```bash
./scripts/start.sh
```

Or start components individually:

```bash
# FastAPI backend (optional — UI calls K8s directly)
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload &

# Streamlit UI
streamlit run app/ui/streamlit_app.py --server.port 8501 --server.headless true
```

| Service | URL |
|---|---|
| Streamlit UI | http://localhost:8501 |
| FastAPI docs | http://localhost:8000/docs |
| Grafana | http://localhost:3000 (admin/admin) |
| Prometheus | http://localhost:9090 |

### 7. Create your first account

The UI requires authentication. On first launch:
1. Open http://localhost:8501
2. Click the **Sign Up** tab
3. Enter full name, username, email, password, and select a role
4. Sign in with those credentials

Role guide:
- `viewer` — read-only: dashboards, incidents, reports
- `operator` — all viewer access + cluster action execution
- `admin` — full access

### 8. (Optional) Deploy broken demo apps

```bash
./scripts/demo.sh
```

Deploys `CrashLoopBackOff`, `OOMKilled`, and `ImagePullBackOff` demo workloads into the cluster. Click **Run RCA Analysis** in the UI to see the full pipeline in action.

---

## Configuration

All settings are loaded from `.env` via Pydantic Settings. Every variable has a sensible default and the system starts without any `.env` file present.

```bash
# ── Ollama ──────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.1:8b
OLLAMA_TEMPERATURE=0.1          # low = deterministic, high = creative
OLLAMA_TIMEOUT=180              # seconds; 70b models need 120+

# ── Kubernetes ──────────────────────────────────────
K8S_IN_CLUSTER=false            # set true when deployed inside a pod
K8S_CONTEXT=                    # blank = current context

# ── Prometheus ──────────────────────────────────────
PROMETHEUS_URL=http://localhost:9090
PROMETHEUS_TIMEOUT=10

# ── ChromaDB ────────────────────────────────────────
CHROMA_PERSIST_DIR=./data/chroma
CHROMA_COLLECTION=rca_incidents

# ── SQLite ──────────────────────────────────────────
SQLITE_DB_PATH=./data/sqlite/rca_incidents.db

# ── Application ─────────────────────────────────────
DEBUG=false
LOG_LEVEL=INFO
```

---

## Project Structure

```
rca-agent/
│
├── app/
│   ├── config.py                    # Centralised Pydantic settings
│   │
│   ├── auth/
│   │   ├── __init__.py
│   │   └── user_store.py            # bcrypt auth, SQLite users table
│   │
│   ├── agents/
│   │   └── rca_agent.py             # Public orchestration entrypoint
│   │
│   ├── graph/
│   │   ├── state.py                 # LangGraph AgentState TypedDict
│   │   └── workflow.py              # 10-node directed graph + conditional edges
│   │
│   ├── tools/
│   │   ├── kubernetes_tools.py      # K8s API wrappers + pod health classifier
│   │   ├── metrics_tools.py         # Prometheus query wrappers
│   │   └── log_tools.py             # Pattern-based log error extraction
│   │
│   ├── llm/
│   │   ├── schemas.py               # RCAOutput Pydantic model
│   │   └── ollama_client.py         # ChatOllama wrapper, JSON normalisation, retry
│   │
│   ├── memory/
│   │   └── chromadb_store.py        # Vector memory: store + semantic search
│   │
│   ├── database/
│   │   ├── models.py                # SQLAlchemy: Incident, RCAReport, Evidence
│   │   └── sqlite_store.py          # Async (FastAPI) + Sync (LangGraph) CRUD
│   │
│   ├── reports/
│   │   └── generator.py             # Markdown + ReportLab PDF generation
│   │
│   ├── api/
│   │   ├── main.py                  # FastAPI app factory
│   │   ├── schemas.py               # Request/response Pydantic models
│   │   └── routes/
│   │       ├── analysis.py          # /analysis/* endpoints
│   │       └── incidents.py         # /incidents/* endpoints
│   │
│   └── ui/
│       └── streamlit_app.py         # Multi-page Streamlit UI (auth-gated)
│
├── data/
│   ├── assets/
│   │   └── team.png                 # Sidebar branding image
│   ├── chroma/                      # ChromaDB vector store (auto-created)
│   ├── reports/                     # Generated Markdown + PDF files
│   └── sqlite/
│       ├── rca_incidents.db         # Incident + RCA report store
│       └── users.db                 # User authentication store
│
├── k8s/
│   ├── k3d-config.yaml              # k3d: 1 server, 2 agents, port mappings
│   ├── namespaces.yaml
│   ├── prometheus/                  # Prometheus + kube-state-metrics + RBAC
│   ├── grafana/                     # Grafana + pre-built RCA dashboard JSON
│   └── apps/                        # Demo broken workloads
│       ├── crashloop-app.yaml
│       ├── oom-app.yaml
│       └── imagepull-app.yaml
│
├── tests/
│   ├── test_log_tools.py            # Zero-dependency unit tests
│   ├── test_config.py
│   ├── test_llm_schema.py
│   └── test_database.py             # SQLite integration tests
│
├── scripts/
│   ├── setup.sh                     # One-shot: venv + k3d + monitoring stack
│   ├── start.sh                     # Start API + UI via nohup/disown
│   └── demo.sh                      # Deploy broken apps + trigger RCA
│
├── .env.example
├── requirements.txt
└── pyproject.toml
```

---

## API Reference

The FastAPI backend exposes a REST interface for programmatic access and CI/CD integration.

```
POST   /api/v1/analysis/run                    Full RCA (blocking, returns complete result)
POST   /api/v1/analysis/stream                 SSE stream of workflow step events
GET    /api/v1/analysis/cluster-health         Current cluster health snapshot
GET    /api/v1/analysis/status                 Connectivity: K8s / Ollama / Prometheus

GET    /api/v1/incidents                       List incidents (filter: ns, status, severity)
GET    /api/v1/incidents/{id}                  Incident detail + full RCA report body
GET    /api/v1/incidents/{id}/report/markdown  Download .md file
GET    /api/v1/incidents/{id}/report/pdf       Download .pdf file
PATCH  /api/v1/incidents/{id}/status           Update incident status
DELETE /api/v1/incidents/{id}                  Remove incident record
```

Full interactive docs with request/response schemas: http://localhost:8000/docs

---

## Report Format

Each incident generates a 9-section structured document:

```
┌─────────────────────────────────────────────────────────────┐
│  SECTION                        CONTENT                     │
├─────────────────────────────────────────────────────────────┤
│  1. Incident Cover              ID, timestamp, severity     │
│  2. Executive Summary           Plain-English, 2-3 sentences│
│  3. Incident Overview           Metadata table              │
│  4. Root Cause Analysis                                     │
│     4a. Primary Root Cause      The actual failure reason   │
│     4b. Contributing Factors    What made it worse          │
│     4c. Evidence Summary        Log lines + events cited    │
│  5. Impact Assessment           Services + blast radius     │
│  6. Remediation Plan                                        │
│     6a. Immediate Actions       kubectl steps now           │
│     6b. Validation Steps        How to confirm it's fixed   │
│  7. Preventive Measures         Infrastructure improvements │
│  8. Action Items Table                                      │
│     Immediate  → red                                        │
│     Short-term → yellow                                     │
│     Long-term  → green                                      │
│  9. Engineering Sign-off Table  Owner / Date / Status       │
└─────────────────────────────────────────────────────────────┘
```

Reports are stored in `data/reports/` and accessible from **📊 RCA Reports** in the UI.

---

## Running Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

| Test File | Scope | External Dependencies |
|---|---|---|
| `test_log_tools.py` | Log pattern matching | None |
| `test_config.py` | Settings loading | None |
| `test_llm_schema.py` | RCAOutput Pydantic validation | None |
| `test_database.py` | SQLite CRUD operations | SQLite only (bundled) |

Tests that require a live K8s cluster or Ollama are tagged as integration tests and skipped unless the environment is available.

---

## Design Decisions

**Why LangGraph instead of a plain function chain?**

Each node is independently testable and the graph state is fully inspectable at every step. The conditional routing after `gather_pods` means a healthy cluster short-circuits with zero LLM calls. Adding a new analysis step is `g.add_node()` + `g.add_edge()` — not a refactor of a monolithic pipeline function.

**Why local Ollama instead of OpenAI/Anthropic APIs?**

Kubernetes clusters frequently contain sensitive data in logs and environment variables — database credentials, API tokens, internal hostnames. Running inference locally means that data never crosses a network boundary. It also eliminates API cost and rate-limit exposure during high-incident periods when you're making many rapid analysis calls.

**Why SQLite instead of PostgreSQL?**

This is a single-node SRE tool, not a multi-tenant SaaS platform. SQLite's zero-config, embeddable nature matches the deployment model exactly. Switching to PostgreSQL requires only changing the connection URL — SQLAlchemy abstracts the difference.

**Why `pyarrow==17.0.0` pinned?**

`pyarrow>=18` causes a `SIGSEGV` in `libarrow.so` on WSL2 kernels (confirmed on `6.1.x` and `6.18.x`). The crash happens during Streamlit's internal DataFrame serialisation. `17.0.0` is stable with NumPy `2.1.x`. The pin stays until the upstream fix is confirmed on affected kernels.

**Why bcrypt for passwords?**

`hashlib.sha256` is fast — which is a vulnerability for password storage, not a feature. Speed makes brute-force cheap. bcrypt has a tunable work factor built in and is the correct primitive for password hashing at any scale.

**Why fuzzy resource matching in the chat?**

Pod names in Kubernetes contain generated suffixes (`argocd-repo-server-b957bdbd9-pnc2t`). Requiring exact names in a conversational interface defeats the purpose. Token intersection scoring on `-`-delimited name segments gives deterministic, explainable matches without the overhead of embedding models for short strings.
