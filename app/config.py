"""
Centralised application configuration.

All settings are loaded once at import time from environment variables
(and from a .env file when present).  Every other module imports the
`settings` singleton — no raw ``os.getenv`` calls outside this file.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_", extra="ignore")

    base_url: str = "http://localhost:11434"
    default_model: str = "llama3.1:8b"
    timeout: int = 120
    temperature: float = 0.1


class KubernetesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="K8S_", extra="ignore")

    config_type: Literal["in_cluster", "kubeconfig", "auto"] = "auto"
    kubeconfig_path: str = "~/.kube/config"
    context: str | None = None
    default_namespace: str = "default"
    log_tail_lines: int = 200


class ChromaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHROMA_", extra="ignore")

    persist_dir: str = "./data/chroma"
    collection_incidents: str = "rca_incidents"
    collection_knowledge: str = "rca_knowledge"
    embedding_model: str = "all-MiniLM-L6-v2"
    top_k: int = 5


class SQLiteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SQLITE_", extra="ignore")

    db_path: str = "./data/sqlite/rca_incidents.db"

    @property
    def db_url(self) -> str:
        path = Path(self.db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path}"

    @property
    def db_url_sync(self) -> str:
        path = Path(self.db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"


class PrometheusSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROMETHEUS_", extra="ignore")

    url: str = "http://localhost:9090"
    timeout: int = 30


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:8501", "http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


class ReportSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPORTS_", extra="ignore")

    output_dir: str = "./data/reports"
    company_name: str = "Platform Engineering"

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


class Settings(BaseSettings):
    """Root settings object — composed from sub-settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "RCA-Agent"
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    debug: bool = False

    # Sub-settings are populated directly from env vars via their own prefixes
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    kubernetes: KubernetesSettings = Field(default_factory=KubernetesSettings)
    chroma: ChromaSettings = Field(default_factory=ChromaSettings)
    sqlite: SQLiteSettings = Field(default_factory=SQLiteSettings)
    prometheus: PrometheusSettings = Field(default_factory=PrometheusSettings)
    api: APISettings = Field(default_factory=APISettings)
    reports: ReportSettings = Field(default_factory=ReportSettings)

    def configure_logging(self) -> None:
        logging.basicConfig(
            level=self.log_level.upper(),
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        # Suppress noisy third-party loggers
        for noisy in ("httpx", "httpcore", "chromadb", "urllib3", "kubernetes"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.configure_logging()
    logger.info("Settings loaded | env=%s | model=%s", s.app_env, s.ollama.default_model)
    return s


# Module-level singleton — preferred import target
settings: Settings = get_settings()
