"""Integration tests for SQLite store — uses a temp in-memory DB."""

import asyncio
import os
import tempfile
import pytest

# Point settings at a temp DB before importing the store
os.environ["SQLITE_DB_PATH"] = ":memory:"

from app.database.sqlite_store import init_db_sync, SyncIncidentStore


@pytest.fixture(scope="module")
def store():
    init_db_sync()
    return SyncIncidentStore()


def test_create_and_retrieve_incident(store):
    incident = store.create_incident(
        title="Test CrashLoop",
        namespace="test-ns",
        resource_type="Pod",
        resource_name="my-pod-xyz",
        issue_type="CrashLoopBackOff",
        severity="high",
    )
    assert incident.id is not None
    assert incident.namespace == "test-ns"


def test_save_and_get_rca_report(store):
    incident = store.create_incident(
        title="Test OOM",
        namespace="prod",
        resource_type="Pod",
        resource_name="oom-pod",
        issue_type="OOMKilled",
        severity="critical",
    )
    report = store.save_rca_report(
        incident_id=incident.id,
        executive_summary="Pod was killed by OOM.",
        root_cause="Memory limit too low.",
        contributing_factors="Memory leak in the app.",
        impact_assessment="Service degraded.",
        evidence_summary="Terminated with OOMKilled reason.",
        recommended_fix="Increase memory limit.",
        validation_steps="Check pod status.",
        preventive_actions="Use VPA.",
        similar_incidents=[],
    )
    assert report.incident_id == incident.id
    fetched = store.get_report(incident.id)
    assert fetched is not None
    assert fetched.root_cause == "Memory limit too low."


def test_list_recent(store):
    incidents = store.list_recent(limit=10)
    assert isinstance(incidents, list)
    assert len(incidents) >= 2
