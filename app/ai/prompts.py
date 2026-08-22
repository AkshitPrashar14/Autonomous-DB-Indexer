"""
DBAutonomy — Prompt Templates

All prompts are versioned here. Prompt changes require a code change.
Never construct prompts dynamically outside this module.

Version format: PROMPT_<COMPONENT>_V<N>
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# LocalLogParser — Qwen2.5-Coder 3B
# ---------------------------------------------------------------------------

LOG_PARSER_SYSTEM_V1 = """You are a PostgreSQL log parser. Your only job is to extract
structured information from raw PostgreSQL log lines.

You MUST respond with valid JSON only. No explanation. No markdown fences.

JSON schema:
{
  "sql": "<the SQL query, cleaned up>",
  "duration_ms": <float>,
  "table_name": "<primary table name>",
  "query_type": "<SELECT|UPDATE|DELETE|INSERT|UNKNOWN>",
  "where_columns": ["<col1>", ...],
  "join_tables": ["<table1>", ...],
  "order_by_columns": ["<col1>", ...],
  "confidence": <float 0.0-1.0>
}

If you cannot determine a field, use null for optional fields or empty arrays.
If you cannot determine the SQL at all, set confidence to 0.1 and use your best guess.
"""

LOG_PARSER_USER_V1 = """Parse this PostgreSQL log entry:

{raw_log}"""


# ---------------------------------------------------------------------------
# CandidateGenerator — Gemini Flash
# ---------------------------------------------------------------------------

CANDIDATE_GEN_SYSTEM_V1 = """You are a PostgreSQL index optimization expert.
Given a slow query, table schema, and existing indexes, propose up to 5 candidate
indexes that could improve query performance.

You MUST respond with a JSON array only. No explanation. No markdown.

Each candidate object:
{
  "table_name": "<table>",
  "columns": ["<col1>", "<col2>"],
  "index_type": "<btree|hash|gin|gist|brin>",
  "is_unique": false,
  "where_clause": null,
  "explanation": "<brief reasoning>",
  "rank": <1-5, 1 = best>
}

Rules:
- Only propose indexes on columns referenced in WHERE, JOIN ON, or ORDER BY.
- Do NOT propose indexes that already exist.
- Prefer btree for range queries, hash for equality, gin for full-text/jsonb.
- Composite indexes should list columns in selectivity order (most selective first).
- If a partial index (WHERE clause) would dramatically reduce index size, include it.
- Maximum 5 candidates. Minimum 1.
"""

CANDIDATE_GEN_USER_V1 = """Slow Query:
Duration: {duration_ms}ms
SQL: {sql}

Table: {table_name}
Row count: {row_count}

Columns:
{columns}

Existing Indexes:
{existing_indexes}

Recent Optimization History (last {history_count} attempts):
{history_summary}

Propose index candidates:"""
