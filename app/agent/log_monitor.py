"""
DBAutonomy — LogMonitor (Skeleton)

Polls pg_stat_statements for slow queries and enqueues OptimizationJobs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.interfaces import IJobQueue
from app.models.domain import OptimizationJob, SlowQueryEvent

logger = logging.getLogger(__name__)

_PG_STAT_SLOW_QUERIES_SQL = """
SELECT
    query,
    mean_exec_time AS duration_ms,
    calls,
    total_exec_time
FROM pg_stat_statements
WHERE mean_exec_time > :threshold_ms
  AND query NOT LIKE '%pg_stat_statements%'
  AND query NOT LIKE '%dbautonomy%'
ORDER BY mean_exec_time DESC
LIMIT 10
"""


class LogMonitor:
    """
    Polls pg_stat_statements every POLL_INTERVAL_S seconds.
    Emits SlowQueryEvent → OptimizationJob into the job queue.

    Phase 2 will add:
    - Deduplication (don't re-enqueue the same query pattern repeatedly)
    - Log file tailing as alternative source
    """

    def __init__(self, settings: Settings, job_queue: IJobQueue, db_conn):
        self._settings = settings
        self._queue = job_queue
        self._conn = db_conn
        self._running = False
        self._seen_queries: set[str] = set()  # simple dedup cache

    async def start(self) -> None:
        self._running = True
        logger.info(
            "LogMonitor started (threshold=%.0fms, interval=%.0fs)",
            self._settings.LOG_MONITOR_SLOW_THRESHOLD_MS,
            self._settings.LOG_MONITOR_POLL_INTERVAL_S,
        )
        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.warning("LogMonitor poll error: %s", e)
            await asyncio.sleep(self._settings.LOG_MONITOR_POLL_INTERVAL_S)

    async def stop(self) -> None:
        self._running = False

    async def _poll(self) -> None:
        """Poll pg_stat_statements and enqueue new slow queries."""
        # TODO (Phase 2): implement with real async DB connection
        logger.debug("LogMonitor polling pg_stat_statements (stub)")

    def inject_slow_query(self, raw_log: str) -> OptimizationJob:
        """
        Manually inject a slow query string as a job.
        Used by the API endpoint POST /api/jobs/inject for the demo.
        """
        event = SlowQueryEvent(raw_log=raw_log, source="manual_injection")
        return OptimizationJob(raw_log=raw_log)
