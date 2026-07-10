"""
Pydantic output schemas for LLM responses.

Kept in a separate module so tests can import RCAOutput without
pulling in langchain_ollama (which requires a full install).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RCAOutput(BaseModel):
    """Structured output from the LLM RCA analysis."""

    executive_summary: str = Field(description="2-3 sentence non-technical summary")
    root_cause: str = Field(description="Technical root cause in 1-3 sentences")
    contributing_factors: str = Field(description="Additional factors that worsened the issue")
    impact_assessment: str = Field(description="What was / could be affected")
    evidence_summary: str = Field(description="How the collected evidence supports this conclusion")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Confidence 0.0-1.0")
    severity: str = Field(description="low | medium | high | critical")
    recommended_fix: str = Field(description="Step-by-step remediation instructions")
    validation_steps: str = Field(description="How to verify the fix worked")
    preventive_actions: str = Field(description="Long-term preventive measures")
    issue_type: str = Field(description="K8s issue type classification")

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "critical"}
        v_lower = v.lower()
        return v_lower if v_lower in allowed else "medium"

    @field_validator("confidence_score", mode="before")
    @classmethod
    def clamp_confidence(cls, v) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5
