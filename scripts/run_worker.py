"""
DBAutonomy — Standalone AgentWorker Runner

This is the separate process that:
  1. Reads AGENT configuration from environment / .env
  2. Initialises the full DI container
  3. Loads persisted bandit state
  4. Consumes Redis job stream indefinitely
  5. Runs the full optimization pipeline per job

Run with:
    python scripts/run_worker.py

Or inside Docker:
    python -m scripts.run_worker

Architecture note:
  This process is SEPARATE from the FastAPI app.
  - FastAPI app: handles HTTP (inject, decisions, bandit API)
  - This process: consumes Redis, runs the agent pipeline

In production, run multiple instances for parallelism.
In the hackathon demo, one instance is sufficient.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

# Ensure project root is in path when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.core.container import Container
from app.core.exceptions import StartupError


def setup_logging():
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def main():
    setup_logging()
    logger = logging.getLogger("worker_runner")
    settings = get_settings()
    logger.info("=" * 60)
    logger.info("DBAutonomy AgentWorker starting")
    logger.info("Redis: %s", settings.redis_url.split("@")[-1])  # no password in log
    logger.info("Primary DB: %s:%d/%s", settings.DB_HOST, settings.DB_PORT, settings.DB_NAME)
    logger.info("Shadow DB: %s:%d/%s", settings.SHADOW_DB_HOST, settings.SHADOW_DB_PORT, settings.SHADOW_DB_NAME)
    logger.info("Ollama: %s, model=%s", settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL)
    logger.info("Safety tables: %s", settings.SAFETY_ALLOWED_TABLES or "(all allowed)")
    logger.info("=" * 60)

    container = Container(settings=settings)
    worker = container.make_agent_worker(consumer_id="worker-main")

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler for all signals
            pass

    try:
        # Startup checks (shadow DB health, etc.)
        try:
            await worker._startup_checks()
        except StartupError as e:
            logger.error("STARTUP FAILED: %s", e)
            logger.error(
                "Ensure docker-compose is running: "
                "docker-compose up db_primary db_shadow redis ollama"
            )
            sys.exit(1)

        # Load persisted bandit state
        await worker._load_bandit_state()

        logger.info("AgentWorker ready — consuming Redis stream '%s'", settings.REDIS_STREAM_PENDING)
        logger.info("Send a job via: POST http://localhost:8000/api/jobs/inject")

        # Main consume loop
        while not stop_event.is_set():
            try:
                job = await container.job_queue.dequeue(
                    consumer_id="worker-main",
                    timeout_ms=2000,
                )
                if job is None:
                    continue

                logger.info(
                    "Dequeued job %s (attempt %d/%d)",
                    job.job_id, job.attempt + 1, job.max_attempts
                )
                await worker._process_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Unexpected error in consume loop: %s", e)
                await asyncio.sleep(1)

    finally:
        logger.info("Shutting down container resources...")
        await container.close()
        logger.info("AgentWorker stopped.")


if __name__ == "__main__":
    asyncio.run(main())
