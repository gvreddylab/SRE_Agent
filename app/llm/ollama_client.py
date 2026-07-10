"""
Ollama LLM client.

Wraps langchain-ollama to provide:
  - A shared ChatOllama instance (cached per model name).
  - `generate_rca()` — the main entry point that calls the LLM with a
    rich structured prompt and returns a validated RCAOutput Pydantic model.
  - `stream_chat()` — async generator for real-time Streamlit streaming.
  - `list_models()` — queries Ollama's REST API to populate the UI dropdown.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import AsyncGenerator, Iterator

import requests
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.schemas import RCAOutput  # re-exported for backwards compat

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# System Prompt
# ──────────────────────────────────────────────────────────────

RCA_SYSTEM_PROMPT = """You are a Senior Site Reliability Engineer and Kubernetes expert with 10+ years of experience.
Your task is to perform a Root Cause Analysis (RCA) for a Kubernetes incident.

You will be given:
- Unhealthy pod/deployment status
- Container logs (with error extraction)
- Kubernetes events
- Node status
- Prometheus metrics (if available)
- Similar past incidents from the knowledge base

INSTRUCTIONS:
1. Analyse ALL provided evidence carefully.
2. Identify the PRIMARY root cause — not just symptoms.
3. Assign a confidence score (0.0-1.0) based on evidence quality.
4. Provide concrete, kubectl-based remediation steps.
5. Consider preventive infrastructure improvements.

RESPOND IN STRICT JSON FORMAT matching this schema:
{
  "executive_summary": "...",
  "root_cause": "...",
  "contributing_factors": "...",
  "impact_assessment": "...",
  "evidence_summary": "...",
  "confidence_score": 0.85,
  "severity": "high",
  "recommended_fix": "...",
  "validation_steps": "...",
  "preventive_actions": "...",
  "issue_type": "CrashLoopBackOff"
}

DO NOT add any text before or after the JSON block.
"""


# ──────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _get_llm(model_name: str) -> ChatOllama:
    return ChatOllama(
        model=model_name,
        base_url=settings.ollama.base_url,
        temperature=settings.ollama.temperature,
        timeout=settings.ollama.timeout,
        format="json",  # Ask Ollama to force JSON mode
    )


def _get_chat_llm(model_name: str) -> ChatOllama:
    """Non-JSON-forced LLM for conversational chat."""
    return ChatOllama(
        model=model_name,
        base_url=settings.ollama.base_url,
        temperature=0.3,
        timeout=settings.ollama.timeout,
    )


def _extract_json(text: str) -> str:
    """Extract the first JSON block from text that may contain prose."""
    # Try to find ```json ... ``` fenced block
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # Try bare JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def generate_rca(evidence_context: str, model_name: str | None = None) -> RCAOutput:
    """
    Call the LLM with the full evidence context and return a structured RCAOutput.
    Retries up to 3 times on transient failures.
    """
    model = model_name or settings.ollama.default_model
    llm = _get_llm(model)

    messages = [
        SystemMessage(content=RCA_SYSTEM_PROMPT),
        HumanMessage(content=f"## Evidence Package\n\n{evidence_context}"),
    ]

    logger.info("Calling Ollama model=%s for RCA generation", model)
    response = llm.invoke(messages)
    raw_text = response.content

    logger.debug("LLM raw response length=%d", len(raw_text))

    # Parse JSON output
    try:
        json_text = _extract_json(raw_text)
        data = json.loads(json_text)
        return RCAOutput(**_normalise_rca_data(data))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("JSON parse failed (%s), attempting field extraction", exc)
        # Fallback: build a partial response from what we can extract
        return RCAOutput(
            executive_summary=_extract_field(raw_text, "executive_summary") or "Analysis incomplete",
            root_cause=_extract_field(raw_text, "root_cause") or raw_text[:500],
            contributing_factors=_extract_field(raw_text, "contributing_factors") or "",
            impact_assessment=_extract_field(raw_text, "impact_assessment") or "",
            evidence_summary=_extract_field(raw_text, "evidence_summary") or "",
            confidence_score=0.4,
            severity="medium",
            recommended_fix=_extract_field(raw_text, "recommended_fix") or "Investigate further",
            validation_steps=_extract_field(raw_text, "validation_steps") or "",
            preventive_actions=_extract_field(raw_text, "preventive_actions") or "",
            issue_type="Unknown",
        )


def _to_str(value: object) -> str:
    """Flatten any LLM-returned value (str, dict, list) into a readable string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Pick the most descriptive text key, fall back to full JSON
        for key in ("description", "cause", "action", "step", "factor", "summary", "text"):
            if key in value:
                return str(value[key])
        return json.dumps(value, indent=2)
    if isinstance(value, list):
        parts = []
        for i, item in enumerate(value, 1):
            if isinstance(item, dict):
                # Grab text-like keys
                text = (
                    item.get("description") or item.get("cause") or item.get("action")
                    or item.get("step") or item.get("factor") or item.get("summary")
                    or item.get("text") or json.dumps(item)
                )
                parts.append(f"{i}. {text}")
            else:
                parts.append(f"{i}. {item}")
        return "\n".join(parts)
    return str(value)


def _normalise_rca_data(data: dict) -> dict:
    """Ensure every RCAOutput string field is actually a string."""
    str_fields = {
        "executive_summary", "root_cause", "contributing_factors",
        "impact_assessment", "evidence_summary", "recommended_fix",
        "validation_steps", "preventive_actions", "issue_type", "severity",
    }
    return {k: (_to_str(v) if k in str_fields else v) for k, v in data.items()}


def _extract_field(text: str, field: str) -> str:
    """Regex-based fallback field extractor for plain-string values."""
    pattern = rf'"{field}"\s*:\s*"([^"]*)"'
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else ""


async def stream_chat(
    message: str,
    model_name: str | None = None,
    system_prompt: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Async streaming chat for Streamlit UI.
    Yields text chunks as they arrive from Ollama.
    """
    model = model_name or settings.ollama.default_model
    llm = _get_chat_llm(model)

    msgs = []
    if system_prompt:
        msgs.append(SystemMessage(content=system_prompt))
    msgs.append(HumanMessage(content=message))

    async for chunk in llm.astream(msgs):
        if chunk.content:
            yield chunk.content


def list_available_models() -> list[str]:
    """Query Ollama API to list locally available models."""
    try:
        resp = requests.get(
            f"{settings.ollama.base_url}/api/tags",
            timeout=10,
        )
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return sorted(models) if models else [settings.ollama.default_model]
    except Exception as exc:
        logger.warning("Could not list Ollama models: %s", exc)
        return [settings.ollama.default_model]


def is_ollama_reachable() -> bool:
    try:
        resp = requests.get(f"{settings.ollama.base_url}/api/tags", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False
