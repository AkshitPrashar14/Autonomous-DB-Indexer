"""
DBAutonomy — LocalLogParser (Skeleton)

Parses raw PostgreSQL log lines using Qwen2.5-Coder 3B via Ollama.
Falls back to regex extraction if the LLM fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.ai.prompts import LOG_PARSER_SYSTEM_V1, LOG_PARSER_USER_V1
from app.core.config import Settings
from app.core.exceptions import OllamaConnectionError, OllamaTimeoutError, ParseError
from app.models.domain import ParsedQuery, QueryType

logger = logging.getLogger(__name__)

# Regex fallback patterns
_DURATION_RE = re.compile(r"duration:\s*([\d.]+)\s*ms", re.IGNORECASE)
_TABLE_RE = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)
_QUERY_TYPE_RE = re.compile(r"^\s*(SELECT|UPDATE|DELETE|INSERT)", re.IGNORECASE)


class LocalLogParser:
    """
    Parses PostgreSQL log lines using Qwen2.5-Coder 3B.

    On LLM failure: falls back to regex (sets parse_source="regex_fallback",
    confidence reduced to 0.4).
    On complete failure: raises ParseError.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.OLLAMA_BASE_URL,
            timeout=settings.OLLAMA_TIMEOUT_S,
        )

    async def parse(self, raw_log: str) -> ParsedQuery:
        if self._settings.AI_MODE.lower() == "mock":
            return self._mock_parse(raw_log)

        # Truncate to prevent slow inference (FL-001 mitigation)
        truncated = raw_log[: self._settings.OLLAMA_MAX_INPUT_CHARS]

        try:
            result = await self._parse_with_llm(truncated)
            return result
        except (OllamaTimeoutError, OllamaConnectionError) as e:
            logger.warning("Ollama unavailable (%s), falling back to regex", e)
            return self._parse_with_regex(truncated)
        except json.JSONDecodeError as e:
            logger.warning("LLM returned invalid JSON, falling back to regex: %s", e)
            return self._parse_with_regex(truncated)

    async def _parse_with_llm(self, raw_log: str) -> ParsedQuery:
        """Call Ollama and parse the JSON response."""
        prompt = LOG_PARSER_USER_V1.format(raw_log=raw_log)
        try:
            response = await self._client.post(
                "/api/generate",
                json={
                    "model": self._settings.OLLAMA_MODEL,
                    "system": LOG_PARSER_SYSTEM_V1,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as e:
            raise OllamaTimeoutError("Ollama request timed out", cause=e)
        except httpx.ConnectError as e:
            raise OllamaConnectionError("Cannot connect to Ollama", cause=e)

        data: dict[str, Any] = response.json()
        raw_response: str = data.get("response", "")

        parsed = json.loads(raw_response)  # May raise JSONDecodeError
        return self._dict_to_parsed_query(parsed, source="llm")

    def _parse_with_regex(self, raw_log: str) -> ParsedQuery:
        """Regex-based fallback. Always succeeds; confidence is low."""
        duration_match = _DURATION_RE.search(raw_log)
        duration_ms = float(duration_match.group(1)) if duration_match else 0.0

        table_match = _TABLE_RE.search(raw_log)
        table_name = table_match.group(1) if table_match else "unknown"

        qt_match = _QUERY_TYPE_RE.search(raw_log)
        query_type_str = qt_match.group(1).upper() if qt_match else "UNKNOWN"
        query_type = QueryType(query_type_str) if query_type_str in QueryType.__members__ else QueryType.UNKNOWN

        # Extract raw SQL: everything after "statement: " or "execute: "
        sql_match = re.search(r"(?:statement|execute):\s*(.+)$", raw_log, re.DOTALL | re.IGNORECASE)
        sql = sql_match.group(1).strip() if sql_match else raw_log.strip()
        logger.info("Regex fallback extracted: %s (duration %s)", sql[:30], duration_ms)
        
        return ParsedQuery(
            sql=sql,
            duration_ms=duration_ms,
            table_name=table_name,
            query_type=query_type,
            where_columns=[],
            join_tables=[],
            order_by_columns=[],
            parse_source="regex_fallback",
            confidence=0.4,
        )

    def _mock_parse(self, raw_log: str) -> ParsedQuery:
        """Returns a deterministic parsed result for integration tests and demo mode."""
        return ParsedQuery(
            sql=raw_log.strip(),
            duration_ms=1500.0,
            table_name="orders",
            query_type=QueryType.SELECT,
            where_columns=["customer_id"],
            join_tables=[],
            order_by_columns=[],
            parse_source="mock",
            confidence=1.0,
        )

    def _dict_to_parsed_query(self, data: dict, source: str) -> ParsedQuery:
        """Convert the LLM JSON dict to a ParsedQuery, with safe defaults."""
        return ParsedQuery(
            sql=data.get("sql", ""),
            duration_ms=float(data.get("duration_ms") or 0.0),
            table_name=data.get("table_name") or "unknown",
            query_type=QueryType(data.get("query_type", "UNKNOWN"))
            if data.get("query_type") in QueryType.__members__
            else QueryType.UNKNOWN,
            where_columns=list(data.get("where_columns") or []),
            join_tables=list(data.get("join_tables") or []),
            order_by_columns=list(data.get("order_by_columns") or []),
            parse_source=source,
            confidence=float(data.get("confidence") or 1.0),
        )

    async def close(self) -> None:
        await self._client.aclose()
