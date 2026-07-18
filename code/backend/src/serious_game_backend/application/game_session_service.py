from __future__ import annotations

from dataclasses import replace
import secrets

from serious_game_backend.application.hashing import canonical_request_hash
from serious_game_backend.application.event_service import EventService
from serious_game_backend.application.ports import (
    GameSessionRepository,
    RuntimeTransactionRepository,
    ScriptPackageRepository,
    SessionRequestRepository,
)
from serious_game_backend.application.story_flow_service import StoryFlowService
from serious_game_backend.domain.enums import AvailabilityMode, NPCStateTier, OperationStatus
from serious_game_backend.domain.errors import (
    ContentValidationError,
    IdempotencyKeyReusedError,
    NotFoundError,
    SessionBusyError,
)
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.game_state import GameState
from serious_game_backend.domain.npc_state import NPCState
from serious_game_backend.domain.operation import OperationRecord, utc_now_iso


class GameSessionService:
    def __init__(
        self,
        sessions: GameSessionRepository,
        session_requests: SessionRequestRepository,
        transactions: RuntimeTransactionRepository,
        packages: ScriptPackageRepository,
        story_flow: StoryFlowService,
        events: EventService,
    ) -> None:
        self._sessions = sessions
        self._requests = session_requests
        self._transactions = transactions
        self._packages = packages
        self._story_flow = story_flow
        self._events = events

    def start_session(
        self,
        *,
        account_id: str,
        package_id: str,
        client_request_id: str,
        origin_id: str,
    ) -> GameSession:
        payload = {
            "package_id": package_id,
            "client_request_id": client_request_id,
            "origin_id": origin_id,
        }
        request_hash = canonical_request_hash(payload)
        existing = self._requests.get(account_id, client_request_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyKeyReusedError("client_request_id 已用于不同的新局请求")
            if existing.status is OperationStatus.SUCCEEDED and existing.session_id:
                session = self._sessions.get_owned(existing.session_id, account_id)
                if session is None:
                    raise NotFoundError("幂等记录指向的游戏不存在")
                return session
            if existing.status is OperationStatus.PROCESSING:
                raise SessionBusyError(
                    "新局正在创建，请使用相同 client_request_id 重试",
                    details={"operation_id": existing.operation_id},
                )

        package = self._packages.get(package_id)
        if package is None:
            raise NotFoundError("剧本包不存在")
        if package.status == "retired":
            raise ContentValidationError("退役剧本包不能用于新开局")
        if origin_id not in package.origins:
            raise ContentValidationError(
                "开局出身未在剧本包注册",
                details={"origin_id": origin_id},
            )

        request = OperationRecord(
            operation_id=f"new_{secrets.token_hex(12)}",
            account_id=account_id,
            session_id=None,
            client_action_id=client_request_id,
            request_hash=request_hash,
        )
        if existing is None:
            try:
                self._requests.create(request)
            except ValueError as exc:
                # 唯一约束竞争中只有一个请求能继续创建 session。
                concurrent = self._requests.get(account_id, client_request_id)
                if concurrent is None or concurrent.request_hash != request_hash:
                    raise IdempotencyKeyReusedError(
                        "client_request_id 已被并发请求占用"
                    ) from exc
                raise SessionBusyError(
                    "新局正在创建，请使用相同 client_request_id 重试",
                    details={"operation_id": concurrent.operation_id},
                ) from exc

        session_id = f"sess_{secrets.token_hex(16)}"
        session = GameSession(
            session_id=session_id,
            account_id=account_id,
            package_id=package.package_id,
            package_version=package.package_version,
            package_content_hash=package.content_hash,
            random_seed=secrets.token_hex(32),
            game_state=GameState.new_game(),
            origin_id=origin_id,
            npc_states=self._initial_npc_states(package.npc_profiles),
        )
        self._events.trigger_fixed_events(session, package)
        self._story_flow.initialize(session, package)
        now = utc_now_iso()
        completed = replace(
            request,
            session_id=session_id,
            status=OperationStatus.SUCCEEDED,
            response={"session_id": session_id},
            updated_at=now,
        )
        self._transactions.complete_session_request(session, completed)
        return session

    def get_owned(self, session_id: str, account_id: str) -> GameSession:
        session = self._sessions.get_owned(session_id, account_id)
        if session is None:
            # 不区分不存在和不属于当前账号，防止枚举他人 session。
            raise NotFoundError("游戏不存在")
        return session

    def latest_active(self, account_id: str) -> GameSession:
        session = self._sessions.latest_active(account_id)
        if session is None:
            raise NotFoundError("没有可继续的游戏")
        return session

    @staticmethod
    def _initial_npc_states(profiles) -> dict[str, NPCState]:
        states: dict[str, NPCState] = {}
        for profile in profiles:
            if profile.state_tier is NPCStateTier.AMBIENT:
                continue
            if profile.state_tier is NPCStateTier.DEEP:
                states[profile.npc_id] = NPCState(
                    npc_id=profile.npc_id,
                    state_tier=profile.state_tier,
                    availability_mode=AvailabilityMode.CLOSED,
                    profile_id=profile.profile_id or profile.npc_id,
                    trust_score=40,
                    attitude_score=profile.initial_attitude,
                    anxiety_score=profile.initial_anxiety,
                )
            else:
                states[profile.npc_id] = NPCState(
                    npc_id=profile.npc_id,
                    state_tier=profile.state_tier,
                    availability_mode=AvailabilityMode.CLOSED,
                    profile_id=profile.profile_id or profile.npc_id,
                )
        return states
