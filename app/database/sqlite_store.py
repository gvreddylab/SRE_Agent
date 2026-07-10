"""
SQLite persistence layer for RCA incidents.

Uses SQLAlchemy async engine so that FastAPI endpoints never block the
event loop.  A synchronous variant is exposed for the LangGraph agent
(which runs outside asyncio context in Streamlit).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database.models import (
    Base,
    EvidenceItem,
    Incident,
    IncidentStatus,
    IssueType,
    RCAReport,
    Severity,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Engine singletons
# ──────────────────────────────────────────────────────────────

_async_engine = create_async_engine(
    settings.sqlite.db_url,
    echo=settings.debug,
    pool_pre_ping=True,
)
_sync_engine = create_engine(
    settings.sqlite.db_url_sync,
    echo=settings.debug,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(_async_engine, expire_on_commit=False)
SyncSessionLocal = sessionmaker(_sync_engine, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables (idempotent)."""
    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("SQLite tables initialised at %s", settings.sqlite.db_path)


def init_db_sync() -> None:
    Base.metadata.create_all(_sync_engine)
    logger.info("SQLite tables initialised (sync) at %s", settings.sqlite.db_path)


# ──────────────────────────────────────────────────────────────
# Context managers
# ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ──────────────────────────────────────────────────────────────
# Async CRUD helpers (used by FastAPI)
# ──────────────────────────────────────────────────────────────

class IncidentStore:
    """High-level async CRUD for incidents and RCA reports."""

    # ── Incidents ──────────────────────────────────────────────

    async def create_incident(self, **kwargs) -> Incident:
        async with get_async_session() as session:
            incident = Incident(**kwargs)
            session.add(incident)
            await session.flush()
            await session.refresh(incident)
            return incident

    async def get_incident(self, incident_id: str) -> Incident | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(Incident).where(Incident.id == incident_id)
            )
            return result.scalar_one_or_none()

    async def list_incidents(
        self,
        namespace: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Incident]:
        async with get_async_session() as session:
            stmt = select(Incident).order_by(desc(Incident.created_at))
            if namespace:
                stmt = stmt.where(Incident.namespace == namespace)
            if status:
                stmt = stmt.where(Incident.status == status)
            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_incident_status(
        self, incident_id: str, status: IncidentStatus
    ) -> Incident | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(Incident).where(Incident.id == incident_id)
            )
            incident = result.scalar_one_or_none()
            if incident:
                incident.status = status
                if status == IncidentStatus.RESOLVED:
                    incident.resolved_at = datetime.now(timezone.utc)
            return incident

    async def count_incidents(self, namespace: str | None = None) -> int:
        async with get_async_session() as session:
            stmt = select(func.count()).select_from(Incident)
            if namespace:
                stmt = stmt.where(Incident.namespace == namespace)
            result = await session.execute(stmt)
            return result.scalar_one()

    # ── RCA Reports ───────────────────────────────────────────

    async def save_rca_report(self, incident_id: str, **kwargs) -> RCAReport:
        async with get_async_session() as session:
            report = RCAReport(incident_id=incident_id, **kwargs)
            session.add(report)
            await session.flush()
            await session.refresh(report)
            return report

    async def get_rca_report(self, incident_id: str) -> RCAReport | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(RCAReport).where(RCAReport.incident_id == incident_id)
            )
            return result.scalar_one_or_none()

    # ── Evidence ──────────────────────────────────────────────

    async def add_evidence(
        self, incident_id: str, source: str, content: str
    ) -> EvidenceItem:
        async with get_async_session() as session:
            item = EvidenceItem(
                incident_id=incident_id, source=source, content=content
            )
            session.add(item)
            await session.flush()
            return item


# ──────────────────────────────────────────────────────────────
# Sync CRUD (used by the LangGraph agent nodes)
# ──────────────────────────────────────────────────────────────

class SyncIncidentStore:
    """Synchronous CRUD — safe to call from LangGraph nodes."""

    def create_incident(self, **kwargs) -> Incident:
        with get_sync_session() as session:
            incident = Incident(**kwargs)
            session.add(incident)
            session.flush()
            session.refresh(incident)
            return incident

    def save_rca_report(self, incident_id: str, **kwargs) -> RCAReport:
        with get_sync_session() as session:
            report = RCAReport(incident_id=incident_id, **kwargs)
            session.add(report)
            session.flush()
            session.refresh(report)
            return report

    def add_evidence(self, incident_id: str, source: str, content: str) -> EvidenceItem:
        with get_sync_session() as session:
            item = EvidenceItem(
                incident_id=incident_id, source=source, content=content
            )
            session.add(item)
            session.flush()
            return item

    def update_incident_status(
        self, incident_id: str, status: IncidentStatus
    ) -> None:
        with get_sync_session() as session:
            result = session.execute(
                select(Incident).where(Incident.id == incident_id)
            )
            incident = result.scalar_one_or_none()
            if incident:
                incident.status = status
                if status == IncidentStatus.RESOLVED:
                    incident.resolved_at = datetime.now(timezone.utc)

    def list_recent(self, limit: int = 10) -> list[Incident]:
        with get_sync_session() as session:
            result = session.execute(
                select(Incident).order_by(desc(Incident.created_at)).limit(limit)
            )
            return list(result.scalars().all())

    def get_report(self, incident_id: str) -> RCAReport | None:
        with get_sync_session() as session:
            result = session.execute(
                select(RCAReport).where(RCAReport.incident_id == incident_id)
            )
            return result.scalar_one_or_none()


# Module-level singletons
incident_store = IncidentStore()
sync_incident_store = SyncIncidentStore()
