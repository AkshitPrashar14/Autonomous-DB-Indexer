"""
DBAutonomy — Observability Events Model
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class PipelineState(str, Enum):
    DETECTED = "DETECTED"
    PARSED = "PARSED"
    SCHEMA_ANALYZED = "SCHEMA_ANALYZED"
    CANDIDATES_GENERATED = "CANDIDATES_GENERATED"
    CONTEXT_BUILT = "CONTEXT_BUILT"
    BANDIT_SELECTED = "BANDIT_SELECTED"
    SHADOW_STARTED = "SHADOW_STARTED"
    BASELINE_COMPLETE = "BASELINE_COMPLETE"
    CANDIDATE_COMPLETE = "CANDIDATE_COMPLETE"
    REWARD_CALCULATED = "REWARD_CALCULATED"
    SAFETY_EVALUATED = "SAFETY_EVALUATED"
    DEPLOYED = "DEPLOYED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PipelineEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    state: PipelineState
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
