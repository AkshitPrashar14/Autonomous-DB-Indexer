"""
DBAutonomy — SchemaInspector (Skeleton)

Read-only access to production database schema.
Results are cached in Redis with configurable TTL.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings
from app.core.exceptions import SchemaInspectionError, SchemaNotFoundError
from app.models.domain import ColumnInfo, IndexInfo, IndexType, TableSchema

logger = logging.getLogger(__name__)

# SQL to fetch table metadata
_TABLE_EXISTS_SQL = """
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = :table_name
"""

_COLUMNS_SQL = """
SELECT
    c.column_name,
    c.data_type,
    c.is_nullable,
    (SELECT COUNT(*) > 0
     FROM information_schema.key_column_usage kcu
     JOIN information_schema.table_constraints tc
       ON kcu.constraint_name = tc.constraint_name
     WHERE tc.constraint_type = 'PRIMARY KEY'
       AND kcu.table_name = c.table_name
       AND kcu.column_name = c.column_name
    ) AS is_primary_key,
    (SELECT COUNT(*) > 0
     FROM information_schema.key_column_usage kcu
     JOIN information_schema.table_constraints tc
       ON kcu.constraint_name = tc.constraint_name
     WHERE tc.constraint_type = 'FOREIGN KEY'
       AND kcu.table_name = c.table_name
       AND kcu.column_name = c.column_name
    ) AS is_foreign_key,
    s.n_distinct,
    s.null_frac
FROM information_schema.columns c
LEFT JOIN pg_stats s
    ON s.schemaname = 'public'
    AND s.tablename = c.table_name
    AND s.attname = c.column_name
WHERE c.table_schema = 'public'
  AND c.table_name = :table_name
ORDER BY c.ordinal_position
"""

_INDEXES_SQL = """
SELECT
    i.relname AS index_name,
    am.amname AS index_type,
    array_agg(a.attname ORDER BY x.n) AS columns,
    ix.indisunique AS is_unique,
    pg_get_expr(ix.indpred, ix.indrelid) AS where_clause
FROM pg_index ix
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_am am ON am.oid = i.relam
JOIN pg_namespace n ON n.oid = t.relnamespace
CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS x(attnum, n)
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = x.attnum
WHERE n.nspname = 'public'
  AND t.relname = :table_name
GROUP BY i.relname, am.amname, ix.indisunique, ix.indpred, ix.indrelid
"""

_ROW_COUNT_SQL = """
SELECT reltuples::bigint AS row_count
FROM pg_class
WHERE relname = :table_name AND relkind = 'r'
"""


class SchemaInspector:
    """
    Inspects PostgreSQL table schema with Redis caching.
    Uses a read-only async SQLAlchemy connection.
    """

    def __init__(
        self,
        settings: Settings,
        conn: AsyncConnection,
        redis_client: aioredis.Redis,
    ):
        self._settings = settings
        self._conn = conn
        self._redis = redis_client

    def _cache_key(self, table_name: str) -> str:
        return f"dbautonomy:schema:{table_name}"

    async def inspect(self, table_name: str) -> TableSchema:
        # Try cache first
        cached = await self._redis.get(self._cache_key(table_name))
        if cached:
            logger.debug("Schema cache hit for %s", table_name)
            return TableSchema.model_validate_json(cached)

        # Fetch from database
        try:
            schema = await self._fetch_from_db(table_name)
        except SchemaNotFoundError:
            raise
        except Exception as e:
            raise SchemaInspectionError(
                f"Failed to inspect schema for {table_name}", cause=e
            )

        # Cache result
        await self._redis.setex(
            self._cache_key(table_name),
            self._settings.REDIS_SCHEMA_CACHE_TTL_S,
            schema.model_dump_json(),
        )
        return schema

    async def invalidate_cache(self, table_name: str) -> None:
        await self._redis.delete(self._cache_key(table_name))

    async def _fetch_from_db(self, table_name: str) -> TableSchema:
        from sqlalchemy import text

        # Check table exists
        result = await self._conn.execute(
            text(_TABLE_EXISTS_SQL), {"table_name": table_name}
        )
        count = result.scalar()
        if not count:
            raise SchemaNotFoundError(f"Table '{table_name}' not found")

        # Row count estimate
        rc_result = await self._conn.execute(
            text(_ROW_COUNT_SQL), {"table_name": table_name}
        )
        row_count = rc_result.scalar() or 0

        # Columns
        col_result = await self._conn.execute(
            text(_COLUMNS_SQL), {"table_name": table_name}
        )
        columns = [
            ColumnInfo(
                name=row.column_name,
                data_type=row.data_type,
                is_nullable=(row.is_nullable == "YES"),
                is_primary_key=bool(row.is_primary_key),
                is_foreign_key=bool(row.is_foreign_key),
                n_distinct=row.n_distinct,
                null_frac=row.null_frac,
            )
            for row in col_result.fetchall()
        ]

        # Indexes
        idx_result = await self._conn.execute(
            text(_INDEXES_SQL), {"table_name": table_name}
        )
        indexes = []
        for row in idx_result.fetchall():
            idx_type_str = (row.index_type or "btree").lower()
            try:
                idx_type = IndexType(idx_type_str)
            except ValueError:
                idx_type = IndexType.BTREE
            indexes.append(
                IndexInfo(
                    name=row.index_name,
                    index_type=idx_type,
                    columns=list(row.columns),
                    is_unique=bool(row.is_unique),
                    where_clause=row.where_clause,
                )
            )

        return TableSchema(
            table_name=table_name,
            row_count=int(row_count),
            columns=columns,
            existing_indexes=indexes,
        )
