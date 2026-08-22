# DBAutonomy Final Demo

## 1. Problem
Modern PostgreSQL databases require constant vigilance from senior DBAs to detect slow queries and optimize them with appropriate indexes. 
However, indexing is risky: a poorly constructed index can severely impact write performance or consume excessive disk space.
Most AI tools today either act as blind query generators (which are unsafe to run against production) or rule-based linters (which lack contextual intelligence).

## 2. Solution
**DBAutonomy** is a completely autonomous "AI Junior DBA". 
It automatically detects slow queries, utilizes two distinct AI models to generate multiple optimization strategies, and crucially, *tests them experimentally* in an isolated shadow database. 
It then evaluates the empirical results using a LinUCB contextual bandit algorithm, passes the best candidate through a deterministic safety gate, and deploys it only if proven effective.

## 3. Architecture
```
Slow Query → Qwen (parse) → Gemini (candidates) → LinUCB (select)
    → Shadow DB (experiment) → Reward → Safety Gate → Deploy/Reject
```

## 4. Why two AI models?
- **Qwen2.5-Coder 3B (Local)**: Highly efficient for fast, structured parsing of PostgreSQL log outputs into precise JSON metadata (table name, query type, where clauses) without latency penalties or token costs.
- **Gemini Flash (Remote)**: Exceptional at reasoning and generating diverse, complex SQL index candidates based on the parsed context and schema structure.

## 5. Why contextual bandit?
Traditional AI tools output one "best" answer. DBAutonomy generates *multiple* candidates. The LinUCB contextual bandit allows the agent to empirically learn which types of indexes (e.g., BTREE vs GIN) work best for specific query patterns over time by balancing exploration (trying new index types) and exploitation (using proven index types).

## 6. Why shadow database?
AI hallucinated SQL can break production schemas or lock critical tables. The shadow database provides a perfectly isolated, identical schema filled with up to 5,000,000 synthetic rows where indexes can be built and tested for performance latency without ever touching production data.

## 7. Why reward?
We use a calculated reward function (`0.8 * Latency Improvement - 0.2 * Write Regression`). This forces the AI to consider the holistic impact of an index, penalizing candidates that speed up reads but ruin write speeds.

## 8. Why deterministic safety?
AI cannot be trusted implicitly with production access. The SafetyGate ensures no destructive commands (`DROP`, `DELETE`, `ALTER`) can ever slip through. It acts as an unbreakable firewall, only allowing structural `CREATE INDEX CONCURRENTLY` commands.

## 9. Example successful run
- **Detected**: `SELECT * FROM orders WHERE customer_id = 42;`
- **Parsed**: Qwen identifies table `orders`, column `customer_id`.
- **Candidates**: Gemini generates `CREATE INDEX CONCURRENTLY ... ON orders(customer_id);`.
- **Selected**: LinUCB selects the BTREE candidate.
- **Shadow Test**: Baseline: 109ms. After index: 12ms.
- **Reward**: High positive reward.
- **Safety Gate**: Passed.
- **Deployment**: `CREATE INDEX CONCURRENTLY ...` applied to Production!

## 10. Example rejected run
- **Detected**: `SELECT * FROM events WHERE ...`
- **Candidates**: Gemini generates an index that takes 1GB of storage and causes a 25% write regression during shadow testing.
- **Reward**: Negative reward due to severe write penalty.
- **Safety Gate**: Rejected (Exceeds maximum write regression limit).
- **Result**: Production remains untouched and safe.

## 11. AWS Deployment Architecture
Designed for the Free Tier:
- **Laptop (Developer)**: Runs Ollama/Qwen locally (to avoid EC2 RAM exhaustion).
- **AWS EC2 (t2/t3.micro)**: Hosts the DBAutonomy FastAPI backend, Worker, Dashboard, Redis, and Shadow PostgreSQL database.
- **AWS RDS (db.t3.micro)**: Hosts the Primary PostgreSQL database.

## 12. Limitations
- Synthetic data generation in the shadow database assumes a uniform/random distribution which may not exactly match the data skew of production data.
- The contextual bandit requires multiple observations to converge on the optimal strategy.

## 13. Future Improvements
- Implement automated schema migrations.
- Support composite indexes based on JOIN combinations.
- Evolve the reward function to consider index size and total storage costs dynamically.
