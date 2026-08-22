# DBAutonomy — Safety Specification

## Philosophy

The safety gate is the **last line of defence** before production is touched.
It is deliberately conservative, deterministic, and AI-free.

The gate answers one question: **"Is this specific SQL string safe to execute
as a CREATE INDEX CONCURRENTLY on this specific production database right now?"**

If the answer is anything other than a confident YES, the answer is NO.

---

## Gate Stages

Safety evaluation is multi-stage. A candidate must pass **all** stages.

### Stage 1: SQL Syntax Validation

```python
def validate_sql_syntax(sql: str) -> bool:
```

- Uses `sqlparse` to parse the SQL string.
- Verifies the statement type is `CREATE INDEX`.
- Rejects any SQL containing: `DROP`, `DELETE`, `UPDATE`, `INSERT`,
  `TRUNCATE`, `ALTER`, `GRANT`, `REVOKE`, `EXECUTE`, `CALL`, `DO`,
  semicolons after the first statement (multi-statement injection),
  comments that could mask payloads (`--`, `/* */`).

### Stage 2: Structural Pattern Match

```python
def validate_pattern(sql: str) -> bool:
```

Regex:
```python
ALLOWED_PATTERN = re.compile(
    r"^CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+"
    r"IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+\w+(\.\w+)?\s*"
    r"\([\w\s,]+\)"
    r"(\s+WHERE\s+.+)?"
    r"\s*;?\s*$",
    re.IGNORECASE,
)
```

The `IF NOT EXISTS` clause is **required** in all deployed SQL to prevent
duplicate index errors.

### Stage 3: Table Whitelist

```python
def validate_table_allowed(table_name: str) -> bool:
```

- Production database has a set of tables explicitly allowed for indexing.
- Configured via `ALLOWED_TABLES` environment variable (comma-separated).
- If `ALLOWED_TABLES` is empty, **all tables are forbidden** (fail-safe default).

### Stage 4: Index Name Convention

```python
def validate_index_name(index_name: str) -> bool:
```

Format: `dbautonomy_{table}_{columns}_{type}_{timestamp}`

This prevents naming conflicts with manually managed indexes and makes
auto-created indexes auditable.

### Stage 5: Duplicate Detection

```python
def check_no_duplicate(table: str, columns: list[str], index_type: str) -> bool:
```

Queries `pg_indexes` (live, not cached) on the production database to check
if a functionally equivalent index already exists. Rejects if found.

### Stage 6: Improvement Threshold

```python
def validate_reward(reward: float) -> bool:
    return reward >= MIN_REWARD_THRESHOLD  # default 0.05
```

### Stage 7: Write Regression Limit

```python
def validate_write_regression(baseline: BenchmarkResult,
                               experiment: BenchmarkResult) -> bool:
    regression = (experiment.write_p50_ms - baseline.write_p50_ms) \
                 / max(1.0, baseline.write_p50_ms)
    return regression <= MAX_WRITE_REGRESSION  # default 0.10
```

### Stage 8: Bandit Confidence Check

```python
def validate_bandit_confidence(uncertainty: float) -> bool:
    return uncertainty <= MAX_BANDIT_UNCERTAINTY  # default 2.0
```

High uncertainty (large UCB exploration term) means the bandit is guessing.
We can optionally skip deployment for very uncertain selections.
*Currently disabled by default in v1 — uncertainty is logged but not a blocker.*

---

## Output

```python
@dataclass
class SafetyDecision:
    approved: bool
    reason: str                    # human-readable explanation
    stages_passed: list[str]       # e.g. ["syntax", "pattern", "table_whitelist"]
    stages_failed: list[str]       # e.g. ["write_regression"]
    reward: float
    risk_score: float              # 0.0 (safe) – 1.0 (dangerous), informational
```

---

## Audit Trail

Every `SafetyDecision` is written to the `safety_decisions` table regardless
of outcome. This table is append-only (no UPDATE, no DELETE).

---

## What the Gate Does NOT Do

- It does not call any AI model.
- It does not use heuristics or fuzzy matching.
- It does not consider the "intent" of the SQL.
- It does not trust `IndexCandidate.explanation` from Gemini.

---

## Configuration

```
SAFETY_MIN_REWARD_THRESHOLD=0.05
SAFETY_MAX_WRITE_REGRESSION=0.10
SAFETY_ALLOWED_TABLES=orders,products,users,events
SAFETY_INDEX_TYPE_WHITELIST=btree,hash,gin,gist,brin
SAFETY_MAX_BANDIT_UNCERTAINTY=2.0
SAFETY_REQUIRE_IF_NOT_EXISTS=true
```
