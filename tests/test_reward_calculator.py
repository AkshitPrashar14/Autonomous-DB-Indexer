"""
DBAutonomy — Reward Calculator Tests

These tests run without any mocks (pure function).
"""

import pytest

from app.core.config import Settings
from app.evaluation.reward_calculator import RewardCalculator
from app.models.domain import BenchmarkResult


def make_result(**kwargs) -> BenchmarkResult:
    defaults = {
        "query_p50_ms": 1000.0,
        "query_p95_ms": 1500.0,
        "query_mean_ms": 1100.0,
        "planning_time_ms": 5.0,
        "shared_blks_hit": 100,
        "shared_blks_read": 900,
        "write_p50_ms": 50.0,
        "write_p95_ms": 80.0,
        "n_iterations": 10,
    }
    defaults.update(kwargs)
    return BenchmarkResult(**defaults)


def make_calculator() -> RewardCalculator:
    settings = Settings(
        GEMINI_API_KEY="fake",
        SAFETY_ALLOWED_TABLES="orders",
        REWARD_WEIGHT_LATENCY=0.80,
        REWARD_WEIGHT_WRITE=0.20,
        SAFETY_MAX_WRITE_REGRESSION=0.10,
    )
    return RewardCalculator(settings)


class TestRewardCalculator:
    def test_perfect_improvement(self):
        calc = make_calculator()
        baseline = make_result(query_p50_ms=1000.0)
        experiment = make_result(query_p50_ms=100.0)
        reward = calc.compute(baseline, experiment)
        assert 0.7 < reward <= 1.0, f"Expected high reward, got {reward}"

    def test_no_improvement(self):
        calc = make_calculator()
        baseline = make_result(query_p50_ms=1000.0)
        experiment = make_result(query_p50_ms=1000.0)
        reward = calc.compute(baseline, experiment)
        assert -0.1 <= reward <= 0.1, f"Expected near-zero reward, got {reward}"

    def test_regression_penalised(self):
        calc = make_calculator()
        baseline = make_result(query_p50_ms=1000.0)
        experiment = make_result(query_p50_ms=1200.0)  # 20% slower
        reward = calc.compute(baseline, experiment)
        assert reward < 0, f"Expected negative reward for regression, got {reward}"

    def test_write_regression_reduces_reward(self):
        calc = make_calculator()
        baseline = make_result(query_p50_ms=1000.0, write_p50_ms=50.0)
        experiment = make_result(query_p50_ms=200.0, write_p50_ms=80.0)  # 60% write regression
        reward_with_write_penalty = calc.compute(baseline, experiment)

        # Without write regression: same query improvement but no write cost
        baseline2 = make_result(query_p50_ms=1000.0, write_p50_ms=0.0)
        experiment2 = make_result(query_p50_ms=200.0, write_p50_ms=0.0)
        reward_without = calc.compute(baseline2, experiment2)

        assert reward_with_write_penalty < reward_without, \
            "Write regression must reduce reward"

    def test_reward_bounded(self):
        calc = make_calculator()
        for _ in range(100):
            import random
            baseline = make_result(
                query_p50_ms=random.uniform(100, 10000),
                write_p50_ms=random.uniform(0, 500),
            )
            experiment = make_result(
                query_p50_ms=random.uniform(10, 15000),
                write_p50_ms=random.uniform(0, 1000),
            )
            reward = calc.compute(baseline, experiment)
            assert -1.0 <= reward <= 1.0, f"Reward out of bounds: {reward}"

    def test_zero_baseline_duration(self):
        calc = make_calculator()
        baseline = make_result(query_p50_ms=0.0)
        experiment = make_result(query_p50_ms=0.0)
        reward = calc.compute(baseline, experiment)
        assert reward == pytest.approx(0.0, abs=0.15)
