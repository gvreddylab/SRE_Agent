"""
/incidents endpoints — CRUD for stored incident history.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.schemas import IncidentResponse, PaginatedIncidents
from app.database.sqlite_store import incident_store

router = APIRouter(prefix="/incidents", tags=["incidents"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PaginatedIncidents)
async def list_incidents(
    namespace: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedIncidents:
    """List incidents with optional filtering."""
    items = await incident_store.list_incidents(
        namespace=namespace, status=status, limit=limit, offset=offset
    )
    total = await incident_store.count_incidents(namespace=namespace)
    return PaginatedIncidents(
        total=total,
        items=[IncidentResponse.model_validate(i) for i in items],
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(incident_id: str) -> IncidentResponse:
    incident = await incident_store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentResponse.model_validate(incident)


@router.get("/{incident_id}/report/markdown")
async def download_markdown_report(incident_id: str) -> FileResponse:
    report = await incident_store.get_rca_report(incident_id)
    if not report or not report.markdown_path:
        raise HTTPException(status_code=404, detail="Markdown report not found")
    path = Path(report.markdown_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report file missing from disk")
    return FileResponse(path, media_type="text/markdown", filename=path.name)


@router.get("/{incident_id}/report/pdf")
async def download_pdf_report(incident_id: str) -> FileResponse:
    report = await incident_store.get_rca_report(incident_id)
    if not report or not report.pdf_path:
        raise HTTPException(status_code=404, detail="PDF report not found")
    path = Path(report.pdf_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report file missing from disk")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.patch("/{incident_id}/status")
async def update_status(incident_id: str, status: str) -> dict:
    from app.database.models import IncidentStatus
    try:
        new_status = IncidentStatus(status)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Allowed: {[s.value for s in IncidentStatus]}",
        )
    updated = await incident_store.update_incident_status(incident_id, new_status)
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"id": incident_id, "status": status}
