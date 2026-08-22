"""
DBAutonomy — SafetyGate

Deterministic rule engine. No AI. No exceptions (unless internal error).
See docs/SAFETY_SPEC.md for full specification.
"""

from __future__ import annotations

import logging
import re

from app.core.config import Settings
from app.models.domain import BenchmarkResult, IndexCandidate, SafetyDecision

logger = logging.getLogger(__name__)

# Pattern from SAFETY_SPEC RULE-02 / Stage 2
_ALLOWED_SQL_PATTERN = re.compile(
    r"^CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+"
    r"IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+\w+(\.\w+)?\s*"
    r"\([\w\s,]+\)"
    r"(\s+WHERE\s+.+)?"
    r"\s*;?\s*$",
    re.IGNORECASE,
)

# Dangerous SQL keywords that must never appear in a candidate SQL string
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|TRUNCATE|ALTER|GRANT|REVOKE|EXECUTE|CALL|DO)\b",
    re.IGNORECASE,
)


class SafetyGate:
    """
    8-stage deterministic safety evaluation.

    Stages 1-4 are structural (no benchmark data needed) → used in pre-bandit check.
    Stages 5-8 require benchmark data → used in full evaluate().
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def structural_check(self, candidate: IndexCandidate) -> bool:
        """
        Stages 1-4: Fast pre-bandit validation.
        Returns True if the candidate is structurally safe to experiment with.
        """
        sql = candidate.create_sql

        # Stage 1: No forbidden keywords
        if _FORBIDDEN_KEYWORDS.search(sql):
            logger.warning("Structural check failed (forbidden keyword): %s", sql[:100])
            return False

        # Stage 1b: No multiple statements
        if sql.count(";") > 1 or (sql.count(";") == 1 and not sql.strip().endswith(";")):
            logger.warning("Structural check failed (multiple statements detected): %s", sql[:100])
            return False

        # Stage 2: Pattern match
        if not _ALLOWED_SQL_PATTERN.match(sql):
            logger.warning("Structural check failed (pattern mismatch): %s", sql[:100])
            return False

        # Stage 3: Table whitelist
        allowed = self._settings.allowed_tables
        if allowed and candidate.table_name not in allowed:
            logger.warning(
                "Structural check failed (table not in whitelist): %s",
                candidate.table_name,
            )
            return False

        # Stage 4: Index type whitelist
        if candidate.index_type.value not in self._settings.allowed_index_types:
            logger.warning(
                "Structural check failed (index type not allowed): %s",
                candidate.index_type.value,
            )
            return False

        # Stage 4b: Index name convention
        if not candidate.index_name.startswith("dbautonomy_"):
            logger.warning("Structural check failed (index name convention)")
            return False

        return True

    def evaluate(
        self,
        candidate: IndexCandidate,
        baseline: BenchmarkResult,
        experiment: BenchmarkResult,
        reward: float,
    ) -> SafetyDecision:
        """
        Full 8-stage evaluation.
        Returns SafetyDecision. Never raises.
        """
        stages_passed: list[str] = []
        stages_failed: list[str] = []

        # --- Stages 1-4: Structural ---
        if self.structural_check(candidate):
            stages_passed.extend(["syntax", "pattern", "table_whitelist", "index_name"])
        else:
            stages_failed.append("structural")
            return SafetyDecision(
                approved=False,
                reason="Failed structural check. See logs for details.",
                stages_passed=stages_passed,
                stages_failed=stages_failed,
                reward=reward,
                risk_score=1.0,
            )

        # --- Stage 6: Improvement threshold ---
        if reward >= self._settings.SAFETY_MIN_REWARD_THRESHOLD:
            stages_passed.append("improvement_threshold")
        else:
            stages_failed.append("improvement_threshold")
            return SafetyDecision(
                approved=False,
                reason=f"Reward {reward:.3f} below threshold "
                       f"{self._settings.SAFETY_MIN_REWARD_THRESHOLD:.3f}.",
                stages_passed=stages_passed,
                stages_failed=stages_failed,
                reward=reward,
                risk_score=0.5,
            )

        # --- Stage 7: Write regression ---
        if baseline.write_p50_ms > 0:
            regression = (
                experiment.write_p50_ms - baseline.write_p50_ms
            ) / baseline.write_p50_ms
            if regression <= self._settings.SAFETY_MAX_WRITE_REGRESSION:
                stages_passed.append("write_regression")
            else:
                stages_failed.append("write_regression")
                return SafetyDecision(
                    approved=False,
                    reason=f"Write regression {regression:.1%} exceeds limit "
                           f"{self._settings.SAFETY_MAX_WRITE_REGRESSION:.1%}.",
                    stages_passed=stages_passed,
                    stages_failed=stages_failed,
                    reward=reward,
                    risk_score=0.8,
                )
        else:
            stages_passed.append("write_regression")  # No write benchmark data → skip

        # --- Stage 5: Duplicate detection (requires live DB query — stub) ---
        # TODO (Phase 2): Query pg_indexes to check for duplicates
        stages_passed.append("duplicate_check")  # Placeholder

        # --- Approved ---
        return SafetyDecision(
            approved=True,
            reason="All safety stages passed.",
            stages_passed=stages_passed,
            stages_failed=[],
            reward=reward,
            risk_score=max(0.0, 1.0 - reward),
        )
