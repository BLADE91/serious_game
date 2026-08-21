from __future__ import annotations

from dataclasses import asdict
from threading import Event
from typing import Callable

from serious_game_backend.application.package_lock import require_locked_package
from serious_game_backend.application.input_review_service import (
    InputReviewService,
    input_rejection_message,
)
from serious_game_backend.application.stream_lifecycle import (
    StreamCancelCallback,
    StreamCancelled,
    ensure_stream_open,
    wait_for_stream_ack,
)
from serious_game_backend.application.disclosure_gate_service import DisclosureGateService
from serious_game_backend.application.night_turn_safety import validate_night_turn_result
from serious_game_backend.application.turn_operation_lease import (
    TurnLease,
    TurnOperationLeaseService,
)
from serious_game_backend.application.ports import (
    GameSessionRepository,
    OperationRepository,
    RoleLLMGateway,
    RuntimeTransactionRepository,
    ScriptPackageRepository,
)
from serious_game_backend.application.visible_state import VisibleStateProjector
from serious_game_backend.application.npc_demand_service import NPCDemandService
from serious_game_backend.domain.errors import ActionUnavailableError
from serious_game_backend.domain.llm import NightAgentContext


class GroupConversationService:
    def __init__(
        self,
        sessions: GameSessionRepository,
        packages: ScriptPackageRepository,
        gateway: RoleLLMGateway,
        projector: VisibleStateProjector,
        input_review: InputReviewService,
        operations: OperationRepository,
        transactions: RuntimeTransactionRepository,
        disclosure_gate: DisclosureGateService | None = None,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._gateway = gateway
        self._projector = projector
        self._input_review = input_review
        self._leases = TurnOperationLeaseService(sessions, operations, transactions)
        self._disclosure_gate = disclosure_gate or DisclosureGateService()

    def reply(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        player_text: str,
        client_action_id: str | None = None,
        retry: bool = False,
        stream_event: Callable[[dict], None] | None = None,
        stream_cancelled: StreamCancelled = None,
        stream_cancel_register: Callable[[StreamCancelCallback], None] | None = None,
    ) -> dict:
        text = player_text.strip()
        if not text:
            raise ActionUnavailableError("群组会谈回应不能为空")
        key = client_action_id or self._leases.legacy_client_action_id({
            "kind": "group_conversation_turn",
            "state_version": state_version,
            "player_text": text,
        })
        reserved = self._leases.reserve(
            account_id=account_id,
            session_id=session_id,
            client_action_id=key,
            state_version=state_version,
            request_payload={
                "kind": "group_conversation_turn",
                "state_version": state_version,
                "player_text": text,
            },
            retry=retry,
            stream_cancel_register=stream_cancel_register,
        )
        if isinstance(reserved, dict):
            return reserved
        lease: TurnLease = reserved
        session = lease.session
        try:
            conversation = session.active_group_conversation
            if conversation is None:
                raise ActionUnavailableError("当前没有强制群组会谈")
            package = require_locked_package(self._packages, session)
            relevant, review_reason = self._input_review.review(
                session,
                operation_id=(
                    f"group:{session.session_id}:{conversation.conversation_id}:"
                    f"input-review:{conversation.turn_count + 1}"
                ),
                player_text=text,
                scene_goal=conversation.agenda,
            )
            ensure_stream_open(stream_cancelled)
            if not relevant:
                session.logs.append({
                    "type": "unrelated_input_rejected",
                    "scene_type": "forced_group_conversation",
                    "scene_id": conversation.conversation_id,
                    "story_day": session.game_state.story_day,
                    "reason": review_reason,
                    "visible_to_player": False,
                })
                NPCDemandService.sync(session, package)
                return self._leases.complete(lease, lambda committed: {
                    "completed": False,
                    "input_rejected": True,
                    "message": input_rejection_message(review_reason),
                    "turn_dialogues": [],
                    "visible_state": self._projector.project(committed, package),
                })
            profiles = {item.npc_id: item for item in package.npc_profiles}
            boundary = self._disclosure_gate.session_boundary(session, package)
            conversation.add_player_turn(text)
            turn_dialogues: list[dict] = []
            for npc_id in conversation.participant_ids:
                profile = profiles.get(npc_id)
                if profile is None:
                    continue
                raw_result = self._gateway.run_night_turn(NightAgentContext(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    operation_id=(
                        f"group:{session.session_id}:{conversation.conversation_id}:"
                        f"{conversation.turn_count + 1}:{npc_id}"
                    ),
                    story_day=session.game_state.story_day,
                    scene_id=conversation.conversation_id,
                    phase="player_group_dialogue",
                    npc_id=npc_id,
                    npc_name=profile.name,
                    role_setting=profile.role_setting,
                    big_five=(
                        profile.big_five.as_dict() if profile.big_five else {}
                    ),
                    counterpart_ids=tuple(
                        item for item in conversation.participant_ids
                        if item != npc_id
                    ),
                    transcript=tuple(conversation.transcript),
                    round_index=conversation.turn_count + 1,
                    scene_goal=conversation.agenda,
                    player_text=text,
                    model_id="",
                ))
                ensure_stream_open(stream_cancelled)
                result = validate_night_turn_result(
                    raw_result,
                    expected_npc_id=npc_id,
                    forbidden_fact_signatures=boundary.forbidden_fact_signatures,
                )
                if result.dialogue:
                    conversation.add_npc_turn(
                        npc_id=npc_id,
                        npc_name=profile.name,
                        model_id=result.model_id,
                        text=result.dialogue,
                    )
                    turn_dialogues.append({
                        "npc_id": npc_id,
                        "npc_name": profile.name,
                        "model_id": result.model_id,
                        "text": result.dialogue,
                    })
            ensure_stream_open(stream_cancelled)
            conversation.turn_count += 1
            completed = conversation.turn_count >= conversation.max_turns
            if completed:
                conversation.status = "completed"
                session.completed_group_conversations.append(asdict(conversation))
                session.logs.append({
                    "type": "forced_group_conversation_completed",
                    "conversation_id": conversation.conversation_id,
                    "conversation_type": conversation.conversation_type,
                    "story_day": session.game_state.story_day,
                    "participant_ids": list(conversation.participant_ids),
                    "visible_to_player": True,
                })
                session.active_group_conversation = None
                if session.group_conversation_queue:
                    session.active_group_conversation = session.group_conversation_queue.pop(0)
                    session.active_group_conversation.status = "active"
            NPCDemandService.sync(session, package)
            response = self._leases.complete(lease, lambda committed: {
                "completed": completed,
                "input_rejected": False,
                "turn_dialogues": turn_dialogues,
                "visible_state": self._projector.project(committed, package),
            })
            self._emit_committed_replies(
                turn_dialogues, stream_event, stream_cancelled
            )
            return response
        except Exception as exc:
            self._leases.fail(lease, exc)
            raise

    @staticmethod
    def _emit_committed_replies(
        replies: list[dict],
        stream_event: Callable[[dict], None] | None,
        stream_cancelled: StreamCancelled,
    ) -> None:
        if stream_event is None:
            return
        for reply in replies:
            identity = {
                "stream_id": f"{reply['npc_id']}:committed",
                "npc_id": reply["npc_id"],
                "npc_name": reply["npc_name"],
            }
            stream_event({"type": "npc_thinking_start", **identity})
            stream_event({"type": "npc_thinking_end", **identity})
            acknowledged = Event()
            stream_event({
                "type": "_npc_reply_ready",
                "reply": reply,
                "acknowledged": acknowledged,
            })
            wait_for_stream_ack(acknowledged, stream_cancelled)
