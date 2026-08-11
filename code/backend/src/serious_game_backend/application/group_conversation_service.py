from __future__ import annotations

from dataclasses import asdict

from serious_game_backend.application.package_lock import require_locked_package
from serious_game_backend.application.input_review_service import (
    InputReviewService,
    input_rejection_message,
)
from serious_game_backend.application.ports import (
    GameSessionRepository,
    RoleLLMGateway,
    ScriptPackageRepository,
)
from serious_game_backend.application.visible_state import VisibleStateProjector
from serious_game_backend.domain.enums import SessionStatus
from serious_game_backend.domain.errors import (
    ActionUnavailableError,
    NotFoundError,
    SessionEndedError,
    StateVersionConflictError,
)
from serious_game_backend.domain.llm import NightAgentContext


class GroupConversationService:
    def __init__(
        self,
        sessions: GameSessionRepository,
        packages: ScriptPackageRepository,
        gateway: RoleLLMGateway,
        projector: VisibleStateProjector,
        input_review: InputReviewService,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._gateway = gateway
        self._projector = projector
        self._input_review = input_review

    def reply(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        player_text: str,
    ) -> dict:
        session = self._sessions.get_owned(session_id, account_id)
        if session is None:
            raise NotFoundError("游戏不存在")
        if session.status is not SessionStatus.ACTIVE:
            raise SessionEndedError("当前游戏不可继续写入")
        if session.state_version != state_version:
            raise StateVersionConflictError(
                "状态版本已变化，请刷新后重试",
                details={"current_state_version": session.state_version},
            )
        conversation = session.active_group_conversation
        if conversation is None:
            raise ActionUnavailableError("当前没有强制群组会谈")
        text = player_text.strip()
        if not text:
            raise ActionUnavailableError("群组会谈回应不能为空")
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
        if not relevant:
            session.logs.append({
                "type": "unrelated_input_rejected",
                "scene_type": "forced_group_conversation",
                "scene_id": conversation.conversation_id,
                "story_day": session.game_state.story_day,
                "reason": review_reason,
                "visible_to_player": False,
            })
            session.state_version += 1
            session.touch()
            self._sessions.save(session, expected_version=state_version)
            return {
                "state_version": session.state_version,
                "completed": False,
                "input_rejected": True,
                "message": input_rejection_message(review_reason),
                "turn_dialogues": [],
                "visible_state": self._projector.project(session, package),
            }
        profiles = {item.npc_id: item for item in package.npc_profiles}

        conversation.add_player_turn(text)
        turn_dialogues: list[dict] = []
        for npc_id in conversation.participant_ids:
            profile = profiles.get(npc_id)
            if profile is None:
                continue
            result = self._gateway.run_night_turn(NightAgentContext(
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
            if result.npc_id == npc_id and result.dialogue:
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
                session.active_group_conversation = (
                    session.group_conversation_queue.pop(0)
                )
                session.active_group_conversation.status = "active"
        session.state_version += 1
        session.touch()
        self._sessions.save(session, expected_version=state_version)
        return {
            "state_version": session.state_version,
            "completed": completed,
            "input_rejected": False,
            "turn_dialogues": turn_dialogues,
            "visible_state": self._projector.project(session, package),
        }
