# DBAutonomy — Demo Plan

## Demo Goal

Show an end-to-end live cycle where:
1. A slow query is detected.
2. Two AI models cooperate to generate candidates.
3. The contextual bandit selects a candidate.
4. The shadow database measures the improvement.
5. The safety gate approves (or rejects).
6. The index is deployed to the demo database.
7. The dashboard shows the reward and decision history in real time.

Total demo runtime: **~5 minutes**.

---

## Demo Environment Setup

### Prerequisites
- Docker Desktop running
- Ollama running with `qwen2.5-coder:3b` pulled
- `GEMINI_API_KEY` set in `.env`
- All containers healthy: `docker-compose up`

### Seed Database
`scripts/seed_demo_db.py` creates a demo PostgreSQL database with:
- `orders` table (1M rows, no indexes on frequently-queried columns)
- `products` table (100K rows)
- `events` table (5M rows, timestamp-heavy)

```bash
python scripts/seed_demo_db.py
```

### Inject Slow Queries
`scripts/generate_slow_queries.py` runs queries deliberately without indexes
to populate `pg_stat_statements` with slow-query candidates.

```bash
python scripts/generate_slow_queries.py --duration 60
```

---

## Demo Script

### Act 1: Show the Problem (30 seconds)
1. Open pgAdmin or `psql` on the demo database.
2. Run: `SELECT * FROM orders WHERE customer_id = 12345 AND status = 'pending';`
3. Show: query takes ~2.4 seconds (no index on `customer_id`, `status`).

### Act 2: Start the Agent (30 seconds)
```bash
docker-compose up
```
- Show the FastAPI docs at `http://localhost:8000/docs`.
- Show the Streamlit dashboard at `http://localhost:8501`.

### Act 3: Trigger the Loop (2 minutes)
1. In dashboard, click "Inject Slow Query" (calls POST `/api/jobs/inject`).
2. Watch the dashboard's "Agent Activity" panel:
   - **Log Parser**: Qwen parses the log → `ParsedQuery` shown
   - **Candidate Generator**: Gemini returns 3 candidates
   - **Bandit Selection**: LinUCB selects candidate #2
   - **Shadow Experiment**: Baseline 2400ms → With index 120ms
   - **Reward**: 0.92
   - **Safety Gate**: APPROVED
   - **Deployment**: `CREATE INDEX CONCURRENTLY dbautonomy_orders_customer_id_status_btree_...`

### Act 4: Verify Improvement (1 minute)
1. Re-run the same slow query in psql.
2. Show: query now takes ~45ms.
3. Dashboard shows cumulative reward chart trending up.

### Act 5: Bandit Learning (1 minute)
1. Inject 3 more slow queries (different tables).
2. Show the bandit's action distribution shifting toward btree indexes on
   FK columns (what it has learned works best).
3. Show the `bandit_state` in the dashboard's "Model State" tab.

---

## Curveball Readiness

If the judges add a curveball (likely options):

| Curveball | Response |
|---|---|
| "Show what happens when Gemini is down" | Set `GEMINI_API_KEY=invalid`, trigger job, show retry + failure record |
| "Show what happens when an unsafe index is proposed" | Use `scripts/inject_bad_candidate.py` — SafetyGate rejects it |
| "Can you add partial index support?" | `IndexCandidate` already has `where_clause` field; CandidateGenerator and SafetyGate support it |
| "Show the bandit learning over time" | Dashboard reward chart + action distribution heatmap |
| "What if shadow DB is down?" | Stop `db_shadow` container, trigger job, show retry queue accumulating, restart container, show queue draining |

---

## Dashboard Panels

| Panel | Data Source |
|---|---|
| Agent Activity Feed | Redis pub/sub live stream |
| Reward History Chart | `optimization_records` table |
| Bandit Action Distribution | `bandit_state` table |
| Safety Gate Decisions | `safety_decisions` table |
| Deployed Indexes | `deployed_indexes` table |
| System Health | FastAPI `/health` endpoint |

---

## Fallback Plan

If any component fails during the demo:
1. The dashboard shows the last 20 decisions from the database (pre-recorded).
2. Run `scripts/replay_demo.py` to replay a recorded session at 2× speed.
3. The pre-recorded session was saved during integration testing.
