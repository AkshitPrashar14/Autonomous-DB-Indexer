"""
DBAutonomy — LinUCB Disjoint Contextual Bandit

Mathematical formulation (from BANDIT_SPEC.md):

  For each action fingerprint a:
    A_a ∈ ℝ^{d×d}   initialised to I_d
    b_a ∈ ℝ^d        initialised to 0

    θ̂_a = A_a^{-1} b_a
    UCB_a(x) = θ̂_a·x  +  α · sqrt(x·A_a^{-1}·x)

  Selection:  a* = argmax_a UCB_a(x)

  Update after observing reward r:
    A_a ← A_a + x·xᵀ
    b_a ← b_a + r·x

Numerical stability:
  - We solve A·θ = b via np.linalg.solve instead of explicit inversion
    for the weight vector (more numerically stable).
  - For the confidence term x·A⁻¹·x we solve A·v = x, then compute x·v.
  - A is symmetric positive-definite by construction; we catch
    LinAlgError and fall back to identity to survive corruption (FL-005).
  - A is regularised with a small diagonal jitter (1e-6) to avoid
    near-singular matrices when d >> n_observations.

ADR-009 (DECIDED): Gemini ranking is a tiebreaker ONLY when UCB scores
are within TIEBREAK_EPS of each other. LinUCB is always the decision
mechanism.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np

from app.core.exceptions import BanditStateCorruptError
from app.models.domain import (
    BanditActionState,
    BanditState,
    ContextVector,
    IndexCandidate,
)

logger = logging.getLogger(__name__)

# When two actions are within this distance of each other's UCB score,
# use Gemini's rank as a deterministic tiebreaker (lower rank = better).
_TIEBREAK_EPS: float = 1e-6

# Small regularisation added to diagonal of A to avoid near-singularity
_JITTER: float = 1e-6


class _ActionModel:
    """
    Per-action LinUCB matrices.

    Maintains A (d×d) and b (d,) for one candidate fingerprint.
    """

    __slots__ = ("fingerprint", "A", "b", "n_obs")

    def __init__(self, fingerprint: str, d: int) -> None:
        self.fingerprint = fingerprint
        self.A: np.ndarray = np.eye(d, dtype=np.float64)
        self.b: np.ndarray = np.zeros(d, dtype=np.float64)
        self.n_obs: int = 0

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def ucb_score(self, x: np.ndarray, alpha: float) -> float:
        """
        Compute UCB(x) = θ̂·x + α·sqrt(x·A⁻¹·x).

        Uses np.linalg.solve to avoid explicit matrix inversion.
        """
        A_reg = self.A + _JITTER * np.eye(len(self.b))

        try:
            # Solve A·θ = b  →  θ = A⁻¹·b
            theta = np.linalg.solve(A_reg, self.b)

            # Solve A·v = x  →  v = A⁻¹·x
            v = np.linalg.solve(A_reg, x)

            exploitation = float(theta @ x)
            confidence = float(np.sqrt(max(0.0, float(x @ v))))
            return exploitation + alpha * confidence

        except np.linalg.LinAlgError:
            # Degenerate matrix — treat as pure exploration
            logger.warning(
                "LinAlgError for action %s; falling back to pure exploration score",
                self.fingerprint,
            )
            return alpha * float(np.linalg.norm(x))

    # ------------------------------------------------------------------
    # Update (the learning step)
    # ------------------------------------------------------------------

    def update(self, x: np.ndarray, reward: float) -> None:
        """
        Update sufficient statistics:
          A ← A + x·xᵀ
          b ← b + r·x
        """
        self.A += np.outer(x, x)
        self.b += reward * x
        self.n_obs += 1

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_state(self) -> BanditActionState:
        return BanditActionState(
            fingerprint=self.fingerprint,
            A=self.A.tolist(),
            b=self.b.tolist(),
            n_observations=self.n_obs,
        )

    @classmethod
    def from_state(cls, state: BanditActionState) -> "_ActionModel":
        d = len(state.b)
        obj = cls.__new__(cls)
        obj.fingerprint = state.fingerprint
        obj.A = np.array(state.A, dtype=np.float64)
        obj.b = np.array(state.b, dtype=np.float64)
        obj.n_obs = state.n_observations

        # Validate positive-definiteness (FL-005 mitigation)
        try:
            np.linalg.cholesky(obj.A)
        except np.linalg.LinAlgError:
            logger.warning(
                "Loaded action %s has non-PD A matrix; resetting to identity",
                state.fingerprint,
            )
            obj.A = np.eye(d, dtype=np.float64)
            obj.b = np.zeros(d, dtype=np.float64)
            obj.n_obs = 0

        return obj


class LinUCBPolicy:
    """
    LinUCB Disjoint Contextual Bandit.

    Each candidate fingerprint has its own _ActionModel.
    New fingerprints are initialised lazily on first encounter.

    Thread-safety: NOT thread-safe. Each AgentWorker instance owns its
    own LinUCBPolicy. Bandit state is synchronised through the
    DecisionRepository between worker restarts only.
    """

    def __init__(self, alpha: float = 1.0, d: int = 20) -> None:
        self.alpha: float = alpha
        self.d: int = d
        self._actions: dict[str, _ActionModel] = {}
        self._total_updates: int = 0

    # ------------------------------------------------------------------
    # Public interface (IBanditPolicy)
    # ------------------------------------------------------------------

    def select(
        self,
        context_vector: ContextVector,
        candidates: list[IndexCandidate],
    ) -> IndexCandidate:
        """
        Select the candidate with the highest UCB score.

        ADR-009: when scores are within _TIEBREAK_EPS of each other,
        the candidate with the lower gemini_rank wins.
        """
        if not candidates:
            raise ValueError("candidates must not be empty")

        x = np.asarray(context_vector, dtype=np.float64)
        if x.shape != (self.d,):
            raise ValueError(
                f"Context vector has wrong shape: {x.shape}, expected ({self.d},)"
            )

        # Score every candidate
        scored: list[tuple[float, int, IndexCandidate]] = []
        for c in candidates:
            model = self._get_or_create(c.fingerprint)
            score = model.ucb_score(x, self.alpha)
            scored.append((score, c.gemini_rank, c))

        # Sort: descending UCB, then ascending Gemini rank (tiebreaker)
        scored.sort(key=lambda t: (-t[0], t[1]))

        best_score = scored[0][0]
        chosen = scored[0][2]

        # Log all scores for observability
        score_str = ", ".join(
            f"{c.fingerprint}={s:.4f}" for s, _, c in scored
        )
        logger.info(
            "LinUCB scores [%s] → selected %s (UCB=%.4f, n_obs=%d)",
            score_str,
            chosen.fingerprint,
            best_score,
            self._actions[chosen.fingerprint].n_obs,
        )
        return chosen

    def update(
        self,
        context_vector: ContextVector,
        action_fingerprint: str,
        reward: float,
    ) -> None:
        """
        Update A and b for the chosen action with observed reward.

        This is the genuine learning step. Must be called before
        saving state to the DecisionRepository.
        """
        x = np.asarray(context_vector, dtype=np.float64)
        model = self._get_or_create(action_fingerprint)
        model.update(x, reward)
        self._total_updates += 1
        logger.debug(
            "Bandit updated: action=%s reward=%.4f total_updates=%d",
            action_fingerprint,
            reward,
            self._total_updates,
        )

    @property
    def state(self) -> BanditState:
        """Return a serialisable snapshot of the full bandit state."""
        return BanditState(
            version=1,
            alpha=self.alpha,
            d=self.d,
            actions={fp: m.to_state() for fp, m in self._actions.items()},
            total_updates=self._total_updates,
            saved_at=datetime.utcnow(),
        )

    def load_state(self, state: BanditState) -> None:
        """Restore bandit state from a persisted snapshot (FL-005 safe)."""
        if state.d != self.d:
            raise BanditStateCorruptError(
                f"Persisted state has d={state.d}, policy expects d={self.d}"
            )
        self.alpha = state.alpha
        self._total_updates = state.total_updates
        self._actions = {}
        for fp, action_state in state.actions.items():
            self._actions[fp] = _ActionModel.from_state(action_state)
        logger.info(
            "Loaded bandit state: %d actions, %d total updates",
            len(self._actions),
            self._total_updates,
        )

    # ------------------------------------------------------------------
    # Introspection helpers (for API / dashboard)
    # ------------------------------------------------------------------

    def action_summary(self) -> list[dict]:
        """Return per-action statistics for the dashboard."""
        summary = []
        for fp, m in self._actions.items():
            try:
                A_reg = m.A + _JITTER * np.eye(self.d)
                theta = np.linalg.solve(A_reg, m.b)
                mean_weight = float(np.mean(np.abs(theta)))
            except np.linalg.LinAlgError:
                mean_weight = 0.0
            summary.append({
                "fingerprint": fp,
                "n_observations": m.n_obs,
                "mean_abs_weight": mean_weight,
            })
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, fingerprint: str) -> _ActionModel:
        if fingerprint not in self._actions:
            logger.debug("New action initialised: %s", fingerprint)
            self._actions[fingerprint] = _ActionModel(fingerprint, self.d)
        return self._actions[fingerprint]
