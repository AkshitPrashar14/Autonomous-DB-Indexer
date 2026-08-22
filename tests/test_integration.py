"""
DBAutonomy — End-to-End Integration Tests
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from app.agent.worker import AgentWorker
from app.core.config import get_settings
from app.core.container import get_container
from app.database.orm_models import Base
from app.main import app
from app.models.domain import IndexCandidate, IndexType, OptimizationJob, ParsedQuery, QueryType, OptimizationStatus

# We need a longer timeout for integration tests
pytestmark = pytest.mark.asyncio

logger = logging.getLogger(__name__)

@pytest_asyncio.fixture
async def setup_integration_env():
    settings = get_settings()
    
    # 1. Clear Redis
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushall()
    
    # 2. Reset database tables
    engine = create_async_engine(settings.db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
        # Create a test table in primary DB if not exists
        await conn.execute(text("DROP TABLE IF EXISTS test_orders CASCADE"))
        await conn.execute(text(
            "CREATE TABLE test_orders ("
            "id SERIAL PRIMARY KEY, "
            "customer_id INT, "
            "status TEXT, "
            "amount NUMERIC)"
        ))
        
        # Insert some data
        await conn.execute(text(
            "INSERT INTO test_orders (customer_id, status, amount) "
            "SELECT i % 100, 'pending', 10.5 FROM generate_series(1, 1000) i"
        ))
        await conn.execute(text("ANALYZE test_orders"))
        
        # Drop any existing index
        await conn.execute(text("DROP INDEX IF EXISTS dbautonomy_test_idx"))
        
    yield settings
    
    await redis.aclose()
    await engine.dispose()


@pytest.fixture
def api_client():
    return TestClient(app)


async def test_end_to_end_pipeline(setup_integration_env, mocker):
    """
    Integration test for the full pipeline.
    Mocks the LLM calls to be deterministic and fast, but exercises
    all other real infrastructure (Redis, PostgreSQL primary + shadow).
    """
    settings = setup_integration_env
    container = get_container()
    
    # --- Mock LLMs ---
    # 1. Mock LocalLogParser
    mock_parser = mocker.patch.object(container.local_log_parser, "parse", new_callable=AsyncMock)
    mock_parser.return_value = ParsedQuery(
        sql="SELECT * FROM test_orders WHERE customer_id = 42",
        duration_ms=1500.0,
        table_name="test_orders",
        query_type=QueryType.SELECT,
        where_columns=["customer_id"],
        join_tables=[],
        order_by_columns=[],
        parse_source="mock",
        confidence=1.0,
    )
    
    # 2. Mock CandidateGenerator
    mock_gen = mocker.patch.object(container.candidate_generator, "generate", new_callable=AsyncMock)
    mock_gen.return_value = [
        IndexCandidate(
            table_name="test_orders",
            columns=["customer_id"],
            index_type=IndexType.BTREE,
            is_unique=False,
            where_clause=None,
            explanation="Test candidate",
            gemini_rank=1
        )
    ]
    
    # 3. Whitelist test_orders in safety settings and lower threshold
    settings.SAFETY_ALLOWED_TABLES = "orders,products,events,test_orders"
    settings.SAFETY_MIN_REWARD_THRESHOLD = -1.0
    settings.BENCHMARK_WRITE_IMPACT = False
    
    # 4. Create a custom worker with the patched container
    worker = container.make_agent_worker(consumer_id="test-worker")
    
    # --- Execute ---
    # 1. Inject job via HTTP API
    from app.api.jobs import inject_slow_query, InjectRequest
    response = await inject_slow_query(InjectRequest(raw_log="SELECT * FROM test_orders WHERE customer_id = 42"))
    job_id = response.job_id
    stream_entry_id = response.stream_entry_id
    
    assert job_id is not None
    assert stream_entry_id is not None
    
    # 2. Worker setup
    await worker._startup_checks()
    await worker._load_bandit_state()
    
    # 3. Consume the job directly from queue
    job = await container.job_queue.dequeue("test-worker", timeout_ms=1000)
    assert job is not None
    assert str(job.job_id) == job_id
    
    # 4. Run the pipeline
    await worker._process_job(job)
    
    # --- Verify ---
    # 1. Check decisions API
    from app.api.decisions import get_decisions
    decisions_res = await get_decisions(table_name=None, limit=10)
    assert decisions_res["count"] == 1
    
    decision = decisions_res["decisions"][0]
    assert decision["table_name"] == "test_orders"
    assert decision["status"] == OptimizationStatus.DEPLOYED.value, decision.get("rejection_reason", "Unknown reason")
    assert decision["approved"] is True
    assert decision["deployed_index"] is not None
    
    # 2. Verify bandit state learned
    bandit_state = await container.decision_repository.load_bandit_state()
    assert bandit_state is not None
    assert bandit_state.total_updates == 1
    assert len(bandit_state.actions) == 1
    
    # 3. Verify the index was actually deployed to primary DB
    engine = container.primary_engine
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT count(*) FROM pg_indexes "
            "WHERE tablename = 'test_orders' AND indexname = :idx"
        ), {"idx": decision["deployed_index"]})
        count = result.scalar()
        assert count == 1, "The index was not found on the primary database!"
    
    await container.close()
