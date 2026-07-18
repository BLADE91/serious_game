from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    CONTENT_BLOCKED = "content_blocked"


class ActionInputMode(StrEnum):
    TOOL = "tool"
    FREE_TEXT = "free_text"
    DECISION = "decision"


class OperationStatus(StrEnum):
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"


class ActionCostTier(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    ACCEPTANCE = "acceptance"


class NPCStateTier(StrEnum):
    DEEP = "deep"
    LIMITED = "limited"
    AMBIENT = "ambient"


class AvailabilityMode(StrEnum):
    FREE = "free"
    LIMITED = "limited"
    CLOSED = "closed"


class DecisionState(StrEnum):
    PRESENTED = "presented"
    PENDING_DECISION = "pending_decision"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    EFFECTS_COMMITTED = "effects_committed"


class MetricAuthority(StrEnum):
    SCRIPT = "script"
    DERIVED_RULE = "derived_rule"
    LLM_BOUNDED = "llm_bounded"
