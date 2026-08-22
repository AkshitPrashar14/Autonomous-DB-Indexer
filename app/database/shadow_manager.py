"""
DBAutonomy — Shadow Database Manager

Manages the isolated shadow PostgreSQL instance for safe experimentation.
Production is never touched by this component.

Lifecycle per job:
  1. clone_schema()         — copy DDL from production → shadow (structure only)
  2. populate_benchmark_data() — insert synthetic rows for realistic plans
  3. [BenchmarkRunner measures baseline]
  4. apply_index()          — CREATE INDEX CONCURRENTLY on shadow
  5. [BenchmarkRunner measures experiment]
  6. remove_index()         — DROP INDEX on shadow (clean state for next job)

All methods raise typed exceptions from app.core.exceptions.
Cleanup is idempotent — safe to call even when a previous step failed.
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
from datetime import datetime
from typing import AsyncIterator

import asyncpg

from app.core.config import Settings
from app.core.exceptions import (
    BenchmarkError,
    ShadowSetupError,
    ShadowUnavailableError,
)
from app.models.domain import IndexCandidate, IndexType

logger = logging.getLogger(__name__)

# Maximum time to wait for a shadow connection
_CONNECT_TIMEOUT_S: float = 10.0

# Maximum rows inserted during populate_benchmark_data
_MAX_SYNTHETIC_ROWS: int = 5_000_000

# Batch size for synthetic INSERT
_INSERT_BATCH: int = 1_000


class ShadowDatabaseManager:
    """
    Manages a shadow PostgreSQL database for experiment isolation.

    Connection is kept alive for the lifetime of one AgentWorker instance.
    The shadow DB is managed externally (Docker Compose db_shadow service).
    This class does NOT start/stop the container — it only interacts via
    the asyncpg connection.

    Safety contract: production DB connection string is NEVER passed here.
    The constructor accepts only shadow connection parameters.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            try:
                self._pool = await asyncio.wait_for(
                    asyncpg.create_pool(
                        host=self._settings.SHADOW_DB_HOST,
                        port=self._settings.SHADOW_DB_PORT,
                        database=self._settings.SHADOW_DB_NAME,
                        user=self._settings.SHADOW_DB_USER,
                        password=self._settings.SHADOW_DB_PASSWORD,
                        min_size=1,
                        max_size=3,
                        command_timeout=120,
                    ),
                    timeout=_CONNECT_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                raise ShadowUnavailableError(
                    f"Shadow DB connection timed out after {_CONNECT_TIMEOUT_S}s"
                )
            except Exception as e:
                raise ShadowUnavailableError(
                    f"Cannot connect to shadow DB: {e}", cause=e
                )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------
    # IIShadowDatabaseManager contract
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        """Return True if shadow DB is reachable."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.warning("Shadow DB health check failed: %s", e)
            return False

    async def clone_schema(self, table_name: str) -> None:
        """
        Copy the table DDL from production to shadow.

        Strategy: query information_schema on production to reconstruct
        a CREATE TABLE statement, then execute it on shadow.
        We do NOT use pg_dump to avoid OS subprocess dependency.

        Only clones column definitions and NOT existing indexes —
        we want the shadow to start without indexes so we can measure
        baseline performance cleanly.
        """
        try:
            # Connect to production (read-only) to get column info
            prod_conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=self._settings.DB_HOST,
                    port=self._settings.DB_PORT,
                    database=self._settings.DB_NAME,
                    user=self._settings.DB_USER,
                    password=self._settings.DB_PASSWORD,
                    command_timeout=30,
                ),
                timeout=10.0,
            )
            try:
                cols = await prod_conn.fetch(
                    """
                    SELECT column_name, data_type,
                           character_maximum_length, is_nullable,
                           column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = $1
                    ORDER BY ordinal_position
                    """,
                    table_name,
                )
            finally:
                await prod_conn.close()

            if not cols:
                raise ShadowSetupError(
                    f"Table '{table_name}' not found in production schema"
                )

            col_defs = []
            for col in cols:
                dtype = col["data_type"]
                if col["character_maximum_length"]:
                    dtype = f"{dtype}({col['character_maximum_length']})"
                nullable = "" if col["is_nullable"] == "YES" else " NOT NULL"
                col_defs.append(f'  "{col["column_name"]}" {dtype}{nullable}')

            ddl = (
                f'DROP TABLE IF EXISTS "{table_name}";\n'
                f'CREATE TABLE "{table_name}" (\n'
                + ",\n".join(col_defs)
                + "\n);"
            )

            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(ddl)
                logger.info("Cloned schema for table '%s' to shadow", table_name)

        except ShadowUnavailableError:
            raise
        except ShadowSetupError:
            raise
        except Exception as e:
            raise ShadowSetupError(
                f"Failed to clone schema for '{table_name}': {e}", cause=e
            )

    async def populate_benchmark_data(
        self, table_name: str, row_count: int
    ) -> None:
        """
        Insert synthetic rows into the shadow table for realistic query plans.

        Caps at _MAX_SYNTHETIC_ROWS to keep benchmarks fast.
        Runs ANALYZE after insertion so the planner has accurate statistics.
        """
        actual_rows = min(row_count, _MAX_SYNTHETIC_ROWS)
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Get column info from shadow table
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
                    raise ShadowSetupError(
                        f"Shadow table '{table_name}' has no columns; "
                        "did clone_schema run first?"
                    )

                col_names = [c["column_name"] for c in cols]
                col_types = [c["data_type"].lower() for c in cols]

                # Build dynamic SELECT for generate_series to generate data inside postgres
                select_exprs = []
                for name, ctype in zip(col_names, col_types):
                    name_lower = name.lower()
                    if any(k in name_lower for k in ("id", "_id", "key")):
                        select_exprs.append("(1 + floor(random() * 100000))::bigint")
                    elif "email" in name_lower:
                        select_exprs.append("'user' || (1 + floor(random() * 50000))::text || '@example.com'")
                    elif "status" in name_lower:
                        select_exprs.append("(ARRAY['active', 'inactive', 'pending', 'deleted'])[1 + floor(random() * 4)]")
                    elif "amount" in name_lower or "price" in name_lower:
                        select_exprs.append("round((0.01 + random() * 9999.98)::numeric, 2)")
                    elif any(t in ctype for t in ("integer", "bigint", "smallint", "int")):
                        select_exprs.append("(1 + floor(random() * 1000000))::bigint")
                    elif any(t in ctype for t in ("numeric", "decimal", "real", "double")):
                        select_exprs.append("round((random() * 10000.0)::numeric, 4)")
                    elif any(t in ctype for t in ("boolean", "bool")):
                        select_exprs.append("random() > 0.5")
                    elif any(t in ctype for t in ("timestamp", "date", "time")):
                        select_exprs.append("NOW() - (random() * interval '2 years')")
                    elif "json" in ctype:
                        select_exprs.append("'{\"key\": \"value\"}'::jsonb")
                    else:
                        select_exprs.append("md5(random()::text)")
                
                col_list = ", ".join(f'"{c}"' for c in col_names)
                select_clause = ", ".join(select_exprs)
                
                insert_sql = f"""
                    INSERT INTO "{table_name}" ({col_list})
                    SELECT {select_clause}
                    FROM generate_series(1, {actual_rows})
                """
                
                await conn.execute(insert_sql)
                await conn.execute(f'ANALYZE "{table_name}"')
                logger.info(
                    "Populated %d synthetic rows into shadow '%s'",
                    actual_rows,
                    table_name,
                )

        except ShadowSetupError:
            raise
        except Exception as e:
            raise ShadowSetupError(
                f"Failed to populate benchmark data for '{table_name}': {e}",
                cause=e,
            )

    async def apply_index(self, candidate: IndexCandidate) -> None:
        """
        Apply a candidate index to the shadow database.

        Uses CREATE INDEX (not CONCURRENTLY) since the shadow is single-user
        during an experiment and CONCURRENTLY requires no active transaction.
        """
        # Build shadow-specific SQL (no CONCURRENTLY, no IF NOT EXISTS for clean test)
        cols = ", ".join(f'"{c}"' for c in candidate.columns)
        unique = "UNIQUE " if candidate.is_unique else ""
        where = f" WHERE {candidate.where_clause}" if candidate.where_clause else ""
        idx_name = f"shadow_exp_{candidate.fingerprint[:40]}"
        sql = (
            f"CREATE {unique}INDEX {idx_name} "
            f'ON "{candidate.table_name}" USING {candidate.index_type.value} ({cols}){where};'
        )

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(sql)
                logger.info("Applied shadow index: %s", idx_name)
        except Exception as e:
            raise ShadowSetupError(
                f"Failed to apply candidate index on shadow: {e}", cause=e
            )

    async def remove_index(self, candidate: IndexCandidate) -> None:
        """
        Remove the candidate index from the shadow database.

        Idempotent: IF EXISTS prevents errors on double-cleanup.
        """
        idx_name = f"shadow_exp_{candidate.fingerprint[:40]}"
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f'DROP INDEX IF EXISTS "{idx_name}"')
                logger.info("Removed shadow index: %s", idx_name)
        except Exception as e:
            # Cleanup failures are logged but not fatal — they affect the
            # next experiment (stale index), not the current decision.
            logger.error(
                "Failed to remove shadow index '%s': %s (will continue)",
                idx_name,
                e,
            )

    async def get_connection(self) -> asyncpg.Connection:
        """Return an acquired connection from the shadow pool."""
        pool = await self._get_pool()
        return await pool.acquire()


# ---------------------------------------------------------------------------
# Synthetic data generator
# ---------------------------------------------------------------------------

def _synthetic_row(
    col_names: list[str],
    col_types: list[str],
    seed: int,
) -> tuple:
    """
    Generate one synthetic row for benchmarking.

    Values are deterministic given (col_names, col_types, seed) so that
    repeated experiments on the same schema are reproducible.
    """
    rng = random.Random(seed)
    values = []
    for col_name, col_type in zip(col_names, col_types):
        name_lower = col_name.lower()

        # Semantic hints from column name
        if any(k in name_lower for k in ("id", "_id", "key")):
            values.append(rng.randint(1, 100_000))
        elif "email" in name_lower:
            values.append(f"user{rng.randint(1, 50000)}@example.com")
        elif "status" in name_lower:
            values.append(rng.choice(["active", "inactive", "pending", "deleted"]))
        elif "amount" in name_lower or "price" in name_lower:
            values.append(round(rng.uniform(0.01, 9999.99), 2))
        elif any(k in name_lower for k in ("name", "title", "label")):
            values.append("".join(rng.choices(string.ascii_letters, k=12)))
        # Type-based fallbacks
        elif any(t in col_type for t in ("integer", "bigint", "smallint", "int")):
            values.append(rng.randint(1, 1_000_000))
        elif any(t in col_type for t in ("numeric", "decimal", "real", "double")):
            values.append(round(rng.uniform(0.0, 10_000.0), 4))
        elif any(t in col_type for t in ("boolean", "bool")):
            values.append(rng.choice([True, False]))
        elif any(t in col_type for t in ("timestamp", "date", "time")):
            # Random datetime in last 2 years
            ts = datetime(2023, 1, 1).timestamp() + rng.random() * 365 * 24 * 3600 * 2
            values.append(datetime.fromtimestamp(ts))
        elif "json" in col_type:
            values.append('{"key": "value"}')
        else:
            # varchar / text fallback
            values.append("".join(rng.choices(string.ascii_letters + string.digits, k=16)))

    return tuple(values)
