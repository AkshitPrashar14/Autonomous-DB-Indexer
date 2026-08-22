"""
DBAutonomy — DecisionRepository

SQLAlchemy async ORM implementation of IDecisionRepository.

Persists:
  - OptimizationRecord  → optimization_records table
  - BanditState         → bandit_state table (singleton row, upserted)
  - SafetyDecision      → safety_decisions table (append-only)
  - Deployed indexes    → deployed_indexes table

The tables are created by scripts/sql/init_primary.sql which runs at
container startup. This module does NOT run DDL itself.

Thread-safety: each AgentWorker creates its own engine/session factory.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, text, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.database.orm_models import (
    BanditStateORM,
    Base,
    DeployedIndexORM,
    OptimizationRecordORM,
    SafetyDecisionORM,
)
from app.models.domain import BanditState, OptimizationRecord, OptimizationStatus

logger = logging.getLogger(__name__)


class DecisionRepository:
    """
    SQLAlchemy async persistence for all agent decisions and bandit state.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "DecisionRepository":
        engine = create_async_engine(
            settings.db_url,
            pool_size=3,
            max_overflow=5,
            echo=settings.DEBUG,
        )
        return cls(engine)

    async def create_tables(self) -> None:
        """Create all tables if they don't exist (used in tests / dev)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------
    # IDecisionRepository contract
    # ------------------------------------------------------------------

    async def save_decision(self, record: OptimizationRecord) -> None:
        """Persist a complete optimization decision record."""
        async with self._session_factory() as session:
            async with session.begin():
                orm = OptimizationRecordORM(
                    record_id=record.record_id,
                    job_id=record.job_id,
                    table_name=record.candidate.table_name,
                    candidate_json=record.candidate.model_dump(mode="json"),
                    baseline_json=record.baseline.model_dump(mode="json"),
                    experiment_json=record.experiment.model_dump(mode="json"),
                    reward=record.reward,
                    decision_json=record.decision.model_dump(mode="json"),
                    status=record.status.value,
                    deployed_index=record.deployed_index_name,
                    context_vector=record.context_vector,
                    created_at=record.created_at,
                )
                session.add(orm)

                # Also persist safety decision row (append-only)
                safety_orm = SafetyDecisionORM(
                    record_id=record.record_id,
                    approved=record.decision.approved,
                    reason=record.decision.reason,
                    stages_passed=record.decision.stages_passed,
                    stages_failed=record.decision.stages_failed,
                    reward=record.decision.reward,
                    risk_score=record.decision.risk_score,
                )
                session.add(safety_orm)

                # If deployed, persist deployment record
                if record.status == OptimizationStatus.DEPLOYED and record.deployed_index_name:
                    dep_orm = DeployedIndexORM(
                        record_id=record.record_id,
                        index_name=record.deployed_index_name,
                        table_name=record.candidate.table_name,
                        columns=record.candidate.columns,
                        index_type=record.candidate.index_type.value,
                        create_sql=record.candidate.create_sql,
                        deployed_at=datetime.utcnow(),
                    )
                    session.add(dep_orm)

        logger.info(
            "Decision saved: record_id=%s status=%s reward=%.4f",
            record.record_id,
            record.status.value,
            record.reward,
        )

    async def get_recent_decisions(
        self,
        table_name: str | None = None,
        limit: int = 20,
    ) -> list[OptimizationRecord]:
        """Retrieve recent decisions, newest first."""
        async with self._session_factory() as session:
            stmt = select(OptimizationRecordORM).order_by(
                OptimizationRecordORM.created_at.desc()
            ).limit(limit)
            if table_name:
                stmt = stmt.where(OptimizationRecordORM.table_name == table_name)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_orm_to_domain(row) for row in rows]

    async def save_bandit_state(self, state: BanditState) -> None:
        """
        Upsert bandit state.

        Uses a singleton pattern: always updates row id=1.
        Wrapped in a transaction to satisfy RULE-07.
        """
        async with self._session_factory() as session:
            async with session.begin():
                # Delete existing row, then insert fresh
                await session.execute(
                    delete(BanditStateORM)
                )
                orm = BanditStateORM(
                    state_json=state.model_dump(mode="json"),
                    saved_at=datetime.utcnow(),
                )
                session.add(orm)
        logger.debug(
            "Bandit state persisted: %d actions, %d updates",
            len(state.actions),
            state.total_updates,
        )

    async def load_bandit_state(self) -> BanditState | None:
        """Load the most recent bandit state snapshot."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(BanditStateORM).order_by(BanditStateORM.saved_at.desc()).limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            try:
                return BanditState.model_validate(row.state_json)
            except Exception as e:
                logger.error("Failed to deserialise bandit state: %s", e)
                return None

    async def get_decisions_for_context(
        self,
        table_name: str,
        limit: int = 10,
    ) -> list[OptimizationRecord]:
        """Retrieve recent decisions for a specific table."""
        return await self.get_recent_decisions(table_name=table_name, limit=limit)

    async def get_stats(self) -> dict:
        """Aggregate statistics for the dashboard."""
        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(OptimizationRecordORM)
            )
            deployed = await session.scalar(
                select(func.count()).select_from(OptimizationRecordORM).where(
                    OptimizationRecordORM.status == "deployed"
                )
            )
            avg_reward = await session.scalar(
                select(func.avg(OptimizationRecordORM.reward)).select_from(
                    OptimizationRecordORM
                )
            )
            return {
                "total_decisions": total or 0,
                "deployed": deployed or 0,
                "rejected": (total or 0) - (deployed or 0),
                "avg_reward": float(avg_reward) if avg_reward is not None else 0.0,
            }

    async def close(self) -> None:
        await self._engine.dispose()


# ---------------------------------------------------------------------------
# ORM → Domain conversion
# ---------------------------------------------------------------------------

def _orm_to_domain(row: OptimizationRecordORM) -> OptimizationRecord:
    from app.models.domain import (
        BenchmarkResult,
        IndexCandidate,
        SafetyDecision,
    )
    return OptimizationRecord(
        record_id=row.record_id,
        job_id=row.job_id,
        candidate=IndexCandidate.model_validate(row.candidate_json),
        baseline=BenchmarkResult.model_validate(row.baseline_json),
        experiment=BenchmarkResult.model_validate(row.experiment_json),
        reward=row.reward,
        decision=SafetyDecision.model_validate(row.decision_json),
        status=OptimizationStatus(row.status),
        deployed_index_name=row.deployed_index,
        context_vector=row.context_vector or [],
        created_at=row.created_at,
    )
