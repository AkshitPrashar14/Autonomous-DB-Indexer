# DBAutonomy — Engineering Rules

These rules are INVARIANTS. They may not be overridden by configuration,
environment variables, or runtime flags.

---

## RULE-01: LLMs Never Execute Production SQL

An LLM-generated string must never be passed to a production database
connection executor without passing through SafetyGate first.

**Enforcement:** `DeploymentManager.__init__` accepts only a `SafetyDecision`
object. The method signature physically prevents bypassing the gate.

---

## RULE-02: Only Index Creation is Deployable

The only SQL operation that may reach `DeploymentManager.deploy()` is:

```
CREATE [UNIQUE] INDEX CONCURRENTLY <name> ON <table> (<columns>)
```

Regex: `^CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+\w+\s+ON\s+\w+\s*\(.*\)\s*;?\s*$`

Any deviation from this pattern results in immediate rejection.

---

## RULE-03: Shadow-First, Always

No experiment may run against the production database. All `BenchmarkRunner`
calls must receive a `ShadowDatabaseManager.session()` connection.

**Enforcement:** `BenchmarkRunner.__init__` accepts a `ShadowDatabaseManager`,
not a raw connection string.

---

## RULE-04: Measurable Improvement Required

A candidate may not be deployed unless `reward > MIN_REWARD_THRESHOLD`
(default: 0.05, i.e., ≥5% measured improvement).

---

## RULE-05: Write Regression Hard Cap

If write-path benchmarks degrade by more than `MAX_WRITE_REGRESSION`
(default: 10%), the candidate is rejected regardless of read improvement.

---

## RULE-06: No Silent Failures

Every exception in the AgentWorker pipeline must be:
1. Logged with full traceback to the ObservabilityService.
2. Written as a failure record to the DecisionRepository.
3. Either retried (if retryable) or permanently failed.

Swallowing exceptions is forbidden.

---

## RULE-07: Bandit State Must Be Persisted

After every `BanditPolicy.update()` call, the bandit state (A matrix, b vector)
must be written to the DecisionRepository before the job is acknowledged in Redis.
This prevents learning loss on restart.

---

## RULE-08: Candidate Validation Before Bandit Selection

All candidates returned by `CandidateGenerator` must be validated by `SafetyGate`
(structural check only, not deployment approval) before being presented to the
bandit. Invalid candidates are dropped silently after logging.

---

## RULE-09: Schema Cache May Be Stale — Always Verify

Before deploying an index, `SchemaInspector` must re-query the production
database to confirm the index does not already exist. Stale cache data must
not cause duplicate index creation.

---

## RULE-10: AI Output Is Assumed Malformed Until Proven Otherwise

All JSON output from Ollama and Gemini must be parsed with `model_validate()`
(Pydantic strict mode). Validation errors are caught and logged; the component
falls back to its safe default or raises `CandidateGenerationError`.
