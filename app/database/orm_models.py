"""
DBAutonomy — SQLAlchemy ORM Models for Persistence

These are the database-layer representations of the domain models.
They live separately from domain.py to preserve the domain/persistence split.
"""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    Integer, String, Text, JSON, Index as SaIndex, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY as PgArray, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class OptimizationRecordORM(Base):
    __tablename__ = "optimization_records"

    record_id = Column(PgUUID(as_uuid=True), primary_key=True)
    job_id = Column(PgUUID(as_uuid=True), nullable=False, index=True)
    table_name = Column(Text, nullable=False, index=True)
    candidate_json = Column(JSON, nullable=False)
    baseline_json = Column(JSON, nullable=False)
    experiment_json = Column(JSON, nullable=False)
    reward = Column(Float, nullable=False)
    decision_json = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    deployed_index = Column(Text, nullable=True)
    context_vector = Column(JSON, nullable=True)  # list[float]
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        SaIndex("idx_records_created_at_desc", "created_at"),
    )


class BanditStateORM(Base):
    __tablename__ = "bandit_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_json = Column(JSON, nullable=False)
    saved_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SafetyDecisionORM(Base):
    __tablename__ = "safety_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(PgUUID(as_uuid=True), nullable=True)
    approved = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=False)
    stages_passed = Column(JSON, nullable=True)
    stages_failed = Column(JSON, nullable=True)
    reward = Column(Float, nullable=True)
    risk_score = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DeployedIndexORM(Base):
    __tablename__ = "deployed_indexes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(PgUUID(as_uuid=True), nullable=True)
    index_name = Column(Text, nullable=False, unique=True)
    table_name = Column(Text, nullable=False)
    columns = Column(JSON, nullable=True)
    index_type = Column(String(16), nullable=True)
    create_sql = Column(Text, nullable=False)
    deployed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
