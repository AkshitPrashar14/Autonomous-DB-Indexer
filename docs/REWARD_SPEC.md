# DBAutonomy — Reward Specification

## Purpose

The reward signal is the feedback that the contextual bandit learns from.
It must:
1. Reflect genuine measured improvement (not LLM opinion)
2. Penalise write regressions
3. Be bounded in [-1.0, 1.0] for numerical stability
4. Be computed from `EXPLAIN ANALYZE` output — never from LLM text

---

## Inputs

```python
@dataclass
class BenchmarkResult:
    query_p50_ms: float        # median execution time
    query_p95_ms: float        # 95th percentile execution time
    planning_time_ms: float    # query planning time
    shared_blks_hit: int       # buffer cache hits
    shared_blks_read: int      # disk reads
    write_p50_ms: float        # median write op time (INSERT/UPDATE benchmark)
    n_iterations: int          # how many times the query was run
```

---

## Formula

### Primary: Latency Improvement Ratio

```
latency_improvement = (baseline.query_p50_ms - experiment.query_p50_ms)
                      / baseline.query_p50_ms

# Clipped to [-1.0, 1.0]
latency_improvement = clip(latency_improvement, -1.0, 1.0)
```

### Write Regression Penalty

```
write_regression = (experiment.write_p50_ms - baseline.write_p50_ms)
                   / baseline.write_p50_ms

write_penalty = max(0.0, write_regression - MAX_WRITE_REGRESSION)
# write_penalty ∈ [0.0, ∞) — clipped at 1.0 for the formula
write_penalty = min(write_penalty, 1.0)
```

### Cache Efficiency Bonus

```
baseline_cache_ratio = baseline.shared_blks_hit /
                       max(1, baseline.shared_blks_hit + baseline.shared_blks_read)

experiment_cache_ratio = experiment.shared_blks_hit /
                         max(1, experiment.shared_blks_hit + experiment.shared_blks_read)

cache_bonus = (experiment_cache_ratio - baseline_cache_ratio) * 0.1
# Contribution is capped at ±0.1
```

### Final Reward

```python
reward = (
    WEIGHT_LATENCY * latency_improvement
    - WEIGHT_WRITE  * write_penalty
    + cache_bonus
)

reward = clip(reward, -1.0, 1.0)
```

### Default Weights

| Weight | Default Value |
|---|---|
| `WEIGHT_LATENCY` | 0.80 |
| `WEIGHT_WRITE` | 0.20 |
| `MAX_WRITE_REGRESSION` | 0.10 (10%) |

---

## Reward Interpretation

| Range | Meaning | Action |
|---|---|---|
| > 0.30 | Strong improvement | Deploy |
| 0.05 – 0.30 | Moderate improvement | Deploy (if safety passes) |
| 0.0 – 0.05 | Negligible improvement | Reject |
| < 0.0 | Regression | Reject + penalise bandit |

Minimum deployment threshold: `MIN_REWARD_THRESHOLD = 0.05`

---

## Write Benchmark Protocol

To measure write impact, `BenchmarkRunner` executes a synthetic workload
against the same table:

```sql
-- Parameterised INSERT matching the table schema
INSERT INTO {table} ({cols}) VALUES ({synthetic_values})
```

Run 100× before and after index application. p50 is used to compute
`write_p50_ms`.

This write benchmark is only run when `BENCHMARK_WRITE_IMPACT=true`
(default: true).

---

## Baseline Collection

Baseline is always measured on the shadow database **before** the candidate
index is applied. This ensures:
- Same data distribution
- Same PostgreSQL statistics
- No contamination from prior experiments

`ANALYZE` is run after data population and before each baseline measurement.

---

## Implementation

```python
# app/evaluation/reward_calculator.py
def compute(baseline: BenchmarkResult, experiment: BenchmarkResult) -> float:
    ...
```

This function is a **pure function** — no side effects, no I/O, no AI calls.
It can be unit-tested in isolation with synthetic `BenchmarkResult` objects.
