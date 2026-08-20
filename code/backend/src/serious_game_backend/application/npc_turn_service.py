from __future__ import annotations

from dataclasses import replace
from threading import Event
from typing import Callable

from serious_game_backend.application.ports import RoleLLMGateway
from serious_game_backend.application.stream_lifecycle import (
    StreamCancelled,
    ensure_stream_open,
    wait_for_stream_ack,
)
from serious_game_backend.application.state_delta_validator import StateDeltaValidator
from serious_game_backend.domain.llm import RoleTurnContext, ValidatedRoleTurn
from serious_game_backend.domain.npc_state import NPCState
from serious_game_backend.domain.errors import RoleLLMResponseError, RoleLLMUnavailableError


class NPCTurnService:
    """角色回合门面；真实供应商接入时保持此服务签名不变。"""

    def __init__(self, gateway: RoleLLMGateway, validator: StateDeltaValidator) -> None:
        self._gateway = gateway
        self._validator = validator

    def run(
        self,
        context: RoleTurnContext,
        npc_state: NPCState,
        *,
        random_seed: str,
        stream_event: Callable[[dict], None] | None = None,
        stream_cancelled: StreamCancelled = None,
    ) -> ValidatedRoleTurn:
        identity = {
            "stream_id": f"{context.npc_id}:0",
            "npc_id": context.npc_id,
            "npc_name": context.npc_name,
        }
        if stream_event is not None:
            stream_event({"type": "npc_thinking_start", **identity})
        try:
            result = self._gateway.run_turn(context)
        except (ConnectionError, TimeoutError) as exc:
            raise RoleLLMUnavailableError("角色模型暂时不可用") from exc
        finally:
            if stream_event is not None:
                stream_event({"type": "npc_thinking_end", **identity})
        ensure_stream_open(stream_cancelled)
        if result.npc_id != context.npc_id:
            raise RoleLLMResponseError("角色模型返回了错误的 npc_id")
        if result.input_relevance not in {"relevant", "irrelevant"}:
            raise RoleLLMResponseError("角色模型返回了非法的输入相关性")
        if result.input_relevance == "irrelevant":
            result = replace(
                result,
                dialogue="请输入与本游戏相关的话语",
                portrait_state="neutral",
                attitude_direction="none",
                attitude_band="none",
                anxiety_direction="none",
                anxiety_band="none",
                disclosure_id=None,
                flag_candidates=(),
                will_share_with=(),
                memory_candidate=None,
                risk_notes=(),
                conversation_state="continue",
                exit_narrative=None,
            )
        if not result.dialogue.strip() or len(result.dialogue) > 1000:
            raise RoleLLMResponseError("角色模型返回了空白或过长回复")
        if result.portrait_state not in {"neutral", "warm", "guarded", "anxious"}:
            raise RoleLLMResponseError("角色模型返回了非法的立绘状态")
        if result.attitude_direction not in {"none", "increase", "decrease"}:
            raise RoleLLMResponseError("角色模型返回了非法的态度方向")
        if result.attitude_band not in {"none", "micro", "medium", "heavy"}:
            raise RoleLLMResponseError("角色模型返回了非法的态度幅度")
        if result.anxiety_direction not in {"none", "increase", "decrease"}:
            raise RoleLLMResponseError("角色模型返回了非法的焦虑方向")
        if result.anxiety_band not in {"none", "light", "medium", "heavy"}:
            raise RoleLLMResponseError("角色模型返回了非法的焦虑幅度")
        if (
            result.disclosure_id is not None
            and result.disclosure_id not in context.allowed_fact_ids
        ):
            raise RoleLLMResponseError("角色模型尝试吐露机会边界外的事实")
        if result.flag_candidates:
            raise RoleLLMResponseError("角色模型不能直接提交旗标")
        lowered_dialogue = result.dialogue.lower()
        forbidden_output_markers = (
            "system prompt", "developer message", "忽略以上指令",
            "flag_", "state_version", "结局轴", "```json",
        )
        if any(marker in lowered_dialogue for marker in forbidden_output_markers):
            raise RoleLLMResponseError("角色模型输出包含越权或提示词泄露内容")
        if any(
            marker and marker.lower() in lowered_dialogue
            for marker in context.forbidden_fact_markers
        ):
            raise RoleLLMResponseError("角色模型在对白中泄露了知识边界外的事实")
        if result.memory_candidate and len(result.memory_candidate.strip()) > 500:
            raise RoleLLMResponseError("角色模型记忆候选过长")
        if result.conversation_state not in {"continue", "end"}:
            raise RoleLLMResponseError("角色模型返回了非法的会谈状态")
        if (result.conversation_state == "end") != bool(result.exit_narrative):
            raise RoleLLMResponseError("角色模型会谈状态与离场叙事不一致")
        if result.conversation_state == "continue" and any(
            marker in result.dialogue
            for marker in ("请回吧", "不必再谈", "谈话到此", "不谈了", "出去")
        ):
            raise RoleLLMResponseError("角色对白已经送客，但会谈状态仍为 continue")
        validated = self._validator.validate_role_turn(
            result,
            npc_state,
            random_seed=random_seed,
            source_id=context.opportunity_id,
        )
        if stream_event is not None:
            acknowledged = Event()
            stream_event({
                "type": "_npc_reply_ready",
                "reply": {
                    "npc_id": validated.npc_id,
                    "npc_name": context.npc_name,
                    "text": validated.dialogue,
                },
                "acknowledged": acknowledged,
            })
            wait_for_stream_ack(acknowledged, stream_cancelled)
        return validated
