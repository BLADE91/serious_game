from __future__ import annotations

import json
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from serious_game_backend.domain.errors import StateVersionConflictError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.operation import OperationRecord
from serious_game_backend.domain.llm_runtime import LLMCallAudit, NPCMemory
from serious_game_backend.infrastructure.repositories.codec import (
    decode_operation,
    decode_session,
    dumps,
    encode_operation,
    encode_session,
)


class SqliteRuntimeStore:
    """开发与测试用持久化运行库；每个方法都是独立短事务。"""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("pragma journal_mode = wal")
            connection.executescript(
                """
                create table if not exists runtime_schema_versions (
                  version integer primary key,
                  applied_at text not null default current_timestamp
                );

                create table if not exists runtime_game_sessions (
                  session_id text primary key,
                  account_id text not null,
                  status text not null,
                  state_version integer not null,
                  processing_action_id text null,
                  updated_at text not null,
                  payload_json text not null
                );
                create index if not exists idx_runtime_sessions_account
                  on runtime_game_sessions(account_id, status, updated_at);

                create table if not exists runtime_operations (
                  operation_id text primary key,
                  account_id text not null,
                  session_id text not null,
                  client_action_id text not null,
                  status text not null,
                  payload_json text not null,
                  unique(account_id, session_id, client_action_id)
                );

                create table if not exists runtime_session_requests (
                  account_id text not null,
                  client_request_id text not null,
                  status text not null,
                  payload_json text not null,
                  primary key(account_id, client_request_id)
                );

                create table if not exists runtime_llm_call_audits (
                  audit_id text primary key,
                  session_id text not null,
                  operation_id text not null,
                  request_hash text not null,
                  status text not null,
                  payload_json text not null
                );
                create index if not exists idx_runtime_llm_session
                  on runtime_llm_call_audits(session_id, status);
                create index if not exists idx_runtime_llm_operation
                  on runtime_llm_call_audits(operation_id, request_hash, status);

                create table if not exists runtime_npc_memories (
                  memory_id text primary key,
                  session_id text not null,
                  npc_id text not null,
                  valid_from_day integer not null,
                  expires_after_day integer null,
                  invalidated_at text null,
                  created_at text not null,
                  payload_json text not null
                );
                create index if not exists idx_runtime_memory_lookup
                  on runtime_npc_memories(session_id, npc_id, valid_from_day);

                insert or ignore into runtime_schema_versions(version) values (1);
                insert or ignore into runtime_schema_versions(version) values (2);
                """
            )


