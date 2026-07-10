"""
/analysis endpoints — trigger RCA analysis and stream results.
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.rca_agent import RCAAgent
from app.api.schemas import AnalysisRequest, AnalysisResponse, ClusterHealthResponse, SystemStatusResponse
from app.config import settings
from app.llm.ollama_client import is_ollama_reachable, list_available_models
from app.memory.chromadb_store import chroma_store
from app.tools.kubernetes_tools import cluster_health_summary
from app.tools.metrics_tools import is_prometheus_reachable

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


@router.post("/run", response_model=AnalysisResponse)
async def run_analysis(request: AnalysisRequest) -> AnalysisResponse:
    """
    Trigger a full RCA analysis.  Runs the LangGraph agent in a thread pool
    so the async FastAPI event loop is not blocked.
    """
    loop = asyncio.get_event_loop()

    def _run():
        agent = RCAAgent()
        return agent.run_analysis(
            namespace=request.namespace,
            resource_name=request.resource_name,
            resource_type=request.resource_type,
            model_name=request.model_name,
        )

    try:
        final_state = await loop.run_in_executor(_executor, _run)
    except Exception as exc:
        logger.error("Analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    rca = final_state.get("rca_output")
    rca_resp = None
    if rca:
        rca_resp = rca.model_dump()

    return AnalysisResponse(
        incident_id=final_state.get("incident_id") or "",
        messages=final_state.get("messages", []),
        errors=final_state.get("errors", []),
        rca_report=rca_resp,
        report_markdown_path=final_state.get("report_markdown_path"),
        report_pdf_path=final_state.get("report_pdf_path"),
        completed=final_state.get("completed", False),
    )


@router.post("/stream")
async def stream_analysis(request: AnalysisRequest) -> StreamingResponse:
    """
    Server-Sent Events stream of agent progress.
    Each event is a JSON line: {"step": "...", "message": "...", "data": {...}}
    """
    async def event_generator():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _stream_worker():
            agent = RCAAgent()
            for partial in agent.stream_analysis(
                namespace=request.namespace,
                resource_name=request.resource_name,
                resource_type=request.resource_type,
                model_name=request.model_name,
            ):
                asyncio.run_coroutine_threadsafe(queue.put(partial), loop)
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        loop.run_in_executor(_executor, _stream_worker)

        while True:
            item = await queue.get()
            if item is None:
                break
            event = {
                "step": item.get("current_step", ""),
                "messages": item.get("messages", []),
                "errors": item.get("errors", []),
                "completed": item.get("completed", False),
            }
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/cluster-health", response_model=ClusterHealthResponse)
async def get_cluster_health() -> ClusterHealthResponse:
    """Return a quick cluster health snapshot without triggering full RCA."""
    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(_executor, cluster_health_summary)
    return ClusterHealthResponse(**summary)


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Report connectivity status of all system components."""
    loop = asyncio.get_event_loop()
    ollama_ok = await loop.run_in_executor(_executor, is_ollama_reachable)
    prom_ok = await loop.run_in_executor(_executor, is_prometheus_reachable)
    models = await loop.run_in_executor(_executor, list_available_models)
    chroma_stats = await loop.run_in_executor(_executor, chroma_store.get_collection_stats)

    return SystemStatusResponse(
        ollama_reachable=ollama_ok,
        prometheus_reachable=prom_ok,
        default_model=settings.ollama.default_model,
        available_models=models,
        chroma_stats=chroma_stats,
        kubernetes_context=settings.kubernetes.context,
    )
