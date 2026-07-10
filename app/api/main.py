"""
FastAPI application entry point.

Run with:
    uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.analysis import router as analysis_router
from app.api.routes.incidents import router as incidents_router
from app.config import settings
from app.database.sqlite_store import init_db

logger = logging.getLogger(__name__)

app = FastAPI(
    title="RCA Agent API",
    description="AI-powered Kubernetes Root Cause Analysis — offline, powered by Ollama",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(analysis_router, prefix=settings.api.prefix)
app.include_router(incidents_router, prefix=settings.api.prefix)


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()
    from app.memory.chromadb_store import chroma_store
    chroma_store.seed_knowledge_base()
    logger.info("RCA Agent API started | prefix=%s", settings.api.prefix)


@app.get("/", include_in_schema=False)
async def root():
    return JSONResponse({"service": "rca-agent", "version": "1.0.0", "docs": "/docs"})


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}
