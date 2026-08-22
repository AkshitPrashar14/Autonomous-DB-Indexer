"""
DBAutonomy — Feature Extractor Unit Tests

All tests are deterministic — no randomness, no I/O.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.bandit.features import ContextFeatureExtractor, FEATURE_DIM
from app.models.domain import (
    ColumnInfo,
    IndexCandidate,
    IndexInfo,
    IndexType,
    OptimizationContext,
    ParsedQuery,
    QueryType,
    TableSchema,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_schema(
    table_name: str = "orders",
    row_count: int = 100_000,
    columns: list[ColumnInfo] | None = None,
    indexes: list[IndexInfo] | None = None,
) -> TableSchema:
    return TableSchema(
        table_name=table_name,
        row_count=row_count,
        columns=columns or [
            ColumnInfo(name="id", data_type="bigint", is_primary_key=True),
            ColumnInfo(name="customer_id", data_type="integer", is_foreign_key=True),
            ColumnInfo(name="status", data_type="text"),
            ColumnInfo(name="created_at", data_type="timestamp"),
        ],
        existing_indexes=indexes or [],
    )


def make_query(
    sql: str = "SELECT * FROM orders WHERE customer_id = $1",
    table_name: str = "orders",
    duration_ms: float = 1500.0,
    query_type: QueryType = QueryType.SELECT,
    where_cols: list[str] | None = None,
    join_tables: list[str] | None = None,
    order_by: list[str] | None = None,
) -> ParsedQuery:
    return ParsedQuery(
        sql=sql,
        duration_ms=duration_ms,
        table_name=table_name,
        query_type=query_type,
        where_columns=where_cols or ["customer_id"],
        join_tables=join_tables or [],
        order_by_columns=order_by or [],
    )


def make_context(query: ParsedQuery | None = None, schema: TableSchema | None = None) -> OptimizationContext:
    return OptimizationContext(
        parsed_query=query or make_query(),
        schema=schema or make_schema(),
    )


def make_candidate(
    cols: list[str] | None = None,
    idx_type: IndexType = IndexType.BTREE,
) -> IndexCandidate:
    return IndexCandidate(
        table_name="orders",
        columns=cols or ["customer_id"],
        index_type=idx_type,
    )


# ── Feature Count ─────────────────────────────────────────────────────────────

class TestFeatureCount:
    def test_output_dimension(self):
        ext = ContextFeatureExtractor()
        ctx = make_context()
        vec = ext.extract(ctx)
        assert vec.shape == (FEATURE_DIM,), f"Expected ({FEATURE_DIM},), got {vec.shape}"

    def test_output_dimension_with_candidate(self):
        ext = ContextFeatureExtractor()
        ctx = make_context()
        c = make_candidate()
        vec = ext.extract(ctx, c)
        assert vec.shape == (FEATURE_DIM,)

    def test_feature_names_count(self):
        ext = ContextFeatureExtractor()
        names = ext.feature_names()
        assert len(names) == FEATURE_DIM

    def test_d_constant_matches(self):
        assert ContextFeatureExtractor.D == FEATURE_DIM == 20


# ── Deterministic Output ──────────────────────────────────────────────────────

class TestDeterministicOutput:
    def test_same_input_same_output(self):
        ext = ContextFeatureExtractor()
        ctx = make_context()
        c = make_candidate()
        v1 = ext.extract(ctx, c)
        v2 = ext.extract(ctx, c)
        np.testing.assert_array_equal(v1, v2)

    def test_different_tables_different_vectors(self):
        ext = ContextFeatureExtractor()
        ctx1 = make_context(schema=make_schema("orders", 1_000_000))
        ctx2 = make_context(schema=make_schema("products", 100))
        v1 = ext.extract(ctx1)
        v2 = ext.extract(ctx2)
        assert not np.array_equal(v1, v2)


# ── Empty / Minimal Input ─────────────────────────────────────────────────────

class TestMinimalInput:
    def test_zero_rows_no_crash(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(row_count=0, columns=[ColumnInfo(name="id", data_type="integer")])
        ctx = make_context(schema=schema)
        vec = ext.extract(ctx)
        assert vec.shape == (FEATURE_DIM,)
        assert np.all(np.isfinite(vec))

    def test_no_candidate_features_10_to_19_are_zero(self):
        """Without a candidate, features 10-19 must all be zero."""
        ext = ContextFeatureExtractor()
        ctx = make_context()
        vec = ext.extract(ctx, candidate=None)
        np.testing.assert_array_equal(vec[10:20], np.zeros(10))

    def test_minimal_query(self):
        ext = ContextFeatureExtractor()
        q = ParsedQuery(sql="SELECT 1", duration_ms=0.0, table_name="t")
        schema = TableSchema(table_name="t", row_count=0, columns=[], existing_indexes=[])
        ctx = OptimizationContext(parsed_query=q, schema=schema)
        vec = ext.extract(ctx)
        assert np.all(np.isfinite(vec))


# ── Normal Query Features ─────────────────────────────────────────────────────

class TestNormalQueryFeatures:
    def test_duration_normalised(self):
        ext = ContextFeatureExtractor()
        q = make_query(duration_ms=15_000.0)  # 15000 / 30000 = 0.5
        ctx = make_context(query=q)
        vec = ext.extract(ctx)
        assert vec[0] == pytest.approx(0.5)

    def test_row_count_log_normalised(self):
        ext = ContextFeatureExtractor()
        # log10(100000 + 1) / 10 ≈ 5 / 10 = 0.5
        schema = make_schema(row_count=100_000)
        ctx = make_context(schema=schema)
        vec = ext.extract(ctx)
        expected = math.log10(100_001) / 10.0
        assert vec[1] == pytest.approx(expected, rel=0.01)

    def test_select_one_hot(self):
        ext = ContextFeatureExtractor()
        q = make_query(query_type=QueryType.SELECT)
        ctx = make_context(query=q)
        vec = ext.extract(ctx)
        assert vec[7] == 1.0   # SELECT
        assert vec[8] == 0.0   # UPDATE
        assert vec[9] == 0.0   # DELETE

    def test_update_one_hot(self):
        ext = ContextFeatureExtractor()
        q = make_query(query_type=QueryType.UPDATE)
        ctx = make_context(query=q)
        vec = ext.extract(ctx)
        assert vec[7] == 0.0
        assert vec[8] == 1.0
        assert vec[9] == 0.0

    def test_delete_one_hot(self):
        ext = ContextFeatureExtractor()
        q = make_query(query_type=QueryType.DELETE)
        ctx = make_context(query=q)
        vec = ext.extract(ctx)
        assert vec[9] == 1.0

    def test_unknown_query_type_all_zero(self):
        ext = ContextFeatureExtractor()
        q = make_query(query_type=QueryType.UNKNOWN)
        ctx = make_context(query=q)
        vec = ext.extract(ctx)
        assert vec[7] == 0.0
        assert vec[8] == 0.0
        assert vec[9] == 0.0

    def test_btree_one_hot(self):
        ext = ContextFeatureExtractor()
        ctx = make_context()
        c = make_candidate(idx_type=IndexType.BTREE)
        vec = ext.extract(ctx, c)
        assert vec[10] == 1.0
        assert vec[11] == 0.0  # HASH

    def test_hash_one_hot(self):
        ext = ContextFeatureExtractor()
        ctx = make_context()
        c = make_candidate(idx_type=IndexType.HASH)
        vec = ext.extract(ctx, c)
        assert vec[10] == 0.0
        assert vec[11] == 1.0

    def test_gin_one_hot(self):
        ext = ContextFeatureExtractor()
        ctx = make_context()
        c = make_candidate(idx_type=IndexType.GIN)
        vec = ext.extract(ctx, c)
        assert vec[12] == 1.0

    def test_numeric_column_type(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(columns=[
            ColumnInfo(name="customer_id", data_type="integer")
        ])
        ctx = make_context(schema=schema)
        c = make_candidate(cols=["customer_id"])
        vec = ext.extract(ctx, c)
        assert vec[15] == 1.0  # numeric
        assert vec[16] == 0.0  # text

    def test_text_column_type(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(columns=[
            ColumnInfo(name="status", data_type="text")
        ])
        ctx = make_context(schema=schema)
        c = make_candidate(cols=["status"])
        vec = ext.extract(ctx, c)
        assert vec[15] == 0.0
        assert vec[16] == 1.0

    def test_timestamp_column_type(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(columns=[
            ColumnInfo(name="created_at", data_type="timestamp without time zone")
        ])
        ctx = make_context(schema=schema)
        c = make_candidate(cols=["created_at"])
        vec = ext.extract(ctx, c)
        assert vec[17] == 1.0

    def test_boolean_column_type(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(columns=[
            ColumnInfo(name="is_active", data_type="boolean")
        ])
        ctx = make_context(schema=schema)
        c = make_candidate(cols=["is_active"])
        vec = ext.extract(ctx, c)
        assert vec[18] == 1.0


# ── Large Values (Bounds) ─────────────────────────────────────────────────────

class TestLargeValuesBounds:
    def test_all_features_in_0_1(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(
            row_count=10_000_000_000,  # very large
            columns=[ColumnInfo(name="x", data_type="integer", n_distinct=1_000_000.0)],
        )
        q = make_query(
            duration_ms=999_999.0,   # way above max
            where_cols=["x"] * 50,   # many predicates
            join_tables=["a"] * 20,
            order_by=["x"] * 20,
        )
        ctx = make_context(query=q, schema=schema)
        c = make_candidate(cols=["x"])
        vec = ext.extract(ctx, c)
        assert vec.shape == (FEATURE_DIM,)
        assert np.all(vec >= 0.0), f"Negative features: {vec}"
        assert np.all(vec <= 1.0), f"Features > 1: {vec}"

    def test_zero_duration_gives_zero_feature(self):
        ext = ContextFeatureExtractor()
        q = make_query(duration_ms=0.0)
        ctx = make_context(query=q)
        vec = ext.extract(ctx)
        assert vec[0] == pytest.approx(0.0)

    def test_cardinality_ratio_negative_n_distinct(self):
        """pg_stats uses negative n_distinct to mean fraction of total."""
        ext = ContextFeatureExtractor()
        schema = make_schema(
            row_count=100_000,
            columns=[ColumnInfo(name="status", data_type="text", n_distinct=-0.3)],
        )
        ctx = make_context(schema=schema)
        c = make_candidate(cols=["status"])
        vec = ext.extract(ctx, c)
        # -0.3 means 30% distinct → cardinality_ratio ≈ 0.3
        assert vec[19] == pytest.approx(0.3, abs=0.01)


# ── Missing Optional Information ──────────────────────────────────────────────

class TestMissingInformation:
    def test_column_not_in_schema_no_crash(self):
        """Candidate references a column not in schema → feature gracefully 0."""
        ext = ContextFeatureExtractor()
        ctx = make_context()
        c = make_candidate(cols=["nonexistent_col"])
        vec = ext.extract(ctx, c)
        assert vec.shape == (FEATURE_DIM,)
        assert np.all(np.isfinite(vec))
        # Column type features should be 0
        assert vec[15] == 0.0
        assert vec[16] == 0.0
        assert vec[17] == 0.0
        assert vec[18] == 0.0
        # Cardinality should be 0
        assert vec[19] == 0.0

    def test_n_distinct_none_gives_zero_cardinality(self):
        ext = ContextFeatureExtractor()
        schema = make_schema(columns=[
            ColumnInfo(name="col", data_type="integer", n_distinct=None)
        ])
        ctx = make_context(schema=schema)
        c = make_candidate(cols=["col"])
        vec = ext.extract(ctx, c)
        assert vec[19] == 0.0
