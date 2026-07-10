"""
LangGraph state definition for the RCA workflow.

AgentState is the single data structure passed between all nodes.
Using TypedDict + Annotated[list, operator.add] means every node can
APPEND to lists without overwriting sibling nodes' contributions.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.llm.schemas import RCAOutput
from app.memory.chromadb_store import SimilarIncident
from app.tools.kubernetes_tools import K8sEvent, NodeSummary, PodSummary


class AgentState(TypedDict):
    # ── Inputs ────────────────────────────────────────────────
    namespace: str
    resource_name: str | None      # Specific pod/deployment to focus on
    resource_type: str             # "auto" | "pod" | "deployment" | "node"
    model_name: str

    # ── Collected Evidence ────────────────────────────────────
    unhealthy_pods: list[PodSummary]
    pod_describe: dict[str, Any]
    logs_raw: str
    log_analysis: str              # Pre-processed log summary for LLM
    events: list[K8sEvent]
    node_status: list[NodeSummary]
    metrics_summary: dict[str, Any]

    # ── Memory / Context ──────────────────────────────────────
    similar_incidents: list[SimilarIncident]
    knowledge_snippets: list[str]

    # ── LLM Output ────────────────────────────────────────────
    rca_output: RCAOutput | None
    evidence_context: str          # Formatted string sent to LLM

    # ── Persistence ───────────────────────────────────────────
    incident_id: str | None
    report_markdown_path: str | None
    report_pdf_path: str | None

    # ── Control ───────────────────────────────────────────────
    messages: Annotated[list[str], operator.add]   # Running log for UI
    errors: Annotated[list[str], operator.add]
    current_step: str
    completed: bool
