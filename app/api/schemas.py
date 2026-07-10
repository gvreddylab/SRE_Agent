"""
Pydantic request/response schemas for the FastAPI routes.

Decoupled from the ORM models so the API contract is stable even if
the database schema evolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalysisRequest(BaseModel):
    namespace: str = Field(default="default", description="Kubernetes namespace to analyse")
    resource_name: str | None = Field(default=None, description="Specific pod/deployment name")
    resource_type: str = Field(default="auto", description="auto | pod | deployment | node")
    model_name: str | None = Field(default=None, description="Ollama model to use")


class RCAReportResponse(BaseModel):
    executive_summary: str
    root_cause: str
    contributing_factors: str
    impact_assessment: str
    evidence_summary: str
    confidence_score: float
    severity: str
    recommended_fix: str
    validation_steps: str
    preventive_actions: str
    issue_type: str


class IncidentResponse(BaseModel):
    id: str
    title: str
    namespace: str
    cluster: str
    resource_type: str
    resource_name: str
    issue_type: str
    severity: str
    status: str
    confidence_score: float | None
    model_used: str | None
    created_at: datetime
    resolved_at: datetime | None
    rca_report: RCAReportResponse | None = None

    model_config = {"from_attributes": True}


class AnalysisResponse(BaseModel):
    incident_id: str
    messages: list[str]
    errors: list[str]
    rca_report: RCAReportResponse | None
    report_markdown_path: str | None
    report_pdf_path: str | None
    completed: bool


class ClusterHealthResponse(BaseModel):
    total_nodes: int
    ready_nodes: int
    unhealthy_pods: int
    issue_breakdown: dict[str, int]
    pods: list[dict[str, Any]]
    nodes: list[dict[str, Any]]


class SystemStatusResponse(BaseModel):
    ollama_reachable: bool
    prometheus_reachable: bool
    default_model: str
    available_models: list[str]
    chroma_stats: dict[str, Any]
    kubernetes_context: str | None


class PaginatedIncidents(BaseModel):
    total: int
    items: list[IncidentResponse]
    limit: int
    offset: int
