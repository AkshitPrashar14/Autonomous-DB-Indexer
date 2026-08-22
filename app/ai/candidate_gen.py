"""
DBAutonomy — CandidateGenerator (Skeleton)

Generates index candidates using Gemini Flash.
Output is validated against the IndexCandidate schema before use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import google.generativeai as genai

from app.ai.prompts import CANDIDATE_GEN_SYSTEM_V1, CANDIDATE_GEN_USER_V1
from app.core.config import Settings
from app.core.exceptions import (
    CandidateGenerationError,
    GeminiAPIError,
    GeminiRateLimitError,
    NoCandidatesError,
)
from app.models.domain import IndexCandidate, IndexType, OptimizationContext

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```")
_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


class CandidateGenerator:
    """
    Calls Gemini Flash to produce index candidates.

    - Retries up to GEMINI_MAX_RETRIES times with exponential backoff on 429.
    - Extracts JSON from markdown-wrapped responses.
    - Validates each candidate against IndexCandidate schema.
    - Returns only valid candidates.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            system_instruction=CANDIDATE_GEN_SYSTEM_V1,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

    async def generate(self, context: OptimizationContext) -> list[IndexCandidate]:
        """Generate and validate candidates. See AGENT_SPEC.md Step 4."""
        if self._settings.AI_MODE.lower() == "mock":
            return self._mock_generate(context)

        prompt = self._build_prompt(context)
        raw = await self._call_with_retry(prompt)
        candidates = self._parse_and_validate(raw, context)

        if not candidates:
            raise NoCandidatesError(
                f"All candidates from Gemini failed validation for "
                f"table={context.parsed_query.table_name}"
            )

        return candidates
        
    def _mock_generate(self, context: OptimizationContext) -> list[IndexCandidate]:
        """Return deterministic mock candidates."""
        return [
            IndexCandidate(
                table_name=context.parsed_query.table_name,
                columns=context.parsed_query.where_columns[:1] if context.parsed_query.where_columns else ["id"],
                index_type=IndexType.BTREE,
                is_unique=False,
                where_clause=None,
                explanation="Mock Candidate 1 (B-Tree on primary lookup)",
                gemini_rank=1
            ),
            IndexCandidate(
                table_name=context.parsed_query.table_name,
                columns=(context.parsed_query.where_columns + context.parsed_query.order_by_columns)[:2] if context.parsed_query.where_columns else ["id"],
                index_type=IndexType.BTREE,
                is_unique=False,
                where_clause=None,
                explanation="Mock Candidate 2 (B-Tree composite)",
                gemini_rank=2
            ),
            IndexCandidate(
                table_name=context.parsed_query.table_name,
                columns=context.parsed_query.where_columns[:1] if context.parsed_query.where_columns else ["id"],
                index_type=IndexType.HASH,
                is_unique=False,
                where_clause=None,
                explanation="Mock Candidate 3 (Hash for equality)",
                gemini_rank=3
            )
        ]

    def _build_prompt(self, context: OptimizationContext) -> str:
        schema = context.schema
        columns_text = "\n".join(
            f"  - {c.name} ({c.data_type})"
            + (f", FK" if c.is_foreign_key else "")
            + (f", PK" if c.is_primary_key else "")
            for c in schema.columns
        )
        indexes_text = "\n".join(
            f"  - {i.name}: ({', '.join(i.columns)}) [{i.index_type.value}]"
            for i in schema.existing_indexes
        ) or "  None"

        history_text = "\n".join(
            f"  - {r.candidate.columns} → reward={r.reward:.2f} ({r.status.value})"
            for r in context.recent_history[:5]
        ) or "  No history yet"

        return CANDIDATE_GEN_USER_V1.format(
            duration_ms=context.parsed_query.duration_ms,
            sql=context.parsed_query.sql,
            table_name=context.parsed_query.table_name,
            row_count=schema.row_count,
            columns=columns_text,
            existing_indexes=indexes_text,
            history_count=len(context.recent_history),
            history_summary=history_text,
        )

    async def _call_with_retry(self, prompt: str) -> str:
        """Call Gemini with exponential backoff on rate limits."""
        delay = self._settings.GEMINI_RETRY_BASE_DELAY_S
        last_error: Exception | None = None

        for attempt in range(1, self._settings.GEMINI_MAX_RETRIES + 1):
            try:
                # Gemini SDK is synchronous; run in executor to avoid blocking
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._model.generate_content(prompt),
                )
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str or "quota" in err_str:
                    logger.warning(
                        "Gemini rate limit on attempt %d/%d, retrying in %.1fs",
                        attempt, self._settings.GEMINI_MAX_RETRIES, delay,
                    )
                    last_error = GeminiRateLimitError(str(e), cause=e)
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise GeminiAPIError(f"Gemini API error: {e}", cause=e)

        if self._settings.GROQ_API_KEY:
            logger.warning("Falling back to Groq API due to Gemini rate limits...")
            try:
                import openai
                client = openai.AsyncOpenAI(
                    api_key=self._settings.GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                )
                response = await client.chat.completions.create(
                    model="openai/gpt-oss-120b",
                    messages=[
                        {"role": "system", "content": CANDIDATE_GEN_SYSTEM_V1},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.2,
                )
                return response.choices[0].message.content
            except Exception as groq_err:
                logger.error("Groq fallback also failed: %s", groq_err)

        raise CandidateGenerationError(
            "Gemini failed after all retries (and Groq fallback failed/unset)", cause=last_error
        )

    def _parse_and_validate(
        self,
        raw: str,
        context: OptimizationContext,
    ) -> list[IndexCandidate]:
        """Extract JSON array from response and validate each entry."""
        # Strip markdown fences if present
        fence_match = _FENCE_RE.search(raw)
        json_text = fence_match.group(1) if fence_match else raw

        # Find first JSON array
        array_match = _ARRAY_RE.search(json_text)
        if not array_match:
            logger.warning("Gemini response contains no JSON array: %s", raw[:200])
            return []

        try:
            raw_candidates: list[dict] = json.loads(array_match.group(0))
        except json.JSONDecodeError as e:
            logger.warning("Gemini JSON parse error: %s", e)
            return []

        valid: list[IndexCandidate] = []
        existing_cols = {i.name for i in context.schema.existing_indexes}

        for i, raw_c in enumerate(raw_candidates):
            try:
                # Coerce index_type safely
                raw_c["index_type"] = raw_c.get("index_type", "btree").lower()
                if raw_c["index_type"] not in IndexType.__members__.values():
                    raw_c["index_type"] = "btree"

                candidate = IndexCandidate(
                    table_name=raw_c.get("table_name", context.parsed_query.table_name),
                    columns=list(raw_c.get("columns", [])),
                    index_type=IndexType(raw_c["index_type"]),
                    is_unique=bool(raw_c.get("is_unique", False)),
                    where_clause=raw_c.get("where_clause"),
                    explanation=str(raw_c.get("explanation", "")),
                    gemini_rank=int(raw_c.get("rank", i + 1)),
                )

                if not candidate.columns:
                    logger.warning("Candidate %d has no columns, skipping", i)
                    continue

                valid.append(candidate)
            except Exception as e:
                logger.warning("Candidate %d failed validation: %s", i, e)

        return valid
