"""
Prometheus metrics query tools.

Queries the Prometheus HTTP API to gather resource utilization metrics
that give the LLM quantitative evidence (CPU spike, memory ramp, etc.)
alongside the qualitative Kubernetes events and logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from requests.exceptions import ConnectionError, Timeout

from app.config import settings

logger = logging.getLogger(__name__)

PROM_URL = settings.prometheus.url
TIMEOUT = settings.prometheus.timeout


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float


@dataclass
class MetricSeries:
    metric: dict[str, str]
    values: list[MetricPoint]

    @property
    def latest(self) -> float | None:
        return self.values[-1].value if self.values else None

    @property
    def max(self) -> float | None:
        return max(v.value for v in self.values) if self.values else None

    @property
    def avg(self) -> float | None:
        if not self.values:
            return None
        return sum(v.value for v in self.values) / len(self.values)


def _query(promql: str) -> list[dict]:
    """Execute an instant Prometheus query."""
    try:
        resp = requests.get(
            f"{PROM_URL}/api/v1/query",
            params={"query": promql},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except (ConnectionError, Timeout):
        logger.warning("Prometheus not reachable at %s", PROM_URL)
        return []
    except Exception as exc:
        logger.error("Prometheus query error: %s | query=%s", exc, promql)
        return []


def _range_query(promql: str, minutes: int = 30) -> list[dict]:
    """Execute a range Prometheus query over the last N minutes."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    try:
        resp = requests.get(
            f"{PROM_URL}/api/v1/query_range",
            params={
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": "60s",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("result", [])
    except (ConnectionError, Timeout):
        logger.warning("Prometheus not reachable at %s", PROM_URL)
        return []
    except Exception as exc:
        logger.error("Prometheus range query error: %s", exc)
        return []


def _parse_series(raw: list[dict]) -> list[MetricSeries]:
    series = []
    for r in raw:
        values = [
            MetricPoint(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                value=float(val),
            )
            for ts, val in r.get("values", [])
        ]
        series.append(MetricSeries(metric=r.get("metric", {}), values=values))
    return series


# ──────────────────────────────────────────────────────────────
# Pod Metrics
# ──────────────────────────────────────────────────────────────

def get_pod_cpu_usage(pod_name: str, namespace: str, minutes: int = 30) -> dict[str, Any]:
    """CPU usage rate for a specific pod over the last N minutes."""
    query = (
        f'rate(container_cpu_usage_seconds_total{{pod="{pod_name}",'
        f'namespace="{namespace}",container!="POD"}}[5m])'
    )
    raw = _range_query(query, minutes)
    series = _parse_series(raw)
    if not series:
        return {"pod": pod_name, "namespace": namespace, "cpu_data": "unavailable"}

    result = {}
    for s in series:
        container = s.metric.get("container", "unknown")
        result[container] = {
            "latest_cores": round(s.latest or 0, 6),
            "max_cores": round(s.max or 0, 6),
            "avg_cores": round(s.avg or 0, 6),
        }
    return {"pod": pod_name, "namespace": namespace, "containers": result}


def get_pod_memory_usage(pod_name: str, namespace: str, minutes: int = 30) -> dict[str, Any]:
    """Memory (RSS) usage for a specific pod over the last N minutes."""
    query = (
        f'container_memory_rss{{pod="{pod_name}",'
        f'namespace="{namespace}",container!="POD"}}'
    )
    raw = _range_query(query, minutes)
    series = _parse_series(raw)
    if not series:
        return {"pod": pod_name, "namespace": namespace, "memory_data": "unavailable"}

    result = {}
    for s in series:
        container = s.metric.get("container", "unknown")
        result[container] = {
            "latest_mb": round((s.latest or 0) / 1024 / 1024, 2),
            "max_mb": round((s.max or 0) / 1024 / 1024, 2),
            "avg_mb": round((s.avg or 0) / 1024 / 1024, 2),
        }
    return {"pod": pod_name, "namespace": namespace, "containers": result}


def get_pod_restart_rate(namespace: str) -> list[dict[str, Any]]:
    """Detect pods with increasing restart counts over time."""
    query = (
        f'increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[1h])'
    )
    raw = _query(query)
    results = []
    for r in raw:
        val = float(r.get("value", [0, "0"])[1])
        if val > 0:
            results.append({
                "pod": r["metric"].get("pod"),
                "container": r["metric"].get("container"),
                "restarts_last_hour": round(val, 1),
            })
    results.sort(key=lambda x: x["restarts_last_hour"], reverse=True)
    return results


# ──────────────────────────────────────────────────────────────
# Node Metrics
# ──────────────────────────────────────────────────────────────

def get_node_cpu_usage() -> list[dict[str, Any]]:
    """Current CPU utilization percent per node."""
    query = '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    raw = _query(query)
    return [
        {
            "node": r["metric"].get("instance", "unknown"),
            "cpu_percent": round(float(r["value"][1]), 2),
        }
        for r in raw
    ]


def get_node_memory_usage() -> list[dict[str, Any]]:
    """Current memory utilization percent per node."""
    query = (
        '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
    )
    raw = _query(query)
    return [
        {
            "node": r["metric"].get("instance", "unknown"),
            "memory_percent": round(float(r["value"][1]), 2),
        }
        for r in raw
    ]


def get_node_disk_usage() -> list[dict[str, Any]]:
    """Disk usage percent per node."""
    query = (
        '100 - (node_filesystem_avail_bytes{mountpoint="/"} / '
        'node_filesystem_size_bytes{mountpoint="/"} * 100)'
    )
    raw = _query(query)
    return [
        {
            "node": r["metric"].get("instance", "unknown"),
            "disk_percent": round(float(r["value"][1]), 2),
        }
        for r in raw
    ]


# ──────────────────────────────────────────────────────────────
# Cluster-wide OOM / Error Rates
# ──────────────────────────────────────────────────────────────

def get_oom_kills(namespace: str = "") -> list[dict[str, Any]]:
    """Containers that were OOM-killed in the past hour."""
    ns_filter = f',namespace="{namespace}"' if namespace else ""
    query = (
        f'kube_pod_container_status_last_terminated_reason{{'
        f'reason="OOMKilled"{ns_filter}}} == 1'
    )
    raw = _query(query)
    return [
        {
            "pod": r["metric"].get("pod"),
            "container": r["metric"].get("container"),
            "namespace": r["metric"].get("namespace"),
        }
        for r in raw
    ]


def get_metrics_summary(namespace: str, pod_name: str | None = None) -> dict[str, Any]:
    """
    Aggregate all metrics into a single dict for the LLM context.
    Used when the agent is building its evidence package.
    """
    summary: dict[str, Any] = {
        "namespace": namespace,
        "prometheus_url": PROM_URL,
    }

    if pod_name:
        summary["pod_cpu"] = get_pod_cpu_usage(pod_name, namespace)
        summary["pod_memory"] = get_pod_memory_usage(pod_name, namespace)

    summary["restart_rates"] = get_pod_restart_rate(namespace)
    summary["oom_kills"] = get_oom_kills(namespace)
    summary["node_cpu"] = get_node_cpu_usage()
    summary["node_memory"] = get_node_memory_usage()
    summary["node_disk"] = get_node_disk_usage()

    return summary


def is_prometheus_reachable() -> bool:
    try:
        resp = requests.get(f"{PROM_URL}/-/ready", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False
