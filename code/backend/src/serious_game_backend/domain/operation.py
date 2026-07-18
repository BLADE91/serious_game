from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from serious_game_backend.domain.enums import OperationStatus


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    account_id: str
    session_id: str | None
    client_action_id: str
    request_hash: str
    status: OperationStatus = OperationStatus.PROCESSING
    attempt_count: int = 1
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = utc_now_iso()
        if not self.created_at:
            object.__setattr__(self, "created_at", now)
        if not self.updated_at:
            object.__setattr__(self, "updated_at", now)
