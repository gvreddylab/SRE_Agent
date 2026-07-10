"""Test RCA output schema validation — no Ollama required."""

import pytest
from app.llm.schemas import RCAOutput


def test_valid_rca_output():
    rca = RCAOutput(
        executive_summary="Pod is crashing on startup.",
        root_cause="Database connection string is missing from environment.",
        contributing_factors="No readiness probe configured.",
        impact_assessment="Users cannot log in.",
        evidence_summary="Logs show ConnectionRefused errors.",
        confidence_score=0.9,
        severity="high",
        recommended_fix="Set DB_URL env var in the deployment.",
        validation_steps="kubectl rollout status deployment/app",
        preventive_actions="Add secret validation in CI/CD.",
        issue_type="CrashLoopBackOff",
    )
    assert rca.confidence_score == 0.9
    assert rca.severity == "high"


def test_confidence_clamping():
    rca = RCAOutput(
        executive_summary="x",
        root_cause="x",
        contributing_factors="x",
        impact_assessment="x",
        evidence_summary="x",
        confidence_score=1.5,  # over 1.0 — should clamp
        severity="low",
        recommended_fix="x",
        validation_steps="x",
        preventive_actions="x",
        issue_type="Unknown",
    )
    assert rca.confidence_score == 1.0


def test_severity_normalisation():
    rca = RCAOutput(
        executive_summary="x",
        root_cause="x",
        contributing_factors="x",
        impact_assessment="x",
        evidence_summary="x",
        confidence_score=0.5,
        severity="HIGH",  # uppercase — should normalise
        recommended_fix="x",
        validation_steps="x",
        preventive_actions="x",
        issue_type="OOMKilled",
    )
    assert rca.severity == "high"


def test_invalid_severity_defaults_to_medium():
    rca = RCAOutput(
        executive_summary="x",
        root_cause="x",
        contributing_factors="x",
        impact_assessment="x",
        evidence_summary="x",
        confidence_score=0.5,
        severity="catastrophic",  # not in allowed set
        recommended_fix="x",
        validation_steps="x",
        preventive_actions="x",
        issue_type="Unknown",
    )
    assert rca.severity == "medium"
