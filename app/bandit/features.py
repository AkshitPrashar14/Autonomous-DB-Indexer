"""
DBAutonomy — Context Feature Extractor

Converts an OptimizationContext + IndexCandidate into the 20-dimensional
numpy context vector consumed by the LinUCB bandit.

Feature table (from BANDIT_SPEC.md, ADR-006):

  idx  feature                  formula
  ---  -------                  -------
   0   query_duration_norm      duration / 30000ms  (clipped 0–1)
   1   table_row_count_log      log10(rows + 1) / 10  (clipped 0–1)
   2   table_col_count_norm     cols / 100  (clipped 0–1)
   3   existing_index_count     count / 20  (clipped 0–1)
   4   where_predicate_count    count / 10  (clipped 0–1)
   5   join_clause_count        count / 5   (clipped 0–1)
   6   order_by_col_count       count / 5   (clipped 0–1)
   7   query_type_select        one-hot (SELECT=1, else 0)
   8   query_type_update        one-hot (UPDATE=1, else 0)
   9   query_type_delete        one-hot (DELETE=1, else 0)
  10   idx_type_btree           one-hot
  11   idx_type_hash            one-hot
  12   idx_type_gin             one-hot
  13   idx_type_gist            one-hot
  14   idx_type_brin            one-hot
  15   col_type_numeric         one-hot for first candidate column
  16   col_type_text            one-hot
  17   col_type_timestamp       one-hot
  18   col_type_boolean         one-hot
  19   cardinality_ratio        n_distinct / total_rows (clipped 0–1)

Notes:
  - All continuous features are clipped to [0, 1] before entry into the
    bandit model; this keeps the A matrix well-conditioned.
  - Features 15-18 reflect the data type of the FIRST column in the
    candidate index (the most selective column).
  - If the candidate has no columns or the column is not found in the
    schema, features 10-19 are all 0.
  - The function is deterministic: same input → same output, no RNG.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from app.models.domain import (
    ContextVector,
    IndexCandidate,
    IndexType,
    OptimizationContext,
    QueryType,
)

logger = logging.getLogger(__name__)

FEATURE_DIM: int = 20  # Must match Settings.BANDIT_CONTEXT_DIM

# Normalisation denominators (from BANDIT_SPEC.md)
_DURATION_MAX_MS: float = 30_000.0
_ROW_COUNT_LOG_MAX: float = 10.0   # log10(10B rows) ≈ 10
_COL_COUNT_MAX: float = 100.0
_INDEX_COUNT_MAX: float = 20.0
_WHERE_COUNT_MAX: float = 10.0
_JOIN_COUNT_MAX: float = 5.0
_ORDER_BY_MAX: float = 5.0

# Column data-type groups → feature indices 15-18
_NUMERIC_TYPES = frozenset({
    "integer", "bigint", "smallint", "numeric", "decimal",
    "real", "double precision", "float", "int4", "int8",
})
_TEXT_TYPES = frozenset({
    "text", "character varying", "varchar", "char",
    "character", "bpchar", "name",
})
_TIMESTAMP_TYPES = frozenset({
    "timestamp", "timestamp without time zone",
    "timestamp with time zone", "timestamptz", "date", "time",
})
_BOOLEAN_TYPES = frozenset({"boolean", "bool"})


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class ContextFeatureExtractor:
    """
    Extracts a 20-dimensional context vector.

    IFeatureExtractor contract:
      extract(context, candidate?) → ContextVector of shape (20,)

    This extractor accepts an optional IndexCandidate to populate
    features 10-19 (index-type and column-type one-hots). When the
    bandit needs to score all candidates it should call extract once
    per candidate, varying the candidate argument.

    When called from the AgentWorker for bandit.update() the same
    candidate that was selected is passed.
    """

    D: int = FEATURE_DIM

    def extract(
        self,
        context: OptimizationContext,
        candidate: IndexCandidate | None = None,
    ) -> ContextVector:
        """
        Extract the 20-dim feature vector.

        Args:
            context:   Full optimization context.
            candidate: The specific candidate being scored/updated.
                       If None, features 10-19 are zero.

        Returns:
            np.ndarray of shape (20,), dtype float64, all values in [0, 1].
        """
        vec = np.zeros(self.D, dtype=np.float64)

        pq = context.parsed_query
        schema = context.schema

        # ── features 0-6: continuous query/schema features ─────────────────

        vec[0] = _clip(pq.duration_ms / _DURATION_MAX_MS)
        vec[1] = _clip(math.log10(max(1, schema.row_count) + 1) / _ROW_COUNT_LOG_MAX)
        vec[2] = _clip(len(schema.columns) / _COL_COUNT_MAX)
        vec[3] = _clip(len(schema.existing_indexes) / _INDEX_COUNT_MAX)
        vec[4] = _clip(len(pq.where_columns) / _WHERE_COUNT_MAX)
        vec[5] = _clip(len(pq.join_tables) / _JOIN_COUNT_MAX)
        vec[6] = _clip(len(pq.order_by_columns) / _ORDER_BY_MAX)

        # ── features 7-9: query type one-hot ────────────────────────────────

        if pq.query_type == QueryType.SELECT:
            vec[7] = 1.0
        elif pq.query_type == QueryType.UPDATE:
            vec[8] = 1.0
        elif pq.query_type == QueryType.DELETE:
            vec[9] = 1.0
        # INSERT and UNKNOWN → all zeros (implied)

        # ── features 10-19: candidate-specific ──────────────────────────────

        if candidate is not None:
            # Features 10-14: index type one-hot
            idx_type_map = {
                IndexType.BTREE: 10,
                IndexType.HASH:  11,
                IndexType.GIN:   12,
                IndexType.GIST:  13,
                IndexType.BRIN:  14,
            }
            feat_idx = idx_type_map.get(candidate.index_type)
            if feat_idx is not None:
                vec[feat_idx] = 1.0

            # Features 15-18: data type of the first candidate column
            if candidate.columns:
                first_col_name = candidate.columns[0].lower()
                col_info = next(
                    (c for c in schema.columns if c.name.lower() == first_col_name),
                    None,
                )
                if col_info is not None:
                    dtype = col_info.data_type.lower()
                    if dtype in _NUMERIC_TYPES:
                        vec[15] = 1.0
                    elif dtype in _TEXT_TYPES:
                        vec[16] = 1.0
                    elif dtype in _TIMESTAMP_TYPES:
                        vec[17] = 1.0
                    elif dtype in _BOOLEAN_TYPES:
                        vec[18] = 1.0
                    # Other types → all zeros

            # Feature 19: cardinality ratio of the first candidate column
            if candidate.columns:
                first_col_name = candidate.columns[0].lower()
                col_info = next(
                    (c for c in schema.columns if c.name.lower() == first_col_name),
                    None,
                )
                if col_info is not None and col_info.n_distinct is not None:
                    row_count = max(1, schema.row_count)
                    n_distinct = abs(col_info.n_distinct)  # pg uses negative for fraction
                    if col_info.n_distinct < 0:
                        # pg_stats convention: negative means fraction of rows
                        cardinality_ratio = abs(col_info.n_distinct)
                    else:
                        cardinality_ratio = n_distinct / row_count
                    vec[19] = _clip(cardinality_ratio)

        assert vec.shape == (self.D,), f"Feature vector has wrong shape: {vec.shape}"
        assert np.all(np.isfinite(vec)), "Feature vector contains non-finite values"

        return vec

    def feature_names(self) -> list[str]:
        """Return human-readable names for all 20 features."""
        return [
            "query_duration_norm",
            "table_row_count_log",
            "table_col_count_norm",
            "existing_index_count",
            "where_predicate_count",
            "join_clause_count",
            "order_by_col_count",
            "query_type_select",
            "query_type_update",
            "query_type_delete",
            "idx_type_btree",
            "idx_type_hash",
            "idx_type_gin",
            "idx_type_gist",
            "idx_type_brin",
            "col_type_numeric",
            "col_type_text",
            "col_type_timestamp",
            "col_type_boolean",
            "cardinality_ratio",
        ]
