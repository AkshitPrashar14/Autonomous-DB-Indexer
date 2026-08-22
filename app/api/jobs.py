"""
DBAutonomy — Jobs API Router (Phase 2 — Redis-only, no BackgroundTasks)

POST /api/jobs/inject   → validates input → enqueues to Redis → returns immediately
GET  /api/jobs/{job_id}/status → check job status

The HTTP handler does NOT execute the pipeline.
The standalone worker (scripts/run_worker.py) consumes Redis and runs the agent.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.container import get_container
from app.models.domain import OptimizationJob

router = APIRouter()
logger = logging.getLogger(__name__)


class InjectRequest(BaseModel):
    raw_log: str
    """
    Raw PostgreSQL slow-query log line or a plain SQL statement.
    Example: "SELECT * FROM orders WHERE customer_id = 12345 AND status = 'pending'"
    """


class InjectResponse(BaseModel):
    job_id: str
    stream_entry_id: str
    message: str


@router.post("/inject", response_model=InjectResponse, status_code=202)
async def inject_slow_query(request: InjectRequest) -> InjectResponse:
    """
    Inject a slow-query event into the Redis job queue.

    The HTTP request returns immediately after placing the job on the stream.
    The autonomous pipeline is executed by the standalone AgentWorker process
    (scripts/run_worker.py), which consumes the Redis stream independently.

    Poll GET /api/decisions to observe the result.
    """
    if not request.raw_log.strip():
        raise HTTPException(status_code=422, detail="raw_log must not be empty")

    container = get_container()
    job = OptimizationJob(raw_log=request.raw_log)

    try:
        stream_entry_id = await container.job_queue.enqueue(job)
    except Exception as e:
        logger.exception("Failed to enqueue job: %s", e)
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {e}")

    logger.info(
        "Job %s enqueued → stream entry %s",
        job.job_id,
        stream_entry_id,
    )
    return InjectResponse(
        job_id=str(job.job_id),
        stream_entry_id=stream_entry_id,
        message=(
            "Job placed on Redis stream. "
            "The AgentWorker process will consume and process it. "
            "Poll GET /api/decisions to observe the result."
        ),
    )


class EvaluateInjectRequest(BaseModel):
    sql: str


@router.post("/evaluate-and-inject", response_model=InjectResponse, status_code=202)
async def evaluate_and_inject_slow_query(request: EvaluateInjectRequest) -> InjectResponse:
    """
    Executes the raw SQL query on the primary database, measures its execution time,
    and then injects it into the pipeline as a realistic log entry.
    """
    import time
    import asyncpg
    from app.core.config import get_settings
    
    if not request.sql.strip():
        raise HTTPException(status_code=422, detail="sql must not be empty")

    settings = get_settings()
    start_time = time.time()
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
        await conn.execute(request.sql)
        await conn.close()
    except Exception as e:
        logger.exception("Failed to execute query for evaluation: %s", e)
        raise HTTPException(status_code=400, detail=f"Query failed to execute: {e}")

    duration_ms = (time.time() - start_time) * 1000.0
    raw_log = f"duration: {duration_ms:.3f} ms statement: {request.sql.strip()}"
    
    # Re-use the enqueue logic
    container = get_container()
    job = OptimizationJob(raw_log=raw_log)

    try:
        stream_entry_id = await container.job_queue.enqueue(job)
    except Exception as e:
        logger.exception("Failed to enqueue evaluated job: %s", e)
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {e}")

    logger.info("Job %s enqueued with REAL duration %.2fms → stream entry %s", job.job_id, duration_ms, stream_entry_id)
    return InjectResponse(
        job_id=str(job.job_id),
        stream_entry_id=stream_entry_id,
        message="Evaluated on Primary DB and injected successfully.",
    )


@router.get("/{job_id}/status")
async def get_job_status(job_id: str):
    """
    Check the status of a job.
    Returns 'pending', 'failed', or 'unknown'.
    Completed jobs appear in GET /api/decisions instead.
    """
    container = get_container()
    redis = container.redis_client

    # Check if it ended in failure
    failed_data = await redis.get(f"dbautonomy:failed:{job_id}")
    if failed_data:
        import json
        data = json.loads(failed_data)
        return {"job_id": job_id, "status": "failed", "error": data.get("error_message")}

    return {"job_id": job_id, "status": "pending_or_processing"}
