"""
RCA Agent — public orchestration interface.

Usage::

    from app.agents.rca_agent import RCAAgent

    agent = RCAAgent()
    result = agent.run_analysis(
        namespace="production",
        model_name="llama3.1:8b",
    )
    print(result.rca_output.executive_summary)

For streaming progress (Streamlit UI)::

    for step_state in agent.stream_analysis(namespace="production"):
        print(step_state["current_step"], step_state["messages"][-1])
"""

from __future__ import annotations

import logging
from typing import Generator, Iterator

from app.config import settings
from app.database.sqlite_store import init_db_sync
from app.graph.state import AgentState
from app.graph.workflow import compile_rca_graph
from app.memory.chromadb_store import chroma_store

logger = logging.getLogger(__name__)


class RCAAgent:
    """
    Wrapper around the compiled LangGraph that provides a clean API
    for triggering RCA analysis runs.
    """

    def __init__(self) -> None:
        self._graph = compile_rca_graph()
        init_db_sync()
        chroma_store.seed_knowledge_base()
        logger.info("RCAAgent initialised")

    def _build_initial_state(
        self,
        namespace: str,
        resource_name: str | None = None,
        resource_type: str = "auto",
        model_name: str | None = None,
    ) -> AgentState:
        return AgentState(
            namespace=namespace,
            resource_name=resource_name,
            resource_type=resource_type,
            model_name=model_name or settings.ollama.default_model,
            unhealthy_pods=[],
            pod_describe={},
            logs_raw="",
            log_analysis="",
            events=[],
            node_status=[],
            metrics_summary={},
            similar_incidents=[],
            knowledge_snippets=[],
            rca_output=None,
            evidence_context="",
            incident_id=None,
            report_markdown_path=None,
            report_pdf_path=None,
            messages=[],
            errors=[],
            current_step="start",
            completed=False,
        )

    def run_analysis(
        self,
        namespace: str = "default",
        resource_name: str | None = None,
        resource_type: str = "auto",
        model_name: str | None = None,
    ) -> AgentState:
        """
        Run the full RCA workflow synchronously.

        Returns the final AgentState after all nodes have completed.
        """
        initial_state = self._build_initial_state(
            namespace=namespace,
            resource_name=resource_name,
            resource_type=resource_type,
            model_name=model_name,
        )
        logger.info(
            "Starting RCA analysis | ns=%s | resource=%s | model=%s",
            namespace,
            resource_name or "auto",
            initial_state["model_name"],
        )
        final_state: AgentState = self._graph.invoke(initial_state)
        logger.info(
            "RCA analysis complete | incident_id=%s | errors=%d",
            final_state.get("incident_id"),
            len(final_state.get("errors", [])),
        )
        return final_state

    def stream_analysis(
        self,
        namespace: str = "default",
        resource_name: str | None = None,
        resource_type: str = "auto",
        model_name: str | None = None,
    ) -> Generator[AgentState, None, None]:
        """
        Stream intermediate states from the LangGraph.

        Each yielded value is a partial AgentState dict after one node
        has completed — suitable for feeding into a Streamlit progress UI.
        """
        initial_state = self._build_initial_state(
            namespace=namespace,
            resource_name=resource_name,
            resource_type=resource_type,
            model_name=model_name,
        )
        logger.info(
            "Streaming RCA analysis | ns=%s | model=%s",
            namespace,
            initial_state["model_name"],
        )
        for step_output in self._graph.stream(initial_state):
            # LangGraph stream yields {node_name: partial_state} dicts
            for node_name, partial_state in step_output.items():
                yield {**partial_state, "_node": node_name}
