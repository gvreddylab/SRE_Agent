"""
SQLAlchemy ORM models for the RCA incident store.

Every completed RCA analysis is persisted here so that:
  - Users can browse & search past incidents via the UI.
  - The agent can load prior reports to avoid duplicate analysis.
  - Prometheus metrics can expose incident counts / MTTR.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueType(str, enum.Enum):
    CRASH_LOOP_BACKOFF = "CrashLoopBackOff"
    OOM_KILLED = "OOMKilled"
    IMAGE_PULL_BACKOFF = "ImagePullBackOff"
    PENDING = "Pending"
    EVICTED = "Evicted"
    NODE_NOT_READY = "NodeNotReady"
    RESOURCE_QUOTA = "ResourceQuota"
    LIVENESS_PROBE = "LivenessProbe"
    READINESS_PROBE = "ReadinessProbe"
    DEPLOYMENT_STALLED = "DeploymentStalled"
    NETWORK = "Network"
    STORAGE = "Storage"
    UNKNOWN = "Unknown"


class Incident(Base):
    """Top-level incident record."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    cluster: Mapped[str] = mapped_column(String(256), nullable=False, default="k3d-rca-cluster")
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)  # Pod / Deployment / Node
    resource_name: Mapped[str] = mapped_column(String(256), nullable=False)
    issue_type: Mapped[str] = mapped_column(
        Enum(IssueType), nullable=False, default=IssueType.UNKNOWN
    )
    severity: Mapped[str] = mapped_column(
        Enum(Severity), nullable=False, default=Severity.MEDIUM
    )
    status: Mapped[str] = mapped_column(
        Enum(IncidentStatus), nullable=False, default=IncidentStatus.OPEN
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)
    model_used: Mapped[str] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)

    # One-to-one RCA report
    rca_report: Mapped[RCAReport | None] = relationship(
        "RCAReport", back_populates="incident", uselist=False, cascade="all, delete-orphan"
    )
    # Raw evidence collected during analysis
    evidence_items: Mapped[list[EvidenceItem]] = relationship(
        "EvidenceItem", back_populates="incident", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Incident id={self.id} ns={self.namespace} type={self.issue_type}>"


class RCAReport(Base):
    """Structured RCA report produced by the LLM agent."""

    __tablename__ = "rca_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False, unique=True)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    contributing_factors: Mapped[str] = mapped_column(Text, nullable=True)
    impact_assessment: Mapped[str] = mapped_column(Text, nullable=True)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=True)
    recommended_fix: Mapped[str] = mapped_column(Text, nullable=False)
    validation_steps: Mapped[str] = mapped_column(Text, nullable=True)
    preventive_actions: Mapped[str] = mapped_column(Text, nullable=True)
    similar_incidents: Mapped[dict] = mapped_column(JSON, default=list)
    raw_llm_output: Mapped[str] = mapped_column(Text, nullable=True)
    markdown_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    incident: Mapped[Incident] = relationship("Incident", back_populates="rca_report")

    def __repr__(self) -> str:
        return f"<RCAReport id={self.id} incident={self.incident_id}>"


class EvidenceItem(Base):
    """Individual piece of evidence collected during K8s analysis."""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # logs | events | metrics | describe
    content: Mapped[str] = mapped_column(Text, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    incident: Mapped[Incident] = relationship("Incident", back_populates="evidence_items")
