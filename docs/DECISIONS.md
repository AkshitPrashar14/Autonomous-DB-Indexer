# DBAutonomy — Architecture Decision Records

## ADR-001: Bandit Algorithm — LinUCB over Epsilon-Greedy

**Status:** Accepted  
**Date:** 2026-08-21

**Context:**  
We need a contextual bandit that genuinely uses query context (table sizes,
column cardinality, existing indexes, query patterns) to select index candidates.
A pure epsilon-greedy agent ignores context in its exploration step.

**Decision:**  
Implement LinUCB with a diagonal approximation for the A matrix per action.
Each index candidate is an "action". The context vector encodes features derived
from `OptimizationContext`. The alpha hyperparameter controls exploration width.

**Consequences:**  
- LinUCB is interpretable: we can inspect A⁻¹b per action to understand what
  the bandit has learned.
- Diagonal approximation reduces memory from O(d²·k) to O(d·k) where d = context
  dimension and k = candidate count.
- Requires persistent state. Solved by serialising A and b to the DecisionRepository
  between restarts.

---

## ADR-002: Two-Model Architecture — Qwen locally + Gemini remotely

**Status:** Accepted  
**Date:** 2026-08-21

**Context:**  
The hackathon requires two genuinely different models. Log parsing is a pattern-
extraction task that benefits from a code-specialist model running locally (no
external latency, no token cost). Candidate generation requires broader SQL
knowledge and complex multi-step reasoning.

**Decision:**  
- Qwen2.5-Coder 3B via Ollama: parse raw PostgreSQL log lines → structured JSON.
- Gemini Flash via Google API: given structured context, generate ranked candidates.

**Consequences:**  
- Local model runs even without internet connectivity.
- Gemini Flash is rate-limited; implement exponential backoff with a 3-attempt cap.
- The two models are fully isolated — neither sees the other's output directly.

---

## ADR-003: Redis Streams over Celery

**Status:** Accepted  
**Date:** 2026-08-21

**Context:**  
The system needs a durable job queue with retry semantics and consumer group
support. Celery adds significant complexity (broker + result backend + worker
process management). Redis Streams natively support consumer groups, pending
entry lists, and acknowledgements.

**Decision:**  
Use Redis Streams directly via `redis-py` async client. Two streams:
`jobs:pending` (new jobs) and `jobs:retry` (failed, retryable jobs).

**Consequences:**  
- Fewer moving parts. No Celery broker, no Flower.
- We manage retry logic ourselves (simpler than it sounds: read from pending,
  re-enqueue on failure, cap at MAX_RETRIES).
- Redis becomes a single point of failure — acceptable for a hackathon demo;
  mitigated by Sentinel or Cluster for production.

---

## ADR-004: Shadow Database — Docker PostgreSQL with Schema-Only Clone

**Status:** Accepted  
**Date:** 2026-08-21

**Context:**  
Experiments must not run on production. We need a realistic schema but do not
want to copy PII data.

**Decision:**  
- `ShadowDatabaseManager` uses `pg_dump --schema-only` to extract DDL.
- Applies DDL to the shadow container.
- Populates with synthetic benchmark data using Faker / pg_bench patterns.
- After each experiment, drops candidate index (does not wipe the database).

**Consequences:**  
- Schema-only clone means statistics may differ from production. We compensate
  by running `ANALYZE` after data population.
- Shadow container must be running before the first job is processed.
  `AgentWorker` performs a health-check on startup and raises `ShadowUnavailable`
  if shadow is down (job is retried, not silently skipped).

---

## ADR-005: Safety Gate is 100% Deterministic

**Status:** Accepted  
**Date:** 2026-08-21

**Context:**  
All LLM output is untrusted. Using an LLM to validate another LLM's output
creates a circular trust problem.

**Decision:**  
`SafetyGate` is a pure Python rule engine with no AI components. Rules are
encoded as explicit checks in `app/evaluation/safety_gate.py`. Changes to rules
require a code change and a review — not a prompt change.

**Consequences:**  
- False negatives (overly cautious) are acceptable; false positives (approving
  unsafe candidates) are not.
- Default stance: reject on any ambiguity.

---

## ADR-006: Context Vector Feature Engineering

**Status:** Proposed  
**Date:** 2026-08-21

**Context:**  
LinUCB requires a fixed-dimension context vector. The features must capture
enough signal for the bandit to learn column-type and query-pattern preferences.

**Proposed features (d = 20):**
- Query duration (normalised)
- Table row count (log-scaled)
- Table column count
- Existing index count on the table
- Number of WHERE predicates
- Number of JOIN clauses
- Number of ORDER BY columns
- Query type one-hot (SELECT / UPDATE / DELETE) [3 dims]
- Index type one-hot (btree / hash / gin / gist / brin) [5 dims]
- Column data type one-hot (numeric / text / timestamp / boolean / other) [5 dims]
- Estimated cardinality ratio (column distinct / total rows)

**Status:** Needs empirical validation after first experiments.

---

## ADR-007: Deployment — Only CREATE INDEX CONCURRENTLY

**Status:** Accepted  
**Date:** 2026-08-21

**Context:**  
The system is scoped to index optimisation for v1. Dropping indexes, altering
tables, rewriting queries are out of scope.

**Decision:**  
`DeploymentManager` only accepts SQL strings that match the pattern:
`CREATE (UNIQUE )?INDEX CONCURRENTLY ...`

Any other SQL is rejected at the gate, regardless of safety score.

---

## ADR-008: Unresolved — Write Regression Threshold

**Status:** Open  
**Date:** 2026-08-21

**Context:**  
Adding an index improves SELECT performance but degrades INSERT/UPDATE/DELETE
throughput. We need a maximum acceptable write regression percentage.

**Options:**
- A: Fixed 10% write regression limit (simple, conservative)
- B: Configurable per-table (complex, flexible)
- C: Omit write regression check in v1 (fast, unsafe)

**Decision:** Pending. Defaulting to Option A (10%) for hackathon demo.

---

## ADR-009: Unresolved — Bandit Cold Start

**Status:** Open  
**Date:** 2026-08-21

**Context:**  
On first startup, the bandit has no learned history. With LinUCB, alpha drives
exploration, but all actions are equally unknown.

**Options:**
- A: Start with uniform random selection for first N jobs
- B: Use Gemini's ranking as a prior (first-action = Gemini's top candidate)
- C: Pre-seed A and b with synthetic prior observations

**Decision:** Pending. Leaning toward Option B as it uses existing infrastructure.
