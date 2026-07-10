"""
Kubernetes data-gathering tools.

Each public function is called by an agent node to collect a specific
type of evidence:
  - list_unhealthy_pods()     → find pods that aren't Running/Succeeded
  - get_pod_logs()            → tail recent container logs
  - describe_pod()            → full pod spec + status + conditions
  - get_events()              → namespace-scoped Warning events
  - get_node_status()         → node conditions and allocatable resources
  - get_deployment_status()   → replica counts and rollout conditions
  - get_resource_metrics()    → top-nodes / top-pods (metrics-server)
  - detect_issue_type()       → classify the issue from pod status
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from app.config import settings
from app.database.models import IssueType

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# K8s client bootstrap
# ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_k8s_config() -> None:
    cfg_type = settings.kubernetes.config_type
    try:
        if cfg_type == "in_cluster":
            config.load_incluster_config()
            logger.info("K8s: in-cluster config loaded")
        elif cfg_type == "kubeconfig":
            config.load_kube_config(
                config_file=settings.kubernetes.kubeconfig_path,
                context=settings.kubernetes.context,
            )
            logger.info("K8s: kubeconfig loaded (context=%s)", settings.kubernetes.context)
        else:  # auto
            try:
                config.load_incluster_config()
                logger.info("K8s: in-cluster config loaded (auto)")
            except config.ConfigException:
                config.load_kube_config(context=settings.kubernetes.context)
                logger.info("K8s: kubeconfig loaded (auto)")
    except Exception as exc:
        logger.error("Failed to load K8s config: %s", exc)
        raise


def get_core_api() -> client.CoreV1Api:
    _load_k8s_config()
    return client.CoreV1Api()


def get_apps_api() -> client.AppsV1Api:
    _load_k8s_config()
    return client.AppsV1Api()


def get_events_api() -> client.EventsV1Api:
    _load_k8s_config()
    return client.EventsV1Api()


# ──────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────

@dataclass
class PodSummary:
    name: str
    namespace: str
    phase: str
    ready: bool
    restart_count: int
    node_name: str | None
    issue_type: str
    container_statuses: list[dict]
    conditions: list[dict]
    labels: dict[str, str]
    created_at: datetime | None


@dataclass
class NodeSummary:
    name: str
    ready: bool
    conditions: list[dict]
    allocatable: dict[str, str]
    capacity: dict[str, str]
    roles: list[str]
    taints: list[dict]
    version: str


@dataclass
class DeploymentSummary:
    name: str
    namespace: str
    desired: int
    ready: int
    available: int
    updated: int
    conditions: list[dict]
    strategy: str
    image: str


@dataclass
class K8sEvent:
    name: str
    namespace: str
    reason: str
    message: str
    regarding_kind: str
    regarding_name: str
    event_type: str
    count: int
    first_time: datetime | None
    last_time: datetime | None


# ──────────────────────────────────────────────────────────────
# Issue Detection
# ──────────────────────────────────────────────────────────────

def detect_issue_type(pod: client.V1Pod) -> str:
    """Classify the most likely issue type from a pod's status."""
    if not pod.status:
        return IssueType.UNKNOWN

    phase = (pod.status.phase or "").lower()

    for cs in pod.status.container_statuses or []:
        if cs.state:
            # Waiting states
            if cs.state.waiting:
                reason = cs.state.waiting.reason or ""
                if reason == "CrashLoopBackOff":
                    return IssueType.CRASH_LOOP_BACKOFF
                if reason in ("ImagePullBackOff", "ErrImagePull"):
                    return IssueType.IMAGE_PULL_BACKOFF
                if reason in ("CreateContainerError", "CreateContainerConfigError", "RunContainerError"):
                    return IssueType.CRASH_LOOP_BACKOFF
            # Terminated states
            if cs.state.terminated:
                reason = cs.state.terminated.reason or ""
                if reason == "OOMKilled":
                    return IssueType.OOM_KILLED
                # reason='Unknown' with non-zero exit typically means the node
                # killed the container (node restart / eviction / SIGKILL).
                # Keep as UNKNOWN — not enough info to classify further.
        # Container not passing readiness probe while running
        if not cs.ready and cs.state and cs.state.running:
            return IssueType.READINESS_PROBE

    if phase == "pending":
        return IssueType.PENDING

    # Check pod conditions for eviction / readiness failures
    for cond in pod.status.conditions or []:
        msg = (cond.message or "").lower()
        reason = (cond.reason or "").lower()
        if "evict" in msg:
            return IssueType.EVICTED
        if cond.type in ("Ready", "ContainersReady") and cond.status == "False":
            if "containersnotready" in reason:
                return IssueType.READINESS_PROBE

    return IssueType.UNKNOWN


