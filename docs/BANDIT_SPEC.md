# DBAutonomy — Bandit Specification

## Algorithm: LinUCB (Disjoint Model)

### Rationale

LinUCB is a contextual bandit algorithm that models the expected reward of
each action as a linear function of a context vector. The "disjoint" variant
maintains a separate linear model per action (per index candidate type).

This is appropriate because:
- Different index types (btree, hash, gin) have genuinely different reward
  characteristics.
- The relationship between query features and index benefit is approximately
  linear in the feature space we define.
- LinUCB is interpretable: we can inspect the learned weights.

### Mathematical Formulation

For each action `a` (index candidate type/column combination):

```
A_a  ∈ ℝ^{d×d}   (initialized to I_d)
b_a  ∈ ℝ^d        (initialized to 0)

θ̂_a = A_a^{-1} b_a                          (estimated weights)

UCB_a(x) = θ̂_a^T x + α √(x^T A_a^{-1} x)   (upper confidence bound)

Action selection: a* = argmax_a UCB_a(x)

After observing reward r:
  A_a ← A_a + x x^T
  b_a ← b_a + r x
```

Where:
- `x ∈ ℝ^d` is the context vector
- `α > 0` controls exploration (higher = more exploration)
- `d` is the context dimension (see ADR-006, d = 20)

### Action Space

In DBAutonomy, an "action" is not a single global index name but a
**candidate fingerprint** — a hash of (table, columns, index_type).

This means the bandit generalises across queries on the same table/column
combination, not per-query.

### Cold Start

On first startup (no history), A_a = I and b_a = 0, so θ̂_a = 0 and UCB
is dominated by the exploration term `α √(x^T A_a^{-1} x)`. The bandit
selects approximately uniformly at random until enough observations exist.

As a pragmatic improvement, the first selection uses Gemini's ranked order
as a tiebreaker (ADR-009 Option B).

### State Persistence

After every `update()` call, the following are serialised to JSON and written
to `bandit_state` table in the DecisionRepository:

```json
{
  "version": 1,
  "alpha": 1.0,
  "d": 20,
  "actions": {
    "<fingerprint>": {
      "A": [[...], ...],
      "b": [...]
    }
  },
  "total_updates": 42
}
```

On restart, state is loaded before the first job is processed.

### Context Vector (d = 20)

| Index | Feature | Notes |
|---|---|---|
| 0 | query_duration_norm | duration / 30000ms |
| 1 | table_row_count_log | log10(rows + 1) / 10 |
| 2 | table_col_count_norm | cols / 100 |
| 3 | existing_index_count | count / 20 |
| 4 | where_predicate_count | count / 10 |
| 5 | join_clause_count | count / 5 |
| 6 | order_by_col_count | count / 5 |
| 7 | query_type_select | one-hot |
| 8 | query_type_update | one-hot |
| 9 | query_type_delete | one-hot |
| 10 | idx_type_btree | one-hot |
| 11 | idx_type_hash | one-hot |
| 12 | idx_type_gin | one-hot |
| 13 | idx_type_gist | one-hot |
| 14 | idx_type_brin | one-hot |
| 15 | col_type_numeric | one-hot |
| 16 | col_type_text | one-hot |
| 17 | col_type_timestamp | one-hot |
| 18 | col_type_boolean | one-hot |
| 19 | cardinality_ratio | distinct / total_rows |

### Hyperparameters

| Parameter | Default | Range | Effect |
|---|---|---|---|
| `alpha` | 1.0 | 0.1 – 5.0 | Exploration width |
| `d` | 20 | fixed | Context dimension |
| `min_observations` | 3 | 1 – 20 | Min updates before exploitation |

### Evaluation

The bandit's learning progress is tracked via:
- **Cumulative reward** over time (should increase)
- **Action distribution** (should narrow toward high-reward candidates)
- **Regret proxy**: `max_possible_reward - actual_reward` per episode

These are exposed via `ObservabilityService` and displayed in the dashboard.

---

## What This Is NOT

- Not DQN / PPO / SAC / deep RL
- Not LLM-as-bandit (Gemini generates candidates; bandit selects among them)
- Not epsilon-greedy (UCB provides principled exploration without a fixed ε)
- Not a simulation — reward comes from real shadow-database benchmarks
