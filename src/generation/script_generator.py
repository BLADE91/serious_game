"""基于 Qwen 的剧本初稿生成器。"""

import json
from typing import Any

from src.config import QwenConfig
from src.domain.game_action import GameActionRule
from src.domain.game_state import GameState
from src.domain.npc_state import NPCState
from src.domain.script_design import ScriptCitation, ScriptDesign, ScriptEventOutline
from src.domain.source_context import SourceContext
from src.generation.qwen_client import ChatMessage, QwenChatClient


class ScriptGenerationError(RuntimeError):
    """当剧本生成结果无法解析时抛出。"""


class QwenScriptGenerator:
    """根据检索资料生成结构化剧本初稿。"""

    def __init__(self, client: QwenChatClient | None = None) -> None:
        self._client = client or QwenChatClient(QwenConfig.from_env())

    def generate(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
    ) -> ScriptDesign:
        if not query.strip():
            raise ValueError("query must not be empty")

        content = self._client.complete(self._build_messages(query, contexts, feedback), temperature=0.2)
        payload = self._parse_json_object(content)
        return self._build_script_design(payload)

    def _build_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
    ) -> list[ChatMessage]:
        context_payload = [
            {
                "reference_id": context.id,
                "title": context.title,
                "content": context.content,
                "metadata": context.metadata,
            }
            for context in contexts
        ]
        user_payload = {
            "query": query,
            "source_contexts": context_payload,
            "human_feedback": feedback.strip(),
            "output_contract": {
                "title": "string",
                "premise": "string",
                "player_role": "string",
                "core_conflict": "string",
                "initial_game_state": {
                    "day": 1,
                    "action_points": 3,
                    "budget_remaining": 8000,
                    "signed_households": 0,
                    "total_households": 36,
                    "social_stability_index": 70,
                    "political_credit": 70,
                    "cadre_execution_index": 60,
                },
                "npc_seed": [
                    {
                        "npc_id": "string",
                        "name": "string",
                        "npc_type": "cadre | external | villager",
                        "group": "string",
                        "trust_to_player": 0,
                        "attitude_score": 0,
                        "anxiety_level": 0,
                        "reference_point": 0,
                        "granovetter_threshold": 0,
                        "core_demand_satisfied": False,
                        "signed": False,
                        "known_info": ["string"],
                        "player_promises": [],
                    }
                ],
                "action_rules": [
                    {
                        "action_id": "string",
                        "name": "string",
                        "cost_action_points": 1,
                        "budget_cost": 0,
                        "allowed_targets": ["villager"],
                        "preconditions": ["string"],
                        "forbidden_conditions": ["string"],
                        "direct_payoff": {},
                        "side_effects": ["string"],
                        "risk_notes": ["string"],
                        "citations": ["reference_id - title"],
                    }
                ],
                "event_outline": [
                    {
                        "event_id": "string",
                        "name": "string",
                        "day_window": "string",
                        "trigger_condition": "string",
                        "description": "string",
                        "payoff": {},
                        "citations": ["reference_id - title"],
                    }
                ],
                "night_rules": ["string"],
                "payoff_notes": ["string"],
                "citations": [
                    {
                        "citation_id": "string",
                        "source_context_id": "string",
                        "title": "string",
                        "note": "string",
                    }
                ],
            },
            "rules": [
                "生成《父母官》方向的剧本初稿，重点是规则、约束和 payoff，不要只写文学设定。",
                "输出必须是一个合法 JSON 对象，不要 Markdown。",
                "首版控制规模：7 个左右 NPC、8 个左右行动规则、5 个左右事件概要。",
                "所有 action_rules 和 event_outline 都必须带 citations。",
                "citations 只能引用 source_contexts 中真实存在的 reference_id 和 title。",
                "如果资料不足，允许使用 query 本身作为设定来源，并在 citation note 中写明来自原始需求。",
                "NPC 类型只能使用 cadre、external、villager。",
                "所有数值字段必须是整数，不要使用百分号字符串。",
                "语言必须是中文。",
                "如果 human_feedback 非空，必须优先满足其中的人工反馈，但不能破坏 JSON 输出结构。",
            ],
        }

        return [
            ChatMessage(
                role="system",
                content=(
                    "你是严肃游戏《父母官》的剧本生成器。"
                    "你的输出要服务后续代码实现：规则、约束、payoff、NPC 初始状态必须结构化。"
                    "必须只输出一个合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ScriptGenerationError("Qwen script response was not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise ScriptGenerationError("Qwen script response must be a JSON object")
        return parsed

    def _build_script_design(self, payload: dict[str, Any]) -> ScriptDesign:
        return ScriptDesign(
            title=self._required_str(payload, "title"),
            premise=self._required_str(payload, "premise"),
            player_role=self._required_str(payload, "player_role"),
            core_conflict=self._required_str(payload, "core_conflict"),
            initial_game_state=self._build_game_state(payload.get("initial_game_state", {})),
            npc_seed=self._build_npc_states(payload.get("npc_seed", [])),
            action_rules=self._build_action_rules(payload.get("action_rules", [])),
            event_outline=self._build_event_outline(payload.get("event_outline", [])),
            night_rules=self._string_list(payload.get("night_rules")),
            payoff_notes=self._string_list(payload.get("payoff_notes")),
            citations=self._build_citations(payload.get("citations", [])),
        )

    def _build_game_state(self, payload: Any) -> GameState:
        if not isinstance(payload, dict):
            payload = {}
        return GameState(
            day=self._int_value(payload.get("day"), 1),
            action_points=self._int_value(payload.get("action_points"), 3),
            budget_remaining=self._int_value(payload.get("budget_remaining"), 8000),
            signed_households=self._int_value(payload.get("signed_households"), 0),
            total_households=self._int_value(payload.get("total_households"), 36),
            social_stability_index=self._int_value(payload.get("social_stability_index"), 70),
            political_credit=self._int_value(payload.get("political_credit"), 70),
            cadre_execution_index=self._int_value(payload.get("cadre_execution_index"), 60),
        )

    def _build_npc_states(self, value: Any) -> list[NPCState]:
        if not isinstance(value, list):
            return []

        npcs = []
        for item in value:
            if not isinstance(item, dict):
                continue
            npcs.append(
                NPCState(
                    npc_id=self._required_str(item, "npc_id"),
                    name=self._required_str(item, "name"),
                    npc_type=self._required_str(item, "npc_type"),
                    group=self._required_str(item, "group"),
                    trust_to_player=self._int_value(item.get("trust_to_player"), 50),
                    attitude_score=self._int_value(item.get("attitude_score"), 50),
                    anxiety_level=self._int_value(item.get("anxiety_level"), 50),
                    reference_point=self._int_value(item.get("reference_point"), 0),
                    granovetter_threshold=self._int_value(item.get("granovetter_threshold"), 50),
                    core_demand_satisfied=bool(item.get("core_demand_satisfied", False)),
                    signed=bool(item.get("signed", False)),
                    known_info=self._string_list(item.get("known_info")),
                    player_promises=self._string_list(item.get("player_promises")),
                )
            )
        return npcs

    def _build_action_rules(self, value: Any) -> list[GameActionRule]:
        if not isinstance(value, list):
            return []

        rules = []
        for item in value:
            if not isinstance(item, dict):
                continue
            rules.append(
                GameActionRule(
                    action_id=self._required_str(item, "action_id"),
                    name=self._required_str(item, "name"),
                    cost_action_points=self._int_value(item.get("cost_action_points"), 1),
                    budget_cost=self._int_value(item.get("budget_cost"), 0),
                    allowed_targets=self._string_list(item.get("allowed_targets")),
                    preconditions=self._string_list(item.get("preconditions")),
                    forbidden_conditions=self._string_list(item.get("forbidden_conditions")),
                    direct_payoff=item.get("direct_payoff", {}) if isinstance(item.get("direct_payoff"), dict) else {},
                    side_effects=self._string_list(item.get("side_effects")),
                    risk_notes=self._string_list(item.get("risk_notes")),
                    citations=self._string_list(item.get("citations")),
                )
            )
        return rules

    def _build_event_outline(self, value: Any) -> list[ScriptEventOutline]:
        if not isinstance(value, list):
            return []

        events = []
        for item in value:
            if not isinstance(item, dict):
                continue
            events.append(
                ScriptEventOutline(
                    event_id=self._required_str(item, "event_id"),
                    name=self._required_str(item, "name"),
                    day_window=self._required_str(item, "day_window"),
                    trigger_condition=self._required_str(item, "trigger_condition"),
                    description=self._required_str(item, "description"),
                    payoff=item.get("payoff", {}) if isinstance(item.get("payoff"), dict) else {},
                    citations=self._string_list(item.get("citations")),
                )
            )
        return events

    def _build_citations(self, value: Any) -> list[ScriptCitation]:
        if not isinstance(value, list):
            return []

        citations = []
        for item in value:
            if not isinstance(item, dict):
                continue
            citations.append(
                ScriptCitation(
                    citation_id=self._required_str(item, "citation_id"),
                    source_context_id=self._required_str(item, "source_context_id"),
                    title=self._required_str(item, "title"),
                    note=self._required_str(item, "note"),
                )
            )
        return citations

    def _required_str(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ScriptGenerationError(f"script field '{key}' must be a non-empty string")
        return value.strip()

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    def _int_value(self, value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return default