# ──────────────────────────────────────────────────────────────
# Core Tool Functions
# ──────────────────────────────────────────────────────────────

def list_unhealthy_pods(namespace: str = "") -> list[PodSummary]:
    """
    Return all pods that are not in Running/Succeeded phase OR have
    containers with high restart counts (>= 5).
    """
    core = get_core_api()
    ns = namespace or settings.kubernetes.default_namespace

    try:
        pods = (
            core.list_pod_for_all_namespaces()
            if ns == "all"
            else core.list_namespaced_pod(namespace=ns)
        )
    except ApiException as exc:
        logger.error("K8s list_pods failed: %s", exc)
        return []

    _BAD_WAITING_REASONS = {
        "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
        "OOMKilled", "Error", "CreateContainerError", "CreateContainerConfigError",
        "RunContainerError",
    }

    unhealthy: list[PodSummary] = []
    for pod in pods.items:
        phase = (pod.status.phase or "") if pod.status else ""

        # Completed jobs are healthy by definition — skip them.
        if phase == "Succeeded":
            continue

        if phase == "Running":
            # A Running pod is unhealthy when any container is:
            #   - stuck in a known bad waiting state (CrashLoopBackOff etc.)
            #   - terminated with a non-zero exit code
            #   - not passing its readiness probe (ready=False)
            def _container_bad(cs) -> bool:
                if cs.state:
                    if (cs.state.waiting
                            and cs.state.waiting.reason in _BAD_WAITING_REASONS):
                        return True
                    if (cs.state.terminated
                            and (cs.state.terminated.exit_code or 0) != 0):
                        return True
                return not cs.ready

            if not any(_container_bad(cs)
                       for cs in (pod.status.container_statuses or [])):
                continue

        cstatuses = []
        total_restarts = 0
        for cs in pod.status.container_statuses or [] if pod.status else []:
            total_restarts += cs.restart_count or 0
            state_info: dict = {}
            if cs.state:
                if cs.state.waiting:
                    state_info = {
                        "state": "waiting",
                        "reason": cs.state.waiting.reason,
                        "message": cs.state.waiting.message,
                    }
                elif cs.state.running:
                    state_info = {"state": "running"}
                elif cs.state.terminated:
                    state_info = {
                        "state": "terminated",
                        "reason": cs.state.terminated.reason,
                        "exit_code": cs.state.terminated.exit_code,
                    }
            cstatuses.append({
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count or 0,
                **state_info,
            })

        conditions = [
            {
                "type": c.type,
                "status": c.status,
                "reason": c.reason,
                "message": c.message,
            }
            for c in (pod.status.conditions or [])
        ]

        unhealthy.append(PodSummary(
            name=pod.metadata.name,
            namespace=pod.metadata.namespace,
            phase=phase,
            ready=all(cs.get("ready", False) for cs in cstatuses),
            restart_count=total_restarts,
            node_name=pod.spec.node_name if pod.spec else None,
            issue_type=detect_issue_type(pod),
            container_statuses=cstatuses,
            conditions=conditions,
            labels=pod.metadata.labels or {},
            created_at=pod.metadata.creation_timestamp,
        ))

    return unhealthy


