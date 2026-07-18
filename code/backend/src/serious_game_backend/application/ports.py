from __future__ import annotations

from typing import Protocol

from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.llm import RoleTurnContext, RoleTurnResult
from serious_game_backend.domain.llm_runtime import LLMCallAudit, NPCMemory
from serious_game_backend.domain.operation import OperationRecord
from serious_game_backend.domain.script_package import ScriptPackage


class GameSessionRepository(Protocol):
    def create(self, session: GameSession) -> None: ...

    def get_owned(self, session_id: str, account_id: str) -> GameSession | None: ...

    def latest_active(self, account_id: str) -> GameSession | None: ...

    def save(self, session: GameSession, *, expected_version: int) -> None: ...


class OperationRepository(Protocol):
    def get(
        self,
        account_id: str,
        session_id: str,
        client_action_id: str,
    ) -> OperationRecord | None: ...

    def create(self, operation: OperationRecord) -> None: ...

    def save(self, operation: OperationRecord) -> None: ...


class SessionRequestRepository(Protocol):
    def get(self, account_id: str, client_request_id: str) -> OperationRecord | None: ...

    def create(self, request: OperationRecord) -> None: ...

    def save(self, request: OperationRecord) -> None: ...


class RuntimeTransactionRepository(Protocol):
    def reserve_operation(
        self,
        session: GameSession,
        *,
        expected_version: int,
        operation: OperationRecord,
        create_operation: bool,
    ) -> None: ...

    def finish_operation(
        self,
        session: GameSession,
        *,
        expected_version: int,
        operation: OperationRecord,
    ) -> None: ...

    def complete_session_request(
        self,
        session: GameSession,
        request: OperationRecord,
    ) -> None: ...


class ScriptPackageRepository(Protocol):
    def get(self, package_id: str) -> ScriptPackage | None: ...


class RoleLLMGateway(Protocol):
    def run_turn(self, context: RoleTurnContext) -> RoleTurnResult: ...


class LLMCallAuditRepository(Protocol):
    def save(self, audit: LLMCallAudit) -> None: ...

    def successful_for_operation(
        self, operation_id: str, request_hash: str
    ) -> LLMCallAudit | None: ...

    def list_for_session(self, session_id: str) -> tuple[LLMCallAudit, ...]: ...


class NPCMemoryRepository(Protocol):
    def save(self, memory: NPCMemory) -> None: ...

    def active_for_npc(
        self, session_id: str, npc_id: str, story_day: int
    ) -> tuple[NPCMemory, ...]: ...

    def invalidate(self, memory_ids: tuple[str, ...], invalidated_at: str) -> None: ...
