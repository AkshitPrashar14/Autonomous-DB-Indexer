# DBAutonomy — Failure Log

This file is a living document. Add a new entry for every failure mode
discovered during development or during the hackathon demo.

---

## Template

```
### FL-XXX: <Title>
**Discovered:** YYYY-MM-DD
**Severity:** Critical | High | Medium | Low
**Component:** <ComponentName>
**Symptom:** What the operator/user sees
**Root Cause:** Technical explanation
**Recovery:** How the system handles it automatically
**Manual Recovery:** What a human must do if auto-recovery fails
**Prevention:** Long-term fix or architectural improvement
**Status:** Open | Mitigated | Resolved
```

---

## Known Failure Modes

### FL-001: Ollama Timeout on Large Log Lines
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** Medium
**Component:** LocalLogParser
**Symptom:** Job stays in processing state, eventually times out after 30s.
**Root Cause:** Qwen2.5-Coder 3B can take >30s on very long log payloads
              (>4KB raw text) on CPU-only hardware.
**Recovery:** Automatic fallback to regex extractor. Job continues with
             degraded (but still valid) ParsedQuery.
**Manual Recovery:** None required.
**Prevention:** Truncate log input to 2KB before sending to Ollama.
**Status:** Mitigated (input truncation implemented in LocalLogParser).

---

### FL-002: Gemini Flash Rate Limit (429)
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** High
**Component:** CandidateGenerator
**Symptom:** `GeminiRateLimitError` raised; job enters retry queue.
**Root Cause:** Free-tier Gemini Flash has 15 RPM / 1M TPD limits.
**Recovery:** Exponential backoff: 2s, 4s, 8s. After 3 failures, job
             is permanently failed with reason "gemini_rate_limit".
**Manual Recovery:** Wait for quota reset (next minute / next day).
**Prevention:** Implement request-level caching: same `OptimizationContext`
               hash reuses cached candidates for 5 minutes.
**Status:** Mitigated (retry + caching planned in Phase 3).

---

### FL-003: Shadow Database Unavailable
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** High
**Component:** ShadowDatabaseManager
**Symptom:** `ShadowUnavailableError` on every job; queue accumulates.
**Root Cause:** Shadow Docker container stopped or OOM-killed.
**Recovery:** Job re-enqueued with 60s delay. AgentWorker retries 3×.
**Manual Recovery:** `docker-compose restart db_shadow`
**Prevention:** Health-check endpoint in FastAPI. Dashboard shows shadow DB status.
**Status:** Open.

---

### FL-004: Gemini Returns Invalid JSON
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** Medium
**Component:** CandidateGenerator
**Symptom:** `CandidateValidationError`; no candidates produced.
**Root Cause:** Gemini occasionally outputs markdown-wrapped JSON or
               truncated responses when the prompt is near the token limit.
**Recovery:** JSON extraction with regex fallback (strip markdown fences,
             find first `{...}` or `[...]`). If still invalid after
             extraction, job is permanently failed.
**Manual Recovery:** None; check dashboard for Gemini response logs.
**Prevention:** Reduce prompt size via ContextBuilder token budgeting.
               Use Gemini response schema enforcement (response_mime_type).
**Status:** Mitigated (response schema enforcement implemented).

---

### FL-005: Bandit State Corruption on Crash
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** High
**Component:** BanditPolicy / DecisionRepository
**Symptom:** On restart, bandit loads corrupt/partial state; may select
             poorly or crash with LinearAlgebraError.
**Root Cause:** Process killed between `bandit.update()` and
               `decision_repo.save_bandit_state()`.
**Recovery:** On load, validate A matrix (positive definiteness check).
             If invalid, reset to identity (cold start). Log warning.
**Manual Recovery:** Delete `bandit_state` row; system cold-starts.
**Prevention:** Wrap update + save in a database transaction.
**Status:** Open.

---

### FL-006: Duplicate Index Deployed
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** High
**Component:** SafetyGate / DeploymentManager
**Symptom:** `CREATE INDEX` fails with "relation already exists" error.
**Root Cause:** Race between two concurrent workers, or stale cache in
               Stage 5 of SafetyGate.
**Recovery:** `CREATE INDEX CONCURRENTLY IF NOT EXISTS` is idempotent.
             Error is caught, logged as INFO (not failure). Job succeeds.
**Manual Recovery:** None required.
**Prevention:** `IF NOT EXISTS` is mandated by RULE-02 and SAFETY_SPEC.
**Status:** Mitigated.

---

### FL-007: Production DB Connection Loss During Deployment
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** Critical
**Component:** DeploymentManager
**Symptom:** `CREATE INDEX CONCURRENTLY` starts but connection drops mid-way.
**Root Cause:** Network interruption or DB restart during index build.
**Recovery:** PostgreSQL cleans up invalid indexes automatically.
             DeploymentManager catches the exception and records status
             as `deployment_interrupted`. Dashboard alerts operator.
**Manual Recovery:** Check `pg_indexes` for invalid indexes:
             `SELECT * FROM pg_indexes WHERE indexname LIKE 'dbautonomy_%';`
             Drop any invalid ones manually.
**Prevention:** Monitor `pg_stat_progress_create_index` via ObservabilityService.
**Status:** Open.

---

### FL-008: Redis Stream Grows Unbounded
**Discovered:** 2026-08-21 (pre-emptive)
**Severity:** Medium
**Component:** JobQueue
**Symptom:** Redis memory usage grows continuously.
**Root Cause:** Old acknowledged messages not trimmed from stream.
**Recovery:** `XADD jobs:pending MAXLEN ~ 10000 ...` caps stream at 10K entries.
**Manual Recovery:** `XTRIM jobs:pending MAXLEN 1000`
**Prevention:** Use `MAXLEN` on all XADD calls. Configure Redis `maxmemory-policy`.
**Status:** Mitigated.
