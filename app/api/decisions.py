"""
DBAutonomy — Decisions API Router

GET /api/decisions        Recent optimization decisions
GET /api/decisions/stats  Aggregate statistics
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from app.core.container import get_container

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
async def get_decisions(
    table_name: Optional[str] = Query(None, description="Filter by table name"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Return recent autonomous optimization decisions.

    Each record contains:
    - Parsed query info
    - Bandit-selected candidate
    - Baseline vs experiment benchmarks
    - Reward
    - Safety decision
    - Deployment status
    """
    container = get_container()
    records = await container.decision_repository.get_recent_decisions(
        table_name=table_name, limit=limit
    )
    return {
        "count": len(records),
        "decisions": [
            {
                "record_id": str(r.record_id),
                "job_id": str(r.job_id),
                "table_name": r.candidate.table_name,
                "candidate": {
                    "columns": r.candidate.columns,
                    "index_type": r.candidate.index_type.value,
                    "fingerprint": r.candidate.fingerprint,
                    "explanation": r.candidate.explanation,
                },
                "baseline_p50_ms": r.baseline.query_p50_ms,
                "experiment_p50_ms": r.experiment.query_p50_ms,
                "improvement_pct": round(
                    (r.baseline.query_p50_ms - r.experiment.query_p50_ms)
                    / max(1.0, r.baseline.query_p50_ms) * 100,
                    2,
                ),
                "reward": round(r.reward, 4),
                "approved": r.decision.approved,
                "rejection_reason": None if r.decision.approved else r.decision.reason,
                "status": r.status.value,
                "deployed_index": r.deployed_index_name,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ],
    }


@router.get("/stats")
async def get_decision_stats():
    """Return aggregate statistics for the dashboard summary panel."""
    container = get_container()
    return await container.decision_repository.get_stats()