class SqliteLLMCallAuditRepository:
    def __init__(self, store: SqliteRuntimeStore) -> None:
        self._store = store

    def save(self, audit: LLMCallAudit) -> None:
        with self._store.connect() as connection:
            connection.execute(
                """
                insert into runtime_llm_call_audits(
                  audit_id, session_id, operation_id, request_hash, status, payload_json
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    audit.audit_id, audit.session_id, audit.operation_id,
                    audit.request_hash, audit.status,
                    dumps(asdict(audit)),
                ),
            )

    def successful_for_operation(
        self, operation_id: str, request_hash: str
    ) -> LLMCallAudit | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """
                select payload_json from runtime_llm_call_audits
                where operation_id = ? and request_hash = ? and status = 'succeeded'
                order by rowid desc limit 1
                """,
                (operation_id, request_hash),
            ).fetchone()
        return LLMCallAudit(**json.loads(row["payload_json"])) if row else None

    def list_for_session(self, session_id: str) -> tuple[LLMCallAudit, ...]:
        with self._store.connect() as connection:
            rows = connection.execute(
                """
                select payload_json from runtime_llm_call_audits
                where session_id = ? order by rowid
                """,
                (session_id,),
            ).fetchall()
        return tuple(LLMCallAudit(**json.loads(row["payload_json"])) for row in rows)


class SqliteNPCMemoryRepository:
    def __init__(self, store: SqliteRuntimeStore) -> None:
        self._store = store

    def save(self, memory: NPCMemory) -> None:
        with self._store.connect() as connection:
            connection.execute(
                """
                insert into runtime_npc_memories(
                  memory_id, session_id, npc_id, valid_from_day,
                  expires_after_day, invalidated_at, created_at, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id, memory.session_id, memory.npc_id,
                    memory.valid_from_day, memory.expires_after_day,
                    memory.invalidated_at, memory.created_at,
                    dumps(asdict(memory)),
                ),
            )

    def active_for_npc(
        self, session_id: str, npc_id: str, story_day: int
    ) -> tuple[NPCMemory, ...]:
        with self._store.connect() as connection:
            rows = connection.execute(
                """
                select payload_json from runtime_npc_memories
                where session_id = ? and npc_id = ? and invalidated_at is null
                  and valid_from_day <= ?
                  and (expires_after_day is null or expires_after_day >= ?)
                order by created_at
                """,
                (session_id, npc_id, story_day, story_day),
            ).fetchall()
        values = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["keywords"] = tuple(payload.get("keywords", ()))
            values.append(NPCMemory(**payload))
        return tuple(values)

    def invalidate(self, memory_ids: tuple[str, ...], invalidated_at: str) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with self._store.connect() as connection:
            rows = connection.execute(
                f"select memory_id, payload_json from runtime_npc_memories where memory_id in ({placeholders})",
                memory_ids,
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload["invalidated_at"] = invalidated_at
                connection.execute(
                    "update runtime_npc_memories set invalidated_at = ?, payload_json = ? where memory_id = ?",
                    (invalidated_at, dumps(payload), row["memory_id"]),
                )


class SqliteGameSessionRepository:
    def __init__(self, store: SqliteRuntimeStore) -> None:
        self._store = store

    def create(self, session: GameSession) -> None:
        try:
            with self._store.connect() as connection:
                connection.execute(
                    """
                    insert into runtime_game_sessions(
                      session_id, account_id, status, state_version,
                      processing_action_id, updated_at, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        session.account_id,
                        session.status.value,
                        session.state_version,
                        session.processing_action_id,
                        session.updated_at,
                        dumps(encode_session(session)),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate session_id") from exc

    def get_owned(self, session_id: str, account_id: str) -> GameSession | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """
                select payload_json from runtime_game_sessions
                where session_id = ? and account_id = ?
                """,
                (session_id, account_id),
            ).fetchone()
        return decode_session(json.loads(row["payload_json"])) if row else None

    def latest_active(self, account_id: str) -> GameSession | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """
                select payload_json from runtime_game_sessions
                where account_id = ? and status = 'active'
                order by updated_at desc limit 1
                """,
                (account_id,),
            ).fetchone()
        return decode_session(json.loads(row["payload_json"])) if row else None

    def save(self, session: GameSession, *, expected_version: int) -> None:
        reservation_guard = ""
        parameters: list[object] = [
            session.status.value,
            session.state_version,
            session.processing_action_id,
            session.updated_at,
            dumps(encode_session(session)),
            session.session_id,
            session.account_id,
            expected_version,
        ]
        if session.processing_action_id is not None:
            reservation_guard = " and processing_action_id is null"
        with self._store.connect() as connection:
            cursor = connection.execute(
                f"""
                update runtime_game_sessions
                set status = ?, state_version = ?, processing_action_id = ?,
                    updated_at = ?, payload_json = ?
                where session_id = ? and account_id = ? and state_version = ?
                {reservation_guard}
                """,
                parameters,
            )
            if cursor.rowcount != 1:
                raise StateVersionConflictError(
                    "状态版本冲突或已有操作占用当前游戏"
                )


class SqliteOperationRepository:
    def __init__(self, store: SqliteRuntimeStore) -> None:
        self._store = store

    def get(
        self,
        account_id: str,
        session_id: str,
        client_action_id: str,
    ) -> OperationRecord | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """
                select payload_json from runtime_operations
                where account_id = ? and session_id = ? and client_action_id = ?
                """,
                (account_id, session_id, client_action_id),
            ).fetchone()
        return decode_operation(json.loads(row["payload_json"])) if row else None

    def create(self, operation: OperationRecord) -> None:
        if operation.session_id is None:
            raise ValueError("game operation requires session_id")
        try:
            with self._store.connect() as connection:
                connection.execute(
                """
                insert into runtime_operations(
                  operation_id, account_id, session_id, client_action_id,
                  status, payload_json
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.operation_id,
                    operation.account_id,
                    operation.session_id,
                    operation.client_action_id,
                    operation.status.value,
                    dumps(encode_operation(operation)),
                ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate idempotency key") from exc

    def save(self, operation: OperationRecord) -> None:
        with self._store.connect() as connection:
            cursor = connection.execute(
                """
                update runtime_operations set status = ?, payload_json = ?
                where operation_id = ?
                """,
                (
                    operation.status.value,
                    dumps(encode_operation(operation)),
                    operation.operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("operation does not exist")


class SqliteSessionRequestRepository:
    def __init__(self, store: SqliteRuntimeStore) -> None:
        self._store = store

    def get(
        self, account_id: str, client_request_id: str
    ) -> OperationRecord | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """
                select payload_json from runtime_session_requests
                where account_id = ? and client_request_id = ?
                """,
                (account_id, client_request_id),
            ).fetchone()
        return decode_operation(json.loads(row["payload_json"])) if row else None

    def create(self, request: OperationRecord) -> None:
        try:
            with self._store.connect() as connection:
                connection.execute(
                """
                insert into runtime_session_requests(
                  account_id, client_request_id, status, payload_json
                ) values (?, ?, ?, ?)
                """,
                (
                    request.account_id,
                    request.client_action_id,
                    request.status.value,
                    dumps(encode_operation(request)),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate idempotency key") from exc

    def save(self, request: OperationRecord) -> None:
        with self._store.connect() as connection:
            cursor = connection.execute(
                """
                update runtime_session_requests set status = ?, payload_json = ?
                where account_id = ? and client_request_id = ?
                """,
                (
                    request.status.value,
                    dumps(encode_operation(request)),
                    request.account_id,
                    request.client_action_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("session request does not exist")


class SqliteRuntimeTransactionRepository:
    """跨 session 与幂等记录的 SQLite 原子提交边界。"""

    def __init__(self, store: SqliteRuntimeStore) -> None:
        self._store = store

    def reserve_operation(
        self,
        session: GameSession,
        *,
        expected_version: int,
        operation: OperationRecord,
        create_operation: bool,
    ) -> None:
        if operation.session_id is None:
            raise ValueError("game operation requires session_id")
        try:
            with self._store.connect() as connection:
                cursor = connection.execute(
                    """
                    update runtime_game_sessions
                    set status = ?, state_version = ?, processing_action_id = ?,
                        updated_at = ?, payload_json = ?
                    where session_id = ? and account_id = ? and state_version = ?
                      and processing_action_id is null
                    """,
                    self._session_parameters(session, expected_version),
                )
                if cursor.rowcount != 1:
                    raise StateVersionConflictError(
                        "状态版本冲突或已有操作占用当前游戏"
                    )
                if create_operation:
                    connection.execute(
                        """
                        insert into runtime_operations(
                          operation_id, account_id, session_id, client_action_id,
                          status, payload_json
                        ) values (?, ?, ?, ?, ?, ?)
                        """,
                        self._operation_parameters(operation),
                    )
                else:
                    self._update_operation(connection, operation)
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate idempotency key") from exc

    def finish_operation(
        self,
        session: GameSession,
        *,
        expected_version: int,
        operation: OperationRecord,
    ) -> None:
        with self._store.connect() as connection:
            cursor = connection.execute(
                """
                update runtime_game_sessions
                set status = ?, state_version = ?, processing_action_id = ?,
                    updated_at = ?, payload_json = ?
                where session_id = ? and account_id = ? and state_version = ?
                  and processing_action_id = ?
                """,
                (*self._session_parameters(session, expected_version),
                 operation.operation_id),
            )
            if cursor.rowcount != 1:
                raise StateVersionConflictError("动作预留已失效或状态版本冲突")
            self._update_operation(connection, operation)

    def complete_session_request(
        self,
        session: GameSession,
        request: OperationRecord,
    ) -> None:
        try:
            with self._store.connect() as connection:
                connection.execute(
                    """
                    insert into runtime_game_sessions(
                      session_id, account_id, status, state_version,
                      processing_action_id, updated_at, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        session.account_id,
                        session.status.value,
                        session.state_version,
                        session.processing_action_id,
                        session.updated_at,
                        dumps(encode_session(session)),
                    ),
                )
                cursor = connection.execute(
                    """
                    update runtime_session_requests
                    set status = ?, payload_json = ?
                    where account_id = ? and client_request_id = ?
                      and status = 'processing'
                    """,
                    (
                        request.status.value,
                        dumps(encode_operation(request)),
                        request.account_id,
                        request.client_action_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("session request does not exist or is completed")
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate session_id") from exc

    @staticmethod
    def _session_parameters(
        session: GameSession, expected_version: int
    ) -> tuple[object, ...]:
        return (
            session.status.value,
            session.state_version,
            session.processing_action_id,
            session.updated_at,
            dumps(encode_session(session)),
            session.session_id,
            session.account_id,
            expected_version,
        )

    @staticmethod
    def _operation_parameters(operation: OperationRecord) -> tuple[object, ...]:
        return (
            operation.operation_id,
            operation.account_id,
            operation.session_id,
            operation.client_action_id,
            operation.status.value,
            dumps(encode_operation(operation)),
        )

    @staticmethod
    def _update_operation(
        connection: sqlite3.Connection, operation: OperationRecord
    ) -> None:
        cursor = connection.execute(
            """
            update runtime_operations set status = ?, payload_json = ?
            where operation_id = ?
            """,
            (
                operation.status.value,
                dumps(encode_operation(operation)),
                operation.operation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("operation does not exist")
