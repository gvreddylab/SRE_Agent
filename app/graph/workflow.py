"""
LangGraph RCA Workflow.

Graph topology (left to right):
  START
    → gather_pods
    → gather_logs          (if pods found)
    → gather_events
    → gather_metrics
    → query_memory
    → build_context
    → call_llm
    → persist_results
    → generate_reports
  END

Each node updates a slice of AgentState and appends a human-readable
message so the Streamlit UI can display live progress.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.database.models import IncidentStatus, IssueType, Severity
from app.database.sqlite_store import init_db_sync, sync_incident_store
from app.graph.state import AgentState
from app.llm.ollama_client import generate_rca
from app.llm.schemas import RCAOutput  # noqa: F401  kept for type references
from app.memory.chromadb_store import SimilarIncident, chroma_store
from app.tools.kubernetes_tools import (
    PodSummary,
    describe_pod,
    get_deployment_status,
    get_namespace_events,
    get_node_status,
    get_pod_logs,
    list_unhealthy_pods,
)
from app.tools.log_tools import analyse_logs, format_log_context
from app.tools.metrics_tools import get_metrics_summary

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helper: evidence context builder
# ──────────────────────────────────────────────────────────────

def _format_pod(p: PodSummary) -> str:
    cs_summary = ", ".join(
        f"{cs['name']}:{cs.get('state','?')}(restarts={cs.get('restart_count',0)})"
        for cs in p.container_statuses
    )
    return (
        f"  Pod: {p.namespace}/{p.name}\n"
        f"  Phase: {p.phase} | Issue: {p.issue_type} | Node: {p.node_name}\n"
        f"  Containers: {cs_summary}\n"
        f"  Restarts: {p.restart_count}"
    )


def _format_events(events) -> str:
    if not events:
        return "  No warning events."
    lines = []
    for e in events[:20]:
        lines.append(
            f"  [{e.event_type}] {e.reason}: {e.message[:200]} "
            f"(count={e.count}, object={e.regarding_kind}/{e.regarding_name})"
        )
    return "\n".join(lines)


def _format_similar(similars: list[SimilarIncident]) -> str:
    if not similars:
        return "  No similar past incidents found."
    lines = []
    for s in similars:
        lines.append(
            f"  [{s.similarity:.0%} match] {s.title} ({s.issue_type}) | "
            f"Root cause: {s.root_cause[:150]} | "
            f"Fix: {s.recommended_fix[:150]}"
        )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Node implementations
# ──────────────────────────────────────────────────────────────

def node_gather_pods(state: AgentState) -> dict:
    logger.info("Node: gather_pods | ns=%s", state["namespace"])
    pods = list_unhealthy_pods(namespace=state["namespace"])
    return {
        "unhealthy_pods": pods,
        "messages": [f"🔍 Found {len(pods)} unhealthy pod(s) in namespace '{state['namespace']}'"],
        "current_step": "gather_pods",
    }


def node_gather_logs(state: AgentState) -> dict:
    pods = state["unhealthy_pods"]
    if not pods:
        return {
            "logs_raw": "",
            "log_analysis": "No unhealthy pods — no logs to gather.",
            "pod_describe": {},
            "messages": ["ℹ️  No unhealthy pods found; skipping log collection."],
            "current_step": "gather_logs",
        }

    # Focus on the specific resource if provided, else pick the worst pod
    target_pod: PodSummary | None = None
    if state.get("resource_name"):
        target_pod = next((p for p in pods if p.name == state["resource_name"]), None)
    if target_pod is None:
        # Prioritise CrashLoopBackOff → OOMKilled → highest restarts
        priority_order = [
            IssueType.CRASH_LOOP_BACKOFF,
            IssueType.OOM_KILLED,
            IssueType.IMAGE_PULL_BACKOFF,
        ]
        for issue in priority_order:
            target_pod = next((p for p in pods if p.issue_type == issue), None)
            if target_pod:
                break
        if target_pod is None:
            target_pod = max(pods, key=lambda p: p.restart_count)

    logger.info("Gathering logs for pod=%s/%s", target_pod.namespace, target_pod.name)

    # Try previous (crashed) container first
    logs = get_pod_logs(
        pod_name=target_pod.name,
        namespace=target_pod.namespace,
        previous=True,
    )
    if "(log retrieval failed" in logs or logs == "(no logs)":
        logs = get_pod_logs(
            pod_name=target_pod.name,
            namespace=target_pod.namespace,
            previous=False,
        )

    describe = describe_pod(pod_name=target_pod.name, namespace=target_pod.namespace)
    log_analysis_obj = analyse_logs(logs)
    log_context = format_log_context(log_analysis_obj)

    return {
        "logs_raw": logs,
        "log_analysis": log_context,
        "pod_describe": describe,
        "messages": [
            f"📋 Collected logs for pod {target_pod.namespace}/{target_pod.name} "
            f"({log_analysis_obj.error_count} error lines, signals: {', '.join(log_analysis_obj.signals) or 'none'})"
        ],
        "current_step": "gather_logs",
    }


def node_gather_events(state: AgentState) -> dict:
    logger.info("Node: gather_events | ns=%s", state["namespace"])
    events = get_namespace_events(namespace=state["namespace"])
    warning_count = sum(1 for e in events if e.event_type == "Warning")
    return {
        "events": events,
        "messages": [f"📢 Retrieved {len(events)} events ({warning_count} warnings)"],
        "current_step": "gather_events",
    }


def node_gather_metrics(state: AgentState) -> dict:
    logger.info("Node: gather_metrics")
    pods = state["unhealthy_pods"]
    pod_name = pods[0].name if pods else None
    try:
        metrics = get_metrics_summary(namespace=state["namespace"], pod_name=pod_name)
        msg = "📊 Prometheus metrics collected."
    except Exception as exc:
        metrics = {"error": str(exc)}
        msg = f"⚠️  Metrics collection failed: {exc}"

    return {
        "metrics_summary": metrics,
        "messages": [msg],
        "current_step": "gather_metrics",
    }


def node_gather_nodes(state: AgentState) -> dict:
    logger.info("Node: gather_nodes")
    nodes = get_node_status()
    unhealthy_nodes = [n for n in nodes if not n.ready]
    return {
        "node_status": nodes,
        "messages": [
            f"🖥️  Nodes: {len(nodes)} total, "
            f"{len(unhealthy_nodes)} not-ready"
            + (f" ({', '.join(n.name for n in unhealthy_nodes)})" if unhealthy_nodes else "")
        ],
        "current_step": "gather_nodes",
    }


def node_query_memory(state: AgentState) -> dict:
    logger.info("Node: query_memory")
    # Build a symptom description from the current state
    pods = state["unhealthy_pods"]
    issue_type = pods[0].issue_type if pods else "Unknown"
    symptom = (
        f"Kubernetes {issue_type} in namespace {state['namespace']}. "
        f"Pods: {[p.name for p in pods[:3]]}. "
        f"Log signals: {state.get('log_analysis', '')[:200]}"
    )

    try:
        similar = chroma_store.find_similar_incidents(
            symptom_description=symptom,
            issue_type=issue_type,
        )
        knowledge = chroma_store.query_knowledge(symptom, top_k=3)
        msg = f"🧠 Found {len(similar)} similar past incident(s) in memory."
    except Exception as exc:
        similar = []
        knowledge = []
        msg = f"⚠️  Memory query failed: {exc}"

    return {
        "similar_incidents": similar,
        "knowledge_snippets": knowledge,
        "messages": [msg],
        "current_step": "query_memory",
    }


def node_build_context(state: AgentState) -> dict:
    """Assemble all evidence into a single, well-structured prompt context."""
    logger.info("Node: build_context")

    pods = state["unhealthy_pods"]
    sections: list[str] = []

    # 1. Pod status
    sections.append("## 1. Unhealthy Pods\n" + (
        "\n\n".join(_format_pod(p) for p in pods[:5])
        if pods else "  None detected."
    ))

    # 2. Pod describe
    if state.get("pod_describe"):
        sections.append(
            "## 2. Pod Describe\n"
            + json.dumps(state["pod_describe"], indent=2, default=str)[:3000]
        )

    # 3. Logs
    sections.append("## 3. Container Log Analysis\n" + (state.get("log_analysis") or "Not available."))

    # 4. Events
    sections.append("## 4. Kubernetes Events\n" + _format_events(state.get("events", [])))

    # 5. Node status
    nodes = state.get("node_status", [])
    if nodes:
        node_lines = [
            f"  {n.name}: {'Ready' if n.ready else 'NOT READY'} | roles={n.roles} | version={n.version}"
            for n in nodes
        ]
        sections.append("## 5. Node Status\n" + "\n".join(node_lines))

    # 6. Metrics
    metrics = state.get("metrics_summary", {})
    if metrics and "error" not in metrics:
        sections.append(
            "## 6. Prometheus Metrics\n"
            + json.dumps(metrics, indent=2, default=str)[:2000]
        )

    # 7. Similar past incidents
    sections.append(
        "## 7. Similar Past Incidents (from memory)\n"
        + _format_similar(state.get("similar_incidents", []))
    )

    # 8. Runbook knowledge
    snippets = state.get("knowledge_snippets", [])
    if snippets:
        sections.append(
            "## 8. Runbook Knowledge\n"
            + "\n---\n".join(snippets)
        )

    context = "\n\n".join(sections)
    return {
        "evidence_context": context,
        "messages": ["🗂️  Evidence context assembled."],
        "current_step": "build_context",
    }


def node_call_llm(state: AgentState) -> dict:
    logger.info("Node: call_llm | model=%s", state["model_name"])
    try:
        rca = generate_rca(
            evidence_context=state["evidence_context"],
            model_name=state["model_name"],
        )
        msg = (
            f"🤖 RCA generated | confidence={rca.confidence_score:.0%} | "
            f"severity={rca.severity} | issue={rca.issue_type}"
        )
        return {
            "rca_output": rca,
            "messages": [msg],
            "current_step": "call_llm",
        }
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return {
            "errors": [f"LLM error: {exc}"],
            "messages": ["❌ LLM analysis failed."],
            "current_step": "call_llm",
        }


def node_persist_results(state: AgentState) -> dict:
    logger.info("Node: persist_results")
    rca = state.get("rca_output")
    if not rca:
        return {"messages": ["⚠️  No RCA output to persist."], "current_step": "persist_results"}

    pods = state["unhealthy_pods"]
    target_pod = pods[0] if pods else None

    try:
        # Ensure tables exist
        init_db_sync()

        incident = sync_incident_store.create_incident(
            title=f"{rca.issue_type} in {state['namespace']}",
            namespace=state["namespace"],
            resource_type=state["resource_type"],
            resource_name=target_pod.name if target_pod else (state.get("resource_name") or "unknown"),
            issue_type=rca.issue_type,
            severity=rca.severity,
            status=IncidentStatus.INVESTIGATING,
            confidence_score=rca.confidence_score,
            model_used=state["model_name"],
        )

        sync_incident_store.save_rca_report(
            incident_id=incident.id,
            executive_summary=rca.executive_summary,
            root_cause=rca.root_cause,
            contributing_factors=rca.contributing_factors,
            impact_assessment=rca.impact_assessment,
            evidence_summary=rca.evidence_summary,
            recommended_fix=rca.recommended_fix,
            validation_steps=rca.validation_steps,
            preventive_actions=rca.preventive_actions,
            similar_incidents=[
                {
                    "incident_id": s.incident_id,
                    "title": s.title,
                    "similarity": s.similarity,
                }
                for s in state.get("similar_incidents", [])
            ],
            raw_llm_output=state.get("evidence_context", ""),
        )

        # Store in ChromaDB for future retrieval
        chroma_store.store_incident(
            incident_id=incident.id,
            title=f"{rca.issue_type} in {state['namespace']}",
            issue_type=rca.issue_type,
            namespace=state["namespace"],
            root_cause=rca.root_cause,
            recommended_fix=rca.recommended_fix,
            confidence_score=rca.confidence_score,
            executive_summary=rca.executive_summary,
        )

        return {
            "incident_id": incident.id,
            "messages": [f"💾 Incident persisted (id={incident.id})"],
            "current_step": "persist_results",
        }
    except Exception as exc:
        logger.error("Persist failed: %s", exc)
        return {
            "errors": [f"Persist error: {exc}"],
            "messages": ["⚠️  Failed to persist incident."],
            "current_step": "persist_results",
        }


def node_generate_reports(state: AgentState) -> dict:
    """Generate Markdown and PDF reports."""
    logger.info("Node: generate_reports")
    rca = state.get("rca_output")
    incident_id = state.get("incident_id")
    if not rca or not incident_id:
        return {
            "messages": ["⚠️  Skipping report generation (no RCA output or incident ID)."],
            "completed": True,
            "current_step": "generate_reports",
        }

    try:
        from app.reports.generator import ReportGenerator
        gen = ReportGenerator()
        md_path = gen.generate_markdown(
            incident_id=incident_id,
            rca=rca,
            namespace=state["namespace"],
            similar_incidents=state.get("similar_incidents", []),
        )
        pdf_path = gen.generate_pdf(
            incident_id=incident_id,
            rca=rca,
            namespace=state["namespace"],
        )
        # Update DB with file paths
        from app.database.sqlite_store import sync_incident_store as store
        report = store.get_report(incident_id)
        if report:
            from app.database.sqlite_store import get_sync_session
            with get_sync_session() as session:
                db_report = session.get(type(report), report.id)
                if db_report:
                    db_report.markdown_path = str(md_path)
                    db_report.pdf_path = str(pdf_path)

        return {
            "report_markdown_path": str(md_path),
            "report_pdf_path": str(pdf_path),
            "messages": [f"📄 Reports generated: {md_path.name}"],
            "completed": True,
            "current_step": "done",
        }
    except Exception as exc:
        logger.error("Report generation failed: %s", exc)
        return {
            "errors": [f"Report error: {exc}"],
            "messages": ["⚠️  Report generation failed."],
            "completed": True,
            "current_step": "done",
        }


# ──────────────────────────────────────────────────────────────
# Graph construction
# ──────────────────────────────────────────────────────────────

def _route_after_pods(state: AgentState) -> str:
    """Skip LLM pipeline entirely when the cluster is healthy."""
    if not state.get("unhealthy_pods"):
        return "healthy"
    return "gather_logs"


def node_cluster_healthy(state: AgentState) -> dict:
    return {
        "messages": ["✅ Cluster is healthy — no unhealthy pods found. No RCA needed."],
        "current_step": "cluster_healthy",
    }


def build_rca_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("gather_pods", node_gather_pods)
    g.add_node("cluster_healthy", node_cluster_healthy)
    g.add_node("gather_logs", node_gather_logs)
    g.add_node("gather_events", node_gather_events)
    g.add_node("gather_metrics", node_gather_metrics)
    g.add_node("gather_nodes", node_gather_nodes)
    g.add_node("query_memory", node_query_memory)
    g.add_node("build_context", node_build_context)
    g.add_node("call_llm", node_call_llm)
    g.add_node("persist_results", node_persist_results)
    g.add_node("generate_reports", node_generate_reports)

    g.add_edge(START, "gather_pods")
    g.add_conditional_edges("gather_pods", _route_after_pods, {
        "healthy": "cluster_healthy",
        "gather_logs": "gather_logs",
    })
    g.add_edge("cluster_healthy", END)
    g.add_edge("gather_logs", "gather_events")
    g.add_edge("gather_events", "gather_metrics")
    g.add_edge("gather_metrics", "gather_nodes")
    g.add_edge("gather_nodes", "query_memory")
    g.add_edge("query_memory", "build_context")
    g.add_edge("build_context", "call_llm")
    g.add_edge("call_llm", "persist_results")
    g.add_edge("persist_results", "generate_reports")
    g.add_edge("generate_reports", END)

    return g


def compile_rca_graph():
    return build_rca_graph().compile()
