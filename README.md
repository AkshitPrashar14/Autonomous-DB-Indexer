# Autonomous Database Indexer

"An AI Junior DBA that detects slow queries, generates index candidates, experimentally tests them in a shadow database, evaluates the result using a contextual bandit and deterministic safety rules, and deploys successful indexes."

---

## Architecture

```
Slow Query → Qwen (parse) → Gemini (candidates) → LinUCB (select)
    → Shadow DB (experiment) → Reward → Safety Gate → Deploy/Reject
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full diagram and component
descriptions.

---

## Quick Start

### Prerequisites
- Docker Desktop
- Ollama installed (pulls `qwen2.5-coder:3b` automatically on first run)
- A Gemini API key

### 1. Configure
```bash
cp .env.example .env
# Edit .env — set GEMINI_API_KEY and SAFETY_ALLOWED_TABLES
```

### 2. Start all services
```bash
docker-compose up
```

*(First run downloads the Ollama model — allow ~5 minutes)*

### 3. Seed demo database
```bash
python scripts/seed_demo_db.py
```

### 4. Open the dashboard
- **Dashboard**: http://localhost:8501
- **API Docs**: http://localhost:8000/docs

---

## Component Overview

| Component | Purpose | AI? |
|---|---|---|
| LogMonitor | Detect slow queries from `pg_stat_statements` | No |
| LocalLogParser | Parse log lines → structured JSON | **Qwen2.5-Coder 3B** |
| SchemaInspector | Read-only schema + caching | No |
| ContextBuilder | Assemble context for Gemini | No |
| CandidateGenerator | Generate index candidates | **Gemini Flash** |
| BanditPolicy | Select which candidate to test (LinUCB) | No |
| ShadowDatabaseManager | Isolated experiment environment | No |
| BenchmarkRunner | EXPLAIN ANALYZE measurements | No |
| RewardCalculator | Compute empirical reward | No |
| SafetyGate | 8-stage deterministic rule engine | No |
| DeploymentManager | CREATE INDEX CONCURRENTLY on production | No |
| DecisionRepository | Persist all decisions + bandit state | No |
| ObservabilityService | Real-time events + metrics | No |

---

## Safety

- **LLMs never execute production SQL directly.**
- Every candidate passes 8 deterministic safety stages.
- Only `CREATE INDEX CONCURRENTLY IF NOT EXISTS` is deployable.
- Shadow database isolates all experiments from production.
- Any ambiguity → reject.

See [docs/SAFETY_SPEC.md](docs/SAFETY_SPEC.md) and [docs/RULES.md](docs/RULES.md).

---

## Bandit Algorithm

LinUCB (disjoint model). The bandit:
- Selects from Gemini's candidate list using Upper Confidence Bounds
- Updates A and b matrices from measured rewards
- Persists state between restarts

See [docs/BANDIT_SPEC.md](docs/BANDIT_SPEC.md).

---

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, components, data flow |
| [DECISIONS.md](docs/DECISIONS.md) | Architecture Decision Records |
| [RULES.md](docs/RULES.md) | Engineering invariants |
| [AGENT_SPEC.md](docs/AGENT_SPEC.md) | Agent pipeline specification |
| [BANDIT_SPEC.md](docs/BANDIT_SPEC.md) | LinUCB specification |
| [REWARD_SPEC.md](docs/REWARD_SPEC.md) | Reward formula |
| [SAFETY_SPEC.md](docs/SAFETY_SPEC.md) | Safety gate specification |
| [FAILURE_LOG.md](docs/FAILURE_LOG.md) | Known failure modes + recovery |
| [DEMO_PLAN.md](docs/DEMO_PLAN.md) | Hackathon demo script |
| [FINAL_DEMO.md](docs/FINAL_DEMO.md) | Final architecture, demo, and justification |

---

## Implementation Status

| Phase | Status | Description |
|---|---|---|
| Phase 0 — Foundation | ✅ Complete | Interfaces, models, exceptions, config, stubs |
| Phase 1 — Core AI | ✅ Complete | Full Qwen + Gemini integration |
| Phase 2 — Bandit + Shadow | ✅ Complete | LinUCB math, shadow DB, benchmarking |
| Phase 3 — Agent Loop | ✅ Complete | Full AgentWorker pipeline |
| Phase 4 — API + Dashboard | ✅ Complete | FastAPI routes, Streamlit panels |
| Phase 5 — Integration Testing | ✅ Complete | End-to-end tests, demo recording |