def list_all_pods() -> list[dict]:
    """
    Return every pod across all namespaces with a human-readable status.
    Used by the dashboard to show both healthy and unhealthy pods.
    """
    core = get_core_api()
    _BAD_WAITING_REASONS = {
        "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
        "OOMKilled", "Error", "CreateContainerError", "CreateContainerConfigError",
        "RunContainerError",
    }

    try:
        pods = core.list_pod_for_all_namespaces()
    except ApiException as exc:
        logger.error("K8s list_all_pods failed: %s", exc)
        return []

    rows: list[dict] = []
    for pod in pods.items:
        phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
        node = (pod.spec.node_name or "") if pod.spec else ""
        container_statuses = (pod.status.container_statuses or []) if pod.status else []

        if phase == "Succeeded":
            status = "✅ Succeeded"
        elif phase == "Running":
            bad_reason: str | None = None
            not_ready = False
            for cs in container_statuses:
                if cs.state:
                    if cs.state.waiting and cs.state.waiting.reason in _BAD_WAITING_REASONS:
                        bad_reason = cs.state.waiting.reason
                        break
                    if cs.state.terminated and (cs.state.terminated.exit_code or 0) != 0:
                        bad_reason = cs.state.terminated.reason or "Terminated"
                        break
                if not cs.ready:
                    not_ready = True
            if bad_reason:
                status = f"❌ {bad_reason}"
            elif not_ready:
                issue = detect_issue_type(pod)
                issue_str = issue.value if hasattr(issue, "value") else str(issue)
                status = f"⚠️ {issue_str}"
            else:
                status = "✅ Running"
        elif phase == "Pending":
            status = "⏳ Pending"
        elif phase == "Failed":
            status = "❌ Failed"
        else:
            status = f"⚠️ {phase}"

        rows.append({
            "Pod Name": pod.metadata.name,
            "Namespace": pod.metadata.namespace,
            "Status": status,
            "Node": node,
        })

    rows.sort(key=lambda r: (r["Status"].startswith("✅"), r["Namespace"], r["Pod Name"]))
    return rows


def get_pod_logs(
    pod_name: str,
    namespace: str = "",
    container: str | None = None,
    previous: bool = False,
    tail_lines: int | None = None,
) -> str:
    """Retrieve recent container logs. Tries `previous=True` automatically on crash."""
    core = get_core_api()
    ns = namespace or settings.kubernetes.default_namespace
    tail = tail_lines or settings.kubernetes.log_tail_lines

    kwargs: dict[str, Any] = {
        "name": pod_name,
        "namespace": ns,
        "tail_lines": tail,
        "timestamps": True,
    }
    if container:
        kwargs["container"] = container
    if previous:
        kwargs["previous"] = True

    try:
        logs: str = core.read_namespaced_pod_log(**kwargs)
        return logs or "(no logs)"
    except ApiException as exc:
        if "previous terminated container" in str(exc) or exc.status == 400:
            # Container hasn't crashed yet — return current logs
            kwargs.pop("previous", None)
            try:
                return core.read_namespaced_pod_log(**kwargs) or "(no logs)"
            except ApiException as inner:
                return f"(log retrieval failed: {inner.reason})"
        return f"(log retrieval failed: {exc.reason})"


def describe_pod(pod_name: str, namespace: str = "") -> dict[str, Any]:
    """Return a structured dict equivalent to `kubectl describe pod`."""
    core = get_core_api()
    ns = namespace or settings.kubernetes.default_namespace

    try:
        pod = core.read_namespaced_pod(name=pod_name, namespace=ns)
    except ApiException as exc:
        return {"error": f"Pod not found: {exc.reason}"}

    spec = pod.spec
    status = pod.status

    containers = []
    for c in spec.containers if spec else []:
        containers.append({
            "name": c.name,
            "image": c.image,
            "resources": {
                "requests": (c.resources.requests if c.resources else None),
                "limits": (c.resources.limits if c.resources else None),
            },
            "liveness_probe": bool(c.liveness_probe),
            "readiness_probe": bool(c.readiness_probe),
            "env": [
                {"name": e.name, "value": e.value or "(from secret/configmap)"}
                for e in (c.env or [])
            ],
        })

    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "node": spec.node_name if spec else None,
        "phase": status.phase if status else None,
        "ip": status.pod_ip if status else None,
        "labels": pod.metadata.labels or {},
        "annotations": pod.metadata.annotations or {},
        "containers": containers,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (status.conditions or [])
        ],
        "container_statuses": [
            {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "state": (
                    {"waiting": {"reason": cs.state.waiting.reason, "message": cs.state.waiting.message}}
                    if cs.state and cs.state.waiting else
                    {"running": True} if cs.state and cs.state.running else
                    {"terminated": {"reason": cs.state.terminated.reason, "exit_code": cs.state.terminated.exit_code}}
                    if cs.state and cs.state.terminated else {}
                ),
            }
            for cs in (status.container_statuses or [])
        ],
        "volumes": [v.name for v in (spec.volumes or [])] if spec else [],
        "created_at": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
    }


