"""
ChromaDB vector store for RCA incident memory.

Two collections are maintained:
  - rca_incidents  : embeddings of completed RCA reports (summary + root cause).
  - rca_knowledge  : curated runbook snippets injected at bootstrap.

At analysis time the agent queries `rca_incidents` with the current symptom
description to surface the most relevant previous incidents and their fixes.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaClientSettings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SimilarIncident:
    incident_id: str
    title: str
    issue_type: str
    namespace: str
    root_cause: str
    recommended_fix: str
    confidence_score: float
    similarity: float  # 0-1, higher = more similar


class ChromaMemoryStore:
    """Wrapper around ChromaDB providing semantic RCA memory."""

    def __init__(self) -> None:
        self._client: chromadb.ClientAPI | None = None
        self._embedding_fn: SentenceTransformerEmbeddingFunction | None = None

    def _get_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=settings.chroma.persist_dir,
                settings=ChromaClientSettings(anonymized_telemetry=False),
            )
            logger.info("ChromaDB client initialised at %s", settings.chroma.persist_dir)
        return self._client

    def _get_embedding_fn(self) -> SentenceTransformerEmbeddingFunction:
        if self._embedding_fn is None:
            self._embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name=settings.chroma.embedding_model
            )
            logger.info("Embedding model loaded: %s", settings.chroma.embedding_model)
        return self._embedding_fn

    def _incidents_collection(self):
        return self._get_client().get_or_create_collection(
            name=settings.chroma.collection_incidents,
            embedding_function=self._get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )

    def _knowledge_collection(self):
        return self._get_client().get_or_create_collection(
            name=settings.chroma.collection_knowledge,
            embedding_function=self._get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )

    # ──────────────────────────────────────────────────────────
    # Incident Memory
    # ──────────────────────────────────────────────────────────

    def store_incident(
        self,
        incident_id: str,
        title: str,
        issue_type: str,
        namespace: str,
        root_cause: str,
        recommended_fix: str,
        confidence_score: float,
        executive_summary: str,
    ) -> None:
        """Embed and store a completed RCA report in ChromaDB."""
        # The text we embed is the richest description of the incident
        document = (
            f"Title: {title}\n"
            f"Issue Type: {issue_type}\n"
            f"Namespace: {namespace}\n"
            f"Root Cause: {root_cause}\n"
            f"Summary: {executive_summary}"
        )
        metadata = {
            "incident_id": incident_id,
            "title": title,
            "issue_type": issue_type,
            "namespace": namespace,
            "root_cause": root_cause,
            "recommended_fix": recommended_fix,
            "confidence_score": confidence_score,
        }
        col = self._incidents_collection()
        col.upsert(
            ids=[incident_id],
            documents=[document],
            metadatas=[metadata],
        )
        logger.info("Stored incident embedding: %s", incident_id)

    def find_similar_incidents(
        self,
        symptom_description: str,
        issue_type: str | None = None,
        top_k: int | None = None,
    ) -> list[SimilarIncident]:
        """Return the most semantically similar past incidents."""
        k = top_k or settings.chroma.top_k
        col = self._incidents_collection()

        try:
            count = col.count()
        except Exception:
            count = 0

        if count == 0:
            return []

        where: dict | None = None
        if issue_type:
            where = {"issue_type": issue_type}

        try:
            results = col.query(
                query_texts=[symptom_description],
                n_results=min(k, count),
                where=where,
                include=["metadatas", "distances", "documents"],
            )
        except Exception as exc:
            logger.warning("ChromaDB query failed (falling back to no filter): %s", exc)
            results = col.query(
                query_texts=[symptom_description],
                n_results=min(k, count),
                include=["metadatas", "distances", "documents"],
            )

        similar: list[SimilarIncident] = []
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for meta, dist in zip(metadatas, distances):
            # Cosine distance → similarity (0=identical, 2=opposite)
            similarity = max(0.0, 1.0 - (dist / 2.0))
            similar.append(
                SimilarIncident(
                    incident_id=meta.get("incident_id", ""),
                    title=meta.get("title", ""),
                    issue_type=meta.get("issue_type", ""),
                    namespace=meta.get("namespace", ""),
                    root_cause=meta.get("root_cause", ""),
                    recommended_fix=meta.get("recommended_fix", ""),
                    confidence_score=float(meta.get("confidence_score", 0.0)),
                    similarity=round(similarity, 4),
                )
            )

        return sorted(similar, key=lambda x: x.similarity, reverse=True)

    # ──────────────────────────────────────────────────────────
    # Runbook / Knowledge Base
    # ──────────────────────────────────────────────────────────

    def add_knowledge(self, content: str, metadata: dict | None = None) -> str:
        """Insert a runbook snippet into the knowledge collection."""
        doc_id = str(uuid.uuid4())
        col = self._knowledge_collection()
        col.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata or {}],
        )
        return doc_id

    def query_knowledge(self, query: str, top_k: int = 3) -> list[str]:
        col = self._knowledge_collection()
        try:
            count = col.count()
        except Exception:
            count = 0
        if count == 0:
            return []
        results = col.query(
            query_texts=[query],
            n_results=min(top_k, count),
            include=["documents"],
        )
        return results.get("documents", [[]])[0]

    def seed_knowledge_base(self) -> None:
        """Populate the knowledge base with common K8s runbook entries."""
        runbooks = [
            {
                "content": (
                    "CrashLoopBackOff: Pod is repeatedly crashing and restarting. "
                    "Check: kubectl logs <pod> --previous. Common causes: "
                    "application exception on startup, missing env vars/secrets, "
                    "bad command/entrypoint, failed health checks. "
                    "Fix: review logs, fix config, set correct COMMAND/ARGS."
                ),
                "meta": {"issue_type": "CrashLoopBackOff", "source": "runbook"},
            },
            {
                "content": (
                    "OOMKilled: Container was killed by the Linux OOM killer. "
                    "Memory limit was too low for the workload. "
                    "Fix: increase resources.limits.memory, or profile for memory leaks. "
                    "Use VPA (Vertical Pod Autoscaler) for automatic tuning."
                ),
                "meta": {"issue_type": "OOMKilled", "source": "runbook"},
            },
            {
                "content": (
                    "ImagePullBackOff / ErrImagePull: Kubelet cannot pull the container image. "
                    "Causes: wrong image tag, private registry without imagePullSecrets, "
                    "network issues. Fix: verify image name/tag, check imagePullSecrets, "
                    "confirm registry reachability."
                ),
                "meta": {"issue_type": "ImagePullBackOff", "source": "runbook"},
            },
            {
                "content": (
                    "Pod Pending: Pod scheduled but not running. "
                    "Causes: insufficient CPU/memory on nodes, PVC not bound, "
                    "node selector / affinity not matching, taints without tolerations. "
                    "Fix: check kubectl describe pod for events, scale cluster or relax constraints."
                ),
                "meta": {"issue_type": "Pending", "source": "runbook"},
            },
            {
                "content": (
                    "Evicted: Pod evicted due to node resource pressure (DiskPressure / MemoryPressure). "
                    "Fix: free disk space on nodes, add more nodes, set PodDisruptionBudget, "
                    "use priorityClass to protect critical pods."
                ),
                "meta": {"issue_type": "Evicted", "source": "runbook"},
            },
            {
                "content": (
                    "NodeNotReady: Node is in NotReady state. "
                    "Causes: kubelet stopped, network partition, disk full, kernel panic. "
                    "Fix: SSH to node, check systemctl status kubelet, journalctl -u kubelet, "
                    "check disk/memory/network. Drain and replace if unrecoverable."
                ),
                "meta": {"issue_type": "NodeNotReady", "source": "runbook"},
            },
        ]
        col = self._knowledge_collection()
        if col.count() >= len(runbooks):
            return  # Already seeded
        for entry in runbooks:
            doc_id = entry["content"][:36]  # deterministic short id
            col.upsert(
                ids=[doc_id],
                documents=[entry["content"]],
                metadatas=[entry["meta"]],
            )
        logger.info("Knowledge base seeded with %d runbook entries", len(runbooks))

    def get_collection_stats(self) -> dict[str, Any]:
        return {
            "incidents_count": self._incidents_collection().count(),
            "knowledge_count": self._knowledge_collection().count(),
            "persist_dir": settings.chroma.persist_dir,
            "embedding_model": settings.chroma.embedding_model,
        }


# Module-level singleton
chroma_store = ChromaMemoryStore()
