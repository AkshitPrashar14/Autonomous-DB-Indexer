"""
DBAutonomy — ContextBuilder

Assembles an OptimizationContext from parsed query + schema + decision history.
Pure function — no I/O.
"""

from __future__ import annotations

from app.models.domain import (
    OptimizationContext,
    OptimizationRecord,
    ParsedQuery,
    TableSchema,
)


class ContextBuilder:
    """
    Builds an OptimizationContext.

    Responsible for:
    - Combining parsed query + schema + recent history
    - Token-budgeting history (cap at N records to avoid prompt overflow)
    """

    def __init__(self, max_history: int = 10):
        self._max_history = max_history

    def build(
        self,
        parsed: ParsedQuery,
        schema: TableSchema,
        history: list[OptimizationRecord],
    ) -> OptimizationContext:
        """Build context. Pure function."""
        # Cap history to avoid blowing the Gemini prompt window
        capped_history = history[: self._max_history]

        return OptimizationContext(
            parsed_query=parsed,
            schema=schema,
            recent_history=capped_history,
        )