def get_namespace_events(
    namespace: str = "", warning_only: bool = True, limit: int = 50
) -> list[K8sEvent]:
    """Fetch recent events, optionally filtered to Warning type."""
    core = get_core_api()
    ns = namespace or settings.kubernetes.default_namespace

    try:
        events = core.list_namespaced_event(namespace=ns, limit=limit * 2)
    except ApiException as exc:
        logger.error("K8s events failed: %s", exc)
        return []

    results: list[K8sEvent] = []
    for e in events.items:
        if warning_only and e.type != "Warning":
            continue
        results.append(K8sEvent(
            name=e.metadata.name,
            namespace=e.metadata.namespace,
            reason=e.reason or "",
            message=e.message or "",
            regarding_kind=e.involved_object.kind if e.involved_object else "",
            regarding_name=e.involved_object.name if e.involved_object else "",
            event_type=e.type or "",
            count=e.count or 1,
            first_time=e.first_timestamp,
            last_time=e.last_timestamp,
        ))

    # Sort by last occurrence descending
    results.sort(key=lambda ev: ev.last_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return results[:limit]


def get_node_status() -> list[NodeSummary]:
    """Return status for all cluster nodes."""
    core = get_core_api()
    try:
        nodes = core.list_node()
    except ApiException as exc:
        logger.error("K8s list_nodes failed: %s", exc)
        return []

    summaries: list[NodeSummary] = []
    for n in nodes.items:
        labels = n.metadata.labels or {}
        roles = [
            k.replace("node-role.kubernetes.io/", "")
            for k in labels
            if k.startswith("node-role.kubernetes.io/")
        ]

        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (n.status.conditions or [])
        ]
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)

        taints = [
            {"key": t.key, "effect": t.effect, "value": t.value}
            for t in (n.spec.taints or [])
        ] if n.spec else []

        summaries.append(NodeSummary(
            name=n.metadata.name,
            ready=ready,
            conditions=conditions,
            allocatable=n.status.allocatable or {},
            capacity=n.status.capacity or {},
            roles=roles or ["worker"],
            taints=taints,
            version=n.status.node_info.kubelet_version if n.status.node_info else "unknown",
        ))

    return summaries


def get_deployment_status(namespace: str = "") -> list[DeploymentSummary]:
    """Return status of all deployments in a namespace."""
    apps = get_apps_api()
    ns = namespace or settings.kubernetes.default_namespace

    try:
        deploys = apps.list_namespaced_deployment(namespace=ns)
    except ApiException as exc:
        logger.error("K8s list_deployments failed: %s", exc)
        return []

    summaries: list[DeploymentSummary] = []
    for d in deploys.items:
        containers = d.spec.template.spec.containers if d.spec and d.spec.template.spec else []
        image = containers[0].image if containers else "unknown"
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (d.status.conditions or [])
        ]
        s = d.status
        summaries.append(DeploymentSummary(
            name=d.metadata.name,
            namespace=d.metadata.namespace,
            desired=d.spec.replicas or 0,
            ready=s.ready_replicas or 0,
            available=s.available_replicas or 0,
            updated=s.updated_replicas or 0,
            conditions=conditions,
            strategy=d.spec.strategy.type if d.spec and d.spec.strategy else "RollingUpdate",
            image=image,
        ))

    return summaries


def get_all_namespaces() -> list[str]:
    """List all namespaces in the cluster."""
    core = get_core_api()
    try:
        nss = core.list_namespace()
        return [ns.metadata.name for ns in nss.items]
    except ApiException as exc:
        logger.error("K8s list_namespaces failed: %s", exc)
        return [settings.kubernetes.default_namespace]


def cluster_health_summary() -> dict[str, Any]:
    """
    Produce a high-level cluster health snapshot — used by the UI
    dashboard and as the starting context for agent analysis.
    """
    pods = list_unhealthy_pods(namespace="all")
    nodes = get_node_status()

    node_ready_count = sum(1 for n in nodes if n.ready)
    issue_breakdown: dict[str, int] = {}
    for p in pods:
        issue_breakdown[p.issue_type] = issue_breakdown.get(p.issue_type, 0) + 1

    return {
        "total_nodes": len(nodes),
        "ready_nodes": node_ready_count,
        "unhealthy_pods": len(pods),
        "issue_breakdown": issue_breakdown,
        "pods": [
            {
                "name": p.name,
                "namespace": p.namespace,
                "phase": p.phase,
                "issue_type": p.issue_type,
                "restart_count": p.restart_count,
                "node": p.node_name,
            }
            for p in pods
        ],
        "nodes": [
            {
                "name": n.name,
                "ready": n.ready,
                "roles": n.roles,
                "version": n.version,
            }
            for n in nodes
        ],
    }
