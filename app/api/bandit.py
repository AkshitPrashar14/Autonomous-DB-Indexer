"""
DBAutonomy — Bandit API Router

GET /api/bandit        Current bandit state and statistics
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.container import get_container

router = APIRouter()


@router.get("/")
async def get_bandit_state():
    """
    Return current LinUCB bandit state.

    Includes:
    - Algorithm details
    - Number of observations per action
    - Alpha (exploration parameter)
    - Recent rewards
    - Action distribution
    """
    container = get_container()
    bandit = container.bandit_policy
    state = bandit.state

    return {
        "algorithm": "LinUCB-Disjoint",
        "alpha": state.alpha,
        "context_dimension": state.d,
        "total_updates": state.total_updates,
        "n_actions_known": len(state.actions),
        "actions": bandit.action_summary(),
        "saved_at": state.saved_at.isoformat(),
    }


@router.get("/health")
async def bandit_health():
    """Check if bandit state is loaded and valid."""
    container = get_container()
    bandit = container.bandit_policy
    state = bandit.state
    return {
        "status": "ok",
        "total_updates": state.total_updates,
        "n_actions": len(state.actions),
    }
