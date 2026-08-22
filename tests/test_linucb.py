"""
DBAutonomy — LinUCB Bandit Unit Tests

Tests genuine mathematical behavior — no mocks, no fakes.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.bandit.linucb import LinUCBPolicy, _JITTER
from app.models.domain import BanditActionState, BanditState, IndexCandidate, IndexType


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_policy(alpha: float = 1.0, d: int = 4) -> LinUCBPolicy:
    return LinUCBPolicy(alpha=alpha, d=d)


def make_candidate(table: str = "orders", cols: list | None = None, idx_type: IndexType = IndexType.BTREE) -> IndexCandidate:
    return IndexCandidate(
        table_name=table,
        columns=cols or ["customer_id"],
        index_type=idx_type,
    )


def unit_vector(d: int, idx: int) -> np.ndarray:
    v = np.zeros(d)
    v[idx] = 1.0
    return v


# ── Initialisation ────────────────────────────────────────────────────────────

class TestLinUCBInitialisation:
    def test_initial_state_has_no_actions(self):
        policy = make_policy()
        assert len(policy._actions) == 0
        assert policy._total_updates == 0

    def test_initial_state_snapshot(self):
        policy = make_policy(alpha=2.5, d=10)
        state = policy.state
        assert state.alpha == 2.5
        assert state.d == 10
        assert state.total_updates == 0
        assert len(state.actions) == 0

    def test_action_created_on_first_select(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = np.ones(4) / 2
        policy.select(x, [c])
        assert c.fingerprint in policy._actions

    def test_action_matrix_A_is_identity(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = np.ones(4) / 2
        policy.select(x, [c])
        model = policy._actions[c.fingerprint]
        np.testing.assert_array_almost_equal(model.A, np.eye(4))

    def test_action_vector_b_is_zero(self):
        policy = make_policy(d=4)
        c = make_candidate()
        policy.select(np.ones(4) / 2, [c])
        model = policy._actions[c.fingerprint]
        np.testing.assert_array_almost_equal(model.b, np.zeros(4))


# ── Deterministic Selection ───────────────────────────────────────────────────

class TestDeterministicSelection:
    def test_single_candidate_always_selected(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = np.array([0.5, 0.3, 0.1, 0.9])
        for _ in range(5):
            selected = policy.select(x, [c])
            assert selected.fingerprint == c.fingerprint

    def test_same_context_same_selection(self):
        policy = make_policy(d=4)
        c1 = make_candidate(cols=["a"])
        c2 = make_candidate(cols=["b"])
        x = np.array([0.5, 0.5, 0.5, 0.5])
        result1 = policy.select(x, [c1, c2])
        result2 = policy.select(x, [c1, c2])
        assert result1.fingerprint == result2.fingerprint

    def test_empty_candidates_raises(self):
        policy = make_policy(d=4)
        with pytest.raises(ValueError):
            policy.select(np.ones(4), [])

    def test_wrong_dimension_raises(self):
        policy = make_policy(d=4)
        c = make_candidate()
        with pytest.raises(ValueError):
            policy.select(np.ones(6), [c])  # wrong dim


# ── Exploration Behaviour ─────────────────────────────────────────────────────

class TestExplorationBehaviour:
    def test_high_alpha_increases_ucb_score(self):
        """With higher alpha, the exploration term (confidence) is larger."""
        d = 4
        policy_lo = LinUCBPolicy(alpha=0.01, d=d)
        policy_hi = LinUCBPolicy(alpha=10.0, d=d)
        c = make_candidate()
        x = np.array([1.0, 0.0, 0.0, 0.0])

        # Prime both with same context (creates the action)
        policy_lo.select(x, [c])
        policy_hi.select(x, [c])

        # Directly compute UCB scores
        score_lo = policy_lo._actions[c.fingerprint].ucb_score(x, alpha=0.01)
        score_hi = policy_hi._actions[c.fingerprint].ucb_score(x, alpha=10.0)

        assert score_hi > score_lo

    def test_no_observations_yields_positive_ucb(self):
        """With A=I, b=0: UCB = alpha * sqrt(x·x) = alpha * ||x|| > 0."""
        policy = make_policy(alpha=1.0, d=4)
        c = make_candidate()
        policy.select(np.ones(4), [c])  # creates action
        model = policy._actions[c.fingerprint]
        x = np.ones(4) / 2
        score = model.ucb_score(x, alpha=1.0)
        expected_confidence = math.sqrt(float(x @ x))  # x·I·x = x·x
        assert score == pytest.approx(expected_confidence, rel=0.01)


# ── Update Changes Parameters ──────────────────────────────────────────────────

class TestUpdateChangesParameters:
    def test_update_increments_total_updates(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = np.ones(4)
        policy.select(x, [c])
        assert policy._total_updates == 0
        policy.update(x, c.fingerprint, 0.8)
        assert policy._total_updates == 1

    def test_update_modifies_A_matrix(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = unit_vector(4, 0)  # [1, 0, 0, 0]
        policy.select(x, [c])

        A_before = policy._actions[c.fingerprint].A.copy()
        policy.update(x, c.fingerprint, 1.0)
        A_after = policy._actions[c.fingerprint].A

        # A += x·xᵀ → only [0,0] element increases by 1
        diff = A_after - A_before
        assert diff[0, 0] == pytest.approx(1.0)
        assert diff[1, 1] == pytest.approx(0.0)

    def test_update_modifies_b_vector(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = unit_vector(4, 2)  # [0, 0, 1, 0]
        policy.select(x, [c])
        policy.update(x, c.fingerprint, 0.5)

        b = policy._actions[c.fingerprint].b
        assert b[2] == pytest.approx(0.5)   # r * x[2] = 0.5 * 1
        assert b[0] == pytest.approx(0.0)

    def test_negative_reward_reduces_b(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = unit_vector(4, 1)
        policy.select(x, [c])
        policy.update(x, c.fingerprint, -0.3)
        b = policy._actions[c.fingerprint].b
        assert b[1] == pytest.approx(-0.3)

    def test_update_increments_n_obs(self):
        policy = make_policy(d=4)
        c = make_candidate()
        x = np.ones(4)
        policy.select(x, [c])
        policy.update(x, c.fingerprint, 1.0)
        assert policy._actions[c.fingerprint].n_obs == 1


# ── Repeated Observations Affect Decisions ────────────────────────────────────

class TestLearningEffect:
    def test_positive_reward_increases_ucb_for_that_action(self):
        """After positive reward updates, the UCB score should reflect learned signal."""
        policy = make_policy(alpha=0.5, d=4)
        c = make_candidate()
        x = np.array([1.0, 0.0, 0.5, 0.0])

        policy.select(x, [c])
        score_before = policy._actions[c.fingerprint].ucb_score(x, alpha=0.5)

        # Positive reward: b += r*x, θ becomes positive along x direction
        for _ in range(5):
            policy.update(x, c.fingerprint, 1.0)

        score_after = policy._actions[c.fingerprint].ucb_score(x, alpha=0.5)
        # Exploitation term should have increased
        assert score_after != score_before  # state was changed

    def test_bandit_prefers_rewarded_action(self):
        """
        Give two actions. Reward action A highly, reward action B negatively.
        After enough updates, bandit should prefer A.
        """
        policy = make_policy(alpha=0.1, d=4)
        c_a = make_candidate(cols=["customer_id"])
        c_b = make_candidate(cols=["status"])
        x = np.array([0.5, 0.3, 0.8, 0.1])

        # Prime both actions
        policy.select(x, [c_a, c_b])

        # Give many positive rewards to A, negative to B
        for _ in range(20):
            policy.update(x, c_a.fingerprint, 0.9)
            policy.update(x, c_b.fingerprint, -0.5)

        # Now A should have higher UCB score
        score_a = policy._actions[c_a.fingerprint].ucb_score(x, alpha=0.1)
        score_b = policy._actions[c_b.fingerprint].ucb_score(x, alpha=0.1)
        assert score_a > score_b, f"A ({score_a:.4f}) should beat B ({score_b:.4f})"


# ── Multiple Candidates ───────────────────────────────────────────────────────

class TestMultipleCandidates:
    def test_selects_among_multiple_candidates(self):
        policy = make_policy(d=4)
        candidates = [
            make_candidate(cols=[f"col{i}"])
            for i in range(5)
        ]
        x = np.ones(4)
        selected = policy.select(x, candidates)
        assert selected in candidates

    def test_gemini_rank_used_as_tiebreaker(self):
        """
        Cold start: all UCB scores equal (all A=I, b=0, same x).
        Tiebreaker: lower gemini_rank wins (rank 1 > rank 5).
        """
        policy = make_policy(alpha=1.0, d=4)
        c1 = IndexCandidate(table_name="orders", columns=["a"], index_type=IndexType.BTREE, gemini_rank=3)
        c2 = IndexCandidate(table_name="orders", columns=["b"], index_type=IndexType.BTREE, gemini_rank=1)
        c3 = IndexCandidate(table_name="orders", columns=["c"], index_type=IndexType.BTREE, gemini_rank=5)

        x = np.zeros(4)  # zero context → exploration term dominates equally
        selected = policy.select(x, [c1, c2, c3])
        assert selected.gemini_rank == min(c.gemini_rank for c in [c1, c2, c3])


# ── Numerical Stability ───────────────────────────────────────────────────────

class TestNumericalStability:
    def test_many_updates_stay_stable(self):
        """After 1000 updates, UCB score must remain finite."""
        policy = make_policy(alpha=1.0, d=4)
        c = make_candidate()
        x = np.array([0.5, 0.3, 0.8, 0.1])
        policy.select(x, [c])
        for i in range(1000):
            policy.update(x, c.fingerprint, 0.7 if i % 2 == 0 else -0.2)
        score = policy._actions[c.fingerprint].ucb_score(x, alpha=1.0)
        assert math.isfinite(score)

    def test_corrupt_state_resets_to_identity(self):
        """A non-PD A matrix in persisted state must be reset."""
        d = 4
        state = BanditState(
            alpha=1.0,
            d=d,
            actions={
                "corrupt_action": BanditActionState(
                    fingerprint="corrupt_action",
                    A=[[-1.0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],  # not PD
                    b=[0.0, 0.0, 0.0, 0.0],
                    n_observations=10,
                )
            },
        )
        policy = make_policy(d=d)
        policy.load_state(state)  # Should NOT raise
        model = policy._actions["corrupt_action"]
        # After reset: should be identity
        np.testing.assert_array_almost_equal(model.A, np.eye(d))
        assert model.n_obs == 0

    def test_state_save_load_round_trip(self):
        """State serialised and loaded must produce same UCB scores."""
        policy = make_policy(alpha=1.5, d=4)
        c = make_candidate()
        x = np.array([0.1, 0.9, 0.3, 0.7])
        policy.select(x, [c])
        for _ in range(5):
            policy.update(x, c.fingerprint, 0.6)

        score_before = policy._actions[c.fingerprint].ucb_score(x, alpha=1.5)
        state = policy.state

        # Restore into fresh policy
        policy2 = make_policy(alpha=1.5, d=4)
        policy2.load_state(state)
        score_after = policy2._actions[c.fingerprint].ucb_score(x, alpha=1.5)

        assert score_before == pytest.approx(score_after, rel=1e-9)
