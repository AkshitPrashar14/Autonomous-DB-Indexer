"""
DBAutonomy — FastAPI Application Entry Point (Phase 1)
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="DBAutonomy",
    description="Autonomous Contextual-Bandit Database Optimizer",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

from app.api import jobs, decisions, bandit as bandit_router

app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(decisions.router, prefix="/api/decisions", tags=["decisions"])
app.include_router(bandit_router.router, prefix="/api/bandit", tags=["bandit"])


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get("/health", tags=["observability"])
async def health_check():
    """
    System health check.

    Checks: Redis, primary DB, shadow DB, Ollama.
    """
    from app.core.container import get_container
    container = get_container()

    results = {}

    # Redis
    try:
        await container.redis_client.ping()
        results["redis"] = "ok"
    except Exception as e:
        results["redis"] = f"error: {e}"

    # Primary DB
    try:
        async with container.primary_engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        results["primary_db"] = "ok"
    except Exception as e:
        results["primary_db"] = f"error: {e}"

    # Shadow DB
    try:
        ok = await container.shadow_db_manager.is_healthy()
        results["shadow_db"] = "ok" if ok else "unreachable"
    except Exception as e:
        results["shadow_db"] = f"error: {e}"

    # Ollama
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            results["ollama"] = "ok" if r.status_code == 200 else f"status={r.status_code}"
    except Exception as e:
        results["ollama"] = f"error: {e}"

    # Gemini — just check API key is set
    results["gemini"] = "configured" if settings.GEMINI_API_KEY else "missing_api_key"

    overall = "ok" if all(v == "ok" or v == "configured" for v in results.values()) else "degraded"

    return {
        "status": overall,
        "service": "DBAutonomy",
        "version": "1.0.0",
        "components": results,
    }


@app.get("/", tags=["meta"])
async def root():
    return {
        "message": "DBAutonomy is running.",
        "docs": "/docs",
        "health": "/health",
    }


# ---------------------------------------------------------------------------
# Startup event: pre-create tables and load bandit state
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    from app.core.container import get_container
    container = get_container()
    try:
        await container.decision_repository.create_tables()
        logging.getLogger(__name__).info("Database tables ensured")
    except Exception as e:
        logging.getLogger(__name__).warning("Table creation failed (may already exist): %s", e)


@app.on_event("shutdown")
async def on_shutdown():
    from app.core.container import get_container
    container = get_container()
    await container.close()
