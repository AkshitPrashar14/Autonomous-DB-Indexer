# DBAutonomy — Architecture Document

## Overview

DBAutonomy is an autonomous PostgreSQL optimization agent that combines two AI models,
a genuine contextual bandit, shadow-database experimentation, and a deterministic safety
gate to safely discover, evaluate, and deploy index improvements — all without human
intervention.

---

## Design Principles

1. **No LLM ever touches production SQL directly.** Every candidate must pass the
   SafetyGate before the DeploymentManager is invoked.
2. **Loose coupling via interfaces.** Every major component is defined as a Python
   Protocol. Implementations may be swapped without touching the core agent loop.
3. **Measured, not assumed, improvement.** The BenchmarkRunner collects empirical
   query-plan statistics from the shadow database. Reward is computed from data.
4. **Failure is a first-class citizen.** Every component raises typed exceptions.
   The AgentWorker catches them, logs them, and routes the job to the retry queue.
5. **The contextual bandit is the decision engine.** Gemini generates the candidate
   *space*; the bandit selects which single candidate to evaluate. This is not an
   LLM-as-bandit pattern.

---

## Component Catalogue

```
┌────────────────────────────────────────────────────────────────────────┐
│                         Agent Control Loop                             │
│                                                                        │
│  LogMonitor ──► JobQueue ──► AgentWorker                               │
│                                   │                                    │
│         ┌─────────────────────────┤                                    │
│         │                         │                                    │
│         ▼                         ▼                                    │
│  LocalLogParser           ContextBuilder                               │
│  (Qwen2.5-Coder)          (SchemaInspector + history)                  │
│         │                         │                                    │
│         └──────────┬──────────────┘                                    │
│                    ▼                                                    │
│           CandidateGenerator (Gemini Flash)                            │
│                    │                                                    │
│                    ▼                                                    │
│            BanditPolicy (LinUCB)                                       │
│                    │                                                    │
│                    ▼                                                    │
│       ShadowDatabaseManager                                            │
│                    │                                                    │
│                    ▼                                                    │
│          BenchmarkRunner ──► RewardCalculator                          │
│                    │                                                    │
│                    ▼                                                    │
│               SafetyGate                                               │
│                    │                                                    │
│           ┌────────┴────────┐                                          │
│           ▼                 ▼                                          │
│    DeploymentManager   DecisionRepository                              │
│                              │                                         │
│                              ▼                                         │
│                    ObservabilityService                                │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

### LogMonitor
- Tails the PostgreSQL slow-query log (or queries `pg_stat_statements`).
- Emits `SlowQueryEvent` objects to the JobQueue.
- Configurable: minimum duration threshold, polling interval.

### JobQueue
- Backed by Redis Streams.
- Two streams: `jobs:pending` and `jobs:retry`.
- Guarantees at-least-once delivery with consumer groups.
- Jobs are serialised as JSON-encoded `OptimizationJob` Pydantic models.

### AgentWorker
- Long-running background process consuming from JobQueue.
- Orchestrates the full pipeline per job.
- Catches all typed exceptions; writes failure records to DecisionRepository;
  re-enqueues retryable jobs.
- Concurrency: multiple workers may run in parallel via Redis consumer groups.

### LocalLogParser  *(Qwen2.5-Coder 3B via Ollama)*
- Receives raw log text.
- Returns structured `ParsedQuery` (SQL, duration, table hints, bind params).
- Handles malformed / truncated log lines gracefully.
- Falls back to regex extraction when model output fails JSON validation.

### SchemaInspector
- Reads live schema from the target production database (read-only connection).
- Returns `TableSchema` objects: columns, existing indexes, FK references, stats.
- Caches results with a configurable TTL (default 5 min) in Redis.

### ContextBuilder
- Combines `ParsedQuery` + `TableSchema` + recent `DecisionHistory` into a
  structured `OptimizationContext`.
- Responsible for token budgeting — truncates history to fit Gemini prompt window.

### CandidateGenerator  *(Gemini Flash)*
- Receives `OptimizationContext`.
- Returns a list of `IndexCandidate` Pydantic objects.
- Output is validated against a strict schema before use.
- Malformed / unsafe candidates are dropped with a warning.

### BanditPolicy  *(LinUCB)*
- Stateful: maintains A matrix and b vector per action dimension.
- `select(context_vector, candidates) → IndexCandidate`
- `update(context_vector, action_id, reward) → None`
- State is persisted to PostgreSQL via DecisionRepository between restarts.

### ShadowDatabaseManager
- Manages a Docker container running a separate PostgreSQL instance.
- Clones the production schema (structure only, no PII data by default).
- Applies a candidate index, runs benchmarks, removes the index.
- Provides an async context manager: `async with shadow.session() as conn: ...`

### BenchmarkRunner
- Executes the slow query N times against the shadow database.
- Collects `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` output.
- Returns `BenchmarkResult`: p50/p95 latency, planning time, block hits.

### RewardCalculator
- Pure function: `compute(before: BenchmarkResult, after: BenchmarkResult) → float`
- Reward ∈ [-1.0, 1.0].
- See `docs/REWARD_SPEC.md` for formula.

### SafetyGate
- Deterministic rule engine (no AI involvement).
- Checks: SQL validity, index type whitelist, table whitelist, write regression,
  improvement threshold, duplicate detection.
- Returns `SafetyDecision(approved: bool, reason: str)`.

### DeploymentManager
- Executes only `CREATE INDEX CONCURRENTLY` on production.
- Validates the SQL one final time immediately before execution.
- Writes a deployment record to DecisionRepository.

### DecisionRepository
- PostgreSQL-backed storage for all decisions, experiments, and bandit state.
- Provides query interfaces used by ObservabilityService.

### ObservabilityService
- Exposes metrics via FastAPI endpoints consumed by the Streamlit dashboard.
- Publishes events to a Redis pub/sub channel for real-time updates.

---

## Data Flow (Happy Path)

```
1.  LogMonitor detects slow query  → emits SlowQueryEvent
2.  JobQueue stores OptimizationJob
3.  AgentWorker dequeues job
4.  LocalLogParser parses raw log  → ParsedQuery
5.  SchemaInspector reads schema   → TableSchema
6.  ContextBuilder assembles       → OptimizationContext
7.  CandidateGenerator calls Gemini → [IndexCandidate, ...]
8.  BanditPolicy.select()          → chosen IndexCandidate
9.  ShadowDatabaseManager clones schema
10. BenchmarkRunner runs baseline
11. ShadowDatabaseManager applies candidate index
12. BenchmarkRunner runs experiment
13. RewardCalculator.compute()     → reward float
14. BanditPolicy.update()          → bandit learns
15. SafetyGate.evaluate()          → SafetyDecision
16. if approved: DeploymentManager.deploy()
17. DecisionRepository records outcome
18. ObservabilityService publishes event
```

---

## Failure Paths

See `docs/FAILURE_LOG.md` for known failure modes and recovery strategies.

---

## Interface Contracts

All interfaces are defined as Python `Protocol` classes in `app/core/interfaces.py`.
Implementations live in their respective sub-packages and are wired together in
`app/core/container.py` (dependency injection).

---

## Technology Stack

| Layer | Technology |
|---|---|
| API | FastAPI |
| Dashboard | Streamlit |
| Queue | Redis Streams |
| Primary DB | PostgreSQL |
| Shadow DB | PostgreSQL (Docker) |
| Local AI | Ollama + Qwen2.5-Coder 3B |
| Cloud AI | Google Gemini Flash |
| ORM | SQLAlchemy (async) |
| Validation | Pydantic v2 |
| Containers | Docker Compose |
| Bandit | LinUCB (custom NumPy impl) |

---

## Deployment Topology

```
docker-compose up
├── app            (FastAPI + AgentWorker)
├── dashboard      (Streamlit)
├── redis          (Queue + Cache + Pub/Sub)
├── db_primary     (Target PostgreSQL — demo seed)
├── db_shadow      (Shadow PostgreSQL — experiments)
└── ollama         (Qwen2.5-Coder 3B)
```
