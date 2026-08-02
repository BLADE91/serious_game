from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

from serious_game_backend.domain.consent import ConsentDocument, ConsentRecord
from serious_game_backend.domain.errors import StateVersionConflictError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.identity import Account, AuthSession, ROLE_PERMISSIONS
from serious_game_backend.domain.llm_runtime import LLMCallAudit, NPCMemory
from serious_game_backend.domain.operation import OperationRecord
from serious_game_backend.domain.research import (
    ExperimentAssignment,
    ResearchEvent,
    ResearchSubject,
)
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.infrastructure.mysql_migrations import MySQLMigrationRunner
from serious_game_backend.infrastructure.repositories.codec import (
    decode_operation,
    decode_session,
    dumps,
    encode_operation,
    encode_session,
)
from serious_game_backend.infrastructure.crypto import FieldCipher


BACKEND_ROOT = Path(__file__).resolve().parents[4]


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.replace(tzinfo=timezone.utc).isoformat()


def _payload(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)


class MySQLRuntimeStore:
    def __init__(
        self, mysql_url: str, *, field_cipher: FieldCipher, run_migrations: bool = True
    ) -> None:
        parsed = urlparse(mysql_url.replace("mysql+pymysql://", "mysql://", 1))
        if parsed.scheme != "mysql" or not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError("GAME_MYSQL_URL must be mysql://user:password@host:port/database")
        self._config = {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": parsed.path.strip("/"),
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": False,
            "connect_timeout": 10,
            "read_timeout": 30,
            "write_timeout": 30,
        }
        self._field_cipher = field_cipher
        if run_migrations:
            MySQLMigrationRunner(self, BACKEND_ROOT / "migrations").migrate()
            self.seed_rbac()

    def protect_json(self, value: dict, *, purpose: str) -> str:
        return dumps(self._field_cipher.encrypt_json(value, purpose=purpose))

    def unprotect_json(self, value, *, purpose: str) -> dict:
        return self._field_cipher.decrypt_json(_payload(value), purpose=purpose)

    def encrypt_text(self, value: str, *, purpose: str) -> str:
        return self._field_cipher.encrypt_text(value, purpose=purpose)

    @contextmanager
    def connect(self) -> Iterator[pymysql.Connection]:
        connection = pymysql.connect(**self._config)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def seed_rbac(self) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            for role, permissions in ROLE_PERMISSIONS.items():
                cursor.execute(
                    "insert ignore into roles(role_id, description) values (%s, %s)",
                    (role, role),
                )
                for permission in permissions:
                    cursor.execute(
                        "insert ignore into permissions(permission_id, description) values (%s, %s)",
                        (permission, permission),
                    )
                    cursor.execute(
                        "insert ignore into role_permissions(role_id, permission_id) values (%s, %s)",
                        (role, permission),
                    )

    def sync_package(self, package: ScriptPackage, immutable_uri: str) -> None:
        manifest = {
            "package_id": package.package_id,
            "package_version": package.package_version,
            "content_hash": package.content_hash,
            "status": package.status,
        }
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into script_packages(
                  package_id, package_version, content_hash, status,
                  immutable_uri, manifest_json, created_at, published_at, retired_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, null)
                on duplicate key update package_id = values(package_id)
                """,
                (
                    package.package_id, package.package_version, package.content_hash,
                    package.status, immutable_uri, dumps(manifest),
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )


class MySQLGameSessionRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def create(self, session: GameSession) -> None:
        try:
            with self._store.connect() as connection, connection.cursor() as cursor:
                self._insert(cursor, session)
        except pymysql.IntegrityError as exc:
            raise ValueError("duplicate session_id or missing account/package") from exc

    def get_owned(self, session_id: str, account_id: str) -> GameSession | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select current_snapshot_json from game_sessions where session_id=%s and account_id=%s",
                (session_id, account_id),
            )
            row = cursor.fetchone()
        return decode_session(self._store.unprotect_json(
            row["current_snapshot_json"], purpose="game_session"
        )) if row else None

    def latest_active(self, account_id: str) -> GameSession | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select current_snapshot_json from game_sessions
                where account_id=%s and status='active' order by updated_at desc limit 1
                """,
                (account_id,),
            )
            row = cursor.fetchone()
        return decode_session(self._store.unprotect_json(
            row["current_snapshot_json"], purpose="game_session"
        )) if row else None

    def save(self, session: GameSession, *, expected_version: int) -> None:
        guard = " and processing_action_id is null" if session.processing_action_id else ""
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                update game_sessions set status=%s, state_version=%s,
                  processing_action_id=%s, pending_decision_id=%s,
                  consent_record_id=%s, environment=%s, experiment_group_id=%s,
                  updated_at=%s, current_snapshot_json=%s
                where session_id=%s and account_id=%s and state_version=%s {guard}
                """,
                self._update_parameters(session, expected_version),
            )
            if cursor.rowcount != 1:
                raise StateVersionConflictError("状态版本冲突或已有操作占用当前游戏")

    def _insert(self, cursor, session: GameSession) -> None:
        cursor.execute(
            """
            insert into game_sessions(
              session_id, account_id, package_id, package_version, package_content_hash,
              status, state_version, processing_action_id, pending_decision_id,
              random_seed, consent_record_id, environment, experiment_group_id,
              created_at, updated_at, current_snapshot_json, metadata_json
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                session.session_id, session.account_id, session.package_id,
                session.package_version, session.package_content_hash,
                session.status.value, session.state_version, session.processing_action_id,
                session.pending_decision.decision_id if session.pending_decision else None,
                session.random_seed, session.consent_record_id, session.environment,
                session.experiment_group_id, _dt(session.created_at), _dt(session.updated_at),
                self._store.protect_json(
                    encode_session(session), purpose="game_session"
                ), dumps({}),
            ),
        )

    def _update_parameters(self, session: GameSession, expected_version: int) -> tuple:
        return (
            session.status.value, session.state_version, session.processing_action_id,
            session.pending_decision.decision_id if session.pending_decision else None,
            session.consent_record_id, session.environment, session.experiment_group_id,
            _dt(session.updated_at), self._store.protect_json(
                encode_session(session), purpose="game_session"
            ),
            session.session_id, session.account_id, expected_version,
        )


class MySQLOperationRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def get(self, account_id: str, session_id: str, client_action_id: str) -> OperationRecord | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select request_json from game_actions
                where account_id=%s and session_id=%s and client_action_id=%s
                """,
                (account_id, session_id, client_action_id),
            )
            row = cursor.fetchone()
        return decode_operation(self._store.unprotect_json(
            row["request_json"], purpose="game_operation"
        )) if row else None

    def create(self, operation: OperationRecord) -> None:
        if operation.session_id is None:
            raise ValueError("game operation requires session_id")
        try:
            with self._store.connect() as connection, connection.cursor() as cursor:
                self._insert(cursor, operation, base_state_version=0)
        except pymysql.IntegrityError as exc:
            raise ValueError("duplicate idempotency key") from exc

    def save(self, operation: OperationRecord) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            self._update(cursor, operation)

    def _insert(self, cursor, operation: OperationRecord, base_state_version: int) -> None:
        cursor.execute(
            """
            insert into game_actions(
              operation_id, session_id, account_id, client_action_id, request_hash,
              input_mode, opportunity_id, action_id, decision_id, option_id,
              base_state_version, committed_state_version, status,
              processing_worker_token, lease_expires_at, attempt_count,
              request_json, response_json, error_json, created_at, updated_at
            ) values (%s,%s,%s,%s,%s,'runtime',null,null,null,null,%s,null,%s,null,null,%s,%s,%s,%s,%s,%s)
            """,
            (
                operation.operation_id, operation.session_id, operation.account_id,
                operation.client_action_id, operation.request_hash, base_state_version,
                operation.status.value, operation.attempt_count,
                self._store.protect_json(
                    encode_operation(operation), purpose="game_operation"
                ),
                self._store.protect_json(
                    operation.response, purpose="game_operation_response"
                ) if operation.response is not None else None,
                self._store.protect_json(
                    operation.error, purpose="game_operation_error"
                ) if operation.error is not None else None,
                _dt(operation.created_at), _dt(operation.updated_at),
            ),
        )

    def _update(self, cursor, operation: OperationRecord) -> None:
        cursor.execute(
            """
            update game_actions set status=%s, attempt_count=%s, request_json=%s,
              response_json=%s, error_json=%s, updated_at=%s where operation_id=%s
            """,
            (
                operation.status.value, operation.attempt_count,
                self._store.protect_json(
                    encode_operation(operation), purpose="game_operation"
                ),
                self._store.protect_json(
                    operation.response, purpose="game_operation_response"
                ) if operation.response is not None else None,
                self._store.protect_json(
                    operation.error, purpose="game_operation_error"
                ) if operation.error is not None else None,
                _dt(operation.updated_at), operation.operation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("operation does not exist")


class MySQLSessionRequestRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def get(self, account_id: str, client_request_id: str) -> OperationRecord | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select request_hash, session_id, status, response_json, created_at, updated_at
                from game_session_requests where account_id=%s and client_request_id=%s
                """,
                (account_id, client_request_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return OperationRecord(
            operation_id=f"new_{client_request_id}", account_id=account_id,
            session_id=row["session_id"], client_action_id=client_request_id,
            request_hash=row["request_hash"], status=__import__(
                "serious_game_backend.domain.enums", fromlist=["OperationStatus"]
            ).OperationStatus(row["status"]),
            response=_payload(row["response_json"]) if row["response_json"] else None,
            created_at=_iso(row["created_at"]), updated_at=_iso(row["updated_at"]),
        )

    def create(self, request: OperationRecord) -> None:
        now = _dt(request.created_at)
        try:
            with self._store.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into game_session_requests(
                      account_id, client_request_id, request_hash, session_id,
                      status, response_json, created_at, updated_at
                    ) values (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        request.account_id, request.client_action_id, request.request_hash,
                        request.session_id, request.status.value, None, now, now,
                    ),
                )
        except pymysql.IntegrityError as exc:
            raise ValueError("duplicate idempotency key") from exc

    def save(self, request: OperationRecord) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            self._update(cursor, request)

    @staticmethod
    def _update(cursor, request: OperationRecord) -> None:
        cursor.execute(
            """
            update game_session_requests set session_id=%s, status=%s,
              response_json=%s, updated_at=%s
            where account_id=%s and client_request_id=%s
            """,
            (
                request.session_id, request.status.value,
                dumps(request.response) if request.response is not None else None,
                _dt(request.updated_at), request.account_id, request.client_action_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("session request does not exist")


class MySQLRuntimeTransactionRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def reserve_operation(
        self, session: GameSession, *, expected_version: int,
        operation: OperationRecord, create_operation: bool,
    ) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update game_sessions set status=%s, state_version=%s,
                  processing_action_id=%s, pending_decision_id=%s,
                  consent_record_id=%s, environment=%s, experiment_group_id=%s,
                  updated_at=%s, current_snapshot_json=%s
                where session_id=%s and account_id=%s and state_version=%s
                  and processing_action_id is null
                """,
                MySQLGameSessionRepository(self._store)._update_parameters(
                    session, expected_version
                ),
            )
            if cursor.rowcount != 1:
                raise StateVersionConflictError("状态版本冲突或已有操作占用当前游戏")
            if create_operation:
                MySQLOperationRepository(self._store)._insert(
                    cursor, operation, expected_version
                )
            else:
                MySQLOperationRepository(self._store)._update(cursor, operation)

    def finish_operation(
        self, session: GameSession, *, expected_version: int,
        operation: OperationRecord, research_event: ResearchEvent | None = None,
    ) -> None:
        parameters = list(MySQLGameSessionRepository(self._store)._update_parameters(
            session, expected_version
        ))
        parameters.append(operation.operation_id)
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update game_sessions set status=%s, state_version=%s,
                  processing_action_id=%s, pending_decision_id=%s,
                  consent_record_id=%s, environment=%s, experiment_group_id=%s,
                  updated_at=%s, current_snapshot_json=%s
                where session_id=%s and account_id=%s and state_version=%s
                  and processing_action_id=%s
                """,
                tuple(parameters),
            )
            if cursor.rowcount != 1:
                raise StateVersionConflictError("动作预留已失效或状态版本冲突")
            MySQLOperationRepository(self._store)._update(cursor, operation)
            if research_event is not None:
                cursor.execute(
                    """
                    insert into research_outbox(
                      research_event_id, research_subject_id, status, attempt_count,
                      lease_token, lease_expires_at, created_at, updated_at, payload_json
                    ) values (%s,%s,'pending',0,null,null,%s,%s,%s)
                    """,
                    (
                        research_event.research_event_id,
                        research_event.research_subject_id,
                        _dt(research_event.created_at), _dt(research_event.created_at),
                        dumps(asdict(research_event)),
                    ),
                )

    def complete_session_request(self, session: GameSession, request: OperationRecord) -> None:
        try:
            with self._store.connect() as connection, connection.cursor() as cursor:
                MySQLGameSessionRepository(self._store)._insert(cursor, session)
                MySQLSessionRequestRepository._update(cursor, request)
        except pymysql.IntegrityError as exc:
            raise ValueError("duplicate session_id or invalid foreign key") from exc


class MySQLLLMCallAuditRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def save(self, audit: LLMCallAudit) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into llm_call_audits(
                  model_audit_id, session_id, account_id, operation_id, call_kind,
                  model_provider, model_id, prompt_version, request_hash, temperature,
                  input_tokens, output_tokens, latency_ms, retry_count,
                  raw_output_ciphertext, validated_result_json, validation_status, created_at
                ) values (%s,%s,%s,%s,'npc_turn',%s,%s,%s,%s,0.35,%s,%s,%s,%s,null,%s,%s,%s)
                """,
                (
                    audit.audit_id, audit.session_id, audit.account_id,
                    audit.operation_id or None, audit.provider, audit.model_id,
                    audit.prompt_version, audit.request_hash, audit.input_tokens,
                    audit.output_tokens, audit.latency_ms, audit.retry_count,
                    self._store.protect_json(
                        asdict(audit), purpose="llm_audit"
                    ), audit.status, _dt(audit.created_at),
                ),
            )

    def successful_for_operation(self, operation_id: str, request_hash: str) -> LLMCallAudit | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select validated_result_json from llm_call_audits
                where operation_id=%s and request_hash=%s and validation_status='succeeded'
                order by created_at desc limit 1
                """,
                (operation_id, request_hash),
            )
            row = cursor.fetchone()
        return LLMCallAudit(**self._store.unprotect_json(
            row["validated_result_json"], purpose="llm_audit"
        )) if row else None

    def list_for_session(self, session_id: str) -> tuple[LLMCallAudit, ...]:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select validated_result_json from llm_call_audits
                where session_id=%s order by created_at
                """,
                (session_id,),
            )
            rows = cursor.fetchall()
        return tuple(LLMCallAudit(**self._store.unprotect_json(
            row["validated_result_json"], purpose="llm_audit"
        )) for row in rows)


class MySQLNPCMemoryRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def save(self, memory: NPCMemory) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into npc_memories(
                  memory_id, session_id, account_id, npc_id, source_operation_id,
                  memory_type, content_json, visibility, valid_from_day,
                  expires_after_day, invalidated_at, created_at
                ) values (%s,%s,%s,%s,%s,%s,%s,'internal',%s,%s,%s,%s)
                """,
                (
                    memory.memory_id, memory.session_id, memory.account_id, memory.npc_id,
                    memory.source_operation_id, memory.memory_type,
                    self._store.protect_json(asdict(memory), purpose="npc_memory"),
                    memory.valid_from_day, memory.expires_after_day,
                    _dt(memory.invalidated_at), _dt(memory.created_at),
                ),
            )

    def active_for_npc(self, session_id: str, npc_id: str, story_day: int) -> tuple[NPCMemory, ...]:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select content_json from npc_memories where session_id=%s and npc_id=%s
                  and invalidated_at is null and valid_from_day <= %s
                  and (expires_after_day is null or expires_after_day >= %s)
                order by created_at
                """,
                (session_id, npc_id, story_day, story_day),
            )
            rows = cursor.fetchall()
        values = []
        for row in rows:
            item = self._store.unprotect_json(row["content_json"], purpose="npc_memory")
            item["keywords"] = tuple(item.get("keywords", ()))
            values.append(NPCMemory(**item))
        return tuple(values)

    def invalidate(self, memory_ids: tuple[str, ...], invalidated_at: str) -> None:
        if not memory_ids:
            return
        placeholders = ",".join(["%s"] * len(memory_ids))
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"select memory_id, content_json from npc_memories where memory_id in ({placeholders})",
                memory_ids,
            )
            for row in cursor.fetchall():
                item = self._store.unprotect_json(
                    row["content_json"], purpose="npc_memory"
                )
                item["invalidated_at"] = invalidated_at
                cursor.execute(
                    "update npc_memories set invalidated_at=%s, content_json=%s where memory_id=%s",
                    (
                        _dt(invalidated_at),
                        self._store.protect_json(item, purpose="npc_memory"),
                        row["memory_id"],
                    ),
                )


class MySQLAccountRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def create(self, account: Account) -> None:
        primary_role = sorted(account.roles)[0]
        try:
            with self._store.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into accounts(
                      account_id, username, password_hash, password_hash_scheme,
                      role, disabled, created_at, updated_at, metadata_json
                    ) values (%s,%s,%s,'scrypt',%s,%s,%s,%s,%s)
                    """,
                    (
                        account.account_id, account.username, account.password_hash,
                        primary_role, account.disabled, _dt(account.created_at),
                        _dt(account.updated_at), dumps({}),
                    ),
                )
                for role in account.roles:
                    cursor.execute(
                        "insert into account_roles(account_id, role_id, granted_at) values (%s,%s,%s)",
                        (account.account_id, role, _dt(account.created_at)),
                    )
        except pymysql.IntegrityError as exc:
            raise ValueError("duplicate account or invalid role") from exc

    def get_by_id(self, account_id: str) -> Account | None:
        return self._get("a.account_id=%s", (account_id,))

    def get_by_username(self, username: str) -> Account | None:
        return self._get("a.username=%s", (username,))

    def _get(self, where: str, parameters: tuple) -> Account | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                select a.*, group_concat(ar.role_id order by ar.role_id) as roles
                from accounts a left join account_roles ar on ar.account_id=a.account_id
                where {where} group by a.account_id
                """,
                parameters,
            )
            row = cursor.fetchone()
        if row is None:
            return None
        roles = frozenset((row.get("roles") or row["role"]).split(","))
        return Account(
            account_id=row["account_id"], username=row["username"],
            password_hash=row["password_hash"], roles=roles,
            disabled=bool(row["disabled"]), created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
        )


class MySQLAuthSessionRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def create(self, session: AuthSession) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into auth_sessions(
                  token_hash, account_id, csrf_token_hash, created_at,
                  last_seen_at, expires_at, revoked_at
                ) values (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    session.token_hash, session.account_id, session.csrf_token_hash,
                    _dt(session.created_at), _dt(session.last_seen_at),
                    _dt(session.expires_at), _dt(session.revoked_at),
                ),
            )

    def get(self, token_hash: str) -> AuthSession | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("select * from auth_sessions where token_hash=%s", (token_hash,))
            row = cursor.fetchone()
        if row is None:
            return None
        return AuthSession(
            token_hash=row["token_hash"], account_id=row["account_id"],
            csrf_token_hash=row["csrf_token_hash"], created_at=_iso(row["created_at"]),
            last_seen_at=_iso(row["last_seen_at"]), expires_at=_iso(row["expires_at"]),
            revoked_at=_iso(row["revoked_at"]),
        )

    def save(self, session: AuthSession) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update auth_sessions set last_seen_at=%s, expires_at=%s, revoked_at=%s
                where token_hash=%s
                """,
                (
                    _dt(session.last_seen_at), _dt(session.expires_at),
                    _dt(session.revoked_at), session.token_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("auth session does not exist")


class MySQLConsentRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def publish_document(self, document: ConsentDocument) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select document_hash from consent_documents where consent_version=%s",
                (document.consent_version,),
            )
            row = cursor.fetchone()
            if row:
                if row["document_hash"] != document.document_hash:
                    raise ValueError("consent version is immutable")
                return
            cursor.execute(
                """
                insert into consent_documents(
                  consent_version, document_hash, model_provider, processing_region,
                  retention_days_raw_text, published_at, retired_at, document_json
                ) values (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    document.consent_version, document.document_hash,
                    document.model_provider, document.processing_region,
                    document.retention_days_raw_text, _dt(document.published_at),
                    _dt(document.retired_at), dumps(asdict(document)),
                ),
            )

    def get_document(self, consent_version: str) -> ConsentDocument | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select document_json from consent_documents where consent_version=%s",
                (consent_version,),
            )
            row = cursor.fetchone()
        return ConsentDocument(**_payload(row["document_json"])) if row else None

    def create_record(self, record: ConsentRecord) -> None:
        payload = asdict(record); payload["scopes"] = sorted(record.scopes)
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into consent_records(
                  consent_record_id, account_id, consent_version, document_hash,
                  scopes_json, signed_at, withdrawn_at, withdrawal_reason, record_json
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.consent_record_id, record.account_id, record.consent_version,
                    record.document_hash, dumps({"scopes": sorted(record.scopes)}),
                    _dt(record.signed_at), _dt(record.withdrawn_at),
                    record.withdrawal_reason, dumps(payload),
                ),
            )

    def get_record(self, consent_record_id: str) -> ConsentRecord | None:
        return self._get("consent_record_id=%s", (consent_record_id,))

    def latest_for_account(self, account_id: str) -> ConsentRecord | None:
        return self._get("account_id=%s order by signed_at desc limit 1", (account_id,))

    def _get(self, where: str, parameters: tuple) -> ConsentRecord | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"select record_json from consent_records where {where}", parameters)
            row = cursor.fetchone()
        if not row:
            return None
        payload = _payload(row["record_json"]); payload["scopes"] = frozenset(payload["scopes"])
        return ConsentRecord(**payload)

    def save_record(self, record: ConsentRecord) -> None:
        current = self.get_record(record.consent_record_id)
        if current is None or (
            current.account_id, current.consent_version, current.document_hash,
            current.scopes, current.signed_at
        ) != (
            record.account_id, record.consent_version, record.document_hash,
            record.scopes, record.signed_at
        ):
            raise ValueError("signed consent fields are immutable")
        payload = asdict(record); payload["scopes"] = sorted(record.scopes)
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update consent_records set withdrawn_at=%s, withdrawal_reason=%s,
                  record_json=%s where consent_record_id=%s
                """,
                (
                    _dt(record.withdrawn_at), record.withdrawal_reason,
                    dumps(payload), record.consent_record_id,
                ),
            )


class MySQLResearchIdentityRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def get_or_create(self, account_id: str) -> ResearchSubject:
        current = self.get_for_account(account_id)
        if current:
            return current
        import secrets
        value = ResearchSubject(f"rs_{secrets.token_hex(16)}", account_id)
        try:
            with self._store.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into research_subjects(
                      research_subject_id, account_id, created_at, retired_at, identity_json
                    ) values (%s,%s,%s,%s,%s)
                    """,
                    (
                        value.research_subject_id, value.account_id, _dt(value.created_at),
                        _dt(value.retired_at), self._store.protect_json(
                            asdict(value), purpose="research_identity"
                        ),
                    ),
                )
        except pymysql.IntegrityError:
            current = self.get_for_account(account_id)
            if current:
                return current
            raise
        return value

    def get_for_account(self, account_id: str) -> ResearchSubject | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select identity_json from research_subjects where account_id=%s", (account_id,)
            )
            row = cursor.fetchone()
        return ResearchSubject(**self._store.unprotect_json(
            row["identity_json"], purpose="research_identity"
        )) if row else None


class MySQLExperimentAssignmentRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def create(self, value: ExperimentAssignment) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into experiment_assignments(
                  assignment_id, research_subject_id, experiment_id, experiment_group_id,
                  environment, package_content_hash, model_id, prompt_version,
                  assigned_at, assignment_json
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    value.assignment_id, value.research_subject_id, value.experiment_id,
                    value.experiment_group_id, value.environment, value.package_content_hash,
                    value.model_id, value.prompt_version, _dt(value.assigned_at),
                    dumps(asdict(value)),
                ),
            )

    def get_for_subject(self, research_subject_id: str, experiment_id: str) -> ExperimentAssignment | None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select assignment_json from experiment_assignments
                where research_subject_id=%s and experiment_id=%s
                """,
                (research_subject_id, experiment_id),
            )
            row = cursor.fetchone()
        return ExperimentAssignment(**_payload(row["assignment_json"])) if row else None


