from __future__ import annotations

from copy import deepcopy
from threading import RLock

from serious_game_backend.domain.errors import StateVersionConflictError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.operation import OperationRecord
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.domain.llm_runtime import LLMCallAudit, NPCMemory


class InMemoryGameSessionRepository:
    def __init__(self) -> None:
        self._items: dict[str, GameSession] = {}
        self._lock = RLock()

    def create(self, session: GameSession) -> None:
        with self._lock:
            if session.session_id in self._items:
                raise ValueError("duplicate session_id")
            self._items[session.session_id] = deepcopy(session)

    def get_owned(self, session_id: str, account_id: str) -> GameSession | None:
        with self._lock:
            session = self._items.get(session_id)
            if session is None or session.account_id != account_id:
                return None
            return deepcopy(session)

    def latest_active(self, account_id: str) -> GameSession | None:
        with self._lock:
            values = [
                item
                for item in self._items.values()
                if item.account_id == account_id and item.status.value == "active"
            ]
            if not values:
                return None
            return deepcopy(max(values, key=lambda item: item.updated_at))

    def save(self, session: GameSession, *, expected_version: int) -> None:
        with self._lock:
            current = self._items.get(session.session_id)
            if current is None or current.account_id != session.account_id:
                raise StateVersionConflictError("游戏不存在或所有权已变化")
            if current.state_version != expected_version:
                raise StateVersionConflictError(
                    "状态版本冲突",
                    details={"current_state_version": current.state_version},
                )
            if (
                session.processing_action_id is not None
                and current.processing_action_id is not None
                and current.processing_action_id != session.processing_action_id
            ):
                raise StateVersionConflictError("当前游戏已被另一操作占用")
            self._items[session.session_id] = deepcopy(session)


class InMemoryOperationRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], OperationRecord] = {}
        self._lock = RLock()

    def get(
        self,
        account_id: str,
        session_id: str,
        client_action_id: str,
    ) -> OperationRecord | None:
        with self._lock:
            value = self._items.get((account_id, session_id, client_action_id))
            return deepcopy(value) if value is not None else None

    def create(self, operation: OperationRecord) -> None:
        if operation.session_id is None:
            raise ValueError("game operation requires session_id")
        key = (operation.account_id, operation.session_id, operation.client_action_id)
        with self._lock:
            if key in self._items:
                raise ValueError("duplicate idempotency key")
            self._items[key] = deepcopy(operation)

    def save(self, operation: OperationRecord) -> None:
        if operation.session_id is None:
            raise ValueError("game operation requires session_id")
        key = (operation.account_id, operation.session_id, operation.client_action_id)
        with self._lock:
            if key not in self._items:
                raise ValueError("operation does not exist")
            self._items[key] = deepcopy(operation)


class InMemorySessionRequestRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], OperationRecord] = {}
        self._lock = RLock()

    def get(self, account_id: str, client_request_id: str) -> OperationRecord | None:
        with self._lock:
            value = self._items.get((account_id, client_request_id))
            return deepcopy(value) if value is not None else None

    def create(self, request: OperationRecord) -> None:
        key = (request.account_id, request.client_action_id)
        with self._lock:
            if key in self._items:
                raise ValueError("duplicate idempotency key")
            self._items[key] = deepcopy(request)

    def save(self, request: OperationRecord) -> None:
        key = (request.account_id, request.client_action_id)
        with self._lock:
            if key not in self._items:
                raise ValueError("session request does not exist")
            self._items[key] = deepcopy(request)


class InMemoryRuntimeTransactionRepository:
    """内存测试适配器；生产级原子性由持久化适配器保证。"""

    def __init__(
        self,
        sessions: InMemoryGameSessionRepository,
        operations: InMemoryOperationRepository,
        requests: InMemorySessionRequestRepository,
    ) -> None:
        self._sessions = sessions
        self._operations = operations
        self._requests = requests
        self._lock = RLock()

    def reserve_operation(
        self,
        session: GameSession,
        *,
        expected_version: int,
        operation: OperationRecord,
        create_operation: bool,
    ) -> None:
        with self._lock:
            self._sessions.save(session, expected_version=expected_version)
            if create_operation:
                self._operations.create(operation)
            else:
                self._operations.save(operation)

    def finish_operation(
        self,
        session: GameSession,
        *,
        expected_version: int,
        operation: OperationRecord,
    ) -> None:
        with self._lock:
            self._sessions.save(session, expected_version=expected_version)
            self._operations.save(operation)

    def complete_session_request(
        self,
        session: GameSession,
        request: OperationRecord,
    ) -> None:
        with self._lock:
            self._sessions.create(session)
            self._requests.save(request)

class InMemoryScriptPackageRepository:
    def __init__(self, packages: list[ScriptPackage]) -> None:
        self._items = {item.package_id: item for item in packages}

    def get(self, package_id: str) -> ScriptPackage | None:
        return self._items.get(package_id)


class InMemoryLLMCallAuditRepository:
    def __init__(self) -> None:
        self._items: list[LLMCallAudit] = []
        self._lock = RLock()

    def save(self, audit: LLMCallAudit) -> None:
        with self._lock:
            self._items.append(deepcopy(audit))

    def successful_for_operation(
        self, operation_id: str, request_hash: str
    ) -> LLMCallAudit | None:
        with self._lock:
            matches = [
                item for item in self._items
                if item.operation_id == operation_id
                and item.request_hash == request_hash
                and item.status == "succeeded"
            ]
            return deepcopy(matches[-1]) if matches else None

    def list_for_session(self, session_id: str) -> tuple[LLMCallAudit, ...]:
        with self._lock:
            return tuple(deepcopy(
                [item for item in self._items if item.session_id == session_id]
            ))


class InMemoryNPCMemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, NPCMemory] = {}
        self._lock = RLock()

    def save(self, memory: NPCMemory) -> None:
        with self._lock:
            self._items[memory.memory_id] = deepcopy(memory)

    def active_for_npc(
        self, session_id: str, npc_id: str, story_day: int
    ) -> tuple[NPCMemory, ...]:
        with self._lock:
            values = [
                item for item in self._items.values()
                if item.session_id == session_id
                and item.npc_id == npc_id
                and item.is_active(story_day)
            ]
            return tuple(deepcopy(sorted(values, key=lambda item: item.created_at)))

    def invalidate(self, memory_ids: tuple[str, ...], invalidated_at: str) -> None:
        with self._lock:
            for memory_id in memory_ids:
                current = self._items.get(memory_id)
                if current is not None:
                    from dataclasses import replace
                    self._items[memory_id] = replace(
                        current, invalidated_at=invalidated_at
                    )
