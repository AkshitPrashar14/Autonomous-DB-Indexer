# DBAutonomy — Agent Specification

## Purpose

The Agent is the autonomous decision-making unit of DBAutonomy. It is a
long-running coroutine that consumes optimization jobs, orchestrates the
full pipeline, and learns from every experiment.

---

## Lifecycle

```
START
  │
  ├─► health_check_all_dependencies()
  │     raises StartupError if Redis / shadow DB / Ollama / Gemini unreachable
  │
  ├─► load_bandit_state() from DecisionRepository
  │
  └─► consume_loop():
        for each job in JobQueue:
          try:
            run_pipeline(job)
            ack_job(job)
          except RetryableError as e:
            log(e); requeue(job)
          except FatalError as e:
            log(e); fail_job(job)
          except Exception as e:
            log(e); fail_job(job)  # unknown errors treated as fatal
```

---

## Pipeline Steps

### Step 1: Parse
```python
parsed: ParsedQuery = await local_log_parser.parse(job.raw_log)
```
- Timeout: 30s
- Fallback: regex extractor if Ollama times out or returns invalid JSON

### Step 2: Inspect Schema
```python
schema: TableSchema = await schema_inspector.inspect(parsed.table_name)
```
- Uses cached result if < 5 min old
- Raises `SchemaNotFoundError` if table doesn't exist (job → fatal)

### Step 3: Build Context
```python
context: OptimizationContext = context_builder.build(parsed, schema, history)
```

### Step 4: Generate Candidates
```python
candidates: list[IndexCandidate] = await candidate_generator.generate(context)
```
- Timeout: 60s
- Minimum 1 candidate required; raises `NoCandidatesError` if empty after validation

### Step 5: Validate Candidates (Pre-Bandit)
```python
valid_candidates = [c for c in candidates if safety_gate.structural_check(c)]
```

### Step 6: Bandit Selection
```python
context_vector = feature_extractor.extract(context)
chosen: IndexCandidate = bandit.select(context_vector, valid_candidates)
```

### Step 7: Shadow Experiment
```python
async with shadow_db.session() as conn:
    baseline: BenchmarkResult = await benchmark_runner.run(conn, parsed.sql)
    await shadow_db.apply_index(conn, chosen)
    experiment: BenchmarkResult = await benchmark_runner.run(conn, parsed.sql)
    await shadow_db.remove_index(conn, chosen)
```

### Step 8: Compute Reward
```python
reward: float = reward_calculator.compute(baseline, experiment)
```

### Step 9: Update Bandit
```python
bandit.update(context_vector, chosen.id, reward)
await decision_repo.save_bandit_state(bandit.state)
```

### Step 10: Safety Gate
```python
decision: SafetyDecision = safety_gate.evaluate(chosen, baseline, experiment, reward)
```

### Step 11: Deploy or Reject
```python
if decision.approved:
    await deployment_manager.deploy(chosen, decision)
    status = "deployed"
else:
    status = "rejected"
```

### Step 12: Record
```python
await decision_repo.save_decision(OptimizationRecord(
    job_id=job.id,
    candidate=chosen,
    baseline=baseline,
    experiment=experiment,
    reward=reward,
    decision=decision,
    status=status,
))
await observability.publish(status, chosen, reward)
```

---

## Error Classification

| Exception | Category | Recovery |
|---|---|---|
| `OllamaTimeoutError` | Retryable | Retry up to 3× with regex fallback |
| `GeminiRateLimitError` | Retryable | Exponential backoff, max 3 attempts |
| `NoCandidatesError` | Fatal | Record, skip |
| `ShadowUnavailableError` | Retryable | Retry after 60s |
| `SchemaNotFoundError` | Fatal | Record, skip |
| `BenchmarkTimeoutError` | Retryable | Retry once |
| `SafetyGateError` | Fatal | Record rejection |
| `DeploymentError` | Fatal (alert) | Record, alert operator |
| Unknown | Fatal | Record, alert operator |

---

## Concurrency Model

- Multiple `AgentWorker` instances may run simultaneously.
- Each worker holds its own bandit instance; state is synced via
  `DecisionRepository` at load and after every update.
- Redis consumer groups ensure each job is processed by exactly one worker.

---

## Configuration

All agent configuration is read from environment variables:

```
AGENT_MAX_RETRIES=3
AGENT_JOB_TIMEOUT_S=300
AGENT_BANDIT_ALPHA=1.0
AGENT_MIN_REWARD_THRESHOLD=0.05
AGENT_MAX_WRITE_REGRESSION=0.10
AGENT_BENCHMARK_ITERATIONS=10
AGENT_SCHEMA_CACHE_TTL_S=300
```
