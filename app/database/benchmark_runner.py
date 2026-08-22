"""
DBAutonomy — Benchmark Runner

Executes EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) on the shadow database
and collects timing statistics.

Protocol (from REWARD_SPEC.md):
  - Run the query N times (AGENT_BENCHMARK_ITERATIONS, default 10)
  - Collect actual execution time from each EXPLAIN ANALYZE output
  - Compute p50, p95, mean
  - Run write benchmark (100 INSERTs) if BENCHMARK_WRITE_IMPACT=true

Guarantees:
  - Never runs against production DB — accepts IShadowDatabaseManager
  - Returns explicit BenchmarkError if measurements cannot be trusted
  - All individual timings are collected; p50/p95 computed via numpy
  - EXPLAIN output is stored in raw_plans for dashboard display
  - Explicit timeouts per query execution
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import string
import time
from typing import Any

import asyncpg
import numpy as np

from app.core.config import Settings
from app.core.exceptions import BenchmarkError, BenchmarkTimeoutError
from app.models.domain import BenchmarkResult

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """
    Executes EXPLAIN ANALYZE benchmarks on the shadow database.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    # IBenchmarkRunner contract
    # ------------------------------------------------------------------

    async def run(
        self,
        sql: str,
        shadow,  # IShadowDatabaseManager — avoids circular import
        iterations: int | None = None,
    ) -> BenchmarkResult:
        """
        Run EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) N times on shadow.

        Returns BenchmarkResult with p50, p95, plan info.
        Raises BenchmarkTimeoutError or BenchmarkError on failure.
        """
        n = iterations if iterations is not None else self._settings.AGENT_BENCHMARK_ITERATIONS

        exec_times: list[float] = []
        planning_times: list[float] = []
        blks_hit: list[int] = []
        blks_read: list[int] = []
        raw_plans: list[dict] = []

        explain_sql = (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON, TIMING TRUE) " + sql
        )

        try:
            conn = await shadow.get_connection()
        except Exception as e:
            raise BenchmarkError(
                f"Cannot acquire shadow connection for benchmarking: {e}", cause=e
            )

        try:
            for i in range(n):
                try:
                    plan_json = await asyncio.wait_for(
                        self._run_explain(conn, explain_sql),
                        timeout=30.0,
                    )
                    # Parse result
                    if plan_json:
                        plan = plan_json[0]  # asyncpg returns list
                        top = plan.get("Plan", {})
                        exec_ms = float(
                            plan.get("Execution Time", top.get("Actual Total Time", 0.0))
                        )
                        plan_ms = float(plan.get("Planning Time", 0.0))
                        hit = int(top.get("Shared Hit Blocks", 0))
                        rd = int(top.get("Shared Read Blocks", 0))

                        exec_times.append(exec_ms)
                        planning_times.append(plan_ms)
                        blks_hit.append(hit)
                        blks_read.append(rd)
                        if i == 0:  # store only first plan to limit memory
                            raw_plans.append(plan)
                    else:
                        raise BenchmarkError("EXPLAIN returned empty result")

                except asyncio.TimeoutError:
                    raise BenchmarkTimeoutError(
                        f"Benchmark query timed out after 30s on iteration {i}"
                    )
        finally:
            # Release connection back to the pool
            try:
                await shadow._pool.release(conn)  # type: ignore[attr-defined]
            except Exception:
                pass

        if not exec_times:
            raise BenchmarkError("No benchmark measurements collected")

        arr = np.array(exec_times, dtype=np.float64)

        return BenchmarkResult(
            query_p50_ms=float(np.percentile(arr, 50)),
            query_p95_ms=float(np.percentile(arr, 95)),
            query_mean_ms=float(np.mean(arr)),
            planning_time_ms=float(np.mean(planning_times)) if planning_times else 0.0,
            shared_blks_hit=int(np.mean(blks_hit)) if blks_hit else 0,
            shared_blks_read=int(np.mean(blks_read)) if blks_read else 0,
            n_iterations=n,
            raw_plans=raw_plans,
        )

    async def run_write_benchmark(
        self,
        table_name: str,
        shadow,  # IShadowDatabaseManager
        iterations: int | None = None,
    ) -> BenchmarkResult:
        """
        Measure INSERT performance on the shadow table.

        Protocol from REWARD_SPEC.md:
          Run 100 parameterised INSERTs. Collect p50 timing.
        """
        n = iterations if iterations is not None else self._settings.AGENT_BENCHMARK_WRITE_ITERATIONS

        write_times: list[float] = []

        try:
            conn = await shadow.get_connection()
        except Exception as e:
            raise BenchmarkError(
                f"Cannot acquire shadow connection for write benchmark: {e}", cause=e
            )

        try:
            # Get column info to build synthetic INSERT
            cols = await conn.fetch(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                ORDER BY ordinal_position
                """,
                table_name,
            )
            if not cols:
                raise BenchmarkError(
                    f"Table '{table_name}' not found in shadow for write benchmark"
                )

            col_names = [c["column_name"] for c in cols]
            col_types = [c["data_type"].lower() for c in cols]

            placeholders = ", ".join(f"${i+1}" for i in range(len(col_names)))
            col_list = ", ".join(f'"{c}"' for c in col_names)
            insert_sql = (
                f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders})'
            )

            for i in range(n):
                from app.database.shadow_manager import _synthetic_row
                row = _synthetic_row(col_names, col_types, seed=900_000 + i)
                t0 = time.perf_counter()
                try:
                    await asyncio.wait_for(
                        conn.execute(insert_sql, *row),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    raise BenchmarkTimeoutError(
                        f"Write benchmark timed out on iteration {i}"
                    )
                t1 = time.perf_counter()
                write_times.append((t1 - t0) * 1000.0)  # ms

            # Roll back writes — shadow data should stay clean
            await conn.execute(f'DELETE FROM "{table_name}" WHERE ctid IN '
                               f'(SELECT ctid FROM "{table_name}" ORDER BY ctid DESC LIMIT {n})')

        finally:
            try:
                await shadow._pool.release(conn)  # type: ignore[attr-defined]
            except Exception:
                pass

        if not write_times:
            raise BenchmarkError("No write benchmark measurements collected")

        arr = np.array(write_times, dtype=np.float64)
        return BenchmarkResult(
            query_p50_ms=0.0,  # Not a read benchmark
            query_p95_ms=0.0,
            query_mean_ms=0.0,
            planning_time_ms=0.0,
            shared_blks_hit=0,
            shared_blks_read=0,
            write_p50_ms=float(np.percentile(arr, 50)),
            write_p95_ms=float(np.percentile(arr, 95)),
            n_iterations=n,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run_explain(
        self, conn: asyncpg.Connection, explain_sql: str
    ) -> list[dict] | None:
        """Execute EXPLAIN ANALYZE and return parsed JSON plan."""
        try:
            rows = await conn.fetch(explain_sql)
            if not rows:
                return None
            # EXPLAIN FORMAT JSON returns a single row with one column
            raw = rows[0][0]
            if isinstance(raw, str):
                return json.loads(raw)
            elif isinstance(raw, list):
                return raw
            else:
                return [raw]
        except asyncpg.PostgresError as e:
            raise BenchmarkError(
                f"EXPLAIN ANALYZE failed: {e}", cause=e
            )
