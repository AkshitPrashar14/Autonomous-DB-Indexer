"""
DBAutonomy — AgentWorker (Phase 2 — Complete Implementation)

Implements the 12-step pipeline from docs/AGENT_SPEC.md.

State machine:
  DETECTED → PARSED → SCHEMA_READ → CONTEXT_BUILT
  → CANDIDATES_GENERATED → ACTION_SELECTED
  → SHADOW_TESTING → EVALUATED → SAFETY_CHECK
  → DEPLOYED | REJECTED

Error classification:
  RetryableError → re-enqueue (up to max_attempts)
  FatalError     → permanently fail job
  Unknown        → treat as fatal + publish alert

Architecture note:
  This worker runs as a separate process (scripts/run_worker.py).
  It DOES NOT run inside the FastAPI server.
  HTTP API → Redis → this worker.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from enum import Enum
from typing import NoReturn

from app.core.config import Settings
from app.core.exceptions import (
    FatalError,
    JobMaxRetriesExceeded,
    NoCandidatesError,
    RetryableError,
    StartupError,
)
from app.models.domain import (
    IndexCandidate,
    OptimizationJob,
    OptimizationRecord,
    OptimizationStatus,
)

logger = logging.getLogger(__name__)


class PipelineState(str, Enum):
    DETECTED             = "DETECTED"
    PARSED               = "PARSED"
    SCHEMA_ANALYZED      = "SCHEMA_ANALYZED"
    CONTEXT_BUILT        = "CONTEXT_BUILT"
    CANDIDATES_GENERATED = "CANDIDATES_GENERATED"
    BANDIT_SELECTED      = "BANDIT_SELECTED"
    SHADOW_STARTED       = "SHADOW_STARTED"
    BASELINE_COMPLETE    = "BASELINE_COMPLETE"
    CANDIDATE_COMPLETE   = "CANDIDATE_COMPLETE"
    REWARD_CALCULATED    = "REWARD_CALCULATED"
    SAFETY_EVALUATED     = "SAFETY_EVALUATED"
    DEPLOYED             = "DEPLOYED"
    REJECTED             = "REJECTED"
    FAILED               = "FAILED"


class AgentWorker:
    """
    The autonomous optimization agent.

    All components are constructor-injected (loose coupling).
    The worker is created by Container.make_agent_worker().
    """

    def __init__(
        self,
        settings: Settings,
        job_queue,
        log_parser,
        schema_inspector_factory,
        context_builder,
        candidate_generator,
        feature_extractor,
        bandit_policy,
        shadow_db,
        benchmark_runner,
        reward_calculator,
        safety_gate,
        decision_repo,
        observability,
        consumer_id: str | None = None,
        primary_engine=None,
    ) -> None:
        self._settings = settings
        self._queue = job_queue
        self._log_parser = log_parser
        self._schema_inspector_factory = schema_inspector_factory
        self._context_builder = context_builder
        self._candidate_gen = candidate_generator
        self._features = feature_extractor
        self._bandit = bandit_policy
        self._shadow = shadow_db
        self._benchmark = benchmark_runner
        self._reward_calc = reward_calculator
        self._safety = safety_gate
        self._repo = decision_repo
        self._obs = observability
        self._primary_engine = primary_engine
        self._consumer_id = consumer_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> NoReturn:
        """Start the consume loop. Runs indefinitely."""
        logger.info("AgentWorker %s starting…", self._consumer_id)
        await self._startup_checks()
        await self._load_bandit_state()
        self._running = True
        logger.info("AgentWorker %s ready — consuming jobs", self._consumer_id)

        while self._running:
            job = await self._queue.dequeue(
                consumer_id=self._consumer_id,
                timeout_ms=2000,
            )
            if job is None:
                continue
            await self._process_job(job)

    async def stop(self) -> None:
        self._running = False

    async def _startup_checks(self) -> None:
        """Verify shadow DB is reachable before accepting any jobs."""
        if not await self._shadow.is_healthy():
            raise StartupError(
                "Shadow database is unavailable at startup — "
                "ensure docker-compose db_shadow is running"
            )
        logger.info("Startup: shadow DB is healthy ✓")

    async def _load_bandit_state(self) -> None:
        """Restore persisted LinUCB state from DecisionRepository."""
        try:
            state = await self._repo.load_bandit_state()
            if state:
                self._bandit.load_state(state)
                logger.info(
                    "Bandit state loaded: %d actions, %d total updates",
                    len(state.actions),
                    state.total_updates,
                )
            else:
                logger.info("Cold start — LinUCB initialised fresh (A=I, b=0)")
        except Exception as e:
            logger.warning("Failed to load bandit state (using fresh): %s", e)

    # ------------------------------------------------------------------
    # Job dispatcher
    # ------------------------------------------------------------------

    async def _process_job(self, job: OptimizationJob) -> None:
        logger.info(
            "Job %s | attempt %d/%d",
            job.job_id, job.attempt + 1, job.max_attempts,
        )
        try:
            await self._run_pipeline(job)
            if job.stream_entry_id:
                await self._queue.acknowledge(job.stream_entry_id)
            logger.info("Job %s ✓ COMPLETED", job.job_id)

        except RetryableError as e:
            logger.warning("Job %s | RETRYABLE: %s", job.job_id, e)
            try:
                await self._queue.requeue(job)
            except JobMaxRetriesExceeded:
                await self._queue.fail(job, f"Max retries exceeded: {e}")
                await self._emit_failure(job, str(e))

        except FatalError as e:
            logger.error("Job %s | FATAL: %s", job.job_id, e)
            if job.stream_entry_id:
                await self._queue.acknowledge(job.stream_entry_id)
            await self._queue.fail(job, str(e))
            await self._emit_failure(job, str(e))

        except Exception as e:
            logger.exception("Job %s | UNKNOWN: %s", job.job_id, e)
            if job.stream_entry_id:
                await self._queue.acknowledge(job.stream_entry_id)
            await self._queue.fail(job, f"Unexpected: {type(e).__name__}: {e}")
            await self._obs.publish_event("alert", {
                "type": "unknown_pipeline_error",
                "job_id": str(job.job_id),
                "error": str(e),
            })

    # ------------------------------------------------------------------
    # 12-step pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, job: OptimizationJob) -> None:
        """Execute all 12 pipeline steps from AGENT_SPEC.md."""

        await self._emit(job, PipelineState.DETECTED, "Slow query detected", {
            "raw_log_preview": job.raw_log[:120],
        })

        # ── Step 1: Parse log ───────────────────────────────────────────
        parsed = await self._log_parser.parse(job.raw_log)
        logger.info(
            "Job %s | PARSED table=%s duration=%.0fms source=%s",
            job.job_id, parsed.table_name, parsed.duration_ms, parsed.parse_source,
        )
        await self._emit(job, PipelineState.PARSED, "Query parsed successfully", {
            "table_name":  parsed.table_name,
            "duration_ms": parsed.duration_ms,
            "query_type":  parsed.query_type.value,
            "parse_source": parsed.parse_source,
            "confidence":  parsed.confidence,
            "sql":         parsed.sql[:200],
        })

        # ── Step 2: Schema inspection ───────────────────────────────────
        schema_inspector = await self._schema_inspector_factory()
        try:
            schema = await schema_inspector.inspect(parsed.table_name)
        finally:
            # Release the DB connection
            conn = getattr(schema_inspector, "_conn_to_close", None)
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass

        logger.info(
            "Job %s | SCHEMA rows=%d cols=%d indexes=%d",
            job.job_id, schema.row_count, len(schema.columns), len(schema.existing_indexes),
        )
        await self._emit(job, PipelineState.SCHEMA_ANALYZED, f"Schema retrieved for {schema.table_name}", {
            "table_name":         schema.table_name,
            "row_count":          schema.row_count,
            "column_count":       len(schema.columns),
            "existing_index_count": len(schema.existing_indexes),
            "existing_indexes":   [i.name for i in schema.existing_indexes],
            "schema":             schema.model_dump(),
        })

        # ── Step 3: Build context ───────────────────────────────────────
        history = await self._repo.get_decisions_for_context(
            parsed.table_name, limit=10
        )
        context = self._context_builder.build(parsed, schema, history)
        await self._emit(job, PipelineState.CONTEXT_BUILT, "Optimization context established", {
            "history_records": len(history),
        })

        # ── Step 4: Generate candidates (Gemini) ────────────────────────
        candidates = await self._candidate_gen.generate(context)
        logger.info(
            "Job %s | CANDIDATES generated=%d",
            job.job_id, len(candidates),
        )
        await self._emit(job, PipelineState.CANDIDATES_GENERATED, f"Generated {len(candidates)} candidates", {
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "fingerprint": c.fingerprint,
                    "columns":     c.columns,
                    "type":        c.index_type.value,
                    "rank":        c.gemini_rank,
                    "explanation": c.explanation[:100],
                }
                for c in candidates
            ],
        })

        # ── Step 5: Structural safety filter ───────────────────────────
        valid_candidates = [
            c for c in candidates if self._safety.structural_check(c)
        ]
        if not valid_candidates:
            logger.warning("Job %s | No valid candidates passed structural safety", job.job_id)
            await self._emit(job, PipelineState.REJECTED, "All candidates failed safety checks", {
                "reason": f"No valid candidates for table '{parsed.table_name}' passed structural safety."
            })
            return
        logger.info(
            "Job %s | STRUCTURAL CHECK %d/%d candidates passed",
            job.job_id, len(valid_candidates), len(candidates),
        )

        # ── Step 6: LinUCB selects a candidate ─────────────────────────
        shared_context_vec = self._features.extract(context, valid_candidates[0])
        chosen = self._bandit.select(shared_context_vec, valid_candidates)
        chosen_context_vec = self._features.extract(context, chosen)

        bandit_updates_before = self._bandit._total_updates
        logger.info(
            "Job %s | BANDIT selected %s (total_updates=%d)",
            job.job_id, chosen.fingerprint, bandit_updates_before,
        )
        await self._emit(job, PipelineState.BANDIT_SELECTED, f"LinUCB selected candidate {chosen.fingerprint}", {
            "chosen_fingerprint":    chosen.fingerprint,
            "chosen_columns":        chosen.columns,
            "chosen_index_type":     chosen.index_type.value,
            "chosen_explanation":    chosen.explanation,
            "bandit_updates_before": bandit_updates_before,
        })

        # ── Steps 7–8: Shadow experiment ────────────────────────────────
        await self._shadow.clone_schema(parsed.table_name)
        await self._emit(job, PipelineState.SHADOW_STARTED, "Shadow database cloned", {})
        
        # Populate shadow with synthetic data to allow the experiment index scan to have realistic relative cost
        await self._shadow.populate_benchmark_data(parsed.table_name, context.schema.row_count)
        
        logger.info("Job %s | BENCHMARK baseline", job.job_id)
        baseline = await self._benchmark.run(
            sql=parsed.sql,
            shadow=self._shadow,
            iterations=self._settings.AGENT_BENCHMARK_ITERATIONS,
        )
        
        # Override the baseline with the REAL duration from the production log if it's valid
        if parsed.duration_ms > 0:
            baseline = baseline.model_copy(update={
                "query_p50_ms": parsed.duration_ms,
                "query_p95_ms": parsed.duration_ms,
                "query_mean_ms": parsed.duration_ms,
            })
        # Write impact baseline
        if self._settings.BENCHMARK_WRITE_IMPACT:
            write_base = await self._benchmark.run_write_benchmark(
                table_name=parsed.table_name,
                shadow=self._shadow,
            )
            baseline = baseline.model_copy(update={
                "write_p50_ms": write_base.write_p50_ms,
                "write_p95_ms": write_base.write_p95_ms,
            })

        await self._emit(job, PipelineState.BASELINE_COMPLETE, f"Baseline benchmark: {baseline.query_p50_ms:.1f}ms", {
            "p50_ms": baseline.query_p50_ms,
        })
        
        logger.info("Job %s | BENCHMARK candidate", job.job_id)
        
        # Apply candidate index to shadow only
        try:
            await self._shadow.apply_index(chosen)
        except Exception as e:
            await self._shadow.remove_index(chosen)  # cleanup attempt
            raise

        try:
            experiment = await self._benchmark.run(
                sql=parsed.sql,
                shadow=self._shadow,
                iterations=self._settings.AGENT_BENCHMARK_ITERATIONS,
            )
        except Exception as e:
            await self._shadow.remove_index(chosen)
            raise

        # Write impact experiment
        if self._settings.BENCHMARK_WRITE_IMPACT:
            write_exp = await self._benchmark.run_write_benchmark(
                table_name=parsed.table_name,
                shadow=self._shadow,
            )
            experiment = experiment.model_copy(update={
                "write_p50_ms": write_exp.write_p50_ms,
                "write_p95_ms": write_exp.write_p95_ms,
            })

        # Always clean up shadow index
        await self._shadow.remove_index(chosen)

        await self._emit(job, PipelineState.CANDIDATE_COMPLETE, f"Candidate benchmark: {experiment.query_p50_ms:.1f}ms", {
            "p50_ms": experiment.query_p50_ms,
        })

        # ── Step 9a: Compute reward ──────────────────────────────────────
        reward = self._reward_calc.compute(baseline, experiment)
        improvement_pct = (
            (baseline.query_p50_ms - experiment.query_p50_ms)
            / max(1.0, baseline.query_p50_ms) * 100
        )
        logger.info(
            "Job %s | REWARD %.4f (improvement=%.1f%%)",
            job.job_id, reward, improvement_pct,
        )
        await self._emit(job, PipelineState.REWARD_CALCULATED, f"Calculated reward: {reward:.3f}", {
            "reward": reward,
            "baseline_p50": baseline.query_p50_ms,
            "experiment_p50": experiment.query_p50_ms,
            "improvement_pct": ((baseline.query_p50_ms - experiment.query_p50_ms) / max(baseline.query_p50_ms, 1.0)) * 100
        })

        # ── Step 9b: Update LinUCB (learning step) ──────────────────────
        self._bandit.update(chosen_context_vec, chosen.fingerprint, reward)
        await self._repo.save_bandit_state(self._bandit.state)
        logger.info(
            "Job %s | BANDIT UPDATED action=%s reward=%.4f total_updates=%d",
            job.job_id, chosen.fingerprint, reward, self._bandit._total_updates,
        )

        # ── Step 10: Safety gate ────────────────────────────────────────
        decision = self._safety.evaluate(chosen, baseline, experiment, reward)
        logger.info(
            "Job %s | SAFETY %s — %s",
            job.job_id,
            "APPROVED" if decision.approved else "REJECTED",
            decision.reason,
        )
        await self._emit(job, PipelineState.SAFETY_EVALUATED, f"Safety Gate: {'APPROVED' if decision.approved else 'REJECTED'}", {
            "approved": decision.approved,
            "reason": decision.reason,
            "is_duplicate": getattr(decision, 'is_duplicate', False),
        })

        # ── Step 11: Deploy or reject ────────────────────────────────────
        deployed_index_name: str | None = None
        if decision.approved:
            deployed_index_name = await self._deploy_to_production(
                job, chosen, decision
            )
            status = OptimizationStatus.DEPLOYED
            await self._emit(job, PipelineState.DEPLOYED, f"Index {deployed_index_name} deployed successfully", {
                "index_name": deployed_index_name
            })
        else:
            status = OptimizationStatus.REJECTED
            await self._emit(job, PipelineState.REJECTED, f"Deployment rejected: {decision.reason}", {
                "reason": decision.reason
            })

        # ── Step 12: Persist decision record ────────────────────────────
        record = OptimizationRecord(
            job_id=job.job_id,
            candidate=chosen,
            baseline=baseline,
            experiment=experiment,
            reward=reward,
            decision=decision,
            status=status,
            deployed_index_name=deployed_index_name,
            context_vector=chosen_context_vec.tolist(),
        )
        await self._repo.save_decision(record)
        await self._obs.publish_event("decision_recorded", {
            "job_id":       str(job.job_id),
            "record_id":    str(record.record_id),
            "status":       status.value,
            "reward":       reward,
            "approved":     decision.approved,
            "table_name":   chosen.table_name,
            "index_name":   deployed_index_name,
            "improvement_pct": round(improvement_pct, 2),
        })

    # ------------------------------------------------------------------
    # Production deployment
    # ------------------------------------------------------------------

    async def _deploy_to_production(
        self,
        job: OptimizationJob,
        chosen: IndexCandidate,
        decision,
    ) -> str:
        """
        Execute CREATE INDEX CONCURRENTLY on the production database.

        SAFETY: decision.approved is verified by DeploymentManager.
        Connection is acquired here to ensure it is fresh (no open txn).
        """
        # Invalidate schema cache so next job reads fresh indexes
        try:
            schema_inspector = await self._schema_inspector_factory()
            await schema_inspector.invalidate_cache(chosen.table_name)
            conn = getattr(schema_inspector, "_conn_to_close", None)
            if conn is not None:
                await conn.close()
        except Exception as e:
            logger.warning("Schema cache invalidation failed (non-fatal): %s", e)

        # Deploy on a fresh connection with AUTOCOMMIT
        # CREATE INDEX CONCURRENTLY cannot be executed in a transaction block
        async with self._primary_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            from app.database.deployment_manager import DeploymentManager
            deployer = DeploymentManager(conn)
            index_name = await deployer.deploy(chosen, decision)

        logger.info("Job %s | DEPLOYED index=%s", job.job_id, index_name)
        return index_name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _emit(
        self,
        job: OptimizationJob,
        state: PipelineState,
        message: str,
        payload: dict,
    ) -> None:
        logger.info("Job %s | STATE: %s | %s", job.job_id, state.value, message)
        try:
            import datetime
            event = {
                "event_id": str(uuid.uuid4()),
                "job_id": str(job.job_id),
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "state": state.value,
                "event_type": state.value.lower(),
                "message": message,
                "metadata": payload,
            }
            await self._obs.publish_event(state.value, event)
        except Exception as e:
            logger.debug("Observability publish failed (non-fatal): %s", e)

    async def _emit_failure(self, job: OptimizationJob, reason: str) -> None:
        try:
            import datetime
            event = {
                "event_id": str(uuid.uuid4()),
                "job_id": str(job.job_id),
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "state": PipelineState.FAILED.value,
                "event_type": "failed",
                "message": f"Job Failed: {reason}",
                "metadata": {"reason": reason},
            }
            await self._obs.publish_event(PipelineState.FAILED.value, event)
        except Exception:
            pass
