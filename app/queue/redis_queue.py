"""
DBAutonomy — RedisJobQueue

Implements IJobQueue using Redis Streams with consumer groups.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import Settings
from app.core.exceptions import JobMaxRetriesExceeded, JobQueueError
from app.models.domain import JobStatus, OptimizationJob

logger = logging.getLogger(__name__)


class RedisJobQueue:
    """
    Redis Streams-backed job queue.

    Streams:
      - REDIS_STREAM_PENDING: new jobs
      - REDIS_STREAM_RETRY:   failed jobs awaiting retry

    Consumer group: REDIS_CONSUMER_GROUP
    """

    def __init__(self, settings: Settings, redis_client: aioredis.Redis | None = None):
        self._settings = settings
        self._redis = redis_client or aioredis.from_url(
            settings.redis_url, decode_responses=True
        )

    async def _ensure_groups(self) -> None:
        """Create consumer groups if they don't already exist."""
        for stream in (
            self._settings.REDIS_STREAM_PENDING,
            self._settings.REDIS_STREAM_RETRY,
        ):
            try:
                await self._redis.xgroup_create(
                    stream, self._settings.REDIS_CONSUMER_GROUP, id="0", mkstream=True
                )
            except aioredis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

    async def enqueue(self, job: OptimizationJob) -> str:
        """Enqueue a job to the pending stream."""
        try:
            await self._ensure_groups()
            entry_id = await self._redis.xadd(
                self._settings.REDIS_STREAM_PENDING,
                {"payload": job.model_dump_json()},
                maxlen=10_000,
                approximate=True,
            )
            return entry_id
        except Exception as e:
            raise JobQueueError(f"Failed to enqueue job {job.job_id}", cause=e)

    async def dequeue(
        self, consumer_id: str, timeout_ms: int = 5000
    ) -> OptimizationJob | None:
        """Block until a job is available. Returns None on timeout."""
        try:
            await self._ensure_groups()
            results = await self._redis.xreadgroup(
                groupname=self._settings.REDIS_CONSUMER_GROUP,
                consumername=consumer_id,
                streams={self._settings.REDIS_STREAM_PENDING: ">"},
                count=1,
                block=timeout_ms,
            )
            if not results:
                return None

            _stream, entries = results[0]
            entry_id, fields = entries[0]

            job = OptimizationJob.model_validate_json(fields["payload"])
            job.stream_entry_id = entry_id
            return job
        except Exception as e:
            raise JobQueueError(f"Failed to dequeue: {e}", cause=e)

    async def acknowledge(self, job_id: str) -> None:
        """Acknowledge that a job was successfully processed."""
        # job_id here is the stream entry ID
        try:
            await self._redis.xack(
                self._settings.REDIS_STREAM_PENDING,
                self._settings.REDIS_CONSUMER_GROUP,
                job_id,
            )
        except Exception as e:
            raise JobQueueError(f"Failed to ack job {job_id}", cause=e)

    async def requeue(self, job: OptimizationJob, delay_s: int = 0) -> str:
        """Re-enqueue a job for retry. Increments attempt counter."""
        if not job.is_retryable:
            raise JobMaxRetriesExceeded(
                f"Job {job.job_id} has exceeded max retries ({job.max_attempts})"
            )
        job = job.model_copy(
            update={"attempt": job.attempt + 1, "status": JobStatus.RETRYING}
        )
        # For simplicity, we don't implement delay in v1 (jobs go to retry stream)
        try:
            entry_id = await self._redis.xadd(
                self._settings.REDIS_STREAM_RETRY,
                {"payload": job.model_dump_json()},
                maxlen=1_000,
                approximate=True,
            )
            return entry_id
        except Exception as e:
            raise JobQueueError(f"Failed to requeue job {job.job_id}", cause=e)

    async def fail(self, job: OptimizationJob, reason: str) -> None:
        """Permanently fail a job. Writes tombstone to Redis."""
        failed_job = job.model_copy(
            update={"status": JobStatus.FAILED, "error_message": reason}
        )
        logger.error("Job %s permanently failed: %s", job.job_id, reason)
        # In Phase 3: also write to DecisionRepository
        try:
            await self._redis.setex(
                f"dbautonomy:failed:{job.job_id}",
                86400,  # 24h TTL
                failed_job.model_dump_json(),
            )
        except Exception as e:
            logger.warning("Failed to write failure record to Redis: %s", e)

    async def close(self) -> None:
        await self._redis.aclose()
