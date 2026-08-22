"""
DBAutonomy — Dependency Injection Container

Wires all interface implementations together.
All concrete classes are imported and instantiated here — nowhere else.
Components are cached as singletons after first creation.

Usage:
    from app.core.container import get_container
    container = get_container()
    job_queue = container.job_queue
"""

from __future__ import annotations

import logging
from functools import lru_cache

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Container:
    """
    Lightweight dependency injection container.

    Components are created lazily and cached as singletons.
    All external I/O happens inside the component constructors,
    not here — this keeps the container synchronous.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict = {}

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    @property
    def redis_client(self) -> aioredis.Redis:
        if "redis" not in self._cache:
            self._cache["redis"] = aioredis.from_url(
                self._settings.redis_url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
        return self._cache["redis"]

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------

    @property
    def job_queue(self):
        if "job_queue" not in self._cache:
            from app.queue.redis_queue import RedisJobQueue
            self._cache["job_queue"] = RedisJobQueue(
                self._settings, self.redis_client
            )
        return self._cache["job_queue"]

    # ------------------------------------------------------------------
    # Local AI — Qwen2.5-Coder 3B (Ollama)
    # ------------------------------------------------------------------

    @property
    def local_log_parser(self):
        if "log_parser" not in self._cache:
            from app.ai.local_parser import LocalLogParser
            self._cache["log_parser"] = LocalLogParser(self._settings)
        return self._cache["log_parser"]

    # ------------------------------------------------------------------
    # Remote AI — Gemini Flash
    # ------------------------------------------------------------------

    @property
    def candidate_generator(self):
        if "candidate_gen" not in self._cache:
            from app.ai.candidate_gen import CandidateGenerator
            self._cache["candidate_gen"] = CandidateGenerator(self._settings)
        return self._cache["candidate_gen"]

    # ------------------------------------------------------------------
    # Context Builder
    # ------------------------------------------------------------------

    @property
    def context_builder(self):
        if "context_builder" not in self._cache:
            from app.ai.context_builder import ContextBuilder
            self._cache["context_builder"] = ContextBuilder()
        return self._cache["context_builder"]

    # ------------------------------------------------------------------
    # Feature Extractor
    # ------------------------------------------------------------------

    @property
    def feature_extractor(self):
        if "feature_extractor" not in self._cache:
            from app.bandit.features import ContextFeatureExtractor
            self._cache["feature_extractor"] = ContextFeatureExtractor()
        return self._cache["feature_extractor"]

    # ------------------------------------------------------------------
    # Bandit Policy — LinUCB
    # ------------------------------------------------------------------

    @property
    def bandit_policy(self):
        if "bandit" not in self._cache:
            from app.bandit.linucb import LinUCBPolicy
            self._cache["bandit"] = LinUCBPolicy(
                alpha=self._settings.BANDIT_ALPHA,
                d=self._settings.BANDIT_CONTEXT_DIM,
            )
        return self._cache["bandit"]

    # ------------------------------------------------------------------
    # Primary DB engine (read-only for schema inspection)
    # ------------------------------------------------------------------

    @property
    def primary_engine(self):
        if "primary_engine" not in self._cache:
            self._cache["primary_engine"] = create_async_engine(
                self._settings.db_url,
                pool_size=2,
                max_overflow=3,
                echo=self._settings.DEBUG,
            )
        return self._cache["primary_engine"]

    # ------------------------------------------------------------------
    # Schema Inspector
    # ------------------------------------------------------------------

    @property
    def schema_inspector_factory(self):
        """
        Returns an async factory that creates a SchemaInspector with an
        open DB connection. The connection stays open for the life of the
        returned inspector.

        IMPORTANT: The caller must call inspector.inspect() before the
        engine disposes. In practice, the AgentWorker keeps the engine
        alive for the process lifetime, so this is safe.
        """
        from app.database.schema_inspector import SchemaInspector

        # Return a coroutine factory. Each call acquires a connection,
        # runs the inspect, and releases it automatically.
        # The returned SchemaInspector holds the connection — caller must
        # use it before releasing.
        async def _make():
            # Use pool-managed connection (held open, returned to pool on close)
            conn = await self.primary_engine.connect()
            inspector = SchemaInspector(self._settings, conn, self.redis_client)
            # Monkey-patch a close method so the worker can release it
            inspector._conn_to_close = conn
            return inspector

        return _make

    # ------------------------------------------------------------------
    # Shadow Database Manager
    # ------------------------------------------------------------------

    @property
    def shadow_db_manager(self):
        if "shadow_db" not in self._cache:
            from app.database.shadow_manager import ShadowDatabaseManager
            self._cache["shadow_db"] = ShadowDatabaseManager(self._settings)
        return self._cache["shadow_db"]

    # ------------------------------------------------------------------
    # Benchmark Runner
    # ------------------------------------------------------------------

    @property
    def benchmark_runner(self):
        if "benchmark_runner" not in self._cache:
            from app.database.benchmark_runner import BenchmarkRunner
            self._cache["benchmark_runner"] = BenchmarkRunner(self._settings)
        return self._cache["benchmark_runner"]

    # ------------------------------------------------------------------
    # Reward Calculator
    # ------------------------------------------------------------------

    @property
    def reward_calculator(self):
        if "reward_calc" not in self._cache:
            from app.evaluation.reward_calculator import RewardCalculator
            self._cache["reward_calc"] = RewardCalculator(self._settings)
        return self._cache["reward_calc"]

    # ------------------------------------------------------------------
    # Safety Gate
    # ------------------------------------------------------------------

    @property
    def safety_gate(self):
        if "safety_gate" not in self._cache:
            from app.evaluation.safety_gate import SafetyGate
            self._cache["safety_gate"] = SafetyGate(self._settings)
        return self._cache["safety_gate"]

    # ------------------------------------------------------------------
    # Decision Repository
    # ------------------------------------------------------------------

    @property
    def decision_repository(self):
        if "decision_repo" not in self._cache:
            from app.database.decision_repo import DecisionRepository
            self._cache["decision_repo"] = DecisionRepository.from_settings(
                self._settings
            )
        return self._cache["decision_repo"]

    # ------------------------------------------------------------------
    # Deployment Manager
    # ------------------------------------------------------------------

    def make_deployment_manager(self, conn):
        """Factory — returns a DeploymentManager for a specific connection."""
        from app.database.deployment_manager import DeploymentManager
        return DeploymentManager(conn)

    # ------------------------------------------------------------------
    # Observability Service
    # ------------------------------------------------------------------

    @property
    def observability_service(self):
        if "observability" not in self._cache:
            from app.database.observability import ObservabilityService
            self._cache["observability"] = ObservabilityService(
                self._settings, self.redis_client
            )
        return self._cache["observability"]

    # ------------------------------------------------------------------
    # Agent Worker factory
    # ------------------------------------------------------------------

    def make_agent_worker(self, consumer_id: str | None = None):
        from app.agent.worker import AgentWorker
        return AgentWorker(
            settings=self._settings,
            job_queue=self.job_queue,
            log_parser=self.local_log_parser,
            schema_inspector_factory=self.schema_inspector_factory,
            context_builder=self.context_builder,
            candidate_generator=self.candidate_generator,
            feature_extractor=self.feature_extractor,
            bandit_policy=self.bandit_policy,
            shadow_db=self.shadow_db_manager,
            benchmark_runner=self.benchmark_runner,
            reward_calculator=self.reward_calculator,
            safety_gate=self.safety_gate,
            decision_repo=self.decision_repository,
            observability=self.observability_service,
            consumer_id=consumer_id,
            primary_engine=self.primary_engine,
        )

    async def close(self) -> None:
        """Close all async resources."""
        if "redis" in self._cache:
            await self._cache["redis"].aclose()
        if "shadow_db" in self._cache:
            await self._cache["shadow_db"].close()
        if "primary_engine" in self._cache:
            await self._cache["primary_engine"].dispose()
        if "decision_repo" in self._cache:
            await self._cache["decision_repo"].close()
        if "log_parser" in self._cache:
            await self._cache["log_parser"].close()


@lru_cache(maxsize=1)
def get_container() -> Container:
    return Container(settings=get_settings())
