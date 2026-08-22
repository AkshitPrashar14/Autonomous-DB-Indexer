"""
DBAutonomy — RewardCalculator

Pure function. No I/O. No AI.
See docs/REWARD_SPEC.md for the full specification.
"""

from __future__ import annotations

import math

from app.core.config import Settings
from app.models.domain import BenchmarkResult


class RewardCalculator:
    """
    Computes a scalar reward ∈ [-1.0, 1.0] from two BenchmarkResults.

    All weights are configurable via Settings.
    """

    def __init__(self, settings: Settings):
        self._w_latency = settings.REWARD_WEIGHT_LATENCY
        self._w_write = settings.REWARD_WEIGHT_WRITE
        self._max_write_regression = settings.SAFETY_MAX_WRITE_REGRESSION

    def compute(
        self,
        baseline: BenchmarkResult,
        experiment: BenchmarkResult,
    ) -> float:
        """
        Compute reward from before/after benchmark results.

        See docs/REWARD_SPEC.md for the formula.
        """
        # --- Primary: Latency improvement ---
        if baseline.query_p50_ms <= 0:
            latency_improvement = 0.0
        else:
            latency_improvement = (
                baseline.query_p50_ms - experiment.query_p50_ms
            ) / baseline.query_p50_ms

        latency_improvement = _clip(latency_improvement, -1.0, 1.0)

        # --- Write regression penalty ---
        if baseline.write_p50_ms <= 0:
            write_penalty = 0.0
        else:
            write_regression = (
                experiment.write_p50_ms - baseline.write_p50_ms
            ) / baseline.write_p50_ms
            write_penalty = max(0.0, write_regression - self._max_write_regression)
            write_penalty = min(write_penalty, 1.0)

        # --- Cache efficiency bonus ---
        baseline_cache = _cache_ratio(baseline)
        experiment_cache = _cache_ratio(experiment)
        cache_bonus = _clip((experiment_cache - baseline_cache) * 0.1, -0.1, 0.1)

        # --- Final reward ---
        reward = (
            self._w_latency * latency_improvement
            - self._w_write * write_penalty
            + cache_bonus
        )
        return _clip(reward, -1.0, 1.0)


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _cache_ratio(result: BenchmarkResult) -> float:
    total = result.shared_blks_hit + result.shared_blks_read
    if total == 0:
        return 0.0
    return result.shared_blks_hit / total
