"""
DBAutonomy — ObservabilityService (Skeleton)

Publishes real-time events to Redis pub/sub.
Consumed by the Streamlit dashboard.
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ObservabilityService:
    """
    Publishes structured events to a Redis pub/sub channel.

    Events are JSON objects with:
      { "event_type": "...", "payload": {...}, "timestamp": "..." }
    """

    def __init__(self, settings: Settings, redis_client: aioredis.Redis):
        self._settings = settings
        self._redis = redis_client
        self._channel = settings.REDIS_PUBSUB_CHANNEL

    async def publish_event(self, event_type: str, payload: dict) -> None:
        from datetime import datetime
        message = json.dumps({
            "event_type": event_type,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
        })
        try:
            await self._redis.publish(self._channel, message)
            # Persist for dashboard polling (keep last 100 events)
            history_key = f"{self._channel}:history"
            await self._redis.rpush(history_key, message)
            await self._redis.ltrim(history_key, -100, -1)
            logger.debug("Published event: %s", event_type)
        except Exception as e:
            logger.warning("Failed to publish event %s: %s", event_type, e)

    async def record_metric(
        self, name: str, value: float, tags: dict | None = None
    ) -> None:
        await self.publish_event("metric", {
            "name": name,
            "value": value,
            "tags": tags or {},
        })
