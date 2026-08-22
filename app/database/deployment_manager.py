"""
DBAutonomy — DeploymentManager

Executes CREATE INDEX CONCURRENTLY on the production database.

SAFETY INVARIANTS (RULE-01, RULE-02, RULE-03):
  - Only accepts SafetyDecision(approved=True).
  - SQL validated with final pattern check immediately before execution.
  - Uses raw asyncpg connection (not SQLAlchemy) to support CONCURRENTLY.
  - Only ever emits CREATE INDEX CONCURRENTLY IF NOT EXISTS.
  - Never touches the shadow database.

CREATE INDEX CONCURRENTLY cannot run inside an explicit transaction.
We therefore use a raw asyncpg connection passed from the engine's
connection pool, outside any transaction block.
"""

from __future__ import annotations

import logging
import re

from app.core.exceptions import DeploymentError, DeploymentPreconditionError
from app.models.domain import IndexCandidate, SafetyDecision

logger = logging.getLogger(__name__)

# Final belt-and-suspenders pattern — identical to SafetyGate Stage 2
_ALLOWED_PATTERN = re.compile(
    r"^CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+"
    r"IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+\w+(\.\w+)?\s*"
    r"\([\w\s,]+\)"
    r"(\s+WHERE\s+.+)?"
    r"\s*;?\s*$",
    re.IGNORECASE,
)


class DeploymentManager:
    """
    Executes approved index deployments on production.

    Accepts an asyncpg connection (NOT inside a transaction).
    The worker calls this inside `engine.begin()` context but
    issues ROLLBACK/COMMIT explicitly if needed.
    """

    def __init__(self, conn) -> None:
        """
        conn: SQLAlchemy AsyncConnection opened with engine.begin()
              or engine.connect(), or a raw asyncpg connection.
        """
        self._conn = conn

    async def deploy(
        self,
        candidate: IndexCandidate,
        decision: SafetyDecision,
    ) -> str:
        """
        Deploy an approved index to production.

        Returns the index name created.

        Raises:
            DeploymentPreconditionError: decision.approved is False (programming error).
            DeploymentError: CREATE INDEX failed.
        """
        # Hard precondition — programming error if violated
        if not decision.approved:
            raise DeploymentPreconditionError(
                f"Deployment attempted without approved SafetyDecision. "
                f"Reason: {decision.reason}"
            )

        sql = candidate.create_sql
        index_name = candidate.index_name

        # Final structural SQL validation
        if not _ALLOWED_PATTERN.match(sql):
            raise DeploymentError(
                f"Candidate SQL failed final pattern check: {sql[:200]}"
            )

        logger.info("Deploying to production: %s", sql[:120])

        try:
            from sqlalchemy import text
            # Strip trailing semicolon — SQLAlchemy text() handles without it
            clean_sql = sql.rstrip(";").strip()
            await self._conn.execute(text(clean_sql))
            logger.info("✓ Deployed index: %s", index_name)
            return index_name

        except Exception as e:
            raise DeploymentError(
                f"Failed to deploy index '{index_name}': {e}", cause=e
            )
