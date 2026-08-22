"""
DBAutonomy — Core Interfaces (Protocols)

All major components are defined here as Python Protocols.
Implementations live in their respective sub-packages.
This file is the authoritative source of the component contracts.

Rules:
  - Never import concrete implementations here.
  - Never add business logic here.
  - Every Protocol method must be fully typed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from app.models.domain import (
    SlowQueryEvent,
    OptimizationJob,
    ParsedQuery,
    TableSchema,
    OptimizationContext,
    IndexCandidate,
    BenchmarkResult,
    SafetyDecision,
    OptimizationRecord,
    BanditState,
    ContextVector,
)


# ---------------------------------------------------------------------------
# Log Monitoring
# ---------------------------------------------------------------------------

@runtime_checkable
class ILogMonitor(Protocol):
    """Tail or poll the PostgreSQL slow-query log / pg_stat_statements."""

    async def start(self) -> None:
        """Start the monitoring loop. Must be non-blocking (run as a task)."""
        ...

    async def stop(self) -> None:
        """Gracefully stop the monitoring loop."""
        ...


# ---------------------------------------------------------------------------
# Job Queue
# ---------------------------------------------------------------------------

@runtime_checkable
class IJobQueue(Protocol):
    """Durable job queue backed by Redis Streams."""

    async def enqueue(self, job: OptimizationJob) -> str:
        """Enqueue a job. Returns the stream entry ID."""
        ...

    async def dequeue(self, consumer_id: str, timeout_ms: int = 5000) -> OptimizationJob | None:
        """Block until a job is available or timeout expires. Returns None on timeout."""
        ...

    async def acknowledge(self, job_id: str) -> None:
        """Acknowledge successful processing. Removes from PEL."""
        ...

    async def requeue(self, job: OptimizationJob, delay_s: int = 0) -> str:
        """Re-enqueue a failed job into the retry stream."""
        ...

    async def fail(self, job: OptimizationJob, reason: str) -> None:
        """Permanently fail a job. Writes to the dead-letter record."""
        ...


# ---------------------------------------------------------------------------
# Log Parsing (Local AI — Qwen2.5-Coder 3B)
# ---------------------------------------------------------------------------

@runtime_checkable
class ILocalLogParser(Protocol):
    """Parse raw PostgreSQL log lines into structured ParsedQuery objects."""

    async def parse(self, raw_log: str) -> ParsedQuery:
        """
        Parse a raw log line or block.
        
        Raises:
            ParseError: If parsing fails completely (even after regex fallback).
        """
        ...


# ---------------------------------------------------------------------------
# Schema Inspection
# ---------------------------------------------------------------------------

@runtime_checkable
class ISchemaInspector(Protocol):
    """Read-only access to the production database schema."""

    async def inspect(self, table_name: str) -> TableSchema:
        """
        Retrieve schema for a table.
        
        Raises:
            SchemaNotFoundError: If the table does not exist.
        """
        ...

    async def invalidate_cache(self, table_name: str) -> None:
        """Force cache invalidation for a specific table."""
        ...


# ---------------------------------------------------------------------------
# Context Building
# ---------------------------------------------------------------------------

@runtime_checkable
class IContextBuilder(Protocol):
    """Assemble an OptimizationContext from parsed query + schema + history."""

    def build(
        self,
        parsed: ParsedQuery,
        schema: TableSchema,
        history: list[OptimizationRecord],
    ) -> OptimizationContext:
        """Build context. Pure function — no I/O."""
        ...


# ---------------------------------------------------------------------------
# Candidate Generation (Remote AI — Gemini Flash)
# ---------------------------------------------------------------------------

@runtime_checkable
class ICandidateGenerator(Protocol):
    """Generate index candidates from an OptimizationContext using Gemini Flash."""

    async def generate(self, context: OptimizationContext) -> list[IndexCandidate]:
        """
        Generate candidates.
        
        Returns at least one valid candidate.
        
        Raises:
            CandidateGenerationError: If Gemini fails after all retries.
            NoCandidatesError: If all returned candidates fail validation.
        """
        ...


# ---------------------------------------------------------------------------
# Contextual Bandit
# ---------------------------------------------------------------------------

@runtime_checkable
class IBanditPolicy(Protocol):
    """
    Contextual bandit that selects which candidate to experimentally evaluate.
    
    Algorithm: LinUCB (disjoint model). See docs/BANDIT_SPEC.md.
    """

    def select(
        self,
        context_vector: ContextVector,
        candidates: list[IndexCandidate],
    ) -> IndexCandidate:
        """
        Select a candidate to evaluate.
        
        Args:
            context_vector: Fixed-dimension feature vector.
            candidates: Non-empty list of validated candidates.
        
        Returns:
            The selected candidate.
        
        Raises:
            ValueError: If candidates is empty.
        """
        ...

    def update(
        self,
        context_vector: ContextVector,
        action_fingerprint: str,
        reward: float,
    ) -> None:
        """
        Update bandit state with observed reward.
        
        This is the learning step. Must be called BEFORE saving state to DB.
        
        Args:
            context_vector: Same vector used in select().
            action_fingerprint: Unique ID of the candidate that was evaluated.
            reward: Empirical reward ∈ [-1.0, 1.0].
        """
        ...

    @property
    def state(self) -> BanditState:
        """Serialisable state snapshot for persistence."""
        ...

    def load_state(self, state: BanditState) -> None:
        """Restore state from a persisted snapshot."""
        ...


# ---------------------------------------------------------------------------
# Shadow Database
# ---------------------------------------------------------------------------

@runtime_checkable
class IShadowDatabaseManager(Protocol):
    """Manage the shadow PostgreSQL instance used for safe experimentation."""

    async def is_healthy(self) -> bool:
        """Return True if the shadow database is reachable and ready."""
        ...

    async def clone_schema(self, table_name: str) -> None:
        """Clone the production schema for a table into the shadow database."""
        ...

    async def populate_benchmark_data(self, table_name: str, row_count: int) -> None:
        """Populate shadow table with synthetic data for benchmarking."""
        ...

    async def apply_index(self, candidate: IndexCandidate) -> None:
        """Apply a candidate index to the shadow database."""
        ...

    async def remove_index(self, candidate: IndexCandidate) -> None:
        """Remove a candidate index from the shadow database."""
        ...

    async def get_connection(self):
        """Return an async database connection to the shadow database."""
        ...


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------

@runtime_checkable
class IBenchmarkRunner(Protocol):
    """Execute benchmarks on the shadow database and collect statistics."""

    async def run(
        self,
        sql: str,
        shadow: IShadowDatabaseManager,
        iterations: int = 10,
    ) -> BenchmarkResult:
        """
        Execute the query N times and collect EXPLAIN ANALYZE statistics.
        
        Args:
            sql: The query to benchmark.
            shadow: The shadow database manager.
            iterations: Number of executions for statistics.
        
        Returns:
            BenchmarkResult with p50, p95, cache stats.
        
        Raises:
            BenchmarkTimeoutError: If the benchmark exceeds the time limit.
        """
        ...

    async def run_write_benchmark(
        self,
        table_name: str,
        shadow: IShadowDatabaseManager,
        iterations: int = 100,
    ) -> BenchmarkResult:
        """Measure write performance on the shadow table."""
        ...


# ---------------------------------------------------------------------------
# Reward Calculator
# ---------------------------------------------------------------------------

@runtime_checkable
class IRewardCalculator(Protocol):
    """Compute the empirical reward from benchmark results."""

    def compute(
        self,
        baseline: BenchmarkResult,
        experiment: BenchmarkResult,
    ) -> float:
        """
        Pure function. No I/O. See docs/REWARD_SPEC.md for formula.
        
        Returns:
            Reward ∈ [-1.0, 1.0].
        """
        ...


# ---------------------------------------------------------------------------
# Safety Gate
# ---------------------------------------------------------------------------

@runtime_checkable
class ISafetyGate(Protocol):
    """
    Deterministic rule engine. No AI. See docs/SAFETY_SPEC.md.
    """

    def structural_check(self, candidate: IndexCandidate) -> bool:
        """
        Fast pre-bandit check: is this candidate structurally valid?
        Does NOT check improvement thresholds (no benchmark data yet).
        """
        ...

    def evaluate(
        self,
        candidate: IndexCandidate,
        baseline: BenchmarkResult,
        experiment: BenchmarkResult,
        reward: float,
    ) -> SafetyDecision:
        """
        Full evaluation including all 8 stages from SAFETY_SPEC.
        
        Returns:
            SafetyDecision with approved flag and reason.
        """
        ...


# ---------------------------------------------------------------------------
# Deployment Manager
# ---------------------------------------------------------------------------

@runtime_checkable
class IDeploymentManager(Protocol):
    """Execute approved index deployments on the production database."""

    async def deploy(
        self,
        candidate: IndexCandidate,
        decision: SafetyDecision,
    ) -> str:
        """
        Deploy an approved index to production.
        
        PRECONDITION: decision.approved must be True.
        
        Args:
            candidate: The validated candidate.
            decision: The approved safety decision (proof of gate passage).
        
        Returns:
            The index name that was created.
        
        Raises:
            AssertionError: If decision.approved is False (programming error).
            DeploymentError: If the CREATE INDEX fails.
        """
        ...


# ---------------------------------------------------------------------------
# Decision Repository
# ---------------------------------------------------------------------------

@runtime_checkable
class IDecisionRepository(Protocol):
    """Persist all decisions, experiments, and bandit state."""

    async def save_decision(self, record: OptimizationRecord) -> None:
        """Persist a complete optimization decision record."""
        ...

    async def get_recent_decisions(
        self,
        table_name: str | None = None,
        limit: int = 20,
    ) -> list[OptimizationRecord]:
        """Retrieve recent decisions, optionally filtered by table."""
        ...

    async def save_bandit_state(self, state: BanditState) -> None:
        """Persist the current bandit state."""
        ...

    async def load_bandit_state(self) -> BanditState | None:
        """Load the most recent bandit state. Returns None if no state exists."""
        ...

    async def get_decisions_for_context(
        self,
        table_name: str,
        limit: int = 10,
    ) -> list[OptimizationRecord]:
        """Retrieve recent decisions for a specific table (used by ContextBuilder)."""
        ...


# ---------------------------------------------------------------------------
# Observability Service
# ---------------------------------------------------------------------------

@runtime_checkable
class IObservabilityService(Protocol):
    """Publish events and metrics for the dashboard."""

    async def publish_event(self, event_type: str, payload: dict) -> None:
        """Publish an event to the Redis pub/sub channel."""
        ...

    async def record_metric(self, name: str, value: float, tags: dict | None = None) -> None:
        """Record a numeric metric."""
        ...


# ---------------------------------------------------------------------------
# Feature Extractor
# ---------------------------------------------------------------------------

@runtime_checkable
class IFeatureExtractor(Protocol):
    """Extract a fixed-dimension context vector from an OptimizationContext."""

    def extract(self, context: OptimizationContext) -> ContextVector:
        """
        Extract features. Pure function.
        
        Returns:
            Numpy array of shape (d,) where d matches bandit configuration.
        """
        ...