class MySQLResearchEventRepository:
    def __init__(self, store: MySQLRuntimeStore) -> None:
        self._store = store

    def append(self, event: ResearchEvent) -> None:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into research_events(
                  research_event_id, research_subject_id, experiment_id,
                  experiment_group_id, session_public_id, event_type, story_day,
                  structured_payload_json, raw_text_ciphertext, consent_record_id, created_at
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    event.research_event_id, event.research_subject_id,
                    event.experiment_id, event.experiment_group_id,
                    event.session_public_id, event.event_type, event.story_day,
                    dumps(event.structured_payload), event.raw_text_ciphertext,
                    event.consent_record_id, _dt(event.created_at),
                ),
            )

    def list_for_subject(self, research_subject_id: str) -> tuple[ResearchEvent, ...]:
        with self._store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from research_events where research_subject_id=%s order by created_at",
                (research_subject_id,),
            )
            rows = cursor.fetchall()
        return tuple(ResearchEvent(
            research_event_id=row["research_event_id"],
            research_subject_id=row["research_subject_id"],
            experiment_id=row["experiment_id"],
            experiment_group_id=row["experiment_group_id"],
            session_public_id=row["session_public_id"],
            event_type=row["event_type"], story_day=row["story_day"],
            structured_payload=_payload(row["structured_payload_json"]),
            raw_text_ciphertext=row["raw_text_ciphertext"],
            consent_record_id=row["consent_record_id"], created_at=_iso(row["created_at"]),
        ) for row in rows)
